"""
Correlating nest reports from the public with what the smart hives found.

Two independent kinds of evidence point at the same thing:
  * triangulated nest areas: 95 % ellipses where flight lines from several
    hives cross
  * direction-only corridors: one hive's flight line, nest somewhere along it
  * nest reports: a person found a nest at a spot

A report "matches" an area if it is statistically consistent with it: the
Mahalanobis distance of the report from the area's centre, using the area's
own uncertainty (a long thin ellipse is precise sideways, not lengthwise)
plus about REPORT_SIGMA_M for where people drop the pin. For corridors the
test is the angle off the flight line, in units of the line's uncertainty.

Levels:  strong (inside the 68 % zone)  fits (95 %)  near (99.7 %, shown only)
"""
from __future__ import annotations

import math
import time

REPORT_SIGMA_M = 50.0                  # how precisely people pin a nest on the map
CHI2_68, CHI2_95, CHI2_997 = 2.30, 5.99, 11.83   # 2 degrees of freedom
CORRIDOR_MAX_M = 2500.0
SEASON_DAYS = 150                      # nests are built in spring/summer and abandoned by winter
CANDIDATE_STATUSES = ("pending", "asian_hornet")
M_PER_DEG = 111320.0


def _xy(lat0, lon0, lat, lon):
    return ((lon - lon0) * M_PER_DEG * math.cos(math.radians(lat0)), (lat - lat0) * M_PER_DEG)


def _ang(a, b):
    return (a - b + 180) % 360 - 180


def nest_m2(nest: dict, lat: float, lon: float) -> tuple[float, float]:
    """Squared Mahalanobis distance of a point from a nest area, and metres."""
    x, y = _xy(nest["lat"], nest["lon"], lat, lon)
    k = math.sqrt(CHI2_95)                               # stored axes are 95 % semi-axes
    s_maj = math.hypot(nest["semi_major_m"] / k, REPORT_SIGMA_M)
    s_min = math.hypot(nest["semi_minor_m"] / k, REPORT_SIGMA_M)
    o = math.radians(nest.get("orientation_deg") or 0)
    u = x * math.sin(o) + y * math.cos(o)                # along the major axis
    v = x * math.cos(o) - y * math.sin(o)                # across it
    return (u / s_maj) ** 2 + (v / s_min) ** 2, math.hypot(x, y)


def corridor_z(line: dict, lat: float, lon: float) -> tuple[float, float] | None:
    """How many sigmas off a corridor's flight line a point lies, and metres out."""
    x, y = _xy(line["lat"], line["lon"], lat, lon)
    d = math.hypot(x, y)
    if d < 30 or d > CORRIDOR_MAX_M:
        return None
    off = _ang(math.degrees(math.atan2(x, y)) % 360, line["bearing"])
    if abs(off) > 90:
        return None                                      # behind the hive
    sig = math.hypot(line["sigma"], math.degrees(REPORT_SIGMA_M / d))
    return abs(off) / sig, d


def level_from_m2(m2: float) -> str | None:
    return "strong" if m2 <= CHI2_68 else "fits" if m2 <= CHI2_95 else "near" if m2 <= CHI2_997 else None


def level_from_z(z: float) -> str | None:
    return "strong" if z <= 1.0 else "fits" if z <= 2.0 else "near" if z <= 3.0 else None


def match(nests: list[dict], lines: list[dict], reports: list[dict], now: float | None = None) -> dict:
    """{"by_report": {rid: best match}, "by_nest": {nest_id: [matches]}, "by_line": {key: [matches]}}"""
    now = now or time.time()
    by_report, by_nest, by_line = {}, {}, {}
    active = [n for n in nests if n.get("status") in ("suspected", "assigned")]
    corridors = [l for l in lines if l.get("corridor")]
    for r in reports:
        if r["status"] not in CANDIDATE_STATUSES or now - r["ts"] > SEASON_DAYS * 86400:
            continue
        cands = []
        for n in active:
            m2, d = nest_m2(n, r["lat"], r["lon"])
            lv = level_from_m2(m2)
            if lv:
                cands.append({"kind": "nest", "nest_id": n["id"], "level": lv, "score": round(m2, 2),
                              "dist_m": round(d), "p": round(math.exp(-0.5 * m2), 3)})
        for l in corridors:
            zc = corridor_z(l, r["lat"], r["lon"])
            if zc:
                lv = level_from_z(zc[0])
                if lv:
                    cands.append({"kind": "corridor", "line": l["key"], "observer_id": l["observer_id"], "level": lv,
                                  "score": round(zc[0] ** 2, 2), "dist_m": round(zc[1]), "p": round(math.exp(-0.5 * zc[0] ** 2), 3)})
        if not cands:
            continue
        best = min(cands, key=lambda c: (c["kind"] != "nest", c["score"]))   # a crossing beats a single line
        best = dict(best)
        # a small nest found late in the season may be an abandoned spring (primary) nest
        month = time.localtime(r["ts"]).tm_mon
        if r.get("size") == "fist" and month >= 7:
            best["note"] = "small nest late in the season: may be an old spring nest"
        by_report[r["id"]] = best
        if best["kind"] == "nest":
            by_nest.setdefault(best["nest_id"], []).append({"report_id": r["id"], "status": r["status"], **best})
        else:
            by_line.setdefault(best["line"], []).append({"report_id": r["id"], "status": r["status"], **best})
    for v in list(by_nest.values()) + list(by_line.values()):
        v.sort(key=lambda m: (m["status"] != "asian_hornet", m["score"]))
    return {"by_report": by_report, "by_nest": by_nest, "by_line": by_line}


def corroborates(m: dict | None) -> bool:
    """Is this match good enough to count as evidence (not just 'near')?"""
    return bool(m) and m["level"] in ("strong", "fits")
