"""End-to-end tests of the BeeMap server API (no network, temp database)."""
import json
import math
import os
os.environ["BEEMAP_GEODATA"] = "demo"
os.environ["BEEMAP_ONLINE"] = "off"
import sys
import tempfile
import time

TMP = tempfile.mkdtemp()
os.environ["BEEMAP_DATA"] = TMP
os.environ["BEEMAP_ADMIN_KEY"] = "secret"
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from fastapi.testclient import TestClient  # noqa: E402

import app as server  # noqa: E402

A = {"X-Admin-Key": "secret"}
NEST = (49.1155, 9.2035)


def bearing(lat, lon, nlat, nlon):
    kx = math.cos(math.radians(lat)) * 111320
    return math.degrees(math.atan2((nlon - lon) * kx, (nlat - lat) * 111320)) % 360


def dist(a, b):
    kx = math.cos(math.radians(a[0])) * 111320
    return math.hypot((b[1] - a[1]) * kx, (b[0] - a[0]) * 111320)


def add(c, sid, lat, lon, heading=0, planned=False):
    r = c.post("/api/stations", headers=A, json={"id": sid, "name": sid, "lat": lat, "lon": lon,
                                                  "heading_deg": heading, "planned": planned})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def depart(c, sid, token, exit_angle, eid, heading_cam=False):
    meta = {"event_id": eid, "ts": time.time(), "confidence": 0.95,
            "straightness": 0.95, "track": [[0, .5, .5], [.1, .6, .4]]}
    if heading_cam:
        meta.update(heading_rel_deg=exit_angle, heading_sigma_deg=6, heading_source="heading_camera")
    else:
        meta["exit_angle_img"] = exit_angle
    return c.post(f"/api/stations/{sid}/detections", headers={"X-Station-Token": token},
                  data={"meta": json.dumps(meta)})


def settle():
    server.recompute_nests()


