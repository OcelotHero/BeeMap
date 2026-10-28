#!/usr/bin/env python3
"""
Refresh BeeMap's OpenStreetMap land cover by hand. Normally not needed: the
server fetches it by itself on first start and refreshes it every 30 days.

    python3 tools/update_osm_data.py [--include-landscape]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))
from osm_import import *  # noqa: E402,F401,F403
from osm_import import OUT, fetch_and_save  # noqa: E402


def main(argv=None, data: dict | None = None):
    ap = argparse.ArgumentParser(description="Download OpenStreetMap land cover for BeeMap")
    ap.add_argument("--include-landscape", action="store_true")
    ap.add_argument("--out", default=str(OUT))
    a = ap.parse_args(argv)
    if data is None:
        print("Downloading land cover for Heilbronn from OpenStreetMap…")
    res = fetch_and_save(a.include_landscape, Path(a.out), data)
    print(f"Saved {len(res['landcover'])} land-cover areas, {len(res['lines'])} hedges and tree rows, "
          f"{len(res['wild_bee_zones'])} protected areas and {len(res['places'])} place names to {a.out}.")
    return res


if __name__ == "__main__":
    main()
