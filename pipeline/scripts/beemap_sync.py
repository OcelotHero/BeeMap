#!/usr/bin/env python3
"""Send the real OpenSpace hive data from the pipeline into the BeeMap server.

What goes where (through BeeMap's normal upload API, like a Pi or SensoBox would):
  * BeeHappy in-hive sensors, three colonies   -> /api/stations/<station>/hive
    (hive_temp_c, hive_humidity, hive = "Beehive No.1" ...): colony health
  * the hive's SenseCAP weather station        -> SensoBox "openspace-weather",
    linked to that station: the outside weather colony health compares against

Input: output/hourly.csv from `make analyse`. Only hours newer than the last
sync are sent; progress is kept in output/beemap_sync_state.json, per server
and per device token, so a reset BeeMap database gets everything again.

  python scripts/beemap_sync.py                      # http://127.0.0.1:8000, station hornet-pi
  python scripts/beemap_sync.py --server http://192.168.7.34:8000 --station hornet-pi
  python scripts/beemap_sync.py --temp brood         # use the brood-nest probes instead of the hive-body sensor
"""
import argparse
import csv
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import HIVE_LAT, HIVE_LON, OUTPUT  # noqa: E402

HIVES = ["Beehive No.1", "Beehive No.2", "Beehive No.3"]
BOX_ID = "openspace-weather"
BOX_NAME = "OpenSpace hive weather station"
STATE = OUTPUT / "beemap_sync_state.json"
BATCH = 5000


def admin_key(arg):
    if arg:
        return arg
    if os.environ.get("BEEMAP_ADMIN_KEY"):
        return os.environ["BEEMAP_ADMIN_KEY"]
    f = Path(os.environ.get("BEEMAP_DATA", Path(__file__).resolve().parents[2] / "server" / "data")) / "admin_key.txt"
    if f.exists():
        return f.read_text().strip()
    sys.exit("No admin key: start the BeeMap server once (it writes server/data/admin_key.txt) or pass --admin-key.")


class Server:
    def __init__(self, base, key):
        self.base, self.s = base.rstrip("/"), requests.Session()
        self.admin = {"X-Admin-Key": key}

    def call(self, method, path, ok=(200,), **kw):
        try:
            r = self.s.request(method, self.base + path, timeout=60, **kw)
        except requests.RequestException as e:
            sys.exit(f"Can't reach BeeMap at {self.base}: {e}")
        if r.status_code not in ok:
            sys.exit(f"{method} {path} refused ({r.status_code}): {r.text[:300]}")
        return r


def ensure_devices(srv, station):
    r = srv.call("GET", f"/api/stations/{station}/token", ok=(200, 404), headers=srv.admin)
    if r.status_code == 404:
        print(f"station '{station}' not on the server: adding it at the OpenSpace hives (planned, no camera yet)")
        srv.call("POST", "/api/stations", headers=srv.admin,
                 json={"id": station, "name": "OpenSpace hives", "lat": HIVE_LAT, "lon": HIVE_LON, "heading_deg": 0, "planned": True})
        r = srv.call("GET", f"/api/stations/{station}/token", headers=srv.admin)
    st_token = r.json()["token"]
    r = srv.call("GET", f"/api/sensoboxes/{BOX_ID}/token", ok=(200, 404), headers=srv.admin)
    if r.status_code == 404:
        print(f"adding SensoBox '{BOX_ID}' linked to {station}")
        srv.call("POST", "/api/sensoboxes", headers=srv.admin,
                 json={"id": BOX_ID, "name": BOX_NAME, "lat": HIVE_LAT, "lon": HIVE_LON, "station_id": station})
        r = srv.call("GET", f"/api/sensoboxes/{BOX_ID}/token", headers=srv.admin)
    return st_token, r.json()["token"]


def num(v):
    try:
        return None if v in ("", None) else float(v)
    except ValueError:
        return None


def load_hours(temp_kind):
    path = OUTPUT / "hourly.csv"
    if not path.exists():
        sys.exit(f"{path} missing: run `make analyse` first.")
    hive_rows, weather = {h: [] for h in HIVES}, []
    prefix = "brood_temp_" if temp_kind == "brood" else "inside_temp_"
    with open(path, newline="") as fh:
        for r in csv.DictReader(fh):
            ts = datetime.fromisoformat(r["hour"]).timestamp()
            for h in HIVES:
                t, rh = num(r.get(prefix + h)), num(r.get("inside_hum_" + h))
                if t is not None or rh is not None:
                    hive_rows[h].append({"ts": ts, "hive_temp_c": t, "hive_humidity": rh, "hive": h})
            w = {"ts": ts, "temp_c": num(r.get("out_temp")), "humidity": num(r.get("out_humidity")),
                 "pressure_hpa": num(r.get("pressure")), "lux": num(r.get("light")), "uv_index": num(r.get("uv"))}
            if any(v is not None for k, v in w.items() if k != "ts"):
                weather.append(w)
    return hive_rows, weather


def send(srv, path, header, rows):
    for i in range(0, len(rows), BATCH):
        srv.call("POST", path, headers=header, json={"readings": rows[i:i + BATCH]})


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--server", default=os.environ.get("BEEMAP_SERVER", "http://127.0.0.1:8000"))
    ap.add_argument("--station", default="hornet-pi", help="BeeMap station at the OpenSpace hives")
    ap.add_argument("--admin-key", default=None)
    ap.add_argument("--temp", choices=["body", "brood"], default="body",
                    help="body = Dragino S31 in the hive body (with humidity); brood = D23 brood-nest probes")
    a = ap.parse_args()

    srv = Server(a.server, admin_key(a.admin_key))
    st_token, box_token = ensure_devices(srv, a.station)
    hive_rows, weather = load_hours(a.temp)

    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    sent = {}
    for h, rows in hive_rows.items():
        key = f"{srv.base}|{st_token[:10]}|{h}|{a.temp}"
        new = [r for r in rows if r["ts"] > state.get(key, 0)]
        if new:
            send(srv, f"/api/stations/{a.station}/hive", {"X-Station-Token": st_token}, new)
            state[key] = new[-1]["ts"]
        sent[h] = len(new)
    key = f"{srv.base}|{box_token[:10]}|weather"
    new = [r for r in weather if r["ts"] > state.get(key, 0)]
    if new:
        send(srv, f"/api/sensoboxes/{BOX_ID}/readings", {"X-Box-Token": box_token}, new)
        state[key] = new[-1]["ts"]
    sent["weather station"] = len(new)
    STATE.write_text(json.dumps(state, indent=1))

    for k, n in sent.items():
        print(f"  {k:<16} {n:>6} new hourly readings")
    last = max((r["ts"] for rows in hive_rows.values() for r in rows), default=None)
    if last:
        print(f"  data up to {datetime.fromtimestamp(last):%d %b %Y %H:%M}. Colony health: python3 tools/admin.py health {a.station}")


if __name__ == "__main__":
    main()
