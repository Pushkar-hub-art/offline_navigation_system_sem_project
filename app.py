"""
app.py - Flask server for the offline navigation website.

Routes
------
GET  /            -> serves the web page (templates/index.html)
GET  /api/info    -> map coverage (bounds, centre) so the page can zoom to it
GET  /api/tiles   -> manifest of the offline tile set (zoom levels, counts)
POST /api/route   -> body: {"start": {"lat":..,"lon":..},
                            "end":   {"lat":..,"lon":..},
                            "speed_kmh": 30 (optional)}
                     returns the route path, distance, ETA and turn-by-turn steps

Run:
    python app.py
then open http://127.0.0.1:5000
"""

import json
import os
from bisect import bisect_left

import networkx as nx
from flask import Flask, jsonify, render_template, request

from nav_engine import OfflineMap, haversine, Route

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MAP_FILE = os.environ.get("MAP_FILE", os.path.join(BASE_DIR, "data", "city.graphml"))
MAX_SNAP_M = 1000          # reject points further than this from any road
DEFAULT_SPEED_KMH = 30     # used for the ETA estimate

app = Flask(__name__)

# --------------------------------------------------------------------------
# Load the offline map once, when the server starts
# --------------------------------------------------------------------------
if not os.path.exists(MAP_FILE):
    raise SystemExit(
        f"Map file not found: {MAP_FILE}\n"
        "Run download_map.py first (needs internet, one time only)."
    )

omap = OfflineMap(MAP_FILE)

MAP_BOUNDS = {
    "south": float(omap.lats.min()),
    "north": float(omap.lats.max()),
    "west": float(omap.lons.min()),
    "east": float(omap.lons.max()),
}

# --------------------------------------------------------------------------
# Offline raster tiles (produced by tools/build_tiles.py)
# --------------------------------------------------------------------------
TILES_DIR = os.environ.get("TILES_DIR", os.path.join(BASE_DIR, "static", "tiles"))
TILES_MANIFEST = os.path.join(TILES_DIR, "tiles.json")


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def tile_info():
    """Read the tile manifest written by tools/build_tiles.py."""
    result = {
        "available": False,
        "url": "/static/tiles/{z}/{x}/{y}.png",
        "tile_size": 256,
        "zooms": [],
    }

    if not os.path.exists(TILES_MANIFEST):
        return result

    try:
        with open(TILES_MANIFEST, encoding="utf-8") as handle:
            manifest = json.load(handle)
    except (OSError, ValueError):
        return result

    zooms = manifest.get("zooms", {})
    result["available"] = bool(zooms)
    result["tile_size"] = manifest.get("tile_size", 256)
    result["style"] = manifest.get("style", "light")
    result["zooms"] = sorted(int(z) for z in zooms)
    result["count"] = sum(int(z.get("count", 0)) for z in zooms.values())
    return result


def parse_point(data, key):
    """Read and validate {"lat": .., "lon": ..} from the request body."""
    point = data.get(key)
    if not isinstance(point, dict):
        raise ValueError(f"'{key}' is missing")
    try:
        lat, lon = float(point["lat"]), float(point["lon"])
    except (KeyError, TypeError, ValueError):
        raise ValueError(f"'{key}' needs numeric 'lat' and 'lon'")
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError(f"'{key}' coordinates are out of range")
    return lat, lon


def snap_to_road(lat, lon, label):
    """Find the nearest road node; fail if the point is outside the map area."""
    node = omap.nearest_node(lat, lon)
    node_lat, node_lon = omap.coord(node)
    dist = haversine(lat, lon, node_lat, node_lon)
    if dist > MAX_SNAP_M:
        raise ValueError(
            f"The {label} is {dist / 1000:.1f} km from the nearest road in the "
            "offline map. Pick a point inside the downloaded area."
        )
    return node


def error(message, status=400):
    return jsonify({"error": message}), status


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------
@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/info")
def api_info():
    return jsonify(
        {
            "bounds": MAP_BOUNDS,
            "center": [
                (MAP_BOUNDS["south"] + MAP_BOUNDS["north"]) / 2,
                (MAP_BOUNDS["west"] + MAP_BOUNDS["east"]) / 2,
            ],
            "nodes": len(omap.ids),
            "tiles": tile_info(),
        }
    )


@app.get("/api/tiles")
def api_tiles():
    """Manifest of the offline tile set: which zoom levels exist and where."""
    info = tile_info()
    if not info.get("available"):
        return error(
            "No offline tiles found. Run: python tools/build_tiles.py", 404
        )
    return jsonify(info)


@app.post("/api/route")
def api_route():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return error("Request body must be JSON")

    # ---- validate input ----
    try:
        start = parse_point(data, "start")
        end = parse_point(data, "end")
        src = snap_to_road(*start, "start point")
        dst = snap_to_road(*end, "destination")
    except ValueError as exc:
        return error(str(exc))

    try:
        speed = float(data.get("speed_kmh", DEFAULT_SPEED_KMH))
    except (TypeError, ValueError):
        return error("'speed_kmh' must be a number")
    if speed <= 0:
        return error("'speed_kmh' must be greater than 0")

    if src == dst:
        return error("Start and destination are on the same road point")

    # ---- compute the route ----
    try:
        nodes = omap.route(src, dst)
    except nx.NetworkXNoPath:
        return error("No drivable route exists between these two points", 404)

    route = Route(omap, nodes)

    # attach coordinates to each turn instruction (for map markers)
    steps = []
    for step in route.steps:
        idx = min(bisect_left(route.cum, step["at"]), len(route.pts) - 1)
        lat, lon = route.pts[idx]
        steps.append(
            {
                "text": step["text"],
                "at_m": round(step["at"], 1),
                "lat": lat,
                "lon": lon,
            }
        )

    return jsonify(
        {
            "path": [[lat, lon] for lat, lon in route.pts],
            "distance_m": round(route.total, 1),
            "duration_s": round(route.total / (speed / 3.6)),
            "steps": steps,
            "snapped_start": list(route.pts[0]),
            "snapped_end": list(route.pts[-1]),
        }
    )


@app.errorhandler(500)
def server_error(_exc):
    return error("Internal server error", 500)


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------
if __name__ == "__main__":
    # use_reloader=False so the (large) map isn't loaded twice in debug mode
    app.run(host="127.0.0.1", port=5000, debug=True, use_reloader=False)