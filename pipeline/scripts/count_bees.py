#!/usr/bin/env python3
"""Run the bee detector over every recording session and write activity tables.

Outputs
  data/activity_frames.csv   one row per frame: timestamp, video, frame, bee_count
  data/detections.csv        one row per detected bee: video, frame, cx, cy, w, h, conf (pixels, 320x240 frames)
  data/activity.csv          resampled to one row per minute: timestamp, video,
                             frames, bee_count_mean, bees_per_min
  output/demo_annotated.mp4  short clip with boxes drawn, for the pitch

Timestamps: a session folder name gives the start time (e.g. 20260812-1720).
Frames carry no clock, so frame k is placed at start + k * FRAME_INTERVAL_S
(see common.py).  Sessions named <date>_<n> only have a date; they get
start_known = False and are excluded from the time-of-day analysis.

Metrics
  bee_count      detections in one composite frame
  bee_count_mean mean detections per frame within the minute (independent of
                 the frame interval)
  bees_per_min   sum of detections in the minute, i.e. detections per minute
                 of recording (scales with FRAME_INTERVAL_S)
"""
import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np
import pandas as pd
import torch
from PIL import Image
from ultralytics import YOLO

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import DATA, FRAME_INTERVAL_S, MODEL_PATH, OUTPUT, session_start  # noqa: E402


# A composite frame shows motion as colour (R,G,B = three captures).  A session
# whose frames contain no coloured pixels at all was recorded without the
# composite trick, or nothing moved: either way it carries no activity signal and
# must not be read as "zero bees".  Threshold: share of pixels with channel
# spread > 60, averaged over every 10th frame; real sessions sit at 0.1-12 %.
SIGNAL_MIN_COLOUR_PCT = 0.02


def colour_share_pct(frames, step=10):
    """Mean % of strongly coloured pixels over every `step`-th frame of a session."""
    vals = []
    for _, p in frames[::step]:
        a = np.asarray(Image.open(p).convert("RGB")).astype(np.int16)
        vals.append(((a.max(2) - a.min(2)) > 60).mean() * 100)
    return float(np.mean(vals)) if vals else 0.0


def frames_of(session_dir):
    """Sorted [(frame_no, path)] for a session folder (frames may sit in a subfolder)."""
    out = []
    for p in session_dir.rglob("bee_*.jpg"):
        m = re.search(r"bee_(\d+)\.jpg$", p.name)
        if m:
            out.append((int(m.group(1)), p))
    return sorted(out)


DETECTIONS = []      # one row per detected bee: video, frame, centre, size, confidence -> data/detections.csv


def detect_session(model, session_dir, conf, device, batch=64):
    """Return a DataFrame with one row per frame of this session."""
    frames = frames_of(session_dir)
    start = session_start(session_dir.name)
    start_known = bool(re.match(r"\d{8}[-_]\d{4}$", session_dir.name))
    colour_pct = colour_share_pct(frames)
    signal_ok = colour_pct >= SIGNAL_MIN_COLOUR_PCT
    rows = []
    for i in range(0, len(frames), batch):
        chunk = frames[i:i + batch]
        results = model.predict([str(p) for _, p in chunk], conf=conf, imgsz=640,
                                device=device, verbose=False)
        for (frame_no, _), r in zip(chunk, results):
            for (x1, y1, x2, y2), c in zip(r.boxes.xyxy.tolist(), r.boxes.conf.tolist()):
                DETECTIONS.append({"video": session_dir.name, "frame": frame_no, "cx": round((x1 + x2) / 2, 1),
                                   "cy": round((y1 + y2) / 2, 1), "w": round(x2 - x1, 1), "h": round(y2 - y1, 1), "conf": round(c, 3)})
            rows.append({
                "timestamp": start + pd.Timedelta(seconds=frame_no * FRAME_INTERVAL_S),
                "video": session_dir.name,
                "frame": frame_no,
                "bee_count": len(r.boxes),
                "start_known": start_known,
                "colour_pct": colour_pct,
                "signal_ok": signal_ok,
            })
        print(f"  {session_dir.name}: {min(i + batch, len(frames))}/{len(frames)} frames", end="\r")
    print()
    return pd.DataFrame(rows)


