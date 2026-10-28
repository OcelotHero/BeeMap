"""
Observed flowering months from iNaturalist (observations annotated
"Plant Phenology: Flowering", counted by month of the year).
Used by the server's on-demand fetch (ondemand.py) and tools/update_bloom_data.py.
Data: iNaturalist (www.inaturalist.org); only aggregated counts are stored.
"""
from __future__ import annotations

import datetime as dt
import json
import time
from pathlib import Path

import requests

import ecology

API = "https://api.inaturalist.org/v1"
OUT = Path(__file__).with_name("bloom_data.json")
HEADERS = {"User-Agent": "BeeMap hackathon prototype (bloom calendar; contact via project README)"}

# The scientific name to look up on iNaturalist, where the planner's label
# isn't a single species. None = skip (mixes and groups have no one calendar).
INAT_NAME = {
    "taraxacum": "Taraxacum officinale",
    "campanula": "Campanula rotundifolia",
    "rubus": "Rubus fruticosus",
    "regio_mix": None,
    "fruit": "Malus domestica",
}


class Api:
    def __init__(self, delay=1.1, session=None):
        self.s = session or requests.Session()
        self.s.headers.update(HEADERS)
        self.delay = delay
        self.last = 0.0

    def get(self, path: str, **params) -> dict:
        for attempt in range(4):
            wait = self.delay - (time.time() - self.last)
            if wait > 0:
                time.sleep(wait)
            self.last = time.time()
            try:
                r = self.s.get(f"{API}{path}", params=params, timeout=30)
                if r.status_code == 429:
                    time.sleep(10 * (attempt + 1))
                    continue
                r.raise_for_status()
                return r.json()
            except requests.RequestException as e:
                if attempt == 3:
                    raise
                print(f"   retrying after error: {e}")
                time.sleep(3 * (attempt + 1))
        raise RuntimeError("iNaturalist API kept refusing requests")


def find_place(api: Api, name: str) -> dict | None:
    res = api.get("/places/autocomplete", q=name, per_page=10).get("results", [])
    for p in res:
        if p.get("name", "").lower() == name.lower() or p.get("display_name", "").lower().startswith(name.lower()):
            return {"id": p["id"], "name": p.get("display_name") or p["name"]}
    return None


def find_taxon(api: Api, name: str) -> dict | None:
    """Exact scientific-name match only: never guess a different plant."""
    res = api.get("/taxa", q=name, is_active="true", per_page=10).get("results", [])
    for t in res:
        if t.get("name", "").lower() == name.lower():
            return {"id": t["id"], "name": t["name"], "rank": t.get("rank")}
    return None


def flowering_by_month(api: Api, taxon_id: int, place_id: int) -> list[int]:
    j = api.get("/observations/histogram", taxon_id=taxon_id, place_id=place_id, term_id=12,
                term_value_id=13, interval="month_of_year", date_field="observed", verifiable="true")
    moy = j.get("results", {}).get("month_of_year", {})
    return [int(moy.get(str(m), 0)) for m in range(1, 13)]


def summarise(counts: list[int]) -> dict:
    """Bloom months = months with at least 20% of the peak month's flowering
    observations; peak months = at least 60%."""
    top = max(counts) or 1
    months = [m + 1 for m, c in enumerate(counts) if c >= 0.2 * top]
    peak = [m + 1 for m, c in enumerate(counts) if c >= 0.6 * top]
    return {"months": months, "peak": peak, "share": [round(c / top, 3) for c in counts]}




def header(min_obs: int) -> dict:
    return {"source": "iNaturalist API v1, observations annotated 'Plant Phenology: Flowering', counted by month",
            "attribution": "Data: iNaturalist (www.inaturalist.org); aggregated counts of community observations",
            "fetched": None, "min_obs": min_obs, "rule": "bloom = months with >=20% of the peak month",
            "plants": {}, "checked": {}}


def fetch_plant(api, p, places: list[dict], min_obs: int) -> dict | None:
    """Observed calendar for one plant, or None (mix, unknown name, too few photos)."""
    name = INAT_NAME.get(p.id, p.latin)
    if not name:
        return None
    taxon = find_taxon(api, name)
    if not taxon:
        return None
    for place in places:
        counts = flowering_by_month(api, taxon["id"], place["id"])
        if sum(counts) >= min_obs:
            return {"taxon_id": taxon["id"], "taxon": taxon["name"], "place": place["name"], "place_id": place["id"],
                    "n_obs": sum(counts), "by_month": counts, **summarise(counts)}
    return None


def save(out: Path, data: dict):
    tmp = Path(str(out) + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False))
    tmp.replace(out)
