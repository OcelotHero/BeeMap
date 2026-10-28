"""Bloom-data updater against a mock iNaturalist API (same response shapes as
the documented /places/autocomplete, /taxa and /observations/histogram)."""
import json, os, sys, tempfile
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools")); sys.path.insert(0, os.path.join(ROOT, "server"))
import update_bloom_data as U  # noqa: E402
import ecology  # noqa: E402

BW, DE = 1001, 1002
# goat willow: plenty in BW; lungwort: only enough Germany-wide; knautia: name lookup returns a different taxon
HIST = {
    ("Salix caprea", BW): [0, 2, 40, 55, 6, 0, 0, 0, 0, 0, 0, 0],
    ("Pulmonaria officinalis", BW): [0, 1, 5, 4, 1, 0, 0, 0, 0, 0, 0, 0],
    ("Pulmonaria officinalis", DE): [0, 3, 60, 80, 20, 1, 0, 0, 0, 0, 0, 0],
}


class FakeResp:
    def __init__(self, j): self.j, self.status_code = j, 200
    def json(self): return self.j
    def raise_for_status(self): pass


class FakeSession:
    headers = {}
    def __init__(self): self.calls = []; self.taxa = {}
    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params))
        if url.endswith("/places/autocomplete"):
            q = params["q"]
            return FakeResp({"results": [{"id": BW if q.startswith("Baden") else DE, "name": q, "display_name": f"{q}, DE"}]})
        if url.endswith("/taxa"):
            q = params["q"]
            if q == "Knautia arvensis":     # API returns something else first: must NOT be used
                return FakeResp({"results": [{"id": 9, "name": "Knautia dipsacifolia", "rank": "species"}]})
            tid = self.taxa.setdefault(q, 500 + len(self.taxa))
            return FakeResp({"results": [{"id": tid, "name": q, "rank": "species"}]})
        if url.endswith("/observations/histogram"):
            assert params["term_id"] == 12 and params["term_value_id"] == 13 and params["interval"] == "month_of_year"
            name = next(n for n, t in self.taxa.items() if t == params["taxon_id"])
            counts = HIST.get((name, params["place_id"]), [0] * 12)
            return FakeResp({"total_results": 12, "results": {"month_of_year": {str(m + 1): c for m, c in enumerate(counts)}}})
        raise AssertionError(url)


def test_updater():
    out = os.path.join(tempfile.mkdtemp(), "bloom.json")
    api = U.Api(delay=0, session=FakeSession())
    res = U.main(["--out", out], api=api)
    d = json.load(open(out))["plants"]
    assert d["salix"]["months"] == [3, 4] and d["salix"]["place"].startswith("Baden")
    assert d["pulmonaria"]["place"].startswith("Germany") and d["pulmonaria"]["months"] == [3, 4, 5]
    assert "knautia" not in d, "a different species must never be substituted"
    assert "regio_mix" not in d
    # the app uses it, and says where it came from
    ecology.BLOOM_FILE = type(ecology.BLOOM_FILE)(out); ecology.load_bloom()
    b = ecology.bloom_of(ecology.PLANTS_BY_ID["salix"])
    assert b["source"] == "observed" and b["n_obs"] == 103
    assert ecology.bloom_of(ecology.PLANTS_BY_ID["knautia"])["source"] == "curated"
    print("bloom: updater parses iNaturalist histograms, falls back BW -> Germany, never swaps species; app uses observed months")


if __name__ == "__main__":
    test_updater()
