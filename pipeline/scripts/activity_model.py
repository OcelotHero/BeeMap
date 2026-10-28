#!/usr/bin/env python3
"""Weather -> bee activity (0..1): the rule model, checked against the camera counts,
and bee flight hours per month for Heilbronn.

Two variants of the rule model are evaluated side by side:
  base      the Bee Balance rules: warmth, not-too-hot, calm, sun, dry, daylight
  humidity  base x a humidity factor.  The camera counts show the humid day
            (70-87 % RH) nearly dead while dry afternoons were busy, and
            humidity was as strong a correlate as temperature (|r| ~ 0.58).

Honesty note: the humidity factor's shape was chosen after seeing the nine
real hours, so the agreement below is NOT an independent test.  It is a
sanity check that the rules point the right way.  A real test needs weeks of
continuous footage; the code path is the same.

Outputs (./output)
  activity_validation.csv   per real hour: measured traffic, weather, both rule predictions
  activity_validation.png   predicted vs measured, both variants
  flight_hours.csv          Heilbronn flight hours per month (2025 and 2026 so far), both variants
  flight_hours.png
  city_flight_hours.csv     monthly flight hours 2025 for 8 German cities, both variants + heat hours
"""
import sys
from pathlib import Path

import matplotlib
import numpy as np
import pandas as pd
from scipy import stats

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import HIVE_LAT, HIVE_LON, OUTPUT, open_meteo_hourly  # noqa: E402


# --------------------------------------------------------------------------- #
# The rule model.  Every factor is 0..1; activity is their product.
# --------------------------------------------------------------------------- #
def factors(w):
    """w: DataFrame with temp (°C), rh (%), rain (mm/h), wind (m/s), radiation (W/m²), is_day (0/1)."""
    f = pd.DataFrame(index=w.index)
    f["warmth"] = ((w.temp - 10) / 10).clip(0, 1)          # nothing below 10 °C, full from 20 °C
    f["not_too_hot"] = ((38 - w.temp) / 6).clip(0, 1)      # fades above 32 °C, zero at 38 °C
    f["calm"] = ((8 - w.wind) / 5).clip(0, 1)              # fades above 3 m/s, zero at 8 m/s
    f["sun"] = (w.radiation / 300).clip(0.3, 1)            # overcast counts 30 %
    f["dry"] = (w.rain < 0.2).astype(float)                # no flying in rain
    f["daylight"] = (w.is_day > 0).astype(float)
    f["humidity"] = ((90 - w.rh) / 30).clip(0, 1)          # full below 60 % RH, zero at 90 %
    return f


def rule_activity(w, variant="humidity"):
    f = factors(w)
    cols = ["warmth", "not_too_hot", "calm", "sun", "dry", "daylight"]
    if variant == "humidity":
        cols.append("humidity")
    return f[cols].prod(axis=1)


# --------------------------------------------------------------------------- #
def validate_against_camera():
    """Compare both rule variants with the measured traffic for the real hours."""
    h = pd.read_csv(OUTPUT / "hourly.csv", index_col=0, parse_dates=True)
    real = h.dropna(subset=["activity"]).copy()
    real.index = pd.to_datetime(real.index, utc=True).tz_convert("Europe/Berlin")
    om = open_meteo_hourly(HIVE_LAT, HIVE_LON, "2026-08-01", "2026-09-30").set_index("ts")
    om = om.reindex(real.index)

    # Weather as the hive station measured it, with sun/rain/daylight from Open-Meteo
    # (the station's lux and rain-gauge semantics are not documented).
    station = pd.DataFrame({"temp": real.out_temp, "rh": real.out_humidity, "wind": real.wind,
                            "rain": om.rain, "radiation": om.radiation, "is_day": om.is_day})
    out = real[["activity", "minutes", "out_temp", "out_humidity", "wind", "videos"]].copy()
    out["om_temp"], out["om_rh"] = om.temp, om.rh
    for variant in ("base", "humidity"):
        out[f"pred_{variant}_station"] = rule_activity(station, variant)
        out[f"pred_{variant}_openmeteo"] = rule_activity(om, variant)
    out.to_csv(OUTPUT / "activity_validation.csv")

    print("rule model vs camera traffic (n = %d real hours)" % len(out))
    print(f"  {'variant':10} {'weather':10} {'spearman':>9} {'p':>6} {'pearson':>8}")
    summary = {}
    for variant in ("base", "humidity"):
        for src in ("station", "openmeteo"):
            col = f"pred_{variant}_{src}"
            rs, ps = stats.spearmanr(out[col], out.activity)
            rp = out[col].corr(out.activity)
            summary[(variant, src)] = (rs, ps, rp)
            print(f"  {variant:10} {src:10} {rs:9.2f} {ps:6.2f} {rp:8.2f}")
    print(out[["activity", "out_temp", "out_humidity", "pred_base_station", "pred_humidity_station"]].round(2).to_string())

    fig, axes = plt.subplots(1, 2, figsize=(10, 4), sharey=True)
    for ax, variant in zip(axes, ("base", "humidity")):
        col = f"pred_{variant}_station"
        ax.scatter(out[col], out.activity, s=50, color="#1f6f8b", edgecolor="white", zorder=3)
        for t, r in out.iterrows():
            ax.annotate(f"{t:%d %b %H}h", (r[col], r.activity), fontsize=7, xytext=(4, 3),
                        textcoords="offset points", color="#6b7280")
        rs, ps, _ = summary[(variant, "station")]
        ax.set_title(f"{variant} rules: Spearman {rs:.2f} (p = {ps:.2f})", fontsize=10)
        ax.set_xlabel("predicted activity (0–1), hive-station weather")
        ax.set_xlim(-0.05, 1.05)
        ax.grid(alpha=0.25)
        ax.spines[["top", "right"]].set_visible(False)
    axes[0].set_ylabel("measured traffic (detections / min)")
    fig.suptitle("Rule model vs camera counts — sanity check on 9 hours, not an independent test", fontsize=10)
    fig.tight_layout()
    fig.savefig(OUTPUT / "activity_validation.png", dpi=120)
    return out, summary


