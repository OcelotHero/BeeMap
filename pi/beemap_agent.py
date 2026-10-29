#!/usr/bin/env python3
"""
BeeMap agent for a Raspberry Pi VespAI station.

What it does
  1. Reads frames from the camera above the bait station.
  2. Runs the VespAI YOLOv5 model to find Vespa velutina.
  3. Tracks each hornet from frame to frame.
  4. When a hornet leaves, works out the direction it left the frame
     (the "exit angle", clockwise from the top edge of the image) and how
     straight that exit was.
  5. Sends the visit to the BeeMap server with the best snapshot and the
     visit's median confidence. The server confirms or dismisses it by that
     confidence, or asks for a short clip of the visit for a person to check.
     If the network is down, the visit (and its clip) is queued on the SD card
     and sent later.
  6. Sends a heartbeat (and a live frame for aiming the camera) every minute.

The server turns the exit angle into a compass bearing using the station's
heading, which you set in the BeeMap app. So if the camera gets knocked,
re-measure the heading in the app and all past bearings are corrected.

Run modes
  python3 beemap_agent.py --config beemap.ini              # camera + VespAI model
  python3 beemap_agent.py --config beemap.ini --simulate   # no camera, fake hornets
  python3 beemap_agent.py --config beemap.ini --test       # check server + token, then exit

Already running VespAI's own script? Import the hook instead, see README:
  from beemap_agent import BeeMapHook
"""
from __future__ import annotations

import argparse
import collections
import configparser
import json
import logging
import math
import os
import random
import statistics
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import requests

log = logging.getLogger("beemap")


# =============================================================== config
@dataclass
class Config:
    server: str
    station_id: str
    token: str
    weights: str = "models/vespai.pt"
    yolov5_dir: str = ""               # local ultralytics/yolov5 checkout (VespAI needs commit 23701eac); empty = GitHub
    target_class: str = "velutina"     # matched against model.names, case-insensitive
    min_confidence: float = 0.6
    camera: str = "picamera2"          # picamera2 | opencv:<index> | file:<path>
    width: int = 1280
    height: int = 720
    fps: float = 10.0
    rotate: int = 0                    # detection camera: rotate clockwise 0 | 90 | 180 | 270 before anything else
    autofocus: bool = True             # Camera Module 3: continuous autofocus (ignored by cameras without it)
    stream_port: int = 0               # >0: live view with boxes at http://<pi>:<port>/ for aiming the camera
    lost_after_s: float = 0.6          # hornet gone this long = it has left
    min_track_points: int = 4
    max_jump: float = 0.25             # max move between frames, as a fraction of the frame
    heartbeat_s: float = 60.0
    send_live_frame: bool = True
    queue_dir: str = "queue"
    clip_s: float = 8.0                # video kept of the last seconds of a visit, for a manual check; 0 = none
    clip_width: int = 640              # clip frame width in pixels
    clip_queue_max: int = 50           # clips kept on the SD card while the server is unreachable
    # --- external heading camera (follows the hornet as it flies off) ---
    heading_camera: str = "none"       # picamera2:1 | opencv:1 | file:<path> | none (single-camera setup)
    heading_mode: str = "sky"          # sky = fisheye looking straight up | horizon = looking outward
    heading_hfov: float = 120.0        # horizon mode: horizontal field of view, degrees
    heading_flip: bool = False         # set if the heading image is mirrored
    heading_width: int = 1280
    heading_height: int = 720
    heading_fps: float = 30.0
    match_window_s: float = 3.0        # heading track must start within this long after the hornet leaves
    min_blob_px: int = 6               # motion blobs smaller/larger than this are not hornets
    max_blob_px: int = 6000
    # --- colony health: in-hive sensor + entrance activity ---
    hive_sensor: str = "none"          # none | bme280 | sht31 | dht22
    hive_sensor_pin: str = "D4"        # dht22 only: board pin name
    hive_sensor_addr: int = 0x76       # bme280 only: I2C address (0x76 or 0x77)
    entrance_roi: str = "0,0.55,1,1"   # x0,y0,x1,y1 of the entrance in the detection image (0..1)
    bee_blob_min_px: int = 20
    bee_blob_max_px: int = 3000
    hive_post_s: float = 300.0
    sim_nest_bearing: float = 135.0    # simulate mode: true direction home (compass)
    sim_heading: float = 0.0           # simulate mode: camera heading set in the app
    sim_rate_per_min: float = 2.0

    @classmethod
    def load(cls, path: str) -> "Config":
        cp = configparser.ConfigParser()
        if not cp.read(path):
            raise SystemExit(f"Config file not found: {path}. Copy beemap.example.ini to {path} and fill it in.")
        g = cp["beemap"]
        kw = {}
        for f in cls.__dataclass_fields__.values():
            if f.name in g:
                raw = g[f.name]
                kw[f.name] = (raw.lower() in ("1", "true", "yes")) if f.type == "bool" else \
                    float(raw) if f.type == "float" else int(raw, 0) if f.type == "int" else raw
        missing = [k for k in ("server", "station_id", "token") if not kw.get(k)]
        if missing:
            raise SystemExit(f"Missing in [beemap]: {', '.join(missing)}")
        kw["server"] = kw["server"].rstrip("/")
        return cls(**kw)


# =============================================================== tracking
@dataclass
class Det:
    x: float          # box centre, 0..1 of frame width
    y: float          # box centre, 0..1 of frame height (downwards)
    w: float
    h: float
    conf: float


@dataclass
class Track:
    tid: str
    points: list = field(default_factory=list)    # (t, x, y)
    best_conf: float = 0.0
    best_frame: object = None
    best_box: Det | None = None
    last_t: float = 0.0
    confs: list = field(default_factory=list)     # every box's confidence: the median sorts the visit
    clip: list = field(default_factory=list)      # (t, jpeg) frames of the visit's last seconds


