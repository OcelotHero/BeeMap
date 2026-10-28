"""
Results of the BeeMap data pipeline (pipeline/), served to the map.

The pipeline (pipeline/Makefile) does the heavy work offline: it fetches the
hive camera recordings, BeeHappy hive sensors, senseBox stations, summer-school
heat walks, OpenStreetMap land use and GBIF sightings, trains the bee detector
and writes small result files to pipeline/output/. This module only reads
those files, with the standard library, so the server needs nothing extra.
Files are re-read when they change, so rerunning a pipeline step updates the
map without a restart.

Datasets (each is optional; a missing one is reported in status()):
  balance      250 m Bee Balance squares: food within 1.5 km (honeybees) and
               300 m (wild bees), verdict green / yellow / red, street heat
  schools      every school with its square's verdict
  neighbourhoods  same for districts and neighbourhoods
  sightings    GBIF records of Asian hornet, European hornet and honey bee
  heat         street heat from the summer-school walks, 100 m cells
  flight       bee flight hours: Heilbronn by month, 8-city comparison
  study        what the hive camera measured: traffic vs weather
"""
from __future__ import annotations

import csv
import json
import os
import threading
from pathlib import Path

OUT = Path(os.environ.get("BEEMAP_PIPELINE_OUT", Path(__file__).resolve().parent.parent / "pipeline" / "output"))
CELL_M = 250.0

FILES = {
    "balance": ["bee_balance_grid.csv", "bee_balance_summary.json"],
    "schools": ["bee_balance_schools.csv"],
    "neighbourhoods": ["bee_balance_neighbourhoods.csv"],
    "sightings": ["hornet_sightings.csv", "hornet_summary.json"],
    "heat": ["heat_walks_grid.csv"],
    "flight": ["flight_hours.csv", "city_flight_hours.csv"],
    "study": ["correlations.csv", "activity_validation.csv"],
}

_cache: dict[str, tuple[float, object]] = {}
_lock = threading.Lock()


def _f(v):
    if v in (None, ""):
        return None
    try:
        return float(v)
    except ValueError:
        return None


