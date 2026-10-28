#!/usr/bin/env python3
"""Step 5: where in Heilbronn would a beehive do well?  A site-suitability heatmap.

Score = FORAGE_WEIGHT * forage + (1 - FORAGE_WEIGHT) * environment, on a 1 km grid.

  forage       OpenStreetMap green areas (orchards, allotments, meadows, parks, ...)
               within FORAGE_RADIUS_KM of the cell, weighted by how much a bee can
               collect there (see FORAGE_VALUE), area from the feature bounding box.
  environment  per senseBox station: share of daytime hours in bee-flight
               temperature, share of very humid hours, mean PM2.5; combined with
               weights taken from the activity correlations of step 3 when they
               exist (output/correlations.csv), else equal weights.  Interpolated
               to the grid by inverse distance from the stations.

Outputs (./output)
  site_scores.geojson   grid cells with forage, environment and total score
  site_stations.csv     environment metrics per station
  site_map.png          static heatmap with stations and the hive
  site_top.csv          best 10 cells
Needs: data/sensebox_all.csv.gz (fetch_data.py).  Downloads OSM data once into
data/osm_green.json.
"""
import json
import sys
from pathlib import Path

import matplotlib
import numpy as np
import pandas as pd
import requests

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import DATA, HIVE_LAT, HIVE_LON, OUTPUT, haversine_km, load_sensebox  # noqa: E402

# Grid over Heilbronn city (roughly 11 x 12 km), ~1 km cells.
LAT_MIN, LAT_MAX, LON_MIN, LON_MAX, STEP = 49.09, 49.21, 9.13, 9.31, 0.009
FORAGE_RADIUS_KM = 2.0        # typical honeybee foraging radius for most trips
FORAGE_WEIGHT = 0.5
STATION_MIN_ROWS = 1000       # ignore stations that only reported for an afternoon
STATION_MAX_KM = 20
FLIGHT_T_LOW, FLIGHT_T_HIGH, WET_RH = 12.0, 35.0, 85.0

# Relative value of one km² of each land cover to a bee (0..1); judgement, documented.
FORAGE_VALUE = {
    ("landuse", "orchard"): 1.0, ("landuse", "allotments"): 1.0, ("leisure", "garden"): 1.0,
    ("landuse", "flowerbed"): 1.0, ("landuse", "meadow"): 0.8, ("natural", "grassland"): 0.8,
    ("leisure", "park"): 0.7, ("natural", "scrub"): 0.6, ("natural", "wetland"): 0.5,
    ("natural", "heath"): 0.5, ("landuse", "forest"): 0.4, ("natural", "wood"): 0.4,
    ("leisure", "nature_reserve"): 0.6, ("landuse", "vineyard"): 0.3, ("landuse", "farmland"): 0.3,
    ("landuse", "grass"): 0.3, ("landuse", "greenfield"): 0.3, ("natural", "water"): 0.2,
}
OVERPASS = "https://overpass-api.de/api/interpreter"
OVERPASS_QUERY = """
[out:json][timeout:180];
(
  way["leisure"~"^(park|garden|allotments|nature_reserve)$"]({bbox});
  way["landuse"~"^(orchard|meadow|vineyard|grass|forest|farmland|allotments|flowerbed|greenfield)$"]({bbox});
  way["natural"~"^(wood|grassland|scrub|heath|water|wetland)$"]({bbox});
  relation["leisure"~"^(park|garden|allotments|nature_reserve)$"]({bbox});
  relation["landuse"~"^(orchard|meadow|forest|farmland)$"]({bbox});
);
out tags center bb;
"""


# --------------------------------------------------------------------------- #
def load_osm_green():
    """Green features as DataFrame(lat, lon, area_km2, value).  Cached in data/osm_green.json."""
    cache = DATA / "osm_green.json"
    if not cache.exists():
        pad = FORAGE_RADIUS_KM / 100       # ~0.02 deg so cells at the edge see their surroundings
        bbox = f"{LAT_MIN - pad},{LON_MIN - pad * 1.5},{LAT_MAX + pad},{LON_MAX + pad * 1.5}"
        print("downloading OSM green areas ...")
        r = requests.post(OVERPASS, data={"data": OVERPASS_QUERY.format(bbox=bbox)},
                          headers={"User-Agent": "bee-commute-tracker/0.1 (hackathon)"}, timeout=300)
        r.raise_for_status()
        cache.write_bytes(r.content)
    rows = []
    for e in json.loads(cache.read_text())["elements"]:
        tags, b = e.get("tags", {}), e.get("bounds")
        if not b:
            continue
        value = max((FORAGE_VALUE.get((k, tags.get(k)), 0.0) for k in ("leisure", "landuse", "natural")), default=0.0)
        if value == 0:
            continue
        area = haversine_km(b["minlat"], b["minlon"], b["maxlat"], b["minlon"]) * \
            haversine_km(b["minlat"], b["minlon"], b["minlat"], b["maxlon"])
        rows.append({"lat": (b["minlat"] + b["maxlat"]) / 2, "lon": (b["minlon"] + b["maxlon"]) / 2, "area_km2": area,
                     "value": value, "name": tags.get("name", "")})
    return pd.DataFrame(rows)