def exit_vector(points: list[tuple[float, float, float]]) -> tuple[float | None, float]:
    """Direction a hornet left in, from the tail of its track.

    Hornets hover and loop over the bait before committing, so only the last
    stretch of the track counts: the points from the last ~0.8 s, or the last
    6, whichever is more. Direction = least-squares fit of position over time
    (robust to one jittery box). Straightness = net displacement / path length
    over that stretch: 1.0 for a clean dash, near 0 for a loop.

    Returns (angle clockwise from image top in degrees, straightness 0..1)."""
    if len(points) < 3:
        return None, 0.0
    t_end = points[-1][0]
    tail = [p for p in points if t_end - p[0] <= 0.8]
    if len(tail) < 6:
        tail = points[-6:]
    if len(tail) < 3:
        return None, 0.0
    ts = [p[0] for p in tail]
    tm = sum(ts) / len(ts)
    var = sum((t - tm) ** 2 for t in ts) or 1e-9
    vx = sum((t - tm) * p[1] for t, p in zip(ts, tail)) / var
    vy = sum((t - tm) * p[2] for t, p in zip(ts, tail)) / var
    if math.hypot(vx, vy) < 1e-4:
        return None, 0.0
    # image y grows downward; "up" in the image = towards the heading
    angle = math.degrees(math.atan2(vx, -vy)) % 360
    path = sum(math.hypot(b[1] - a[1], b[2] - a[2]) for a, b in zip(tail, tail[1:]))
    net = math.hypot(tail[-1][1] - tail[0][1], tail[-1][2] - tail[0][2])
    straight = 0.0 if path < 1e-6 else min(1.0, net / path)
    # a track that ends mid-frame (it flew up, out of focus) is less telling
    # than one that crosses the frame edge
    lx, ly = tail[-1][1], tail[-1][2]
    near_edge = min(lx, ly, 1 - lx, 1 - ly) < 0.12
    if not near_edge:
        straight *= 0.7
    return angle, straight


class Tracker:
    """Nearest-neighbour tracker. A bait station rarely has more than a few
    hornets at once, so nothing fancier is needed."""

    def __init__(self, cfg: Config, on_departure):
        self.cfg = cfg
        self.on_departure = on_departure
        self.tracks: dict[str, Track] = {}

    def update(self, t: float, dets: list[Det], frame=None):
        free = dict(self.tracks)
        for d in sorted(dets, key=lambda d: -d.conf):
            best, best_dist = None, self.cfg.max_jump
            for tr in free.values():
                _, px, py = tr.points[-1]
                dist = math.hypot(d.x - px, d.y - py)
                if dist < best_dist:
                    best, best_dist = tr, dist
            if best is None:
                best = Track(tid=uuid.uuid4().hex[:12])
                self.tracks[best.tid] = best
            else:
                free.pop(best.tid)
            best.points.append((t, d.x, d.y))
            best.confs.append(d.conf)
            best.last_t = t
            if d.conf > best.best_conf:
                best.best_conf, best.best_box = d.conf, d
                best.best_frame = frame.copy() if frame is not None and hasattr(frame, "copy") else frame
        for tid, tr in list(self.tracks.items()):
            if t - tr.last_t > self.cfg.lost_after_s:
                del self.tracks[tid]
                if len(tr.points) >= self.cfg.min_track_points:
                    self.on_departure(tr)
                else:
                    log.debug("dropped short track %s (%d points)", tid, len(tr.points))


# =============================================================== heading camera
class _HCfg:
    """Tracker settings for the heading camera: fast, small, short-lived blobs."""
    max_jump = 0.35
    lost_after_s = 0.35
    min_track_points = 3


