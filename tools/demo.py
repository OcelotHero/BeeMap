#!/usr/bin/env python3
"""
Fill a BeeMap server with a realistic demo: six hive cameras around
Heilbronn, two hidden nests, and simulated hornet departures pushed through
the real Pi agent code (tracker -> exit angle -> upload).

    python3 tools/demo.py                       # seed once
    python3 tools/demo.py --live                # keep sending a hornet every ~20 s
    python3 tools/demo.py --server http://pi-server:8000 --admin-key secret

At the end it prints how far each estimated nest is from the hidden truth.
"""
from __future__ import annotations

import argparse
import math
import random
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pi"))
import beemap_agent as agent  # noqa: E402

# The hidden truth the demo hornets fly home to
NESTS = {
    "A (Sontheim)": (49.1155, 9.2035),
    "B (Neckargartach)": (49.1590, 9.1930),
    "C (Heilbronn-Ost)": (49.1395, 9.2560),
}
# Today's sparse network. Hornets visit a hive more the closer it is to
# their nest (none beyond ~1.8 km), so which nests each hive "sees" falls out
# of the geography, as it would in reality.
# id, name, beekeeper, lat, lon, heading camera direction (sky: image top; horizon: image centre)
STATIONS = [
    ("hive2-boeckingen", "Hive 2, Böckingen", "M. Keller", 49.1250, 9.1930, 35),
    ("hive7-sontheim", "Hive 7, Sontheim", "S. Yilmaz", 49.1060, 9.2160, 300),
    ("hive4-suedstadt", "Hive 4, Südstadt", "J. Brandt", 49.1215, 9.2180, 180),
    ("hive9-frankenbach", "Hive 9, Frankenbach", "A. Vogel", 49.1490, 9.1830, 90),
    ("hive5-innenstadt", "Hive 5, Innenstadt", "Imkerverein HN", 49.1450, 9.2140, 0),
    ("hive12-ost", "Hive 12, Heilbronn-Ost", "P. Roth", 49.1440, 9.2440, 270),
]
# Already chosen, Pi not installed yet
PLANNED = [("hive14-wartberg", "Hive 14, Wartberg", "Imkerverein HN", 49.1560, 9.2330)]
OFFLINE = {"hive12-ost"}
# Heading camera per hive: most look up through a fisheye (all directions);
# Hive 4 has an outward camera with a 120° view aimed at 240° (towards Sontheim).
HEADING_MODE = {"hive4-suedstadt": ("horizon", 240)}


def visits_from(lat, lon, rng):
    """{nest name: number of departures} a hive here would record in a few hours."""
    out = {}
    for name, n in NESTS.items():
        d = dist_m((lat, lon), n)
        if d < 1800:
            k = round(14 * math.exp(-d / 800) * rng.uniform(0.8, 1.2))
            if k:
                out[name] = k
    return out


def true_bearing(lat, lon, nlat, nlon):
    kx = math.cos(math.radians(lat)) * 111320
    return math.degrees(math.atan2((nlon - lon) * kx, (nlat - lat) * 111320)) % 360


def dist_m(a, b):
    kx = math.cos(math.radians(a[0])) * 111320
    return math.hypot((b[1] - a[1]) * kx, (b[0] - a[0]) * 111320)


def register(srv, H, sid, name, owner, lat, lon, heading, planned=False):
    r = requests.post(f"{srv}/api/stations", headers=H, json={
        "id": sid, "name": name, "owner": owner, "lat": lat, "lon": lon,
        "heading_deg": heading, "heading_sigma_deg": 5, "planned": planned})
    if r.status_code == 409:
        return requests.get(f"{srv}/api/stations/{sid}/token", headers=H).json()["token"]
    r.raise_for_status()
    return r.json()["token"]


def start_hook(srv, sid, token, rng, mode="sky"):
    cfg = agent.Config(server=srv, station_id=sid, token=token, heartbeat_s=30,
                       queue_dir=f"/tmp/beemap-demo-queue/{sid}",
                       heading_camera="simulated", heading_mode=mode, heading_hfov=120.0)
    hook = agent.BeeMapHook(cfg=cfg)
    hook.model_name = "vespai.pt (demo)"
    hook.last_frame = agent._sim_background()
    hook.fps = round(rng.uniform(7.5, 9.8), 1)
    return hook


