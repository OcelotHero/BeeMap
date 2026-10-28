"""
Nest-location estimation from hornet departure bearings.

Asian hornets leaving a food source (a bait station or an apiary) circle
briefly to orient, then fly a fairly straight line home. Each station that
sees hornets leave therefore gives us a *bearing line* pointing at the nest.
Lines from two or more stations cross near the nest.

Pipeline
--------
1. Per observer (camera station or manual sighting spot), cluster the
   departure bearings on the circle. One station can sit between two nests,
   so it can produce more than one bearing line.
2. Intersect every pair of lines from *different* observers, keeping only
   crossings that lie in front of both observers, within a sensible range,
   and at a crossing angle wide enough to be meaningful.
3. Cluster the crossings spatially. Each cluster is a candidate nest.
4. Refine each candidate with iteratively re-weighted least squares on the
   angular residuals, which also yields a covariance and a 95 % ellipse.
5. Lines that crossed nothing are kept as "direction only" rays; if a
   round-trip time gave a distance, a rough single-line point is produced.

Pure Python, no numpy, so it runs anywhere including the Pi itself.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

EARTH_R = 6_371_000.0
CHI2_95_2D = 5.991  # 95 % for 2 degrees of freedom
GATE = 3.5          # a line "points at" a spot if it is within this many sigmas


# ---------------------------------------------------------------- geometry --
class LocalFrame:
    """Equirectangular projection around a reference point. Metres east/north.
    Accurate to well under 1 % over the few kilometres a hornet colony forages."""

    def __init__(self, lat0: float, lon0: float):
        self.lat0, self.lon0 = lat0, lon0
        self.kx = math.cos(math.radians(lat0)) * math.pi / 180 * EARTH_R
        self.ky = math.pi / 180 * EARTH_R

    def to_xy(self, lat: float, lon: float) -> tuple[float, float]:
        return ((lon - self.lon0) * self.kx, (lat - self.lat0) * self.ky)

    def to_ll(self, x: float, y: float) -> tuple[float, float]:
        return (self.lat0 + y / self.ky, self.lon0 + x / self.kx)


def bearing_unit(deg: float) -> tuple[float, float]:
    """Compass bearing (0 = north, clockwise) -> unit vector (east, north)."""
    r = math.radians(deg)
    return (math.sin(r), math.cos(r))


def circ_mean_deg(angles: list[float], weights: list[float] | None = None) -> tuple[float, float]:
    """Weighted circular mean and circular standard deviation, in degrees."""
    if not angles:
        raise ValueError("no angles")
    weights = weights or [1.0] * len(angles)
    s = sum(w * math.sin(math.radians(a)) for a, w in zip(angles, weights))
    c = sum(w * math.cos(math.radians(a)) for a, w in zip(angles, weights))
    W = sum(weights)
    R = math.hypot(s, c) / W
    mean = math.degrees(math.atan2(s, c)) % 360
    R = min(max(R, 1e-9), 1.0)
    std = math.degrees(math.sqrt(-2 * math.log(R)))
    return mean, std


def small_sample(n: float) -> float:
    """Widen a mean bearing's uncertainty when it rests on few departures:
    their spread underestimates the truth, and one stray hornet can swing
    the mean (Student-t-like; 2 departures x2, 3 x1.5, 10 x1.1)."""
    return 1.0 + 1.0 / max(n - 1.0, 1.0) if n < 30 else 1.0


def ang_diff(a: float, b: float) -> float:
    return (a - b + 180) % 360 - 180


# ------------------------------------------------------------------ inputs --
@dataclass
class Observation:
    """One departure (or one manual sighting) with a bearing."""
    observer_id: str            # station id or "sighting:<id>"
    lat: float
    lon: float
    bearing: float              # degrees, compass
    sigma: float = 25.0         # 1-sigma bearing uncertainty for a single departure
    weight: float = 1.0         # e.g. track straightness x detection confidence
    distance_m: float | None = None  # optional, from round-trip timing
    detection_id: int | None = None
    sigma_floor: float = 4.0    # systematic error, e.g. camera heading calibration


@dataclass
class BearingLine:
    observer_id: str
    lat: float
    lon: float
    bearing: float
    sigma: float                # uncertainty of the *mean* bearing
    n: int
    spread: float               # circular std of the individual departures
    detection_ids: list[int] = field(default_factory=list)
    distance_m: float | None = None
    # filled during solve
    x: float = 0.0
    y: float = 0.0


@dataclass
class NestEstimate:
    lat: float
    lon: float
    semi_major_m: float
    semi_minor_m: float
    orientation_deg: float      # compass direction of the major axis
    n_lines: int
    n_observers: int
    n_departures: int
    method: str                 # "triangulated" | "single_line_distance"
    line_keys: list[str]
    detection_ids: list[int]
    rms_residual_deg: float

    @property
    def radius_m(self) -> float:
        """Single number for UIs: radius of a circle with the ellipse's area."""
        return math.sqrt(self.semi_major_m * self.semi_minor_m)