class HeadingCamera:
    """The external camera that measures which way the hornet flies off.

    It doesn't need to recognise species: the detection camera already did.
    It just tracks small moving things (background subtraction), and the
    detection camera's "hornet left" event says which track to trust.

    sky mode:     fisheye looking straight up, image top facing the heading
                  set in the app. The direction the hornet crosses the image
                  is its heading. Looking up mirrors the world, which is
                  undone here.
    horizon mode: camera looking outward, image centre facing the heading set
                  in the app. A hornet flying away shrinks towards one point;
                  its horizontal position gives the bearing. More precise,
                  but only sees directions within its field of view."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.tracker = Tracker(_HCfg, self._finished)
        self.finished: list[dict] = []
        self.frames = 0
        self.fps = 0.0
        self._bg = None
        self.lock = threading.Lock()

    def blobs(self, frame) -> list[Det]:
        import cv2
        if self._bg is None:
            self._bg = cv2.createBackgroundSubtractorMOG2(history=300, varThreshold=32, detectShadows=False)
        mask = self._bg.apply(frame)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)))
        n, _, stats, cents = cv2.connectedComponentsWithStats(mask)
        h, w = mask.shape[:2]
        out = []
        for i in range(1, n):
            area = stats[i, cv2.CC_STAT_AREA]
            if self.cfg.min_blob_px <= area <= self.cfg.max_blob_px:
                out.append(Det(cents[i][0] / w, cents[i][1] / h, stats[i, 2] / w, stats[i, 3] / h, 1.0))
        return out

    def update(self, t: float, dets: list[Det]):
        self.frames += 1
        with self.lock:
            self.tracker.update(t, dets, None)

    def _finished(self, tr: Track):
        rel, sigma = self.heading(tr.points)
        if rel is not None:
            self.finished.append({"t0": tr.points[0][0], "t1": tr.points[-1][0], "rel": rel, "sigma": sigma,
                                  "n": len(tr.points)})
            self.finished = self.finished[-50:]

    def heading(self, points) -> tuple[float | None, float]:
        """Direction relative to the camera's reference (clockwise, degrees)
        and its uncertainty."""
        if self.cfg.heading_mode == "horizon":
            tail = points[-4:]
            xs = [p[1] for p in tail]
            x = sum(xs) / len(xs)
            half = math.radians(self.cfg.heading_hfov / 2)
            rel = math.degrees(math.atan((x - 0.5) * 2 * math.tan(half)))
            wobble = (max(xs) - min(xs)) * self.cfg.heading_hfov
            sigma = min(20.0, 3.0 + wobble)
        else:
            angle, straight = exit_vector(points)
            if angle is None:
                return None, 0.0
            rel = -angle                       # looking up: image is mirrored vs the map
            sigma = 6.0 + 20.0 * (1.0 - straight)
        if self.cfg.heading_flip:
            rel = -rel
        return (rel % 360 if self.cfg.heading_mode != "horizon" else rel), sigma

    def take(self, t_leave: float, window: float) -> dict | None:
        """The heading track that started as the hornet left the detection camera."""
        with self.lock:
            cands = [h for h in self.finished if t_leave - 1.0 <= h["t0"] <= t_leave + window]
            if not cands:
                return None
            best = min(cands, key=lambda h: abs(h["t0"] - t_leave))
            self.finished.remove(best)
            return best

    def latest_time(self) -> float:
        return max((h["t1"] for h in self.finished), default=0.0)


# =============================================================== colony health
class HiveMonitor:
    """In-hive temperature/humidity and entrance activity.

    Activity = average number of bees in view in the entrance area, counted
    as moving blobs (background subtraction) on the detection camera. It is a
    relative measure: the server compares each colony with itself under
    similar weather, so camera angle and zoom don't matter as long as they
    stay put."""

    def __init__(self, cfg: Config, up: "Uploader"):
        self.cfg, self.up = cfg, up
        self.counts: list[int] = []
        self.unsent: list[dict] = []
        self._bg = None
        self._sensor = None
        x0, y0, x1, y1 = (float(v) for v in cfg.entrance_roi.split(","))
        self.roi = (x0, y0, x1, y1)
        self.last_post = time.time()
        self.last_reading: dict | None = None

    # ---- sensor -----------------------------------------------------------
    def read_sensor(self) -> tuple[float | None, float | None]:
        kind = self.cfg.hive_sensor
        if kind == "none":
            return None, None
        try:
            if kind == "bme280":
                import bme280, smbus2   # pip install RPi.bme280 smbus2
                if self._sensor is None:
                    bus = smbus2.SMBus(1)
                    self._sensor = (bus, bme280.load_calibration_params(bus, self.cfg.hive_sensor_addr))
                d = bme280.sample(self._sensor[0], self.cfg.hive_sensor_addr, self._sensor[1])
                return d.temperature, d.humidity
            if kind == "sht31":
                import adafruit_sht31d, board   # pip install adafruit-circuitpython-sht31d
                self._sensor = self._sensor or adafruit_sht31d.SHT31D(board.I2C())
                return self._sensor.temperature, self._sensor.relative_humidity
            if kind == "dht22":
                import adafruit_dht, board      # pip install adafruit-circuitpython-dht
                self._sensor = self._sensor or adafruit_dht.DHT22(getattr(board, self.cfg.hive_sensor_pin))
                for _ in range(3):              # DHT sensors often miss a read
                    try:
                        return self._sensor.temperature, self._sensor.humidity
                    except RuntimeError:
                        time.sleep(2)
        except Exception as e:
            log.warning("hive sensor (%s) read failed: %s", kind, e)
        return None, None

    # ---- activity -----------------------------------------------------------
    def count(self, frame):
        try:
            import cv2
        except ImportError:
            return
        h, w = frame.shape[:2]
        x0, y0, x1, y1 = self.roi
        crop = frame[int(y0 * h):int(y1 * h), int(x0 * w):int(x1 * w)]
        if self._bg is None:
            self._bg = cv2.createBackgroundSubtractorMOG2(history=500, varThreshold=25, detectShadows=False)
        mask = self._bg.apply(crop)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)))
        n, _, stats, _ = cv2.connectedComponentsWithStats(mask)
        self.counts.append(sum(1 for i in range(1, n)
                               if self.cfg.bee_blob_min_px <= stats[i, cv2.CC_STAT_AREA] <= self.cfg.bee_blob_max_px))
        self.maybe_post()

    def add_count(self, n: int):
        """For the simulator or another counter (e.g. a gate counter)."""
        self.counts.append(n)

    def maybe_post(self, now: float | None = None, force: bool = False):
        now = now or time.time()
        if not force and now - self.last_post < self.cfg.hive_post_s:
            return
        t, rh = self.read_sensor()
        r = {"ts": now}
        if self.counts:
            r["activity"] = round(sum(self.counts) / len(self.counts), 2)
        if t is not None:
            r["hive_temp_c"] = round(t, 2)
        if rh is not None:
            r["hive_humidity"] = round(rh, 1)
        self.counts, self.last_post = [], now
        if len(r) > 1:
            self.last_reading = r
            self.unsent.append(r)
        if self.unsent and self.up.post_hive(self.unsent):
            self.unsent = []
        self.unsent = self.unsent[-5000:]             # keep at most ~2 weeks offline


# =============================================================== uploading
class Uploader:
    """Sends visits to the server. Anything that fails is written to the
    queue folder and retried in the background, oldest first."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.q = Path(cfg.queue_dir)
        self.q.mkdir(parents=True, exist_ok=True)
        self.session = requests.Session()
        self.session.headers["X-Station-Token"] = cfg.token
        self.calibration = {"heading_deg": None, "mirrored": False}
        self.last_ok: float | None = None
        threading.Thread(target=self._retry_loop, daemon=True).start()

    def url(self, path: str) -> str:
        return f"{self.cfg.server}/api/stations/{self.cfg.station_id}/{path}"

    def send_visit(self, meta: dict, jpeg: bytes | None, clip_frames: list | None = None):
        reply = self._post_visit(meta, jpeg)
        if reply is None:
            base = self.q / f"{int(meta['ts'] * 1000)}_{meta['event_id']}"
            if jpeg:
                base.with_suffix(".jpg").write_bytes(jpeg)
            clip = make_clip(clip_frames) if clip_frames else None
            if clip:                                  # the server may want it once it has the visit
                base.with_suffix("." + clip[1]).write_bytes(clip[0])
            base.with_suffix(".json").write_text(json.dumps(meta))
            log.warning("server unreachable, queued visit %s (%d waiting)", meta["event_id"], self.queued())
        elif reply.get("clip_wanted") and clip_frames:
            clip = make_clip(clip_frames)
            if clip:
                self._send_clip(meta["event_id"], *clip)

    def _post_visit(self, meta: dict, jpeg: bytes | None) -> dict | None:
        """The server's reply, or None if the visit has to wait in the queue."""
        files = {"image": ("hornet.jpg", jpeg, "image/jpeg")} if jpeg else None
        try:
            r = self.session.post(self.url("detections"), data={"meta": json.dumps(meta)},
                                  files=files, timeout=15)
        except requests.RequestException as e:
            log.debug("post failed: %s", e)
            return None
        if r.status_code in (401, 404):
            log.error("server refused this station (%s): %s", r.status_code, r.text[:200])
            return None
        if not r.ok:
            return None
        self.last_ok = time.time()
        reply = r.json()
        b = reply.get("bearing_deg")
        log.info("sent visit %s -> bearing %s, %s", meta["event_id"], f"{b:.0f}°" if b is not None else "n/a",
                 {"confirmed": "confirmed", "rejected": "dismissed"}.get(reply.get("status"), "to be checked"))
        return reply

    def _send_clip(self, event_id: str, data: bytes, ext: str):
        """Upload a visit's clip; if that fails, keep it for the retry loop."""
        if self._post_clip(event_id, data, ext):
            return
        waiting = self.q / "clips"
        waiting.mkdir(exist_ok=True)
        (waiting / f"{event_id}.{ext}").write_bytes(data)
        old = sorted(waiting.iterdir(), key=lambda f: f.stat().st_mtime)
        for f in old[:max(0, len(old) - self.cfg.clip_queue_max)]:
            f.unlink(missing_ok=True)

    def _post_clip(self, event_id: str, data: bytes, ext: str) -> bool:
        kind = "video/mp4" if ext == "mp4" else "video/x-msvideo"
        try:
            r = self.session.post(self.url(f"detections/{event_id}/clip"),
                                  files={"clip": (f"{event_id}.{ext}", data, kind)}, timeout=60)
        except requests.RequestException as e:
            log.debug("clip not sent: %s", e)
            return False
        if r.ok:
            log.info("sent clip of visit %s for a manual check (%d KB)", event_id, len(data) // 1024)
        elif r.status_code in (404, 413, 415):         # the server won't take it; don't retry forever
            log.error("clip of visit %s refused (%s): %s", event_id, r.status_code, r.text[:200])
            return True
        return r.ok

    def queued(self) -> int:
        return len(list(self.q.glob("*.json")))

    def clips_waiting(self) -> int:
        d = self.q / "clips"
        return len(list(d.iterdir())) if d.exists() else 0

    def _retry_loop(self):
        delay = 10
        while True:
            time.sleep(delay)
            pending = sorted(self.q.glob("*.json"))
            clips = sorted((self.q / "clips").glob("*.*")) if (self.q / "clips").exists() else []
            if not pending and not clips:
                delay = 10
                continue
            for meta_path in pending:
                meta = json.loads(meta_path.read_text())
                jpg = meta_path.with_suffix(".jpg")
                reply = self._post_visit(meta, jpg.read_bytes() if jpg.exists() else None)
                if reply is None:
                    delay = min(delay * 2, 300)
                    break
                for clip in (meta_path.with_suffix(".mp4"), meta_path.with_suffix(".avi")):
                    if clip.exists():
                        if reply.get("clip_wanted"):
                            self._send_clip(meta["event_id"], clip.read_bytes(), clip.suffix[1:])
                        clip.unlink(missing_ok=True)
                meta_path.unlink(missing_ok=True)
                jpg.unlink(missing_ok=True)
                delay = 10
            else:
                for clip in clips:                         # asked for, but the upload failed earlier
                    if self._post_clip(clip.stem, clip.read_bytes(), clip.suffix[1:]):
                        clip.unlink(missing_ok=True)
                    else:
                        delay = min(delay * 2, 300)
                        break

    def post_hive(self, readings: list[dict]) -> bool:
        try:
            r = self.session.post(self.url("hive"), json={"readings": readings}, timeout=20)
            if r.ok:
                self.last_ok = time.time()
                return True
            log.error("hive readings refused (%s): %s", r.status_code, r.text[:200])
        except requests.RequestException as e:
            log.debug("hive readings not sent: %s", e)
        return False

    def heartbeat(self, status: dict, jpeg: bytes | None) -> bool:
        files = {"image": ("live.jpg", jpeg, "image/jpeg")} if jpeg else None
        try:
            r = self.session.post(self.url("heartbeat"), data={"status": json.dumps(status)},
                                  files=files, timeout=15)
            if r.ok:
                self.calibration = r.json()
                self.last_ok = time.time()
                return True
            log.error("heartbeat refused (%s): %s", r.status_code, r.text[:200])
        except requests.RequestException as e:
            log.warning("heartbeat failed: %s", e)
        return False


# =============================================================== reporting
def encode_jpeg(frame, box: Det | None = None, label: str = "") -> bytes | None:
    try:
        import cv2
    except ImportError:
        return None
    if frame is None:
        return None
    img = frame.copy()
    if box is not None:
        h, w = img.shape[:2]
        x0, y0 = int((box.x - box.w / 2) * w), int((box.y - box.h / 2) * h)
        x1, y1 = int((box.x + box.w / 2) * w), int((box.y + box.h / 2) * h)
        cv2.rectangle(img, (x0, y0), (x1, y1), (48, 194, 242), 3)        # BeeMap yellow (BGR)
        if label:
            cv2.putText(img, label, (x0, max(18, y0 - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (48, 194, 242), 2)
    if img.shape[1] > 1280:
        s = 1280 / img.shape[1]
        img = cv2.resize(img, (1280, int(img.shape[0] * s)))
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 82])
    return buf.tobytes() if ok else None


def draw_track(frame, points):
    try:
        import cv2
    except ImportError:
        return frame
    if frame is None or len(points) < 2:
        return frame
    h, w = frame.shape[:2]
    pts = [(int(p[1] * w), int(p[2] * h)) for p in points]
    for a, b in zip(pts, pts[1:]):
        cv2.line(frame, a, b, (27, 72, 194), 2)                           # hornet orange
    cv2.arrowedLine(frame, pts[-2], pts[-1], (27, 72, 194), 3, tipLength=0.5)
    return frame


def clip_frame(frame, boxes: list, cfg: Config, t: float) -> bytes | None:
    """One small JPEG for the visit clip, with the boxes the tracker used and the time."""
    try:
        import cv2
    except ImportError:
        return None
    img = frame.copy()
    for x1, y1, x2, y2, conf, cls in boxes:
        if conf >= cfg.min_confidence and cfg.target_class.lower() in str(cls).lower():
            cv2.rectangle(img, (int(x1), int(y1)), (int(x2), int(y2)), (48, 194, 242), 2)
            cv2.putText(img, f"{conf:.2f}", (int(x1), max(14, int(y1) - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                        (48, 194, 242), 1)
    if img.shape[1] > cfg.clip_width:
        img = cv2.resize(img, (cfg.clip_width, int(img.shape[0] * cfg.clip_width / img.shape[1])))
    cv2.putText(img, time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t)), (8, img.shape[0] - 8),
                cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 75])
    return buf.tobytes() if ok else None


