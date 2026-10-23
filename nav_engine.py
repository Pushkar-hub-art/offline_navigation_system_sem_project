"""
nav_engine.py - offline routing engine (no web, no GPS hardware code).

Provides
--------
haversine()      distance in metres between two lat/lon points
bearing()        compass direction between two lat/lon points
OfflineMap       loads the saved road network, snaps points, finds routes
Route            a computed route: distances, turn-by-turn steps, progress

Used by app.py:
    from nav_engine import OfflineMap, Route, haversine
"""

import math

import networkx as nx
import numpy as np

EARTH_R = 6371000.0  # metres


# --------------------------------------------------------------------------
# Geometry helpers
# --------------------------------------------------------------------------
def haversine(lat1, lon1, lat2, lon2):
    """Great-circle distance in metres between two points."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * EARTH_R * math.asin(math.sqrt(a))


def bearing(lat1, lon1, lat2, lon2):
    """Initial compass bearing (0-360 degrees) from point 1 to point 2."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def to_xy(lat, lon, lat0):
    """Local flat-earth projection to metres (accurate over short distances)."""
    return (
        math.radians(lon) * EARTH_R * math.cos(math.radians(lat0)),
        math.radians(lat) * EARTH_R,
    )


def point_to_segment(p, a, b):
    """
    Distance (m) from point p to the segment a-b, and the fraction t (0..1)
    along the segment where the closest point lies. Points are (lat, lon).
    """
    lat0 = p[0]
    px, py = to_xy(p[0], p[1], lat0)
    ax, ay = to_xy(a[0], a[1], lat0)
    bx, by = to_xy(b[0], b[1], lat0)
    dx, dy = bx - ax, by - ay
    seg_len2 = dx * dx + dy * dy
    if seg_len2 == 0:
        return math.hypot(px - ax, py - ay), 0.0
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / seg_len2))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy)), t


def fmt_dist(m):
    """Human-friendly distance: '250 m' or '1.4 km'."""
    return f"{m:.0f} m" if m < 1000 else f"{m / 1000:.1f} km"


# --------------------------------------------------------------------------
# Offline map
# --------------------------------------------------------------------------
class OfflineMap:
    """Road network loaded from a .graphml file saved by download_map.py."""

    def __init__(self, path):
        raw = nx.read_graphml(path)

        # Build a simple directed graph with float coordinates and lengths.
        self.G = nx.DiGraph()
        for node, data in raw.nodes(data=True):
            if "x" not in data or "y" not in data:
                continue
            self.G.add_node(node, lat=float(data["y"]), lon=float(data["x"]))

        for u, v, data in raw.edges(data=True):
            if u not in self.G or v not in self.G:
                continue
            length = float(data.get("length", 0) or 0)
            name = str(data.get("name", "") or "")
            if name.startswith("["):  # osmnx stores multiple names as a list string
                name = name.strip("[]").split(",")[0].strip(" '\"")
            # parallel edges: keep the shortest one
            if self.G.has_edge(u, v) and self.G[u][v]["length"] <= length:
                continue
            self.G.add_edge(u, v, length=length, name=name or "unnamed road")

        if self.G.number_of_nodes() == 0:
            raise ValueError(f"No road data found in {path}")

        # Arrays used for fast nearest-node lookup.
        self.ids = list(self.G.nodes)
        self.lats = np.array([self.G.nodes[n]["lat"] for n in self.ids])
        self.lons = np.array([self.G.nodes[n]["lon"] for n in self.ids])

        print(
            f"Map loaded: {len(self.ids)} intersections, "
            f"{self.G.number_of_edges()} road segments"
        )

    def coord(self, node):
        """(lat, lon) of a node."""
        data = self.G.nodes[node]
        return data["lat"], data["lon"]

    def nearest_node(self, lat, lon):
        """Closest road intersection to any lat/lon."""
        dlat = np.radians(self.lats - lat)
        dlon = np.radians(self.lons - lon) * math.cos(math.radians(lat))
        return self.ids[int(np.argmin(dlat * dlat + dlon * dlon))]

    def route(self, src, dst):
        """Shortest path (list of nodes) from src to dst using A*."""

        def heuristic(a, b):
            return haversine(*self.coord(a), *self.coord(b))

        return nx.astar_path(self.G, src, dst, heuristic=heuristic, weight="length")


# --------------------------------------------------------------------------
# Route
# --------------------------------------------------------------------------
class Route:
    """
    A computed route.

    Attributes
    ----------
    nodes : list of node ids along the route
    pts   : list of (lat, lon) for each node
    cum   : cumulative distance (m) at each node; cum[0] == 0
    total : total route length in metres
    steps : turn-by-turn list of {"at": metres from start, "text": instruction}
    """

    TURN_THRESHOLD = 25  # degrees; smaller direction changes count as "straight"

    def __init__(self, omap, nodes):
        self.omap = omap
        self.nodes = nodes
        self.pts = [omap.coord(n) for n in nodes]

        self.cum = [0.0]
        for i in range(1, len(nodes)):
            self.cum.append(self.cum[-1] + omap.G[nodes[i - 1]][nodes[i]]["length"])
        self.total = self.cum[-1]

        self.steps = self._build_steps()

    @classmethod
    def _turn_text(cls, diff):
        """Convert a signed angle change into 'Turn left', 'Bear right', etc."""
        magnitude = abs(diff)
        if magnitude < cls.TURN_THRESHOLD:
            return None
        side = "right" if diff > 0 else "left"
        if magnitude < 60:
            return f"Bear {side}"
        if magnitude < 135:
            return f"Turn {side}"
        return f"Sharp {side}"

    def _build_steps(self):
        steps = []
        for i in range(1, len(self.nodes) - 1):
            b_in = bearing(*self.pts[i - 1], *self.pts[i])
            b_out = bearing(*self.pts[i], *self.pts[i + 1])
            diff = (b_out - b_in + 540) % 360 - 180  # signed, -180..180
            text = self._turn_text(diff)
            if text:
                road = self.omap.G[self.nodes[i]][self.nodes[i + 1]]["name"]
                steps.append({"at": self.cum[i], "text": f"{text} onto {road}"})

        # Prepend a "head out" instruction and append the arrival message.
        if len(self.nodes) > 1:
            first_road = self.omap.G[self.nodes[0]][self.nodes[1]]["name"]
            steps.insert(0, {"at": 0.0, "text": f"Head out on {first_road}"})
        steps.append({"at": self.total, "text": "You have arrived at your destination"})
        return steps

    def locate(self, pos, hint=0, window=40):
        """
        Project a GPS position (lat, lon) onto the route.

        Returns (off_route_m, progress_m, segment_index):
          off_route_m   how far the position is from the route line
          progress_m    distance travelled along the route
          segment_index segment the position is on (pass back as `hint`)
        Only a window of segments around `hint` is searched, which is faster
        and avoids jumping to a later part of the route that passes nearby.
        """
        best = (float("inf"), 0.0, hint)
        lo = max(0, hint - 3)
        hi = min(len(self.pts) - 1, hint + window)
        for j in range(lo, hi):
            dist, t = point_to_segment(pos, self.pts[j], self.pts[j + 1])
            if dist < best[0]:
                seg_len = self.cum[j + 1] - self.cum[j]
                best = (dist, self.cum[j] + t * seg_len, j)
        return best