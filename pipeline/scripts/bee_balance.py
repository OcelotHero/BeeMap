#!/usr/bin/env python3
"""Bee Balance Heilbronn: the merged site map.  One pipeline for the team's 250 m
verdict map (forage within 1.5 km and 300 m, 🟢 / 🟡 / 🔴) plus our layers
(senseBox environment, street heat from the summer walks, the camera hive).

Steps
  1. Land use from OpenFreeMap vector tiles (OpenMapTiles schema, zoom 14),
     the same OpenStreetMap data the Overpass API serves but always reachable.
     Every polygon gets a bee-food weight (FORAGE_WEIGHT) and is rasterised on
     a 25 m grid; where polygons overlap, the higher weight wins.
  2. Mean weight within 1.5 km (honeybee range) and 300 m (wild bees) around
     the centre of every 250 m square, via a disk convolution.
  3. Squares at least 40 % inside the Heilbronn boundary (Nominatim) are kept.
  4. Verdict from food within 1.5 km: green "hive-ready" / yellow "plant first" /
     red "no extra hives".  The plan sets the two thresholds so the city splits
     into roughly a quarter, a half and a quarter (28 % / 20 % on the team's
     raster).  Our raster has a different level, so by default the thresholds
     are the 75th and 25th percentile of the city's squares (THRESHOLDS = None);
     set THRESHOLDS = (0.28, 0.20) to use the fixed values.
  5. Extras per square: environment score interpolated from senseBox stations
     (site_map.py) and street heat from the summer walks (heat_walks.py).
  6. Schools (POI layer) and neighbourhoods (place layer) get their square's
     numbers, for the "which school can host a hive" and "plant here first" lists.

Outputs (./output)
  bee_balance_grid.csv / .geojson   one row per 250 m square
  bee_balance_schools.csv           schools with verdict, best first
  bee_balance_neighbourhoods.csv    neighbourhoods by food within 300 m, poorest first
  bee_balance_summary.json          shares, hive square, counts (for the app)
  bee_balance_map.png               static map
"""
import gzip
import json
import math
import sys
from pathlib import Path

import matplotlib
import numpy as np
import pandas as pd
import requests
from PIL import Image, ImageDraw
from scipy.signal import fftconvolve

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import DATA, HIVE_LAT, HIVE_LON, OUTPUT, haversine_km, load_sensebox  # noqa: E402
from site_map import environment_score, environment_weights, idw, station_environment  # noqa: E402

Z = 14                         # tile zoom; one tile ≈ 1.6 km at 49° N
PX_PER_TILE = 64               # 4096 tile units / 64 px = 25 m pixels
CELL_PX = 10                   # 250 m squares
R_HONEY_M, R_WILD_M = 1500, 300
MARGIN_KM = 1.6                # tiles beyond the city so edge squares see their surroundings
INSIDE_SHARE = 0.40            # square must be this much inside the boundary
THRESHOLDS = None              # (green, yellow) fixed values, or None = city quartiles
UA = {"User-Agent": "bee-commute-tracker/0.1 (hackathon)"}
TILEJSON = "https://tiles.openfreemap.org/planet"

