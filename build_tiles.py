"""
build_tiles.py - render the offline road network into slippy-map PNG tiles.

WHY THIS FILE EXISTS
--------------------
static/app.js asks Leaflet for tiles at:

    /static/tiles/{z}/{x}/{y}.png

but that folder was never created, so the Leaflet canvas stayed an empty
grey rectangle (only the blue route polyline was visible).  Leaflet itself
is already vendored in static/leaflet/, so the only missing piece is the
imagery.

This script reads data/city.graphml - the very same file app.py routes on -
projects every road edge with the Web Mercator projection and writes one
PNG per tile into static/tiles/<z>/<x>/<y>.png.

NO INTERNET ACCESS IS REQUIRED.

Usage
-----
    python tools/build_tiles.py                        # zoom 12..17, light
    python tools/build_tiles.py --style dark
    python tools/build_tiles.py --min-zoom 13 --max-zoom 18
    python tools/build_tiles.py --workers 8            # parallel rendering
    python tools/build_tiles.py --clean                # wipe tiles first

Output
------
    static/tiles/<z>/<x>/<y>.png      the tiles Leaflet requests
    static/tiles/tiles.json           manifest (used by /api/tiles)
"""

from __future__ import annotations

import argparse
import ast
import json
import math
import os
import re
import shutil
import sys
from concurrent.futures import ProcessPoolExecutor

from PIL import Image, ImageDraw, ImageFont

# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_GRAPHML = os.path.join(BASE_DIR, "data", "city.graphml")
DEFAULT_OUT = os.path.join(BASE_DIR, "static", "tiles")

TILE_SIZE = 256          # pixels per tile (standard slippy map)
SUPERSAMPLE = 2          # render at 2x then downscale -> smooth edges

# --------------------------------------------------------------------------
# Road styling:  class -> (colour, width in px at 256px tiles)
# Widths follow the usual OSM cartographic hierarchy.
# --------------------------------------------------------------------------
STYLES = {
    "light": {
        "background": (247, 244, 238),
        "casing": (198, 192, 180),
        "roads": {
            "motorway": (233, 146, 162),
            "motorway_link": (233, 146, 162),
            "trunk": (252, 153, 153),
            "trunk_link": (252, 153, 153),
            "primary": (253, 205, 102),
            "primary_link": (253, 205, 102),
            "secondary": (253, 224, 153),
            "secondary_link": (253, 224, 153),
            "tertiary": (253, 219, 140),
            "tertiary_link": (253, 219, 140),
            "unclassified": (255, 255, 255),
            "residential": (255, 255, 255),
            "living_street": (255, 255, 255),
            "pedestrian": (224, 222, 212),
            "service": (255, 255, 255),
            "track": (205, 178, 128),
            "path": (205, 178, 128),
            "footway": (205, 178, 128),
            "steps": (205, 178, 128),
            "cycleway": (205, 178, 128),
        },
        "widths": {
            "motorway": 9, "motorway_link": 5,
            "trunk": 8, "trunk_link": 5,
            "primary": 7, "primary_link": 4,
            "secondary": 6, "secondary_link": 4,
            "tertiary": 5, "tertiary_link": 3,
            "unclassified": 4, "residential": 4, "living_street": 4,
            "pedestrian": 3, "service": 3,
            "track": 3, "path": 2, "footway": 2, "steps": 2, "cycleway": 2,
        },
    },
    "dark": {
        "background": (24, 27, 32),
        "casing": (12, 14, 17),
        "roads": {
            "motorway": (198, 106, 124),
            "motorway_link": (198, 106, 124),
            "trunk": (190, 110, 110),
            "trunk_link": (190, 110, 110),
            "primary": (205, 156, 70),
            "primary_link": (205, 156, 70),
            "secondary": (180, 140, 70),
            "secondary_link": (180, 140, 70),
            "tertiary": (150, 120, 62),
            "tertiary_link": (150, 120, 62),
            "unclassified": (78, 86, 98),
            "residential": (78, 86, 98),
            "living_street": (78, 86, 98),
            "pedestrian": (64, 70, 80),
            "service": (70, 78, 90),
            "track": (104, 92, 66),
            "path": (104, 92, 66),
            "footway": (104, 92, 66),
            "steps": (104, 92, 66),
            "cycleway": (104, 92, 66),
        },
        "widths": {
            "motorway": 9, "motorway_link": 5,
            "trunk": 8, "trunk_link": 5,
            "primary": 7, "primary_link": 4,
            "secondary": 6, "secondary_link": 4,
            "tertiary": 5, "tertiary_link": 3,
            "unclassified": 4, "residential": 4, "living_street": 4,
            "pedestrian": 3, "service": 3,
            "track": 3, "path": 2, "footway": 2, "steps": 2, "cycleway": 2,
        },
    },
}