def make_clip(frames: list) -> tuple[bytes, str] | None:
    """(t, jpeg) frames -> (video bytes, "mp4" | "avi"), played at the speed they were taken.
    MP4 (mp4v) where OpenCV can write it, otherwise Motion-JPEG AVI."""
    try:
        import cv2
        import numpy as np
    except ImportError:
        return None
    imgs = [cv2.imdecode(np.frombuffer(j, np.uint8), cv2.IMREAD_COLOR) for _, j in frames]
    imgs = [i for i in imgs if i is not None]
    if len(imgs) < 2:
        return None
    span = frames[-1][0] - frames[0][0]
    fps = min(30.0, max(1.0, (len(frames) - 1) / span)) if span > 0 else 10.0
    h, w = imgs[0].shape[:2]
    for ext, fourcc in (("mp4", "mp4v"), ("avi", "MJPG")):
        fd, tmp = tempfile.mkstemp(suffix="." + ext)
        os.close(fd)
        try:
            vw = cv2.VideoWriter(tmp, cv2.VideoWriter_fourcc(*fourcc), fps, (w, h))
            if not vw.isOpened():
                continue
            for img in imgs:
                vw.write(img if img.shape[:2] == (h, w) else cv2.resize(img, (w, h)))
            vw.release()
            data = Path(tmp).read_bytes()
            if len(data) > 1000:
                return data, ext
        finally:
            Path(tmp).unlink(missing_ok=True)
    return None