def test_everything():
    with TestClient(server.app) as c:
        # auth
        assert c.post("/api/stations", json={"id": "x1", "name": "x"}).status_code == 401
        t1 = add(c, "h1", 49.1250, 9.1930, heading=40)
        assert depart(c, "h1", "wrong", 10, "e0").status_code == 401

        # a real Pi with a fixed token (the pitch demo registers it on every fresh database)
        fixed = "real-pi-token-0123456789"
        assert c.post("/api/stations", headers=A, json={"id": "realpi", "name": "r", "token": "short"}).status_code == 422
        r = c.post("/api/stations", headers=A, json={"id": "realpi", "name": "r", "lat": 49.146, "lon": 9.212, "token": fixed})
        assert r.status_code == 200 and r.json()["token"] == fixed, r.text
        assert c.post("/api/stations/realpi/heartbeat", headers={"X-Station-Token": fixed}).status_code == 200
        assert depart(c, "realpi", fixed, 10, "real-e0").status_code == 200
        # same token after re-registering, but a new created time (beemap_sync keys its progress on it)
        first = c.get("/api/stations/realpi/token", headers=A).json()
        c.delete("/api/stations/realpi", headers=A)
        c.post("/api/stations", headers=A, json={"id": "realpi", "name": "r", "lat": 49.146, "lon": 9.212, "token": fixed})
        again = c.get("/api/stations/realpi/token", headers=A).json()
        assert again["token"] == first["token"] == fixed and again["created"] > first["created"], (first, again)
        c.delete("/api/stations/realpi", headers=A)

        # nest reports from the public: on by default, the city can switch them off
        assert c.get("/api/settings").json()["reports"] is True
        assert c.patch("/api/settings", json={"reports": False}).status_code == 401
        assert c.patch("/api/settings", headers=A, json={"reports": False}).json()["reports"] is False
        assert c.post("/api/nest-reports", data={"lat": 49.12, "lon": 9.19}).status_code == 403
        c.patch("/api/settings", headers=A, json={"reports": True})
        assert c.post("/api/sightings", data={"lat": 49.12, "lon": 9.19}).status_code in (404, 405)
        assert c.post("/api/nest-reports", data={"lat": 49.12, "lon": 9.19, "location": "moon"}).status_code == 400
        assert c.post("/api/nest-reports", data={"lat": 52.5, "lon": 13.4}).status_code == 400          # not Heilbronn
        import io
        from PIL import Image
        img = Image.new("RGB", (2400, 1800), (200, 120, 40))
        exif = Image.Exif(); exif[0x8825] = {2: (49.0, 7.0, 30.0), 1: "N"}; exif[0x0110] = "Pixel 9"
        buf = io.BytesIO(); img.save(buf, "JPEG", exif=exif)
        r = c.post("/api/nest-reports", data={"lat": 49.115712, "lon": 9.203144, "location": "tree", "height": "above-5m",
                                              "size": "bigger", "note": "In the oak behind Mrs Example's house"},
                   files={"image": ("nest.jpg", buf.getvalue(), "image/jpeg")})
        assert r.status_code == 200, r.text
        pub = r.json(); rid = pub["id"]
        assert pub["status"] == "pending" and pub["lat"] == 49.116 and pub["lon"] == 9.203
        assert "note" not in pub and "image" not in pub and pub["has_photo"]
        listed = c.get("/api/nest-reports").json()[0]
        assert "note" not in listed and "photo_path" not in listed
        # the photo is private, cleaned and smaller; only the admin list points to it
        assert not any(server.IMAGES.glob("nest_*"))
        adm = c.get("/api/admin/nest-reports", headers=A).json()[0]
        assert adm["lat"] == 49.115712 and adm["note"].startswith("In the oak")
        saved = open(adm["photo_path"], "rb").read()
        assert b"Pixel 9" not in saved and not Image.open(io.BytesIO(saved)).getexif() and max(Image.open(io.BytesIO(saved)).size) <= 1600
        assert c.get("/api/admin/nest-reports").status_code == 401
        # the city confirms what it is
        assert c.patch(f"/api/admin/nest-reports/{rid}", json={"status": "asian_hornet"}).status_code == 401
        assert c.patch(f"/api/admin/nest-reports/{rid}", headers=A, json={"status": "other_species", "species": "European hornet"}).json()["species"] == "European hornet"
        assert c.get("/api/nest-reports").json()[0]["status"] == "other_species"
        assert c.get("/api/summary").json()["nest_reports_pending"] == 0
        print("api: nest reports on by default; public sees ~100 m, no note or photo; photo private and clean; city confirms species")

        # camera heading turns on-image exit angles into compass bearings
        true = bearing(49.1250, 9.1930, *NEST)
        for k in range(8):
            r = depart(c, "h1", t1, (true - 40 + (k % 3 - 1) * 5) % 360, f"a{k}")
            assert r.status_code == 200
            assert abs((r.json()["bearing_deg"] - true + 180) % 360 - 180) <= 6
        # a retried upload is not counted twice
        assert depart(c, "h1", t1, 0, "a0").json().get("duplicate")
        settle()

        # one hive: a corridor, no nest, and the corridor starts at the hive
        assert c.get("/api/nests").json() == []
        plan = c.get("/api/planning").json()
        cor = [t for t in plan["targets"] if t["kind"] == "corridor"]
        assert len(cor) == 1
        o = cor[0]["origin"]
        assert dist((o["lat"], o["lon"]), (49.1250, 9.1930)) < 1, "corridor drawn away from its hive"
        assert cor[0]["suggestions"], "no site suggested for a lone corridor"
        site = cor[0]["suggestions"][0]
        print("api: planner promised", site["radius_m"], "m, range", site["radius_low_m"], "-", site["radius_high_m"], "; corridor sigma", round(c.get("/api/lines").json()[0]["sigma"], 1), "n", c.get("/api/lines").json()[0]["n"])

        # what-if at the suggested site says it would help
        ev = c.post("/api/planning/evaluate", json={"lat": site["lat"], "lon": site["lon"]}).json()
        assert ev["effects"] and ev["effects"][0]["kind"] == "corridor"

        # register it as planned; it doesn't count as installed yet
        t2 = add(c, "h2", site["lat"], site["lon"], heading=0, planned=True)
        s = c.get("/api/summary").json()
        assert s["planned"] == 1 and s["stations"] == 1
        # first heartbeat from its Pi: installed
        r = c.post("/api/stations/h2/heartbeat", headers={"X-Station-Token": t2}, data={"status": "{}"})
        assert r.status_code == 200
        assert c.get("/api/summary").json()["planned"] == 0

        # its hornets turn the corridor into a located nest
        true2 = bearing(site["lat"], site["lon"], *NEST)
        for k in range(8):   # this hive has an external heading camera (reference 0°)
            r = depart(c, "h2", t2, (true2 + (k % 3 - 1) * 3) % 360, f"b{k}", heading_cam=True)
            assert r.status_code == 200
        d = c.get("/api/detections?station_id=h2").json()[0]
        assert d["heading_source"] == "heading_camera" and d["bearing_sigma"] == 6
        settle()
        nests = c.get("/api/nests").json()
        assert len(nests) == 1
        err = dist((nests[0]["lat"], nests[0]["lon"]), NEST)
        b1, b2 = bearing(49.1250, 9.1930, *NEST), bearing(site["lat"], site["lon"], *NEST)
        print("api: crossing angle", round(abs((b1 - b2 + 180) % 360 - 180)), "deg; site", round(dist((site["lat"], site["lon"]), NEST)), "m from nest; hive1", round(dist((49.125, 9.193), NEST)), "m")
        print(f"api: lone corridor -> planned hive -> nest located {err:.0f} m from truth, ± {nests[0]['radius_m']:.0f} m")
        assert err < 250

        # fixing a camera heading later recalculates its past bearings
        before = c.get("/api/detections?station_id=h1").json()[0]["bearing_deg"]
        c.patch("/api/stations/h1", headers=A, json={"heading_deg": 50})
        after = c.get("/api/detections?station_id=h1").json()[0]["bearing_deg"]
        assert abs(((after - before) % 360) - 10) < 1e-6

        # removing a nest takes its departures out of future estimates
        c.patch("/api/stations/h1", headers=A, json={"heading_deg": 40})
        settle()
        nid = c.get("/api/nests").json()[0]["id"]
        assert c.patch(f"/api/nests/{nid}", headers=A, json={"status": "removed"}).status_code == 200
        settle()
        assert c.get("/api/nests").json() == []
        assert c.get("/api/summary").json()["removed"] == 1
        print("api: auth, retries, planned->installed, heading fix, nest removal OK")


if __name__ == "__main__":
    test_everything()
