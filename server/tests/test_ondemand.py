"""On-demand fetching: land cover and bloom times arrive without a manual step."""
import os, sys, tempfile, time
TMP = tempfile.mkdtemp()
os.environ["BEEMAP_DATA"] = TMP
os.environ["BEEMAP_ONLINE"] = "off"          # nothing starts by itself during app start-up
os.environ.pop("BEEMAP_GEODATA", None)
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE)); sys.path.insert(0, HERE)
from pathlib import Path  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
import app as server, ondemand, geodata, ecology, osm_import, bloom_import  # noqa: E402
from test_osm import mock as osm_mock  # noqa: E402
from test_bloom import FakeSession  # noqa: E402

geodata.OSM_FILE = osm_import.OUT = Path(TMP) / "osm_data.json"
ecology.BLOOM_FILE = Path(TMP) / "bloom_data.json"
geodata.load("demo"); ecology._world = None; ecology.load_bloom()


def test_ondemand():
    with TestClient(server.app) as c:
        st = c.get("/api/eco/status").json()
        assert st["osm"]["state"] == "off" and st["osm"]["source"] == "demo"
        assert not ondemand._threads, "offline: nothing fetched"
        assert ondemand.osm_needed() and len(ondemand.bloom_todo()) == len(ecology.PLANTS)

        # OpenStreetMap arrives and is swapped into the running model
        old = ecology.world()
        ondemand.fetch_osm(data=osm_mock())
        assert ondemand.STATUS["osm"]["state"] == "ready" and geodata.SOURCE["kind"] == "osm"
        w = ecology.world()
        assert w is not old and w.forest[w.cell(49.131, 9.271)]
        assert not ondemand.osm_needed()
        lay = c.get("/api/eco/layers").json()
        assert lay["source"]["kind"] == "osm" and lay["wild_bee_zones"][0]["name"] == "NSG Test"
        # the land-cover layer only sends what's in view
        allf = c.get("/api/eco/landcover").json()["features"]
        view = c.get("/api/eco/landcover", params={"bbox": "49.12,9.25,49.15,9.29"}).json()["features"]
        assert len(view) < len(allf) and {f["kind"] for f in view} >= {"forest"}
        print(f"ondemand: OpenStreetMap fetched and swapped in; land-cover layer sends {len(view)} of {len(allf)} shapes for the view")

        # iNaturalist, plant by plant, saved as it goes; a second run skips done plants
        api = bloom_import.Api(delay=0, session=FakeSession())
        ondemand.fetch_bloom(api=api)
        assert ondemand.STATUS["bloom"]["state"] == "ready"
        assert ecology.bloom_of(ecology.PLANTS_BY_ID["salix"])["source"] == "observed"
        st = c.get("/api/eco/status").json()
        assert st["bloom"]["checked"] == len(ecology.PLANTS) and st["bloom"]["observed"] >= 2
        n_calls = len(api.s.calls)
        ondemand.fetch_bloom(api=api)
        assert len(api.s.calls) == n_calls, "nothing left to fetch"
        p = c.get("/api/eco/plants", params={"lat": 49.133, "lon": 9.183}).json()
        assert any(x["bloom_source"] == "observed" for x in p["fills_gap"] + p["also_good"])
        print(f"ondemand: iNaturalist bloom times fetched plant by plant ({st['bloom']['observed']} observed), not refetched")

        # a failed fetch is retried later, not on every request
        os.environ["BEEMAP_ONLINE"] = "on"
        geodata.OSM_FILE.unlink(); ondemand.STATUS["osm"] = {"state": "idle"}
        def boom(*a, **k): raise SystemExit("no internet")
        osm_import.fetch = boom
        ondemand.kick(); ondemand._threads["osm"].join(5)
        assert ondemand.STATUS["osm"]["state"] == "failed"
        t = ondemand._threads["osm"]
        ondemand.kick()
        assert ondemand._threads["osm"] is t, "no retry within RETRY_S"
        assert geodata.SOURCE["kind"] == "osm", "keeps the last good data"
        print("ondemand: failed fetch keeps the last data and waits before retrying")
        os.environ["BEEMAP_ONLINE"] = "off"


if __name__ == "__main__":
    test_ondemand()