def forage_score(grid, green):
    """Value-weighted green area within FORAGE_RADIUS_KM of each grid cell."""
    glat, glon = np.radians(green.lat.values), np.radians(green.lon.values)
    weight = (green.area_km2 * green.value).values
    out = []
    for lat, lon in zip(grid.lat, grid.lon):
        a = np.sin((glat - np.radians(lat)) / 2) ** 2 + np.cos(np.radians(lat)) * np.cos(glat) * np.sin((glon - np.radians(lon)) / 2) ** 2
        d = 2 * 6371 * np.arcsin(np.sqrt(a))
        out.append(weight[d <= FORAGE_RADIUS_KM].sum())
    return np.array(out)


# --------------------------------------------------------------------------- #
def station_environment(sb):
    """Per-station metrics from daytime hours (8-18 local)."""
    s = sb[(sb.ts.dt.hour >= 8) & (sb.ts.dt.hour < 18)].dropna(subset=["sb_temp"])
    rows = []
    for name, g in s.groupby("boxName"):
        if len(g) < STATION_MIN_ROWS or g.dist_km.iloc[0] > STATION_MAX_KM:
            continue
        rows.append({
            "station": name, "lat": g.lat.iloc[0], "lon": g.lon.iloc[0], "dist_km": g.dist_km.iloc[0],
            "rows": len(g), "first": g.ts.min().date(), "last": g.ts.max().date(),
            "flight_share": ((g.sb_temp >= FLIGHT_T_LOW) & (g.sb_temp <= FLIGHT_T_HIGH)).mean(),
            "wet_share": (g.sb_humidity > WET_RH).mean() if g.sb_humidity.notna().any() else np.nan,
            "pm25_mean": g.pm25.mean() if g.pm25.notna().any() else np.nan,
        })
    return pd.DataFrame(rows)


def environment_weights():
    """Weights for (temperature, humidity, PM2.5) from step 3 correlations, else equal."""
    path = OUTPUT / "correlations.csv"
    if path.exists():
        c = pd.read_csv(path).set_index("variable")["spearman_r"].abs()
        w = np.array([c.get("out_temp", np.nan), c.get("out_humidity", np.nan), c.get("pm25", np.nan)])
        if np.isfinite(w).all() and w.sum() > 0:
            w = np.maximum(w, 0.1)          # floor so no factor vanishes on a tiny sample
            return w / w.sum(), "from output/correlations.csv (|Spearman r|, floored at 0.1)"
    return np.array([1 / 3] * 3), "equal (no usable correlations yet)"


def norm(x):
    x = np.asarray(x, dtype=float)
    lo, hi = np.nanmin(x), np.nanmax(x)
    return (x - lo) / (hi - lo) if hi > lo else np.full_like(x, 0.5)


def environment_score(stations, weights):
    """0..1 per station: higher = better for bees."""
    temp = norm(stations.flight_share)
    dry = 1 - norm(stations.wet_share.fillna(stations.wet_share.median()))
    clean = 1 - norm(stations.pm25_mean.fillna(stations.pm25_mean.median()))
    return weights[0] * temp + weights[1] * dry + weights[2] * clean


def idw(grid, stations, values, power=2):
    """Inverse-distance interpolation of station values to the grid."""
    out = []
    for lat, lon in zip(grid.lat, grid.lon):
        d = np.array([haversine_km(lat, lon, a, b) for a, b in zip(stations.lat, stations.lon)])
        w = 1 / np.maximum(d, 0.3) ** power
        out.append((w * values).sum() / w.sum())
    return np.array(out)


