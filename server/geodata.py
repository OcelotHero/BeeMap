"""
Demo map data for Heilbronn: land cover, wild-bee zones and registered
apiaries.

IMPORTANT: these shapes are hand-placed approximations for the hackathon
demo, NOT official data. Names refer to real districts so the map reads
naturally, but the outlines, habitats and apiaries are illustrative.

For real use, replace with:
  * land cover: OpenStreetMap landuse/natural tags (orchard, vineyard,
    meadow, forest, farmland, allotments, park) or ATKIS/ALKIS
  * wild-bee zones: protected areas and biotope maps from LUBW
    (Landesanstalt für Umwelt Baden-Württemberg) plus local expert input
  * apiaries: the beekeeping association's / veterinary office's register
"""
from __future__ import annotations

import math

M_PER_DEG = 111320.0


def _ll(lat0, lon0, x, y):
    return (lat0 + y / M_PER_DEG, lon0 + x / (M_PER_DEG * math.cos(math.radians(lat0))))


def blob(lat, lon, rx, ry, rot=0.0, wobble=(1.0, 0.9, 1.05, 0.85, 1.0, 0.95, 1.1, 0.9)):
    """An organic-looking polygon around (lat, lon), radii in metres."""
    pts = []
    n = len(wobble)
    r = math.radians(rot)
    for k in range(n):
        a = 2 * math.pi * k / n
        x, y = rx * wobble[k] * math.cos(a), ry * wobble[k] * math.sin(a)
        xr, yr = x * math.cos(r) - y * math.sin(r), x * math.sin(r) + y * math.cos(r)
        pts.append(_ll(lat, lon, xr, yr))
    return pts


def band(points, half_width_m):
    """A strip along a polyline (lat, lon points), e.g. a river or hedge."""
    left, right = [], []
    for i, (la, lo) in enumerate(points):
        a = points[max(i - 1, 0)]
        b = points[min(i + 1, len(points) - 1)]
        kx = M_PER_DEG * math.cos(math.radians(la))
        dx, dy = (b[1] - a[1]) * kx, (b[0] - a[0]) * M_PER_DEG
        n = math.hypot(dx, dy) or 1
        nx, ny = -dy / n, dx / n
        left.append(_ll(la, lo, nx * half_width_m, ny * half_width_m))
        right.append(_ll(la, lo, -nx * half_width_m, -ny * half_width_m))
    return left + right[::-1]


NECKAR = [(49.090, 9.199), (49.105, 9.201), (49.118, 9.204), (49.130, 9.207), (49.142, 9.208),
          (49.155, 9.205), (49.166, 9.206), (49.178, 9.211), (49.195, 9.216)]

