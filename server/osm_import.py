"""
OpenStreetMap land cover for BeeMap (Overpass API): query, convert, save.
Used by the server's on-demand fetch (ondemand.py) and tools/update_osm_data.py.
Data © OpenStreetMap contributors, ODbL.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import sys
from pathlib import Path

import requests

OUT = Path(__file__).with_name("osm_data.json")
ENDPOINTS = ["https://overpass-api.de/api/interpreter", "https://lz4.overpass-api.de/api/interpreter",
             "https://z.overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter"]
MIN_AREAS = 50          # Heilbronn has thousands of mapped areas; far fewer means a broken answer
HEADERS = {"User-Agent": "BeeMap hackathon prototype (pollinator map, one-off land-cover download)"}
BBOX = (49.080, 9.120, 49.200, 9.310)          # same as server/ecology.py

LANDUSE = "orchard|meadow|vineyard|forest|farmland|allotments|grass|residential|commercial|industrial|retail|reservoir|village_green|flowerbed|plant_nursery"
NATURAL = "wood|scrub|heath|water|grassland"
LEISURE = "park|garden|nature_reserve"


def query(bbox) -> str:
    s, w, n, e = bbox
    b = f"({s},{w},{n},{e})"
    return f"""[out:json][timeout:240];
(
  way["landuse"~"^({LANDUSE})$"]{b};   relation["landuse"~"^({LANDUSE})$"]{b};
  way["natural"~"^({NATURAL})$"]{b};   relation["natural"~"^({NATURAL})$"]{b};
  way["leisure"~"^({LEISURE})$"]{b};   relation["leisure"~"^({LEISURE})$"]{b};
  way["waterway"="riverbank"]{b};
  way["boundary"="protected_area"]{b}; relation["boundary"="protected_area"]{b};
  way["barrier"="hedge"]{b};           way["natural"="tree_row"]{b};
  node["place"~"^(suburb|quarter|neighbourhood|village|hamlet|town)$"]{b};
);
out geom;"""


# ------------------------------------------------------------------ classify
def kind_of(tags: dict) -> str | None:
    lu, nat, lei = tags.get("landuse"), tags.get("natural"), tags.get("leisure")
    if nat == "water" or lu == "reservoir" or tags.get("waterway") == "riverbank":
        return "water"
    if lu == "orchard":
        return "orchard"
    if lu == "vineyard":
        return "vineyard"
    if lu == "meadow" or nat == "grassland":
        return "meadow"
    if lu == "forest" or nat == "wood":
        return "forest"
    if nat in ("scrub", "heath"):
        return "hedge"
    if lu in ("flowerbed", "plant_nursery"):
        return "flower_strip"
    if lu == "allotments" or lei == "garden":
        return "gardens"
    if lei == "park" or lu == "village_green":
        return "park"
    if lu == "grass":
        return "grass"
    if lu == "farmland":
        crop = (tags.get("crop") or "").lower()
        return "rapeseed" if "rape" in crop or "raps" in crop else "arable"
    if lu in ("residential", "commercial", "industrial", "retail"):
        return "urban"
    return None


# draw order: big, coarse areas first; finer, more specific areas on top
ORDER = ["arable", "urban", "grass", "forest", "vineyard", "meadow", "orchard", "rapeseed", "park",
         "gardens", "hedge", "flower_strip", "water"]
LINE_KINDS = {"hedge": ("hedge", 3.0), "tree_row": ("tree_row", 8.0)}   # kind, width in metres
STRICT_PROTECT = {"1", "1a", "1b", "2", "3", "4", "7"}
STRICT_TITLES = ("naturschutzgebiet", "naturdenkmal", "geschützter landschaftsbestandteil", "geschuetzter landschaftsbestandteil")


def is_wild_zone(tags: dict, include_landscape: bool) -> bool:
    if tags.get("leisure") == "nature_reserve":
        return True
    if tags.get("boundary") != "protected_area":
        return False
    pc = str(tags.get("protect_class", "")).lower()
    title = (tags.get("protection_title") or "").lower()
    if pc in STRICT_PROTECT or any(t in title for t in STRICT_TITLES):
        return True
    return include_landscape and (pc == "5" or "landschaftsschutz" in title)


# ------------------------------------------------------------------ geometry
def _key(p):
    return (round(p[0], 7), round(p[1], 7))


def stitch(segments: list[list[tuple]]) -> list[list[tuple]]:
    """Join way segments that share end points into closed rings."""
    segs = [list(s) for s in segments if len(s) >= 2]
    rings = []
    while segs:
        ring = segs.pop(0)
        changed = True
        while _key(ring[0]) != _key(ring[-1]) and changed:
            changed = False
            for k, s in enumerate(segs):
                if _key(s[0]) == _key(ring[-1]):
                    ring += s[1:]
                elif _key(s[-1]) == _key(ring[-1]):
                    ring += s[::-1][1:]
                elif _key(s[-1]) == _key(ring[0]):
                    ring = s[:-1] + ring
                elif _key(s[0]) == _key(ring[0]):
                    ring = s[::-1][:-1] + ring
                else:
                    continue
                segs.pop(k)
                changed = True
                break
        if _key(ring[0]) == _key(ring[-1]) and len(ring) >= 4:
            rings.append(ring)
    return rings


def _xy(p, lat0):
    return (p[1] * 111320 * math.cos(math.radians(lat0)), p[0] * 111320)


def simplify(ring, tol_m=4.0):
    """Douglas-Peucker in metres."""
    if len(ring) < 5:
        return ring
    lat0 = ring[0][0]
    pts = [_xy(p, lat0) for p in ring]

    def rdp(i, j, keep):
        (x1, y1), (x2, y2) = pts[i], pts[j]
        dx, dy = x2 - x1, y2 - y1
        n = math.hypot(dx, dy) or 1e-9
        best, bi = -1.0, -1
        for k in range(i + 1, j):
            d = abs(dy * pts[k][0] - dx * pts[k][1] + x2 * y1 - y2 * x1) / n
            if d > best:
                best, bi = d, k
        if best > tol_m:
            keep.add(bi)
            rdp(i, bi, keep)
            rdp(bi, j, keep)

    keep = {0, len(ring) - 1}
    sys.setrecursionlimit(10000)
    rdp(0, len(ring) - 1, keep)
    out = [ring[k] for k in sorted(keep)]
    return out if len(out) >= 4 else ring


def area_m2(ring):
    lat0 = ring[0][0]
    pts = [_xy(p, lat0) for p in ring]
    return abs(sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(pts, pts[1:]))) / 2


def point_in(pt, ring):
    lat, lon = pt
    inside = False
    for (y1, x1), (y2, x2) in zip(ring, ring[1:]):
        if (y1 > lat) != (y2 > lat) and lon < (x2 - x1) * (lat - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def polys_of(el) -> list[dict]:
    """Polygons (outer ring + holes) of a way or multipolygon relation."""
    if el["type"] == "way":
        g = [(p["lat"], p["lon"]) for p in el.get("geometry") or [] if p]
        if len(g) >= 4 and _key(g[0]) == _key(g[-1]):
            return [{"outer": g, "inner": []}]
        return []
    outers, inners = [], []
    for m in el.get("members", []):
        if m.get("type") != "way" or not m.get("geometry"):
            continue
        seg = [(p["lat"], p["lon"]) for p in m["geometry"] if p]
        (inners if m.get("role") == "inner" else outers).append(seg)   # missing role: outer
    polys = [{"outer": r, "inner": []} for r in stitch(outers)]
    for hole in stitch(inners):
        for poly in polys:
            if point_in(hole[0], poly["outer"]):
                poly["inner"].append(hole)
                break
    return polys


def clean(polys, min_area=150.0):
    out = []
    for p in polys:
        if area_m2(p["outer"]) < min_area:
            continue
        out.append({"outer": [[round(a, 6), round(b, 6)] for a, b in simplify(p["outer"])],
                    "inner": [[[round(a, 6), round(b, 6)] for a, b in simplify(h)] for h in p["inner"] if area_m2(h) >= min_area]})
    return out


# ------------------------------------------------------------------ main
def convert(data: dict, include_landscape=False) -> dict:
    land, lines, zones, places = [], [], [], []
    counts: dict[str, int] = {}
    for el in data.get("elements", []):
        tags = el.get("tags") or {}
        if el["type"] == "node":
            if tags.get("place") and tags.get("name"):
                places.append({"name": tags["name"], "lat": el["lat"], "lon": el["lon"], "type": tags["place"]})
            continue
        line_tag = "hedge" if tags.get("barrier") == "hedge" else "tree_row" if tags.get("natural") == "tree_row" else None
        if line_tag and el["type"] == "way":
            g = [[round(p["lat"], 6), round(p["lon"], 6)] for p in el.get("geometry") or [] if p]
            if len(g) >= 2:
                k, wdt = LINE_KINDS[line_tag]
                lines.append({"kind": k, "width_m": wdt, "coords": g})
                counts[k + " (line)"] = counts.get(k + " (line)", 0) + 1
            continue
        if is_wild_zone(tags, include_landscape):
            polys = clean(polys_of(el), min_area=50)
            if polys:
                name = tags.get("name") or tags.get("protection_title") or "Protected area"
                zones.append({"id": f"osm-{el['type'][0]}{el['id']}", "name": name, "polys": polys,
                              "note": tags.get("protection_title") or ("Nature reserve" if tags.get("leisure") == "nature_reserve" else "Protected area"),
                              "osm": f"https://www.openstreetmap.org/{el['type']}/{el['id']}"})
            if tags.get("boundary") == "protected_area" and not tags.get("landuse") and not tags.get("natural"):
                continue
        k = kind_of(tags)
        if not k:
            continue
        polys = clean(polys_of(el))
        if polys:
            land.append({"kind": k, "name": tags.get("name") or "", "polys": polys})
            counts[k] = counts.get(k, 0) + 1
    land.sort(key=lambda f: ORDER.index(f["kind"]))
    return {"source": "OpenStreetMap via Overpass API", "attribution": "© OpenStreetMap contributors (ODbL)",
            "fetched": dt.date.today().isoformat(), "bbox": list(BBOX), "include_landscape": include_landscape,
            "counts": counts, "landcover": land, "lines": lines, "wild_bee_zones": zones, "places": places}


def fetch(q: str) -> dict:
    last = None
    for url in ENDPOINTS:
        try:
            r = requests.post(url, data={"data": q}, headers=HEADERS, timeout=300)
            if r.status_code == 200:
                try:
                    data = r.json()
                except ValueError:
                    last = f"{url}: not JSON ({r.text[:120]!r})"
                else:
                    # Overpass answers 200 even when the query timed out or ran
                    # out of memory: the error is in "remark" and the data is cut short.
                    remark = str(data.get("remark") or "")
                    if "error" in remark.lower():
                        last = f"{url}: {remark[:200]}"
                    else:
                        return data
            else:
                last = f"{url}: HTTP {r.status_code} {r.text[:200]}"
        except requests.RequestException as e:
            last = f"{url}: {e}"
        print(f"  {last}; trying the next server")
    raise SystemExit(f"Couldn't download from OpenStreetMap ({last}).")




def fetch_and_save(include_landscape=False, out=None, data: dict | None = None, min_areas: int = MIN_AREAS) -> dict:
    out = Path(out or OUT)
    fetched = data is None
    res = convert(fetch(query(BBOX)) if fetched else data, include_landscape)
    if fetched and len(res["landcover"]) < min_areas:
        # never replace good data with a near-empty answer
        raise SystemExit(f"OpenStreetMap returned only {len(res['landcover'])} land-cover areas for Heilbronn; "
                         "keeping the previous map data.")
    tmp = Path(str(out) + ".tmp")
    tmp.write_text(json.dumps(res, ensure_ascii=False, separators=(",", ":")))
    tmp.replace(out)
    return res
