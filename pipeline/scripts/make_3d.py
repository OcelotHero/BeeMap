#!/usr/bin/env python3
"""Data for the three "bee lights" views in the app.

  commute_trips.json     an animated commute on the city map.  For every hour the
                         camera measured, glowing trips leave the hive into its
                         1.5 km foraging circle and return.  The NUMBER of trips
                         follows the measured bees per minute; the TARGETS are
                         sampled from the Bee Balance squares in range, weighted
                         by food within 300 m.  The paths themselves are an
                         illustration: the camera cannot see where a bee flies.
  detections_cloud.json  every bee the detector found in the busiest session,
                         as a point (x, y in the frame, time as height).  Real data.
  city3d.csv             the 250 m squares with a column height for the 3D map.
"""
import json
import math
import random
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import DATA, HIVE_LAT, HIVE_LON, OUTPUT, TZ, haversine_km  # noqa: E402

RANGE_KM = 1.5
LOOP_SECONDS = 60            # one animation loop per recorded hour
MAX_TRIPS, MIN_TRIPS = 90, 3
random.seed(7)


def curved_path(a, b, bulge, n=8):
    """Points from a to b and back with a sideways bulge, so trips don't overlap."""
    (x0, y0), (x1, y1) = a, b
    dx, dy = x1 - x0, y1 - y0
    px, py = -dy, dx                       # perpendicular
    out = []
    for i in range(n + 1):
        t = i / n
        s = math.sin(math.pi * t) * bulge
        out.append([x0 + dx * t + px * s, y0 + dy * t + py * s])
    back = [[x0 + dx * t - px * math.sin(math.pi * t) * bulge, y0 + dy * t - py * math.sin(math.pi * t) * bulge]
            for t in [1 - i / n for i in range(1, n + 1)]]
    return out + back


def commute_trips():
    hourly = pd.read_csv(OUTPUT / "hourly.csv", index_col=0)
    real = hourly.dropna(subset=["activity"])
    real.index = pd.to_datetime(real.index, utc=True).tz_convert(TZ)
    cells = pd.read_csv(OUTPUT / "bee_balance_grid.csv")
    cells = cells[[haversine_km(a, b, HIVE_LAT, HIVE_LON) <= RANGE_KM for a, b in zip(cells.lat, cells.lon)]]
    weights = (cells.forage_300.values + 0.02)
    weights = weights / weights.sum()
    hours = []
    for ts, r in real.sort_index().iterrows():
        n = int(np.clip(round(r.activity / 3.5), MIN_TRIPS, MAX_TRIPS))
        trips = []
        for _ in range(n):
            c = cells.iloc[np.random.choice(len(cells), p=weights)]
            target = (c.lon + random.uniform(-0.0012, 0.0012), c.lat + random.uniform(-0.0008, 0.0008))
            path = curved_path((HIVE_LON, HIVE_LAT), target, random.uniform(-0.35, 0.35))
            dur = random.uniform(14, 30)
            start = random.uniform(0, LOOP_SECONDS)
            stamps = [round((start + dur * i / (len(path) - 1)) % LOOP_SECONDS, 2) for i in range(len(path))]
            # keep timestamps monotonic within the loop by splitting at the wrap
            trips.append({"path": [[round(x, 6), round(y, 6)] for x, y in path], "t0": round(start, 2), "dur": round(dur, 2)})
        hours.append({"label": f"{ts:%d %b %H:%M}", "bees_per_min": round(float(r.activity)), "temp": round(float(r.out_temp), 1),
                      "rh": round(float(r.out_humidity)), "trips": trips})
    out = {"hive": [HIVE_LON, HIVE_LAT], "range_km": RANGE_KM, "loop": LOOP_SECONDS, "hours": hours}
    (OUTPUT / "commute_trips.json").write_text(json.dumps(out))
    print(f"commute: {len(hours)} hours, {sum(len(h['trips']) for h in hours)} trips, targets from {len(cells)} squares in range")


def detections_cloud():
    det = pd.read_csv(DATA / "detections.csv")
    act = pd.read_csv(DATA / "activity.csv")
    ok = act[act.signal_ok & act.start_known].groupby("video").bees_per_min.sum().sort_values(ascending=False)
    session = ok.index[0]
    d = det[det.video == session].copy()
    frames = sorted(d.frame.unique())
    f0, f1 = min(frames), max(frames)
    d["t"] = (d.frame - f0) / max(f1 - f0, 1)
    pts = [[float(r.cx), float(240 - r.cy), round(float(r.t), 4), round(float(r.conf), 2)] for r in d.itertuples()]
    out = {"session": session, "n": len(pts), "frames": int(f1 - f0 + 1), "width": 320, "height": 240, "points": pts}
    (OUTPUT / "detections_cloud.json").write_text(json.dumps(out))
    print(f"cloud: session {session}, {len(pts)} detections over {f1 - f0 + 1} frames")


def city3d():
    cells = pd.read_csv(OUTPUT / "bee_balance_grid.csv")
    cells["height"] = (cells.forage_1500 * 2500).round(0)
    cells[["cell_id", "lat", "lon", "forage_1500", "forage_300", "category", "height"]].to_csv(OUTPUT / "city3d.csv", index=False)
    print(f"city3d: {len(cells)} columns")


if __name__ == "__main__":
    OUTPUT.mkdir(exist_ok=True)
    commute_trips()
    city3d()
    if (DATA / "detections.csv").exists():
        detections_cloud()
    else:
        print("no data/detections.csv yet - run count_bees.py first")