# Bee-food weight per OpenMapTiles (layer, class, subclass); first match wins.
FORAGE_WEIGHT = [
    ("landcover", None, "meadow", 1.0), ("landcover", None, "orchard", 1.0), ("landcover", None, "heath", 1.0),
    ("landcover", None, "flowerbed", 1.0),
    ("landcover", None, "allotments", 0.8), ("landcover", None, "wet_meadow", 0.8),
    ("landcover", None, "garden", 0.7), ("landcover", None, "scrub", 0.7),
    ("landcover", None, "grassland", 0.6), ("park", "nature_reserve", None, 0.6),
    ("landcover", "wood", None, 0.5), ("landcover", None, "park", 0.5), ("park", "park", None, 0.5),
    ("landuse", "cemetery", None, 0.5), ("landcover", None, "plant_nursery", 0.5),
    ("landcover", None, "village_green", 0.4), ("landcover", "wetland", None, 0.4),
    ("landcover", None, "grass", 0.2),
    ("landuse", "residential", None, 0.15), ("landuse", "suburb", None, 0.15), ("landuse", "neighbourhood", None, 0.15),
    ("landcover", "farmland", None, 0.1), ("landcover", None, "vineyard", 0.1),
]
CATEGORY = {"green": "Hive-ready", "yellow": "Plant first", "red": "No extra hives"}
ADVICE = {
    "green": "Enough food for a honeybee colony. Keep the hive shaded and check wild bee food before adding more.",
    "yellow": "A hive would struggle. Plant flower strips, fruit trees or meadows first; a wild bee garden works right away.",
    "red": "Too little food. More honeybees would compete with wild bees. Plant native flowers and add wild bee nesting spots instead.",
}


# --------------------------------------------------------------------------- #
# Web-Mercator helpers
# --------------------------------------------------------------------------- #
def lonlat_to_tilef(lon, lat, z=Z):
    n = 2 ** z
    x = (lon + 180) / 360 * n
    y = (1 - math.log(math.tan(math.radians(lat)) + 1 / math.cos(math.radians(lat))) / math.pi) / 2 * n
    return x, y


def tilef_to_lonlat(x, y, z=Z):
    n = 2 ** z
    lon = x / n * 360 - 180
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    return lon, lat


def weight_of(layer, props):
    cls, sub = props.get("class"), props.get("subclass")
    for lay, c, s, w in FORAGE_WEIGHT:
        if lay == layer and (c is None or c == cls) and (s is None or s == sub):
            return w
    return 0.0


# --------------------------------------------------------------------------- #
# 1. tiles -> 25 m raster of bee-food weight
# --------------------------------------------------------------------------- #
def fetch_tile(tmpl, x, y):
    cache = DATA / "tiles" / f"z{Z}" / f"{x}_{y}.pbf"
    if not cache.exists():
        cache.parent.mkdir(parents=True, exist_ok=True)
        r = requests.get(tmpl.format(z=Z, x=x, y=y), headers=UA, timeout=60)
        r.raise_for_status()
        cache.write_bytes(r.content)
    data = cache.read_bytes()
    try:
        return gzip.decompress(data)
    except gzip.BadGzipFile:
        return data


def decode(data):
    import mapbox_vector_tile
    try:
        return mapbox_vector_tile.decode(data, default_options={"y_coord_down": True})
    except TypeError:                     # older library API
        return mapbox_vector_tile.decode(data, y_coord_down=True)


def polygons_of(geom):
    if geom["type"] == "Polygon":
        return [geom["coordinates"]]
    if geom["type"] == "MultiPolygon":
        return geom["coordinates"]
    return []


def rasterise_tile(tile, meta):
    """64x64 weight image for one tile (max over overlapping polygons) and named features."""
    img = np.zeros((PX_PER_TILE, PX_PER_TILE), dtype=np.float32)
    pois, places = [], []
    for layer in ("landcover", "landuse", "park"):
        if layer not in tile:
            continue
        extent = tile[layer].get("extent", 4096)
        scale = PX_PER_TILE / extent
        feats = sorted(tile[layer]["features"], key=lambda f: weight_of(layer, f["properties"]))
        for f in feats:
            w = weight_of(layer, f["properties"])
            if w == 0:
                continue
            for poly in polygons_of(f["geometry"]):
                mask = Image.new("L", (PX_PER_TILE, PX_PER_TILE), 0)
                d = ImageDraw.Draw(mask)
                outer = [(x * scale, y * scale) for x, y in poly[0]]
                if len(outer) >= 3:
                    d.polygon(outer, fill=255)
                for hole in poly[1:]:
                    pts = [(x * scale, y * scale) for x, y in hole]
                    if len(pts) >= 3:
                        d.polygon(pts, fill=0)
                m = np.asarray(mask) > 0
                img[m] = np.maximum(img[m], w)
    tx, ty, extent = meta
    for layer, keep in (("poi", {"school", "college", "university"}), ("place", {"suburb", "neighbourhood", "quarter"})):
        if layer not in tile:
            continue
        ext = tile[layer].get("extent", 4096)
        for f in tile[layer]["features"]:
            p = f["properties"]
            if p.get("class") not in keep or not p.get("name") or f["geometry"]["type"] != "Point":
                continue
            if layer == "poi" and p.get("subclass") not in ("school", "college", "university"):
                continue
            x, y = f["geometry"]["coordinates"]
            lon, lat = tilef_to_lonlat(tx + x / ext, ty + y / ext)
            (pois if layer == "poi" else places).append({"name": p["name"], "kind": p["class"], "lat": lat, "lon": lon})
    return img, pois, places