def send_departures(hook, lat, lon, heading, counts, rng, t_from, t_to):
    plan = []
    for name, k in counts.items():
        plan += [(rng.uniform(t_from, t_to), name) for _ in range(k)]
    if plan and rng.random() < 0.5:
        plan.append((rng.uniform(t_from, t_to), ""))      # one that wandered off elsewhere
    for t, name in sorted(plan):
        # heading camera: about ±6° (it sees metres of the flight, not centimetres)
        compass = rng.uniform(0, 360) if name is "" else true_bearing(lat, lon, *NESTS[name]) + rng.gauss(0, 6)
        t_end = agent.fake_visit(hook, rng.uniform(0, 360), t, rng)     # detection camera: presence + timing
        agent.fake_heading(hook, compass % 360, heading, t_end, rng)
        hook.flush()
    return len(plan)


def wait_for(srv, n):
    for _ in range(80):
        if len(requests.get(f"{srv}/api/detections?limit=5000").json()) >= n:
            return
        time.sleep(0.5)


def report(srv, title):
    time.sleep(1.5)                                           # coalesced recompute
    nests = requests.get(f"{srv}/api/nests").json()
    plan = requests.get(f"{srv}/api/planning").json()
    print(f"\n{title}")
    found = set()
    for n in nests:
        name, truth = min(NESTS.items(), key=lambda kv: dist_m((n["lat"], n["lon"]), kv[1]))
        found.add(name)
        print(f"  nest area #{n['id']}: {dist_m((n['lat'], n['lon']), truth):4.0f} m from nest {name}, "
              f"search radius ± {n['radius_m']:.0f} m, {n['n_departures']} departures, {n['n_observers']} hives")
    for name in NESTS:
        if name not in found:
            print(f"  nest {name}: not located yet")
    s = plan["summary"]
    print(f"  direction-only corridors: {s['corridors']}, wide nest areas: {s['wide_nests']}")
    return plan


# Monthly mean temperature in Heilbronn, roughly (°C)
T_MEAN = [1.5, 2.5, 6, 10, 14.5, 18, 20, 19.5, 15.5, 10.5, 5.5, 2.5]
# How the *real* bee forage differs from the land-cover model at each box
# (the point of measuring). 1.0 = as modelled. Simulated for the demo.
SENSOBOXES = [
    ("sb-boeckingen", "Böckingen Rathaus", 49.1330, 9.1830, True,
     [1, 1, .45, .7, 1, 1, 1, 1, 1, 1, 1, 1]),          # willows cut back: lean early spring
    ("sb-sontheim", "Sontheim Campus", 49.1180, 9.2100, True,
     [1, 1, 1, 1, 1, 1.2, 1.6, 1, 1, 1, 1, 1]),           # lime trees on campus: rich July
    ("sb-innenstadt", "Innenstadt Marktplatz", 49.1425, 9.2195, True,
     [1, 1, 1, 1, .8, .55, .5, .6, 1, 1, 1, 1]),          # parks mown short: lean summer
    ("sb-frankenbach", "Frankenbach Schule", 49.1490, 9.1700, False,
     [1] * 12),                                          # weather sensors only, no pollen yet
]
BEE_TYPES = {3: ["salix", "corylus"], 4: ["salix", "rosaceae", "brassicaceae"], 5: ["rosaceae", "taraxacum", "brassicaceae"],
             6: ["tilia", "trifolium", "rosaceae"], 7: ["tilia", "trifolium", "phacelia"], 8: ["trifolium", "phacelia"],
             9: ["hedera", "phacelia"], 10: ["hedera"]}


