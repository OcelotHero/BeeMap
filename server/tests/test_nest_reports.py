"""Nest reports: photo quality gate, image-model check (real ONNX Runtime with
tiny stand-in models), manual check, and what the public sees."""
import io, os, sys, tempfile
TMP = tempfile.mkdtemp()
os.environ.update(BEEMAP_DATA=TMP, BEEMAP_ADMIN_KEY="secret", BEEMAP_ONLINE="off", BEEMAP_GEODATA="demo")
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from pathlib import Path  # noqa: E402
import numpy as np  # noqa: E402
from PIL import Image, ImageDraw, ImageFilter  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
import app as server, nest_classifier as NC  # noqa: E402

A = {"X-Admin-Key": "secret"}
try:
    import onnx, onnxruntime  # noqa: F401  (only needed for the model part)
    HAS_ORT = True
except ImportError:
    HAS_ORT = False
SPOT = {"lat": 49.1301, "lon": 9.2042, "location": "tree", "height": "above-5m", "size": "football"}


def photo(kind="sharp"):
    rng = np.random.default_rng(1)
    w, h = (300, 200) if kind == "small" else (1400, 1050)
    im = Image.fromarray((rng.random((h, w, 3)) * 80 + 90).astype("uint8"))
    d = ImageDraw.Draw(im)
    for k in range(40):
        x, y = int(rng.integers(0, w)), int(rng.integers(0, h))
        d.ellipse((x, y, x + 60, y + 40), outline=(30, 25, 20), width=4)
    if kind == "blurry":
        im = im.filter(ImageFilter.GaussianBlur(9))
    if kind == "dark":
        im = Image.eval(im, lambda v: v // 8)
    b = io.BytesIO(); im.save(b, "JPEG", quality=92); return b.getvalue()


def tiny_model(path, logits):
    """1x3x224x224 -> average pool -> fixed logits -> softmax (answer doesn't depend on the photo)."""
    import onnx
    from onnx import TensorProto, helper
    W = helper.make_tensor("W", TensorProto.FLOAT, [3, 4], [0.0] * 12)
    B = helper.make_tensor("B", TensorProto.FLOAT, [4], logits)
    g = helper.make_graph(
        [helper.make_node("GlobalAveragePool", ["x"], ["p"]), helper.make_node("Flatten", ["p"], ["f"]),
         helper.make_node("Gemm", ["f", "W", "B"], ["z"]), helper.make_node("Softmax", ["z"], ["y"], axis=1)],
        "tiny", [helper.make_tensor_value_info("x", TensorProto.FLOAT, [1, 3, 224, 224])],
        [helper.make_tensor_value_info("y", TensorProto.FLOAT, [1, 4])], [W, B])
    m = helper.make_model(g, opset_imports=[helper.make_opsetid("", 13)])
    m.ir_version = 8                     # readable by any recent ONNX Runtime
    onnx.save(m, path)
    Path(path).with_suffix(".labels.txt").write_text("\n".join(NC.DEFAULT_LABELS))


def use_model(logits):
    p = Path(TMP) / f"m{abs(hash(tuple(logits)))}.onnx"
    tiny_model(str(p), logits)
    NC.MODEL_PATH, NC._session = p, None


def send(c, pic=None):
    files = {"image": ("nest.jpg", pic, "image/jpeg")} if pic else None
    r = c.post("/api/nest-reports", data=SPOT, files=files)
    assert r.status_code == 200, r.text
    return r.json()


def test_nest_reports():
    # quality gate
    assert NC.quality(photo())["ok"]
    assert "too blurry" in NC.quality(photo("blurry"))["issues"]
    assert "too small" in NC.quality(photo("small"))["issues"]
    assert "too dark" in NC.quality(photo("dark"))["issues"]
    print("nest: quality gate passes sharp photos, catches blurry, small and dark ones")

    with TestClient(server.app) as c:
        NC.MODEL_PATH, NC._session = Path(TMP) / "none.onnx", None
        r = send(c, photo())                                        # good photo, no model yet
        assert r["status"] == "pending" and "no image model installed" in r["check"]["reason"]
        waiting = r["id"]
        assert send(c)["check"]["reason"].startswith("No photo")

        if not HAS_ORT:
            print("nest: model checks skipped (pip install onnx onnxruntime to run them)")
            rid = send(c)["id"]
            c.patch(f"/api/admin/nest-reports/{rid}", headers=A, json={"status": "asian_hornet"})
            assert {x["id"]: x for x in c.get("/api/nest-reports").json()}[rid]["verified_by"] == "manual"
            return
        use_model([6.0, 0.0, 0.0, 0.0])                             # model: Asian hornet nest, ~99%
        probs = NC.predict(photo())
        assert abs(sum(probs.values()) - 1) < 1e-3 and probs["asian_hornet_nest"] > 0.95
        r = send(c, photo())
        assert r["status"] == "asian_hornet" and r["verified_by"] == "photo" and r["check"]["p_asian"] > 0.95
        r = send(c, photo("blurry"))                                # a sure model never sees a bad photo
        assert r["status"] == "pending" and "blurry" in r["check"]["reason"]
        print("nest: sure model + good photo confirms; blurry photo goes to a person; real ONNX Runtime used")

        use_model([0.0, 6.0, 0.0, 0.0])                             # model: European hornet nest
        r = send(c, photo())
        assert r["status"] == "pending" and "european hornet nest" in r["check"]["reason"]
        print("nest: 'not an Asian hornet' from the model never closes a report by itself")

        use_model([6.0, 0.0, 0.0, 0.0])                             # model installed later: re-check
        rc = c.post("/api/admin/nest-reports/recheck", headers=A).json()
        assert rc["confirmed"] >= 1
        pub = {x["id"]: x for x in c.get("/api/nest-reports").json()}
        assert pub[waiting]["status"] == "asian_hornet"

        # manual check on site
        rid = send(c)["id"]
        assert c.patch(f"/api/admin/nest-reports/{rid}", json={"status": "other_species"}).status_code == 401
        c.patch(f"/api/admin/nest-reports/{rid}", headers=A, json={"status": "other_species", "species": "European hornet"})
        pub = {x["id"]: x for x in c.get("/api/nest-reports").json()}
        assert pub[rid]["verified_by"] == "manual" and pub[rid]["species"] == "European hornet"

        # the public sees no photo, no note, position to ~100 m
        for x in pub.values():
            assert "image" not in x and "note" not in x and x["lat"] == round(x["lat"], 3)
        adm = c.get("/api/admin/nest-reports", headers=A).json()
        assert any(a["check"] and a["check"].get("model") for a in adm)
        print("nest: recheck after installing a model; manual check; public view has no photo, note or exact spot")


if __name__ == "__main__":
    test_nest_reports()
