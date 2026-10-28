"""
Checking nest photos: a quality gate, then an optional image classifier.

Quality gate (always on): is the photo big, sharp and well exposed enough for
a model, or for a person, to judge? Poor photos go straight to a manual check.

Classifier (plug-in): an ONNX image-classification model at
server/models/nest_classifier.onnx (or BEEMAP_NEST_MODEL), with its class
names one per line in a .labels.txt next to it (default order below).
Input: 1x3x224x224 float32 RGB, ImageNet-normalised. Output: one score per
class (softmax applied if the scores aren't already probabilities).
Needs `pip install onnxruntime`. Without a model, every report is checked by
hand. BeeMap ships no trained model: one has to be trained on labelled nest
photos (see the README).
"""
from __future__ import annotations

import io
import os
from pathlib import Path

import numpy as np

DEFAULT_LABELS = ["asian_hornet_nest", "european_hornet_nest", "wasp_nest", "not_a_nest"]
LABEL_NAMES = {"asian_hornet_nest": "Asian hornet nest", "european_hornet_nest": "European hornet nest",
               "wasp_nest": "Wasp nest", "not_a_nest": "Not a nest"}
MODEL_PATH = Path(os.environ.get("BEEMAP_NEST_MODEL", Path(__file__).with_name("models") / "nest_classifier.onnx"))

# Quality gate (rules of thumb for phone photos)
MIN_SHORT_SIDE = 480          # pixels
MIN_SHARPNESS = 60.0          # variance of the Laplacian on a 512 px greyscale copy
BRIGHTNESS = (35.0, 225.0)    # mean grey level
# Decisions
AUTO_CONFIRM = 0.90           # P(Asian hornet nest) needed to confirm from photos alone
AUTO_CONFIRM_CORROBORATED = 0.75   # ... when the spot also fits the smart hives' flight lines


def _open(raw: bytes):
    from PIL import Image, ImageOps
    im = Image.open(io.BytesIO(raw))
    return ImageOps.exif_transpose(im).convert("RGB")


def quality(raw: bytes) -> dict:
    """Is this photo good enough to judge? {ok, issues[], width, height, sharpness, brightness}"""
    from PIL import Image
    im = _open(raw)
    w, h = im.size
    g = im.convert("L")
    s = 512 / min(w, h)
    if s < 1:
        g = g.resize((max(1, int(w * s)), max(1, int(h * s))), Image.BILINEAR)
    a = np.asarray(g, dtype=np.float32)
    lap = (a[1:-1, :-2] + a[1:-1, 2:] + a[:-2, 1:-1] + a[2:, 1:-1] - 4 * a[1:-1, 1:-1])
    sharp, bright = float(lap.var()), float(a.mean())
    issues = []
    if min(w, h) < MIN_SHORT_SIDE:
        issues.append("too small")
    if sharp < MIN_SHARPNESS:
        issues.append("too blurry")
    if bright < BRIGHTNESS[0]:
        issues.append("too dark")
    if bright > BRIGHTNESS[1]:
        issues.append("too bright")
    return {"ok": not issues, "issues": issues, "width": w, "height": h,
            "sharpness": round(sharp, 1), "brightness": round(bright, 1)}


# ------------------------------------------------------------------ the model
_session = None
_labels: list[str] = DEFAULT_LABELS


def available() -> tuple[bool, str]:
    if not MODEL_PATH.exists():
        return False, "no image model installed"
    try:
        import onnxruntime  # noqa: F401
    except ImportError:
        return False, "onnxruntime not installed"
    return True, MODEL_PATH.name


def _load():
    global _session, _labels
    if _session is None:
        import onnxruntime as ort
        _session = ort.InferenceSession(str(MODEL_PATH), providers=["CPUExecutionProvider"])
        lf = MODEL_PATH.with_suffix(".labels.txt")
        _labels = [l.strip() for l in lf.read_text().splitlines() if l.strip()] if lf.exists() else DEFAULT_LABELS
    return _session


def preprocess(raw: bytes) -> np.ndarray:
    from PIL import Image
    im = _open(raw)
    w, h = im.size
    s = 256 / min(w, h)
    im = im.resize((max(224, round(w * s)), max(224, round(h * s))), Image.BILINEAR)
    w, h = im.size
    l, t = (w - 224) // 2, (h - 224) // 2
    a = np.asarray(im.crop((l, t, l + 224, t + 224)), dtype=np.float32) / 255.0
    a = (a - np.array([0.485, 0.456, 0.406], np.float32)) / np.array([0.229, 0.224, 0.225], np.float32)
    return a.transpose(2, 0, 1)[None].astype(np.float32)


def predict(raw: bytes) -> dict[str, float]:
    """{label: probability} for one photo."""
    sess = _load()
    out = np.asarray(sess.run(None, {sess.get_inputs()[0].name: preprocess(raw)})[0], dtype=np.float64).reshape(-1)
    if out.min() < 0 or abs(out.sum() - 1) > 1e-3:
        e = np.exp(out - out.max())
        out = e / e.sum()
    return {lab: round(float(p), 4) for lab, p in zip(_labels, out)}


# ------------------------------------------------------------------ decision
def assess(photos: list[bytes], predictor=None) -> dict:
    """What the photos say. Confirms an Asian hornet nest only when a good
    photo makes the model at least AUTO_CONFIRM sure. Never rules a nest out
    by itself: a missed nest costs more than a visit, so those go to a person."""
    checks = [quality(p) for p in photos]
    good = [p for p, q in zip(photos, checks) if q["ok"]]
    res = {"photos": checks, "model": None, "decision": "manual",
           "reason": "No photo: needs a check on site." if not photos else ""}
    if photos and not good:
        res["reason"] = "Photo " + " and ".join(sorted({i for q in checks for i in q["issues"]})) + ": needs a check by a person."
        return res
    if not good:
        return res
    if predictor is None:
        ok, why = available()
        if not ok:
            res["reason"] = f"Good photo, but {why}: needs a check by a person."
            return res
        predictor = predict
    probs = [predictor(p) for p in good]
    best = max(probs, key=lambda d: d.get("asian_hornet_nest", 0))
    top = max(best, key=best.get)
    res["model"] = {"probs": best, "top": top, "p_asian": best.get("asian_hornet_nest", 0.0), "name": MODEL_PATH.name}
    if res["model"]["p_asian"] >= AUTO_CONFIRM:
        res["decision"] = "asian"
        res["reason"] = f"Photo check: Asian hornet nest ({res['model']['p_asian'] * 100:.0f}% sure)."
    else:
        res["reason"] = (f"Photo check: probably {LABEL_NAMES.get(top, top).lower()} "
                         f"({best[top] * 100:.0f}%), not sure enough: needs a check by a person.")
    return res
