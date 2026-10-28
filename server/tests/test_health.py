"""Colony health indicators on simulated colonies."""
import math, os, random, sys
from datetime import datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import health  # noqa: E402

T_MEAN = [1.5, 2.5, 6, 10, 14.5, 18, 20, 19.5, 15.5, 10.5, 5.5, 2.5]


def simulate(now, kind, seed=1, days=30):
    rng = random.Random(seed)
    hive, out, hornets = [], [], []
    for hh in range(days * 24, 0, -1):
        ts = now - hh * 3600
        dt = datetime.fromtimestamp(ts)
        day_ago = hh / 24
        base = T_MEAN[dt.month - 1] + 3 * math.sin(day_ago / 4)             # warm and cold spells
        t_out = base + 5 * math.sin((dt.hour - 9) / 24 * 2 * math.pi) + rng.gauss(0, 1)
        lux = 30000 if 8 <= dt.hour <= 17 else 0
        out.append({"ts": ts, "temp_c": t_out, "humidity": 70, "lux": lux})
        health_f = 1.0
        if kind == "deadout" and day_ago < 5: health_f = 0.02
        if kind == "hornet" and day_ago < 4: health_f = 0.45
        act = (12 * max(0, t_out - 8) * health_f + rng.gauss(0, 2)) if lux else 0
        brood = dt.month in range(3, 10)
        if kind == "broodfail" and day_ago < 6:
            t_in = t_out + 6 + rng.gauss(0, 1)
        elif brood:
            t_in = 35 + rng.gauss(0, 0.4)
        else:
            t_in = (t_out + 0.5 if kind == "deadout" and day_ago < 5 else t_out + 14) + rng.gauss(0, 1)
        hum = 90 + rng.gauss(0, 2) if kind == "damp" else 60 + rng.gauss(0, 5)
        hive.append({"ts": ts, "hive_temp_c": t_in, "hive_humidity": hum, "activity": max(0, act)})
        if kind == "hornet" and day_ago < 6 and dt.hour == 14:
            hornets.append(ts)
    return hive, out, hornets


def run(kind, when):
    now = datetime(*when).timestamp()
    h, o, hz = simulate(now, kind)
    return health.analyse(h, o, hz, 1.1, now)


def keys(r, status):
    return {i["key"] for i in r["indicators"] if i["status"] == status}


def test_health():
    oct_ = (2026, 10, 28, 12)
    jun = (2026, 6, 20, 12)
    r = run("healthy", oct_); assert r["status"] == "ok", r["indicators"]
    assert r["weather_coupling"] > 0.6
    r = run("deadout", oct_); assert r["status"] == "alert" and {"thermo", "activity"} <= keys(r, "alert"), r["indicators"]
    r = run("hornet", oct_); assert "hornets" in keys(r, "watch") and r["activity_index"] < 0.8, r["indicators"]
    r = run("damp", oct_); assert "humidity" in keys(r, "watch")
    r = run("healthy", jun); assert r["status"] == "ok" and "thermo" in keys(r, "ok"), r["indicators"]
    r = run("broodfail", jun); assert "thermo" in keys(r, "alert"), r["indicators"]
    print("health: healthy ok (Oct + Jun); dead-out, hornet pressure, damp hive and failed brood nest all flagged")


if __name__ == "__main__":
    test_health()