# ------------------------------------------------- 1. per-observer clustering
def cluster_bearings(obs: list[Observation], window_deg: float | None = None,
                     min_count: int = 2, min_share: float = 0.2) -> list[BearingLine]:
    """Find the dominant departure directions at one observer.

    Greedy mode-seeking on the circle: take the densest window, peel it off,
    repeat. A manual sighting with a carefully read compass counts once but
    carries a smaller sigma, so a single one is allowed through."""
    remaining = list(obs)
    total = len(obs)
    if window_deg is None:
        # wide enough to hold one nest's noisy departures (~±2 sigma),
        # narrow enough that two nests 60°+ apart stay separate
        sig = sorted(o.sigma for o in obs)[len(obs) // 2]
        window_deg = min(max(30.0, 2.0 * sig), 50.0)
    lines: list[BearingLine] = []
    while remaining:
        best, best_members = None, []
        for o in remaining:
            members = [m for m in remaining if abs(ang_diff(m.bearing, o.bearing)) <= window_deg]
            if sum(m.weight for m in members) > sum(m.weight for m in best_members):
                best, best_members = o, members
        mean, _ = circ_mean_deg([m.bearing for m in best_members], [m.weight for m in best_members])
        # re-centre once on the mean, which steadies the membership
        members = [m for m in remaining if abs(ang_diff(m.bearing, mean)) <= window_deg]
        mean, spread = circ_mean_deg([m.bearing for m in members], [m.weight for m in members])
        n = len(members)
        # a single reading only counts on its own if it is a person's compass
        # reading; one camera departure in a different direction is a stray
        is_precise_manual = n == 1 and total == 1 and members[0].observer_id.startswith("sighting:") and members[0].sigma <= 12
        if (n >= min_count and n / total >= min_share) or is_precise_manual:
            per_obs_sigma = max(spread, min(m.sigma for m in members)) if n > 1 else members[0].sigma
            # uncertainty of the mean shrinks with n, but never below a floor:
            # camera heading calibration error does not average away.
            sigma_mean = max(per_obs_sigma / math.sqrt(n), max(m.sigma_floor for m in members)) * small_sample(n)
            dists = [m.distance_m for m in members if m.distance_m]
            lines.append(BearingLine(
                observer_id=members[0].observer_id, lat=members[0].lat, lon=members[0].lon,
                bearing=mean, sigma=sigma_mean, n=n, spread=spread if n > 1 else members[0].sigma,
                detection_ids=[m.detection_id for m in members if m.detection_id is not None],
                distance_m=(sorted(dists)[len(dists) // 2] if dists else None),
            ))
        remaining = [m for m in remaining if m not in members]
    return lines


# --------------------------------------------------------- 2. intersections
def intersect(a: BearingLine, b: BearingLine, max_range: float) -> tuple[float, float] | None:
    ax, ay = bearing_unit(a.bearing)
    bx, by = bearing_unit(b.bearing)
    det = ax * (-by) - ay * (-bx)
    if abs(det) < math.sin(math.radians(12)):      # nearly parallel: useless crossing
        return None
    dx, dy = b.x - a.x, b.y - a.y
    t = (dx * (-by) - dy * (-bx)) / det
    s = (ax * dy - ay * dx) / det
    if t <= 0 or s <= 0:                           # behind an observer
        return None
    if t > max_range or s > max_range:
        return None
    return (a.x + t * ax, a.y + t * ay)


# ---------------------------------------------------------- 4. refinement
def solve_point(lines: list[BearingLine], x0: float, y0: float,
                iters: int = 12) -> tuple[float, float, list[list[float]], float]:
    """IRLS: minimise sum_i n_i * (perp_dist_i / (d_i * sigma_i))^2.
    Returns x, y, 2x2 covariance (m^2), RMS angular residual in degrees."""
    x, y = x0, y0
    cov = [[1e9, 0.0], [0.0, 1e9]]
    for _ in range(iters):
        A = [[0.0, 0.0], [0.0, 0.0]]
        bvec = [0.0, 0.0]
        for L in lines:
            ux, uy = bearing_unit(L.bearing)
            nx, ny = -uy, ux                         # normal to the line
            d = max(math.hypot(x - L.x, y - L.y), 40.0)
            sig_m = d * math.radians(L.sigma)        # angular -> lateral metres
            w = 1.0 / sig_m ** 2
            c = nx * L.x + ny * L.y
            A[0][0] += w * nx * nx; A[0][1] += w * nx * ny
            A[1][0] += w * ny * nx; A[1][1] += w * ny * ny
            bvec[0] += w * nx * c;  bvec[1] += w * ny * c
        det = A[0][0] * A[1][1] - A[0][1] * A[1][0]
        if abs(det) < 1e-18:
            break
        inv = [[A[1][1] / det, -A[0][1] / det], [-A[1][0] / det, A[0][0] / det]]
        nxp = inv[0][0] * bvec[0] + inv[0][1] * bvec[1]
        nyp = inv[1][0] * bvec[0] + inv[1][1] * bvec[1]
        cov = inv
        moved = math.hypot(nxp - x, nyp - y)
        x, y = nxp, nyp
        if moved < 0.5:
            break
    res = []
    for L in lines:
        to = math.degrees(math.atan2(x - L.x, y - L.y)) % 360
        res.append(ang_diff(to, L.bearing))
    rms = math.sqrt(sum(r * r for r in res) / len(res)) if res else 0.0
    return x, y, cov, rms


def ellipse(cov: list[list[float]]) -> tuple[float, float, float]:
    """95 % ellipse: semi-major, semi-minor (m), major-axis compass bearing."""
    a, b, d = cov[0][0], cov[0][1], cov[1][1]
    tr, det = a + d, a * d - b * b
    disc = math.sqrt(max(tr * tr / 4 - det, 0.0))
    l1, l2 = tr / 2 + disc, max(tr / 2 - disc, 1e-6)
    k = math.sqrt(CHI2_95_2D)
    # eigenvector of l1 in (east, north)
    if abs(b) > 1e-12:
        vx, vy = l1 - d, b
    else:
        vx, vy = (1.0, 0.0) if a >= d else (0.0, 1.0)
    orient = math.degrees(math.atan2(vx, vy)) % 180
    return k * math.sqrt(l1), k * math.sqrt(l2), orient


# ------------------------------------------------------------- main entry
@dataclass
class Result:
    nests: list[NestEstimate]
    lines: list[BearingLine]
    unused_line_keys: list[str]
    frame: "LocalFrame | None" = None   # the x/y frame lines are expressed in


def line_key(L: BearingLine) -> str:
    return f"{L.observer_id}@{round(L.bearing)}"


def estimate_nests(observations: list[Observation], max_range_m: float = 3000.0,
                   cluster_radius_m: float = 350.0, min_ellipse_m: float = 100.0) -> Result:
    if not observations:
        return Result([], [], [], None)
    lat0 = sum(o.lat for o in observations) / len(observations)
    lon0 = sum(o.lon for o in observations) / len(observations)
    frame = LocalFrame(lat0, lon0)

    by_obs: dict[str, list[Observation]] = {}
    for o in observations:
        by_obs.setdefault(o.observer_id, []).append(o)
    lines: list[BearingLine] = []
    for group in by_obs.values():
        lines.extend(cluster_bearings(group))
    for L in lines:
        L.x, L.y = frame.to_xy(L.lat, L.lon)

    # 2. pairwise crossings between different observers
    crossings: list[tuple[float, float, int, int]] = []
    for i in range(len(lines)):
        for j in range(i + 1, len(lines)):
            if lines[i].observer_id == lines[j].observer_id:
                continue
            p = intersect(lines[i], lines[j], max_range_m)
            if p:
                crossings.append((p[0], p[1], i, j))

    # 3. seed candidates: centres of dense groups of crossings. Grid-bucketed
    #    so it stays linear in the number of crossings: a 500-hive network
    #    produces tens of thousands of them.
    cell = cluster_radius_m
    buckets: dict[tuple[int, int], list[int]] = {}
    for ci, c in enumerate(crossings):
        buckets.setdefault((int(c[0] // cell), int(c[1] // cell)), []).append(ci)

    def around(key):
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                yield from buckets.get((key[0] + dx, key[1] + dy), ())

    density = {k: sum(1 for _ in around(k)) for k in buckets}
    taken: set[int] = set()
    seeds: list[tuple[float, float]] = []
    for key in sorted(density, key=density.get, reverse=True):
        members = [ci for ci in around(key) if ci not in taken]
        if not members:
            continue
        mx = sum(crossings[ci][0] for ci in members) / len(members)
        my = sum(crossings[ci][1] for ci in members) / len(members)
        members = [ci for ci in members if math.hypot(crossings[ci][0] - mx, crossings[ci][1] - my) <= cluster_radius_m * 1.5]
        if not members:
            continue
        taken.update(members)
        seeds.append((sum(crossings[ci][0] for ci in members) / len(members),
                      sum(crossings[ci][1] for ci in members) / len(members)))

    # Lines indexed by where they start, so "which lines could point here?"
    # only looks at hives within flying range instead of the whole network.
    lgrid: dict[tuple[int, int], list[int]] = {}
    for i, L in enumerate(lines):
        lgrid.setdefault((int(L.x // max_range_m), int(L.y // max_range_m)), []).append(i)

    def lines_near(x: float, y: float):
        kx, ky = int(x // max_range_m), int(y // max_range_m)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                yield from lgrid.get((kx + dx, ky + dy), ())

    # 4a. Alternate: give each line to the candidate it points at best, then
    #     re-solve each candidate from its lines. Lines are never "claimed"
    #     early, so a bad first guess can't steal them.
    def z(L: BearingLine, x: float, y: float) -> float:
        d = math.hypot(x - L.x, y - L.y)
        if d > max_range_m:
            return math.inf
        if d < 1:
            return 0.0
        off = ang_diff(math.degrees(math.atan2(x - L.x, y - L.y)) % 360, L.bearing)
        return math.inf if abs(off) > 90 else abs(off) / L.sigma

    def assign(cands: list[tuple[float, float]]) -> list[list[int]]:
        groups: list[list[int]] = [[] for _ in cands]
        best: dict[int, tuple[float, int]] = {}
        for j, (cx, cy) in enumerate(cands):
            for i in lines_near(cx, cy):
                sc = z(lines[i], cx, cy)
                if sc <= GATE and sc < best.get(i, (math.inf, -1))[0]:
                    best[i] = (sc, j)
        for i, (_, j) in best.items():
            groups[j].append(i)
        return groups

    def observers(idx: list[int]) -> int:
        return len({lines[i].observer_id for i in idx})

    def dedupe(points):
        out: list[tuple[float, float]] = []
        for p in points:
            if all(math.hypot(p[0] - q[0], p[1] - q[1]) > cluster_radius_m * 0.6 for q in out):
                out.append(p)
        return out

    def same_nest(a, b) -> bool:
        """Two candidates are one nest if they're statistically
        indistinguishable given both of their uncertainties."""
        dx, dy = a[0] - b[0], a[1] - b[1]
        if math.hypot(dx, dy) <= cluster_radius_m * 0.6:
            return True
        C = [[a[2][0][0] + b[2][0][0], a[2][0][1] + b[2][0][1]],
             [a[2][1][0] + b[2][1][0], a[2][1][1] + b[2][1][1]]]
        det = C[0][0] * C[1][1] - C[0][1] * C[1][0]
        if det <= 0:
            return False
        m2 = (dx * (C[1][1] * dx - C[0][1] * dy) + dy * (-C[1][0] * dx + C[0][0] * dy)) / det
        return m2 <= CHI2_95_2D

    # Non-exclusive warm-up: each seed refines using every line that fits it.
    # Candidates that are statistically the same spot merge (union of their
    # lines), so one nest's noisy crossings don't fragment into several.
    cands = seeds
    for _ in range(6):
        refined = []          # (x, y, cov, idx)
        for cx, cy in cands:
            idx = [i for i in lines_near(cx, cy) if z(lines[i], cx, cy) <= GATE]
            if observers(idx) >= 2:
                x, y, cov, _ = solve_point([lines[i] for i in idx], cx, cy)
                refined.append((x, y, cov, set(idx)))
        merged_any = True
        while merged_any:
            merged_any = False
            for i in range(len(refined)):
                for j in range(i + 1, len(refined)):
                    if same_nest(refined[i], refined[j]):
                        idx = refined[i][3] | refined[j][3]
                        x0 = (refined[i][0] + refined[j][0]) / 2
                        y0 = (refined[i][1] + refined[j][1]) / 2
                        x, y, cov, _ = solve_point([lines[k] for k in idx], x0, y0)
                        refined[i] = (x, y, cov, idx)
                        refined.pop(j)
                        merged_any = True
                        break
                if merged_any:
                    break
        new = [(r[0], r[1]) for r in refined]
        if new == cands:
            break
        cands = new

    for _ in range(8):
        groups = assign(cands)
        new = []
        for (cx, cy), idx in zip(cands, groups):
            if observers(idx) >= 2:
                x, y, _, _ = solve_point([lines[i] for i in idx], cx, cy)
                new.append((x, y))
        merged = dedupe(new)
        if not merged:            # never let the exclusive step wipe everything out
            break
        if merged == cands:
            break
        cands = merged

    # 4b. Parsimony: if every line behind a candidate also fits another
    #     candidate, the simpler story (fewer nests) wins. This is what stops
    #     two nearly collinear stations plus one skewed line inventing a nest.
    groups = assign(cands)
    order = sorted(range(len(cands)), key=lambda j: sum(lines[i].n for i in groups[j]))
    alive = set(range(len(cands)))
    for j in order:
        others = [k for k in alive if k != j]
        if not others:
            break
        if all(min(z(lines[i], *cands[k]) for k in others) <= GATE for i in groups[j]):
            alive.discard(j)
            cands_alive = [cands[k] for k in sorted(alive)]
            regroup = assign(cands_alive)
            if any(observers(g) < 2 for g in regroup):  # would orphan a nest: undo
                alive.add(j)
    cands = [cands[k] for k in sorted(alive)]
    groups = assign(cands)

    # 4c. final solve, outlier pass, ellipse
    used: set[int] = set()
    nests: list[NestEstimate] = []

    def build(idx: list[int], cx: float, cy: float, prune: bool = True) -> None:
        group = [lines[i] for i in idx]
        if len({L.observer_id for L in group}) < 2:
            return
        x, y, cov, rms = solve_point(group, cx, cy)
        keep = group
        if prune and len(group) >= 3:
            keep = [L for L in group if z(L, x, y) <= GATE]
        if len({L.observer_id for L in keep}) < 2:
            return
        if len(keep) != len(group):
            x, y, cov, rms = solve_point(keep, x, y)
        # If the lines disagree more than their sigmas predict, believe the
        # disagreement: scale the ellipse by the reduced chi-square.
        chi2 = sum((ang_diff(math.degrees(math.atan2(x - L.x, y - L.y)) % 360, L.bearing) / L.sigma) ** 2
                   for L in keep)
        dof = len(keep) - 2
        if dof > 0 and chi2 / dof > 1:
            k = chi2 / dof
            cov = [[cov[0][0] * k, cov[0][1] * k], [cov[1][0] * k, cov[1][1] * k]]
        a_, b_, orient = ellipse(cov)
        # sigmas come from small samples (t- not normal-distributed), and
        # camera-heading calibration errors are correlated: be a bit humble.
        # Two lines always "agree" (they cross somewhere), so nothing checks them.
        inflate = 1.2 if len(keep) >= 3 else 1.5
        a_, b_ = a_ * inflate, b_ * inflate
        lat, lon = frame.to_ll(x, y)
        used.update(i for i in idx if lines[i] in keep)
        nests.append(NestEstimate(
            lat=lat, lon=lon,
            semi_major_m=max(a_, min_ellipse_m), semi_minor_m=max(b_, min_ellipse_m),
            orientation_deg=orient, n_lines=len(keep),
            n_observers=len({L.observer_id for L in keep}),
            n_departures=sum(L.n for L in keep), method="triangulated",
            line_keys=[line_key(L) for L in keep],
            detection_ids=sorted({d for L in keep for d in L.detection_ids}),
            rms_residual_deg=rms,
        ))

    for (cx, cy), idx in zip(cands, groups):
        build(idx, cx, cy)

    # 4d. Lines left over that still cross each other in front of both
    #     observers. They can't each be a separate nest (a nest needs two
    #     lines), so solve them together; chi-square inflation then widens the
    #     ellipse to however much they disagree. Better a wide search area
    #     than silence.
    left = [i for i in range(len(lines)) if i not in used]
    adj: dict[int, set[int]] = {i: set() for i in left}
    pts: dict[tuple[int, int], tuple[float, float]] = {}
    for ii, i in enumerate(left):
        for j in left[ii + 1:]:
            if lines[i].observer_id != lines[j].observer_id:
                p = intersect(lines[i], lines[j], max_range_m)
                if p:
                    adj[i].add(j); adj[j].add(i); pts[(i, j)] = p
    seen: set[int] = set()
    for i in left:
        if i in seen or not adj[i]:
            continue
        comp, stack = set(), [i]
        while stack:
            k = stack.pop()
            if k not in comp:
                comp.add(k); stack.extend(adj[k] - comp)
        seen |= comp
        cp = [p for (u, v), p in pts.items() if u in comp and v in comp]
        build(sorted(comp), sum(p[0] for p in cp) / len(cp), sum(p[1] for p in cp) / len(cp), prune=False)

    # 4e. Departure-level refinement (EM). Per-hive averaging can blend two
    #     nests that sit close together into one "mixed" line pointing between
    #     them. Now that we have nest hypotheses, give each individual
    #     departure soft weights to the nests it plausibly points at (plus an
    #     outlier class for strays), rebuild every hive's line per nest from
    #     those weights, and re-solve. Evidence from other hives untangles it.
    oxy = [frame.to_xy(o.lat, o.lon) for o in observations]
    OUTLIER = math.exp(-0.5 * 2.5 ** 2)
    tri = [n for n in nests if n.method == "triangulated"]
    centres = [frame.to_xy(n.lat, n.lon) for n in tri]
    weights: list[dict[int, float]] = []
    for _ in range(5):
        if not centres:
            break
        weights = []
        for o, (ox, oy) in zip(observations, oxy):
            lik = {}
            for c, (cx, cy) in enumerate(centres):
                d = math.hypot(cx - ox, cy - oy)
                if d > max_range_m:
                    continue
                if d < 1:
                    lik[c] = 1.0
                    continue
                off = ang_diff(math.degrees(math.atan2(cx - ox, cy - oy)) % 360, o.bearing)
                if abs(off) < 90:
                    lik[c] = math.exp(-0.5 * (off / o.sigma) ** 2)
            tot = sum(lik.values()) + OUTLIER
            weights.append({c: v / tot for c, v in lik.items() if v / tot > 0.05})
        new_nests: list[NestEstimate] = []
        new_centres = []
        for c, (cx, cy) in enumerate(centres):
            per_obs: dict[str, list[tuple[Observation, float]]] = {}
            for o, w in zip(observations, weights):
                if c in w:
                    per_obs.setdefault(o.observer_id, []).append((o, w[c] * o.weight))
            group = []
            for oid, items in per_obs.items():
                eff = sum(w for _, w in items)
                precise_manual = (len(items) == 1 and oid.startswith("sighting:")
                                  and items[0][0].sigma <= 12 and items[0][1] > 0.5)
                if eff < 1.5 and not precise_manual:
                    continue
                mean, spread = circ_mean_deg([o.bearing for o, _ in items], [w for _, w in items])
                base = min(o.sigma for o, _ in items)
                per = max(spread, base) if len(items) > 1 else base
                sig = max(per / math.sqrt(max(eff, 1.0)), max(o.sigma_floor for o, _ in items)) * small_sample(eff)
                o0 = items[0][0]
                L = BearingLine(oid, o0.lat, o0.lon, mean, sig, max(1, round(eff)), spread,
                                [o.detection_id for o, w in items if o.detection_id is not None and w > 0.5],
                                distance_m=None)
                L.x, L.y = frame.to_xy(L.lat, L.lon)
                group.append(L)
            if len({L.observer_id for L in group}) < 2:
                continue
            before = len(nests)
            nests_backup = nests
            nests = []
            lines_backup, used_backup = lines, set(used)
            lines = group
            build(list(range(len(group))), cx, cy)
            lines, used = lines_backup, used_backup
            if nests:
                est = nests[0]
                est.detection_ids = sorted({d for o, w in zip(observations, weights)
                                            if c in w and w[c] > 0.5 and (d := o.detection_id) is not None})
                new_nests.append(est)
                new_centres.append(frame.to_xy(est.lat, est.lon))
            nests = nests_backup
        # merge refinements that converged on the same nest
        merged_n, merged_c = [], []
        for est, cxy in sorted(zip(new_nests, new_centres), key=lambda t: -t[0].n_departures):
            if all(math.hypot(cxy[0] - q[0], cxy[1] - q[1]) > cluster_radius_m * 0.6 for q in merged_c):
                merged_n.append(est); merged_c.append(cxy)
        moved = (len(merged_c) != len(centres) or
                 any(math.hypot(a[0] - b[0], a[1] - b[1]) > 5 for a, b in zip(merged_c, centres)))
        tri, centres = merged_n, merged_c
        if not moved:
            break
    if weights or not centres:
        # an original line counts as explained if most of its departures now
        # belong to a nest; the rest stay as direction-only corridors
        claimed = {o.detection_id for o, w in zip(observations, weights)
                   if o.detection_id is not None and w and max(w.values()) > 0.5}
        used = {i for i, L in enumerate(lines)
                if L.detection_ids and sum(d in claimed for d in L.detection_ids) >= 0.5 * len(L.detection_ids)}
        nests = [n for n in nests if n.method != "triangulated"] + tri

    # 5. single lines with a distance from round-trip timing
    for i, L in enumerate(lines):
        if i in used or not L.distance_m:
            continue
        ux, uy = bearing_unit(L.bearing)
        x, y = L.x + ux * L.distance_m, L.y + uy * L.distance_m
        lat, lon = frame.to_ll(x, y)
        lateral = L.distance_m * math.radians(L.sigma) * math.sqrt(CHI2_95_2D)
        along = max(L.distance_m * 0.4, min_ellipse_m)   # timing is rough
        used.add(i)
        nests.append(NestEstimate(
            lat=lat, lon=lon, semi_major_m=max(along, lateral), semi_minor_m=min(along, lateral),
            orientation_deg=(L.bearing if along >= lateral else L.bearing + 90) % 180,
            n_lines=1, n_observers=1, n_departures=L.n, method="single_line_distance",
            line_keys=[line_key(L)], detection_ids=L.detection_ids, rms_residual_deg=0.0,
        ))

    nests.sort(key=lambda n: (-n.n_observers, -n.n_departures))
    unused = [line_key(L) for i, L in enumerate(lines) if i not in used]
    return Result(nests, lines, unused, frame)


# ================================================================ planning
# Where should the next smart hive go?
#
# A new hive helps only if (a) hornets from the nest actually visit it, which
# falls off with distance from the nest, and (b) its line crosses the
# existing ones at a good angle. Both are predictable before installing
# anything: the uncertainty of a crossing follows from geometry alone
# (information matrix of the bearing lines at the nest), no hornets needed.

EXPECTED_NEW_SIGMA = 8.0      # a hive with ~10 departures and a compass-set heading
FORAGE_SCALE_M = 800.0        # visit chance ~ exp(-distance / this)
MAX_VISIT_M = 1800.0
EXPECTED_VISITS_AT_NEST = 14.0  # departures a hive right next to a nest logs per recompute window
MIN_DEPARTURES_FOR_LINE = 3


def p_line(d: float) -> float:
    """Chance a hive this far from the nest logs enough departures to give a
    direction (Poisson, visits falling off with foraging distance)."""
    lam = EXPECTED_VISITS_AT_NEST * visit_chance(d)
    return 1.0 - sum(math.exp(-lam) * lam ** k / math.factorial(k) for k in range(MIN_DEPARTURES_FOR_LINE))


def useful(r: float, now: float | None) -> float:
    """How much a predicted search radius is worth, 0..1."""
    if not math.isfinite(r):
        return 0.0
    if now is not None and math.isfinite(now):
        return max(0.0, now - r) / now
    return 1.0 / (1.0 + (r / 350.0) ** 2)      # ±350 m counts as half a success


@dataclass
class Target:
    kind: str                     # "corridor" | "nest"
    key: str                      # line key or "nest:<index>"
    origin: tuple[float, float] | None   # corridor start (x, y)
    bearing: float | None
    points: list[tuple[float, float]]    # plausible nest positions (x, y)
    lines: list[BearingLine]             # what we already have pointing there
    now_radius: float | None             # None = direction only, no area yet
    weights: list[float] | None = None   # how plausible each point is (centre of the wedge most)


def visit_chance(d: float) -> float:
    return 0.0 if d > MAX_VISIT_M else math.exp(-d / FORAGE_SCALE_M)


def _ray(ox, oy, x, y, sigma):
    """A hypothetical line from (ox, oy) that points exactly at (x, y)."""
    L = BearingLine("plan", 0, 0, math.degrees(math.atan2(x - ox, y - oy)) % 360, sigma, 10, sigma)
    L.x, L.y = ox, oy
    return L


def predicted_radius(lines: list[BearingLine], x: float, y: float) -> float:
    """95 % search radius we'd get at (x, y) from these lines, assuming they
    all point at it. inf if they can't fix a position (fewer than 2 usable
    directions, or all parallel)."""
    A = [[0.0, 0.0], [0.0, 0.0]]
    for L in lines:
        d = math.hypot(x - L.x, y - L.y)
        if d < 1:
            continue
        b = math.atan2(x - L.x, y - L.y)
        nx, ny = -math.cos(b), math.sin(b)
        w = 1.0 / (d * math.radians(L.sigma)) ** 2
        A[0][0] += w * nx * nx; A[0][1] += w * nx * ny; A[1][1] += w * ny * ny
    A[1][0] = A[0][1]
    det = A[0][0] * A[1][1] - A[0][1] ** 2
    trace = A[0][0] + A[1][1]
    if det <= 1e-30 or det < 1e-6 * trace * trace:
        return math.inf
    cov = [[A[1][1] / det, -A[0][1] / det], [-A[1][0] / det, A[0][0] / det]]
    a, b, _ = ellipse(cov)
    inflate = 1.2 if len(lines) >= 3 else 1.5
    return max(math.sqrt(a * b) * inflate, 100.0)


def planning_targets(result: Result, frame: LocalFrame, max_range_m: float = 3000.0,
                     wide_radius_m: float = 180.0) -> list[Target]:
    targets: list[Target] = []
    unexplained = set(result.unused_line_keys)
    for L in result.lines:
        if line_key(L) not in unexplained:
            continue
        ux, uy = bearing_unit(L.bearing)
        # where along the corridor is the nest? Usually within ~1.5 km of a hive
        # it hunts at; a round-trip time narrows that down when we have one.
        if L.distance_m:
            dists = [L.distance_m * f for f in (0.7, 1.0, 1.3)]
        else:
            dists = [300, 600, 900, 1200, 1500]
        # The corridor's own direction is uncertain (few departures = wide
        # wedge). Plan against the whole wedge, or a site that only looks
        # beside the measured line may in fact sit on the true one, and two
        # collinear hives can never cross.
        spread = min(max(L.sigma, 4.0), 30.0) * 1.5
        pts, wts = [], []
        for off in (-spread, -spread / 2, 0.0, spread / 2, spread):
            vx, vy = bearing_unit(L.bearing + off)
            w = math.exp(-0.5 * (off / (spread / 1.5)) ** 2)      # Gaussian in units of sigma
            for d in dists:
                if d <= max_range_m:
                    pts.append((L.x + vx * d, L.y + vy * d)); wts.append(w)
        targets.append(Target("corridor", line_key(L), (L.x, L.y), L.bearing, pts, [L], None, wts))
    by_key = {line_key(L): L for L in result.lines}
    for i, n in enumerate(result.nests):
        if n.method != "triangulated" or n.radius_m <= wide_radius_m:
            continue
        x, y = frame.to_xy(n.lat, n.lon)
        targets.append(Target("nest", f"nest:{i}", None, None, [(x, y)],
                              [by_key[k] for k in n.line_keys if k in by_key], n.radius_m))
    return targets


def evaluate_site(t: Target, px: float, py: float,
                  planned: list[tuple[float, float]] = ()) -> dict:
    """What would a hive at (px, py) do for this target?"""
    radii, chances, value = [], [], []
    now = baseline(t, planned)
    for (x, y) in t.points:
        base = list(t.lines) + [_ray(qx, qy, x, y, EXPECTED_NEW_SIGMA) for qx, qy in planned
                                if visit_chance(math.hypot(qx - x, qy - y)) > 0.25]
        d = math.hypot(px - x, py - y)
        r = predicted_radius(base + [_ray(px, py, x, y, EXPECTED_NEW_SIGMA)], x, y)
        chances.append(p_line(d))
        radii.append(r)
        # for each place the nest could be: does the hive get a line, and
        # does that line cross well? Both must happen.
        value.append(p_line(d) * useful(r, now))
    ordered = sorted(radii)                       # inf counts: impossible geometry
    median = ordered[len(ordered) // 2] if ordered else math.inf
    finite = [r for r in ordered if math.isfinite(r)]
    chance = sum(chances) / len(chances) if chances else 0.0
    # Before the hive exists we don't know where along a corridor the nest
    # is, so report a range (checked against simulation: the realised
    # radius is median ~0.9x the middle value, with a long upper tail).
    lo = finite[0] if finite else math.inf
    hi = max(ordered[int(len(ordered) * 0.8)] if ordered else math.inf, median * 1.8)
    w = t.weights or [1.0] * len(value)
    return {"radius_m": median, "radius_low_m": lo, "radius_high_m": hi, "visit_chance": chance,
            "value": sum(v * wi for v, wi in zip(value, w)) / sum(w) if value else 0.0}


def baseline(t: Target, planned: list[tuple[float, float]] = ()) -> float:
    """Search radius for this target with the hives we have plus planned ones."""
    radii = []
    for (x, y) in t.points:
        lines = list(t.lines) + [_ray(qx, qy, x, y, EXPECTED_NEW_SIGMA) for qx, qy in planned
                                 if visit_chance(math.hypot(qx - x, qy - y)) > 0.25]
        radii.append(predicted_radius(lines, x, y))
    finite = sorted(r for r in radii if math.isfinite(r))
    return finite[len(finite) // 2] if len(finite) * 2 >= len(radii) and finite else math.inf


def _in_corridor(t: Target, px: float, py: float) -> bool:
    """Inside the corridor's uncertainty wedge (or its extension behind the
    hive) a new hive would look along the same line as the old one: wherever
    the nest really is in that wedge, the two lines barely cross."""
    L = t.lines[0]
    to = math.degrees(math.atan2(px - L.x, py - L.y)) % 360
    half = min(max(L.sigma, 4.0), 30.0) * 1.5 + 10.0
    off = abs(ang_diff(to, L.bearing))
    return off < half or off > 180 - half


def _min_crossing(t: Target, px: float, py: float) -> float:
    """Worst crossing angle (degrees, 0-90) between the corridor and a new
    hive's line, over the most plausible nest positions."""
    L = t.lines[0]
    worst = 90.0
    for (x, y), w in zip(t.points, t.weights or [1.0] * len(t.points)):
        if w < 0.7:
            continue
        b1 = math.degrees(math.atan2(x - L.x, y - L.y))
        b2 = math.degrees(math.atan2(x - px, y - py))
        a = abs(ang_diff(b1, b2))
        worst = min(worst, min(a, 180 - a))
    return worst


def suggest_sites(t: Target, existing: list[tuple[float, float]], planned: list[tuple[float, float]] = (),
                  k: int = 3, min_spacing_m: float = 500.0, keep_clear_m: float = 250.0) -> list[dict]:
    """Best places for a new hive for this target, most useful first."""
    cx = sum(p[0] for p in t.points) / len(t.points)
    cy = sum(p[1] for p in t.points) / len(t.points)
    now = baseline(t, planned)
    cands = []
    for r in (300, 500, 700, 900, 1150, 1400):
        for a in range(0, 360, 10):
            px, py = cx + r * math.sin(math.radians(a)), cy + r * math.cos(math.radians(a))
            if any(math.hypot(px - ex, py - ey) < keep_clear_m for ex, ey in list(existing) + list(planned)):
                continue
            if t.kind == "corridor" and (_in_corridor(t, px, py) or _min_crossing(t, px, py) < 30):
                continue
            ev = evaluate_site(t, px, py, planned)
            if not math.isfinite(ev["radius_m"]) or ev["visit_chance"] < 0.2:
                continue
            cands.append((ev["value"], px, py, ev))
    cands.sort(key=lambda c: -c[0])
    picked: list[tuple] = []
    for c in cands:
        if c[0] <= 0.02:
            break
        if all(math.hypot(c[1] - p[1], c[2] - p[2]) >= min_spacing_m for p in picked):
            picked.append(c)
        if len(picked) == k:
            break
    return [{"x": p[1], "y": p[2], "score": p[0], **p[3]} for p in picked]