def seed_sensoboxes(srv, H, rng):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
    import ecology
    w = ecology.world()
    now = time.time()
    for bid, name, lat, lon, has_pollen, truth in SENSOBOXES:
        token = requests.post(f"{srv}/api/sensoboxes", headers=H, json={"id": bid, "name": name, "lat": lat, "lon": lon}).json()["token"]
        i, j = w.cell(lat, lon)
        readings = []
        for d in range(365, 0, -1):
            day0 = now - d * 86400
            m = time.localtime(day0).tm_mon
            for hour in (2, 8, 12, 16, 20):
                t = day0 - (day0 % 86400) + hour * 3600
                temp = T_MEAN[m - 1] + 5 * math.sin((hour - 9) / 24 * 2 * math.pi) + rng.gauss(0, 2.5)
                daylight = 6 <= hour <= 18 if m in (4, 5, 6, 7, 8, 9) else 8 <= hour <= 16
                r = {"ts": t, "temp_c": round(temp, 1), "humidity": round(min(99, max(35, 75 - (temp - 10) * 1.5 + rng.gauss(0, 10))), 1),
                     "pressure_hpa": round(1015 + rng.gauss(0, 7), 1),
                     "lux": round(rng.uniform(8000, 60000) if daylight else 0), "uv_index": round(max(0, (T_MEAN[m - 1] / 4) * rng.uniform(.3, 1)) if daylight else 0, 1)}
                if has_pollen and hour == 12:
                    bee = float(w.model_supply[i, j, m - 1]) * 400 * truth[m - 1] * rng.uniform(.7, 1.3)
                    # split across types so the bee-weighted sum is exactly `bee`
                    names = BEE_TYPES.get(m, [])
                    shares = [1 / (k + 1.5) for k in range(len(names))]
                    tot = sum(shares) or 1
                    types = {t: round(bee * sh / tot / ecology.POLLEN_BEE_WEIGHT[t], 3) for t, sh in zip(names, shares)}
                    if m in (5, 6, 7):
                        types["poaceae"] = round(rng.uniform(20, 120), 1)   # grass: lots of air pollen, little bee food
                    if m == 4:
                        types["betula"] = round(rng.uniform(30, 200), 1)
                    r["pollen"] = types
                    r["pollen_total"] = round(sum(types.values()), 1)
                readings.append(r)
        requests.post(f"{srv}/api/sensoboxes/{bid}/readings", headers={"X-Box-Token": token},
                      json={"readings": readings}, timeout=120).raise_for_status()
    print(f"{len(SENSOBOXES)} SensoBoxes with a year of readings ({sum(b[4] for b in SENSOBOXES)} with pollen sensors)")


# Colony stories for the health demo (simulated)
HIVE_STORY = {"hive2-boeckingen": "healthy", "hive5-innenstadt": "healthy", "hive7-sontheim": "hornets",
              "hive4-suedstadt": "lost", "hive9-frankenbach": "damp"}


