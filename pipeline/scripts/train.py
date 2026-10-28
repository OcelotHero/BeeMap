#!/usr/bin/env python3
"""Train a YOLOv8n bee detector on data/bees.yaml (built by make_dataset.py).

Usage:
    python scripts/train.py                 # 50 epochs, imgsz 640
    python scripts/train.py --epochs 5      # quick smoke test
    python scripts/train.py --device cpu    # force CPU

Device: Apple GPU (mps) or CUDA when available, otherwise CPU.  If the GPU run
crashes for any reason the script retries once on CPU.
The best weights are copied to models/bee_yolov8n.pt (see common.MODEL_PATH).
"""
import argparse
import shutil
import sys
from pathlib import Path

import torch
from ultralytics import YOLO

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import DATA, MODEL_PATH, ROOT  # noqa: E402


def pick_device(requested):
    if requested != "auto":
        return requested
    if torch.cuda.is_available():
        return "0"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def train(args, device):
    model = YOLO(args.model)
    return model.train(
        data=str(DATA / "bees.yaml"),
        epochs=args.epochs,
        imgsz=args.imgsz,
        batch=args.batch,
        device=device,
        workers=args.workers,
        project=str(ROOT / "runs" / "detect"),
        name=args.name,
        exist_ok=True,
        patience=args.patience,
        plots=True,
        verbose=True,
        # The frames are composites of three time steps; flipping is fine,
        # but hue/saturation changes would destroy the motion colour cue.
        hsv_h=0.0, hsv_s=0.0, hsv_v=0.3,
    )


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--epochs", type=int, default=50)
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--model", default="yolov8n.pt", help="starting weights")
    ap.add_argument("--device", default="auto", help="auto | cpu | mps | 0")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--patience", type=int, default=20, help="early-stopping patience (epochs)")
    ap.add_argument("--name", default="bees", help="run name under runs/detect/")
    args = ap.parse_args()

    device = pick_device(args.device)
    print(f"training on device={device} for {args.epochs} epochs, imgsz={args.imgsz}")
    try:
        results = train(args, device)
    except Exception as e:  # noqa: BLE001
        if device == "cpu":
            raise
        print(f"\n!! training on {device} failed ({e}); retrying on cpu\n")
        results = train(args, "cpu")

    best = ROOT / "runs" / "detect" / args.name / "weights" / "best.pt"
    MODEL_PATH.parent.mkdir(exist_ok=True)
    shutil.copy(best, MODEL_PATH)
    box = results.box
    print(f"\nval mAP50={box.map50:.3f}  mAP50-95={box.map:.3f}  precision={box.mp:.3f}  recall={box.mr:.3f}")
    print(f"weights -> {MODEL_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