class BeeMapHook:
    """Use this from inside an existing VespAI loop:

        hook = BeeMapHook("beemap.ini")
        ...for every frame, after the model runs:
        hook.frame(frame_bgr, [(x1, y1, x2, y2, conf, class_name), ...])

    Boxes are in pixels. Only boxes whose class matches `target_class`
    are tracked; the rest are ignored."""

    def __init__(self, config_path: str = "beemap.ini", cfg: Config | None = None):
        self.cfg = cfg or Config.load(config_path)
        self.up = Uploader(self.cfg)
        self.tracker = Tracker(self.cfg, self._departure)
        self.last_frame = None
        self.frames = 0
        self.visits = 0
        self.started = time.time()
        self.fps = 0.0
        self.model_name = "external"
        self.heading_cam = HeadingCamera(self.cfg) if self.cfg.heading_camera != "none" else None
        self.hive = HiveMonitor(self.cfg, self.up)
        self.pending: list[tuple] = []           # departures waiting for a heading track
        self.pending_lock = threading.Lock()
        self.matched = 0
        self.missed = 0
        self.recent = collections.deque(maxlen=600)  # (t, jpeg) of the last seconds, for visit clips
        threading.Thread(target=self._heartbeat_loop, daemon=True).start()
        if self.heading_cam:
            threading.Thread(target=self._expire_loop, daemon=True).start()

    # ---- heading camera input ------------------------------------------
    def heading_frame(self, frame, t: float | None = None):
        """Feed one frame from the external heading camera."""
        t = t or time.time()
        self.heading_cam.update(t, self.heading_cam.blobs(frame))
        self._resolve()

    def heading_dets(self, dets: list[Det], t: float):
        """Feed already-detected blobs (used by the simulator)."""
        self.heading_cam.update(t, dets)
        self._resolve()

    def flush(self):
        """Close any open heading tracks and send waiting departures now."""
        if self.heading_cam:
            far = max([self.heading_cam.tracker.tracks[k].last_t for k in self.heading_cam.tracker.tracks] or [0]) + 10
            self.heading_cam.update(far, [])
        self._resolve(force=True)

    def _resolve(self, force: bool = False):
        with self.pending_lock:
            keep = []
            for tr, created in self.pending:
                h = self.heading_cam.take(tr.points[-1][0], self.cfg.match_window_s)
                if h:
                    self._send(tr, h)
                elif force or time.time() > created + self.cfg.match_window_s + 1.5:
                    self._send(tr, None)
                else:
                    keep.append((tr, created))
            self.pending = keep

    def _expire_loop(self):
        while True:
            time.sleep(0.5)
            self._resolve()

    def frame(self, frame, boxes: list, t: float | None = None):
        t = t or time.time()
        self.frames += 1
        self.last_frame = frame
        h, w = (frame.shape[:2] if frame is not None else (self.cfg.height, self.cfg.width))
        dets = []
        for x1, y1, x2, y2, conf, cls in boxes:
            if conf < self.cfg.min_confidence or self.cfg.target_class.lower() not in str(cls).lower():
                continue
            dets.append(Det(((x1 + x2) / 2) / w, ((y1 + y2) / 2) / h, (x2 - x1) / w, (y2 - y1) / h, float(conf)))
        if self.cfg.clip_s > 0 and frame is not None and (dets or self.tracker.tracks):
            keep = self.cfg.clip_s + self.cfg.lost_after_s + 2   # only frames while a hornet is in view
            while self.recent and not (t - keep <= self.recent[0][0] <= t):
                self.recent.popleft()                    # too old (or the clock jumped back)
            jpeg = clip_frame(frame, boxes, self.cfg, t)
            if jpeg:
                self.recent.append((t, jpeg))
        self.tracker.update(t, dets, frame)

    def frame_size(self, boxes: list, t: float, size: tuple | None = None):
        """Like frame() but without an image (cv2 missing)."""
        self.frames += 1
        h, w = size or (SIM_H, SIM_W)
        dets = [Det(((x1 + x2) / 2) / w, ((y1 + y2) / 2) / h, (x2 - x1) / w, (y2 - y1) / h, float(c))
                for x1, y1, x2, y2, c, cls in boxes
                if c >= self.cfg.min_confidence and self.cfg.target_class.lower() in str(cls).lower()]
        self.tracker.update(t, dets, None)

    def _departure(self, tr: Track):
        if self.cfg.clip_s > 0:
            start = max(tr.points[0][0] - 1.0, tr.points[-1][0] - self.cfg.clip_s)
            tr.clip = [(t, j) for t, j in self.recent if t >= start]
        if self.heading_cam:
            with self.pending_lock:
                self.pending.append((tr, time.time()))
            return
        self._send(tr, None)

    def _send(self, tr: Track, h: dict | None):
        angle, straight = exit_vector(tr.points)
        self.visits += 1
        meta = {
            "event_id": tr.tid,
            "ts": tr.points[-1][0],
            "arrived_ts": tr.points[0][0],
            "species": "velutina",
            "confidence": round(tr.best_conf, 3),
            "confidence_median": round(statistics.median(tr.confs), 3) if tr.confs else None,
            "straightness": round(straight, 2),
            "track": [[round(t - tr.points[0][0], 2), round(x, 4), round(y, 4)] for t, x, y in tr.points[-60:]],
        }
        if self.heading_cam:
            # two cameras: the heading camera measures the direction
            meta["detector_exit_img"] = None if angle is None else round(angle, 1)
            if h:
                self.matched += 1
                meta.update(heading_rel_deg=round(h["rel"], 1), heading_sigma_deg=round(h["sigma"], 1),
                            heading_source="heading_camera", heading_mode=self.cfg.heading_mode)
            else:
                self.missed += 1
                meta["heading_source"] = None        # presence only
        else:
            # single-camera setup: exit direction on the detection camera
            meta["exit_angle_img"] = None if angle is None else round(angle, 1)
            meta["heading_source"] = "detector" if angle is not None else None
        frame = tr.best_frame
        if frame is not None and hasattr(frame, "copy"):
            frame = draw_track(frame.copy(), tr.points)
        jpeg = encode_jpeg(frame, tr.best_box, f"V. velutina {tr.best_conf:.2f}")
        log.info("hornet left: %s", f"heading camera {h['rel']:.0f}° ±{h['sigma']:.0f}" if h else
                 ("not seen by heading camera" if self.heading_cam else f"exit {angle}° on image"))
        threading.Thread(target=self.up.send_visit, args=(meta, jpeg, tr.clip), daemon=True).start()

    def status(self) -> dict:
        st = {"uptime_s": int(time.time() - self.started), "frames": self.frames, "fps": round(self.fps, 1),
              "visits": self.visits, "queued": self.up.queued(), "clips_waiting": self.up.clips_waiting(),
              "model": self.model_name, "agent": "beemap-agent 1.2"}
        if self.hive.last_reading:
            st["hive"] = self.hive.last_reading
        if self.heading_cam:
            st["heading_camera"] = {"mode": self.cfg.heading_mode, "hfov": self.cfg.heading_hfov,
                                    "fps": round(self.heading_cam.fps, 1), "matched": self.matched,
                                    "missed": self.missed}
        try:
            st["cpu_temp_c"] = round(int(Path("/sys/class/thermal/thermal_zone0/temp").read_text()) / 1000, 1)
        except Exception:
            pass
        return st

    def _heartbeat_loop(self):
        time.sleep(2)
        while True:
            jpeg = encode_jpeg(self.last_frame) if self.cfg.send_live_frame else None
            self.up.heartbeat(self.status(), jpeg)
            time.sleep(self.cfg.heartbeat_s)


