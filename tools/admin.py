#!/usr/bin/env python3
"""
City management for BeeMap. The website is public and read-only for city
operations; changes are made here, with the admin key.

The key: BEEMAP_ADMIN_KEY if you set one, otherwise the server generates one
in server/data/admin_key.txt on first start (this tool reads it from there).

  python3 tools/admin.py alerts                     # hornet alerts waiting for confirmation
  python3 tools/admin.py confirm 12                 # confirm alert 12   (dismiss 12 = not a hornet)
  python3 tools/admin.py nests                      # suspected nest areas
  python3 tools/admin.py assign 3 "Feuerwehr Team 2"
  python3 tools/admin.py removed 3
  python3 tools/admin.py health                     # colony health of every smart hive (private)
  python3 tools/admin.py health hive7-sontheim      # one hive in detail
  python3 tools/admin.py station-add hive20 "Hive 20, Biberach" 49.105 9.150 --heading 40 [--planned]
  python3 tools/admin.py station-token hive20       # Pi config
  python3 tools/admin.py box-add sb-x "Kirchhausen" 49.148 9.125 [--station hive20]
  python3 tools/admin.py box-token sb-x
  python3 tools/admin.py nest-reports [--status pending]   # nests reported by the public (exact spot, note, photo)
  python3 tools/admin.py nest-report 4 asian        # confirmed Asian hornet nest
  python3 tools/admin.py nest-report 5 other --species "European hornet"   # or: not-found, removed, pending
  python3 tools/admin.py reports on|off             # public nest reports (on by default)
  python3 tools/admin.py reports-recheck            # photo check again, e.g. after installing a model
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]


def admin_key() -> str:
    k = os.environ.get("BEEMAP_ADMIN_KEY")
    if k:
        return k
    f = Path(os.environ.get("BEEMAP_DATA", ROOT / "server" / "data")) / "admin_key.txt"
    if f.exists():
        return f.read_text().strip()
    sys.exit("No admin key found. Start the server once (it creates server/data/admin_key.txt) or set BEEMAP_ADMIN_KEY.")


class Api:
    def __init__(self, server, key):
        self.base, self.h = server.rstrip("/"), {"X-Admin-Key": key}

    def __call__(self, method, path, **kw):
        try:
            r = requests.request(method, self.base + path, headers=self.h, timeout=30, **kw)
        except requests.RequestException as e:
            sys.exit(f"Can't reach the server at {self.base}: {e}")
        if not r.ok:
            try:
                msg = r.json().get("detail")
            except ValueError:
                msg = r.text[:200]
            sys.exit(f"Refused ({r.status_code}): {msg}")
        return r.json()


def when(ts):
    return dt.datetime.fromtimestamp(ts).strftime("%d %b %H:%M") if ts else "–"


def main(argv=None):
    ap = argparse.ArgumentParser(description="BeeMap city management", formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("--server", default=os.environ.get("BEEMAP_SERVER", "http://127.0.0.1:8000"))
    sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("alerts").add_argument("--all", action="store_true")
    for c in ("confirm", "dismiss"):
        sp.add_parser(c).add_argument("id", type=int)
    sp.add_parser("nests")
    a = sp.add_parser("assign"); a.add_argument("id", type=int); a.add_argument("team"); a.add_argument("--place", default="")
    sp.add_parser("removed").add_argument("id", type=int)
    sp.add_parser("health").add_argument("id", nargs="?")
    a = sp.add_parser("station-add"); a.add_argument("id"); a.add_argument("name"); a.add_argument("lat", type=float); a.add_argument("lon", type=float)
    a.add_argument("--heading", type=float, default=0); a.add_argument("--planned", action="store_true"); a.add_argument("--owner", default="")
    sp.add_parser("station-token").add_argument("id")
    a = sp.add_parser("box-add"); a.add_argument("id"); a.add_argument("name"); a.add_argument("lat", type=float); a.add_argument("lon", type=float)
    a.add_argument("--station", default=None)
    sp.add_parser("box-token").add_argument("id")
    sp.add_parser("nest-reports").add_argument("--status", default=None)
    a = sp.add_parser("nest-report"); a.add_argument("id", type=int)
    a.add_argument("verdict", choices=["asian", "other", "not-found", "removed", "pending"]); a.add_argument("--species", default="")
    sp.add_parser("reports").add_argument("state", choices=["on", "off"])
    sp.add_parser("reports-recheck")
    a = ap.parse_args(argv)
    api = Api(a.server, admin_key())

    if a.cmd == "alerts":
        for d in api("GET", "/api/detections", params={"limit": 200, **({} if a.all else {"status": "pending"})}):
            b = "–" if d["bearing_deg"] is None else f"{d['bearing_deg']:.0f}°"
            print(f"#{d['id']:<5} {when(d['ts'])}  {d['status']:<9} {d['place'][:30]:<30} flew {b:>5}  {d.get('image_url') or ''}")
    elif a.cmd in ("confirm", "dismiss"):
        api("POST", f"/api/detections/{a.id}/review", json={"status": "confirmed" if a.cmd == "confirm" else "rejected",
                                                            **({} if a.cmd == "confirm" else {"species": "not velutina"})})
        print(f"Alert {a.id} {'confirmed' if a.cmd == 'confirm' else 'dismissed'}.")
    elif a.cmd == "nests":
        for n in api("GET", "/api/nests", params={"include_removed": True}):
            print(f"#{n['id']:<4} {n['status']:<9} ±{n['radius_m']:.0f} m  {n['lat']:.5f},{n['lon']:.5f}  {n['n_departures']} departures  {n.get('assigned_to') or ''}")
    elif a.cmd == "assign":
        api("PATCH", f"/api/nests/{a.id}", json={"status": "assigned", "assigned_to": a.team, "place": a.place}); print("Team assigned.")
    elif a.cmd == "removed":
        api("PATCH", f"/api/nests/{a.id}", json={"status": "removed"}); print("Nest marked removed.")
    elif a.cmd == "health":
        hs = [api("GET", f"/api/admin/health/{a.id}")] if a.id else api("GET", "/api/admin/health")
        for h in hs:
            print(f"{h['name']:<28} {h['status'].upper():<6} {h['summary']}")
            for i in h["indicators"]:
                if a.id or i["status"] not in ("ok", "none"):
                    print(f"    [{i['status']}] {i['title']}: {i['detail']}")
    elif a.cmd == "station-add":
        r = api("POST", "/api/stations", json={"id": a.id, "name": a.name, "lat": a.lat, "lon": a.lon, "heading_deg": a.heading,
                                               "planned": a.planned, "owner": a.owner})
        print(f"Added. Pi config:\n[beemap]\nserver = {a.server}\nstation_id = {a.id}\ntoken = {r['token']}")
    elif a.cmd == "station-token":
        print(api("GET", f"/api/stations/{a.id}/token")["token"])
    elif a.cmd == "box-add":
        r = api("POST", "/api/sensoboxes", json={"id": a.id, "name": a.name, "lat": a.lat, "lon": a.lon, "station_id": a.station})
        print(f"Added. The box posts to {a.server}/api/sensoboxes/{a.id}/readings with header X-Box-Token: {r['token']}")
    elif a.cmd == "box-token":
        print(api("GET", f"/api/sensoboxes/{a.id}/token")["token"])
    elif a.cmd == "nest-reports":
        for r in api("GET", "/api/admin/nest-reports", params={"status": a.status} if a.status else None):
            print(f"#{r['id']:<4} {when(r['ts'])}  {r['status']:<13} {r['lat']:.5f},{r['lon']:.5f}  {r['location']}, {r['height']}, {r['size']}"
                  f"{'  ' + r['species'] if r['species'] else ''}")
            if r["note"]:
                print(f"       note: {r['note']}")
            if r["photo_path"]:
                print(f"       photo: {r['photo_path']}")
            if r.get("check"):
                print(f"       check: {r['check'].get('reason', '')}{'  [confirmed by ' + r['verified_by'] + ']' if r.get('verified_by') else ''}")
    elif a.cmd == "nest-report":
        st = {"asian": "asian_hornet", "other": "other_species", "not-found": "not_found", "removed": "removed", "pending": "pending"}[a.verdict]
        api("PATCH", f"/api/admin/nest-reports/{a.id}", json={"status": st, "species": a.species})
        print(f"Nest report {a.id}: {st.replace('_', ' ')}{' (' + a.species + ')' if a.species else ''}.")
    elif a.cmd == "reports-recheck":
        r = api("POST", "/api/admin/nest-reports/recheck")
        print(f"Re-checked {r['checked']} waiting report(s) with photos; {r['confirmed']} confirmed as Asian hornet nests. Model: {r['model']}.")
    elif a.cmd == "reports":
        r = api("PATCH", "/api/settings", json={"reports": a.state == "on"})
        print(f"Public nest reports are now {'on' if r['reports'] else 'off'}.")


if __name__ == "__main__":
    main()
