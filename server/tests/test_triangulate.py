import math, random, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from triangulate import Observation, estimate_nests, LocalFrame

def dist(lat1, lon1, lat2, lon2):
    f = LocalFrame(lat1, lon1); x, y = f.to_xy(lat2, lon2); return math.hypot(x, y)

def true_bearing(lat, lon, nlat, nlon):
    f = LocalFrame(lat, lon); x, y = f.to_xy(nlat, nlon)
    return math.degrees(math.atan2(x, y)) % 360

def make(stations, nests, n=12, sigma=20, seed=1, decoys=0):
    rng = random.Random(seed); obs = []; did = 0
    for sid, (lat, lon, nest_idx) in stations.items():
        for k in range(n):
            nl = nests[nest_idx[k % len(nest_idx)]]
            b = true_bearing(lat, lon, *nl) + rng.gauss(0, sigma)
            did += 1; obs.append(Observation(sid, lat, lon, b % 360, sigma=sigma, detection_id=did))
        for _ in range(decoys):  # random orientation loops that slipped through
            did += 1; obs.append(Observation(sid, lat, lon, rng.uniform(0, 360), sigma=sigma, detection_id=did))
    return obs

def in_ellipse(n, truth):
    f = LocalFrame(n.lat, n.lon); x, y = f.to_xy(*truth)
    th = math.radians(n.orientation_deg); ux, uy = math.sin(th), math.cos(th)
    a_ = x * ux + y * uy; b_ = -x * uy + y * ux
    return (a_ / n.semi_major_m) ** 2 + (b_ / n.semi_minor_m) ** 2 <= 1

NEST_A = (49.1185, 9.2050)   # near Sontheim
NEST_B = (49.1500, 9.1850)

def test_single_nest():
    # three hives spread around the nest (not collinear with it)
    st = {"hive2": (49.1290, 9.1890, [0]), "hive7": (49.1060, 9.2010, [0]), "hive4": (49.1230, 9.2250, [0])}
    errs = []; inside = 0; runs = 100
    for seed in range(runs):
        r = estimate_nests(make(st, [NEST_A], seed=seed, decoys=2))
        assert r.nests, f"no nest seed {seed}"
        n = r.nests[0]
        e = dist(n.lat, n.lon, *NEST_A); errs.append(e)
        inside += in_ellipse(n, NEST_A)
    errs.sort()
    print("single nest, 3 hives ~1.5 km, ±20° per departure, 12 departures each:")
    print("  median err %.0f m, 90th pct %.0f m; true nest inside 95%% ellipse in %d/%d runs"
          % (errs[runs//2], errs[int(runs*.9)], inside, runs))
    assert errs[runs//2] < 250 and inside >= 85

def test_two_nests_shared_station():
    st = {"hive2": (49.1290, 9.1890, [0, 1]), "hive7": (49.1120, 9.2150, [0]),
          "hive9": (49.1450, 9.2050, [1]), "hive4": (49.1230, 9.2250, [0])}
    two = 0; covered = 0; runs = 50
    for seed in range(runs):
        r = estimate_nests(make(st, [NEST_A, NEST_B], n=14, seed=seed))
        tri = [n for n in r.nests if n.method == "triangulated"]
        two += len(tri) == 2
        covered += sum(any(in_ellipse(n, t) for n in tri) for t in (NEST_A, NEST_B))
    print(f"two nests, one hive sees both: 2 nests separated in {two}/{runs} runs; "
          f"true nest inside an ellipse {covered}/{2*runs}")
    assert two >= runs * 0.9 and covered >= runs * 2 * 0.85

def test_single_line_with_distance():
    o = [Observation("sighting:1", 49.13, 9.19, 120, sigma=8, distance_m=600)]
    r = estimate_nests(o)
    assert r.nests and r.nests[0].method == "single_line_distance"
    print("single line + distance:", round(dist(49.13, 9.19, r.nests[0].lat, r.nests[0].lon)), "m from observer")

def test_collinear_pair_plus_skewed_line():
    """Regression: two hives on opposite sides of the nest (their lines are
    one straight line) plus a long, skewed line used to invent a second,
    wrong nest and steal the good lines from the real one."""
    from triangulate import Observation as O
    A = NEST_A
    hive2, hive7, hive4, far = (49.1290, 9.1880), (49.1060, 9.2160), (49.1250, 9.2240), (49.1450, 9.2140)
    obs, k = [], 0
    for (lat, lon), name, bias, n in [(hive2, "h2", 5, 11), (hive7, "h7", -1, 11), (hive4, "h4", 2, 12), (far, "h5", 20, 3)]:
        for j in range(n):
            k += 1; obs.append(O(name, lat, lon, (true_bearing(lat, lon, *A) + bias + (j % 3 - 1) * 4) % 360,
                                 sigma=20, sigma_floor=8, detection_id=k))
    r = estimate_nests(obs)
    tri = [n for n in r.nests if n.method == "triangulated"]
    assert len(tri) == 1, f"expected one nest, got {len(tri)}"
    e = dist(tri[0].lat, tri[0].lon, *A)
    print(f"collinear pair + skewed far line: one nest, {e:.0f} m off, uses {tri[0].n_observers} stations")
    assert e < 250 and in_ellipse(tri[0], A)


def test_planner_turns_corridor_into_nest():
    """One lonely hive gives only a direction. A hive at the planner's top
    site should, in most runs, produce a located nest."""
    from triangulate import planning_targets, suggest_sites
    ok = 0; runs = 30
    for seed in range(runs):
        rng = random.Random(seed)
        hive = (49.13, 9.19); f = LocalFrame(*hive)
        d, b = rng.uniform(400, 1500), rng.uniform(0, 360)
        nest = f.to_ll(d * math.sin(math.radians(b)), d * math.cos(math.radians(b)))
        obs = [Observation("h1", *hive, (true_bearing(*hive, *nest) + rng.gauss(0, 20)) % 360,
                           sigma=25, sigma_floor=8, detection_id=i) for i in range(12)]
        T = planning_targets(estimate_nests(obs), f)
        S = suggest_sites(T[0], [f.to_xy(*hive)])
        la, lo = f.to_ll(S[0]["x"], S[0]["y"])
        obs += [Observation("new", la, lo, (true_bearing(la, lo, *nest) + rng.gauss(0, 20)) % 360,
                            sigma=25, sigma_floor=8, detection_id=100 + i) for i in range(10)]
        tri = [n for n in estimate_nests(obs).nests if n.method == "triangulated"]
        ok += bool(tri) and dist(tri[0].lat, tri[0].lon, *nest) < 400
    print(f"planner: hive at the top suggested site located the nest (<400 m) in {ok}/{runs} runs")
    assert ok >= runs * 0.75


if __name__ == "__main__":
    test_planner_turns_corridor_into_nest()
    test_single_nest(); test_two_nests_shared_station(); test_single_line_with_distance()
    test_collinear_pair_plus_skewed_line(); print("OK")