# =============================================================== camera + model
def open_camera(cfg: Config, spec: str | None = None, size: tuple[int, int] | None = None):
    spec = spec or cfg.camera
    width, height = size or (cfg.width, cfg.height)
    if spec.startswith("picamera2"):
        from picamera2 import Picamera2
        num = int(spec.split(":", 1)[1]) if ":" in spec else 0      # Pi 5 has two camera ports
        cam = Picamera2(num)
        cam.configure(cam.create_video_configuration(main={"size": (width, height), "format": "RGB888"}))
        cam.start()
        if cfg.autofocus:
            try:                                  # Camera Module 3 has autofocus; older modules don't
                from libcamera import controls
                cam.set_controls({"AfMode": controls.AfModeEnum.Continuous})
            except Exception as e:
                log.debug("no autofocus on this camera: %s", e)
        time.sleep(1)
        rot = _rotation(cfg) if spec == cfg.camera else None
        if rot is None:
            return lambda: cam.capture_array()   # RGB888 on Picamera2 is BGR order in memory, as OpenCV expects
        import cv2
        return lambda: cv2.rotate(cam.capture_array(), rot)
    import cv2
    src = spec.split(":", 1)[1] if ":" in spec else "0"
    cap = cv2.VideoCapture(int(src) if src.isdigit() else src)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    if not cap.isOpened():
        raise SystemExit(f"Could not open camera '{spec}'.")

    rot = _rotation(cfg) if spec == cfg.camera else None

    def read():
        ok, f = cap.read()
        if not ok and spec.startswith("file:"):
            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ok, f = cap.read()
        if ok and rot is not None:
            f = cv2.rotate(f, rot)
        return f if ok else None
    return read


def _rotation(cfg: Config):
    if not cfg.rotate:
        return None
    import cv2
    rots = {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}
    if cfg.rotate not in rots:
        raise SystemExit(f"rotate must be 0, 90, 180 or 270, not {cfg.rotate}")
    return rots[cfg.rotate]


