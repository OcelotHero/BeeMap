#!/usr/bin/env python3
"""Asian hornet alarm: the VespAI detector (YOLOv5s, O'Shea-Wheller et al. 2024) on a
webcam or a video file.  Red box = Vespa velutina (Asian hornet), orange = Vespa crabro
(European hornet).  Press q in the window to stop.

Setup once:   make hornet-setup      (fetches the pinned YOLOv5 code and the VespAI weights)
Run:          make hornet-live       (MacBook camera, 10 minutes max)
              .venv/bin/python scripts/hornet_live.py --source path/to/video.mp4
              .venv/bin/python scripts/hornet_live.py --source 1        (second camera)
              .venv/bin/python scripts/hornet_live.py --source picam --no-window   (Raspberry Pi camera module)

macOS asks once whether the terminal may use the camera; allow it in
System Settings > Privacy & Security > Camera if the window never opens.

Frames with a detection are written to output/hornet_live/ (first 50) and listed in
output/hornet_live/detections.csv so the dashboard can pick them up.
"""
import argparse
import csv
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

os.environ.setdefault("TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD", "1")   # VespAI weights are a full pickled model

import cv2  # noqa: E402
import torch  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
YOLOV5_DIR = ROOT / "vendor" / "yolov5"                        # ultralytics/yolov5 @ 23701eac, as VespAI pins it
WEIGHTS = ROOT / "models" / "vespai_yolov5s_hornets.pt"
OUT = ROOT / "output" / "hornet_live"
COLOUR = {"Vespa velutina": (40, 40, 220), "Vespa crabro": (220, 120, 40)}   # BGR


class PiCamera:
    """Raspberry Pi Camera Module (libcamera) with the same read() interface as cv2.VideoCapture.
    Needs picamera2 from apt (python3-picamera2) and a venv made with --system-site-packages."""

    def __init__(self, size=(1280, 720)):
        from picamera2 import Picamera2
        self.cam = Picamera2()
        # "RGB888" in libcamera is stored B, G, R in memory: already OpenCV order
        self.cam.configure(self.cam.create_video_configuration(main={"size": size, "format": "RGB888"}))
        self.cam.start()
        try:                                    # Camera Module 3 has autofocus; older modules do not
            from libcamera import controls
            self.cam.set_controls({"AfMode": controls.AfModeEnum.Continuous})
        except Exception:
            pass

    def isOpened(self):
        return True

    def read(self):
        return True, self.cam.capture_array()

    def release(self):
        self.cam.stop()


class Stream:
    """Serves the latest annotated frame as an MJPEG stream: open http://<host>:<port>/ in a browser."""

    PAGE = (b"<!doctype html><title>Hornet alarm</title><body style='margin:0;background:#111'>"
            b"<img src='/stream' style='width:100vw;height:100vh;object-fit:contain'></body>")

    def __init__(self, port):
        self.jpeg, self.cond = None, threading.Condition()
        stream = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                if self.path != "/stream":
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html")
                    self.end_headers()
                    self.wfile.write(Stream.PAGE)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                self.end_headers()
                try:
                    while True:
                        with stream.cond:
                            stream.cond.wait(timeout=5)
                            jpeg = stream.jpeg
                        if jpeg is None:
                            continue
                        self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                                         + str(len(jpeg)).encode() + b"\r\n\r\n" + jpeg + b"\r\n")
                except (BrokenPipeError, ConnectionResetError):
                    pass

        self.server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def push(self, frame):
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
        if ok:
            with self.cond:
                self.jpeg = buf.tobytes()
                self.cond.notify_all()


