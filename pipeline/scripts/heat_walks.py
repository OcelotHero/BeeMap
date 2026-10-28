#!/usr/bin/env python3
"""Street-level heat from the Heat-Island Summer School walks (mobile datalogger, 1-3 Sep 2026).

61,546 one-second readings of temperature and humidity with GPS, from students
walking from the OpenSpace to the Wartberg, along the Neckar and into the city
centre.  Every recording starts at the OpenSpace, which gives us a common
reference point for each sensor unit.

Metric: "warmer than the rest of the walk".  The loggers start indoors and
need about 10 minutes to settle (traces fall by 5 °C in the first minutes), so
the first WARMUP_MIN minutes of every recording are dropped.  Then, for every
reading,
    delta = (T - M) - median_over_the_recording(T - M)
where M is the Open-Meteo temperature at the hive interpolated to the minute.
Subtracting the model removes the day's warming during the walk; subtracting
the recording's own median removes the unit's bias (each logger runs 2-4 °C
warm, like the senseBoxes).  What is left is how much hotter or cooler a street
is than the average of that walk.  Absolute offsets are reported separately.

Aggregation: 100 m grid cells, mean delta and point count; cells with fewer
than MIN_POINTS readings are dropped.  Readings at walking pace (< 3 m/s) only.

Outputs (./output)
  heat_walks_grid.csv      lat, lon, delta_c, n, recordings      (the map layer)
  heat_walks_points.csv    every 10th cleaned reading             (for a point layer)
  heat_walks_summary.csv   per recording: points, minutes, absolute offset vs the weather model
  heat_walks_stations.csv  walk delta within 300 m of each senseBox station vs the station's own daytime offset
  heat_walks.png           static map
"""
import sys
from pathlib import Path

import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import DATA, HIVE_LAT, HIVE_LON, OUTPUT, TZ, haversine_km, load_sensebox, open_meteo_hourly  # noqa: E402

OPENSPACE_LAT, OPENSPACE_LON = 49.1519, 9.2155     # where every walk starts (from the data itself)
WARMUP_MIN = 15              # minutes dropped at the start of every recording (sensor settling)
MIN_RECORDING_MIN = 10       # minutes a recording must still have after the warm-up cut
MAX_SPEED = 3.0              # m/s: keep walking pace, drop car/bike segments
CELL_M = 100                 # grid cell size
MIN_POINTS = 20
BBOX = (49.10, 49.20, 9.15, 9.28)   # lat min/max, lon min/max: Heilbronn city
STATION_RADIUS_KM = 0.3


def load_points():
    df = pd.read_csv(DATA / "raw" / "datalogger" / "datasets" / "alles.csv")
    df["ts"] = pd.to_datetime(df.timestamp, utc=True).dt.tz_convert(TZ)
    df = df[(df.latitude.between(*BBOX[:2])) & (df.longitude.between(*BBOX[2:]))
            & df.temperature.between(5, 45) & (df.gps_speed < MAX_SPEED)].copy()
    return df.sort_values(["recording", "ts"]).reset_index(drop=True)


def model_temperature(ts):
    """Open-Meteo temperature at the hive, linearly interpolated to each timestamp."""
    om = open_meteo_hourly(HIVE_LAT, HIVE_LON, "2026-08-31", "2026-09-04").set_index("ts")["temp"]
    t = ts.dt.tz_convert("UTC").astype("int64") / 1e9
    x = om.index.tz_convert("UTC").astype("int64") / 1e9
    return np.interp(t, x, om.values)


def reference(df):
    """Per recording after the warm-up cut: points, span, median (T - model) = the unit's absolute offset."""
    rows = []
    for rec, g in df.groupby("recording"):
        rows.append({"recording": rec, "points": len(g), "start": g.ts.iloc[0], "end": g.ts.iloc[-1],
                     "minutes": (g.ts.iloc[-1] - g.ts.iloc[0]).total_seconds() / 60,
                     "offset_vs_model_c": (g.temperature - g.model).median()})
    return pd.DataFrame(rows).set_index("recording")


def grid(df):
    lat_step = CELL_M / 111_000
    lon_step = CELL_M / (111_000 * np.cos(np.radians(HIVE_LAT)))
    df = df.assign(cell_lat=(np.floor(df.latitude / lat_step) + 0.5) * lat_step,
                   cell_lon=(np.floor(df.longitude / lon_step) + 0.5) * lon_step)
    g = df.groupby(["cell_lat", "cell_lon"]).agg(delta_c=("delta", "mean"), n=("delta", "size"),
                                                 recordings=("recording", "nunique")).reset_index()
    g = g[g.n >= MIN_POINTS].rename(columns={"cell_lat": "lat", "cell_lon": "lon"})
    return g


