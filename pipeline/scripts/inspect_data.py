#!/usr/bin/env python3
"""Report what is actually in ./data after fetch_data.py.

Prints: recording sessions (frames, timestamps), label format and class
counts, labels per frame; hive sensor time range, sampling interval and gaps;
closest senseBox station and its coverage.  Read-only.
"""
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (DATA, HIVE_LAT, HIVE_LON, closest_sensebox_station,  # noqa: E402
                    load_hive_sensors, load_sensebox, session_start)

CROP_RE = re.compile(r"^(?P<prefix>.*?)bee_(?P<frame>\d+)__x(?P<x>\d+)_y(?P<y>\d+)_a(?P<area>\d+)(?P<suffix>_\d{8,9}(?:_\d)?)?\.png$")


def h(title):
    print(f"\n{'=' * 70}\n{title}\n{'=' * 70}")


def inspect_recordings():
    h("1. Recording sessions (data/recordings)")
    rec = DATA / "recordings"
    if not rec.exists():
        print("  missing - run fetch_data.py")
        return
    total = 0
    print(f"  {'session':16} {'frames':>7} {'first':>9} {'last':>9}  start (from name)")
    for s in sorted(rec.iterdir()):
        frames = sorted(p for p in s.rglob("*") if p.suffix.lower() in (".jpg", ".png"))
        nums = [int(m.group(1)) for p in frames if (m := re.search(r"bee_(\d+)", p.name))]
        total += len(frames)
        print(f"  {s.name:16} {len(frames):>7} {min(nums, default=0):>9} {max(nums, default=0):>9}  {session_start(s.name)}")
    print(f"  total frames: {total}")
    print("  NOTE: frames are RGB composites of 3 grayscale captures (R,G,B = t0,t1,t2).")
    print("  NOTE: per-frame timestamps do not exist (file mtimes are 1980-01-01);")
    print("        only the session start is known from the folder name.  The capture")
    print("        interval between frames is NOT documented -> see FRAME_INTERVAL_S in common.py")
    unc = DATA / "uncropped_all" / "uncropped"
    if unc.exists():
        files = list(unc.glob("*.jpg"))
        dup = Counter(re.sub(r" \(\d+\)", "", f.name) for f in files)
        print(f"  uncropped_all: {len(files)} jpgs, {len(dup)} distinct frame numbers, "
              f"up to {max(dup.values())} sessions share a number (OS-style ' (n)' suffixes)")


def inspect_labels():
    h("2. Labels (data/traindata)")
    td = DATA / "traindata"
    if not td.exists():
        print("  missing - run fetch_data.py")
        return
    print("  format: 32x32 PNG crops in one folder per class; no bounding-box files.")
    print("  filename: [<session>]bee_<frame>__x<X>_y<Y>_a<AREA>[_<session>].png")
    per_frame = defaultdict(Counter)
    prefixes = Counter()
    unparsed = 0
    for cls_dir in sorted(td.iterdir()):
        if not cls_dir.is_dir():
            continue
        files = list(cls_dir.glob("*.png"))
        print(f"  class {cls_dir.name:12} {len(files):>6} crops")
        for f in files:
            m = CROP_RE.match(f.name)
            if not m:
                unparsed += 1
                continue
            key = (m["prefix"] or "") + (m["suffix"] or "") or "<none>"
            prefixes[key] += 1
            per_frame[(key, int(m["frame"]))][cls_dir.name] += 1
    print(f"  unparsed filenames: {unparsed}")
    print("  session hint in filename -> crops:")
    for k, v in prefixes.most_common():
        print(f"    {k:22} {v}")
    bee_frames = {k: c for k, c in per_frame.items() if c["bee_motion"] + c["bee_slow"] > 0}
    bees = [c["bee_motion"] + c["bee_slow"] for c in bee_frames.values()]
    print(f"  frames with >=1 bee crop: {len(bee_frames)}; frames with only background crops: {len(per_frame) - len(bee_frames)}")
    if bees:
        print(f"  bee crops per labelled frame: mean {sum(bees) / len(bees):.1f}, max {max(bees)}")
    print("  NOTE: '<none>' crops only carry a frame number, which up to 6 sessions share;")
    print("        make_dataset.py resolves them by pixel-matching the crop against candidates.")


def inspect_hive():
    h("3. Hive sensors (data/hive_sensors.csv)")
    if not (DATA / "hive_sensors.csv").exists():
        print("  missing - run fetch_data.py")
        return
    df = load_hive_sensors()
    print(f"  rows: {len(df)}   range: {df.ts.min()} .. {df.ts.max()}")
    print(f"  {'hive':12} {'sensor':24} {'unit':16} {'n':>6} {'median dt':>9} {'gaps>1h':>7} {'longest gap':>12}")
    for (hive, sensor, unit), g in df.groupby(["hive", "sensor_type", "unit"]):
        dt = g.ts.sort_values().diff().dropna()
        print(f"  {hive:12} {sensor.replace('LoRaWAN ', ''):24} {unit:16} {len(g):>6} "
              f"{dt.median().total_seconds() / 60:>7.1f}m {(dt > pd.Timedelta('1h')).sum():>7} {str(dt.max()):>12}")
    inside = df[(df.sensor_type == "LoRaWAN Dragino-S31-LB") & (df.unit == "temperature")]
    print("  inside temperature summary per hive (deg C):")
    print(inside.groupby("hive")["value"].describe()[["mean", "min", "50%", "max"]].round(1).to_string())


def inspect_sensebox():
    h("4. senseBox:home (data/sensebox_all.csv.gz)")
    if not (DATA / "sensebox_all.csv.gz").exists():
        print("  missing - run fetch_data.py")
        return
    df = load_sensebox()
    print(f"  rows: {len(df)}   stations: {df.boxName.nunique()}   hive position {HIVE_LAT}N {HIVE_LON}E")
    st = df.groupby("boxName").agg(rows=("ts", "size"), first=("ts", "min"), last=("ts", "max"),
                                   dist_km=("dist_km", "first"), pm25_rows=("pm25", "count")).sort_values("dist_km")
    print(st.to_string())
    rec_days = sorted({session_start(s.name).date() for s in (DATA / "recordings").iterdir() if session_start(s.name)})
    start, end = pd.Timestamp(rec_days[0], tz="Europe/Berlin"), pd.Timestamp(rec_days[-1], tz="Europe/Berlin") + pd.Timedelta("1D")
    best = closest_sensebox_station(df, start, end)
    g = df[df.boxName == best].sort_values("ts")
    dt = g.ts.diff().dropna()
    print(f"\n  recording days: {rec_days[0]} .. {rec_days[-1]}")
    print(f"  closest station with PM2.5 during the recordings: {best} ({st.loc[best, 'dist_km']:.1f} km)")
    print(f"  coverage {g.ts.min()} .. {g.ts.max()}, median interval {dt.median().total_seconds() / 60:.1f} min, "
          f"gaps > 6h: {(dt > pd.Timedelta('6h')).sum()}, longest gap {dt.max()}")
    print(f"  non-null: " + ", ".join(f"{c}={g[c].notna().sum()}" for c in ["sb_temp", "sb_humidity", "sb_pressure", "pm25", "pm10"]))


if __name__ == "__main__":
    inspect_recordings()
    inspect_labels()
    inspect_hive()
    inspect_sensebox()