ROTATE = {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", default="0", help="camera index (0 = built-in), 'picam' (Raspberry Pi camera module) or a video file")
    ap.add_argument("--seconds", type=float, default=600, help="stop after this many seconds")
    ap.add_argument("--conf", type=float, default=0.5, help="confidence threshold (VespAI uses 0.8 in the field)")
    ap.add_argument("--no-window", action="store_true", help="headless: no display, only prints and saves")
    ap.add_argument("--stream", type=int, default=0, metavar="PORT",
                    help="serve the annotated feed at http://<this machine>:PORT/ (e.g. 8000); 0 = off")
    ap.add_argument("--rotate", type=int, default=0, choices=(0, 90, 180, 270),
                    help="rotate the camera image clockwise before detection")
    args = ap.parse_args()

    for p, hint in ((YOLOV5_DIR / "hubconf.py", "make hornet-setup"), (WEIGHTS, "make hornet-setup")):
        if not p.exists():
            sys.exit(f"missing {p.relative_to(ROOT)} - run `{hint}` first")
    OUT.mkdir(parents=True, exist_ok=True)

    model = torch.hub.load(str(YOLOV5_DIR), "custom", path=str(WEIGHTS), source="local", trust_repo=True, verbose=False)
    model.conf = args.conf
    print(f"model loaded: {model.names}", flush=True)

    if args.source == "picam":
        cap = PiCamera()
    else:
        src = int(args.source) if args.source.isdigit() else args.source
        cap = cv2.VideoCapture(src)
    if not cap.isOpened():
        sys.exit("could not open the camera / file. On macOS allow the camera for your terminal app in "
                 "System Settings > Privacy & Security > Camera, then run again.")
    print(f"source {args.source} open, press q in the window to stop", flush=True)
    stream = Stream(args.stream) if args.stream else None
    if stream:
        print(f"live view: http://{os.uname().nodename}.local:{args.stream}/", flush=True)

    log = open(OUT / "detections.csv", "a", newline="")
    writer = csv.writer(log)
    if log.tell() == 0:
        writer.writerow(["time", "frame", "species", "confidence", "xmin", "ymin", "xmax", "ymax", "image"])

    t_start, n_frames, n_hits, fps = time.time(), 0, 0, 0.0
    while time.time() - t_start < args.seconds:
        ok, frame = cap.read()
        if not ok:
            break
        if args.rotate:
            frame = cv2.rotate(frame, ROTATE[args.rotate])
        t0 = time.time()
        df = model(frame[:, :, ::-1], size=640).pandas().xyxy[0]      # BGR -> RGB
        fps = 0.9 * fps + 0.1 / max(time.time() - t0, 1e-3)
        n_frames += 1
        for r in df.itertuples():
            c = COLOUR.get(r.name, (200, 200, 200))
            cv2.rectangle(frame, (int(r.xmin), int(r.ymin)), (int(r.xmax), int(r.ymax)), c, 3)
            cv2.putText(frame, f"{r.name} {r.confidence:.2f}", (int(r.xmin), max(int(r.ymin) - 8, 16)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, c, 2)
        if len(df):
            n_hits += 1
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            image = ""
            if n_hits <= 50:
                image = f"hit_{n_hits:03d}.jpg"
                cv2.imwrite(str(OUT / image), frame)
            for r in df.itertuples():
                writer.writerow([stamp, n_frames, r.name, round(r.confidence, 3), int(r.xmin), int(r.ymin), int(r.xmax), int(r.ymax), image])
            log.flush()
            print(f"{stamp} frame {n_frames}: " + ", ".join(f"{r.name} {r.confidence:.2f}" for r in df.itertuples()), flush=True)
        cv2.putText(frame, f"VespAI live  {fps:.1f} fps  conf {args.conf}", (12, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
        if stream:
            stream.push(frame)
        if not args.no_window:
            cv2.imshow("Hornet alarm (VespAI)", frame)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
    cap.release()
    cv2.destroyAllWindows()
    log.close()
    print(f"done: {n_frames} frames in {time.time() - t_start:.0f} s, {n_hits} frames with a detection, ~{fps:.1f} fps. "
          f"Saved to {OUT.relative_to(ROOT)}/", flush=True)


if __name__ == "__main__":
    main()
