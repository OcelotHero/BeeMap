"""
Colony health indicators from smart-hive sensors.

Inputs per hive, all optional:
  * in-hive temperature and humidity
  * entrance activity (bees in view at the entrance, counted by the Pi)
  * outside weather from the hive's own SensoBox (or the nearest one)
  * Asian hornet detections at this hive
  * the flower supply around the hive (ecology model + SensoBox pollen)

The idea: a healthy colony's flight activity follows the weather, and a
colony rearing brood holds its brood nest near 35 °C whatever the weather.
When those relationships break, something changed inside. These are
indicators that suggest an inspection, never a diagnosis. Thresholds are
rules of thumb from beekeeping practice, collected as named constants.
"""
from __future__ import annotations

import math
import statistics as st
import time
from datetime import datetime

BROOD_MONTHS = range(3, 10)        # March..September: brood rearing in full swing
BROOD_BAND = (33.0, 37.0)          # brood nest temperature band, °C
FLY_MIN_T = 12.0                   # honeybees fly from about 12 °C
DAYLIGHT_LUX = 1000.0
DAMP_RH = 85.0
WINDOW_DAYS = 30
RECENT_H = 72


def _pearson(xs, ys):
    if len(xs) < 8:
        return None
    mx, my = st.fmean(xs), st.fmean(ys)
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if sx < 1e-9 or sy < 1e-9:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy)


