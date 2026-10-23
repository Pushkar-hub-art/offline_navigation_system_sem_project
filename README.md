# Offline Navigation System

A semester project that computes driving routes and turn-by-turn directions on a locally stored road network, with no internet connection needed at run time.

## Project Overview

The Offline Navigation System is a Flask web application. It loads a road network saved as a GraphML file (`data/city.graphml`) and finds the shortest drivable route between two points using the **A\*** algorithm from **NetworkX**. A Leaflet-based web page shows the map, the route, the distance, the estimated time and the turn-by-turn directions.

Internet access is needed only once, to download the road network with `download_map.py`. After that, routing and the map display run on the local machine.

## Problem Statement

Most navigation tools rely on a constant internet connection and on remote servers for map data and routing. In areas with poor connectivity, or when a service is unavailable, they stop working. This project addresses the problem by keeping the road data, the routing logic and the map imagery on the user's own computer.

## Objectives

- Download a drivable road network once and store it locally.
- Compute the shortest route between two points on the map using A\*.
- Produce a distance, an estimated travel time and turn-by-turn instructions.
- Provide an interactive web interface for choosing points and viewing the route.
- Serve all front-end assets (Leaflet, CSS, JavaScript, map tiles) locally.
- Report clear errors for invalid input, points outside the map and unreachable destinations.

## Key Features

- Offline A\* shortest-path routing on an OpenStreetMap-derived road graph.
- Start and destination chosen by clicking the map, typing `lat, lon`, or using the browser's current location.
- Snapping of chosen points to the nearest road, with a limit of 1000 m.
- Distance, estimated time (selectable average speed) and a numbered list of directions.
- Live navigation mode with a next-instruction banner.
- Locally bundled Leaflet 1.9.4, so the page does not depend on a CDN.
- Pre-rendered offline PNG map tiles (zoom 13 to 17) generated from the same road graph.
- JSON API with input validation and error messages.

## Technologies Used

| Area | Technology |
|------|------------|
| Language | Python 3 |
| Web framework | Flask |
| Graph / routing | NetworkX (A\* search), NumPy |
| Map download | OSMnx (one-time step only) |
| Tile rendering | Pillow |
| Front end | HTML, CSS, JavaScript, Leaflet 1.9.4 |
| Map data | OpenStreetMap, stored as GraphML |
| Testing | pytest + Selenium (browser tests) |

## System Architecture

```text
+--------------------------+        HTTP / JSON        +----------------------------+
|  Browser (front end)     | <-----------------------> |  Flask server (app.py)     |
|  templates/index.html    |   GET /, /api/info,       |  - input validation        |
|  static/app.js + Leaflet |   /api/tiles              |  - snap points to roads    |
|  static/style.css        |   POST /api/route         |  - builds JSON responses   |
+------------+-------------+                           +-------------+--------------+
             |                                                       |
             | GET /static/tiles/{z}/{x}/{y}.png                     | uses
             v                                                       v
+--------------------------+                           +----------------------------+
|  Offline map tiles       |                           |  Routing engine            |
|  static/tiles/           |                           |  (nav_engine.py)           |
|  (built by               |                           |  OfflineMap: load + snap + |
|   tools/build_tiles.py)  |                           |  A* route; Route: steps    |
+--------------------------+                           +-------------+--------------+
                                                                     |
                                                                     v
                                                       +----------------------------+
                                                       |  data/city.graphml         |
                                                       |  (from download_map.py)    |
                                                       +----------------------------+
```

**API routes (`app.py`)**

| Method | Route | Purpose |
|--------|-------|---------|
| GET | `/` | Serves the web page |
| GET | `/api/info` | Map bounds, centre, node count and tile info |
| GET | `/api/tiles` | Manifest of the offline tile set (404 if no tiles exist) |
| POST | `/api/route` | Body `{"start": {"lat", "lon"}, "end": {"lat", "lon"}, "speed_kmh": 30}`. Returns the path, distance, duration and steps |

## Workflow

