"""
BeeMap server.

Receives hornet departures from Raspberry Pi stations running VespAI, lets the
municipality confirm or dismiss them, and turns departure bearings into
suspected nest areas by triangulation.

Run:  uvicorn app:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from fastapi import (Depends, FastAPI, File, Form, Header, HTTPException, Request,
                     UploadFile, WebSocket, WebSocketDisconnect)
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import threading

from triangulate import (LocalFrame, Observation, baseline, estimate_nests, evaluate_site,
                         line_key, planning_targets, suggest_sites)
import ecology
import geodata
import health as colony
import nest_classifier
import correlate
import ondemand
import citydata

BASE = Path(__file__).resolve().parent
DATA = Path(os.environ.get("BEEMAP_DATA", BASE / "data"))
IMAGES = DATA / "images"
DB_PATH = DATA / "beemap.db"
WEB = Path(os.environ.get("BEEMAP_WEB", BASE.parent / "web"))

# The website is public and needs no login. Management (confirming alerts,
# approving requests, adding devices) goes through the API with this key,
# e.g. with tools/admin.py. If none is set, one is generated and stored in
# the data folder, readable only by whoever runs the server.
ADMIN_KEY = os.environ.get("BEEMAP_ADMIN_KEY", "")
WINDOW_DAYS = float(os.environ.get("BEEMAP_WINDOW_DAYS", "14"))
MAX_RANGE_M = float(os.environ.get("BEEMAP_MAX_RANGE_M", "3000"))
ONLINE_SECS = 150
# Round-trip timing rule of thumb used by beekeepers: roughly 100 m per minute
# away (out, unload, back). Rough, so it only ever gives a wide ellipse.
METRES_PER_MINUTE = float(os.environ.get("BEEMAP_M_PER_MIN", "100"))

DATA.mkdir(parents=True, exist_ok=True)
IMAGES.mkdir(parents=True, exist_ok=True)
ADMIN_KEY_FILE = DATA / "admin_key.txt"
if not ADMIN_KEY:
    if ADMIN_KEY_FILE.exists():
        ADMIN_KEY = ADMIN_KEY_FILE.read_text().strip()
    else:
        ADMIN_KEY = secrets.token_urlsafe(18)
        ADMIN_KEY_FILE.write_text(ADMIN_KEY)
        try:
            os.chmod(ADMIN_KEY_FILE, 0o600)
        except OSError:
            pass

SCHEMA = """
CREATE TABLE IF NOT EXISTS stations (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  owner TEXT DEFAULT '',
  lat REAL, lon REAL,
  heading_deg REAL DEFAULT 0,        -- compass direction of the top edge of the image
  heading_sigma_deg REAL DEFAULT 8,  -- how sure we are of that heading
  mirrored INTEGER DEFAULT 0,        -- 1 if the image is left-right flipped
  token TEXT NOT NULL,
  last_seen REAL,
  status_json TEXT DEFAULT '{}',
  created REAL,
  planned INTEGER DEFAULT 0          -- 1 = site chosen, Pi not installed yet
);
CREATE TABLE IF NOT EXISTS detections (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source TEXT NOT NULL,              -- camera | sighting
  station_id TEXT,
  ts REAL NOT NULL,                  -- when the hornet left (or was seen)
  arrived_ts REAL,
  species TEXT DEFAULT 'velutina',
  confidence REAL,
  exit_angle_img REAL,               -- clockwise from image top, as measured on the Pi
  straightness REAL,                 -- 0..1, how straight the exit path was
  bearing_deg REAL,                  -- compass bearing (derived for cameras, given for sightings)
  bearing_sigma REAL,
  lat REAL, lon REAL,                -- for sightings (cameras use the station position)
  distance_m REAL,
  track_json TEXT,
  image TEXT,
  note TEXT DEFAULT '',
  reporter TEXT DEFAULT '',
  status TEXT DEFAULT 'pending',     -- pending | confirmed | rejected
  reviewed_at REAL,
  resolved_nest_id INTEGER,          -- set once the nest it led to is removed
  event_id TEXT,                     -- the Pi's own id, so retries don't double count
  heading_source TEXT                -- heading_camera | detector | sighting | NULL (presence only)
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS nest_reports (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  lat REAL, lon REAL,                -- exact: only the city sees it; the public map rounds to ~100 m
  location TEXT,                     -- tree | building | hedge | ground | other
  height TEXT,                       -- reach | 2-5m | above-5m | unknown
  size TEXT,                         -- fist | football | bigger | unknown
  note TEXT DEFAULT '',              -- free text: city only
  image TEXT,                        -- private file in data/reports: city only
  status TEXT DEFAULT 'pending',     -- pending | asian_hornet | other_species | not_found | removed
  species TEXT DEFAULT '',           -- what it turned out to be, if not an Asian hornet
  reviewed_at REAL,
  verified_by TEXT,                  -- manual | photo (image model) | NULL while pending
  check_json TEXT                    -- photo quality gate + model result (nest_classifier.assess)
);
CREATE TABLE IF NOT EXISTS apiaries (
  id TEXT PRIMARY KEY, name TEXT, owner TEXT, lat REAL, lon REAL, colonies INTEGER DEFAULT 1,
  source TEXT DEFAULT 'register', created REAL
);
CREATE TABLE IF NOT EXISTS hive_requests (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  beekeeper TEXT, contact TEXT DEFAULT '', place TEXT, lat REAL, lon REAL, colonies INTEGER, camera INTEGER,
  note TEXT DEFAULT '', check_json TEXT, verdict TEXT,
  status TEXT DEFAULT 'pending',      -- pending | changes | approved | rejected
  message TEXT DEFAULT '', created REAL, decided REAL
);
CREATE TABLE IF NOT EXISTS sensoboxes (
  id TEXT PRIMARY KEY, name TEXT, lat REAL, lon REAL, token TEXT, created REAL, last_seen REAL
);
CREATE TABLE IF NOT EXISTS sensor_readings (
  box_id TEXT, ts REAL, temp_c REAL, humidity REAL, pressure_hpa REAL, lux REAL, uv_index REAL,
  pollen_total REAL, pollen_json TEXT
);
CREATE INDEX IF NOT EXISTS sr_box_ts ON sensor_readings(box_id, ts);
CREATE TABLE IF NOT EXISTS hive_readings (
  station_id TEXT, ts REAL, hive_temp_c REAL, hive_humidity REAL, activity REAL,
  hive TEXT DEFAULT ''               -- which colony at the station ('' = the station's only one)
);
CREATE INDEX IF NOT EXISTS hr_st_ts ON hive_readings(station_id, ts);
CREATE TABLE IF NOT EXISTS plantings (
  id INTEGER PRIMARY KEY AUTOINCREMENT, lat REAL, lon REAL, plant_id TEXT, area_m2 REAL,
  who TEXT DEFAULT '', created REAL
);
CREATE UNIQUE INDEX IF NOT EXISTS det_event ON detections(station_id, event_id);
CREATE INDEX IF NOT EXISTS det_ts ON detections(ts);
CREATE TABLE IF NOT EXISTS nests (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  lat REAL, lon REAL,
  semi_major_m REAL, semi_minor_m REAL, orientation_deg REAL,
  method TEXT, n_observers INTEGER, n_departures INTEGER,
  detection_ids TEXT DEFAULT '[]',
  status TEXT DEFAULT 'suspected',   -- suspected | assigned | removed
  assigned_to TEXT DEFAULT '',
  place TEXT DEFAULT '',
  created REAL, updated REAL, removed_at REAL
);
"""


@contextmanager
def db():
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()


with db() as c:
    c.executescript(SCHEMA)
    cols = {r[1] for r in c.execute("PRAGMA table_info(stations)")}
    if "planned" not in cols:                         # databases from v1.0
        c.execute("ALTER TABLE stations ADD COLUMN planned INTEGER DEFAULT 0")
    bcols = {r[1] for r in c.execute("PRAGMA table_info(sensoboxes)")}
    if "station_id" not in bcols:
        c.execute("ALTER TABLE sensoboxes ADD COLUMN station_id TEXT")
    rcols = {r[1] for r in c.execute("PRAGMA table_info(hive_requests)")}
    if "code" not in rcols:
        c.execute("ALTER TABLE hive_requests ADD COLUMN code TEXT")
    ncols = {r[1] for r in c.execute("PRAGMA table_info(nest_reports)")}
    for col in ("verified_by TEXT", "check_json TEXT"):
        if col.split()[0] not in ncols:
            c.execute(f"ALTER TABLE nest_reports ADD COLUMN {col}")
    dcols = {r[1] for r in c.execute("PRAGMA table_info(detections)")}
    if "heading_source" not in dcols:
        c.execute("ALTER TABLE detections ADD COLUMN heading_source TEXT")
    hcols = {r[1] for r in c.execute("PRAGMA table_info(hive_readings)")}
    if "hive" not in hcols:                           # one station can hold several colonies
        c.execute("ALTER TABLE hive_readings ADD COLUMN hive TEXT DEFAULT ''")


with db() as c:
    if not c.execute("SELECT 1 FROM apiaries LIMIT 1").fetchone():
        for a in geodata.APIARIES:
            c.execute("INSERT INTO apiaries (id,name,owner,lat,lon,colonies,source,created) VALUES (?,?,?,?,?,?,?,?)",
                      (a["id"], a["name"], a["owner"], a["lat"], a["lon"], a["colonies"], "register", time.time()))


# Manual reports by beekeepers and citizens are an optional add-on: the
# cameras measure direction on their own. Off unless switched on in Settings
# (or with BEEMAP_SIGHTINGS=on).
def get_setting(key: str, default: str) -> str:
    with db() as c:
        r = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return r[0] if r else default


def reports_enabled() -> bool:
    """Public nest reports: on unless the city switches them off."""
    return get_setting("reports", os.environ.get("BEEMAP_REPORTS", "on")) == "on"


def row(r: sqlite3.Row | None) -> dict | None:
    return dict(r) if r else None


# ------------------------------------------------------------------ live push
class Hub:
    def __init__(self):
        self.clients: set[WebSocket] = set()

    async def send(self, kind: str, data: Any = None):
        msg = json.dumps({"type": kind, "data": data})
        dead = []
        for ws in self.clients:
            try:
                await ws.send_text(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)


hub = Hub()
app = FastAPI(title="BeeMap", version="1.0")


# ------------------------------------------------------------------ auth
def require_admin(x_admin_key: str = Header(default="")):
    if not secrets.compare_digest(x_admin_key, ADMIN_KEY):
        raise HTTPException(401, "This is a management action. Use tools/admin.py with the admin key.")


# ---- light protection for the public forms (no login, so no accounts to block)
_hits: dict[str, list[float]] = {}


def rate_limit(request: Request, limit: int = 20, per_s: float = 600):
    ip = request.client.host if request.client else "?"
    now = time.time()
    h = [t for t in _hits.get(ip, []) if now - t < per_s]
    if len(h) >= limit:
        raise HTTPException(429, "Too many submissions from here. Please try again in a few minutes.")
    h.append(now)
    _hits[ip] = h


def rough(v: float | None, places: int = 3):
    """Round a coordinate to ~100 m for public output (hive locations)."""
    return None if v is None else round(v, places)


MAX_PHOTO_BYTES = 10 * 1024 * 1024


def clean_photo(raw: bytes, max_side: int = 1600) -> bytes | None:
    """Re-encode an uploaded photo: drops all metadata (phones store GPS
    position, time and device in EXIF), applies the rotation, shrinks it."""
    try:
        from PIL import Image, ImageOps
        import io
        im = Image.open(io.BytesIO(raw))
        im = ImageOps.exif_transpose(im).convert("RGB")
        im.thumbnail((max_side, max_side))
        out = io.BytesIO()
        im.save(out, "JPEG", quality=85)            # no exif= argument: nothing carried over
        return out.getvalue()
    except ImportError:
        pass
    except Exception:
        return None
    try:
        import cv2, numpy as np                       # OpenCV fallback (also drops metadata)
        img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            return None
        h, w = img.shape[:2]
        if max(h, w) > max_side:
            f = max_side / max(h, w)
            img = cv2.resize(img, (int(w * f), int(h * f)))
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])
        return buf.tobytes() if ok else None
    except Exception:
        return None


def require_station(station_id: str, x_station_token: str = Header(default="")) -> dict:
    with db() as c:
        s = row(c.execute("SELECT * FROM stations WHERE id=?", (station_id,)).fetchone())
    if not s:
        raise HTTPException(404, f"Station '{station_id}' is not registered on this server.")
    if not secrets.compare_digest(x_station_token, s["token"]):
        raise HTTPException(401, "Station token does not match. Copy it again from the Stations page.")
    return s


# ------------------------------------------------------------------ helpers
def camera_bearing(st: dict, exit_angle_img: float) -> float:
    a = -exit_angle_img if st.get("mirrored") else exit_angle_img
    return (st["heading_deg"] + a) % 360


def departure_sigma(straightness: float | None) -> float:
    """Per-departure bearing uncertainty. A clean straight exit across the
    frame is good to ~15°, a wobbly one barely tells us anything."""
    s = 0.5 if straightness is None else max(0.0, min(1.0, straightness))
    return 15 + 35 * (1 - s)


def public_station(s: dict, with_token=False) -> dict:
    out = {k: v for k, v in s.items() if k not in ("token", "status_json", "owner")}
    out["status"] = json.loads(s.get("status_json") or "{}")
    out["online"] = bool(s.get("last_seen") and time.time() - s["last_seen"] < ONLINE_SECS)
    if with_token:
        out["token"] = s["token"]
    return out


def public_detection(d: dict, stations: dict[str, dict]) -> dict:
    out = dict(d)
    out["track"] = json.loads(d["track_json"]) if d.get("track_json") else None
    out.pop("track_json", None)
    out["image_url"] = f"/images/{d['image']}" if d.get("image") else None
    if d["source"] == "camera" and d.get("station_id") in stations:
        st = stations[d["station_id"]]
        out["lat"], out["lon"] = st["lat"], st["lon"]
        out["place"] = st["name"]
    else:
        out["place"] = "Reported sighting"
        out["lat"], out["lon"] = rough(d.get("lat")), rough(d.get("lon"))
    out.pop("reporter", None)
    out.pop("note", None)
    out.pop("event_id", None)
    return out


# ----------------------------------------------------------- nest estimation
_last: dict = {"result": None, "frame": None, "stations": {}, "at": 0}
_last_lock = threading.Lock()


def _centre(stations: dict, obs: list) -> tuple[float, float]:
    pts = [(s["lat"], s["lon"]) for s in stations.values() if s["lat"] is not None] or \
          [(o.lat, o.lon) for o in obs] or [(49.1427, 9.2109)]
    return sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)


def recompute_nests() -> list[dict]:
    """Rebuild suspected nest areas from all live departures and reconcile
    them with stored nests, so a nest keeps its id (and assignment) as more
    departures come in and the estimate tightens."""
    since = time.time() - WINDOW_DAYS * 86400
    with db() as c:
        stations = {s["id"]: dict(s) for s in c.execute("SELECT * FROM stations")}
        dets = [dict(d) for d in c.execute(
            "SELECT * FROM detections WHERE status!='rejected' AND resolved_nest_id IS NULL "
            "AND bearing_deg IS NOT NULL AND ts>=?", (since,))]
        obs = []
        for d in dets:
            if d["source"] == "camera":
                st = stations.get(d["station_id"])
                if not st or st["lat"] is None:
                    continue
                obs.append(Observation(
                    observer_id=st["id"], lat=st["lat"], lon=st["lon"], bearing=d["bearing_deg"],
                    sigma=d["bearing_sigma"] or 30, weight=max(0.2, (d["straightness"] or 0.5)),
                    detection_id=d["id"], sigma_floor=max(4.0, st["heading_sigma_deg"] or 8)))
            elif d["status"] == "confirmed":
                # Anonymous reports only count once the city has confirmed them:
                # otherwise a few made-up directions could invent a nest.
                obs.append(Observation(
                    observer_id=f"sighting:{d['id']}", lat=d["lat"], lon=d["lon"],
                    bearing=d["bearing_deg"], sigma=d["bearing_sigma"] or 10,
                    distance_m=d["distance_m"], detection_id=d["id"], sigma_floor=5))
        result = estimate_nests(obs, max_range_m=MAX_RANGE_M)
        # plan in the engine's own frame: lines carry x/y in it
        frame = result.frame or LocalFrame(*(_centre(stations, obs)))
        with _last_lock:
            _last.update(result=result, frame=frame, stations=stations, at=time.time())

        existing = [dict(n) for n in c.execute("SELECT * FROM nests WHERE status!='removed'")]
        matched: set[int] = set()
        now = time.time()
        for est in result.nests:
            best, best_d = None, 1e18
            for n in existing:
                if n["id"] in matched:
                    continue
                f = LocalFrame(est.lat, est.lon)
                x, y = f.to_xy(n["lat"], n["lon"])
                dist = math.hypot(x, y)
                if dist < max(350, est.semi_major_m, n["semi_major_m"] or 0) and dist < best_d:
                    best, best_d = n, dist
            vals = (est.lat, est.lon, est.semi_major_m, est.semi_minor_m, est.orientation_deg,
                    est.method, est.n_observers, est.n_departures, json.dumps(est.detection_ids), now)
            if best:
                matched.add(best["id"])
                c.execute("UPDATE nests SET lat=?,lon=?,semi_major_m=?,semi_minor_m=?,orientation_deg=?,"
                          "method=?,n_observers=?,n_departures=?,detection_ids=?,updated=? WHERE id=?",
                          (*vals, best["id"]))
            else:
                cur = c.execute("INSERT INTO nests (lat,lon,semi_major_m,semi_minor_m,orientation_deg,method,"
                                "n_observers,n_departures,detection_ids,updated,created) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                                (*vals, now))
                matched.add(cur.lastrowid)
        # Suspected areas that no longer have support disappear. Assigned ones
        # stay (a team may already be on its way) until someone closes them.
        for n in existing:
            if n["id"] not in matched and n["status"] == "suspected":
                c.execute("DELETE FROM nests WHERE id=?", (n["id"],))
    return list_nests()


def list_nests(include_removed=False) -> list[dict]:
    with db() as c:
        q = "SELECT * FROM nests" + ("" if include_removed else " WHERE status!='removed'") + " ORDER BY id"
        nests = [dict(n) for n in c.execute(q)]
        for n in nests:
            ids = json.loads(n["detection_ids"] or "[]")
            n["detection_ids"] = ids
            if ids:
                ph = ",".join("?" * len(ids))
                n["confirmed_count"] = c.execute(
                    f"SELECT COUNT(*) FROM detections WHERE status='confirmed' AND id IN ({ph})", ids).fetchone()[0]
            else:
                n["confirmed_count"] = 0
            n["radius_m"] = math.sqrt((n["semi_major_m"] or 0) * (n["semi_minor_m"] or 0))
    return nests


def bearing_lines() -> list[dict]:
    """Every hive's mean departure direction(s), for drawing on the map.
    corridor=True means nothing crosses it yet: direction only."""
    with _last_lock:
        result = _last["result"]
    if result is None:
        return []
    unused = set(result.unused_line_keys)
    return [{"observer_id": L.observer_id, "lat": L.lat, "lon": L.lon, "bearing": L.bearing,
             "sigma": L.sigma, "n": L.n, "spread": L.spread, "key": line_key(L),
             "corridor": line_key(L) in unused and not L.distance_m} for L in result.lines]


# ---------------------------------------------------------------- planning
def _planning_inputs():
    with _last_lock:
        result, frame = _last["result"], _last["frame"]
    with db() as c:
        sts = [dict(s) for s in c.execute("SELECT * FROM stations WHERE lat IS NOT NULL")]
    if result is None or frame is None:
        return None, None, [], [], []
    existing = [frame.to_xy(s["lat"], s["lon"]) for s in sts if not s["planned"]]
    planned = [frame.to_xy(s["lat"], s["lon"]) for s in sts if s["planned"]]
    targets = planning_targets(result, frame, MAX_RANGE_M)
    loc_nests, loc_lines = _located()
    def still_open(t):
        if t.kind == "corridor":
            return t.key not in loc_lines
        est = result.nests[int(t.key.split(":")[1])]
        return _match_nest_id(est.lat, est.lon) not in loc_nests
    targets = [t for t in targets if still_open(t)]        # a found nest needs no more hives
    return result, frame, targets, existing, planned


def _r(v):
    return None if v is None or not math.isfinite(v) else round(v)


def _target_json(t, result, frame, stations_by_id, planned):
    if t.kind == "corridor":
        L = t.lines[0]
        st = stations_by_id.get(L.observer_id)
        label = st["name"] if st else "a reported sighting"
        nest_id = None
    else:
        idx = int(t.key.split(":")[1])
        est = result.nests[idx]
        label = None
        nest_id = _match_nest_id(est.lat, est.lon)
    return {
        "kind": t.kind, "key": t.key, "label": label, "nest_id": nest_id,
        "bearing": t.bearing, "n": sum(L.n for L in t.lines),
        "origin": None if not t.origin else dict(zip(("lat", "lon"), frame.to_ll(*t.origin))),
        "points": [dict(zip(("lat", "lon"), frame.to_ll(*p))) for p in t.points],
        "now_radius_m": _r(t.now_radius),
        "with_planned_radius_m": _r(baseline(t, planned)),
    }


def _match_nest_id(lat, lon):
    with db() as c:
        best, bd = None, 1e18
        for n in c.execute("SELECT id, lat, lon FROM nests WHERE status!='removed'"):
            f = LocalFrame(lat, lon)
            d = math.hypot(*f.to_xy(n["lat"], n["lon"]))
            if d < bd:
                best, bd = n["id"], d
    return best if bd < 400 else None


def planning() -> dict:
    result, frame, targets, existing, planned = _planning_inputs()
    if result is None:
        return {"targets": [], "summary": {"corridors": 0, "wide_nests": 0}}
    with db() as c:
        by_id = {s["id"]: dict(s) for s in c.execute("SELECT * FROM stations")}
    out = []
    for t in targets:
        tj = _target_json(t, result, frame, by_id, planned)
        tj["suggestions"] = []
        for sgg in suggest_sites(t, existing, planned):
            lat, lon = frame.to_ll(sgg["x"], sgg["y"])
            tj["suggestions"].append({"lat": lat, "lon": lon, "radius_m": _r(sgg["radius_m"]),
                                      "radius_low_m": _r(sgg["radius_low_m"]),
                                      "radius_high_m": _r(sgg["radius_high_m"]),
                                      "visit_chance": round(sgg["visit_chance"], 2)})
        out.append(tj)
    # most useful first: corridors with many departures, then the widest nests
    out.sort(key=lambda t: (t["kind"] != "corridor", -(t["n"] or 0), -(t["now_radius_m"] or 0)))
    return {"targets": out, "summary": {
        "corridors": sum(1 for t in out if t["kind"] == "corridor"),
        "wide_nests": sum(1 for t in out if t["kind"] == "nest"),
        "planned": len(planned), "installed": len(existing)}}


def evaluate(lat: float, lon: float) -> dict:
    result, frame, targets, existing, planned = _planning_inputs()
    if result is None:
        return {"effects": []}
    px, py = frame.to_xy(lat, lon)
    with db() as c:
        by_id = {s["id"]: dict(s) for s in c.execute("SELECT * FROM stations")}
    effects = []
    for t in targets:
        ev = evaluate_site(t, px, py, planned)
        if ev["visit_chance"] < 0.15 or not math.isfinite(ev["radius_m"]):
            continue
        tj = _target_json(t, result, frame, by_id, planned)
        before = tj["with_planned_radius_m"]
        if before is not None and ev["radius_m"] >= before * 0.95:
            continue                      # doesn't help this one
        tj.update(radius_m=_r(ev["radius_m"]), radius_low_m=_r(ev["radius_low_m"]),
                  radius_high_m=_r(ev["radius_high_m"]), visit_chance=round(ev["visit_chance"], 2))
        effects.append(tj)
    effects.sort(key=lambda e: -e["visit_chance"])
    nearest = min((math.hypot(px - x, py - y) for x, y in existing + planned), default=None)
    return {"effects": effects, "nearest_hive_m": _r(nearest)}


_push = {"task": None, "dirty": False}


async def push_state():
    """Recompute and broadcast. With many hives, alerts arrive in bursts, so
    requests are coalesced: one run in flight, at most one queued behind it."""
    _push["dirty"] = True
    if _push["task"] and not _push["task"].done():
        return
    _push["task"] = asyncio.create_task(_push_loop())


async def _push_loop():
    while _push["dirty"]:
        _push["dirty"] = False
        await asyncio.sleep(0.4)
        nests = await asyncio.to_thread(recompute_nests)
        await hub.send("nests", nests)
        await hub.send("lines", await asyncio.to_thread(bearing_lines))
        await hub.send("planning_changed", None)


# ================================================================ STATIONS
class StationIn(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,40}$")
    name: str
    owner: str = ""
    lat: float | None = None
    lon: float | None = None
    heading_deg: float = 0
    heading_sigma_deg: float = 8
    mirrored: bool = False
    planned: bool = False
    # optional fixed device token, so a real Pi's beemap.ini survives a fresh database (pitch demo)
    token: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{16,128}$")


class StationPatch(BaseModel):
    name: str | None = None
    owner: str | None = None
    lat: float | None = None
    lon: float | None = None
    heading_deg: float | None = None
    heading_sigma_deg: float | None = None
    mirrored: bool | None = None
    planned: bool | None = None


@app.get("/api/stations")
def get_stations():
    with db() as c:
        return [public_station(dict(s)) for s in c.execute("SELECT * FROM stations ORDER BY name")]


@app.post("/api/stations", dependencies=[Depends(require_admin)])
async def add_station(s: StationIn):
    token = s.token or secrets.token_urlsafe(24)
    with db() as c:
        if c.execute("SELECT 1 FROM stations WHERE id=?", (s.id,)).fetchone():
            raise HTTPException(409, f"A station called '{s.id}' already exists.")
        c.execute("INSERT INTO stations (id,name,owner,lat,lon,heading_deg,heading_sigma_deg,mirrored,token,created,planned) "
                  "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                  (s.id, s.name, s.owner, s.lat, s.lon, s.heading_deg % 360, s.heading_sigma_deg,
                   int(s.mirrored), token, time.time(), int(s.planned)))
        st = dict(c.execute("SELECT * FROM stations WHERE id=?", (s.id,)).fetchone())
    await hub.send("station", public_station(st))
    await hub.send("planning_changed", None)
    return public_station(st, with_token=True)


@app.patch("/api/stations/{station_id}", dependencies=[Depends(require_admin)])
async def patch_station(station_id: str, p: StationPatch):
    fields = {k: v for k, v in p.model_dump().items() if v is not None}
    if "heading_deg" in fields:
        fields["heading_deg"] %= 360
    if "mirrored" in fields:
        fields["mirrored"] = int(fields["mirrored"])
    if "planned" in fields:
        fields["planned"] = int(fields["planned"])
    with db() as c:
        st = row(c.execute("SELECT * FROM stations WHERE id=?", (station_id,)).fetchone())
        if not st:
            raise HTTPException(404, "No such station.")
        if fields:
            c.execute(f"UPDATE stations SET {','.join(f'{k}=?' for k in fields)} WHERE id=?",
                      (*fields.values(), station_id))
        st = dict(c.execute("SELECT * FROM stations WHERE id=?", (station_id,)).fetchone())
        # Bearings are derived from the raw on-image exit angle, so fixing a
        # station's heading after the fact corrects all its past departures.
        if {"heading_deg", "mirrored"} & fields.keys():
            for d in c.execute("SELECT id, exit_angle_img FROM detections WHERE station_id=? "
                               "AND exit_angle_img IS NOT NULL", (station_id,)).fetchall():
                c.execute("UPDATE detections SET bearing_deg=? WHERE id=?",
                          (camera_bearing(st, d["exit_angle_img"]), d["id"]))
    await hub.send("station", public_station(st))
    await push_state()
    return public_station(st)


@app.get("/api/stations/{station_id}/token", dependencies=[Depends(require_admin)])
def station_token(station_id: str):
    with db() as c:
        st = row(c.execute("SELECT * FROM stations WHERE id=?", (station_id,)).fetchone())
    if not st:
        raise HTTPException(404, "No such station.")
    return {"id": station_id, "token": st["token"]}


@app.delete("/api/stations/{station_id}", dependencies=[Depends(require_admin)])
async def delete_station(station_id: str):
    with db() as c:
        c.execute("DELETE FROM stations WHERE id=?", (station_id,))
    await hub.send("station_deleted", {"id": station_id})
    await push_state()
    return {"ok": True}


# ---- called by the Pi ------------------------------------------------------
@app.post("/api/stations/{station_id}/heartbeat")
async def heartbeat(station_id: str, status: str = Form("{}"), image: UploadFile | None = File(None),
                    st: dict = Depends(require_station)):
    try:
        status_obj = json.loads(status)
    except json.JSONDecodeError:
        status_obj = {}
    if image is not None:
        (DATA / "live").mkdir(exist_ok=True)      # not under /images: camera frames stay private
        (DATA / "live" / f"{station_id}.jpg").write_bytes(await image.read())
    with db() as c:
        c.execute("UPDATE stations SET last_seen=?, status_json=?, planned=0 WHERE id=?",
                  (time.time(), json.dumps(status_obj), station_id))
        went_live = bool(st.get("planned"))
        st = dict(c.execute("SELECT * FROM stations WHERE id=?", (station_id,)).fetchone())
    await hub.send("station", public_station(st))
    if went_live:
        await hub.send("planning_changed", None)
    # The Pi gets its calibration back so a change made in the app reaches it
    return {"ok": True, "heading_deg": st["heading_deg"], "mirrored": bool(st["mirrored"]),
            "server_time": time.time()}


@app.post("/api/stations/{station_id}/detections")
async def station_detection(station_id: str, meta: str = Form(...), image: UploadFile | None = File(None),
                            st: dict = Depends(require_station)):
    """One hornet visit. `meta` is JSON:
    {event_id, ts, arrived_ts, species, confidence, exit_angle_img, straightness, track:[[t,x,y],...]}
    x, y are normalised 0..1 image coordinates (y downwards)."""
    try:
        m = json.loads(meta)
    except json.JSONDecodeError:
        raise HTTPException(400, "meta must be JSON")
    event_id = str(m.get("event_id") or "")
    with db() as c:
        if event_id:   # the Pi retries after network drops; don't double count
            dup = c.execute("SELECT id FROM detections WHERE station_id=? AND event_id=?",
                            (station_id, event_id)).fetchone()
            if dup:
                return {"ok": True, "id": dup["id"], "duplicate": True}
    img_name = None
    if image is not None:
        img_name = f"{station_id}_{int(m.get('ts', time.time()) * 1000)}_{secrets.token_hex(3)}.jpg"
        (IMAGES / img_name).write_bytes(await image.read())
    straight = m.get("straightness")
    if m.get("heading_rel_deg") is not None:
        # two-camera station: the external heading camera measured it
        exit_angle = float(m["heading_rel_deg"])
        source = "heading_camera"
        sigma = max(3.0, min(float(m.get("heading_sigma_deg") or 10), 60.0))
    else:
        # single-camera station (or the heading camera missed it)
        exit_angle = m.get("exit_angle_img")
        source = "detector" if exit_angle is not None else None
        sigma = departure_sigma(straight)
    bearing = camera_bearing(st, exit_angle) if exit_angle is not None else None
    with db() as c:
        cur = c.execute(
            "INSERT INTO detections (source,station_id,ts,arrived_ts,species,confidence,exit_angle_img,"
            "straightness,bearing_deg,bearing_sigma,track_json,image,event_id,heading_source) VALUES "
            "('camera',?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (station_id, m.get("ts", time.time()), m.get("arrived_ts"), m.get("species", "velutina"),
             m.get("confidence"), exit_angle, straight, bearing,
             sigma if bearing is not None else None,
             json.dumps(m.get("track")) if m.get("track") else None, img_name,
             event_id or None, source))
        det = dict(c.execute("SELECT * FROM detections WHERE id=?", (cur.lastrowid,)).fetchone())
        c.execute("UPDATE stations SET last_seen=? WHERE id=?", (time.time(), station_id))
        stations = {s["id"]: dict(s) for s in c.execute("SELECT * FROM stations")}
    await hub.send("detection", public_detection(det, stations))
    if bearing is not None:
        await push_state()
    return {"ok": True, "id": det["id"], "bearing_deg": bearing}


# ============================================================== DETECTIONS
@app.get("/api/detections")
def get_detections(limit: int = 200, station_id: str | None = None, status: str | None = None):
    q, args = "SELECT * FROM detections WHERE 1=1", []
    if station_id:
        q += " AND station_id=?"; args.append(station_id)
    if status:
        q += " AND status=?"; args.append(status)
    q += " ORDER BY ts DESC LIMIT ?"; args.append(min(limit, 2000))
    with db() as c:
        stations = {s["id"]: dict(s) for s in c.execute("SELECT * FROM stations")}
        return [public_detection(dict(d), stations) for d in c.execute(q, args)]


class Review(BaseModel):
    status: str = Field(pattern="^(confirmed|rejected|pending)$")
    species: str | None = None      # e.g. "crabro" when it was a European hornet


@app.post("/api/detections/{det_id}/review", dependencies=[Depends(require_admin)])
async def review(det_id: int, r: Review):
    with db() as c:
        if not c.execute("SELECT 1 FROM detections WHERE id=?", (det_id,)).fetchone():
            raise HTTPException(404, "No such alert.")
        c.execute("UPDATE detections SET status=?, reviewed_at=? WHERE id=?", (r.status, time.time(), det_id))
        if r.species is not None:
            c.execute("UPDATE detections SET species=? WHERE id=?", (r.species, det_id))
        det = dict(c.execute("SELECT * FROM detections WHERE id=?", (det_id,)).fetchone())
        stations = {s["id"]: dict(s) for s in c.execute("SELECT * FROM stations")}
    await hub.send("detection", public_detection(det, stations))
    await push_state()
    return public_detection(det, stations)


# ---- correlating nest reports with the smart hives ------------------------------
def _matches() -> dict:
    """Which nest reports fit which triangulated nest areas or flight corridors."""
    with db() as c:
        reports = [dict(r) for r in c.execute("SELECT * FROM nest_reports")]
    return correlate.match(list_nests(), bearing_lines(), reports)


def _hive_names(nest_or_line: dict, station_names: dict) -> list[str]:
    if nest_or_line.get("kind") == "corridor":
        return [station_names.get(nest_or_line["observer_id"], "a hive")]
    with db() as c:
        ids = nest_or_line.get("detection_ids") or []
        if not ids:
            return []
        rows = c.execute(f"SELECT DISTINCT station_id FROM detections WHERE id IN ({','.join('?' * len(ids))})", ids)
        return sorted({station_names[r[0]] for r in rows if r[0] in station_names})


def _located(m: dict | None = None) -> tuple[set, set]:
    """Nest areas / corridors explained by a confirmed Asian hornet nest report."""
    m = m or _matches()
    nests = {nid for nid, ms in m["by_nest"].items()
             if any(x["status"] == "asian_hornet" and correlate.corroborates(x) for x in ms)}
    lines = {k for k, ms in m["by_line"].items()
             if any(x["status"] == "asian_hornet" and correlate.corroborates(x) for x in ms)}
    return nests, lines


# ---- nest reports from the public ------------------------------------------------
REPORTS = DATA / "reports"            # photos stay private (not under /images)
REPORTS.mkdir(exist_ok=True)
REPORT_CHOICES = {
    "location": ("tree", "building", "hedge", "ground", "other"),
    "height": ("reach", "2-5m", "above-5m", "unknown"),
    "size": ("fist", "football", "bigger", "unknown"),
}
REPORT_STATUSES = ("pending", "asian_hornet", "other_species", "not_found", "removed")


def public_report(r: dict) -> dict:
    """What anyone may see: no photo, no free text, position to ~100 m."""
    chk = json.loads(r.get("check_json") or "{}")
    return {"id": r["id"], "ts": r["ts"], "lat": rough(r["lat"]), "lon": rough(r["lon"]),
            "location": r["location"], "height": r["height"], "size": r["size"], "status": r["status"],
            "species": r["species"] if r["status"] == "other_species" else "", "reviewed_at": r["reviewed_at"],
            "has_photo": bool(r["image"]), "verified_by": r.get("verified_by"),
            "check": {"reason": chk.get("reason", ""), "p_asian": (chk.get("model") or {}).get("p_asian")} if chk else None}


def check_report(rid: int) -> dict:
    """Photo quality gate, then the image model if one is installed. Confirms
    an Asian hornet nest only when the model is sure; anything else waits for
    a person (a missed nest costs more than a visit)."""
    with db() as c:
        r = dict(c.execute("SELECT * FROM nest_reports WHERE id=?", (rid,)).fetchone())
    photos = [(REPORTS / r["image"]).read_bytes()] if r["image"] and (REPORTS / r["image"]).exists() else []
    res = nest_classifier.assess(photos)
    with db() as c:
        # Independent evidence: a photo that's fairly sure, at a spot the smart
        # hives' flight lines already point to, is enough together.
        p = (res.get("model") or {}).get("p_asian") or 0
        corr = correlate.corroborates(_matches()["by_report"].get(rid)) if res["decision"] != "asian" else False
        if r["status"] == "pending" and res["decision"] != "asian" and corr and p >= nest_classifier.AUTO_CONFIRM_CORROBORATED:
            res["decision"] = "asian"
            res["reason"] = f"Photo check ({p * 100:.0f}% sure) at a spot the smart hives' flight lines point to."
            c.execute("UPDATE nest_reports SET status='asian_hornet', verified_by='photo+flight lines', reviewed_at=?, check_json=? WHERE id=?",
                      (time.time(), json.dumps(res), rid))
        elif res["decision"] == "asian" and r["status"] == "pending":
            c.execute("UPDATE nest_reports SET status='asian_hornet', verified_by='photo', reviewed_at=?, check_json=? WHERE id=?",
                      (time.time(), json.dumps(res), rid))
        else:
            c.execute("UPDATE nest_reports SET check_json=? WHERE id=?", (json.dumps(res), rid))
    return res


@app.post("/api/nest-reports")
async def add_nest_report(request: Request, lat: float = Form(...), lon: float = Form(...),
                          location: str = Form("other"), height: str = Form("unknown"), size: str = Form("unknown"),
                          note: str = Form(""), image: UploadFile | None = File(None)):
    if not reports_enabled():
        raise HTTPException(403, "Nest reports are switched off at the moment.")
    rate_limit(request)
    w = ecology.world()
    if w.cell(lat, lon) is None:
        raise HTTPException(400, "That spot is outside Heilbronn.")
    for k, v in (("location", location), ("height", height), ("size", size)):
        if v not in REPORT_CHOICES[k]:
            raise HTTPException(400, f"{k} must be one of {', '.join(REPORT_CHOICES[k])}")
    img_name = None
    if image is not None and image.filename:
        raw = await image.read(MAX_PHOTO_BYTES + 1)
        if len(raw) > MAX_PHOTO_BYTES:
            raise HTTPException(413, "That photo is too large (10 MB at most).")
        clean = await asyncio.to_thread(clean_photo, raw)
        if clean is None:
            raise HTTPException(400, "That file doesn't look like a photo.")
        img_name = f"nest_{int(time.time() * 1000)}_{secrets.token_hex(4)}.jpg"
        (REPORTS / img_name).write_bytes(clean)
    with db() as c:
        cur = c.execute("INSERT INTO nest_reports (ts,lat,lon,location,height,size,note,image) VALUES (?,?,?,?,?,?,?,?)",
                        (time.time(), lat, lon, location, height, size, note.strip()[:500], img_name))
        rid = cur.lastrowid
    # Quality gate on the original upload (before resizing), model on the stored photo
    if img_name:
        q = await asyncio.to_thread(nest_classifier.quality, raw)
        if not q["ok"]:
            res = {"photos": [q], "model": None, "decision": "manual",
                   "reason": "Photo " + " and ".join(q["issues"]) + ": needs a check by a person."}
            with db() as c:
                c.execute("UPDATE nest_reports SET check_json=? WHERE id=?", (json.dumps(res), rid))
        else:
            await asyncio.to_thread(check_report, rid)
    else:
        with db() as c:
            c.execute("UPDATE nest_reports SET check_json=? WHERE id=?",
                      (json.dumps({"decision": "manual", "model": None, "reason": "No photo: needs a check on site."}), rid))
    with db() as c:
        r = dict(c.execute("SELECT * FROM nest_reports WHERE id=?", (rid,)).fetchone())
    await hub.send("nest_reports", None)
    return public_report(r)


def _match_json(m: dict | None, names: dict, nests: dict, lines: dict) -> dict | None:
    if not m:
        return None
    src = nests.get(m["nest_id"]) if m["kind"] == "nest" else next((l for l in lines.values() if l["key"] == m["line"]), None)
    hives = _hive_names(dict(src or {}, kind=m["kind"], observer_id=m.get("observer_id")), names) if src else []
    return {"kind": m["kind"], "level": m["level"], "dist_m": m["dist_m"], "nest_id": m.get("nest_id"),
            "hives": hives, "note": m.get("note", "")}


@app.get("/api/nest-reports")
def get_nest_reports():
    m = _matches()
    with db() as c:
        names = {s["id"]: s["name"] for s in c.execute("SELECT id, name FROM stations")}
        rows = [dict(r) for r in c.execute("SELECT * FROM nest_reports ORDER BY ts DESC LIMIT 1000")]
    nests = {n["id"]: n for n in list_nests()}
    lines = {l["key"]: l for l in bearing_lines()}
    out = []
    for r in rows:
        pr = public_report(r)
        pr["match"] = _match_json(m["by_report"].get(r["id"]), names, nests, lines)
        out.append(pr)
    return out


@app.get("/api/admin/nest-reports", dependencies=[Depends(require_admin)])
def admin_nest_reports(status: str | None = None):
    m = _matches()
    with db() as c:
        q, a = "SELECT * FROM nest_reports", []
        if status:
            q += " WHERE status=?"; a.append(status)
        out = []
        for r in c.execute(q + " ORDER BY ts DESC", a):
            r = dict(r)
            r["photo_path"] = str(REPORTS / r["image"]) if r["image"] else None
            r["check"] = json.loads(r.pop("check_json") or "null")
            r["match"] = m["by_report"].get(r["id"])
            out.append(r)
    # waiting reports that the smart hives back up first
    out.sort(key=lambda r: (r["status"] != "pending", not correlate.corroborates(r["match"]), -r["ts"]))
    return out


@app.post("/api/admin/nest-reports/recheck", dependencies=[Depends(require_admin)])
async def recheck_reports():
    """Run the photo check again on waiting reports, e.g. after installing a model."""
    with db() as c:
        ids = [r[0] for r in c.execute("SELECT id FROM nest_reports WHERE status='pending' AND image IS NOT NULL")]
    out = {}
    for rid in ids:
        out[rid] = (await asyncio.to_thread(check_report, rid))["decision"]
    await hub.send("nest_reports", None)
    ok, why = nest_classifier.available()
    return {"checked": len(ids), "confirmed": sum(v == "asian" for v in out.values()), "model": why if not ok else "ready"}


class ReportReview(BaseModel):
    status: str = Field(pattern="^(pending|asian_hornet|other_species|not_found|removed)$")
    species: str = ""


@app.patch("/api/admin/nest-reports/{rid}", dependencies=[Depends(require_admin)])
async def review_nest_report(rid: int, r: ReportReview):
    with db() as c:
        if not c.execute("SELECT 1 FROM nest_reports WHERE id=?", (rid,)).fetchone():
            raise HTTPException(404, "No such nest report.")
        before = _matches()["by_report"].get(rid)
        c.execute("UPDATE nest_reports SET status=?, species=?, reviewed_at=?, verified_by=? WHERE id=?",
                  (r.status, r.species.strip()[:80], time.time(), None if r.status == "pending" else "manual", rid))
        row_ = dict(c.execute("SELECT * FROM nest_reports WHERE id=?", (rid,)).fetchone())
    # The nest the hives were pointing at has been removed: close its area too,
    # so its departures stop counting and new sightings start a fresh search.
    if r.status == "removed" and before and before["kind"] == "nest" and correlate.corroborates(before):
        await patch_nest(before["nest_id"], NestPatch(status="removed"))
    await hub.send("nest_reports", None)
    return public_report(row_)


# ================================================================== NESTS
@app.get("/api/nests")
def get_nests(include_removed: bool = False):
    nests = list_nests(include_removed)
    m = _matches()
    with db() as c:
        reps = {r["id"]: dict(r) for r in c.execute("SELECT id, lat, lon, status FROM nest_reports")}
    for n in nests:
        ms = m["by_nest"].get(n["id"], [])
        n["reports"] = [{"report_id": x["report_id"], "level": x["level"], "dist_m": x["dist_m"], "status": x["status"]} for x in ms]
        found = next((x for x in ms if x["status"] == "asian_hornet" and correlate.corroborates(x)), None)
        n["located"] = bool(found)
        if found:
            rr = reps[found["report_id"]]
            n["located_at"] = {"lat": rough(rr["lat"]), "lon": rough(rr["lon"]), "report_id": found["report_id"]}
    return nests


@app.get("/api/lines")
def get_lines():
    return bearing_lines()


class NestPatch(BaseModel):
    status: str | None = Field(default=None, pattern="^(suspected|assigned|removed)$")
    assigned_to: str | None = None
    place: str | None = None


@app.patch("/api/nests/{nest_id}", dependencies=[Depends(require_admin)])
async def patch_nest(nest_id: int, p: NestPatch):
    with db() as c:
        n = row(c.execute("SELECT * FROM nests WHERE id=?", (nest_id,)).fetchone())
        if not n:
            raise HTTPException(404, "This nest area was merged or is gone. Refresh the map.")
        fields = {k: v for k, v in p.model_dump().items() if v is not None}
        if fields.get("status") == "removed":
            fields["removed_at"] = time.time()
            # its departures are explained now; keep them out of future estimates
            ids = json.loads(n["detection_ids"] or "[]")
            if ids:
                c.execute(f"UPDATE detections SET resolved_nest_id=? WHERE id IN ({','.join('?' * len(ids))})",
                          (nest_id, *ids))
        if fields:
            fields["updated"] = time.time()
            c.execute(f"UPDATE nests SET {','.join(f'{k}=?' for k in fields)} WHERE id=?",
                      (*fields.values(), nest_id))
    await push_state()
    return {"ok": True}


# ================================================================ PLANNING
@app.get("/api/planning")
async def get_planning():
    return await asyncio.to_thread(planning)


class SiteIn(BaseModel):
    lat: float
    lon: float


@app.post("/api/planning/evaluate")
async def post_evaluate(p: SiteIn):
    return await asyncio.to_thread(evaluate, p.lat, p.lon)


# ================================================================ SETTINGS
@app.get("/api/settings")
def get_settings():
    return {"reports": reports_enabled()}


class SettingsIn(BaseModel):
    reports: bool | None = None


@app.patch("/api/settings", dependencies=[Depends(require_admin)])
async def patch_settings(p: SettingsIn):
    if p.reports is not None:
        with db() as c:
            c.execute("INSERT INTO settings (key, value) VALUES ('reports', ?) "
                      "ON CONFLICT(key) DO UPDATE SET value=excluded.value", ("on" if p.reports else "off",))
    await hub.send("settings", get_settings())
    return get_settings()


# ================================================================ ECOLOGY
def all_apiaries() -> list[dict]:
    """Registered apiaries plus installed smart hives (each a small apiary)."""
    with db() as c:
        out = [dict(a, camera=False) for a in c.execute("SELECT * FROM apiaries")]
        for s in c.execute("SELECT * FROM stations WHERE planned=0 AND lat IS NOT NULL"):
            out.append({"id": f"station:{s['id']}", "name": s["name"], "owner": s["owner"], "lat": s["lat"],
                        "lon": s["lon"], "colonies": 2, "source": "smart hive", "camera": True})
    return out


def public_apiary(a: dict) -> dict:
    """Registered hives on a public map: no owner, position to ~100 m."""
    out = {"id": a["id"], "name": a["name"] if a.get("camera") else "Apiary", "colonies": a.get("colonies", 1),
           "camera": a.get("camera", False), "lat": a["lat"], "lon": a["lon"]}
    if not a.get("camera"):                     # smart hives are part of public monitoring
        out["lat"], out["lon"] = rough(a["lat"]), rough(a["lon"])
    return out


def public_check(chk: dict) -> dict:
    chk = dict(chk)
    chk["apiaries_near"] = [{"lat": rough(a["lat"]), "lon": rough(a["lon"]), "colonies": a.get("colonies", 1)}
                            for a in chk.get("apiaries_near", [])]
    return chk


def _hornet_context():
    nests = list_nests()
    since = time.time() - WINDOW_DAYS * 86400
    with db() as c:
        st = {s["id"]: dict(s) for s in c.execute("SELECT * FROM stations")}
        dets = []
        for d in c.execute("SELECT * FROM detections WHERE status!='rejected' AND ts>=? AND resolved_nest_id IS NULL",
                           (since,)):
            d = dict(d)
            if d["source"] == "camera" and d["station_id"] in st:
                d["lat"], d["lon"] = st[d["station_id"]]["lat"], st[d["station_id"]]["lon"]
            dets.append(d)
    return nests, dets


def _sync_plantings():
    with db() as c:
        ecology.world().set_plantings([dict(p) for p in c.execute("SELECT * FROM plantings")])


def rebuild_world():
    """New forage model from the current map data, plantings and sensors,
    swapped in whole so requests never see a half-built one."""
    w = ecology.World()
    with db() as c:
        w.set_plantings([dict(p) for p in c.execute("SELECT * FROM plantings")])
    w.set_sensors(_sensor_summaries())
    ecology._world = w


@app.get("/api/eco/status")
def eco_status():
    """Where the map and bloom data come from, and what's still being fetched."""
    ondemand.kick()
    return ondemand.status()


@app.get("/api/eco/layers")
def eco_layers():
    ondemand.kick()
    """Map layers for the citizen and beekeeper maps."""
    with db() as c:
        plantings = [{"lat": p["lat"], "lon": p["lon"], "plant_id": p["plant_id"], "area_m2": p["area_m2"], "created": p["created"]}
                     for p in c.execute("SELECT * FROM plantings ORDER BY created DESC LIMIT 500")]
    return {
        "wild_bee_zones": geodata.WILD_BEE_ZONES,
        "apiaries": [public_apiary(a) for a in all_apiaries()],
        "plantings": plantings,
        "radius_m": ecology.RADIUS_M,
        "demo_data": geodata.SOURCE["kind"] == "demo",
        "source": geodata.SOURCE,
    }


_bb_cache: dict = {}


def _bboxes():
    key = (id(geodata.LANDCOVER), id(geodata.LINES))
    if _bb_cache.get("key") != key:
        def bb(pts):
            return (min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts))
        _bb_cache.update(key=key, features=[bb([q for p in f["polys"] for q in p["outer"]]) for f in geodata.LANDCOVER],
                         lines=[bb(l["coords"]) for l in geodata.LINES])
    return _bb_cache


@app.get("/api/eco/landcover")
def eco_landcover(bbox: str | None = None):
    """Land-cover shapes for the map layer, only those in view (bbox=s,w,n,e)."""
    ondemand.kick()
    if not bbox:
        return {"source": geodata.SOURCE, "features": geodata.LANDCOVER, "lines": geodata.LINES}
    try:
        s_, w_, n_, e_ = (float(v) for v in bbox.split(","))
    except ValueError:
        raise HTTPException(400, "bbox must be south,west,north,east")
    bbs = _bboxes()
    hit = lambda b: b[0] <= n_ and b[2] >= s_ and b[1] <= e_ and b[3] >= w_
    return {"source": geodata.SOURCE,
            "features": [f for f, b in zip(geodata.LANDCOVER, bbs["features"]) if hit(b)],
            "lines": [l for l, b in zip(geodata.LINES, bbs["lines"]) if hit(b)]}


@app.get("/api/eco/place")
async def eco_place(lat: float, lon: float):
    nests, dets = await asyncio.to_thread(_hornet_context)
    near = any(ecology.nest_edge_distance(lat, lon, n) <= ecology.HORNET_WARN_M for n in nests) or \
        any(d.get("lat") is not None and ecology.dist_m((lat, lon), (d["lat"], d["lon"])) <= 1000 for d in dets)
    return {"place": ecology.place_name(lat, lon).replace("Near ", ""), "hornets_nearby": near}


@app.get("/api/eco/spot")
async def eco_spot(lat: float, lon: float, colonies: int = 2):
    nests, dets = await asyncio.to_thread(_hornet_context)
    return public_check(await asyncio.to_thread(ecology.spot_check, lat, lon, max(1, min(colonies, 50)),
                                                all_apiaries(), nests, dets))


@app.get("/api/eco/grid")
async def eco_grid(kind: str = "hive", month: int = 3):
    if kind == "gap":
        return await asyncio.to_thread(ecology.gap_grid, max(1, min(month, 12)) - 1)
    nests, _ = await asyncio.to_thread(_hornet_context)
    return await asyncio.to_thread(ecology.suitability_grid, all_apiaries(), nests)


@app.get("/api/eco/plants")
def eco_plants(lat: float, lon: float, setting: str = "garden", natives_only: bool = False):
    ondemand.kick()
    if setting not in ("balcony", "garden", "public", "hedge", "farm"):
        raise HTTPException(400, "setting must be balcony, garden, public, hedge or farm")
    with db() as c:
        planted = sum(p["area_m2"] for p in c.execute("SELECT * FROM plantings")
                      if ecology.dist_m((lat, lon), (p["lat"], p["lon"])) <= ecology.RADIUS_M)
    return ecology.plant_plan(lat, lon, setting, natives_only, planted)


@app.get("/api/eco/plant-list")
def eco_plant_list():
    return [ecology._plant_json(p, set()) for p in ecology.PLANTS]


class PlantingIn(BaseModel):
    lat: float
    lon: float
    plant_id: str
    area_m2: float = Field(gt=0, le=1_000_000)
    who: str = ""


@app.post("/api/eco/plantings")
async def add_planting(p: PlantingIn, request: Request):
    rate_limit(request)
    if p.plant_id not in ecology.PLANTS_BY_ID:
        raise HTTPException(400, "Unknown plant.")
    with db() as c:
        c.execute("INSERT INTO plantings (lat,lon,plant_id,area_m2,who,created) VALUES (?,?,?,?,?,?)",
                  (p.lat, p.lon, p.plant_id, p.area_m2, p.who, time.time()))
    await asyncio.to_thread(_sync_plantings)
    await asyncio.to_thread(_sync_sensors)
    await hub.send("eco_changed", None)
    return {"ok": True}


# ---- SensoBoxes --------------------------------------------------------------
import datetime as _dt


def _sensor_summaries() -> list[dict]:
    """Per box: latest reading, monthly bee-pollen index (last 12 months),
    whether pollen is typed, and bee flight weather for the last 7 days."""
    now = time.time()
    out = []
    with db() as c:
        boxes = [dict(b) for b in c.execute("SELECT * FROM sensoboxes ORDER BY name")]
        for b in boxes:
            rows = [dict(r) for r in c.execute("SELECT * FROM sensor_readings WHERE box_id=? AND ts>=? ORDER BY ts",
                                               (b["id"], now - 365 * 86400))]
            days: dict[str, list[float]] = {}
            typed_any = False
            for r in rows:
                v, typed = ecology.bee_pollen(json.loads(r["pollen_json"]) if r["pollen_json"] else None, r["pollen_total"])
                if v is None:
                    continue
                typed_any |= typed
                days.setdefault(_dt.datetime.fromtimestamp(r["ts"]).strftime("%Y-%m-%d"), []).append(v)
            daily = [(int(d[5:7]), sum(v) / len(v)) for d, v in days.items()]
            week = [r for r in rows if r["ts"] >= now - 7 * 86400]
            latest = rows[-1] if rows else None
            fs = ecology.flight_share(week)
            untyped_only = (not typed_any) and any(r["pollen_total"] is not None for r in rows)
            out.append({
                "station_id": b.get("station_id"),
                "untyped_only": untyped_only,
                "id": b["id"], "name": b["name"], "lat": b["lat"], "lon": b["lon"], "last_seen": b["last_seen"],
                "online": bool(b["last_seen"] and now - b["last_seen"] < 3 * 3600),
                "readings_12m": len(rows), "pollen_days": len(daily), "typed": typed_any,
                "monthly": [None if v is None else round(v, 2) for v in ecology.monthly_index(daily)],
                "flight_share_7d": None if fs is None else round(fs, 2),
                "latest": None if not latest else {k: latest[k] for k in ("ts", "temp_c", "humidity", "pressure_hpa", "lux", "uv_index", "pollen_total")}
                          | {"pollen": json.loads(latest["pollen_json"]) if latest["pollen_json"] else None},
            })
    return out


def _sync_sensors():
    ecology.world().set_sensors([s for s in _sensor_summaries()])


_sens = {"task": None, "dirty": False}


async def sensors_changed():
    """Coalesce: boxes post often; refuse to refit the map more than every few seconds."""
    _sens["dirty"] = True
    if _sens["task"] and not _sens["task"].done():
        return
    async def run():
        while _sens["dirty"]:
            _sens["dirty"] = False
            await asyncio.sleep(2)
            await asyncio.to_thread(_sync_sensors)
            await hub.send("eco_changed", None)
    _sens["task"] = asyncio.create_task(run())


class BoxIn(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,40}$")
    name: str
    lat: float
    lon: float
    station_id: str | None = None      # the smart hive this box belongs to, if any


@app.get("/api/sensoboxes")
async def get_boxes():
    return await asyncio.to_thread(_sensor_summaries)


@app.post("/api/sensoboxes", dependencies=[Depends(require_admin)])
async def add_box(b: BoxIn):
    token = secrets.token_urlsafe(24)
    with db() as c:
        if c.execute("SELECT 1 FROM sensoboxes WHERE id=?", (b.id,)).fetchone():
            raise HTTPException(409, f"A SensoBox called '{b.id}' already exists.")
        c.execute("INSERT INTO sensoboxes (id,name,lat,lon,token,created,station_id) VALUES (?,?,?,?,?,?,?)",
                  (b.id, b.name, b.lat, b.lon, token, time.time(), b.station_id))
    await hub.send("eco_changed", None)
    return {"id": b.id, "token": token}


@app.get("/api/sensoboxes/{box_id}/token", dependencies=[Depends(require_admin)])
def box_token(box_id: str):
    with db() as c:
        r = c.execute("SELECT token FROM sensoboxes WHERE id=?", (box_id,)).fetchone()
    if not r:
        raise HTTPException(404, "No such SensoBox.")
    return {"id": box_id, "token": r[0]}


@app.delete("/api/sensoboxes/{box_id}", dependencies=[Depends(require_admin)])
async def delete_box(box_id: str):
    with db() as c:
        c.execute("DELETE FROM sensoboxes WHERE id=?", (box_id,))
        c.execute("DELETE FROM sensor_readings WHERE box_id=?", (box_id,))
    await sensors_changed()
    return {"ok": True}


def _ts(v):
    if v is None:
        return time.time()
    if isinstance(v, (int, float)):
        return float(v)
    return _dt.datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()


@app.post("/api/sensoboxes/{box_id}/readings")
async def post_readings(box_id: str, payload: dict, x_box_token: str = Header(default="")):
    """One reading or {"readings": [...]}. Each reading (all fields optional):
    {"ts": unix or ISO time, "temp_c", "humidity", "pressure_hpa", "lux", "uv_index",
     "pollen_total": grains/m³, "pollen": {"salix": 12, "poaceae": 80, ...} grains/m³ by type}"""
    with db() as c:
        b = c.execute("SELECT * FROM sensoboxes WHERE id=?", (box_id,)).fetchone()
    if not b:
        raise HTTPException(404, f"SensoBox '{box_id}' is not registered.")
    if not secrets.compare_digest(x_box_token, b["token"]):
        raise HTTPException(401, "SensoBox token does not match.")
    items = payload.get("readings") if isinstance(payload.get("readings"), list) else [payload]
    if len(items) > 20000:
        raise HTTPException(413, "Send at most 20,000 readings per request.")
    rows, pollen = [], False
    for r in items:
        try:
            rows.append((box_id, _ts(r.get("ts")), r.get("temp_c"), r.get("humidity"), r.get("pressure_hpa"), r.get("lux"),
                         r.get("uv_index"), r.get("pollen_total"),
                         json.dumps({str(k).lower(): float(v) for k, v in r["pollen"].items()}) if r.get("pollen") else None))
        except (ValueError, TypeError, AttributeError) as e:
            raise HTTPException(400, f"Bad reading {r!r}: {e}")
        pollen |= r.get("pollen") is not None or r.get("pollen_total") is not None
    with db() as c:
        c.executemany("INSERT INTO sensor_readings VALUES (?,?,?,?,?,?,?,?,?)", rows)
        c.execute("UPDATE sensoboxes SET last_seen=? WHERE id=?", (time.time(), box_id))
    if pollen:
        await sensors_changed()
    else:
        await hub.send("eco_changed", None)
    return {"ok": True, "stored": len(rows)}


# ---- colony health ------------------------------------------------------------
@app.post("/api/stations/{station_id}/hive")
async def post_hive_readings(station_id: str, payload: dict, st: dict = Depends(require_station)):
    """In-hive sensors and entrance activity, one reading or {"readings": [...]}:
    {"ts": unix or ISO, "hive_temp_c": 34.8, "hive_humidity": 58, "activity": 23.5, "hive": "Beehive No.2"}
    activity = average number of bees in view at the entrance. "hive" names the colony
    when one station watches several (an apiary); leave it out for a single hive."""
    items = payload.get("readings") if isinstance(payload.get("readings"), list) else [payload]
    if len(items) > 20000:
        raise HTTPException(413, "Send at most 20,000 readings per request.")
    try:
        rows = [(station_id, _ts(r.get("ts")), r.get("hive_temp_c"), r.get("hive_humidity"), r.get("activity"),
                 str(r.get("hive") or "")[:60]) for r in items]
    except (ValueError, TypeError) as e:
        raise HTTPException(400, f"Bad reading: {e}")
    with db() as c:
        c.executemany("INSERT INTO hive_readings (station_id, ts, hive_temp_c, hive_humidity, activity, hive) "
                      "VALUES (?,?,?,?,?,?)", rows)
        c.execute("UPDATE stations SET last_seen=? WHERE id=?", (time.time(), station_id))
    await hub.send("health_changed", {"station_id": station_id})
    return {"ok": True, "stored": len(rows)}


def _hives_at(station_id: str, now: float | None = None) -> list[str]:
    """The colonies a station has recent readings for ('' = its only one)."""
    since = (now or time.time()) - (colony.WINDOW_DAYS + 1) * 86400
    with db() as c:
        hs = [r[0] or "" for r in c.execute("SELECT DISTINCT hive FROM hive_readings WHERE station_id=? AND ts>=? ORDER BY hive",
                                            (station_id, since))]
    return hs or [""]


def _health_for(st: dict, now: float | None = None, hive: str = "") -> dict:
    now = now or time.time()
    since = now - (colony.WINDOW_DAYS + 1) * 86400
    with db() as c:
        hive_rows = [dict(r) for r in c.execute("SELECT * FROM hive_readings WHERE station_id=? AND COALESCE(hive,'')=? AND ts>=?",
                                                (st["id"], hive, since))]
        boxes = [dict(b) for b in c.execute("SELECT * FROM sensoboxes")]
        box = next((b for b in boxes if b.get("station_id") == st["id"]), None)
        if not box and boxes and st["lat"] is not None:
            near = min(boxes, key=lambda b: ecology.dist_m((st["lat"], st["lon"]), (b["lat"], b["lon"])))
            box = near if ecology.dist_m((st["lat"], st["lon"]), (near["lat"], near["lon"])) <= 3000 else None
        outside = [dict(r) for r in c.execute("SELECT ts,temp_c,humidity,lux FROM sensor_readings WHERE box_id=? AND ts>=?",
                                              (box["id"], since))] if box else []
        hornets = [r[0] for r in c.execute("SELECT ts FROM detections WHERE station_id=? AND status!='rejected' AND ts>=?",
                                           (st["id"], since))]
    forage = None
    w = ecology.world()
    cell = w.cell(st["lat"], st["lon"]) if st["lat"] is not None else None
    if cell:
        forage = float(w.ratio[cell[0], cell[1], datetime_month(now) - 1])
    res = colony.analyse(hive_rows, outside, hornets, forage, now)
    res.update(station_id=st["id"], name=f"{st['name']} · {hive}" if hive else st["name"], hive=hive,
               weather_from=None if not box else {"id": box["id"], "name": box["name"], "own": box.get("station_id") == st["id"]})
    return res


def datetime_month(ts):
    return _dt.datetime.fromtimestamp(ts).month


# Colony health is the beekeepers' own data: admin only (tools/admin.py health)
@app.get("/api/admin/health", dependencies=[Depends(require_admin)])
async def get_health_all():
    def run():
        with db() as c:
            sts = [dict(s) for s in c.execute("SELECT * FROM stations WHERE planned=0 ORDER BY name")]
        out = []
        for s in sts:
            for hive in _hives_at(s["id"]):
                r = _health_for(s, hive=hive)
                r.pop("series", None)
                out.append(r)
        return out
    return await asyncio.to_thread(run)


@app.get("/api/admin/health/{station_id}", dependencies=[Depends(require_admin)])
async def get_health_one(station_id: str, hive: str | None = None):
    """One colony's health. A station with several colonies: pass ?hive=<name>,
    otherwise the first one is returned and "hives" lists them all."""
    with db() as c:
        s = c.execute("SELECT * FROM stations WHERE id=?", (station_id,)).fetchone()
    if not s:
        raise HTTPException(404, "No such hive.")
    hives = _hives_at(station_id)
    if hive is not None and hive not in hives:
        raise HTTPException(404, f"No recent readings for colony '{hive}'. Colonies here: {hives}")
    res = await asyncio.to_thread(_health_for, dict(s), None, hive if hive is not None else hives[0])
    res["hives"] = hives
    return res


# ================================================================ CITY DATA (pipeline results)
def _city(name: str, value):
    if value is None:
        raise HTTPException(404, f"No '{name}' data yet. Run the pipeline step that makes it (see pipeline/Makefile).")
    return value


@app.get("/api/city/status")
def city_status():
    return citydata.status()


@app.get("/api/city/balance")
def city_balance():
    """250 m Bee Balance squares: food within 1.5 km / 300 m and the verdict."""
    return _city("balance", citydata.balance())


@app.get("/api/city/places")
def city_places():
    """Schools and neighbourhoods with their square's Bee Balance verdict."""
    return {"schools": citydata.schools() or [], "neighbourhoods": citydata.neighbourhoods() or []}


@app.get("/api/city/sightings")
def city_sightings():
    """Public GBIF records of Asian hornet, European hornet and honey bee around Heilbronn."""
    return _city("sightings", citydata.sightings())


@app.get("/api/city/heat")
def city_heat():
    """Street heat from the summer-school walks (°C above or below each walk's median)."""
    return _city("heat", citydata.heat())


@app.get("/api/city/flight-hours")
def city_flight_hours():
    return _city("flight", citydata.flight())


@app.get("/api/city/study")
def city_study():
    """What the OpenSpace hive camera measured: bee traffic vs weather."""
    return _city("study", citydata.study())


# ================================================================ SUMMARY
@app.get("/api/summary")
def summary():
    day = time.time() - 86400
    with db() as c:
        return {
            "to_confirm": c.execute("SELECT COUNT(*) FROM detections WHERE status='pending'").fetchone()[0],
            "today": c.execute("SELECT COUNT(*) FROM detections WHERE ts>=? AND status!='rejected'", (day,)).fetchone()[0],
            "nest_areas": c.execute("SELECT COUNT(*) FROM nests WHERE status!='removed'").fetchone()[0],
            "removed": c.execute("SELECT COUNT(*) FROM nests WHERE status='removed'").fetchone()[0],
            "stations_online": sum(1 for s in c.execute("SELECT last_seen FROM stations WHERE planned=0")
                                   if s[0] and time.time() - s[0] < ONLINE_SECS),
            "stations": c.execute("SELECT COUNT(*) FROM stations WHERE planned=0").fetchone()[0],
            "planned": c.execute("SELECT COUNT(*) FROM stations WHERE planned=1").fetchone()[0],
            "window_days": WINDOW_DAYS,
            "auth_required": bool(ADMIN_KEY),
            "carto_key": os.environ.get("BEEMAP_CARTO_KEY", ""),   # optional: CARTO basemaps need a free key
            "reports_enabled": reports_enabled(),
            "nest_reports_pending": c.execute("SELECT COUNT(*) FROM nest_reports WHERE status='pending'").fetchone()[0],
            "plantings_m2": round(c.execute("SELECT COALESCE(SUM(area_m2),0) FROM plantings").fetchone()[0]),
            "sensoboxes": c.execute("SELECT COUNT(*) FROM sensoboxes").fetchone()[0],
        }


@app.get("/api/health")
def health():
    return {"ok": True}


# ================================================================ LIVE
@app.websocket("/ws")
async def ws(websocket: WebSocket):
    await websocket.accept()
    hub.clients.add(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        hub.clients.discard(websocket)


async def presence_loop():
    """Stations that stop sending heartbeats should visibly go offline."""
    last: dict[str, bool] = {}
    while True:
        await asyncio.sleep(30)
        for s in get_stations():
            if last.get(s["id"]) != s["online"]:
                last[s["id"]] = s["online"]
                await hub.send("station", s)


@app.on_event("startup")
async def _startup():
    loop = asyncio.get_running_loop()
    ondemand.rebuild = rebuild_world
    ondemand.on_change = lambda kind: asyncio.run_coroutine_threadsafe(hub.send("eco_changed", {"source": kind}), loop)
    ondemand.kick()                      # fetch OpenStreetMap / iNaturalist in the background if needed
    ecology.load_bloom()
    await asyncio.to_thread(_sync_plantings)
    await asyncio.to_thread(recompute_nests)
    asyncio.create_task(presence_loop())


# ================================================================ STATIC
app.mount("/images", StaticFiles(directory=IMAGES), name="images")
if WEB.exists():
    @app.get("/")
    def index():
        return FileResponse(WEB / "index.html")
    app.mount("/", StaticFiles(directory=WEB), name="web")