def flight_hours():
    """Monthly flight hours for Heilbronn, both variants, 2025 and 2026 to date."""
    w = open_meteo_hourly(HIVE_LAT, HIVE_LON, "2025-01-01", "2026-10-26").set_index("ts")
    fh = pd.DataFrame({v: rule_activity(w, v) for v in ("base", "humidity")})
    fh["heat_hours"] = ((w.temp >= 30) & (w.is_day > 0)).astype(float)
    monthly = fh.groupby([fh.index.year, fh.index.month]).sum().round(1)
    monthly.index.names = ["year", "month"]
    monthly.to_csv(OUTPUT / "flight_hours.csv")
    yearly = fh.groupby(fh.index.year).sum().round(0)
    print("\nflight hours Heilbronn (Open-Meteo at the hive)")
    print(yearly.to_string())

    m25 = monthly.loc[2025]
    fig, ax = plt.subplots(figsize=(8, 3.4))
    x = np.arange(12)
    ax.bar(x - 0.2, m25.base, 0.4, color="#9aa5b1", label=f"base rules ({m25.base.sum():.0f} h)")
    ax.bar(x + 0.2, m25.humidity, 0.4, color="#1f6f8b", label=f"with humidity ({m25.humidity.sum():.0f} h)")
    ax.set_xticks(x, ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])
    ax.set_ylabel("bee flight hours")
    ax.set_title("Heilbronn 2025: flight hours per month", fontsize=10)
    ax.legend(fontsize=8, frameon=False)
    ax.grid(alpha=0.25, axis="y")
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(OUTPUT / "flight_hours.png", dpi=120)
    return monthly, yearly


CITIES = {"Heilbronn": (HIVE_LAT, HIVE_LON), "Freiburg": (47.999, 7.842), "Stuttgart": (48.776, 9.183),
          "Frankfurt": (50.110, 8.682), "Cologne": (50.938, 6.960), "Munich": (48.137, 11.575),
          "Berlin": (52.520, 13.405), "Hamburg": (53.551, 9.994)}


def city_comparison(year=2025):
    """Monthly flight hours for the cities in CITIES, same rule model, Open-Meteo weather at the city centre."""
    rows = []
    for city, (lat, lon) in CITIES.items():
        w = open_meteo_hourly(lat, lon, f"{year}-01-01", f"{year}-12-31").set_index("ts")
        w = w[w.index.year == year]
        f = pd.DataFrame({v: rule_activity(w, v) for v in ("base", "humidity")})
        f["heat_hours"] = ((w.temp >= 30) & (w.is_day > 0)).astype(float)
        m = f.groupby(f.index.month).sum()
        for month, r in m.iterrows():
            rows.append({"city": city, "year": year, "month": int(month), "base": round(r.base, 1),
                         "humidity": round(r.humidity, 1), "heat_hours": int(r.heat_hours)})
    out = pd.DataFrame(rows)
    out.to_csv(OUTPUT / "city_flight_hours.csv", index=False)
    tot = out.groupby("city")[["base", "humidity", "heat_hours"]].sum().sort_values("humidity", ascending=False)
    print(f"\nflight hours {year} by city (rule model on Open-Meteo at the city centre)")
    print(tot.round(0).to_string())
    return out


def main():
    OUTPUT.mkdir(exist_ok=True)
    validate_against_camera()
    flight_hours()
    city_comparison()
    print("\nwrote output/activity_validation.*, flight_hours.*")


if __name__ == "__main__":
    main()