1. **One-time, online:** `download_map.py` downloads the drivable road network for an area and saves it to `data/city.graphml`.
2. **One-time, offline:** `tools/build_tiles.py` renders that graph into PNG tiles under `static/tiles/`.
3. `app.py` starts and loads the graph into memory through `OfflineMap`.
4. The browser loads the page, then calls `/api/info` and `/api/tiles` to centre the map and enable the tile layer.
5. The user picks a start and a destination.
6. The browser sends `POST /api/route`. The server snaps both points to the nearest road node and runs A\*.
7. The server returns the route polyline, distance, ETA and directions, and the page draws them on the map.

## Project Directory Structure

```text
offline_navigation_system_sem_project/
└── SEM_project/
    ├── app.py                 # Flask server and API routes
    ├── nav_engine.py          # OfflineMap, Route, A* routing, geometry helpers
    ├── download_map.py        # One-time road network downloader (OSMnx)
    ├── requirements.txt       # Python dependencies
    ├── test_navigation.py     # Selenium/pytest browser tests
    ├── README.md
    ├── .gitignore
    ├── templates/
    │   └── index.html         # Web page
    ├── static/
    │   ├── app.js             # Front-end logic
    │   ├── style.css          # Styling
    │   ├── leaflet/           # Local Leaflet 1.9.4 (JS, CSS, images, LICENSE)
    │   └── tiles/             # Pre-rendered offline PNG tiles + tiles.json
    ├── tools/
    │   └── build_tiles.py     # Renders offline map tiles from the road graph
    └── data/
        └── city.graphml       # Road network (Pune area, about 3.6 MB)
```

## Installation Instructions

Requirements: Python 3.9 or newer and `pip`. The project was checked with Python 3.12.

```powershell
git clone https://github.com/Pushkar-hub-art/offline_navigation_system_sem_project.git
cd offline_navigation_system_sem_project\SEM_project

# Optional but recommended: create a virtual environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1

pip install -r requirements.txt
```

If PowerShell blocks the activation script, run `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` first.

`requirements.txt` installs `flask`, `networkx`, `numpy`, `pillow` and `osmnx`. `osmnx` is only needed for the map download step.

## Map Download Instructions

The repository already includes a downloaded road graph (`data/city.graphml`) for an area of Pune (about 3,982 intersections), so you can skip this step to try the project.

To download a different area (this needs internet), run these commands from the `SEM_project` folder:

```powershell
# Centre point + radius in metres
python download_map.py --point "18.5158,73.8150" --radius 3000 --out "data/city.graphml"

# Or by place name
python download_map.py --place "Pune, Maharashtra, India"
```

Large areas produce large files and can take several minutes. After replacing the graph, rebuild the offline tiles so the visual map matches:

```powershell
python tools/build_tiles.py --clean --min-zoom 13 --max-zoom 17
```

**Routing vs. map display.** The road graph (`city.graphml`) is enough for route computation. A completely offline *visual* street map additionally needs locally stored map tiles. This project generates simple road-only tiles from the graph with `tools/build_tiles.py` and stores them in `static/tiles/`. They show roads and street names only, with no buildings, parks or water, and they cover only the downloaded area. If the tiles are missing, routing still works, but the map background is blank.

## How to Run the Project

From the `SEM_project` folder:

```powershell
python app.py
```

Then open <http://127.0.0.1:5000> in your browser. Press `Ctrl+C` in the terminal to stop the server.

Optional environment variables are `MAP_FILE` (path to another `.graphml` file) and `TILES_DIR` (path to another tile folder). For example:

```powershell
$env:MAP_FILE = "data\other_city.graphml"; python app.py
```

## How to Use the Application

1. Choose a **Start** point. Click the 📍 button and then click the map, type `lat, lon` into the field, or use **Use my current location**.
2. Choose a **Destination** in the same way.
3. Optionally change the **Average speed**, which affects only the estimated time.
4. Click **Get route**. The route is drawn on the map and the panel shows the distance, the estimated time and the directions.
5. Click **Start live navigation** to show the next-instruction banner. Click **Stop** to leave this mode.
6. Use the swap button (⇅) to reverse the trip and **Clear** to reset the form.