# Land cover, drawn in order (later shapes win where they overlap).
# kind -> flowering profile in ecology.py
LANDCOVER = [
    # outskirts: fields to the west and north-east
    {"kind": "arable", "name": "Fields west of Frankenbach", "poly": blob(49.150, 9.150, 1600, 2600, 0)},
    {"kind": "arable", "name": "Fields towards Neckarsulm", "poly": blob(49.180, 9.255, 1500, 900, 20)},
    {"kind": "arable", "name": "Fields south of Sontheim", "poly": blob(49.093, 9.180, 1700, 700, 0)},
    {"kind": "rapeseed", "name": "Rapeseed, Neckargartach", "poly": blob(49.176, 9.175, 500, 300, 15)},
    {"kind": "rapeseed", "name": "Rapeseed, Klingenberg", "poly": blob(49.094, 9.165, 420, 260, -10)},
    # the city
    {"kind": "urban", "name": "City centre", "poly": blob(49.142, 9.220, 1100, 1300, 0)},
    {"kind": "urban", "name": "Böckingen", "poly": blob(49.133, 9.183, 1000, 900, 0)},
    {"kind": "urban", "name": "Sontheim", "poly": blob(49.115, 9.208, 750, 600, 0)},
    {"kind": "urban", "name": "Neckargartach", "poly": blob(49.163, 9.193, 700, 600, 0)},
    {"kind": "urban", "name": "Frankenbach", "poly": blob(49.150, 9.168, 600, 500, 0)},
    {"kind": "gardens", "name": "Allotments, Böckingen", "poly": blob(49.124, 9.192, 300, 220, 30)},
    {"kind": "gardens", "name": "Gardens, Heilbronn-Ost", "poly": blob(49.146, 9.243, 450, 380, 0)},
    {"kind": "park", "name": "Pfühlpark", "poly": blob(49.149, 9.232, 260, 200, 0)},
    {"kind": "park", "name": "Wertwiesenpark", "poly": blob(49.128, 9.213, 320, 260, 10)},
    {"kind": "lime_avenue", "name": "Lime-tree avenues, centre", "poly": blob(49.141, 9.222, 300, 120, 80)},
    # vineyards, orchards, forest, hedges
    {"kind": "vineyard", "name": "Vineyards, Wartberg", "poly": blob(49.160, 9.245, 900, 600, 30)},
    {"kind": "vineyard", "name": "Vineyards, Stiftsberg", "poly": blob(49.105, 9.228, 700, 450, -20)},
    {"kind": "orchard", "name": "Orchard meadows, Frankenbach", "poly": blob(49.160, 9.162, 500, 380, 10)},
    {"kind": "orchard", "name": "Orchard meadows, Böckingen west", "poly": blob(49.124, 9.160, 520, 400, -15)},
    {"kind": "orchard", "name": "Orchard meadows, Heilbronn-Ost", "poly": blob(49.133, 9.262, 450, 350, 0)},
    {"kind": "orchard", "name": "Orchard meadows, Klingenberg", "poly": blob(49.104, 9.172, 420, 330, 0)},
    {"kind": "forest", "name": "Stadtwald", "poly": blob(49.140, 9.282, 900, 1700, 5)},
    {"kind": "hedge", "name": "Forest edge, Stadtwald", "poly": band([(49.120, 9.268), (49.140, 9.264), (49.160, 9.268)], 40)},
    {"kind": "hedge", "name": "Hedgerow, Frankenbach", "poly": band([(49.140, 9.150), (49.145, 9.160), (49.150, 9.170)], 15)},
    {"kind": "meadow", "name": "Meadows, Neckar floodplain", "poly": blob(49.106, 9.196, 450, 250, 70)},
    {"kind": "flower_strip", "name": "Flower strip, Frankenbach fields", "poly": band([(49.142, 9.140), (49.152, 9.142)], 6)},
    {"kind": "flower_strip", "name": "Flower strip, Neckargartach", "poly": band([(49.172, 9.180), (49.174, 9.192)], 6)},
    {"kind": "water", "name": "Neckar", "poly": band(NECKAR, 45)},
]

# Habitats that should be kept free of extra honeybee competition.
WILD_BEE_ZONES = [
    {"id": "wz1", "name": "Wild-bee meadow, Wertwiesenpark", "poly": blob(49.1265, 9.2105, 170, 130, 15),
     "note": "Sandy meadow with nesting sites for ground-nesting bees"},
    {"id": "wz2", "name": "Dry slopes, Wartberg", "poly": blob(49.162, 9.240, 420, 260, 30),
     "note": "Dry-stone walls and warm slopes between vineyards"},
    {"id": "wz3", "name": "Floodplain meadows, Sontheim", "poly": blob(49.106, 9.196, 330, 180, 70),
     "note": "Extensive meadows along the Neckar"},
    {"id": "wz4", "name": "Old sand pit, Frankenbach", "poly": blob(49.157, 9.150, 220, 170, 0),
     "note": "Open sand banks used by many solitary bees"},
    {"id": "wz5", "name": "Orchard meadow reserve, Klingenberg", "poly": blob(49.104, 9.172, 300, 230, 0),
     "note": "Old fruit trees with dead wood for wood-nesting bees"},
]