# Point features drawn on top of the roads
POINT_TAGS = ("traffic_signals", "bus_stop", "crossing", "street_lamp")

LINESTRING_RE = re.compile(r"LINESTRING\s*\((.*)\)", re.IGNORECASE)


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------
def parse_list_string(value):
    """osmnx sometimes stores a list as "['a', 'b']". Return a plain list."""
    if not isinstance(value, str):
        return []
    text = value.strip()
    if text.startswith("["):
        try:
            parsed = ast.literal_eval(text)
            if isinstance(parsed, (list, tuple)):
                return [str(item) for item in parsed]
        except (ValueError, SyntaxError):
            text = text.strip("[]")
    return [part.strip().strip("'\"") for part in text.split(",") if part.strip()]


def first(value):
    """First element of a possibly-list attribute, as a plain string."""
    items = parse_list_string(value)
    if items:
        return items[0]
    if isinstance(value, str):
        return value.strip()
    return ""


def edge_class(data):
    """Normalise the 'highway' attribute into a styling class name."""
    value = first(data.get("highway", ""))
    return value if value else "residential"


def edge_name(data):
    """Road name, cleaning osmnx list-strings."""
    items = parse_list_string(data.get("name", ""))
    if items:
        return items[0]
    value = data.get("name", "")
    return value.strip() if isinstance(value, str) else ""


def parse_geometry(raw, fallback):
    """Parse an edge 'geometry' attribute into a list of (lon, lat) points."""
    if raw:
        match = LINESTRING_RE.search(str(raw))
        if match:
            points = []
            for chunk in match.group(1).split(","):
                parts = chunk.split()
                if len(parts) >= 2:
                    try:
                        points.append((float(parts[0]), float(parts[1])))
                    except ValueError:
                        continue
            if len(points) >= 2:
                return points
    return fallback


# --------------------------------------------------------------------------
# Web Mercator projection
# --------------------------------------------------------------------------
MAX_LATITUDE = 85.05112878


def lonlat_to_px(lon, lat, zoom, scale=1.0):
    """Global pixel coordinates of a lon/lat at the given zoom."""
    world = TILE_SIZE * (2 ** zoom) * scale
    x = (lon + 180.0) / 360.0 * world

    clamped = max(min(lat, MAX_LATITUDE), -MAX_LATITUDE)
    rad = math.radians(clamped)
    y = (1.0 - math.log(math.tan(rad) + 1.0 / math.cos(rad)) / math.pi) / 2.0 * world
    return x, y