def station_crosscheck(df):
    """Mean walk delta near each senseBox station vs the station's own daytime offset from validate_weather."""
    off = OUTPUT / "weather_offsets.csv"
    if not off.exists():
        return pd.DataFrame()
    offsets = pd.read_csv(off)
    day = offsets[offsets.hour.between(10, 17)].groupby("station")["median"].median()
    sb = load_sensebox()
    pos = sb.groupby("boxName")[["lat", "lon"]].first()
    rows = []
    for station, o in day.items():
        if station not in pos.index:
            continue
        la, lo = pos.loc[station]
        near = df[[haversine_km(a, b, la, lo) <= STATION_RADIUS_KM for a, b in zip(df.latitude, df.longitude)]]
        if len(near) < MIN_POINTS:
            continue
        rows.append({"station": station, "walk_points": len(near), "walk_delta_vs_walk_median": near.delta.mean(),
                     "station_daytime_offset_vs_model": o})
    out = pd.DataFrame(rows)
    if len(out):
        hive = day.get("Hive weather station (SenseCAP)", np.nan)
        out["station_offset_vs_hive_station"] = out.station_daytime_offset_vs_model - hive
    return out


def plot(points, cells):
    fig, ax = plt.subplots(figsize=(7.5, 7))
    sc = ax.scatter(cells.lon, cells.lat, c=cells.delta_c, cmap="coolwarm", vmin=-3, vmax=3, s=26, marker="s", linewidths=0)
    ax.scatter([OPENSPACE_LON], [OPENSPACE_LAT], marker="*", s=200, c="#e8912d", edgecolor="black", label="OpenSpace (start of every walk)")
    ax.set_aspect(1 / np.cos(np.radians(HIVE_LAT)))
    ax.set_xlabel("lon")
    ax.set_ylabel("lat")
    ax.set_title("Street heat from the summer-school walks (2–3 Sep 2026)\n°C warmer than the rest of the same walk", fontsize=10)
    fig.colorbar(sc, ax=ax, label="Δ °C vs walk median", shrink=0.7)
    ax.legend(loc="lower left", fontsize=8)
    fig.tight_layout()
    fig.savefig(OUTPUT / "heat_walks.png", dpi=120)
    plt.close(fig)


def main():
    OUTPUT.mkdir(exist_ok=True)
    raw = load_points()
    raw["model"] = model_temperature(raw.ts)
    t0 = raw.groupby("recording").ts.transform("min")
    df = raw[raw.ts >= t0 + pd.Timedelta(minutes=WARMUP_MIN)].copy()
    ref = reference(df)
    keep = ref[ref.minutes >= MIN_RECORDING_MIN].index
    df = df[df.recording.isin(keep)].copy()
    diff = df.temperature - df.model
    df["delta"] = diff - diff.groupby(df.recording).transform("median")
    ref.to_csv(OUTPUT / "heat_walks_summary.csv")
    print(f"readings: {len(raw)} at walking pace inside the city; {len(df)} kept after dropping the first "
          f"{WARMUP_MIN} min of each recording and recordings shorter than {MIN_RECORDING_MIN} min "
          f"({len(keep)} of {ref.shape[0]} recordings kept)")
    print(f"days: {sorted(df.ts.dt.date.unique())}")
    print(f"absolute offset of the loggers vs Open-Meteo (median per recording): "
          f"median {ref.loc[keep, 'offset_vs_model_c'].median():+.1f} °C, range "
          f"{ref.loc[keep, 'offset_vs_model_c'].min():+.1f} .. {ref.loc[keep, 'offset_vs_model_c'].max():+.1f}")

    cells = grid(df)
    cells.round(4).to_csv(OUTPUT / "heat_walks_grid.csv", index=False)
    df.iloc[::10][["ts", "latitude", "longitude", "temperature", "humidity", "delta", "recording"]].round(3)\
        .to_csv(OUTPUT / "heat_walks_points.csv", index=False)
    print(f"{len(cells)} cells of {CELL_M} m with ≥ {MIN_POINTS} readings; delta range "
          f"{cells.delta_c.min():+.1f} .. {cells.delta_c.max():+.1f} °C, median {cells.delta_c.median():+.1f}")
    print("hottest cells:")
    print(cells.sort_values("delta_c", ascending=False).head(5).round(2).to_string(index=False))
    print("coolest cells:")
    print(cells.sort_values("delta_c").head(5).round(2).to_string(index=False))

    cross = station_crosscheck(df)
    cross.round(2).to_csv(OUTPUT / "heat_walks_stations.csv", index=False)
    if len(cross):
        print("\nwalk delta near senseBox stations vs the station's own offset:")
        print(cross.round(2).to_string(index=False))
    plot(df, cells)
    print("wrote output/heat_walks_grid.csv, heat_walks_points.csv, heat_walks_summary.csv, heat_walks_stations.csv, heat_walks.png")


if __name__ == "__main__":
    main()
