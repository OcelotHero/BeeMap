"""OpenStreetMap import against a mock Overpass response (documented 'out geom' JSON)."""
import json, os, sys, tempfile
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools")); sys.path.insert(0, os.path.join(ROOT, "server"))
import update_osm_data as U  # noqa: E402


def sq(lat, lon, d, close=True):
    pts = [(lat, lon), (lat, lon + d), (lat + d, lon + d), (lat + d, lon)]
    if close:
        pts.append((lat, lon))
    return [{"lat": a, "lon": b} for a, b in pts]


def way(i, tags, geom):
    return {"type": "way", "id": i, "tags": tags, "geometry": geom}


def mock():
    F = (49.130, 9.270)            # forest square 1.5 km, outline split into two ways
    big = sq(*F, 0.014)
    half1, half2 = big[:3], big[2:]
    hole = sq(F[0] + 0.005, F[1] + 0.005, 0.003)
    return {"elements": [
        way(1, {"landuse": "orchard", "name": "Streuobstwiese Test"}, sq(49.150, 9.160, 0.004)),
        {"type": "relation", "id": 10, "tags": {"type": "multipolygon", "landuse": "forest", "name": "Stadtwald"},
         "members": [{"type": "way", "ref": 2, "role": "outer", "geometry": half1},
                     {"type": "way", "ref": 3, "role": "outer", "geometry": half2},
                     {"type": "way", "ref": 4, "role": "inner", "geometry": hole}]},
        way(5, {"landuse": "meadow"}, hole),
        way(6, {"barrier": "hedge"}, [{"lat": 49.110, "lon": 9.150}, {"lat": 49.110, "lon": 9.170}]),
        way(7, {"natural": "tree_row"}, [{"lat": 49.140, "lon": 9.210}, {"lat": 49.140, "lon": 9.225}]),
        way(8, {"leisure": "nature_reserve", "name": "NSG Test"}, sq(49.100, 9.200, 0.004)),
        way(9, {"boundary": "protected_area", "protect_class": "5", "name": "LSG riesig"}, sq(49.09, 9.13, 0.09)),
        way(11, {"landuse": "farmland", "crop": "rape"}, sq(49.170, 9.250, 0.004)),
        {"type": "node", "id": 12, "lat": 49.1330, "lon": 9.1830, "tags": {"place": "suburb", "name": "Böckingen"}},
        way(13, {"landuse": "residential"}, sq(49.1500, 9.2900, 0.0001)),        # too small: dropped
    ]}


def test_osm():
    out = os.path.join(tempfile.mkdtemp(), "osm.json")
    res = U.main(["--out", out], data=mock())
    kinds = {f["kind"]: f for f in res["landcover"]}
    assert set(kinds) >= {"orchard", "forest", "meadow", "rapeseed"} and "urban" not in kinds
    forest = kinds["forest"]["polys"]
    assert len(forest) == 1 and len(forest[0]["inner"]) == 1, "split outline joined, clearing kept as a hole"
    assert [z["name"] for z in res["wild_bee_zones"]] == ["NSG Test"], "nature reserve yes, landscape area no"
    assert {l["kind"] for l in res["lines"]} == {"hedge", "tree_row"}
    assert res["landcover"][-1]["kind"] != "arable"                                   # draw order: fine areas on top

    # the server uses it
    import geodata, ecology
    geodata.OSM_FILE = type(geodata.OSM_FILE)(out)
    src = geodata.load("osm")
    assert src["kind"] == "osm" and src["zones_from"] == "osm"
    ecology._world = None
    w = ecology.world()
    fi = w.cell(49.131, 9.271)                  # inside the forest
    hi = w.cell(49.1365, 9.2765)                # inside the clearing
    assert w.forest[fi] and not w.forest[hi] and w.landcover[hi] == w.kind_index["meadow"]
    assert w.wild[w.cell(49.102, 9.202)] and not w.wild[w.cell(49.12, 9.15)]
    hedge_cell = w.cell(49.110, 9.160)
    assert w.base_bloom[hedge_cell][3] > ecology.PROFILES["background"][3]          # the hedge adds April flowers
    assert ecology.place_name(49.1332, 9.1832) == "Böckingen"
    print("osm: split outlines joined, holes kept, nature reserves as wild-bee zones (not landscape areas), "
          "hedges as strips, OSM place names")
    geodata.load("demo"); ecology._world = None




def test_overpass_errors():
    """Overpass answers 200 with a 'remark' when a query times out; mirrors may be busy or send HTML."""
    import osm_import as O
    class R:
        def __init__(s, code, j=None, text=""): s.status_code, s._j, s.text = code, j, text
        def json(s):
            if s._j is None: raise ValueError
            return s._j
    answers = [R(200, {"remark": "runtime error: Query timed out in \"query\" at line 3 after 241 seconds.", "elements": []}),
               R(429, text="Too Many Requests"), R(200, None, "<html>busy</html>"), R(200, {"elements": [{"type": "node"}]})]
    calls = []
    real_post, real_fetch = O.requests.post, O.fetch
    O.requests.post = lambda url, **k: (calls.append(url), answers[len(calls) - 1])[1]
    try:
        assert O.fetch("q") == {"elements": [{"type": "node"}]} and len(calls) == 4
        O.fetch = lambda q: {"elements": []}
        try:
            O.fetch_and_save(out=os.path.join(tempfile.mkdtemp(), "x.json"))
            raise AssertionError("an empty answer must not replace the map data")
        except SystemExit:
            pass
    finally:
        O.requests.post, O.fetch = real_post, real_fetch
    print("osm: Overpass timeouts hidden in 200 answers, busy mirrors and empty answers are all caught")


if __name__ == "__main__":
    test_osm()
    test_overpass_errors()
