#!/usr/bin/env python3
"""Download all raw data for the bee commute tracker into ./data.

Sources
  1. Nextcloud share (beehive entrance recordings + labelled crops)  -> data/raw/nextcloud, extracted to data/recordings, data/traindata
  2. Grafana "BeeHappy" dashboard (PostgreSQL behind an anonymous
     Grafana instance; we run the same SQL the panels use)          -> data/hive_sensors.csv
  3. senseBox:home stations in Heilbronn (OGC API Features)         -> data/sensebox_all.csv.gz
  4. Heat-island summer-school walks (mobile datalogger, Nextcloud) -> data/raw/datalogger/datasets/*.csv

Every download is skipped if the target file already exists, so the script
is safe to re-run.  Use --only to fetch a single source.

Usage:
    python scripts/fetch_data.py                # everything
    python scripts/fetch_data.py --only hive    # videos | hive | sensebox | walks
"""
import argparse
import csv
import gzip
import io
import json
import os
import re
import sys
import tarfile
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote

import requests

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

# --------------------------------------------------------------------------- #
# 1. Nextcloud share
# --------------------------------------------------------------------------- #
NC_BASE = "https://cloud.reedu.de"
# Share tokens of the hackathon data (cloud.reedu.de/s/<token>, empty password). Not kept in the
# repo because it is public; ask the organisers, then:  export BEEMAP_NC_SHARE=... BEEMAP_WALKS_SHARE=...
NC_TOKEN = os.environ.get("BEEMAP_NC_SHARE", "")
NC_WEBDAV = f"{NC_BASE}/public.php/webdav"


def share(token, var):
    if not token:
        sys.exit(f"Set {var} to the hackathon data share token (cloud.reedu.de/s/<token>), e.g.  export {var}=...")
    return token


def nextcloud_list(path=""):
    """Return [(relative_path, size_bytes)] of all files under the share."""
    r = requests.request(
        "PROPFIND", f"{NC_WEBDAV}/{path}", auth=(share(NC_TOKEN, "BEEMAP_NC_SHARE"), ""),
        headers={"Depth": "infinity"}, timeout=60,
    )
    r.raise_for_status()
    ns = {"d": "DAV:"}
    files = []
    for resp in ET.fromstring(r.content).findall("d:response", ns):
        href = unquote(resp.find("d:href", ns).text)
        rel = href.split("/public.php/webdav/", 1)[1]
        size = resp.find(".//d:getcontentlength", ns)
        if size is not None and rel and not rel.startswith(".attachments"):
            files.append((rel, int(size.text)))
    return files


def download(url, dest, auth=None, expected_size=None):
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and (expected_size is None or dest.stat().st_size == expected_size):
        print(f"  skip (exists) {dest.relative_to(ROOT)}")
        return
    print(f"  GET {dest.relative_to(ROOT)} ...", end="", flush=True)
    with requests.get(url, auth=auth, stream=True, timeout=120) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
    print(f" {dest.stat().st_size / 1e6:.1f} MB")


def safe_extract(tar_path, dest):
    """Extract a .tar.gz, refusing paths that escape dest."""
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(tar_path) as tar:
        for m in tar.getmembers():
            target = (dest / m.name).resolve()
            if not str(target).startswith(str(dest.resolve())):
                raise RuntimeError(f"unsafe path in {tar_path}: {m.name}")
        tar.extractall(dest)


def fetch_videos():
    print("== Nextcloud share (recordings + labels)")
    raw = DATA / "raw" / "nextcloud"
    for rel, size in nextcloud_list():
        download(f"{NC_WEBDAV}/{rel}", raw / rel, auth=(share(NC_TOKEN, "BEEMAP_NC_SHARE"), ""), expected_size=size)

    # Extract: recording sessions -> data/recordings/<session>/, training crops -> data/traindata/
    for tar_path in sorted((raw / "data" / "recordings_sessions").glob("*.tar.gz")):
        session = tar_path.name.replace(".tar.gz", "")
        out = DATA / "recordings" / session
        if out.exists():
            continue
        print(f"  extract {tar_path.name} -> data/recordings/{session}")
        safe_extract(tar_path, out)
    for name, out in [("traindata_20260916.tar.gz", DATA / "traindata"),
                      ("uncropped_all.tar.gz", DATA / "uncropped_all")]:
        tar_path = raw / "data" / name
        if tar_path.exists() and not out.exists():
            print(f"  extract {name} -> {out.relative_to(ROOT)}")
            safe_extract(tar_path, out)


# --------------------------------------------------------------------------- #
# 2. Grafana / hive sensors
# --------------------------------------------------------------------------- #
GRAFANA = "https://beehive-grafana-production.up.railway.app"
GRAFANA_DS = {"type": "grafana-postgresql-datasource", "uid": "beehive"}

# Which series to pull.  The SenseCAP weather station is registered once per
# hive with identical values, so we only take the copy attached to hive 1.
HIVE_SQL = """
SELECT d.ts AS ts, b.name AS hive, s.sensor_type AS sensor_type,
       d.measurement_unit AS unit, d.value AS value
FROM data d
JOIN sensors  s ON s.sensor_id  = d.sensor_id
JOIN beehives b ON b.beehive_id = d.beehive_id
WHERE s.sensor_type IN ('LoRaWAN Dragino-S31-LB', 'LoRaWAN Dragino-D23-LB')
   OR (s.sensor_type = 'LoRaWAN SenseCAP-S2120' AND b.beehive_id = 1)
ORDER BY d.ts
"""