def build_raster(boundary):
    lons = [p[0] for p in boundary]
    lats = [p[1] for p in boundary]
    dlat = MARGIN_KM / 111
    dlon = MARGIN_KM / (111 * math.cos(math.radians(HIVE_LAT)))
    x0, y0 = lonlat_to_tilef(min(lons) - dlon, max(lats) + dlat)
    x1, y1 = lonlat_to_tilef(max(lons) + dlon, min(lats) - dlat)
    tx0, tx1, ty0, ty1 = int(x0), int(x1), int(y0), int(y1)
    tmpl = requests.get(TILEJSON, headers=UA, timeout=60).json()["tiles"][0]
    nx, ny = tx1 - tx0 + 1, ty1 - ty0 + 1
    raster = np.zeros((ny * PX_PER_TILE, nx * PX_PER_TILE), dtype=np.float32)
    pois, places = [], []
    print(f"tiles: {nx} x {ny} = {nx * ny} at zoom {Z}")
    for j, ty in enumerate(range(ty0, ty1 + 1)):
        for i, tx in enumerate(range(tx0, tx1 + 1)):
            tile = decode(fetch_tile(tmpl, tx, ty))
            img, po, pl = rasterise_tile(tile, (tx, ty, 4096))
            raster[j * PX_PER_TILE:(j + 1) * PX_PER_TILE, i * PX_PER_TILE:(i + 1) * PX_PER_TILE] = img
            pois += po
            places += pl
        print(f"  row {j + 1}/{ny}", end="\r")
    print()
    return raster, (tx0, ty0), pois, places


def px_to_lonlat(col, row, origin):
    tx0, ty0 = origin
    return tilef_to_lonlat(tx0 + col / PX_PER_TILE, ty0 + row / PX_PER_TILE)


def lonlat_to_px(lon, lat, origin):
    x, y = lonlat_to_tilef(lon, lat)
    return (x - origin[0]) * PX_PER_TILE, (y - origin[1]) * PX_PER_TILE


# --------------------------------------------------------------------------- #
# 2-4. disk means, boundary, verdict
# --------------------------------------------------------------------------- #
def disk_mean(raster, radius_px):
    r = int(round(radius_px))
    yy, xx = np.mgrid[-r:r + 1, -r:r + 1]
    k = (xx ** 2 + yy ** 2 <= r * r).astype(np.float32)
    k /= k.sum()
    return fftconvolve(raster, k, mode="same")


def boundary_mask(boundary, raster_shape, origin):
    mask = Image.new("L", (raster_shape[1], raster_shape[0]), 0)
    pts = [lonlat_to_px(lon, lat, origin) for lon, lat in boundary]
    ImageDraw.Draw(mask).polygon(pts, fill=255)
    return np.asarray(mask) > 0


def load_boundary():
    raw = json.loads((DATA / "heilbronn_boundary_raw.json").read_text())
    rel = next(r for r in raw if r.get("osm_type") == "relation" and r.get("class") == "boundary")
    g = rel["geojson"]
    rings = [g["coordinates"][0]] if g["type"] == "Polygon" else [p[0] for p in g["coordinates"]]
    return max(rings, key=len)             # main ring (exclaves ignored)


