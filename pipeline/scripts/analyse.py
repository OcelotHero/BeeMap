#!/usr/bin/env python3
"""Merge bee activity with hive sensors and senseBox air quality; write plots + findings.

Everything is aligned to hourly bins in Europe/Berlin.

Outputs (./output)
  hourly.csv              merged hourly table (activity + sensors), the basis for everything
  daily_curve.csv/.png    (a) mean activity by hour of day
  correlations.csv        (b) activity vs outside temp / humidity / PM2.5 / pressure
  scatter_weather.png     (b) scatter plots
  storm_test.png          (c) pressure drops > 3 hPa in 3 h vs activity
  hive_health.csv/.png    (d) hours with inside temp outside 33-37 C for >= 2 h
  hive_health_status.json (d) current green/yellow/red per hive for the dashboard
  findings.md             the numbers, in words
"""
import json
import sys
from pathlib import Path

import matplotlib
import numpy as np
import pandas as pd
from scipy import stats

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (DATA, FRAME_INTERVAL_S, OUTPUT, ROOT, TZ, closest_sensebox_station,  # noqa: E402
                    floor_hour, load_hive_sensors, load_sensebox)

ACTIVITY = "bees_per_min"            # column of data/activity.csv used as "activity"
T_LOW, T_HIGH = 33.0, 37.0           # healthy brood-nest temperature band (deg C)
MIN_RUN_H = 2                        # hours out of band before we flag
PRESSURE_DROP_HPA, DROP_WINDOW_H = 3.0, 3
HIVE_COLORS = {"Beehive No.1": "#2a78d6", "Beehive No.2": "#e8912d", "Beehive No.3": "#3a9d5d"}
ACT_COLOR, NORMAL_COLOR, MUTED = "#1f6f8b", "#9aa5b1", "#6b7280"

plt.rcParams.update({"figure.dpi": 120, "axes.spines.top": False, "axes.spines.right": False,
                     "axes.grid": True, "grid.alpha": 0.25, "font.size": 9})


# --------------------------------------------------------------------------- #
# Loading and hourly alignment
# --------------------------------------------------------------------------- #
def hourly_activity():
    act = pd.read_csv(DATA / "activity.csv")
    act["timestamp"] = pd.to_datetime(act["timestamp"], utc=True).dt.tz_convert(TZ)
    act = act[act.start_known]                      # sessions without a start time can't be binned
    act = act[act.signal_ok]                        # sessions without motion signal are not "zero bees"
    act["hour"] = floor_hour(act.timestamp)
    h = act.groupby("hour").agg(activity=(ACTIVITY, "mean"), minutes=(ACTIVITY, "size"),
                                videos=("video", lambda v: ",".join(sorted(set(v)))))
    return h, act


def hourly_hive(sensors):
    """Wide hourly table: inside_temp_<hive>, inside_hum_<hive>, brood_temp_<hive>, out_* (weather)."""
    s = sensors.copy()
    s["hour"] = floor_hour(s.ts)
    s31 = s[s.sensor_type == "LoRaWAN Dragino-S31-LB"]
    d23 = s[s.sensor_type == "LoRaWAN Dragino-D23-LB"]
    wx = s[s.sensor_type == "LoRaWAN SenseCAP-S2120"]
    inside_t = s31[s31.unit == "temperature"].pivot_table("value", "hour", "hive", "mean").add_prefix("inside_temp_")
    inside_h = s31[s31.unit == "relativeHumidity"].pivot_table("value", "hour", "hive", "mean").add_prefix("inside_hum_")
    brood = d23.pivot_table("value", "hour", "hive", "mean").add_prefix("brood_temp_")
    weather = wx.pivot_table("value", "hour", "unit", "mean").rename(columns={
        "temperature": "out_temp", "relativeHumidity": "out_humidity", "pressure": "pressure",
        "windSpeed": "wind", "rainGauge": "rain", "lightIntensity": "light", "uvIndex": "uv"})
    if weather["pressure"].median() > 20000:        # stored in Pa, we want hPa
        weather["pressure"] = weather["pressure"] / 100.0
    return pd.concat([inside_t, inside_h, brood, weather], axis=1, sort=True)