def load_model(cfg: Config):
    """VespAI ships YOLOv5 weights. Loaded through torch.hub so the exact
    VespAI checkpoint works without changes.

    The VespAI checkpoint is a whole pickled model and only loads with the
    YOLOv5 code it was saved with (commit 23701eac); the current GitHub
    version fails. Set yolov5_dir= to a local checkout of that commit."""
    import torch
    os.environ.setdefault("TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD", "1")   # torch >= 2.6 refuses pickled models otherwise
    weights = str(Path(cfg.weights).expanduser())
    if not Path(weights).exists():
        raise SystemExit(f"Model weights not found at {cfg.weights}. Put the VespAI .pt file there "
                         "or set weights= in the config.")
    if cfg.yolov5_dir:
        repo = Path(cfg.yolov5_dir).expanduser()
        if not (repo / "hubconf.py").exists():
            raise SystemExit(f"yolov5_dir={cfg.yolov5_dir} has no hubconf.py. Check out ultralytics/yolov5 "
                             "at commit 23701eac there.")
        model = torch.hub.load(str(repo), "custom", path=weights, source="local", trust_repo=True, verbose=False)
    else:
        model = torch.hub.load("ultralytics/yolov5", "custom", path=weights, trust_repo=True)
    model.conf = cfg.min_confidence
    names = model.names if isinstance(model.names, dict) else dict(enumerate(model.names))
    if not any(cfg.target_class.lower() in str(n).lower() for n in names.values()):
        raise SystemExit(f"No class matching '{cfg.target_class}' in model classes {names}. "
                         "Set target_class= in the config.")
    log.info("model classes: %s", names)

    def infer(frame):
        res = model(frame[..., ::-1], size=640)        # model wants RGB
        out = []
        for x1, y1, x2, y2, conf, cls in res.xyxy[0].tolist():
            out.append((x1, y1, x2, y2, conf, names[int(cls)]))
        return out
    return infer


class LiveStream:
    """Live view for aiming the camera: the latest frame with VespAI's boxes, as an
    MJPEG stream any browser on the same network can open at http://<pi>:<port>/.
    No login, so only switch it on in a network you trust."""

    PAGE = (b"<!doctype html><title>BeeMap station</title><body style='margin:0;background:#111'>"
            b"<img src='/stream' style='width:100vw;height:100vh;object-fit:contain'></body>")

    def __init__(self, port: int):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        self.jpeg, self.cond = None, threading.Condition()
        live = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                self.send_response(200)
                if self.path != "/stream":
                    self.send_header("Content-Type", "text/html")
                    self.end_headers()
                    self.wfile.write(LiveStream.PAGE)
                    return
                self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                self.end_headers()
                try:
                    while True:
                        with live.cond:
                            live.cond.wait(timeout=5)
                            jpeg = live.jpeg
                        if jpeg:
                            self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                                             + str(len(jpeg)).encode() + b"\r\n\r\n" + jpeg + b"\r\n")
                except (BrokenPipeError, ConnectionResetError):
                    pass

        server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, daemon=True).start()
        log.info("live view on port %d", port)

    def push(self, frame, boxes: list, cfg: Config, fps: float):
        import cv2
        img = frame.copy()
        for x1, y1, x2, y2, conf, cls in boxes:
            target = cfg.target_class.lower() in str(cls).lower()
            if conf < cfg.min_confidence:
                continue
            colour = (27, 72, 194) if target else (200, 200, 200)       # hornet orange / grey (BGR)
            cv2.rectangle(img, (int(x1), int(y1)), (int(x2), int(y2)), colour, 3)
            cv2.putText(img, f"{cls} {conf:.2f}", (int(x1), max(18, int(y1) - 8)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, colour, 2)
        cv2.putText(img, f"{cfg.station_id}  {fps:.1f} fps  conf >= {cfg.min_confidence}", (12, 28),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 80])
        if ok:
            with self.cond:
                self.jpeg = buf.tobytes()
                self.cond.notify_all()


def run_camera(cfg: Config):
    hook = BeeMapHook(cfg=cfg)
    hook.model_name = Path(cfg.weights).name
    read = open_camera(cfg)
    infer = load_model(cfg)
    live = LiveStream(cfg.stream_port) if cfg.stream_port else None
    period = 1.0 / cfg.fps
    log.info("watching the bait station at %.0f fps", cfg.fps)
    if hook.heading_cam:
        threading.Thread(target=run_heading_camera, args=(cfg, hook), daemon=True).start()
    last = time.time()
    while True:
        t0 = time.time()
        frame = read()
        if frame is None:
            time.sleep(0.5)
            continue
        boxes = infer(frame)
        hook.frame(frame, boxes, t0)
        hook.hive.count(frame)
        if live:
            live.push(frame, boxes, cfg, hook.fps)
        dt = time.time() - last
        last = time.time()
        hook.fps = 0.9 * hook.fps + 0.1 * (1 / dt if dt > 0 else 0)
        time.sleep(max(0.0, period - (time.time() - t0)))


def run_heading_camera(cfg: Config, hook: "BeeMapHook"):
    read = open_camera(cfg, cfg.heading_camera, (cfg.heading_width, cfg.heading_height))
    log.info("heading camera (%s mode) running", cfg.heading_mode)
    last = time.time()
    while True:
        frame = read()
        if frame is None:
            time.sleep(0.2)
            continue
        hook.heading_frame(frame, time.time())
        dt = time.time() - last
        last = time.time()
        hook.heading_cam.fps = 0.9 * hook.heading_cam.fps + 0.1 * (1 / dt if dt > 0 else 0)


# =============================================================== simulation
SIM_W, SIM_H = 640, 360


