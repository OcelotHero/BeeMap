#!/usr/bin/env python3
"""
Refresh observed flowering times from iNaturalist by hand. Normally not
needed: the server fetches them by itself in the background, plant by plant,
and refreshes them every 90 days.

    python3 tools/update_bloom_data.py [--place "Germany"] [--min-obs 50]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))
import ecology  # noqa: E402
from bloom_import import *  # noqa: E402,F401,F403
from bloom_import import OUT, Api, fetch_plant, find_place, header, save  # noqa: E402


def main(argv=None, api: Api | None = None):
    ap = argparse.ArgumentParser(description="Observed flowering times from iNaturalist")
    ap.add_argument("--place", default="Baden-Württemberg")
    ap.add_argument("--fallback", default="Germany")
    ap.add_argument("--min-obs", type=int, default=30)
    ap.add_argument("--out", default=str(OUT))
    a = ap.parse_args(argv)
    api = api or Api()
    places = [p for p in (find_place(api, n) for n in (a.place, a.fallback)) if p]
    if not places:
        sys.exit("No place found. Check the --place name.")
    out = header(a.min_obs)
    for p in ecology.PLANTS:
        got = fetch_plant(api, p, places, a.min_obs)
        out["checked"][p.id] = out["fetched"]
        if got:
            out["plants"][p.id] = got
            print(f"- {p.name:24} months {got['months']}  ({got['n_obs']} flowering obs, {got['place']})")
        else:
            print(f"- {p.name}: kept curated months")
    save(Path(a.out), out)
    print(f"Saved {len(out['plants'])} of {len(ecology.PLANTS)} plants to {a.out}.")
    return out


if __name__ == "__main__":
    main()
