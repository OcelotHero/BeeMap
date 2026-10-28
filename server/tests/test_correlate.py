"""Nest reports correlated with triangulated nest areas and corridors, end to end."""
import io, json, math, os, sys, tempfile, time
TMP = tempfile.mkdtemp()
os.environ.update(BEEMAP_DATA=TMP, BEEMAP_ADMIN_KEY="secret", BEEMAP_ONLINE="off", BEEMAP_GEODATA="demo")
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import numpy as np  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
import app as server, nest_classifier as NC, correlate  # noqa: E402

A = {"X-Admin-Key": "secret"}
NEST = (49.1155, 9.2035)
HIVES = {"h1": (49.1250, 9.1930), "h2": (49.1060, 9.2160), "h3": (49.1215, 9.2180)}
LONE, LONE_NEST = (49.1490, 9.1830), (49.1590, 9.1930)


def bearing(a, b):
    kx = math.cos(math.radians(a[0])) * 111320
    return math.degrees(math.atan2((b[1] - a[1]) * kx, (b[0] - a[0]) * 111320)) % 360


def offset(p, b, d):
    r = math.radians(b)
    return p[0] + math.cos(r) * d / 111320, p[1] + math.sin(r) * d / (111320 * math.cos(math.radians(p[0])))


def dist(a, b):
    kx = math.cos(math.radians(a[0])) * 111320
    return math.hypot((b[1] - a[1]) * kx, (b[0] - a[0]) * 111320)


def good_photo():
    rng = np.random.default_rng(2)
    im = Image.fromarray((rng.random((1050, 1400, 3)) * 80 + 90).astype("uint8"))
    d = ImageDraw.Draw(im)
    for k in range(40):
        x, y = int(rng.integers(0, 1300)), int(rng.integers(0, 1000))
        d.ellipse((x, y, x + 60, y + 40), outline=(30, 25, 20), width=4)
    b = io.BytesIO(); im.save(b, "JPEG", quality=92); return b.getvalue()


def report(c, at, pic=None, size="football"):
    r = c.post("/api/nest-reports", data={"lat": at[0], "lon": at[1], "location": "tree", "height": "above-5m", "size": size},
               files={"image": ("n.jpg", pic, "image/jpeg")} if pic else None)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_correlate():
    with TestClient(server.app) as c:
        for sid, (la, lo) in {**HIVES, "h9": LONE}.items():
            tok = c.post("/api/stations", headers=A, json={"id": sid, "name": f"Hive {sid}", "lat": la, "lon": lo, "heading_deg": 0}).json()["token"]
            target = LONE_NEST if sid == "h9" else NEST
            for k in range(8):
                meta = {"event_id": f"{sid}{k}", "ts": time.time() - k * 600, "confidence": 0.95, "straightness": 0.95,
                        "heading_rel_deg": (bearing((la, lo), target) + (k % 3 - 1) * 3) % 360, "heading_sigma_deg": 6}
                assert c.post(f"/api/stations/{sid}/detections", headers={"X-Station-Token": tok}, data={"meta": json.dumps(meta)}).status_code == 200
        server.recompute_nests()
        nests = c.get("/api/nests").json()
        assert len(nests) == 1
        area = nests[0]
        print(f"correlate: nest area {dist((area['lat'], area['lon']), NEST):.0f} m from the true nest, ± {area['radius_m']:.0f} m")

        # a report at the nest fits; one 2 km away doesn't
        at_nest = report(c, offset(NEST, 30, 60))
        far = report(c, offset(NEST, 90, 2000))
        reps = {r["id"]: r for r in c.get("/api/nest-reports").json()}
        m = reps[at_nest]["match"]
        assert m and m["kind"] == "nest" and m["level"] in ("strong", "fits") and m["nest_id"] == area["id"]
        assert set(m["hives"]) == {"Hive h1", "Hive h2", "Hive h3"}
        assert reps[far]["match"] is None
        n = c.get("/api/nests").json()[0]
        assert n["reports"][0]["report_id"] == at_nest and not n["located"]
        adm = c.get("/api/admin/nest-reports", headers=A).json()
        assert adm[0]["id"] == at_nest, "reports the hives back up are checked first"
        print(f"correlate: report {m['dist_m']} m from the centre fits ({m['level']}), names the 3 hives; 2 km away doesn't")

        # a fairly sure photo is enough where the flight lines point, not elsewhere
        NC.available = lambda: (True, "stub")
        NC.predict = lambda raw: {"asian_hornet_nest": 0.80, "european_hornet_nest": 0.12, "wasp_nest": 0.05, "not_a_nest": 0.03}
        corroborated = report(c, offset(NEST, 200, 40), good_photo())
        lonely = report(c, offset(NEST, 270, 2500), good_photo())
        reps = {r["id"]: r for r in c.get("/api/nest-reports").json()}
        assert reps[corroborated]["status"] == "asian_hornet" and reps[corroborated]["verified_by"] == "photo+flight lines"
        assert reps[lonely]["status"] == "pending"
        print("correlate: 80% photo check confirms at the flight lines' spot ('photo+flight lines'); elsewhere it waits for a person")

        # the area is now located, and needs no more smart hives
        n = c.get("/api/nests").json()[0]
        assert n["located"] and dist((n["located_at"]["lat"], n["located_at"]["lon"]), NEST) < 200
        assert not any(t["kind"] == "nest" and t["nest_id"] == n["id"] for t in c.get("/api/planning").json()["targets"])

        # removing the found nest closes its area
        c.patch(f"/api/admin/nest-reports/{corroborated}", headers=A, json={"status": "removed"})
        assert c.get("/api/nests").json() == []
        assert c.get("/api/summary").json()["removed"] == 1
        print("correlate: located area drops out of hive planning; removing the reported nest closes the area")

        # a report along a lone hive's flight line matches its corridor
        lines = c.get("/api/lines").json()
        assert any(l["corridor"] and l["observer_id"] == "h9" for l in lines)
        on_line = report(c, offset(LONE, bearing(LONE, LONE_NEST), 1300))
        m = {r["id"]: r for r in c.get("/api/nest-reports").json()}[on_line]["match"]
        assert m and m["kind"] == "corridor" and m["hives"] == ["Hive h9"]
        late_small = correlate.match([], [l for l in lines if l["corridor"]],
                                     [{"id": 1, "ts": time.mktime((2026, 8, 20, 12, 0, 0, 0, 0, -1)), "status": "pending", "size": "fist",
                                       "lat": offset(LONE, bearing(LONE, LONE_NEST), 1300)[0], "lon": offset(LONE, bearing(LONE, LONE_NEST), 1300)[1]}],
                                     now=time.mktime((2026, 9, 1, 12, 0, 0, 0, 0, -1)))
        assert "old spring nest" in late_small["by_report"][1]["note"]
        print("correlate: report along a single hive's flight line matches its corridor; small late nests are flagged")


if __name__ == "__main__":
    test_correlate()
