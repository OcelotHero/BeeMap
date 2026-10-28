#!/usr/bin/env bash
# One-time setup for the hornet alarm (scripts/hornet_live.py):
#   1. extra Python packages the pinned YOLOv5 code needs
#   2. ultralytics/yolov5 at commit 23701eac (the one VespAI pins; newer commits break their model)
#   3. the VespAI "all hornets" weights (YOLOv5s, 14 MB, public on GitHub)
set -euo pipefail
cd "$(dirname "$0")/.."
PY=.venv/bin/python
[ -x "$PY" ] || { echo "no .venv - run: python3.11 -m venv .venv && .venv/bin/pip install -r requirements.txt"; exit 1; }

echo "1/3 packages"
.venv/bin/pip install -q "tqdm>=4.64" "seaborn>=0.13" "setuptools<81" "opencv-python>=4.10"

echo "2/3 YOLOv5 @ 23701eac -> vendor/yolov5"
if [ ! -f vendor/yolov5/hubconf.py ]; then
  mkdir -p vendor && rm -rf vendor/yolov5
  git init -q vendor/yolov5
  git -C vendor/yolov5 remote add origin https://github.com/ultralytics/yolov5
  git -C vendor/yolov5 fetch -q --depth 1 origin 23701eac7a7b160e478ba4bbef966d0af9348251
  git -C vendor/yolov5 checkout -q FETCH_HEAD
fi

echo "3/3 VespAI weights -> models/vespai_yolov5s_hornets.pt"
mkdir -p models
[ -s models/vespai_yolov5s_hornets.pt ] || curl -sL -o models/vespai_yolov5s_hornets.pt \
  https://raw.githubusercontent.com/andrw3000/vespai/main/models/yolov5-params/yolov5s-all-hornets.pt

echo "ready: make hornet-live   (or .venv/bin/python scripts/hornet_live.py --source video.mp4)"