def _rows(name: str) -> list[dict]:
    with open(OUT / name, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _json(name: str):
    return json.loads((OUT / name).read_text(encoding="utf-8"))


def _cached(key: str, build):
    """Build a dataset once, again only when one of its files changes."""
    paths = [OUT / n for n in FILES[key]]
    if not all(p.exists() for p in paths):
        return None
    mtime = max(p.stat().st_mtime for p in paths)
    with _lock:
        hit = _cache.get(key)
        if hit and hit[0] == mtime:
            return hit[1]
    value = build()
    with _lock:
        _cache[key] = (mtime, value)
    return value


def status() -> dict:
    out = {}
    for key, names in FILES.items():
        paths = [OUT / n for n in names]
        ok = all(p.exists() for p in paths)
        out[key] = {"available": ok, "updated": max(p.stat().st_mtime for p in paths) if ok else None}
    return {"pipeline_output": str(OUT), "datasets": out}


# ---------------------------------------------------------------- Bee Balance
def balance():
    def build():
        cells = []
        for r in _rows("bee_balance_grid.csv"):
            cells.append([round(float(r["lat"]), 6), round(float(r["lon"]), 6),
                          round(float(r["forage_1500"]), 3), round(float(r["forage_300"]), 3), r["category"],
                          None if _f(r.get("street_heat_c")) is None else round(_f(r["street_heat_c"]), 2),
                          r["cell_id"]])
        s = _json("bee_balance_summary.json")
        return {"cell_m": CELL_M, "fields": ["lat", "lon", "food_1500", "food_300", "category", "street_heat_c", "cell_id"],
                "cells": cells, "thresholds": s.get("thresholds"), "shares": s.get("shares"), "hive": s.get("hive"),
                "n_cells": s.get("n_cells"), "area_km2": s.get("area_km2")}
    return _cached("balance", build)


def _places(key: str, fname: str):
    def build():
        return [{"name": r["name"], "kind": r["kind"], "lat": float(r["lat"]), "lon": float(r["lon"]),
                 "food_1500": round(float(r["forage_1500"]), 3), "food_300": round(float(r["forage_300"]), 3),
                 "category": r["category"], "cell_id": r.get("cell_id")}
                for r in _rows(fname)]
    return _cached(key, build)


def schools():
    return _places("schools", "bee_balance_schools.csv")


def neighbourhoods():
    return _places("neighbourhoods", "bee_balance_neighbourhoods.csv")


# ---------------------------------------------------------------- GBIF sightings
def sightings():
    def build():
        pts = []
        for r in _rows("hornet_sightings.csv"):
            lat, lon = _f(r.get("lat")), _f(r.get("lon"))
            if lat is None or lon is None:
                continue
            pts.append({"species": r["species"], "common": r["common"], "date": r.get("date") or None,
                        "year": int(r["year"]) if r.get("year") else None, "lat": lat, "lon": lon,
                        "uncertainty_m": _f(r.get("uncertainty_m")), "source": r.get("source") or "",
                        "locality": r.get("locality") or "", "license": r.get("license") or "",
                        "url": f"https://www.gbif.org/occurrence/{r['gbif_key']}" if r.get("gbif_key") else None})
        summ = _json("hornet_summary.json")
        return {"sightings": pts, "summary": {k: summ.get(k) for k in ("asian_by_year", "state_asian_by_year", "n",
                                                                        "asian_within_3km", "nearest_asian", "fetched")}}
    return _cached("sightings", build)


# ---------------------------------------------------------------- street heat
def heat():
    def build():
        return {"cell_m": 100.0, "note": "Each walk referenced to its own median after a 15-minute warm-up cut (1–3 Sep 2026).",
                "cells": [[round(float(r["lat"]), 5), round(float(r["lon"]), 5), round(float(r["delta_c"]), 2), int(float(r["n"]))]
                          for r in _rows("heat_walks_grid.csv")]}
    return _cached("heat", build)


# ---------------------------------------------------------------- flight hours
def flight():
    def build():
        months = [{"month": int(r["month"]), "base": _f(r["base"]), "humidity": _f(r["humidity"]),
                   "heat_hours": _f(r["heat_hours"])} for r in _rows("flight_hours.csv") if r["year"] == "2025"]
        cities: dict[str, dict] = {}
        for r in _rows("city_flight_hours.csv"):
            c = cities.setdefault(r["city"], {"city": r["city"], "base": 0.0, "humidity": 0.0, "months": []})
            c["base"] += _f(r["base"]) or 0
            c["humidity"] += _f(r["humidity"]) or 0
            c["months"].append(_f(r["humidity"]))
        ranked = sorted(cities.values(), key=lambda c: -c["humidity"])
        for c in ranked:
            c["base"], c["humidity"] = round(c["base"]), round(c["humidity"])
        return {"year": 2025, "heilbronn_months": months, "cities": ranked,
                "note": "Rule model on Open-Meteo 2025 weather. 'humidity' adds the humidity term the hive camera supports."}
    return _cached("flight", build)


# ---------------------------------------------------------------- hive camera study
def study():
    def build():
        corr = [{"variable": r["variable"], "n_hours": int(r["n_hours"]), "spearman_r": round(float(r["spearman_r"]), 2),
                 "p": round(float(r["spearman_p"]), 3)} for r in _rows("correlations.csv")]
        hours = [{"hour": r["hour"], "activity": round(float(r["activity"]), 1), "out_temp": round(float(r["out_temp"]), 1),
                  "out_humidity": round(float(r["out_humidity"])), "minutes": round(float(r["minutes"]))}
                 for r in _rows("activity_validation.csv")]
        return {"correlations": corr, "hours": hours,
                "note": "Bee detections per minute at one OpenSpace hive entrance, Aug 2026, YOLOv8n detector (mAP50 0.80). "
                        "Six hourly bins: a direction check, not a model."}
    return _cached("study", build)