# --------------------------------------------------------------------------- #
def plot(grid, stations):
    fig, ax = plt.subplots(figsize=(7.5, 6.5))
    sc = ax.scatter(grid.lon, grid.lat, c=grid.score, cmap="YlGn", s=120, marker="s", vmin=0, vmax=1)
    ax.scatter(stations.lon, stations.lat, marker="^", s=70, c="#1f6f8b", edgecolor="white", label="senseBox station")
    ax.scatter([HIVE_LON], [HIVE_LAT], marker="*", s=220, c="#e8912d", edgecolor="black", label="OpenSpace hives")
    for _, s in stations.iterrows():
        ax.annotate(s.station[:22], (s.lon, s.lat), fontsize=6.5, xytext=(4, 4), textcoords="offset points")
    ax.set_xlim(LON_MIN - 0.005, LON_MAX + 0.005)
    ax.set_ylim(LAT_MIN - 0.005, LAT_MAX + 0.005)
    ax.set_aspect(1 / np.cos(np.radians(HIVE_LAT)))
    ax.set_xlabel("lon")
    ax.set_ylabel("lat")
    ax.set_title(f"Hive site suitability (forage {FORAGE_WEIGHT:.0%} + environment {1 - FORAGE_WEIGHT:.0%})")
    fig.colorbar(sc, ax=ax, label="score (0–1)")
    ax.legend(loc="lower left", fontsize=8)
    fig.tight_layout()
    fig.savefig(OUTPUT / "site_map.png", dpi=120)
    plt.close(fig)


def main():
    OUTPUT.mkdir(exist_ok=True)
    lats = np.arange(LAT_MIN, LAT_MAX + 1e-9, STEP)
    lons = np.arange(LON_MIN, LON_MAX + 1e-9, STEP * 1.5)     # ~1 km in both directions at 49°N
    grid = pd.DataFrame([(a, b) for a in lats for b in lons], columns=["lat", "lon"])

    green = load_osm_green()
    print(f"OSM: {len(green)} green features, {green.area_km2.sum():.0f} km² (bbox area)")
    grid["forage_raw"] = forage_score(grid, green)
    grid["forage"] = norm(grid.forage_raw)

    stations = station_environment(load_sensebox())
    weights, source = environment_weights()
    stations["environment"] = environment_score(stations, weights)
    print(f"environment weights temp/humidity/pm25 = {np.round(weights, 2)} {source}")
    print(stations[["station", "dist_km", "rows", "flight_share", "wet_share", "pm25_mean", "environment"]].round(2).to_string(index=False))
    grid["environment"] = idw(grid, stations, stations.environment.values)
    grid["score"] = FORAGE_WEIGHT * grid.forage + (1 - FORAGE_WEIGHT) * grid.environment

    # nearest named green feature, so the top cells have a human-readable label
    named = green[green.name.str.contains(r"[A-Za-zÄÖÜäöüß]{4,}", regex=True) & (green.value >= 0.6)]
    grid["near"] = [named.iloc[np.argmin([haversine_km(a, b, x, y) for x, y in zip(named.lat, named.lon)])]["name"]
                    for a, b in zip(grid.lat, grid.lon)]
    grid["dist_to_hive_km"] = [haversine_km(a, b, HIVE_LAT, HIVE_LON) for a, b in zip(grid.lat, grid.lon)]

    stations.to_csv(OUTPUT / "site_stations.csv", index=False)
    top = grid.sort_values("score", ascending=False).head(10)
    top.round(3).to_csv(OUTPUT / "site_top.csv", index=False)
    features = [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [r.lon, r.lat]},
                 "properties": {"forage": round(r.forage, 3), "environment": round(r.environment, 3),
                                "score": round(r.score, 3), "near": r.near}} for r in grid.itertuples()]
    (OUTPUT / "site_scores.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": features}))
    plot(grid, stations)
    hive_cell = grid.iloc[grid.dist_to_hive_km.idxmin()]
    print(f"\nhive cell score {hive_cell.score:.2f} (forage {hive_cell.forage:.2f}, environment {hive_cell.environment:.2f}); "
          f"rank {int((grid.score > hive_cell.score).sum()) + 1} of {len(grid)} cells")
    print("top cells:")
    print(top[["lat", "lon", "forage", "environment", "score", "near"]].round(2).to_string(index=False))
    print(f"wrote output/site_scores.geojson, site_map.png, site_top.csv, site_stations.csv")


if __name__ == "__main__":
    main()