# Registered apiaries without a smart-hive camera (stations count too).
APIARIES = [
    {"id": "ap1", "name": "Apiary Kreuzgrund", "owner": "R. Weber", "lat": 49.1295, "lon": 9.2165, "colonies": 4},
    {"id": "ap2", "name": "Apiary Südbahnhof", "owner": "Imkerverein HN", "lat": 49.1310, "lon": 9.2070, "colonies": 6},
    {"id": "ap3", "name": "School garden apiary", "owner": "Elly-Heuss-Knapp-Gymnasium", "lat": 49.1235, "lon": 9.2130, "colonies": 2},
    {"id": "ap4", "name": "Apiary Klingenberg", "owner": "T. Maier", "lat": 49.1010, "lon": 9.1780, "colonies": 8},
    {"id": "ap5", "name": "Apiary Hipfelhof", "owner": "S. König", "lat": 49.1700, "lon": 9.2500, "colonies": 5},
    {"id": "ap6", "name": "Apiary Böckingen Nord", "owner": "L. Arnold", "lat": 49.1390, "lon": 9.1800, "colonies": 3},
    {"id": "ap7", "name": "Rooftop hives, centre", "owner": "Hotel am Markt", "lat": 49.1430, "lon": 9.2190, "colonies": 2},
]

# Demo hive requests so the city's review screen isn't empty
DEMO_REQUESTS = [
    {"beekeeper": "J. Fischer", "place": "Near Wertwiesenpark", "lat": 49.1280, "lon": 9.2120, "colonies": 2, "camera": True,
     "note": "Moving two colonies from my garden; I'd fit a smart-hive camera."},
    {"beekeeper": "M. Schulz", "place": "Frankenbach, allotments", "lat": 49.1450, "lon": 9.1600, "colonies": 1, "camera": False,
     "note": "First colony, mentored by the local association."},
    {"beekeeper": "K. Wolf", "place": "Inside nature reserve edge, Wartberg", "lat": 49.1610, "lon": 9.2390, "colonies": 4, "camera": True,
     "note": "Vineyard owner, wants pollination for the cover crops."},
]


# =================================================================== loading
# Every area is stored as polys = [{"outer": ring, "inner": [holes]}].
# BEEMAP_GEODATA=auto (default): OpenStreetMap data if tools/update_osm_data.py
# has been run, otherwise the demo shapes above. =demo or =osm to force.
import json as _json
import os as _os
from pathlib import Path as _Path

OSM_FILE = _Path(__file__).with_name("osm_data.json")


def _as_polys(items):
    out = []
    for f in items:
        f = dict(f)
        if "poly" in f:
            f["polys"] = [{"outer": [list(p) for p in f.pop("poly")], "inner": []}]
        out.append(f)
    return out


DEMO_LANDCOVER = _as_polys(LANDCOVER)
DEMO_WILD_BEE_ZONES = _as_polys(WILD_BEE_ZONES)
LINES: list = []
PLACES: list = []
SOURCE = {"kind": "demo", "label": "Demo map data (hand-placed shapes)", "attribution": None, "fetched": None}


def load(mode: str | None = None):
    global LANDCOVER, WILD_BEE_ZONES, LINES, PLACES, SOURCE
    mode = (mode or _os.environ.get("BEEMAP_GEODATA", "auto")).lower()
    data = None
    if mode in ("auto", "osm") and OSM_FILE.exists():
        try:
            data = _json.loads(OSM_FILE.read_text())
        except ValueError:
            data = None
    if mode == "osm" and data is None:
        raise SystemExit("BEEMAP_GEODATA=osm but server/osm_data.json is missing. Run tools/update_osm_data.py.")
    if data:
        LANDCOVER = data["landcover"]
        LINES = data.get("lines", [])
        PLACES = data.get("places", [])
        # protected areas from OSM; keep the demo zones only if OSM has none here
        WILD_BEE_ZONES = data.get("wild_bee_zones") or DEMO_WILD_BEE_ZONES
        SOURCE = {"kind": "osm", "label": "OpenStreetMap", "attribution": data.get("attribution"),
                  "fetched": data.get("fetched"), "counts": data.get("counts", {}),
                  "zones_from": "osm" if data.get("wild_bee_zones") else "demo"}
    else:
        LANDCOVER, WILD_BEE_ZONES, LINES, PLACES = DEMO_LANDCOVER, DEMO_WILD_BEE_ZONES, [], []
        SOURCE = {"kind": "demo", "label": "Demo map data (hand-placed shapes)", "attribution": None, "fetched": None}
    return SOURCE


load()