def per_minute(df):
    """Resample the per-frame table to one row per (video, minute)."""
    g = df.set_index("timestamp").groupby("video")
    out = g.resample("1min").agg(frames=("bee_count", "size"),
                                 bee_count_mean=("bee_count", "mean"),
                                 bees_per_min=("bee_count", "sum"),
                                 start_known=("start_known", "first"),
                                 colour_pct=("colour_pct", "first"), signal_ok=("signal_ok", "first"))
    out = out[out.frames > 0].reset_index()
    return out[["timestamp", "video", "frames", "bee_count_mean", "bees_per_min", "start_known", "colour_pct", "signal_ok"]]


def write_demo_clip(model, session_dir, conf, device, out_path, n_frames=300, fps=10):
    """Annotated clip of the first n_frames of a session (H.264 if ffmpeg is present)."""
    frames = frames_of(session_dir)[:n_frames]
    tmp = out_path.with_suffix(".mp4v.mp4")
    writer = None
    for i in range(0, len(frames), 32):
        chunk = frames[i:i + 32]
        for (frame_no, _), r in zip(chunk, model.predict([str(p) for _, p in chunk], conf=conf,
                                                         imgsz=640, device=device, verbose=False)):
            img = r.plot(labels=False, line_width=1)       # boxes drawn by ultralytics
            img = cv2.resize(img, (640, 480), interpolation=cv2.INTER_NEAREST)
            cv2.putText(img, f"{session_dir.name}  frame {frame_no}  bees: {len(r.boxes)}",
                        (8, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)
            if writer is None:
                writer = cv2.VideoWriter(str(tmp), cv2.VideoWriter_fourcc(*"mp4v"), fps, (640, 480))
            writer.write(img)
    writer.release()
    # Browsers (and Streamlit) need H.264; re-encode when ffmpeg is available.
    if shutil.which("ffmpeg"):
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(tmp), "-c:v", "libx264",
                        "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out_path)], check=True)
        tmp.unlink()
    else:
        tmp.rename(out_path)
    print(f"  demo clip -> {out_path}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", default=str(MODEL_PATH))
    ap.add_argument("--conf", type=float, default=0.25)
    ap.add_argument("--device", default="mps" if torch.backends.mps.is_available() else "cpu")
    ap.add_argument("--demo-session", default="20260812_1914", help="session used for the demo clip")
    ap.add_argument("--sessions", nargs="*", help="only these session names (for quick tests)")
    args = ap.parse_args()

    model = YOLO(args.model)
    sessions = sorted(p for p in (DATA / "recordings").iterdir() if p.is_dir())
    if args.sessions:
        sessions = [s for s in sessions if s.name in args.sessions]
    print(f"model {args.model}, device {args.device}, {len(sessions)} sessions, "
          f"frame interval {FRAME_INTERVAL_S}s")
    frames = pd.concat([detect_session(model, s, args.conf, args.device) for s in sessions], ignore_index=True)
    frames.to_csv(DATA / "activity_frames.csv", index=False)
    pd.DataFrame(DETECTIONS).to_csv(DATA / "detections.csv", index=False)
    minutes = per_minute(frames)
    minutes.to_csv(DATA / "activity.csv", index=False)
    print(f"wrote {len(frames)} frame rows -> data/activity_frames.csv, "
          f"{len(minutes)} minute rows -> data/activity.csv")
    print(frames.groupby("video").agg(frames=("bee_count", "size"), det_per_frame=("bee_count", "mean"),
                                      colour_pct=("colour_pct", "first"), signal_ok=("signal_ok", "first")).round(3).to_string())

    OUTPUT.mkdir(exist_ok=True)
    demo = DATA / "recordings" / args.demo_session
    write_demo_clip(model, demo if demo.exists() else sessions[0], args.conf, args.device,
                    OUTPUT / "demo_annotated.mp4")


if __name__ == "__main__":
    main()
