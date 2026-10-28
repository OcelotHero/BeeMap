#!/usr/bin/env python3
"""Public sightings of the Asian hornet (Vespa velutina) around Heilbronn, from GBIF.

GBIF (gbif.org) aggregates citizen-science records from iNaturalist, naturgucker
(NABU) and Observation.org.  Every record is a human observation with a date and a
coordinate.  GBIF's `coordinateUncertaintyInMeters` is kept as `uncertainty_m`
(8-250 m for every Asian hornet record here; the "TK25 Blatt ..." text in
`locality` is only naturgucker's map-sheet label, not the precision).

For context the same box is queried for the native European hornet (Vespa crabro)
and the honey bee (Apis mellifera).  Nothing is modelled here: these are counts
of what people reported.

Outputs (./output)
  hornet_sightings.csv    one row per record (species, date, lat, lon, precision, source, locality)
  hornet_summary.json     counts per year and species, nearest Asian hornet to the hive, state-wide trend
  hornet_sightings.png    sightings per year, Heilbronn box and Baden-Württemberg
"""
import json
import re
import sys
import time
from pathlib import Path

import matplotlib
import pandas as pd
import requests

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import DATA, HIVE_LAT, HIVE_LON, OUTPUT, haversine_km  # noqa: E402

GBIF = "https://api.gbif.org/v1/occurrence/search"
BOX = {"lat": (49.05, 49.25), "lon": (9.05, 9.35)}        # ~22 km around Heilbronn
SPECIES = {"Vespa velutina": "Asian hornet", "Vespa crabro": "European hornet", "Apis mellifera": "Honey bee"}
STATE = "Baden-Württemberg"
HEADERS = {"User-Agent": "bee-commute-tracker (Green City Hackathon Heilbronn)"}
PAGE = 300
_publishers: dict[str, str] = {}


def publisher_name(key):
    """Records without an institutionCode (Observation.org) only carry the publisher's GBIF key."""
    if not key:
        return ""
    if key not in _publishers:
        try:
            r = requests.get(f"https://api.gbif.org/v1/organization/{key}", headers=HEADERS, timeout=30)
            _publishers[key] = (r.json().get("title") or key) if r.ok else key
        except requests.RequestException:
            _publishers[key] = key
    return _publishers[key]


def licence_name(url):
    """http://creativecommons.org/licenses/by-nc/4.0/legalcode -> 'CC BY-NC 4.0'."""
    url = url or ""
    m = re.search(r"/licenses/([a-z-]+)/([\d.]+)", url)
    if m:
        return f"CC {m.group(1).upper()} {m.group(2)}"
    if "publicdomain/zero" in url:
        return "CC0 1.0"
    return url


def fetch_box(species):
    """All records of `species` inside BOX, paged."""
    rows, offset = [], 0
    while True:
        params = {"scientificName": species, "decimalLatitude": f"{BOX['lat'][0]},{BOX['lat'][1]}",
                  "decimalLongitude": f"{BOX['lon'][0]},{BOX['lon'][1]}", "limit": PAGE, "offset": offset,
                  "occurrenceStatus": "PRESENT", "hasCoordinate": "true"}
        r = requests.get(GBIF, params=params, headers=HEADERS, timeout=60)
        r.raise_for_status()
        js = r.json()
        for o in js["results"]:
            rows.append({"species": species, "common": SPECIES[species], "gbif_key": o["key"],
                         "date": (o.get("eventDate") or "")[:10], "year": o.get("year"), "month": o.get("month"),
                         "lat": o["decimalLatitude"], "lon": o["decimalLongitude"],
                         "uncertainty_m": o.get("coordinateUncertaintyInMeters"),
                         "source": o.get("institutionCode") or publisher_name(o.get("publishingOrgKey")),
                         "dataset": o.get("datasetKey", "")[:8], "locality": o.get("locality") or "",
                         "basis": o.get("basisOfRecord"), "license": licence_name(o.get("license"))})
        if js.get("endOfRecords", True) or offset + PAGE >= 10000:
            break
        offset += PAGE
        time.sleep(0.2)
    return rows