def lonlat_to_tile(lon, lat, zoom):
    """(tx, ty) tile that contains the given lon/lat."""
    x, y = lonlat_to_px(lon, lat, zoom)
    return int(x // TILE_SIZE), int(y // TILE_SIZE)



# --------------------------------------------------------------------------
# Loading the road network
# --------------------------------------------------------------------------
def load_roads(graphml_path):
    """
    Read the graphml and return a list of road records.

    Each record is a dict:
        cls   : styling class (highway type)
        name  : street name ('' when unnamed)
        pts   : list of (lon, lat) points describing the road
    """
    try:
        import networkx as nx
    except ImportError:
        sys.exit("networkx is not installed. Run:  pip install networkx")

    graph = nx.read_graphml(graphml_path)

    roads = []
    for u, v, data in graph.edges(data=True):
        coords = []
        if u in graph.nodes and v in graph.nodes:
            du, dv = graph.nodes[u], graph.nodes[v]
            if "x" in du and "y" in du and "x" in dv and "y" in dv:
                coords = [
                    (float(du["x"]), float(du["y"])),
                    (float(dv["x"]), float(dv["y"])),
                ]

        pts = parse_geometry(data.get("geometry"), coords)
        if len(pts) < 2:
            continue

        roads.append(
            {
                "cls": edge_class(data),
                "name": edge_name(data),
                "pts": pts,
            }
        )

    return roads


def load_point_features(graphml_path):
    """Traffic signals / bus stops / crossings stored as node attributes."""
    try:
        import networkx as nx
    except ImportError:
        return []

    graph = nx.read_graphml(graphml_path)
    features = []
    for node, data in graph.nodes(data=True):
        tag = str(data.get("highway", "") or "")
        if tag in POINT_TAGS and "x" in data and "y" in data:
            features.append((tag, float(data["x"]), float(data["y"])))
    return features


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------
def load_font(size):
    """Load a TrueType font, falling back to PIL's bitmap font."""
    for name in ("segoeui.ttf", "arial.ttf", "calibri.ttf", "tahoma.ttf"):
        path = os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts", name)
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


def tile_bounds(tx, ty, zoom):
    """(west, south, east, north) lon/lat bounds of a tile."""
    world = TILE_SIZE * (2 ** zoom)
    west = tx * TILE_SIZE / world * 360.0 - 180.0
    east = (tx + 1) * TILE_SIZE / world * 360.0 - 180.0
    north = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * ty * TILE_SIZE / world))))
    south = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (ty + 1) * TILE_SIZE / world))))
    return west, south, east, north