Points must lie within the downloaded map area. A point more than 1000 m from any road is rejected.

## Explanation of the A\* Algorithm and NetworkX

**A\*** is a best-first graph search algorithm. For each node `n` it uses the score:

```text
f(n) = g(n) + h(n)
```

- `g(n)` is the known distance travelled from the start to `n`.
- `h(n)` is a heuristic estimate of the remaining distance from `n` to the goal.

If the heuristic never overestimates the true remaining cost, A\* finds the shortest path and usually explores far fewer nodes than Dijkstra's algorithm.

**In this project** (`nav_engine.py`, `OfflineMap.route`):

- The graph is a `networkx.DiGraph`. Nodes are intersections with `lat` and `lon`. Edges are road segments with a `length` in metres.
- The edge weight is `length`.
- The heuristic is the **haversine** (great-circle) distance to the destination. It is admissible because no road is shorter than the straight line.
- The call is `nx.astar_path(G, src, dst, heuristic=heuristic, weight="length")`.

**NetworkX** is a Python library for building and analysing graphs. Here it reads the GraphML file (`nx.read_graphml`), stores the road network and provides the A\* implementation.

`Route` then converts the node list into a polyline, cumulative distances and turn instructions. It computes the bearing change at each intersection and labels it "Bear", "Turn" or "Sharp" left or right, treating changes under 25 degrees as straight.

## Testing Instructions

**Quick API check (no browser needed).** Run from `SEM_project`:

```powershell
python -c "import app; c = app.app.test_client(); print(c.get('/').status_code, c.get('/api/info').status_code, c.post('/api/route', json={}).status_code)"
```

Expected output: `200 200 400`.

**Syntax check:**

```powershell
python -m py_compile app.py nav_engine.py download_map.py tools\build_tiles.py
```

**Browser tests (Selenium).** `test_navigation.py` drives the page in Chrome. It needs extra packages, Google Chrome and a running server:

```powershell
pip install pytest selenium

# Terminal 1
python app.py

# Terminal 2
pytest test_navigation.py -v
```

Before running them, edit `START` and `END` at the top of `test_navigation.py` so that both points lie inside your downloaded map. The default `START` longitude (`73.8567`) is outside the bundled graph, whose longitude range is about `73.787` to `73.843`. Valid bounds are returned by `GET /api/info`.

## Limitations

- Routing uses distance only. There is no live traffic, no turn restrictions and no road-speed data. The ETA is distance divided by a user-selected average speed.
- The map covers only the downloaded area. Points outside it, or more than 1000 m from a road, are rejected.
- The offline tiles are simple road-only renderings, not a full cartographic map.
- "Use my current location" relies on the browser's geolocation. There is no dedicated GPS hardware code.
- Flask's development server is used. It is not suitable for production.
- The whole graph is loaded into memory, so very large regions need more RAM and start-up time.
- The Selenium tests are basic UI checks and depend on valid coordinates for the loaded map.

## Future Improvements

- Use road-type speed limits and turn costs for more realistic travel times.
- Add alternative routes and waypoints.
- Add address and place-name search backed by a local index.
- Add automated unit tests for `nav_engine.py` and the API.
- Add real GPS input and automatic re-routing when the user leaves the route.
- Serve vector or MBTiles map data for a richer offline map.
- Package the project with Docker and a production WSGI server.

## Author / Team Information

| Role | Name |
|------|------|
| Author / Team Lead | `<Your Name>` |
| Team Member | `<Team Member Name>` |
| Guide / Faculty | `<Guide Name>` |
| Institution / Department | `<College Name, Department>` |
| Academic Year / Semester | `<Year, Semester>` |

GitHub: <https://github.com/Pushkar-hub-art>

## Licenses and Attribution

- **Leaflet 1.9.4** is bundled in `static/leaflet/` under the BSD 2-Clause License. The license file is included in that folder.
- **Map data** is © [OpenStreetMap](https://www.openstreetmap.org/copyright) contributors, available under the Open Database License (ODbL). The road graph and the generated tiles are derived from it.
