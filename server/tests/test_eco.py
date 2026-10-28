"""Pollinator features: spot check, privacy of the public site, plant planner, plantings."""
import os, sys, tempfile
os.environ["BEEMAP_GEODATA"] = "demo"
os.environ["BEEMAP_ONLINE"] = "off"
os.environ["BEEMAP_DATA"] = tempfile.mkdtemp()
os.environ["BEEMAP_ADMIN_KEY"] = "secret"
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from fastapi.testclient import TestClient  # noqa: E402
import app as server  # noqa: E402
import ecology, geodata  # noqa: E402

A = {"X-Admin-Key": "secret"}


def test_eco():
    with TestClient(server.app) as c:
        # the design's example: 2 colonies near Wertwiesenpark -> amber, with a green spot nearby
        s = c.get("/api/eco/spot", params={"lat": 49.1280, "lon": 9.2120, "colonies": 2}).json()
        keys = {k["key"]: k["status"] for k in s["checks"]}
        assert s["verdict"] == "amber" and keys["wild"] == "bad" and keys["apiaries"] == "bad"
        assert s["alternative"] and s["alternative"]["verdict"] == "green"
        alt = c.get("/api/eco/spot", params={"lat": s["alternative"]["lat"], "lon": s["alternative"]["lon"]}).json()
        assert alt["verdict"] == "green"
        print(f"eco: Wertwiesenpark amber, green spot {s['alternative']['distance_m']:.0f} m {s['alternative']['direction']}")

        # inside a wild-bee zone is always red
        z = geodata.WILD_BEE_ZONES[1]["polys"][0]["outer"]
        la, lo = sum(p[0] for p in z) / len(z), sum(p[1] for p in z) / len(z)
        assert c.get("/api/eco/spot", params={"lat": la, "lon": lo}).json()["verdict"] == "red"

        # public site: no personal data, no requests, colony health only for admins
        lay = c.get("/api/eco/layers").json()
        for ap in lay["apiaries"]:
            assert "owner" not in ap
            if not ap["camera"]:
                assert ap["lat"] == round(ap["lat"], 3) and ap["lon"] == round(ap["lon"], 3)
        assert all("owner" not in s and "token" not in s and "live_image" not in s for s in c.get("/api/stations").json())
        assert c.get("/api/requests").status_code == 404
        assert c.get("/api/admin/health").status_code == 401
        assert c.get("/api/admin/health", headers=A).status_code == 200
        print("eco: public data has no owners or tokens, apiaries rounded to ~100 m; requests gone; colony health admin-only")

        # plant planner: early gap gets early bloomers; farm gets crops; plantings count
        p = c.get("/api/eco/plants", params={"lat": 49.133, "lon": 9.183, "setting": "garden"}).json()
        assert 3 in p["gap_months"] and p["fills_gap"][0]["months"][0] <= 4
        f = c.get("/api/eco/plants", params={"lat": 49.145, "lon": 9.150, "setting": "farm"}).json()
        assert any(x["kind"] in ("cover crop", "annual crop", "seed mix") for x in f["fills_gap"] + f["also_good"])
        n = c.get("/api/eco/plants", params={"lat": 49.133, "lon": 9.183, "setting": "balcony", "natives_only": True}).json()
        assert all(x["native"] for x in n["fills_gap"] + n["also_good"])
        assert c.post("/api/eco/plantings", json={"lat": 49.133, "lon": 9.183, "plant_id": "salix", "area_m2": 25}).status_code == 200
        p2 = c.get("/api/eco/plants", params={"lat": 49.133, "lon": 9.183, "setting": "garden"}).json()
        assert p2["planted_m2"] == p["planted_m2"] + 25
        assert c.post("/api/eco/plantings", json={"lat": 49.1, "lon": 9.2, "plant_id": "nope", "area_m2": 1}).status_code == 400
        print(f"eco: planner gap {p['gap_months']} -> {[x['name'] for x in p['fills_gap'][:3]]}; farm -> {[x['name'] for x in f['fills_gap'][:3]]}")

        g = c.get("/api/eco/grid", params={"kind": "hive"}).json()
        assert g["rows"] * g["cols"] == len(g["data"]) and set(g["data"]) <= {0, 1, 2, 3}
        print("eco: city-wide safe-area and gap grids OK")


if __name__ == "__main__":
    test_eco()
