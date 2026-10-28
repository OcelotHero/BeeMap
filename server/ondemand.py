"""
Fetch public data when it's needed, instead of a manual download step.

  * OpenStreetMap land cover for Heilbronn: fetched in the background on first
    start (one Overpass request), kept for OSM_TTL_DAYS, then refreshed.
  * iNaturalist flowering times: fetched in the background one plant at a
    time (the API asks for about one request per second) and saved after each
    plant, so they appear progressively and survive restarts. Each plant is
    re-checked after BLOOM_TTL_DAYS.

Until the data arrives, or without internet, BeeMap uses its built-in demo
shapes and curated bloom months, and says so. Failed fetches are retried at
most every RETRY_S seconds, triggered by requests that need the data.

BEEMAP_ONLINE=off disables all fetching (offline demos, tests).
BEEMAP_GEODATA=demo keeps the demo map even when online.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import threading
import time
from typing import Callable

import bloom_import
import ecology
import geodata
import osm_import

OSM_TTL_DAYS = 30
BLOOM_TTL_DAYS = 90
RETRY_S = 600
BLOOM_PLACES = ("Baden-Württemberg", "Germany")
BLOOM_MIN_OBS = 30

STATUS: dict = {"osm": {"state": "idle"}, "bloom": {"state": "idle"}}
rebuild: Callable[[], None] = lambda: None          # set by the app: rebuild the forage model
on_change: Callable[[str], None] = lambda kind: None  # set by the app: tell browsers
_lock = threading.Lock()
_threads: dict[str, threading.Thread] = {}
_last_try: dict[str, float] = {}


def online() -> bool:
    return os.environ.get("BEEMAP_ONLINE", "on").lower() != "off"


def _age_days(path) -> float | None:
    return (time.time() - path.stat().st_mtime) / 86400 if path.exists() else None


# ------------------------------------------------------------------ what's needed
def osm_needed() -> bool:
    if os.environ.get("BEEMAP_GEODATA", "auto").lower() == "demo":
        return False
    age = _age_days(geodata.OSM_FILE)
    return age is None or age > OSM_TTL_DAYS


def _bloom_data() -> dict:
    try:
        return json.loads(ecology.BLOOM_FILE.read_text())
    except (OSError, ValueError):
        return bloom_import.header(BLOOM_MIN_OBS)


def bloom_todo(data: dict | None = None) -> list:
    data = data or _bloom_data()
    checked = data.get("checked", {})
    limit = (dt.date.today() - dt.timedelta(days=BLOOM_TTL_DAYS)).isoformat()
    return [p for p in ecology.PLANTS if checked.get(p.id, "") < limit]


def status() -> dict:
    s = json.loads(json.dumps(STATUS))
    s["osm"].update(source=geodata.SOURCE["kind"], fetched=geodata.SOURCE.get("fetched"))
    data = _bloom_data()
    s["bloom"].update(observed=len(data.get("plants", {})), checked=len(data.get("checked", {})),
                      total=len(ecology.PLANTS), fetched=data.get("fetched"))
    if s["osm"]["state"] == "idle":
        s["osm"]["state"] = "ready" if geodata.SOURCE["kind"] == "osm" else ("off" if not online() else "demo")
    if s["bloom"]["state"] == "idle":
        s["bloom"]["state"] = "ready" if not bloom_todo(data) else ("off" if not online() else "partial")
    return s


# ------------------------------------------------------------------ trigger
def kick():
    """Cheap; call it from requests. Starts background fetches when needed."""
    if not online():
        return
    if osm_needed():
        _start("osm", fetch_osm)
    if bloom_todo():
        _start("bloom", fetch_bloom)


def _start(kind, fn):
    with _lock:
        t = _threads.get(kind)
        if t and t.is_alive():
            return
        if STATUS[kind].get("state") == "failed" and time.time() - _last_try.get(kind, 0) < RETRY_S:
            return
        _last_try[kind] = time.time()
        t = threading.Thread(target=fn, name=f"fetch-{kind}", daemon=True)
        _threads[kind] = t
        t.start()


# ------------------------------------------------------------------ fetchers
def fetch_osm(data: dict | None = None):
    STATUS["osm"] = {"state": "loading", "since": time.time()}
    on_change("status")
    try:
        res = osm_import.fetch_and_save(data=data)
        geodata.load()
        rebuild()
        STATUS["osm"] = {"state": "ready", "counts": res.get("counts", {})}
    except BaseException as e:          # SystemExit from the fetcher, network errors, bad data
        STATUS["osm"] = {"state": "failed", "error": str(e)[:300], "retry_after_s": RETRY_S}
    on_change("osm")


def fetch_bloom(api=None):
    data = _bloom_data()
    todo = bloom_todo(data)
    STATUS["bloom"] = {"state": "loading", "todo": len(todo)}
    on_change("status")
    try:
        api = api or bloom_import.Api()
        places = data.get("places") or [p for p in (bloom_import.find_place(api, n) for n in BLOOM_PLACES) if p]
        if not places:
            raise RuntimeError("iNaturalist didn't find Baden-Württemberg or Germany")
        data["places"] = places
        data.setdefault("checked", {})
        data.setdefault("plants", {})
        for k, p in enumerate(todo, 1):
            got = bloom_import.fetch_plant(api, p, places, BLOOM_MIN_OBS)
            data["checked"][p.id] = dt.date.today().isoformat()
            if got:
                data["plants"][p.id] = got
            data["fetched"] = dt.date.today().isoformat()
            bloom_import.save(ecology.BLOOM_FILE, data)
            ecology.load_bloom()
            STATUS["bloom"] = {"state": "loading", "todo": len(todo) - k}
            if k % 5 == 0:
                on_change("bloom")
        rebuild()                       # plantings' bloom months feed the forage model
        STATUS["bloom"] = {"state": "ready"}
    except BaseException as e:
        STATUS["bloom"] = {"state": "failed", "error": str(e)[:300], "retry_after_s": RETRY_S}
    on_change("bloom")