def seed_hive_health(srv, H, rng, tokens):
    """Each smart hive: its own SensoBox (outside weather + pollen) and 30 days
    of in-hive temperature, humidity and entrance activity."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
    import ecology
    w = ecology.world()
    now = time.time()
    stations = {x["id"]: x for x in requests.get(f"{srv}/api/stations").json()}
    for sid, story in HIVE_STORY.items():
        st, token = stations.get(sid), tokens.get(sid)
        if not st or not token:
            continue
        box = requests.post(f"{srv}/api/sensoboxes", headers=H, json={"id": f"sb-{sid}", "name": f"{st['name']} (hive)",
                                                                     "lat": st["lat"] + 0.0002, "lon": st["lon"], "station_id": sid}).json()
        i, j = w.cell(st["lat"], st["lon"])
        outside, inside = [], []
        for hh in range(60 * 24, 0, -1):
            ts = now - hh * 3600
            lt = time.localtime(ts)
            day_ago = hh / 24
            t_out = T_MEAN[lt.tm_mon - 1] + 3 * math.sin(day_ago / 4) + 5 * math.sin((lt.tm_hour - 9) / 24 * 2 * math.pi) + rng.gauss(0, 1)
            lux = rng.uniform(8000, 50000) if 8 <= lt.tm_hour <= 17 else 0
            o = {"ts": ts, "temp_c": round(t_out, 1), "humidity": round(min(99, max(40, 80 - (t_out - 8) * 2 + rng.gauss(0, 6))), 1),
                 "pressure_hpa": round(1014 + 6 * math.sin(day_ago / 3) + rng.gauss(0, 1), 1), "lux": round(lux),
                 "uv_index": round(lux / 20000, 1)}
            if lt.tm_hour == 12:
                bee = float(w.model_supply[i, j, lt.tm_mon - 1]) * 400 * rng.uniform(.8, 1.2)
                o["pollen"] = {"hedera": round(bee * .7, 3), "salix": round(bee * .3, 3), "poaceae": round(rng.uniform(5, 30), 1)}
            outside.append(o)
            if day_ago > 30:
                continue
            f = 1.0
            if story == "hornets" and day_ago < 4: f = 0.45
            if story == "lost" and day_ago < 5: f = 0.02
            act = max(0.0, 12 * max(0, t_out - 8) * f + rng.gauss(0, 2)) if lux else 0.0
            t_in = (t_out + 0.6 if story == "lost" and day_ago < 5 else t_out + 14) + rng.gauss(0, 1)
            rh = 90 + rng.gauss(0, 2) if story == "damp" else 62 + rng.gauss(0, 5)
            inside.append({"ts": ts, "hive_temp_c": round(t_in, 2), "hive_humidity": round(rh, 1), "activity": round(act, 2)})
        requests.post(f"{srv}/api/sensoboxes/{box['id']}/readings", headers={"X-Box-Token": box["token"]},
                      json={"readings": outside}, timeout=120).raise_for_status()
        requests.post(f"{srv}/api/stations/{sid}/hive", headers={"X-Station-Token": token},
                      json={"readings": inside}, timeout=120).raise_for_status()
        if story == "hornets":     # hornets hawking at the entrance over the last days
            hook = start_hook(srv, sid, token, rng)
            nest = NESTS["A (Sontheim)"]
            for d in range(5, 0, -1):
                for k in range(2):
                    t0 = now - d * 86400 + rng.uniform(-3, 3) * 3600
                    end = agent.fake_visit(hook, rng.uniform(0, 360), t0, rng)
                    agent.fake_heading(hook, true_bearing(st["lat"], st["lon"], *nest) + rng.gauss(0, 6), st["heading_deg"], end, rng)
                    hook.flush()
    print(f"{len(HIVE_STORY)} smart hives with their own SensoBox and 30 days of in-hive data")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default="http://127.0.0.1:8000")
    ap.add_argument("--admin-key", default="")
    ap.add_argument("--grow", action="store_true",
                    help="afterwards, install hives at the suggested sites and see what they find")
    ap.add_argument("--live", action="store_true", help="keep sending a hornet every ~20 s")
    ap.add_argument("--install-planned", action="store_true",
                    help="pitch step: switch on every planned hive and let hornets visit it")
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    rng = random.Random(a.seed)
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from admin import admin_key
    H = {"X-Admin-Key": a.admin_key or admin_key()}
    srv = a.server.rstrip("/")
    for i in range(30):                      # the server may still be starting
        try:
            requests.get(f"{srv}/api/health", timeout=1).raise_for_status()
            break
        except requests.RequestException:
            if i == 0:
                print(f"Waiting for the BeeMap server at {srv} …")
            time.sleep(0.5)
    else:
        sys.exit(f"Can't reach the BeeMap server at {srv}.\n"
                 "Start it first in its own terminal and leave it running:\n"
                 "    cd server\n    python3 -m uvicorn app:app --port 8000\n"
                 "Then run this demo again in a second terminal. If the server prints an error, "
                 "that's the real problem (Python 3.10+ is required).")

    if a.install_planned:
        sts = [x for x in requests.get(f"{srv}/api/stations").json() if x.get("planned")]
        if not sts:
            print("No planned hives. Plan one in the app first (Network planning > Plan a hive here).")
            return
        now, sent = time.time(), len(requests.get(f"{srv}/api/detections?limit=5000").json())
        for x in sts:
            token = requests.get(f"{srv}/api/stations/{x['id']}/token", headers=H).json()["token"]
            hook = start_hook(srv, x["id"], token, rng)
            counts = visits_from(x["lat"], x["lon"], rng)
            print(f"  {x['name']} switched on: hornets from "
                  f"{', '.join(f'{v}× nest {n}' for n, v in counts.items()) or 'no nest (too far away)'}")
            sent += send_departures(hook, x["lat"], x["lon"], x["heading_deg"], counts, rng, now - 2 * 3600, now - 30)
        wait_for(srv, sent)
        report(srv, "After switching on the planned hives:")
        time.sleep(3)
        return

    hooks = {}
    for sid, name, owner, lat, lon, heading in STATIONS:
        token = register(srv, H, sid, name, owner, lat, lon, heading)
        if sid in HEADING_MODE:
            heading = HEADING_MODE[sid][1]
            requests.patch(f"{srv}/api/stations/{sid}", headers=H, json={"heading_deg": heading})
        if sid not in OFFLINE:
            mode = HEADING_MODE.get(sid, ("sky",))[0]
            hooks[sid] = (start_hook(srv, sid, token, rng, mode), lat, lon, heading)
    for sid, name, owner, lat, lon in PLANNED:
        register(srv, H, sid, name, owner, lat, lon, 0, planned=True)

    now = time.time()
    sent = 0
    for sid, (hook, lat, lon, heading) in hooks.items():
        sent += send_departures(hook, lat, lon, heading, visits_from(lat, lon, rng), rng, now - 9 * 3600, now - 600)
    print(f"{len(STATIONS)} hives ({len(hooks)} online), {len(PLANNED)} planned; sent {sent} departures")
    wait_for(srv, sent)

    # Nests reported by residents: one waiting, one confirmed where the
    # cameras' flight lines cross, one that was a European hornet in a nest box
    if not requests.get(f"{srv}/api/nest-reports").json():
        for lat, lon, where, height, size, note, verdict, species in [
                (49.1596, 9.1922, "tree", "above-5m", "football", "High in a poplar by the allotments", None, ""),
                (49.1157, 9.2031, "tree", "above-5m", "bigger", "Big round nest in an oak, hornets flying in at the side", "asian_hornet", ""),
                (49.1352, 9.2405, "tree", "reach", "football", "Inside a bird box in our garden", "other_species", "European hornet")]:
            r = requests.post(f"{srv}/api/nest-reports", data={"lat": lat, "lon": lon, "location": where, "height": height,
                                                                "size": size, "note": note}).json()
            if verdict:
                requests.patch(f"{srv}/api/admin/nest-reports/{r['id']}", headers=H, json={"status": verdict, "species": species})
        print("3 nest reports added (1 waiting, 1 confirmed Asian hornet, 1 European hornet)")

    # What residents and a farmer have planted so far (anonymous, as on the site)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
    if not requests.get(f"{srv}/api/eco/layers").json().get("plantings"):
        for lat, lon, plant, m2 in [(49.1335, 9.1845, "salix", 25), (49.1328, 9.1812, "pulmonaria", 5),
                                    (49.1420, 9.2200, "echium", 1), (49.1470, 9.1480, "regio_mix", 3000),
                                    (49.1185, 9.2060, "cornus", 15)]:
            requests.post(f"{srv}/api/eco/plantings", json={"lat": lat, "lon": lon, "plant_id": plant, "area_m2": m2}).raise_for_status()
        print("5 plantings added")

    # SensoBoxes: a year of weather (and, where fitted, pollen) readings
    if not requests.get(f"{srv}/api/sensoboxes").json():
        seed_sensoboxes(srv, H, rng)
        seed_hive_health(srv, H, rng, {sid: hooks[sid][0].cfg.token for sid in hooks})

    # The city has already worked through the older alerts
    dets = sorted(requests.get(f"{srv}/api/detections?limit=5000").json(), key=lambda d: d["ts"])
    for d in dets[: int(len(dets) * 0.7)]:
        requests.post(f"{srv}/api/detections/{d['id']}/review", headers=H, json={"status": "confirmed"})

    plan = report(srv, "Today's network:")
    for t in plan["targets"]:
        if t["suggestions"]:
            s0 = t["suggestions"][0]
            what = f"corridor from {t['label']}" if t["kind"] == "corridor" else f"nest area #{t['nest_id']}"
            print(f"  -> best new site for {what}: {s0['lat']:.4f}, {s0['lon']:.4f}, expected ± {s0['radius_m']} m "
                  f"({s0['radius_low_m']}–{s0['radius_high_m']}), visit chance {s0['visit_chance']:.0%}")

    if a.grow:
        print("\nInstalling one hive at the best site for each target…")
        k = 0
        for t in plan["targets"]:
            if not t["suggestions"]:
                continue
            s0 = t["suggestions"][0]
            k += 1
            sid = f"hive-new{k}"
            heading = rng.choice([0, 90, 180, 270])
            token = register(srv, H, sid, f"New hive {k}", "Planned by BeeMap", s0["lat"], s0["lon"], heading, planned=True)
            hook = start_hook(srv, sid, token, rng)
            counts = visits_from(s0["lat"], s0["lon"], rng)
            print(f"  {sid}: hornets from {', '.join(f'{v}× nest {n}' for n, v in counts.items()) or 'no nest (too far)'}")
            sent += send_departures(hook, s0["lat"], s0["lon"], heading, counts, rng, now - 3 * 3600, now - 60)
        wait_for(srv, sent)
        report(srv, "After installing the suggested hives:")

    time.sleep(3)   # let the first heartbeats land
    if not a.live:
        return
    print("\nLive: a hornet every ~20 s. Ctrl+C to stop.")
    while True:
        time.sleep(rng.uniform(12, 28))
        sid = rng.choice(list(hooks))
        hook, lat, lon, heading = hooks[sid]
        counts = visits_from(lat, lon, rng)
        if counts:
            name = rng.choice(list(counts))
            send_departures(hook, lat, lon, heading, {name: 1}, rng, time.time(), time.time())
            print(f"  hornet left {sid}")


if __name__ == "__main__":
    main()