def verdict(forage_1500, green_t, yellow_t):
    return "green" if forage_1500 >= green_t else ("yellow" if forage_1500 >= yellow_t else "red")


# --------------------------------------------------------------------------- #
def main():
    OUTPUT.mkdir(exist_ok=True)
    boundary = load_boundary()
    raster, origin, pois, places = build_raster(boundary)
    m_per_px = 2 * math.pi * 6378137 * math.cos(math.radians(HIVE_LAT)) / (2 ** Z * PX_PER_TILE)
    print(f"raster {raster.shape[1]} x {raster.shape[0]} px, {m_per_px:.1f} m per pixel; "
          f"mean weight {raster.mean():.3f}, non-zero {np.mean(raster > 0):.0%}")
    honey = disk_mean(raster, R_HONEY_M / m_per_px)
    wild = disk_mean(raster, R_WILD_M / m_per_px)
    inside = boundary_mask(boundary, raster.shape, origin).astype(np.float32)

    rows = []
    for r0 in range(0, raster.shape[0] - CELL_PX + 1, CELL_PX):
        for c0 in range(0, raster.shape[1] - CELL_PX + 1, CELL_PX):
            share = inside[r0:r0 + CELL_PX, c0:c0 + CELL_PX].mean()
            if share < INSIDE_SHARE:
                continue
            rc, cc = r0 + CELL_PX / 2, c0 + CELL_PX / 2
            lon, lat = px_to_lonlat(cc, rc, origin)
            f15, f3 = float(honey[int(rc), int(cc)]), float(wild[int(rc), int(cc)])
            rows.append({"cell_id": f"{r0 // CELL_PX}_{c0 // CELL_PX}", "lat": lat, "lon": lon, "row": r0 // CELL_PX, "col": c0 // CELL_PX,
                         "forage_1500": f15, "forage_300": f3})
    cells = pd.DataFrame(rows)
    green_t, yellow_t = THRESHOLDS or (float(cells.forage_1500.quantile(0.75)), float(cells.forage_1500.quantile(0.25)))
    cells["category"] = [verdict(f, green_t, yellow_t) for f in cells.forage_1500]
    print(f"thresholds: green >= {green_t:.0%}, yellow >= {yellow_t:.0%} "
          f"({'fixed' if THRESHOLDS else 'city quartiles of food within 1.5 km'})")
    cells["dist_to_hive_km"] = [haversine_km(a, b, HIVE_LAT, HIVE_LON) for a, b in zip(cells.lat, cells.lon)]

    # environment (senseBox) and street heat (summer walks), reusing the other scripts
    stations = station_environment(load_sensebox())
    weights, source = environment_weights()
    stations["environment"] = environment_score(stations, weights)
    cells["environment"] = idw(cells, stations, stations.environment.values)
    hw = OUTPUT / "heat_walks_grid.csv"
    cells["street_heat_c"] = np.nan
    if hw.exists():
        h = pd.read_csv(hw)
        for i, c in cells.iterrows():
            d = np.array([haversine_km(c.lat, c.lon, a, b) for a, b in zip(h.lat, h.lon)])
            if (d <= 0.25).any():
                cells.at[i, "street_heat_c"] = float(h.delta_c[d <= 0.25].mean())
    cells.to_csv(OUTPUT / "bee_balance_grid.csv", index=False)

    def cell_of(lat, lon):
        d = (cells.lat - lat) ** 2 + ((cells.lon - lon) * math.cos(math.radians(lat))) ** 2
        return cells.iloc[int(d.idxmin())] if d.min() < (0.003) ** 2 else None

    # schools and neighbourhoods
    def table(items):
        out = []
        for it in items:
            c = cell_of(it["lat"], it["lon"])
            if c is None:
                continue
            out.append({**it, "forage_1500": c.forage_1500, "forage_300": c.forage_300, "category": c.category,
                        "environment": c.environment, "street_heat_c": c.street_heat_c, "cell_id": c.cell_id})
        return pd.DataFrame(out).drop_duplicates("name")
    schools = table([p for p in pois if p["kind"] == "school"]).sort_values("forage_1500", ascending=False)
    hoods = table(places).sort_values("forage_300")
    schools.to_csv(OUTPUT / "bee_balance_schools.csv", index=False)
    hoods.to_csv(OUTPUT / "bee_balance_neighbourhoods.csv", index=False)

    hive = cell_of(HIVE_LAT, HIVE_LON)
    shares = cells.category.value_counts(normalize=True).round(3).to_dict()
    summary = {
        "n_cells": int(len(cells)), "area_km2": round(len(cells) * (CELL_PX * m_per_px / 1000) ** 2, 1), "shares": shares,
        "thresholds": {"green": round(green_t, 3), "yellow": round(yellow_t, 3), "mode": "fixed" if THRESHOLDS else "city quartiles"},
        "hive": {"cell_id": hive.cell_id, "forage_1500": round(float(hive.forage_1500), 3), "forage_300": round(float(hive.forage_300), 3),
                 "category": hive.category, "pct_cells_with_more_food": round(float((cells.forage_1500 > hive.forage_1500).mean()) * 100, 1),
                 "environment": round(float(hive.environment), 3)},
        "schools": schools.category.value_counts().to_dict(), "n_schools": int(len(schools)), "n_neighbourhoods": int(len(hoods)),
        "environment_weights": source,
    }
    (OUTPUT / "bee_balance_summary.json").write_text(json.dumps(summary, indent=2))
    feats = [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(r.lon, 6), round(r.lat, 6)]},
              "properties": {"cell_id": r.cell_id, "forage_1500": round(r.forage_1500, 3), "forage_300": round(r.forage_300, 3),
                             "category": r.category, "environment": round(r.environment, 3),
                             "street_heat_c": None if np.isnan(r.street_heat_c) else round(r.street_heat_c, 2)}} for r in cells.itertuples()]
    (OUTPUT / "bee_balance_grid.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": feats}))

    # static map
    fig, ax = plt.subplots(figsize=(7.5, 7))
    col = {"green": "#2e8b57", "yellow": "#e6b422", "red": "#c0392b"}
    ax.scatter(cells.lon, cells.lat, c=cells.category.map(col), s=14, marker="s", linewidths=0)
    ax.plot([p[0] for p in boundary], [p[1] for p in boundary], color="#444", lw=0.8)
    ax.scatter([HIVE_LON], [HIVE_LAT], marker="*", s=220, c="#e8912d", edgecolor="black", zorder=5)
    ax.set_aspect(1 / math.cos(math.radians(HIVE_LAT)))
    ax.set_title("Bee Balance Heilbronn: hive-ready (green), plant first (yellow), no extra hives (red)", fontsize=10)
    fig.tight_layout()
    fig.savefig(OUTPUT / "bee_balance_map.png", dpi=120)

    print(f"{len(cells)} squares of 250 m, {summary['area_km2']} km²; shares {shares}")
    print(f"camera hive: food within 1.5 km {hive.forage_1500:.0%}, within 300 m {hive.forage_300:.0%}, "
          f"category {hive.category}; {summary['hive']['pct_cells_with_more_food']:.0f} % of squares have more food")
    print(f"schools: {len(schools)} -> {summary['schools']}")
    print(schools.head(5)[["name", "forage_1500", "category"]].round(2).to_string(index=False))
    print(f"neighbourhoods: {len(hoods)}; least food within 300 m:")
    print(hoods.head(6)[["name", "kind", "forage_300", "category"]].round(2).to_string(index=False))
    print("wrote output/bee_balance_grid.csv/.geojson, bee_balance_schools.csv, bee_balance_neighbourhoods.csv, bee_balance_summary.json, bee_balance_map.png")


if __name__ == "__main__":
    main()
