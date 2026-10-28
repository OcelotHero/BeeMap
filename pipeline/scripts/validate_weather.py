#!/usr/bin/env python3
"""How well does Open-Meteo (the weather we can get for *any* place) match what the
hive weather station and the senseBox stations actually measured?

Why this matters: the flight-hours model runs on Open-Meteo everywhere.  The
senseBox stations in the city centre read 1-5 °C warmer than Open-Meteo.  Is
that urban heat (real, keep it) or sensor siting (a box in the sun, discard)?
The SenseCAP S2120 at the hives is a proper station with a radiation shield,
so it is the reference: if it agrees with Open-Meteo, the senseBox offsets are
mostly siting; if it also reads warm, the city really is warmer.

Method
  * hourly means of every station, joined to the Open-Meteo hour (UTC-aligned)
  * per variable: n, bias (median station - model), MAE, RMSE, Pearson r
  * temperature offset by hour of day: median and interquartile range
  * daytime offset = median over hours 10-17 local

Outputs (./output)
  weather_validation.csv    metrics per station and variable
  weather_offsets.csv       temperature offset by hour of day per station
  weather_validation.png    scatter (hive station vs model) + offset curves
"""
import sys
from pathlib import Path

import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from analyse import hourly_hive  # noqa: E402
from common import (HIVE_LAT, HIVE_LON, OUTPUT, floor_hour, load_hive_sensors,  # noqa: E402
                    load_sensebox, open_meteo_hourly)

SENSEBOX_MAX_KM = 3.0          # stations around the hives
SENSEBOX_MIN_HOURS = 500       # need a real summer, not an afternoon
SUMMER = ("2026-06-01", "2026-09-30")
DAY_HOURS = range(10, 18)
HIVE_VARS = {"temp": "out_temp", "rh": "out_humidity", "pressure": "pressure", "wind": "wind"}
SB_VARS = {"temp": "sb_temp", "rh": "sb_humidity", "pressure": "sb_pressure"}
COLORS = ["#1f6f8b", "#e8912d", "#3a9d5d", "#8e5ea2", "#c0392b", "#6b7280"]


def metrics(station, model):
    d = pd.concat([station, model], axis=1, keys=["s", "m"], sort=True).dropna()
    if len(d) < 24:
        return None
    err = d.s - d.m
    return {"n": len(d), "bias_median": err.median(), "mae": err.abs().mean(),
            "rmse": np.sqrt((err ** 2).mean()), "pearson_r": d.s.corr(d.m)}


def offset_by_hour(station, model):
    d = pd.concat([station, model], axis=1, keys=["s", "m"], sort=True).dropna()
    err = (d.s - d.m)
    g = err.groupby(err.index.hour)
    return pd.DataFrame({"median": g.median(), "q25": g.quantile(.25), "q75": g.quantile(.75), "n": g.size()})


def main():
    OUTPUT.mkdir(exist_ok=True)
    rows, offsets = [], {}

    # --- hive weather station (SenseCAP S2120), full record ------------------
    hive = hourly_hive(load_hive_sensors())
    om = open_meteo_hourly(HIVE_LAT, HIVE_LON, "2025-06-01", "2026-10-26").set_index("ts")
    hive.index.name = "ts"
    for var, col in HIVE_VARS.items():
        m = metrics(hive[col], om[var])
        if m:
            rows.append({"station": "Hive weather station (SenseCAP)", "variable": var, "period": "2025-06 .. 2026-10", **m})
    offsets["Hive weather station (SenseCAP)"] = offset_by_hour(hive["out_temp"], om["temp"])
    summer_hive = hive.loc[SUMMER[0]:SUMMER[1]]
    rows.append({"station": "Hive weather station (SenseCAP)", "variable": "temp", "period": "summer 2026",
                 **metrics(summer_hive["out_temp"], om["temp"])})

    # --- senseBox stations within 3 km, summer 2026 ---------------------------
    sb = load_sensebox()
    sb = sb[(sb.dist_km <= SENSEBOX_MAX_KM) & (sb.ts >= SUMMER[0]) & (sb.ts <= SUMMER[1] + " 23:59")].copy()
    sb["hour"] = floor_hour(sb.ts)
    for name, g in sb.groupby("boxName"):
        h = g.groupby("hour")[list(SB_VARS.values())].mean()
        if len(h) < SENSEBOX_MIN_HOURS:
            continue
        lat, lon = g.lat.iloc[0], g.lon.iloc[0]
        om_s = open_meteo_hourly(lat, lon, SUMMER[0], SUMMER[1]).set_index("ts")
        for var, col in SB_VARS.items():
            m = metrics(h[col], om_s[var])
            if m:
                rows.append({"station": name, "variable": var, "period": "summer 2026", **m})
        offsets[name] = offset_by_hour(h["sb_temp"], om_s["temp"])

    table = pd.DataFrame(rows)
    table.to_csv(OUTPUT / "weather_validation.csv", index=False)
    off = pd.concat(offsets, names=["station", "hour"]).reset_index()
    off.to_csv(OUTPUT / "weather_offsets.csv", index=False)

    day = {s: o.loc[list(DAY_HOURS), "median"].median() for s, o in offsets.items()}
    print("temperature: daytime offset vs Open-Meteo (median, hours 10-17)")
    for s, v in sorted(day.items(), key=lambda kv: kv[1]):
        print(f"  {v:+5.1f} °C  {s}")
    print()
    print(table[table.variable == "temp"][["station", "period", "n", "bias_median", "mae", "rmse", "pearson_r"]].round(2).to_string(index=False))
    print()
    print(table[table.station.str.startswith("Hive")][["variable", "period", "n", "bias_median", "mae", "rmse", "pearson_r"]].round(2).to_string(index=False))

    # --- figure --------------------------------------------------------------
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2))
    d = pd.concat([hive["out_temp"], om["temp"]], axis=1, keys=["s", "m"], sort=True).dropna()
    axes[0].scatter(d.m, d.s, s=4, alpha=0.25, color="#1f6f8b", edgecolor="none")
    lim = [min(d.min()) - 1, max(d.max()) + 1]
    axes[0].plot(lim, lim, color="#9aa5b1", lw=1)
    axes[0].set_xlabel("Open-Meteo temperature (°C)")
    axes[0].set_ylabel("hive weather station (°C)")
    axes[0].set_title(f"Hive station vs model, {len(d)} hours, r = {d.s.corr(d.m):.3f}", fontsize=10)
    for (name, o), c in zip(offsets.items(), COLORS):
        axes[1].plot(o.index, o["median"], color=c, lw=2 if name.startswith("Hive") else 1.2,
                     label=f"{name[:28]} ({day[name]:+.1f})")
        axes[1].fill_between(o.index, o.q25, o.q75, color=c, alpha=0.08)
    axes[1].axhline(0, color="#9aa5b1", lw=1)
    axes[1].set_xlabel("hour of day")
    axes[1].set_ylabel("station − Open-Meteo (°C)")
    axes[1].set_title("Temperature offset by hour (median, IQR shaded; label = daytime median)", fontsize=10)
    axes[1].legend(fontsize=7, loc="upper left")
    for ax in axes:
        ax.grid(alpha=0.25)
        ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(OUTPUT / "weather_validation.png", dpi=120)
    print(f"\nwrote output/weather_validation.csv, weather_offsets.csv, weather_validation.png")


if __name__ == "__main__":
    main()