def fetch_state_by_year(species):
    """Yearly counts for the whole state, from a GBIF facet (no records downloaded)."""
    r = requests.get(GBIF, params={"scientificName": species, "stateProvince": STATE, "limit": 0,
                                   "facet": "year", "facetLimit": 30, "occurrenceStatus": "PRESENT"}, headers=HEADERS, timeout=60)
    r.raise_for_status()
    facets = r.json().get("facets", [])
    counts = {int(c["name"]): c["count"] for f in facets for c in f["counts"]} if facets else {}
    return dict(sorted(counts.items()))


def main():
    OUTPUT.mkdir(exist_ok=True)
    DATA.mkdir(exist_ok=True)
    rows = []
    for sp in SPECIES:
        got = fetch_box(sp)
        print(f"{sp:16} {len(got):4d} records in the Heilbronn box")
        rows += got
    df = pd.DataFrame(rows).drop_duplicates("gbif_key")
    df["dist_hive_km"] = [round(haversine_km(a, b, HIVE_LAT, HIVE_LON), 2) for a, b in zip(df.lat, df.lon)]
    df["precise"] = df.uncertainty_m.fillna(99999) <= 1000       # GPS-like vs map-sheet records
    df = df.sort_values(["species", "date"])
    df.to_csv(OUTPUT / "hornet_sightings.csv", index=False)
    df.to_csv(DATA / "gbif_sightings_raw.csv", index=False)

    vv = df[df.species == "Vespa velutina"]
    by_year = {sp: df[df.species == sp].groupby("year").size().to_dict() for sp in SPECIES}
    state = fetch_state_by_year("Vespa velutina")
    nearest = vv.sort_values("dist_hive_km").head(3)
    summary = {
        "box": BOX, "n": {SPECIES[s]: int((df.species == s).sum()) for s in SPECIES},
        "asian_by_year": {int(k): int(v) for k, v in by_year["Vespa velutina"].items()},
        "european_by_year": {int(k): int(v) for k, v in by_year["Vespa crabro"].items()},
        "honeybee_by_year": {int(k): int(v) for k, v in by_year["Apis mellifera"].items()},
        "asian_precise": int(vv.precise.sum()), "asian_within_3km": int((vv.dist_hive_km <= 3).sum()),
        "nearest_asian": [{"date": r.date, "km": r.dist_hive_km, "locality": r.locality, "precise": bool(r.precise)} for r in nearest.itertuples()],
        "state_asian_by_year": state, "sources": sorted(df.source.unique().tolist()),
        "fetched": pd.Timestamp.now('UTC').strftime("%Y-%m-%d"),
    }
    (OUTPUT / "hornet_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False))

    print("\nAsian hornet sightings per year")
    years = sorted(set(summary["asian_by_year"]) | set(k for k in state if k >= 2015))
    print(f"  {'year':>6} {'Heilbronn box':>14} {STATE:>18}")
    for y in years:
        print(f"  {y:>6} {summary['asian_by_year'].get(y, 0):>14} {state.get(y, 0):>18}")
    print("\nnearest to the camera hive:")
    for n in summary["nearest_asian"]:
        print(f"  {n['date']}  {n['km']:.1f} km  {n['locality'] or '(no place name)'}  {'GPS' if n['precise'] else 'map-sheet'}")

    fig, axes = plt.subplots(1, 2, figsize=(10, 3.6))
    ys = [y for y in years if y >= 2018]
    axes[0].bar(ys, [summary["asian_by_year"].get(y, 0) for y in ys], color="#c0392b", width=0.7)
    axes[0].set_title("Asian hornet sightings, 22 km box around Heilbronn", fontsize=10)
    axes[1].bar(ys, [state.get(y, 0) for y in ys], color="#8e3b2f", width=0.7)
    axes[1].set_title(f"Asian hornet sightings, {STATE}", fontsize=10)
    for ax in axes:
        ax.set_xticks(ys)
        ax.grid(alpha=0.25, axis="y")
        ax.spines[["top", "right"]].set_visible(False)
    axes[0].set_ylabel("records on GBIF")
    fig.suptitle("Public records (iNaturalist, naturgucker, Observation.org via GBIF), fetched " + summary["fetched"], fontsize=9)
    fig.tight_layout()
    fig.savefig(OUTPUT / "hornet_sightings.png", dpi=120)
    print("\nwrote output/hornet_sightings.csv, hornet_summary.json, hornet_sightings.png")


if __name__ == "__main__":
    main()
