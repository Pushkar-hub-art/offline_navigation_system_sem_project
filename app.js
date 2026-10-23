
/* ==========================================================================
   app.js - Offline Navigation System
   Compatible with:
     app.py       -> /api/info and /api/route
     nav_engine.py -> OfflineMap and Route output
     index.html   -> existing element IDs
     style.css    -> existing classes and marker styles
   ========================================================================== */

document.addEventListener("DOMContentLoaded", () => {
    "use strict";

    // ----------------------------------------------------------------------
    // 1. CONFIGURATION
    // ----------------------------------------------------------------------

    const API_INFO = "/api/info";
    const API_ROUTE = "/api/route";

    // Change this URL if your offline tiles are stored elsewhere.
    // Example expected structure:
    // static/tiles/zoom/x/y.png
    const TILE_URL = "/static/tiles/{z}/{x}/{y}.png";
    const API_TILES = "/api/tiles";

    const DEFAULT_CENTER = [18.5204, 73.8567];
    const DEFAULT_ZOOM = 13;

    const GPS_TIMEOUT = 20000;
    const OFF_ROUTE_THRESHOLD_M = 100;
    const ARRIVAL_THRESHOLD_M = 25;

    // ----------------------------------------------------------------------
    // 2. HTML ELEMENTS
    // ----------------------------------------------------------------------

    const $ = (id) => document.getElementById(id);

    const elements = {
        map: $("map"),
        panel: $("panel"),

        startInput: $("start-input"),
        endInput: $("end-input"),
        speed: $("speed"),

        pickStart: $("btn-pick-start"),
        pickEnd: $("btn-pick-end"),
        locate: $("btn-locate"),
        swap: $("btn-swap"),
        route: $("btn-route"),
        clear: $("btn-clear"),

        status: $("status"),
        result: $("result"),
        distance: $("sum-distance"),
        time: $("sum-time"),
        steps: $("steps"),

        pickHint: $("pick-hint"),

        navButton: $("btn-nav"),
        navStop: $("btn-nav-stop"),
        navBanner: $("nav-banner"),
        navDistance: $("nav-distance"),
        navInstruction: $("nav-instruction"),
        navRemaining: $("nav-remaining")
    };

    if (typeof L === "undefined") {
        elements.status.textContent =
            "Leaflet could not load. Check static/leaflet/leaflet.js.";
        elements.status.classList.add("error");
        return;
    }

    // ----------------------------------------------------------------------
    // 3. APPLICATION STATE
    // ----------------------------------------------------------------------

    let startPoint = null;
    let endPoint = null;

    let startMarker = null;
    let endMarker = null;
    let gpsMarker = null;
    let accuracyCircle = null;
    let routeLayer = null;

    let routeData = null;
    let routePoints = [];
    let routeSteps = [];
    let cumulativeDistances = [];

    let pickMode = null;

    let navigationActive = false;
    let watchId = null;
    let currentPosition = null;

    let routeRequestId = 0;
    let navigationSegmentHint = 0;

    // ----------------------------------------------------------------------
    // 4. INITIALIZE LEAFLET MAP
    // ----------------------------------------------------------------------

    const map = L.map("map").setView(
        DEFAULT_CENTER,
        DEFAULT_ZOOM
    );

    // The tile layer is filled in from the server manifest so that the zoom
    // limits always match the tiles that were actually rendered offline.
    const tileLayer = L.tileLayer(TILE_URL, {
        minZoom: 0,
        maxZoom: 19,
        attribution: "&copy; OpenStreetMap contributors"
    });

    let tileInfo = null;

    async function loadTileInfo() {
        try {
            const response = await fetch(API_TILES);

            if (!response.ok) {
                return;
            }

            const info = await response.json();

            if (!info || !info.available) {
                console.warn("No offline tiles available on the server.");
                return;
            }

            tileInfo = info;

            const zooms = Array.isArray(info.zooms) ? info.zooms : [];
            if (zooms.length) {
                tileLayer.options.minZoom = zooms[0];
                tileLayer.options.maxZoom = zooms[zooms.length - 1];
            }
        } catch (error) {
            console.warn("Could not load the tile manifest:", error);
        }
    }

    async function initTiles() {
        await loadTileInfo();
        tileLayer.addTo(map);
    }

    // Load coverage information from Flask.
    async function loadMapInfo() {
        try {
            const response = await fetch(API_INFO);

            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }

            const info = await response.json();

            if (
                Array.isArray(info.center) &&
                info.center.length === 2 &&
                info.center.every(Number.isFinite)
            ) {
                map.setView(info.center, DEFAULT_ZOOM);
            }

            if (info.bounds) {
                const b = info.bounds;

                if (
                    [b.south, b.north, b.west, b.east]
                        .every(Number.isFinite) &&
                    b.south <= b.north &&
                    b.west <= b.east
                ) {
                    map.fitBounds([
                        [b.south, b.west],
                        [b.north, b.east]
                    ], {
                        padding: [20, 20],
                        maxZoom: DEFAULT_ZOOM
                    });
                }
            }

            const tiles = info.tiles;
            const tileText = tiles && tiles.available
                ? ` ${tiles.count ?? "?"} map tiles (zoom ${(tiles.zooms || []).join("-")}).`
                : " No map tiles found - run python tools/build_tiles.py.";

            setStatus(
                `Offline road network loaded: ${info.nodes ?? "?"} nodes.${tileText}`,
                tiles && tiles.available ? "success" : ""
            );
        } catch (error) {
            console.warn("Could not load map information:", error);

            setStatus(
                "Map interface ready. Could not retrieve map coverage information.",
                "error"
            );
        }
    }

    // ----------------------------------------------------------------------
    // 5. GENERAL HELPERS
    // ----------------------------------------------------------------------

    function setStatus(message, type = "") {
        elements.status.textContent = message;

        elements.status.classList.remove("error", "success");

        if (type === "error" || type === "success") {
            elements.status.classList.add(type);
        }
    }

    function parseCoordinates(value) {
        if (!value || !value.trim()) {
            return null;
        }

        const parts = value.split(",").map(part => part.trim());

        if (
            parts.length !== 2 ||
            parts.some(part => part === "")
        ) {
            return null;
        }

        const lat = Number(parts[0]);
        const lon = Number(parts[1]);

        if (
            !Number.isFinite(lat) ||
            !Number.isFinite(lon) ||
            lat < -90 || lat > 90 ||
            lon < -180 || lon > 180
        ) {
            return null;
        }

        return L.latLng(lat, lon);
    }

    function formatCoordinates(point) {
        return `${point.lat.toFixed(6)}, ${point.lng.toFixed(6)}`;
    }

    function formatDistance(metres) {
        if (!Number.isFinite(metres) || metres < 0) {
            return "—";
        }

        return metres >= 1000
            ? `${(metres / 1000).toFixed(1)} km`
            : `${Math.round(metres)} m`;
    }

    function formatDuration(seconds) {
        if (!Number.isFinite(seconds) || seconds < 0) {
            return "—";
        }

        const minutes = Math.round(seconds / 60);

        if (minutes < 60) {
            return `${minutes} min`;
        }

        const hours = Math.floor(minutes / 60);
        const remainder = minutes % 60;

        return remainder
            ? `${hours} hr ${remainder} min`
            : `${hours} hr`;
    }

    function updateRouteButton() {
        elements.route.disabled = !(startPoint && endPoint);
    }

    function clearInvalid(input) {
        input.classList.remove("invalid");
        input.removeAttribute("aria-invalid");
    }

    function markInvalid(input) {
        input.classList.add("invalid");
        input.setAttribute("aria-invalid", "true");
    }

    function createPin(type) {
        return L.divIcon({
            className: "",
            html: `<div class="pin pin-${type}"></div>`,
            iconSize: [22, 22],
            iconAnchor: [11, 11]
        });
    }

    // ----------------------------------------------------------------------
    // 6. START AND DESTINATION MARKERS
    // ----------------------------------------------------------------------

    function setPoint(type, point) {
        const isStart = type === "start";
        const input = isStart
            ? elements.startInput
            : elements.endInput;

        const oldMarker = isStart
            ? startMarker
            : endMarker;

        if (oldMarker) {
            map.removeLayer(oldMarker);
        }

        const marker = L.marker(point, {
            icon: createPin(isStart ? "start" : "end")
        }).addTo(map);

        marker.bindPopup(
            isStart ? "Starting point" : "Destination"
        );

        if (isStart) {
            startPoint = point;
            startMarker = marker;
        } else {
            endPoint = point;
            endMarker = marker;
        }

        input.value = formatCoordinates(point);
        clearInvalid(input);

        updateRouteButton();
    }

    function removeRoute() {
        // Ignore any earlier route request that is still pending.
        routeRequestId++;

        if (routeLayer) {
            map.removeLayer(routeLayer);
            routeLayer = null;
        }

        routeData = null;
        routePoints = [];
        routeSteps = [];
        cumulativeDistances = [];

        elements.result.hidden = true;
        elements.steps.replaceChildren();

        elements.distance.textContent = "–";
        elements.time.textContent = "–";
    }

    // ----------------------------------------------------------------------
    // 7. PICK LOCATIONS ON THE MAP
    // ----------------------------------------------------------------------

    function stopPicking() {
        pickMode = null;

        elements.pickHint.hidden = true;

        elements.pickStart.classList.remove("active");
        elements.pickEnd.classList.remove("active");

        elements.map.classList.remove("picking");
    }

    function beginPicking(type) {
        pickMode = type;

        elements.pickHint.hidden = false;
        elements.pickHint.textContent =
            type === "start"
                ? "Click the map to choose your starting point."
                : "Click the map to choose your destination.";

        elements.pickStart.classList.toggle("active", type === "start");
        elements.pickEnd.classList.toggle("active", type === "end");

        elements.map.classList.add("picking");
    }

    elements.pickStart.addEventListener("click", () => {
        beginPicking("start");
    });

    elements.pickEnd.addEventListener("click", () => {
        beginPicking("end");
    });

    map.on("click", event => {
        if (!pickMode) return;

        const selectedType = pickMode;

        stopPicking();
        setPoint(selectedType, event.latlng);
        removeRoute();

        setStatus(
            selectedType === "start"
                ? "Starting point selected."
                : "Destination selected.",
            "success"
        );
    });

    // ----------------------------------------------------------------------
    // 8. MANUAL COORDINATE INPUT
    // ----------------------------------------------------------------------

    elements.startInput.addEventListener("change", () => {
        const point = parseCoordinates(elements.startInput.value);

        if (!point) {
            markInvalid(elements.startInput);

            setStatus(
                "Invalid starting coordinates. Enter latitude, longitude.",
                "error"
            );
            return;
        }

        setPoint("start", point);
        removeRoute();

        setStatus("Starting point updated.", "success");
    });

    elements.endInput.addEventListener("change", () => {
        const point = parseCoordinates(elements.endInput.value);

        if (!point) {
            markInvalid(elements.endInput);

            setStatus(
                "Invalid destination coordinates. Enter latitude, longitude.",
                "error"
            );
            return;
        }

        setPoint("end", point);
        removeRoute();

        setStatus("Destination updated.", "success");
    });

    // ----------------------------------------------------------------------
    // 9. GPS LOCATION
    // ----------------------------------------------------------------------

    function updateGPSMarker(point, accuracy) {
        if (gpsMarker) {
            gpsMarker.setLatLng(point);
        } else {
            gpsMarker = L.marker(point, {
                icon: L.divIcon({
                    className: "",
                    html: '<div class="pin pin-you"></div>',
                    iconSize: [22, 22],
                    iconAnchor: [11, 11]
                }),
                zIndexOffset: 1000
            }).addTo(map);
        }

        gpsMarker.bindPopup("Your current GPS location");

        if (Number.isFinite(accuracy)) {
            if (accuracyCircle) {
                accuracyCircle.setLatLng(point);
                accuracyCircle.setRadius(accuracy);
            } else {
                accuracyCircle = L.circle(point, {
                    radius: accuracy,
                    color: "#2563eb",
                    weight: 1,
                    fillOpacity: 0.08
                }).addTo(map);
            }
        }
    }

    function handleGPSError(error) {
        const messages = {
            1: "Location permission denied. Allow location access in your browser.",
            2: "GPS position unavailable. Check your device's location signal.",
            3: "GPS request timed out. Try again in an open area."
        };

        setStatus(
            messages[error.code] || "Unable to obtain GPS location.",
            "error"
        );
    }

    function locateUser() {
        if (!navigator.geolocation) {
            setStatus(
                "This browser does not support geolocation.",
                "error"
            );
            return;
        }

        elements.locate.disabled = true;
        setStatus("Obtaining your current GPS location...");

        navigator.geolocation.getCurrentPosition(
            position => {
                currentPosition = L.latLng(
                    position.coords.latitude,
                    position.coords.longitude
                );

                updateGPSMarker(
                    currentPosition,
                    position.coords.accuracy
                );

                setPoint("start", currentPosition);

                map.setView(currentPosition, 16);

                removeRoute();

                setStatus(
                    `GPS location found. Accuracy approximately ${Math.round(position.coords.accuracy)} m.`,
                    "success"
                );

                elements.locate.disabled = false;
            },
            error => {
                handleGPSError(error);
                elements.locate.disabled = false;
            },
            {
                enableHighAccuracy: true,
                timeout: GPS_TIMEOUT,
                maximumAge: 0
            }
        );
    }

    elements.locate.addEventListener("click", locateUser);

    // ----------------------------------------------------------------------
    // 10. SWAP START AND DESTINATION
    // ----------------------------------------------------------------------

    elements.swap.addEventListener("click", () => {
        const oldStart = startPoint;
        const oldEnd = endPoint;

        if (oldEnd) {
            setPoint("start", oldEnd);
        } else {
            startPoint = null;
            startMarker?.remove();
            startMarker = null;
            elements.startInput.value = "";
        }

        if (oldStart) {
            setPoint("end", oldStart);
        } else {
            endPoint = null;
            endMarker?.remove();
            endMarker = null;
            elements.endInput.value = "";
        }

        removeRoute();
        updateRouteButton();

        setStatus("Starting point and destination swapped.", "success");
    });

    // ----------------------------------------------------------------------
    // 11. CALCULATE ROUTE THROUGH THE REAL FLASK API
    // ----------------------------------------------------------------------

    elements.route.addEventListener("click", calculateRoute);

    async function calculateRoute() {
        if (!startPoint || !endPoint) {
            setStatus(
                "Select both a starting point and a destination.",
                "error"
            );
            return;
        }

        if (map.distance(startPoint, endPoint) < 1) {
            setStatus(
                "Start and destination must be different.",
                "error"
            );
            return;
        }

        const speed = Number(elements.speed.value);

        if (!Number.isFinite(speed) || speed <= 0) {
            setStatus("Choose a valid average speed.", "error");
            return;
        }

        const requestId = ++routeRequestId;

        elements.route.disabled = true;
        setStatus("Calculating route using your offline road network...");

        try {
            const response = await fetch(API_ROUTE, {
                method: "POST",
                headers: {
                    "Content-Type": "application/json"
                },
                body: JSON.stringify({
                    start: {
                        lat: startPoint.lat,
                        lon: startPoint.lng
                    },
                    end: {
                        lat: endPoint.lat,
                        lon: endPoint.lng
                    },
                    speed_kmh: speed
                })
            });

            const data = await response.json();

            if (!response.ok) {
                throw new Error(
                    data.error || `Route request failed: HTTP ${response.status}`
                );
            }

            // Do not display a stale response if the user changed the route.
            if (requestId !== routeRequestId) return;

            if (!Array.isArray(data.path) || data.path.length < 2) {
                throw new Error("The server returned an invalid route path.");
            }

            if (!Array.isArray(data.steps)) {
                throw new Error("The server did not return route directions.");
            }

            displayRoute(data);

        } catch (error) {
            if (requestId !== routeRequestId) return;

            console.error("Route calculation error:", error);

            setStatus(error.message || "Could not calculate the route.", "error");
        } finally {
            if (requestId === routeRequestId) {
                updateRouteButton();
            }
        }
    }

    // ----------------------------------------------------------------------
    // 12. DRAW THE ROUTE AND SHOW THE RESULT
    // ----------------------------------------------------------------------

    function displayRoute(data) {
        if (routeLayer) {
            map.removeLayer(routeLayer);
        }

        routeData = data;

        routePoints = data.path.map(coordinate => {
            if (
                !Array.isArray(coordinate) ||
                coordinate.length < 2 ||
                !Number.isFinite(Number(coordinate[0])) ||
                !Number.isFinite(Number(coordinate[1]))
            ) {
                throw new Error("Invalid latitude/longitude in route path.");
            }

            // nav_engine.py returns [latitude, longitude].
            return L.latLng(
                Number(coordinate[0]),
                Number(coordinate[1])
            );
        });

        routeSteps = data.steps;

        cumulativeDistances = [0];

        for (let i = 1; i < routePoints.length; i++) {
            const segmentLength = map.distance(
                routePoints[i - 1],
                routePoints[i]
            );

            cumulativeDistances.push(
                cumulativeDistances[i - 1] + segmentLength
            );
        }

        routeLayer = L.polyline(routePoints, {
            color: "#2563eb",
            weight: 5,
            opacity: 0.9
        }).addTo(map);

        map.fitBounds(routeLayer.getBounds(), {
            padding: [35, 35]
        });

        // Show the actual road-snapped endpoints returned by Flask.
        if (Array.isArray(data.snapped_start) && data.snapped_start.length === 2) {
            startMarker?.setLatLng(data.snapped_start);
        }

        if (Array.isArray(data.snapped_end) && data.snapped_end.length === 2) {
            endMarker?.setLatLng(data.snapped_end);
        }

        elements.distance.textContent =
            formatDistance(Number(data.distance_m));

        elements.time.textContent =
            formatDuration(Number(data.duration_s));

        renderDirections();

        elements.result.hidden = false;

        setStatus(
            `Route found: ${formatDistance(Number(data.distance_m))}.`,
            "success"
        );
    }

    // ----------------------------------------------------------------------
    // 13. RENDER TURN-BY-TURN DIRECTIONS
    // ----------------------------------------------------------------------

    function renderDirections() {
        elements.steps.replaceChildren();

        routeSteps.forEach((step, index) => {
            const item = document.createElement("li");
            item.className = "step";

            const icon = document.createElement("span");
            icon.className = "step-icon";
            icon.textContent = index === routeSteps.length - 1 ? "●" : "↗";
            icon.setAttribute("aria-hidden", "true");

            const body = document.createElement("span");
            body.className = "step-body";

            const text = document.createElement("span");
            text.className = "step-text";
            text.textContent = step.text || "Continue along the route";

            const distance = document.createElement("span");
            distance.className = "step-dist";

            const at = Number(step.at_m);
            distance.textContent = Number.isFinite(at)
                ? `${formatDistance(at)} from start`
                : "";

            body.append(text, distance);
            item.append(icon, body);

            item.addEventListener("click", () => {
                if (
                    Number.isFinite(Number(step.lat)) &&
                    Number.isFinite(Number(step.lon))
                ) {
                    map.setView(
                        [Number(step.lat), Number(step.lon)],
                        17
                    );
                }

                setActiveStep(index);
            });

            elements.steps.appendChild(item);
        });
    }

    function setActiveStep(activeIndex) {
        [...elements.steps.children].forEach((item, index) => {
            item.classList.toggle("active", index === activeIndex);
            item.classList.toggle("done", index < activeIndex);
        });
    }

    // Recalculate ETA when the selected speed changes.
    elements.speed.addEventListener("change", () => {
        if (routeData) {
            calculateRoute();
        }
    });

    // ----------------------------------------------------------------------
    // 14. GPS LIVE NAVIGATION
    // ----------------------------------------------------------------------

    elements.navButton.addEventListener("click", startNavigation);
    elements.navStop.addEventListener("click", stopNavigation);

    function startNavigation() {
        if (!routeData || !routePoints.length) {
            setStatus("Calculate a route before starting navigation.", "error");
            return;
        }

        if (!navigator.geolocation) {
            setStatus("GPS is not supported by this browser.", "error");
            return;
        }

        if (navigationActive) return;

        navigationActive = true;
        navigationSegmentHint = 0;

        elements.navBanner.hidden = false;

        setStatus("Live navigation started. Waiting for GPS updates...");

        watchId = navigator.geolocation.watchPosition(
            onNavigationPosition,
            handleGPSError,
            {
                enableHighAccuracy: true,
                maximumAge: 1000,
                timeout: GPS_TIMEOUT
            }
        );
    }

    function onNavigationPosition(position) {
        if (!navigationActive) return;

        currentPosition = L.latLng(
            position.coords.latitude,
            position.coords.longitude
        );

        updateGPSMarker(
            currentPosition,
            position.coords.accuracy
        );

        // Keep the current location visible during navigation.
        map.panTo(currentPosition);

        updateNavigationProgress(currentPosition);
    }

    // Project a GPS point onto a route segment using a local
    // flat-earth approximation. Returns distance from GPS point
    // to segment and fractional position along the segment.
    function projectOnSegment(point, a, b) {
        const earthRadius = 6371000;
        const lat0 = point.lat * Math.PI / 180;

        function xy(p) {
            return {
                x: earthRadius * p.lng * Math.PI / 180 * Math.cos(lat0),
                y: earthRadius * p.lat * Math.PI / 180
            };
        }

        const p = xy(point);
        const pa = xy(a);
        const pb = xy(b);

        const dx = pb.x - pa.x;
        const dy = pb.y - pa.y;

        const lengthSquared = dx * dx + dy * dy;

        let t = 0;

        if (lengthSquared > 0) {
            t = Math.max(0, Math.min(1,
                ((p.x - pa.x) * dx + (p.y - pa.y) * dy) /
                lengthSquared
            ));
        }

        const nearestX = pa.x + t * dx;
        const nearestY = pa.y + t * dy;

        return {
            distance: Math.hypot(
                p.x - nearestX,
                p.y - nearestY
            ),
            progress: cumulativeDistances[0] +
                (cumulativeDistances.length > 1
                    ? 0
                    : 0),
            fraction: t
        };
    }

    function locateOnRoute(position) {
        const lower = Math.max(0, navigationSegmentHint - 3);

        const upper = Math.min(
            routePoints.length - 2,
            navigationSegmentHint + 40
        );

        let best = null;

        for (let i = lower; i <= upper; i++) {
            const projection = projectOnSegment(
                position,
                routePoints[i],
                routePoints[i + 1]
            );

            const segmentLength =
                cumulativeDistances[i + 1] - cumulativeDistances[i];

            const progress =
                cumulativeDistances[i] +
                projection.fraction * segmentLength;

            if (!best || projection.distance < best.distance) {
                best = {
                    distance: projection.distance,
                    progress,
                    segment: i
                };
            }
        }

        return best;
    }

    function updateNavigationProgress(position) {
        if (routePoints.length < 2) return;

        const located = locateOnRoute(position);

        if (!located) return;

        navigationSegmentHint = located.segment;

        const totalDistance = Number(routeData.distance_m);
        const progress = Math.min(
            totalDistance,
            Math.max(0, located.progress)
        );

        const remaining = Math.max(0, totalDistance - progress);

        if (remaining <= ARRIVAL_THRESHOLD_M) {
            elements.navDistance.textContent = "Arrived";
            elements.navInstruction.textContent =
                "You have arrived at your destination.";
            elements.navRemaining.textContent = "Navigation complete";

            setStatus("You have arrived at your destination.", "success");

            stopNavigation(false);
            return;
        }

        if (located.distance > OFF_ROUTE_THRESHOLD_M) {
            elements.navInstruction.textContent =
                "You appear to be off the planned route.";
            elements.navDistance.textContent =
                formatDistance(located.distance);
            elements.navRemaining.textContent =
                `${formatDistance(remaining)} remaining`;

            setStatus(
                "You appear to be off route. Return to the planned road.",
                "error"
            );

            return;
        }

        // Find the next instruction ahead of the current route position.
        let nextIndex = routeSteps.findIndex(step =>
            Number(step.at_m) > progress + 10
        );

        if (nextIndex < 0) {
            nextIndex = routeSteps.length - 1;
        }

        const nextStep = routeSteps[nextIndex];

        const distanceToTurn = Math.max(
            0,
            Number(nextStep.at_m || 0) - progress
        );

        elements.navDistance.textContent =
            formatDistance(distanceToTurn);

        elements.navInstruction.textContent =
            nextStep.text || "Continue along the route";

        const speed = Number(elements.speed.value);
        const remainingSeconds =
            speed > 0 ? remaining / (speed / 3.6) : NaN;

        elements.navRemaining.textContent =
            `${formatDistance(remaining)} remaining · ${formatDuration(remainingSeconds)}`;

        setActiveStep(nextIndex);
    }

    function stopNavigation(showMessage = true) {
        navigationActive = false;

        if (watchId !== null) {
            navigator.geolocation.clearWatch(watchId);
            watchId = null;
        }

        elements.navBanner.hidden = true;

        if (showMessage) {
            setStatus("Live navigation stopped.", "success");
        }
    }

    // ----------------------------------------------------------------------
    // 15. CLEAR / RESET
    // ----------------------------------------------------------------------

    elements.clear.addEventListener("click", () => {
        stopNavigation(false);
        stopPicking();
        removeRoute();

        if (startMarker) map.removeLayer(startMarker);
        if (endMarker) map.removeLayer(endMarker);
        if (gpsMarker) map.removeLayer(gpsMarker);
        if (accuracyCircle) map.removeLayer(accuracyCircle);

        startMarker = null;
        endMarker = null;
        gpsMarker = null;
        accuracyCircle = null;

        startPoint = null;
        endPoint = null;
        currentPosition = null;

        elements.startInput.value = "";
        elements.endInput.value = "";

        clearInvalid(elements.startInput);
        clearInvalid(elements.endInput);

        updateRouteButton();

        map.setView(DEFAULT_CENTER, DEFAULT_ZOOM);

        setStatus(
            "Cleared. Select a starting point or use GPS.",
            "success"
        );
    });

    // ----------------------------------------------------------------------
    // 16. INITIAL STATE
    // ----------------------------------------------------------------------

    updateRouteButton();
    loadMapInfo();
    initTiles();
});