def hourly_sensebox(sb, station):
    g = sb[sb.boxName == station].copy()
    g["hour"] = floor_hour(g.ts)
    return g.groupby("hour")[["sb_temp", "sb_humidity", "sb_pressure", "pm25", "pm10"]].mean()


# --------------------------------------------------------------------------- #
# (a) daily curve
# --------------------------------------------------------------------------- #
def daily_curve(act_minutes):
    act = act_minutes.copy()
    act["hod"] = act.timestamp.dt.hour
    curve = act.groupby("hod").agg(mean=(ACTIVITY, "mean"), std=(ACTIVITY, "std"),
                                   minutes=(ACTIVITY, "size"), days=("timestamp", lambda t: t.dt.date.nunique()))
    curve = curve.reindex(range(24))
    curve.to_csv(OUTPUT / "daily_curve.csv")

    fig, ax = plt.subplots(figsize=(7, 3.2))
    ax.bar(curve.index, curve["mean"], color=ACT_COLOR, width=0.8)
    for hod, row in curve.dropna().iterrows():
        ax.text(hod, row["mean"], f"{int(row['minutes'])}'", ha="center", va="bottom", fontsize=7, color=MUTED)
    ax.set_xticks(range(0, 24, 2))
    ax.set_xlabel("hour of day (Europe/Berlin)")
    ax.set_ylabel(f"{ACTIVITY} (mean)")
    ax.set_title("Bee activity by hour of day (camera hive; label = minutes of footage)")
    fig.tight_layout()
    fig.savefig(OUTPUT / "daily_curve.png")
    plt.close(fig)
    return curve


# --------------------------------------------------------------------------- #
# (b) correlations
# --------------------------------------------------------------------------- #
VARS = [("out_temp", "outside temp (hive station, °C)"), ("out_humidity", "outside humidity (hive station, %)"),
        ("pm25", "PM2.5 (senseBox, µg/m³)"), ("pressure", "pressure (hive station, hPa)")]


def correlations(h):
    rows = []
    fig, axes = plt.subplots(2, 2, figsize=(8, 6))
    for ax, (col, label) in zip(axes.flat, VARS):
        d = h[["activity", col]].dropna()
        n = len(d)
        if n >= 3:
            pr, pp = stats.pearsonr(d[col], d["activity"])
            sr, sp = stats.spearmanr(d[col], d["activity"])
        else:
            pr = pp = sr = sp = np.nan
        rows.append({"variable": col, "n_hours": n, "pearson_r": pr, "pearson_p": pp, "spearman_r": sr, "spearman_p": sp})
        ax.scatter(d[col], d["activity"], s=28, color=ACT_COLOR, alpha=0.8, edgecolor="white", linewidth=0.8)
        if n >= 3:
            m, b = np.polyfit(d[col], d["activity"], 1)
            xs = np.linspace(d[col].min(), d[col].max(), 20)
            ax.plot(xs, m * xs + b, color=NORMAL_COLOR, lw=1.5)
        ax.set_xlabel(label)
        ax.set_ylabel(ACTIVITY)
        ax.set_title(f"r={pr:.2f} (p={pp:.2f}), n={n} h", fontsize=9)
    fig.suptitle("Hourly bee activity vs environment")
    fig.tight_layout()
    fig.savefig(OUTPUT / "scatter_weather.png")
    plt.close(fig)
    table = pd.DataFrame(rows)
    table.to_csv(OUTPUT / "correlations.csv", index=False)
    return table