def _bucket(rows, fields, ts_key="ts"):
    out = {}
    for r in rows:
        h = int(r[ts_key] // 3600)
        d = out.setdefault(h, {})
        for f in fields:
            if r.get(f) is not None:
                d.setdefault(f, []).append(float(r[f]))
    return {h: {k: st.fmean(v) for k, v in d.items()} for h, d in out.items()}


def _ind(key, status, title, detail, value=None):
    return {"key": key, "status": status, "title": title, "detail": detail, "value": value}


def analyse(hive_rows: list[dict], outside_rows: list[dict], hornet_ts: list[float],
            forage_ratio: float | None, now: float | None = None) -> dict:
    now = now or time.time()
    start = now - WINDOW_DAYS * 86400
    hive = _bucket([r for r in hive_rows if r["ts"] >= start], ("hive_temp_c", "hive_humidity", "activity"))
    out = _bucket([r for r in outside_rows if r["ts"] >= start], ("temp_c", "humidity", "lux"))
    hours = sorted(hive)
    if not hours:
        return {"status": "none", "summary": "No in-hive data yet.", "indicators": [], "series": None}

    now_h = int(now // 3600)
    recent = [h for h in hours if h > now_h - RECENT_H]
    base = [h for h in hours if h <= now_h - RECENT_H - 24]
    month = datetime.fromtimestamp(now).month
    ind = []

    # ------------------------------------------------ activity vs weather
    def fly(h):
        o = out.get(h, {})
        return (o.get("lux", 0) >= DAYLIGHT_LUX and o.get("temp_c") is not None
                and o["temp_c"] >= FLY_MIN_T and o.get("humidity", 0) < 90)

    def x_of(h):          # flying "pull" of the weather: degrees above 8 °C
        return max(0.0, out[h]["temp_c"] - 8.0)

    act_pts = [(h, hive[h]["activity"]) for h in hours if "activity" in hive[h] and h in out
               and out[h].get("lux", 0) >= DAYLIGHT_LUX and "temp_c" in out[h]]
    r_coupling = _pearson([out[h]["temp_c"] for h, _ in act_pts], [a for _, a in act_pts]) if act_pts else None
    base_ratios = [a / x_of(h) for h, a in act_pts if h in base and fly(h) and x_of(h) > 2]
    k = st.median(base_ratios) if len(base_ratios) >= 6 else None
    recent_fly = [(h, a) for h, a in act_pts if h in recent and fly(h) and x_of(h) > 2]
    act_index = None
    if k and k > 0 and len(recent_fly) >= 3:
        act_index = st.median([a / (k * x_of(h)) for h, a in recent_fly])
        if act_index < 0.25:
            ind.append(_ind("activity", "alert", "Very few bees flying in good weather",
                            f"About {act_index * 100:.0f}% of this colony's usual activity for the weather. "
                            "Check the colony soon.", round(act_index, 2)))
        elif act_index < 0.6:
            ind.append(_ind("activity", "watch", "Fewer bees flying than the weather allows",
                            f"About {act_index * 100:.0f}% of the usual activity for this weather over the last 3 days.",
                            round(act_index, 2)))
        else:
            ind.append(_ind("activity", "ok", "Flight activity as expected for the weather",
                            f"{min(act_index, 2) * 100:.0f}% of the usual activity for this weather.", round(act_index, 2)))
        # sudden drop: last 24 h vs the 3 days before
        last = [a / (k * x_of(h)) for h, a in recent_fly if h > now_h - 24]
        prev = [a / (k * x_of(h)) for h, a in act_pts if now_h - 96 < h <= now_h - 24 and fly(h) and x_of(h) > 2]
        if len(last) >= 3 and len(prev) >= 3 and st.median(last) < 0.4 * st.median(prev):
            ind.append(_ind("drop", "alert", "Sudden drop in activity",
                            "Flight activity fell by more than half within a day in good weather. Possible causes include "
                            "poisoning, a swarm leaving, or robbing. Worth a look today.", None))
    elif act_pts:
        ind.append(_ind("activity", "none", "Not enough flying weather to judge activity",
                        "Too few warm daylight hours recently.", None))
    if r_coupling is not None and len(act_pts) >= 24:
        good = r_coupling >= 0.5
        ind.append(_ind("coupling", "ok" if good else "watch",
                        "Activity follows the weather" if good else "Activity hardly follows the weather",
                        f"Correlation with outside temperature r = {r_coupling:.2f} over {len(act_pts)} daylight hours."
                        + ("" if good else " Healthy colonies fly much more on warm hours."), round(r_coupling, 2)))

    # ------------------------------------------------ hive temperature
    tpairs = [(hive[h]["hive_temp_c"], out[h]["temp_c"]) for h in hours if "hive_temp_c" in hive[h] and h in out and "temp_c" in out[h]]
    rec_t = [(hive[h]["hive_temp_c"], out[h]["temp_c"]) for h in recent if "hive_temp_c" in hive[h] and h in out and "temp_c" in out[h]]
    base_t = [(hive[h]["hive_temp_c"], out[h]["temp_c"]) for h in base if "hive_temp_c" in hive[h] and h in out and "temp_c" in out[h]]
    if len(rec_t) >= 12:
        if month in BROOD_MONTHS:
            share = sum(BROOD_BAND[0] <= t <= BROOD_BAND[1] for t, _ in rec_t) / len(rec_t)
            r_t = _pearson([o for _, o in rec_t], [t for t, _ in rec_t])
            if share < 0.3 and (r_t or 0) > 0.6:
                ind.append(_ind("thermo", "alert", "Brood nest isn't being kept warm",
                                f"Only {share * 100:.0f}% of the last 3 days at 33–37 °C, and the hive temperature follows "
                                "the weather. Possible loss of the queen or brood, or a very weak colony.", round(share, 2)))
            elif share < 0.6:
                ind.append(_ind("thermo", "watch", "Brood nest temperature unsteady",
                                f"{share * 100:.0f}% of the last 3 days at 33–37 °C.", round(share, 2)))
            else:
                ind.append(_ind("thermo", "ok", "Brood nest kept at 33–37 °C",
                                f"{share * 100:.0f}% of the last 3 days.", round(share, 2)))
        else:
            delta = st.fmean(t - o for t, o in rec_t)
            base_delta = st.fmean(t - o for t, o in base_t) if len(base_t) >= 12 else None
            if delta < 2.5 and (base_delta is None or base_delta > 5):
                ind.append(_ind("thermo", "alert", "Hive is as cold as outside",
                                f"Only {delta:.1f} °C warmer than outside over the last 3 days"
                                + (f" (it was {base_delta:.0f} °C warmer before)" if base_delta else "")
                                + ". The winter cluster may have died or left. Check soon.", round(delta, 1)))
            elif delta < 6:
                ind.append(_ind("thermo", "watch", "Winter cluster running cool",
                                f"{delta:.1f} °C warmer than outside. A small cluster or a cold spot near the sensor.",
                                round(delta, 1)))
            else:
                ind.append(_ind("thermo", "ok", "Winter cluster keeps the hive warm",
                                f"{delta:.1f} °C warmer than outside over the last 3 days.", round(delta, 1)))

    # ------------------------------------------------ humidity
    rec_h = [hive[h]["hive_humidity"] for h in recent if "hive_humidity" in hive[h]]
    if len(rec_h) >= 12:
        damp = sum(v > DAMP_RH for v in rec_h) / len(rec_h)
        if damp > 0.5:
            ind.append(_ind("humidity", "watch", "Damp hive",
                            f"Humidity above {DAMP_RH:.0f}% for {damp * 100:.0f}% of the last 3 days. Check ventilation "
                            "and for condensation under the lid.", round(st.fmean(rec_h))))
        else:
            ind.append(_ind("humidity", "ok", "Humidity fine", f"Average {st.fmean(rec_h):.0f}% over the last 3 days.",
                            round(st.fmean(rec_h))))

    # ------------------------------------------------ hornets
    hornets7 = sum(1 for t in hornet_ts if t >= now - 7 * 86400)
    if hornets7 >= 3:
        gone = any(i["status"] == "alert" and i["key"] in ("thermo", "activity") for i in ind)
        # hornets explain *reduced* foraging; they don't explain a colony that is gone
        low = act_index is not None and 0.25 <= act_index < 0.8 and not gone
        ind.append(_ind("hornets", "watch", "Hornets are holding foragers back" if low else "Asian hornets hunting here",
                        f"{hornets7} hornet visits in the last 7 days."
                        + (" Bees stay in when hornets hover at the entrance, so activity is down. "
                           "An entrance reducer or hornet muzzle helps." if low else " Fit an entrance reducer."), hornets7))

    # ------------------------------------------------ forage
    if forage_ratio is not None and forage_ratio < 0.85:
        ind.append(_ind("forage", "info", "Few flowers around the hive this month",
                        f"About {forage_ratio * 100:.0f}% of what bees need within 800 m. Low activity can mean hunger, "
                        "not illness: check food stores.", round(forage_ratio, 2)))

    order = {"alert": 0, "watch": 1, "info": 2, "ok": 3, "none": 4}
    ind.sort(key=lambda i: order[i["status"]])
    status = "alert" if any(i["status"] == "alert" for i in ind) else \
        "watch" if any(i["status"] == "watch" for i in ind) else "ok"
    summary = {"alert": "Needs a look soon", "watch": "Keep an eye on it", "ok": "Looks healthy"}[status]
    return {"status": status, "summary": summary, "indicators": ind,
            "activity_index": None if act_index is None else round(act_index, 2),
            "weather_coupling": None if r_coupling is None else round(r_coupling, 2),
            "hornets_7d": hornets7, "series": _daily(hive, out, k, start, now)}


def _daily(hive, out, k, start, now):
    """Per-day series for charts: activity vs expected, temperatures, humidity."""
    days = {}
    for h, v in hive.items():
        d = days.setdefault(h // 24, {"act": 0.0, "exp": 0.0, "ht": [], "ot": [], "hh": []})
        o = out.get(h, {})
        if "activity" in v and o.get("lux", 0) >= DAYLIGHT_LUX:
            d["act"] += v["activity"]
            if k and "temp_c" in o and o["temp_c"] >= FLY_MIN_T:
                d["exp"] += k * max(0.0, o["temp_c"] - 8.0)
        if "hive_temp_c" in v:
            d["ht"].append(v["hive_temp_c"])
        if "temp_c" in o:
            d["ot"].append(o["temp_c"])
        if "hive_humidity" in v:
            d["hh"].append(v["hive_humidity"])
    keys = sorted(days)
    f = lambda xs: round(st.fmean(xs), 1) if xs else None
    return {"day": [datetime.fromtimestamp(d * 86400).strftime("%d %b") for d in keys],
            "activity": [round(days[d]["act"]) for d in keys],
            "expected": [round(days[d]["exp"]) if k else None for d in keys],
            "hive_temp": [f(days[d]["ht"]) for d in keys], "outside_temp": [f(days[d]["ot"]) for d in keys],
            "hive_humidity": [f(days[d]["hh"]) for d in keys]}
