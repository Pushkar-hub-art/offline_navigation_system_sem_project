"""
download_map.py - one-time script to download the road network for your area.

Needs internet ONLY for this step. After the .graphml file is saved,
the website and routing work completely offline.

Examples
--------
By place name:
    python download_map.py --place "Pune, Maharashtra, India"

By centre point + radius in metres (smaller, faster, good for testing):
    python download_map.py --point 18.5204,73.8567 --radius 8000

Custom output file:
    python download_map.py --place "Amravati, Maharashtra, India" --out data/amravati.graphml

Requires:  pip install osmnx
"""

import argparse
import os
import sys

DEFAULT_OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "city.graphml")


def main():
    parser = argparse.ArgumentParser(description="Download a drivable road network for offline use")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--place", help='place name, e.g. "Pune, Maharashtra, India"')
    source.add_argument("--point", help="centre point as lat,lon, e.g. 18.5204,73.8567")
    parser.add_argument("--radius", type=int, default=5000,
                        help="radius in metres around --point (default 5000)")
    parser.add_argument("--out", default=DEFAULT_OUT,
                        help="output .graphml file (default data/city.graphml)")
    args = parser.parse_args()

    try:
        import osmnx as ox
    except ImportError:
        sys.exit("osmnx is not installed. Run:  pip install osmnx")

    # ---- download ----
    try:
        if args.place:
            print(f"Downloading road network for: {args.place}")
            print("(large cities can take several minutes)")
            graph = ox.graph_from_place(args.place, network_type="drive")
        else:
            try:
                lat, lon = (float(v) for v in args.point.split(","))
            except ValueError:
                sys.exit("--point must look like 18.5204,73.8567")
            print(f"Downloading roads within {args.radius} m of {lat}, {lon}")
            graph = ox.graph_from_point((lat, lon), dist=args.radius, network_type="drive")
    except Exception as exc:  # network errors, unknown place names, etc.
        sys.exit(
            f"Download failed: {exc}\n"
            "Check your internet connection and that the place name is correct "
            '(try adding the state and country, e.g. "Pune, Maharashtra, India").'
        )

    # ---- save ----
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    ox.save_graphml(graph, args.out)

    size_mb = os.path.getsize(args.out) / (1024 * 1024)
    print(f"\nSaved: {args.out}  ({size_mb:.1f} MB)")
    print(f"Intersections: {len(graph.nodes)}   Road segments: {len(graph.edges)}")
    print("You can now run:  python app.py")


if __name__ == "__main__":
    main()