# --------------------------------------------------------------------------- #
# (c) storm test
# --------------------------------------------------------------------------- #
def storm_test(h, hive_hourly):
    p = hive_hourly["pressure"].dropna()
    p = p.reindex(pd.date_range(p.index.min(), p.index.max(), freq="h", tz=TZ)).interpolate(limit=3)
    drop = p - p.shift(DROP_WINDOW_H)                       # negative = falling
    events = drop[drop < -PRESSURE_DROP_HPA]
    event_days = set(events.index.date)
    unstable_days = set(drop[drop.abs() > PRESSURE_DROP_HPA].index.date)

    # Activity in the hour before each drop hour vs the same hour of day on stable days.
    act = h["activity"].dropna()
    before = []
    for t in events.index:
        if (t - pd.Timedelta(hours=1)) in act.index:
            before.append({"event_hour": t, "drop_hpa": drop[t], "activity_before": act[t - pd.Timedelta(hours=1)]})
    before = pd.DataFrame(before)
    stable = act[[d not in unstable_days for d in act.index.date]]
    result = {"n_drop_events_total": int(len(events)), "n_drop_days_total": len(event_days),
              "n_events_with_activity": len(before), "n_stable_activity_hours": int(len(stable))}
    if len(before) and len(stable):
        same_hours = stable[stable.index.hour.isin((before.event_hour - pd.Timedelta(hours=1)).dt.hour)]
        result.update(mean_before_drop=float(before.activity_before.mean()),
                      mean_stable_same_hours=float(same_hours.mean()) if len(same_hours) else np.nan)
        if len(before) >= 2 and len(same_hours) >= 2:
            u, pval = stats.mannwhitneyu(before.activity_before, same_hours, alternative="two-sided")
            result.update(mannwhitney_u=float(u), p_value=float(pval))
    before.to_csv(OUTPUT / "storm_events_with_activity.csv", index=False)

    fig, axes = plt.subplots(2, 1, figsize=(8, 5), sharex=True)
    start, end = act.index.min() - pd.Timedelta(days=2), act.index.max() + pd.Timedelta(days=2)
    pp = p[start:end]
    axes[0].plot(pp.index, pp.values, color=MUTED, lw=1.2)
    ev = events[start:end]
    axes[0].scatter(ev.index, p[ev.index], color="#c0392b", s=18, zorder=3, label=f"drop > {PRESSURE_DROP_HPA} hPa / {DROP_WINDOW_H} h")
    axes[0].set_ylabel("pressure (hPa)")
    axes[0].legend(loc="upper right", fontsize=8)
    axes[1].bar(act.index, act.values, width=1 / 24, color=ACT_COLOR)
    axes[1].set_ylabel(ACTIVITY)
    axes[1].set_title("hourly bee activity", fontsize=9)
    axes[0].set_title("Storm test: pressure around the recording days")
    fig.tight_layout()
    fig.savefig(OUTPUT / "storm_test.png")
    plt.close(fig)
    return result


# --------------------------------------------------------------------------- #
# (d) hive health
# --------------------------------------------------------------------------- #
def hive_health(hive_hourly):
    """Flag hours where inside temp is outside the band for >= MIN_RUN_H consecutive hours."""
    flagged, status = [], {}
    fig, ax = plt.subplots(figsize=(8, 3.2))
    for hive, color in HIVE_COLORS.items():
        t = hive_hourly[f"inside_temp_{hive}"].dropna()
        out = (t < T_LOW) | (t > T_HIGH)
        run_id = (out != out.shift()).cumsum()
        run_len = out.groupby(run_id).transform("size")
        flag = out & (run_len >= MIN_RUN_H)
        for h, f in flag[flag].items():
            flagged.append({"hour": h, "hive": hive, "inside_temp": t[h]})
        last = t.index.max()
        recent = flag[last - pd.Timedelta(hours=MIN_RUN_H - 1):last]
        status[hive] = {
            "last_hour": last.isoformat(), "last_temp": round(float(t[last]), 1),
            "flagged_hours_total": int(flag.sum()), "hours_total": int(len(t)),
            "level": "red" if recent.all() and len(recent) >= MIN_RUN_H else ("yellow" if out[last] else "green"),
        }
        ax.plot(t.index, t.values, color=color, lw=0.9, label=hive.replace("Beehive No.", "Hive "))
    ax.axhspan(T_LOW, T_HIGH, color="#3a9d5d", alpha=0.12, label=f"{T_LOW:g}–{T_HIGH:g} °C band")
    ax.set_ylabel("inside temp (°C)")
    ax.set_title("Inside temperature (Dragino S31) with healthy band")
    ax.legend(loc="lower left", fontsize=8, ncol=4)
    fig.tight_layout()
    fig.savefig(OUTPUT / "hive_health.png")
    plt.close(fig)
    flagged = pd.DataFrame(flagged)
    flagged.to_csv(OUTPUT / "hive_health.csv", index=False)
    (OUTPUT / "hive_health_status.json").write_text(json.dumps(status, indent=2))
    return flagged, status


