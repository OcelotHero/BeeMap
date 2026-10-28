"""SensoBox readings correct the forage map near the box, not far away."""
import os, sys, tempfile, time
os.environ["BEEMAP_GEODATA"] = "demo"
os.environ["BEEMAP_ONLINE"] = "off"
os.environ["BEEMAP_DATA"] = tempfile.mkdtemp()
os.environ["BEEMAP_ADMIN_KEY"] = "secret"
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from fastapi.testclient import TestClient  # noqa: E402
import app as server  # noqa: E402
import ecology  # noqa: E402

A = {"X-Admin-Key": "secret"}
HERE, FAR = (49.1330, 9.1830), (49.1100, 9.2700)


def day_readings(month_factor, typed=True, grass=0.0, days=365):
    w = ecology.world(); i, j = w.cell(*HERE)
    out, now = [], time.time()
    for d in range(days, 0, -1):
        ts = now - d * 86400
        m = time.localtime(ts).tm_mon
        bee = float(w.model_supply[i, j, m - 1]) * 400 * month_factor[m - 1]
        r = {"ts": ts, "temp_c": 15, "lux": 20000, "humidity": 60}
        if typed:
            r["pollen"] = {"salix": bee, "poaceae": grass}
        else:
            r["pollen_total"] = bee * 20 + 300   # mostly grass and birch in reality
        out.append(r)
    return out


def ratio(month):
    w = ecology.world(); i, j = w.cell(*HERE); a, b = w.cell(*FAR)
    return w.ratio[i, j, month], w.ratio[a, b, month]


def test_sensors():
    with TestClient(server.app) as c:
        base_here, base_far = ratio(2)
        tok = c.post("/api/sensoboxes", headers=A, json={"id": "sb1", "name": "Test", "lat": HERE[0], "lon": HERE[1]}).json()["token"]
        assert c.post("/api/sensoboxes/sb1/readings", json={"temp_c": 10}).status_code == 401
        # weather only: no change to the forage map
        r = c.post("/api/sensoboxes/sb1/readings", headers={"X-Box-Token": tok},
                   json={"ts": "2026-05-04T12:00:00Z", "temp_c": 17.2, "humidity": 61, "pressure_hpa": 1016, "lux": 42000, "uv_index": 4})
        assert r.status_code == 200 and r.json()["stored"] == 1
        server._sync_sensors()
        assert abs(ratio(2)[0] - base_here) < 1e-9
        # typed pollen: March measured at 40% of the rest of the year, lots of grass in it
        lean_march = [1, 1, .4, 1, 1, 1, 1, 1, 1, 1, 1, 1]
        c.post("/api/sensoboxes/sb1/readings", headers={"X-Box-Token": tok}, json={"readings": day_readings(lean_march, grass=500)})
        server._sync_sensors()
        here, far = ratio(2)
        assert here < base_here * 0.75, (here, base_here)       # March got leaner where it's measured
        assert abs(far - base_far) < 0.02                      # far away the model holds
        ev = ecology.world().evidence(*HERE, [2])
        assert ev["kind"] == "measured" and ev["vs_model"] == "less" and ev["typed"]
        p = c.get("/api/eco/plants", params={"lat": HERE[0], "lon": HERE[1]}).json()
        assert p["evidence"]["kind"] == "measured" and 3 in p["gap_months"]
        boxes = c.get("/api/sensoboxes").json()
        assert boxes[0]["typed"] and boxes[0]["monthly"][2] is not None and boxes[0]["flight_share_7d"] == 1.0
        print(f"sensors: lean March measured -> ratio {base_here:.2f} -> {here:.2f} at the box, unchanged 6 km away; planner says 'measured'")
        # grass pollen must not count as bee food: a box with only grass looks bare
        assert ecology.bee_pollen({"poaceae": 1000, "betula": 300}, None)[0] == 0 and ecology.bee_pollen({"salix": 100}, None)[0] == 100
        assert ecology.bee_pollen(None, 100) == (None, False)
        # a second box with only an untyped total does not move the map
        tok2 = c.post("/api/sensoboxes", headers=A, json={"id": "sb2", "name": "Untyped", "lat": FAR[0], "lon": FAR[1]}).json()["token"]
        c.post("/api/sensoboxes/sb2/readings", headers={"X-Box-Token": tok2}, json={"readings": day_readings([5] * 12, typed=False)})
        server._sync_sensors()
        assert abs(ratio(2)[1] - base_far) < 0.02
        assert next(b for b in c.get("/api/sensoboxes").json() if b["id"] == "sb2")["untyped_only"]
        print("sensors: grass and birch count zero; an untyped total is shown but not used; weather-only boxes leave the map alone")


if __name__ == "__main__":
    test_sensors()