def render_tile(roads, points, tx, ty, zoom, style, with_labels):
    """Render one tile as a PIL image."""
    cfg = STYLES[style]
    size = TILE_SIZE * SUPERSAMPLE
    big = Image.new("RGB", (size, size), cfg["background"])
    draw = ImageDraw.Draw(big)

    origin_x = tx * TILE_SIZE * SUPERSAMPLE
    origin_y = ty * TILE_SIZE * SUPERSAMPLE

    def to_px(lon, lat):
        px, py = lonlat_to_px(lon, lat, zoom, SUPERSAMPLE)
        return px - origin_x, py - origin_y

    def visible(points_px, margin=64):
        """True when a segment is close enough to the tile to be worth drawing."""
        for i in range(len(points_px) - 1):
            x1, y1 = points_px[i]
            x2, y2 = points_px[i + 1]
            if (
                max(x1, x2) < -margin
                or min(x1, x2) > size + margin
                or max(y1, y2) < -margin
                or min(y1, y2) > size + margin
            ):
                continue
            return True
        return False

    # ---- 1. road casings (drawn first, so they sit under the fill) ----
    for pass_name in ("casing", "fill"):
        for road in roads:
            cls = road["cls"]
            colour = cfg["roads"].get(cls)
            width = cfg["widths"].get(cls, 3)
            if colour is None:
                colour = cfg["roads"]["residential"]
                width = cfg["widths"]["residential"]

            if pass_name == "casing":
                if cls in ("path", "footway", "steps", "cycleway", "track", "service"):
                    continue  # no casing for minor paths
                width = (width + 2) * SUPERSAMPLE
                colour = cfg["casing"]
            else:
                width = width * SUPERSAMPLE

            px = [to_px(lon, lat) for lon, lat in road["pts"]]
            if not visible(px):
                continue
            draw.line(px, fill=colour, width=max(1, int(width)), joint="curve")

    # ---- 2. point features ----
    point_colours = {
        "traffic_signals": (60, 60, 60),
        "bus_stop": (60, 110, 200),
        "crossing": (150, 150, 150),
        "street_lamp": (120, 120, 120),
    }
    for tag, lon, lat in points:
        x, y = to_px(lon, lat)
        if -8 <= x <= size + 8 and -8 <= y <= size + 8:
            r = (3 if tag == "traffic_signals" else 2) * SUPERSAMPLE
            draw.ellipse(
                [x - r, y - r, x + r, y + r],
                fill=point_colours.get(tag, (120, 120, 120)),
            )

    # ---- 3. street name labels (only when zoomed in enough) ----
    if with_labels and zoom >= 16:
        font = load_font(11)
        placed = []
        for road in roads:
            if not road["name"]:
                continue
            px = [to_px(lon, lat) for lon, lat in road["pts"]]
            if not visible(px):
                continue

            mid = px[len(px) // 2]
            if not (20 <= mid[0] <= size - 20 and 20 <= mid[1] <= size - 20):
                continue
            if any(abs(mid[0] - px0) < 110 and abs(mid[1] - py0) < 22
                   for px0, py0 in placed):
                continue
            placed.append((mid[0], mid[1]))

            text = road["name"]
            bbox = draw.textbbox((0, 0), text, font=font)
            tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
            if tw > size - 16:
                continue

            x = mid[0] - tw / 2
            y = mid[1] - th / 2

            for ox, oy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                draw.text((x + ox, y + oy), text, font=font, fill=(255, 255, 255))
            draw.text((x, y), text, font=font, fill=(70, 70, 70))

    return big.resize((TILE_SIZE, TILE_SIZE), Image.LANCZOS)


# --------------------------------------------------------------------------
# Tile generation
# --------------------------------------------------------------------------
def tiles_for_roads(roads, zoom):
    """All (tx, ty) tiles touched by the road network at a given zoom."""
    wanted = set()
    for road in roads:
        for lon, lat in road["pts"]:
            tx, ty = lonlat_to_tile(lon, lat, zoom)
            wanted.add((tx, ty))
            # include the 8 neighbours so road ends that cross a tile border
            # are still drawn in the neighbouring tile
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    wanted.add((tx + dx, ty + dy))
    return wanted


def build_tiles(roads, points, out_dir, min_zoom, max_zoom, style,
                with_labels, workers):
    """Render every tile for every zoom level. Returns (count, manifest)."""
    manifest = {"style": style, "tile_size": TILE_SIZE, "zooms": {}}
    total = 0

    for zoom in range(min_zoom, max_zoom + 1):
        wanted = sorted(tiles_for_roads(roads, zoom))
        if not wanted:
            continue

        zoom_dir = os.path.join(out_dir, str(zoom))
        os.makedirs(zoom_dir, exist_ok=True)

        print(f"  zoom {zoom}: {len(wanted)} tiles ...")
        written = 0

        for tx, ty in wanted:
            x_dir = os.path.join(zoom_dir, str(tx))
            os.makedirs(x_dir, exist_ok=True)
            target = os.path.join(x_dir, f"{ty}.png")

            image = render_tile(roads, points, tx, ty, zoom, style, with_labels)
            image.save(target, "PNG", optimize=True)
            written += 1

        manifest["zooms"][str(zoom)] = {
            "count": written,
            "x_range": [min(t[0] for t in wanted), max(t[0] for t in wanted)],
            "y_range": [min(t[1] for t in wanted), max(t[1] for t in wanted)],
        }
        total += written
        print(f"  zoom {zoom}: {written} tiles written")

    return total, manifest


# --------------------------------------------------------------------------
# Preview helper (not needed by the app - handy for a quick visual check)
# --------------------------------------------------------------------------
def make_preview(out_dir, zoom, style, out_png):
    """Stitch the tiles of one zoom level into a single PNG for inspection."""
    zoom_dir = os.path.join(out_dir, str(zoom))
    if not os.path.isdir(zoom_dir):
        sys.exit(f"No tiles for zoom {zoom} in {zoom_dir}")

    xs, ys = [], []
    for x_name in os.listdir(zoom_dir):
        x_path = os.path.join(zoom_dir, x_name)
        if not os.path.isdir(x_path):
            continue
        xs.append(int(x_name))
        for y_name in os.listdir(x_path):
            if y_name.endswith(".png"):
                ys.append(int(y_name[:-4]))

    x_min, x_max = min(xs), max(xs)
    y_min, y_max = min(ys), max(ys)

    cols = x_max - x_min + 1
    rows = y_max - y_min + 1
    canvas = Image.new("RGB", (cols * TILE_SIZE, rows * TILE_SIZE),
                       STYLES[style]["background"])

    for x in range(x_min, x_max + 1):
        for y in range(y_min, y_max + 1):
            path = os.path.join(zoom_dir, str(x), f"{y}.png")
            if os.path.exists(path):
                canvas.paste(Image.open(path),
                             ((x - x_min) * TILE_SIZE, (y - y_min) * TILE_SIZE))

    canvas.save(out_png, "PNG")
    print(f"Preview written: {out_png}  ({cols}x{rows} tiles)")


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Render the offline road network into PNG map tiles"
    )
    parser.add_argument("--graphml", default=DEFAULT_GRAPHML,
                        help="input .graphml file (default data/city.graphml)")
    parser.add_argument("--out", default=DEFAULT_OUT,
                        help="output tile folder (default static/tiles)")
    parser.add_argument("--min-zoom", type=int, default=12,
                        help="lowest zoom level to render (default 12)")
    parser.add_argument("--max-zoom", type=int, default=17,
                        help="highest zoom level to render (default 17)")
    parser.add_argument("--style", choices=sorted(STYLES), default="light",
                        help="colour scheme (default light)")
    parser.add_argument("--no-labels", action="store_true",
                        help="do not draw street name labels")
    parser.add_argument("--clean", action="store_true",
                        help="delete the output folder before rendering")
    parser.add_argument("--workers", type=int, default=1,
                        help="reserved for future parallel rendering")
    parser.add_argument("--preview", type=int, default=None, metavar="ZOOM",
                        help="after rendering, stitch one zoom level into a "
                             "single PNG (e.g. --preview 15)")
    args = parser.parse_args()

    if not os.path.exists(args.graphml):
        sys.exit(
            f"Map file not found: {args.graphml}\n"
            "Run download_map.py first (needs internet, one time only)."
        )

    print(f"Reading road network from: {args.graphml}")
    roads = load_roads(args.graphml)
    points = load_point_features(args.graphml)
    print(f"  {len(roads)} road segments, {len(points)} point features")

    if args.clean and os.path.isdir(args.out):
        print(f"Removing old tiles in {args.out}")
        shutil.rmtree(args.out)

    os.makedirs(args.out, exist_ok=True)

    total, manifest = build_tiles(
        roads, points, args.out,
        args.min_zoom, args.max_zoom, args.style,
        not args.no_labels, args.workers,
    )

    manifest_path = os.path.join(args.out, "tiles.json")
    with open(manifest_path, "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2)

    size_mb = 0.0
    for root, _dirs, files in os.walk(args.out):
        for name in files:
            size_mb += os.path.getsize(os.path.join(root, name))
    size_mb /= (1024 * 1024)

    print(f"\nDone: {total} tiles written to {args.out}  ({size_mb:.1f} MB)")
    print(f"Manifest: {manifest_path}")

    if args.preview is not None:
        preview_path = os.path.join(BASE_DIR, "static",
                                    f"preview_z{args.preview}.png")
        make_preview(args.out, args.preview, args.style, preview_path)

    print("Now run:  python app.py   and open http://127.0.0.1:5000")


if __name__ == "__main__":
    main()