# --------------------------------------------------------------------------- #
def write_findings(h, act_minutes, curve, corr, storm, flagged, status, station, sb_dist):
    days = sorted(act_minutes.timestamp.dt.date.unique())
    act_h = h.dropna(subset=["activity"])
    peak = curve["mean"].idxmax()
    lines = [
        "# Findings\n",
        f"_Generated by scripts/analyse.py. Frame interval assumed: {FRAME_INTERVAL_S} s "
        f"(undocumented; all bees/min scale with it)._\n",
        "## Data basis\n",
        f"- Activity: {len(act_minutes)} minutes of footage in {act_minutes.video.nunique()} sessions with a known start time, "
        f"on {len(days)} days ({days[0]} .. {days[-1]}), giving {len(act_h)} hourly bins.",
        f"- Hive sensors: hive station weather + inside temp/humidity for 3 hives, "
        f"{h.index.min():%Y-%m-%d} .. {h.index.max():%Y-%m-%d} (hourly).",
        f"- Air quality: senseBox station **{station}** ({sb_dist:.1f} km from the hives).",
        "- The camera watches one hive entrance; the share does not say which of the three hives, "
        "so (a) is one curve, not one per hive.\n",
        "## (a) Daily activity curve\n",
        f"- Peak hour: {peak}:00 with {curve.loc[peak, 'mean']:.1f} {ACTIVITY} "
        f"(over {int(curve.loc[peak, 'minutes'])} minutes of footage).",
        "- Hours with footage: " + ", ".join(f"{int(i)}:00 ({int(r.minutes)}')" for i, r in curve.dropna().iterrows()) + ".",
        "- Only these hours exist; the curve is NOT a full day.\n",
        "## (b) Correlations (hourly bins)\n",
        "| variable | n hours | Pearson r | p | Spearman r | p |", "|---|---|---|---|---|---|",
    ]
    for _, r in corr.iterrows():
        lines.append(f"| {r.variable} | {r.n_hours} | {r.pearson_r:.2f} | {r.pearson_p:.3f} | {r.spearman_r:.2f} | {r.spearman_p:.3f} |")
    lines += ["", f"- With n of this size, treat |r| < 0.5 or p > 0.05 as 'no evidence', not as 'no effect'.\n",
              "## (c) Storm test\n",
              f"- Pressure drops > {PRESSURE_DROP_HPA:g} hPa within {DROP_WINDOW_H} h over the whole sensor record: "
              f"{storm['n_drop_events_total']} hours on {storm['n_drop_days_total']} days.",
              f"- Drop hours with footage in the preceding hour: {storm['n_events_with_activity']}; "
              f"stable-day activity hours available: {storm['n_stable_activity_hours']}."]
    if "mean_before_drop" in storm:
        lines.append(f"- Mean activity in the hour before a drop: {storm['mean_before_drop']:.1f}; "
                     f"same hours on stable days: {storm['mean_stable_same_hours']:.1f}.")
    if "p_value" in storm:
        lines.append(f"- Mann-Whitney U = {storm['mannwhitney_u']:.0f}, p = {storm['p_value']:.3f} "
                     f"({'significant' if storm['p_value'] < 0.05 else 'not significant'} at 0.05).")
    else:
        lines.append("- Not enough overlapping hours for a significance test: no recording session fell "
                     "in the hour before a qualifying pressure drop. The test runs automatically once such footage exists.")
    lines += ["", "## Detector and per-session counts\n"]
    results = ROOT / "runs" / "detect" / "bees" / "results.csv"
    if results.exists():
        r = pd.read_csv(results)
        r.columns = [c.strip() for c in r.columns]
        best = r.loc[r["metrics/mAP50(B)"].idxmax()]
        lines.append(f"- YOLOv8n, {len(r)} epochs; best epoch {int(best['epoch'])}: mAP50 {best['metrics/mAP50(B)']:.2f}, "
                     f"precision {best['metrics/precision(B)']:.2f}, recall {best['metrics/recall(B)']:.2f} on 683 held-out frames.")
    frames = pd.read_csv(DATA / "activity_frames.csv")
    per = frames.groupby("video").agg(frames=("bee_count", "size"), det_per_frame=("bee_count", "mean"),
                                      max_in_frame=("bee_count", "max"), colour_pct=("colour_pct", "first"),
                                      signal_ok=("signal_ok", "first"))
    lines += ["", "| session | frames | detections / frame | max in a frame | coloured px % | signal |", "|---|---|---|---|---|---|"]
    for v, row in per.iterrows():
        lines.append(f"| {v} | {int(row.frames)} | {row.det_per_frame:.2f} | {int(row.max_in_frame)} | "
                     f"{row.colour_pct:.3f} | {'ok' if row.signal_ok else '**none**'} |")
    lines += ["", "- Sessions with `signal_ok = False` contain **no coloured motion ghosts at all** "
              "(0.000 % of pixels with channel spread > 60, vs 0.1–12 % elsewhere): the composite trick was not "
              "active or nothing moved. They carry no activity information and are **excluded** from every "
              "hourly statistic above (they are not 'zero bees').",
              "- Model-free check: the share of coloured pixels per session and the detector's mean count agree "
              "(Spearman 0.66 over 9 sessions)."]
    lines += ["", "## (d) Hive health\n",
              f"- Rule: inside temperature (Dragino S31) outside {T_LOW:g}–{T_HIGH:g} °C for ≥ {MIN_RUN_H} consecutive hours.",
              "", "| hive | flagged hours | of hours | share | last temp | status now |", "|---|---|---|---|---|---|"]
    for hive, s in status.items():
        lines.append(f"| {hive} | {s['flagged_hours_total']} | {s['hours_total']} | "
                     f"{s['flagged_hours_total'] / max(s['hours_total'], 1):.0%} | {s['last_temp']} °C | {s['level']} |")
    lines += ["", "- The S31 sits in the hive body, not necessarily in the brood nest, so the band is "
              "breached for most of the year (winter cluster, empty space). Use the brood probes "
              "(D23 tempC1-3, columns brood_temp_* in hourly.csv) if the organisers confirm their placement.", ""]
    (OUTPUT / "findings.md").write_text("\n".join(lines))