def _sim_background():
    try:
        import numpy as np
        import cv2
    except ImportError:
        return None
    img = np.full((SIM_H, SIM_W, 3), (51, 59, 49), np.uint8)
    for i in range(-SIM_H, SIM_W, 20):
        cv2.line(img, (i, 0), (i + SIM_H, SIM_H), (44, 51, 42), 8)
    cv2.circle(img, (SIM_W // 2, SIM_H // 2), 60, (90, 150, 190), -1)        # the bait dish
    return img


def _sim_quality(img_angle_deg: float, t: float) -> tuple[float, float]:
    """Range of the model's confidence for one simulated visit: most are clear, some
    borderline (a person checks the clip), a few weak (dismissed). Drawn from its own
    generator, so a seeded demo's directions and timings stay the same."""
    q = random.Random(f"{img_angle_deg:.6f}|{t:.3f}").random()
    return (0.88, 0.98) if q < 0.75 else (0.74, 0.9) if q < 0.95 else (0.6, 0.74)


def fake_visit(hook: "BeeMapHook", img_angle_deg: float, t: float, rng: random.Random,
               draw: bool = True) -> float:
    """Feed one fake hornet visit through the real tracking pipeline:
    feeding jitter, an orientation loop, then a straight dash off the frame
    at img_angle_deg (clockwise from image top). Returns the end time."""
    try:
        import cv2
    except ImportError:
        cv2 = None
    a = math.radians(img_angle_deg)
    lo, hi = _sim_quality(img_angle_deg, t)
    x, y = 0.5 + rng.uniform(-0.05, 0.05), 0.5 + rng.uniform(-0.05, 0.05)
    steps = [(x + rng.gauss(0, 0.01), y + rng.gauss(0, 0.01)) for _ in range(rng.randint(15, 40))]
    loop_r = rng.uniform(0.06, 0.14)
    for k in range(12):                                                   # orientation loop
        th = k / 12 * 2 * math.pi
        steps.append((x + loop_r * math.sin(th), y - loop_r * math.cos(th) + loop_r))
    vx, vy = math.sin(a), -math.cos(a)
    px, py = steps[-1]
    while 0 < px < 1 and 0 < py < 1:                                      # straight dash out
        px += vx * 0.07 + rng.gauss(0, 0.008)
        py += vy * 0.07 + rng.gauss(0, 0.008)
        steps.append((px, py))
    bg = _sim_background() if draw else None
    for sx, sy in steps:
        t += 0.1
        frame, boxes = None, []
        if bg is not None:
            frame = bg.copy()
        if 0 <= sx <= 1 and 0 <= sy <= 1:
            cx, cy = int(sx * SIM_W), int(sy * SIM_H)
            if frame is not None and cv2 is not None:
                cv2.ellipse(frame, (cx, cy), (14, 6), 0, 0, 360, (23, 26, 30), -1)
                cv2.ellipse(frame, (cx + 4, cy), (4, 6), 0, 0, 360, (44, 154, 227), -1)
            conf = lo + (rng.uniform(0.8, 0.98) - 0.8) / 0.18 * (hi - lo)
            boxes = [(cx - 16, cy - 10, cx + 16, cy + 10, conf, "Vespa velutina")]
        hook.frame(frame, boxes, t) if frame is not None else hook.frame_size(boxes, t)
    t += hook.cfg.lost_after_s + 0.1
    hook.frame(bg, [], t) if bg is not None else hook.frame_size([], t)   # let the track close
    return t


def fake_heading(hook: "BeeMapHook", compass: float, ref: float, t: float, rng: random.Random) -> bool:
    """What the external heading camera would see of a hornet flying off
    towards `compass`, with its reference direction `ref`. Returns False if
    it flew outside the camera's view (horizon mode)."""
    cfg = hook.cfg
    rel = (compass - ref + 540) % 360 - 180
    if cfg.heading_flip:
        rel = -rel
    pts = []
    if cfg.heading_mode == "horizon":
        half = cfg.heading_hfov / 2
        if abs(rel) > half - 2:
            return False
        x_end = 0.5 + math.tan(math.radians(rel)) / (2 * math.tan(math.radians(half)))
        x0 = x_end + rng.uniform(-0.15, 0.15)              # it comes in from close by and recedes
        for k in range(10):
            f = 1 - (1 - k / 9) ** 2
            pts.append((x0 + (x_end - x0) * f + rng.gauss(0, 0.002), 0.55 - 0.1 * f))
    else:
        a = math.radians(-rel)                              # looking up mirrors the world
        vx, vy = math.sin(a), -math.cos(a)
        x, y = 0.5 + rng.gauss(0, 0.03), 0.5 + rng.gauss(0, 0.03)
        while 0 < x < 1 and 0 < y < 1:
            pts.append((x, y))
            x += vx * 0.08 + rng.gauss(0, 0.006)
            y += vy * 0.08 + rng.gauss(0, 0.006)
    tt = t + rng.uniform(0.05, 0.4)
    for x, y in pts:
        tt += 1 / 30
        hook.heading_dets([Det(x, y, 0.01, 0.01, 1.0)], tt)
    hook.heading_dets([], tt + 1.0)                         # track closes
    return True


def run_simulation(cfg: Config):
    """No camera, no model: fake hornets that feed, circle, then leave towards
    sim_nest_bearing. Exercises tracking, uploading, queueing and the server."""
    if cfg.heading_camera == "none":
        log.info("no heading_camera configured: simulating a single-camera station")
    else:
        cfg.heading_camera = "simulated"
    hook = BeeMapHook(cfg=cfg)
    hook.model_name = "simulated"
    hook.last_frame = _sim_background()
    rng = random.Random()
    log.info("simulating: nest is %.0f° from this station, camera heading %.0f°",
             cfg.sim_nest_bearing, cfg.sim_heading)
    while True:
        time.sleep(rng.expovariate(cfg.sim_rate_per_min / 60))
        if hook.heading_cam:
            # detection camera: the hornet feeds and leaves in any direction on its image;
            # heading camera: follows it towards the nest
            t_end = fake_visit(hook, rng.uniform(0, 360), time.time(), rng)
            fake_heading(hook, (cfg.sim_nest_bearing + rng.gauss(0, 6)) % 360, cfg.sim_heading, t_end, rng)
            hook.flush()
        else:
            # what the camera sees = true compass direction minus the camera heading, plus wobble
            fake_visit(hook, (cfg.sim_nest_bearing - cfg.sim_heading + rng.gauss(0, 15)) % 360, time.time(), rng)


# =============================================================== main
def main():
    ap = argparse.ArgumentParser(description="BeeMap agent for VespAI stations")
    ap.add_argument("--config", default="beemap.ini")
    ap.add_argument("--simulate", action="store_true", help="fake hornets, no camera or model needed")
    ap.add_argument("--test", action="store_true", help="check the server and token, then exit")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    cfg = Config.load(a.config)
    os.chdir(Path(a.config).resolve().parent)
    if a.test:
        up = Uploader(cfg)
        ok = up.heartbeat({"agent": "beemap-agent 1.0", "test": True}, None)
        print("Connected. Heading on server: %s°" % up.calibration.get("heading_deg") if ok else
              "Could not reach the server or the token was refused. See the log above.")
        raise SystemExit(0 if ok else 1)
    if a.simulate:
        run_simulation(cfg)
    else:
        run_camera(cfg)


if __name__ == "__main__":
    main()