def grafana_sql(sql):
    """Run raw SQL through Grafana's datasource query API; return list of dict rows."""
    body = {
        "queries": [{"refId": "A", "datasource": GRAFANA_DS, "format": "table",
                     "rawSql": sql, "maxDataPoints": 10_000_000, "intervalMs": 1000}],
        "from": "0", "to": str(int(time.time() * 1000) + 86_400_000),
    }
    r = requests.post(f"{GRAFANA}/api/ds/query", json=body, timeout=600)
    r.raise_for_status()
    res = r.json()["results"]["A"]
    if res.get("error"):
        raise RuntimeError(res["error"])
    rows = []
    for frame in res["frames"]:
        cols = [f["name"] for f in frame["schema"]["fields"]]
        types = [f.get("type") for f in frame["schema"]["fields"]]
        for values in zip(*frame["data"]["values"]):
            row = dict(zip(cols, values))
            for c, t in zip(cols, types):
                if t == "time" and row[c] is not None:   # epoch ms -> ISO UTC
                    row[c] = datetime.fromtimestamp(row[c] / 1000, tz=timezone.utc).isoformat()
            rows.append(row)
    return rows


def fetch_hive():
    print("== Grafana hive sensors")
    out = DATA / "hive_sensors.csv"
    if out.exists():
        print(f"  skip (exists) {out.relative_to(ROOT)}")
        return
    # One query per month keeps each response well under Grafana's limits.
    bounds = grafana_sql("SELECT min(ts) AS first_ts, max(ts) AS last_ts FROM data")[0]
    first = datetime.fromisoformat(bounds["first_ts"]).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    last = datetime.fromisoformat(bounds["last_ts"])
    print(f"  data range {bounds['first_ts']} .. {bounds['last_ts']}")
    rows = []
    cur = first
    while cur <= last:
        nxt = (cur.replace(year=cur.year + 1, month=1) if cur.month == 12
               else cur.replace(month=cur.month + 1))
        sql = HIVE_SQL.replace("ORDER BY", f"AND d.ts >= '{cur:%Y-%m-%d}' AND d.ts < '{nxt:%Y-%m-%d}' ORDER BY")
        sql = sql.replace("WHERE s.sensor_type IN", "WHERE (s.sensor_type IN").replace("beehive_id = 1)\nAND", "beehive_id = 1))\nAND")
        chunk = grafana_sql(sql)
        print(f"  {cur:%Y-%m}: {len(chunk):>7} rows")
        rows.extend(chunk)
        cur = nxt
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["ts", "hive", "sensor_type", "unit", "value"])
        w.writeheader()
        w.writerows(rows)
    print(f"  wrote {len(rows)} rows -> {out.relative_to(ROOT)}")


# --------------------------------------------------------------------------- #
# 3. senseBox:home (atrai.bike OGC API)
# --------------------------------------------------------------------------- #
SENSEBOX_URL = "https://api.atrai.bike/collections/osem_home_data_heilbronn/items"
SENSEBOX_PAGE = 250_000     # the API ignores bbox/datetime filters but honours offset+limit


def fetch_sensebox():
    print("== senseBox:home Heilbronn")
    out = DATA / "sensebox_all.csv.gz"
    if out.exists():
        print(f"  skip (exists) {out.relative_to(ROOT)}")
        return
    offset, total, writer, n = 0, None, None, 0
    with gzip.open(out, "wt", newline="") as f:
        while total is None or offset < total:
            for attempt in range(3):
                try:
                    r = requests.get(SENSEBOX_URL, params={"f": "json", "limit": SENSEBOX_PAGE, "offset": offset},
                                     timeout=900)
                    r.raise_for_status()
                    page = r.json()
                    break
                except Exception as e:  # noqa: BLE001 - retry anything, the API is flaky
                    print(f"  retry {attempt + 1}: {e}")
                    time.sleep(10)
            else:
                raise RuntimeError("senseBox download failed")
            total = page["numberMatched"]
            feats = page["features"]
            if not feats:
                break
            for ft in feats:
                row = dict(ft["properties"])
                row["lon"], row["lat"] = ft["geometry"]["coordinates"]
                if writer is None:
                    writer = csv.DictWriter(f, fieldnames=list(row.keys()))
                    writer.writeheader()
                writer.writerow(row)
            n += len(feats)
            offset += len(feats)
            print(f"  {n:>9}/{total} rows")
    print(f"  wrote {n} rows -> {out.relative_to(ROOT)}")


# --------------------------------------------------------------------------- #
# 4. Heat-island summer-school walks (mobile datalogger, 1-3 Sep 2026)
# --------------------------------------------------------------------------- #
WALKS_TOKEN = os.environ.get("BEEMAP_WALKS_SHARE", "")   # see NC_TOKEN above
WALKS_FILES = ["README.md", "datasets/alles.csv", "datasets/dienstag.csv", "datasets/mittwoch.csv",
               "datasets/donnerstag.csv", "datasets/innenstadt.csv", "datasets/neckar.csv", "datasets/wartberg.csv"]


def fetch_walks():
    print("== Heat-island walks (mobile datalogger)")
    for rel in WALKS_FILES:
        download(f"{NC_WEBDAV}/{rel}", DATA / "raw" / "datalogger" / rel, auth=(share(WALKS_TOKEN, "BEEMAP_WALKS_SHARE"), ""))


# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", choices=["videos", "hive", "sensebox", "walks"], help="fetch only one source")
    args = ap.parse_args()
    DATA.mkdir(exist_ok=True)
    steps = {"videos": fetch_videos, "hive": fetch_hive, "sensebox": fetch_sensebox, "walks": fetch_walks}
    for name, fn in steps.items():
        if args.only in (None, name):
            fn()
    print("done.")


if __name__ == "__main__":
    main()