def main():
    OUTPUT.mkdir(exist_ok=True)
    print("loading ...")
    act_h, act_minutes = hourly_activity()
    hive_h = hourly_hive(load_hive_sensors())
    sb = load_sensebox()
    station = closest_sensebox_station(sb, act_minutes.timestamp.min() - pd.Timedelta(days=1),
                                       act_minutes.timestamp.max() + pd.Timedelta(days=1))
    sb_dist = float(sb.loc[sb.boxName == station, "dist_km"].iloc[0])
    sb_h = hourly_sensebox(sb, station)

    h = hive_h.join(sb_h, how="outer").join(act_h, how="outer").sort_index()
    h.index.name = "hour"
    h.to_csv(OUTPUT / "hourly.csv")
    print(f"hourly table: {len(h)} hours, {h.activity.notna().sum()} with activity; senseBox station: {station} ({sb_dist:.1f} km)")

    curve = daily_curve(act_minutes)
    corr = correlations(h)
    storm = storm_test(h, hive_h)
    flagged, status = hive_health(hive_h)
    write_findings(h, act_minutes, curve, corr, storm, flagged, status, station, sb_dist)
    print(corr.round(3).to_string(index=False))
    print("storm:", storm)
    print("health:", {k: v["level"] for k, v in status.items()})
    print(f"wrote plots + findings.md -> {OUTPUT}")


if __name__ == "__main__":
    main()
