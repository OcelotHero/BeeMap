#!/usr/bin/env python3
"""Turn the 32x32 class crops into a YOLO detection dataset (one class: bee).

How the labels work
  * data/traindata/<class>/<name>.png are 32x32 crops cut from the recording
    frames.  <name> encodes frame number, crop centre (x, y) and sometimes the
    session.  Classes: background, bee_motion, bee_slow.
  * We turn every bee_motion / bee_slow crop into a 32x32 box centred at (x, y)
    on the source frame.  background crops only tell us the frame was looked
    at; frames with no bee crops become negative images (empty label file).
  * Many crop names carry only a frame number, and up to six sessions reuse the
    same numbers.  We find the right session by comparing the crop pixels with
    the 32x32 patch at (x, y) in every candidate frame (exact match in >95%).

Output
  data/yolo/images/{train,val}/*.jpg, data/yolo/labels/{train,val}/*.txt
  data/bees.yaml
"""
import random
import re
import shutil
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import DATA  # noqa: E402

BOX = 32                      # crop size = box size
MATCH_THRESHOLD = 3.0         # mean abs pixel difference accepted as "same patch"
VAL_FRACTION = 0.2
SEED = 42

CROP_RE = re.compile(
    r"^(?P<prefix>.*?)bee_(?P<frame>\d+)__x(?P<x>\d+)_y(?P<y>\d+)_a(?P<area>\d+)"
    r"(?P<suffix>_\d{8,9}(?:_\d)?)?\.png$")

# Session hints as they appear in crop file names -> session folder.
SESSION_HINTS = {
    "0260812-1720": "20260812-1720",   # leading digit was cut off by the labelling tool
    "20260812-1914": "20260812_1914",
    "_202509031": "20260903_1",        # mistyped year, see data/README.md
    "_20260903_2": "20260903_2",
}


class FrameStore:
    """Lazy cache: (session, frame_no) -> (path, HxWx3 uint8 array)."""

    def __init__(self):
        self.paths = defaultdict(dict)   # frame_no -> {session: path}
        for p in (DATA / "recordings").rglob("bee_*.jpg"):
            session = p.relative_to(DATA / "recordings").parts[0]
            self.paths[int(p.stem.split("_")[1])][session] = p
        self.cache = {}

    def get(self, session, frame_no):
        key = (session, frame_no)
        if key not in self.cache:
            p = self.paths[frame_no][session]
            self.cache[key] = (p, np.asarray(Image.open(p).convert("RGB")))
        return self.cache[key]


def patch_error(img, cx, cy, crop):
    """Mean abs difference between the crop and the 32x32 patch centred at (cx, cy).

    Crops at the frame border were padded by the labelling tool, so only the
    pixels that lie inside the frame are compared.
    """
    h, w = img.shape[:2]
    x0, y0 = cx - BOX // 2, cy - BOX // 2
    ix0, iy0, ix1, iy1 = max(x0, 0), max(y0, 0), min(x0 + BOX, w), min(y0 + BOX, h)
    if ix1 <= ix0 or iy1 <= iy0:
        return 255.0
    inside = img[iy0:iy1, ix0:ix1].astype(np.int16)
    return np.abs(inside - crop[iy0 - y0:iy1 - y0, ix0 - x0:ix1 - x0]).mean()


def resolve_session(store, m, crop):
    """Return the session whose frame contains this crop, or None.

    A session hint in the file name wins.  Otherwise the crop pixels are
    compared with the same patch in every session that has this frame number.
    """
    frame_no = int(m["frame"])
    hint = SESSION_HINTS.get(m["prefix"] or m["suffix"] or "")
    if hint:
        return hint if frame_no in store.paths and hint in store.paths[frame_no] else None
    best, best_err = None, MATCH_THRESHOLD
    for session in sorted(store.paths.get(frame_no, {})):
        _, img = store.get(session, frame_no)
        err = patch_error(img, int(m["x"]), int(m["y"]), crop)
        if err < best_err:
            best, best_err = session, err
    return best


def main():
    store = FrameStore()
    frames = defaultdict(list)          # (session, frame_no) -> [(cx, cy), ...] bee boxes
    stats = defaultdict(int)
    for cls_dir in sorted((DATA / "traindata").iterdir()):
        for f in sorted(cls_dir.glob("*.png")):
            m = CROP_RE.match(f.name)
            if not m:
                stats["unparsed"] += 1
                continue
            crop = np.asarray(Image.open(f).convert("RGB")).astype(np.int16)
            session = resolve_session(store, m, crop)
            if session is None:
                stats["unresolved"] += 1
                continue
            key = (session, int(m["frame"]))
            frames.setdefault(key, [])
            if cls_dir.name != "background":
                frames[key].append((int(m["x"]), int(m["y"])))
                stats["boxes"] += 1
            stats[f"crops_{cls_dir.name}"] += 1
    print("crop resolution:", dict(stats))

    keys = sorted(frames)
    random.Random(SEED).shuffle(keys)
    n_val = int(len(keys) * VAL_FRACTION)
    split = {k: ("val" if i < n_val else "train") for i, k in enumerate(keys)}

    yolo = DATA / "yolo"
    if yolo.exists():
        shutil.rmtree(yolo)
    for part in ("train", "val"):
        (yolo / "images" / part).mkdir(parents=True)
        (yolo / "labels" / part).mkdir(parents=True)

    per_split = defaultdict(lambda: [0, 0, 0])   # images, boxes, negatives
    for (session, frame_no), boxes in frames.items():
        part = split[(session, frame_no)]
        src, img = store.get(session, frame_no)
        h, w = img.shape[:2]
        stem = f"{session}_bee_{frame_no:04d}"
        shutil.copy(src, yolo / "images" / part / f"{stem}.jpg")
        lines = []
        for cx, cy in boxes:
            x0, y0 = max(cx - BOX / 2, 0), max(cy - BOX / 2, 0)
            x1, y1 = min(cx + BOX / 2, w), min(cy + BOX / 2, h)
            lines.append(f"0 {(x0 + x1) / 2 / w:.6f} {(y0 + y1) / 2 / h:.6f} {(x1 - x0) / w:.6f} {(y1 - y0) / h:.6f}")
        (yolo / "labels" / part / f"{stem}.txt").write_text("\n".join(lines) + ("\n" if lines else ""))
        s = per_split[part]
        s[0] += 1
        s[1] += len(boxes)
        s[2] += not boxes

    (DATA / "bees.yaml").write_text(
        f"path: {yolo.resolve()}\ntrain: images/train\nval: images/val\nnames:\n  0: bee\n")
    for part, (n_img, n_box, n_neg) in per_split.items():
        print(f"{part}: {n_img} images, {n_box} boxes, {n_neg} negative images")
    sessions = sorted({s for s, _ in frames})
    print("sessions with labels:", sessions)
    print(f"wrote {DATA / 'bees.yaml'}")


if __name__ == "__main__":
    main()
