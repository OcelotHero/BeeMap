"""
Forage, hive-siting and planting model for BeeMap.

Everything is computed on a 50 m raster of the city:

  * flowering supply per month: each land-cover type has a monthly
    "flowering cover" profile (0..1 = share of the ground in useful bloom).
    Summed over an 800 m radius it says how much food bees find around a
    point in each month. Citizens' plantings are added on top.
  * wild-bee zones: habitats to keep free of extra honeybee competition.
  * apiaries and hornet nest areas come from the live database.

The profiles and thresholds are model assumptions for the demo, chosen to be
plausible for south-west Germany; they are named constants so ecologists can
tune them. They are not measurements.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np

import geodata

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
RADIUS_M = 800.0          # radius used throughout the app (as in the screen designs)
CELL_M = 50.0
BBOX = (49.080, 9.120, 49.200, 9.310)   # lat0, lon0, lat1, lon1

# Monthly share of ground in useful bloom, Jan..Dec (model assumptions)
PROFILES = {
    "background":   [0, 0, .02, .03, .03, .03, .03, .02, .02, .01, 0, 0],
    "urban":        [0, 0, .02, .04, .04, .04, .04, .03, .03, .01, 0, 0],
    "arable":       [0, 0, 0, .01, .02, .02, .01, .01, .01, 0, 0, 0],
    "rapeseed":     [0, 0, 0, .9, .7, 0, 0, 0, 0, 0, 0, 0],
    "gardens":      [0, .03, .15, .30, .35, .35, .35, .30, .25, .10, 0, 0],
    "park":         [0, 0, .10, .20, .25, .25, .25, .20, .15, .05, 0, 0],
    "lime_avenue":  [0, 0, 0, .05, .10, .90, .60, 0, 0, 0, 0, 0],
    "vineyard":     [0, 0, .03, .10, .10, .05, .05, .05, .05, 0, 0, 0],
    "orchard":      [0, 0, .08, .85, .55, .30, .30, .20, .10, 0, 0, 0],
    "forest":       [0, 0, .10, .15, .08, .12, .08, 0, 0, 0, 0, 0],
    "hedge":        [0, .10, .70, .80, .60, .30, .20, .10, .10, .05, 0, 0],
    "meadow":       [0, 0, .05, .20, .55, .65, .55, .40, .20, .05, 0, 0],
    "flower_strip": [0, 0, 0, .10, .40, .80, .90, .80, .60, .20, 0, 0],
    "grass":        [0, 0, .02, .05, .05, .04, .03, .03, .02, 0, 0, 0],     # mown lawns and verges
    "tree_row":     [0, 0, .10, .30, .20, .40, .30, 0, 0, 0, 0, 0],         # street trees: maple, lime, fruit...
    "water":        [0] * 12,
}
# How much bloom an 800 m circle needs per month to count as enough
# (share of the circle's ground in useful bloom). Calibrated so that a typical
# Heilbronn neighbourhood just meets it in the main season (Apr-Jul); early
# spring and late summer then show up as the usual urban gaps. A model
# choice for the demo, to be replaced by expert targets.
TARGET = np.array([0, 0, .025, .032, .032, .032, .032, .025, .022, .008, 0, 0])
SEASON_MONTHS = list(range(2, 10))   # Mar..Oct are judged

# Hive spot check thresholds
APIARIES_WARN, APIARIES_BAD = 2, 3            # apiaries within the radius
HORNET_NEAR_M, HORNET_WARN_M = 300.0, 1500.0  # from the edge of a nest area


# =================================================================== plants
@dataclass(frozen=True)
class Plant:
    id: str
    name: str
    german: str
    latin: str
    months: tuple[int, ...]       # 1..12
    kind: str                     # perennial | shrub | tree | climber | annual crop | cover crop | seed mix
    settings: tuple[str, ...]     # balcony | garden | public | hedge | farm
    native: bool
    wild_bees: int                # 1..3
    honeybees: int                # 1..3
    m2: float                     # typical flowering area one planting gives
    tip: str


PLANTS = [
    # natives: early
    Plant("salix", "Goat willow", "Sal-Weide", "Salix caprea", (3, 4), "shrub", ("garden", "public", "hedge", "farm"),
          True, 3, 3, 25, "Plant a male tree for pollen. One of the first big meals of the year."),
    Plant("cornus", "Cornelian cherry", "Kornelkirsche", "Cornus mas", (2, 3), "shrub", ("garden", "public", "hedge"),
          True, 2, 3, 15, "Flowers before its leaves, on warm days from February."),
    Plant("prunus", "Blackthorn", "Schlehe", "Prunus spinosa", (3, 4), "shrub", ("hedge", "farm", "public"),
          True, 3, 2, 20, "Ideal for field edges and hedgerows."),
    Plant("pulmonaria", "Lungwort", "Lungenkraut", "Pulmonaria officinalis", (3, 4, 5), "perennial",
          ("balcony", "garden"), True, 3, 1, 1, "Likes shade. A favourite of early bumblebee queens."),
    Plant("taraxacum", "Dandelion", "Löwenzahn", "Taraxacum sect. Ruderalia", (4, 5), "perennial",
          ("garden", "public", "farm"), True, 2, 3, 5, "Mow lawns later and less often: it is already there."),
    Plant("crataegus", "Hawthorn", "Eingriffeliger Weißdorn", "Crataegus monogyna", (5,), "shrub",
          ("hedge", "farm", "public"), True, 2, 2, 20, "Robust hedge plant, also good for birds."),
    # natives: summer
    Plant("salvia", "Meadow sage", "Wiesen-Salbei", "Salvia pratensis", (5, 6, 7), "perennial",
          ("balcony", "garden", "public"), True, 3, 2, 1, "Sunny and dry. Cut back after flowering for a second bloom."),
    Plant("lotus", "Bird's-foot trefoil", "Gewöhnlicher Hornklee", "Lotus corniculatus", (5, 6, 7, 8), "perennial",
          ("garden", "public", "farm"), True, 3, 2, 2, "Low-growing, fine in lawns and road verges."),
    Plant("echium", "Viper's bugloss", "Gewöhnlicher Natternkopf", "Echium vulgare", (6, 7, 8, 9), "perennial",
          ("balcony", "garden", "public"), True, 3, 3, 1, "Flowers for months. Needs sun and poor soil."),
    Plant("campanula", "Bellflower", "Glockenblume", "Campanula rotundifolia / C. patula", (6, 7, 8), "perennial",
          ("balcony", "garden"), True, 3, 1, 1, "Some wild bees feed only on bellflowers."),
    Plant("knautia", "Field scabious", "Acker-Witwenblume", "Knautia arvensis", (6, 7, 8), "perennial",
          ("garden", "public", "farm"), True, 3, 2, 1, "Host of the rare knautia mining bee."),
    Plant("centaurea", "Brown knapweed", "Wiesen-Flockenblume", "Centaurea jacea", (6, 7, 8, 9), "perennial",
          ("garden", "public", "farm"), True, 3, 2, 1, "Long-flowering meadow plant."),
    Plant("thymus", "Broad-leaved thyme", "Arznei-Thymian", "Thymus pulegioides", (6, 7, 8), "perennial",
          ("balcony", "garden"), True, 2, 3, 1, "Perfect for sunny balcony boxes."),
    Plant("tilia", "Small-leaved lime", "Winter-Linde", "Tilia cordata", (6, 7), "tree", ("public",),
          True, 2, 3, 80, "A street tree that feeds thousands of bees for weeks."),
    Plant("rubus", "Bramble", "Brombeere", "Rubus fruticosus agg.", (6, 7, 8), "shrub", ("hedge", "farm"),
          True, 3, 3, 10, "Leave a corner wild; dry stems are nest sites."),
    # natives: late
    Plant("origanum", "Wild marjoram", "Gewöhnlicher Dost", "Origanum vulgare", (7, 8, 9), "perennial",
          ("balcony", "garden", "public"), True, 3, 3, 1, "Late-summer magnet, also a kitchen herb."),
    Plant("hylotelephium", "Orpine", "Große Fetthenne", "Hylotelephium telephium", (8, 9), "perennial",
          ("balcony", "garden"), True, 2, 2, 1, "Drought-proof, flowers when little else does."),
    Plant("aster", "Italian aster", "Berg-Aster", "Aster amellus", (8, 9, 10), "perennial", ("garden",),
          True, 2, 2, 1, "Protected in the wild: buy nursery-grown plants."),
    Plant("hedera", "Ivy", "Efeu", "Hedera helix", (9, 10), "climber", ("garden", "public", "hedge"),
          True, 3, 3, 10, "Let old ivy flower: the last big food before winter, and the ivy bee depends on it."),
    # farm crops and cover crops
    Plant("phacelia", "Phacelia", "Büschelschön", "Phacelia tanacetifolia", (6, 7, 8, 9), "cover crop", ("farm",),
          False, 1, 3, 10000, "Not native. Flowers about 6–8 weeks after sowing; stagger sowings from April to "
          "August. Use on fields, not in natural areas."),
    Plant("buckwheat", "Buckwheat", "Buchweizen", "Fagopyrum esculentum", (7, 8, 9), "cover crop", ("farm",),
          False, 1, 3, 10000, "Fast catch crop after cereal harvest, flowers in late summer."),
    Plant("mustard", "White mustard", "Weißer Senf", "Sinapis alba", (8, 9, 10), "cover crop", ("farm",),
          False, 1, 2, 10000, "Catch crop sown after harvest; late food before winter."),
    Plant("sunflower", "Sunflower", "Sonnenblume", "Helianthus annuus", (7, 8, 9), "annual crop", ("farm", "garden"),
          False, 2, 3, 10000, "Choose pollen-rich, non-sterile varieties."),
    Plant("trifolium", "Red clover", "Rot-Klee", "Trifolium pratense", (5, 6, 7, 8, 9), "annual crop", ("farm",),
          True, 3, 2, 10000, "Let leys flower before cutting; leave strips uncut."),
    Plant("onobrychis", "Sainfoin", "Saat-Esparsette", "Onobrychis viciifolia", (5, 6, 7), "annual crop", ("farm",),
          True, 3, 3, 10000, "Perennial forage legume for chalky soils; long established in the region."),
    Plant("fieldbean", "Field bean", "Ackerbohne", "Vicia faba", (5, 6), "annual crop", ("farm",),
          False, 2, 2, 10000, "Protein crop, early-summer food for bumblebees."),
    Plant("regio_mix", "Regional flower strip", "Blühstreifen aus Regiosaatgut", "certified regional seed mix",
          (5, 6, 7, 8, 9), "seed mix", ("farm", "public"), True, 3, 3, 2000,
          "Perennial strip from certified regional seed for your area; leave standing over winter. "
          "Agri-environment funding may apply (in Baden-Württemberg: FAKT)."),
    Plant("fruit", "Orchard fruit trees", "Streuobst (Apfel, Birne, Kirsche)", "Malus, Pyrus, Prunus avium",
          (4, 5), "tree", ("farm", "public", "garden"), False, 2, 3, 60,
          "Traditional orchard meadows are a Heilbronn speciality and rich habitats."),
]
PLANTS_BY_ID = {p.id: p for p in PLANTS}

# ------------------------------------------------------------- bloom data
# Observed flowering months pulled from iNaturalist by
# tools/update_bloom_data.py. Without that file the curated months above are
# used, and the app says so.
BLOOM_FILE = Path(__file__).with_name("bloom_data.json")
BLOOM: dict = {}


def load_bloom() -> dict:
    global BLOOM
    try:
        BLOOM = json.loads(BLOOM_FILE.read_text())
    except (OSError, ValueError):
        BLOOM = {}
    return BLOOM


def bloom_of(p: Plant) -> dict:
    """Bloom months for a plant: observed if we have them, else curated."""
    d = (BLOOM.get("plants") or {}).get(p.id)
    if d and d.get("months"):
        return {"months": tuple(d["months"]), "peak": d.get("peak", []), "share": d.get("share"),
                "source": "observed", "n_obs": d.get("n_obs"), "place": d.get("place"), "taxon_id": d.get("taxon_id")}
    return {"months": p.months, "peak": [], "share": None, "source": "curated"}


load_bloom()

# ------------------------------------------------------------- planting time
# When to plant or sow, so the plant flowers in the months that are short.
# General horticultural guidance for south-west Germany (curated, not data).
# weeks = sowing to first flowers; establish = years before full flowering.
TIMING_BY_KIND = {
    "shrub":     {"verb": "Plant", "months": (10, 11, 12, 1, 2, 3), "window": "October to March, when the ground isn't frozen", "establish": 0},
    "tree":      {"verb": "Plant", "months": (10, 11, 12, 1, 2, 3), "window": "October to March, when the ground isn't frozen", "establish": 0},
    "perennial": {"verb": "Plant", "months": (3, 4, 5, 9, 10), "window": "March to May, or September to October", "establish": 1,
                  "note": "Many wild perennials flower properly only from their second year."},
    "climber":   {"verb": "Plant", "months": (3, 4, 5, 9, 10), "window": "March to May, or September to October", "establish": 1},
    "seed mix":  {"verb": "Sow", "months": (9, 10, 3, 4, 5), "window": "September to October (best), or March to May", "establish": 1,
                  "note": "A perennial mix flowers mainly from the second year. Leave it standing over winter."},
}
TIMING_BY_PLANT = {
    "phacelia":   {"verb": "Sow", "months": (4, 5, 6, 7, 8), "weeks": 7, "window": "April to August; flowers about 6–8 weeks later"},
    "buckwheat":  {"verb": "Sow", "months": (5, 6, 7), "weeks": 5, "window": "May to July; flowers about 5 weeks later"},
    "mustard":    {"verb": "Sow", "months": (8, 9), "weeks": 6, "window": "August to September, after harvest"},
    "sunflower":  {"verb": "Sow", "months": (4, 5), "weeks": 11, "window": "April to May, after the last frosts"},
    "fieldbean":  {"verb": "Sow", "months": (2, 3, 4), "weeks": 10, "window": "February to April"},
    "trifolium":  {"verb": "Sow", "months": (4, 5, 6, 7, 8), "window": "April to August", "establish": 1,
                   "note": "Flowers fully from the year after sowing; leave strips uncut."},
    "onobrychis": {"verb": "Sow", "months": (4, 5, 8), "window": "April to May, or August", "establish": 1,
                   "note": "Establishes slowly; main flowering from the second year."},
    "hedera":     {"verb": "Plant", "months": (3, 4, 5, 9, 10), "window": "March to May, or September to October", "establish": 5,
                   "note": "Ivy flowers only on old, bushy growth: protecting existing ivy helps bees most."},
    "fruit":      {"verb": "Plant", "months": (10, 11, 12, 1, 2, 3), "window": "October to March", "establish": 2,
                   "note": "Young fruit trees take a few years to flower well."},
    "tilia":      {"verb": "Plant", "months": (10, 11, 12, 1, 2, 3), "window": "October to March", "establish": 5,
                   "note": "A long-term street-tree investment."},
}


def timing(p: Plant, gap_months: list[int], today: date | None = None) -> dict:
    """When to plant or sow p so it flowers in the gap months."""
    today = today or date.today()
    info = TIMING_BY_PLANT.get(p.id) or TIMING_BY_KIND.get(p.kind) or TIMING_BY_KIND["perennial"]
    bloom = bloom_of(p)["months"]
    targets = [m for m in bloom if m in gap_months] or list(bloom)
    window = info["months"]
    now_abs = today.year * 12 + today.month - 1          # absolute month index
    first = None
    for k in range(0, 36):                                # next planting/sowing month
        a = now_abs + k
        if a % 12 + 1 in window:
            first = a
            break
    if first is None:
        return {}
    if info.get("weeks"):
        lag = max(1, round(info["weeks"] / 4.33))
        for k in range(0, 36):                            # earliest gap month a sowing can reach
            t = now_abs + k
            sow = t - lag
            if t % 12 + 1 in targets and sow >= now_abs and sow % 12 + 1 in window:
                act, flowers = sow, t
                break
        else:
            act, flowers = first, first + lag
    else:
        act = first
        est = info.get("establish", 0)
        flowers = next((act + k for k in range(1, 36) if (act + k) % 12 + 1 in targets
                        and (act + k) // 12 >= act // 12 + (1 if est and (act % 12 + 1) >= 9 else 0)), act + 12)
        flowers += 12 * max(0, est - 1)
    fmt = lambda a: f"{MONTHS[a % 12]} {a // 12}"
    return {"verb": info["verb"], "window": info["window"], "now": today.month in window,
            "step": f"{info['verb']} in {_full(act % 12)}{' ' + str(act // 12) if act // 12 != today.year else ''}",
            "flowers": fmt(flowers), "full_from": fmt(flowers + (12 if info.get("establish") == 1 else 0)) if info.get("establish") == 1 else None,
            "note": info.get("note", "")}
AVOID = ("Avoid Himalayan balsam and Canadian goldenrod: bees visit them, but they are invasive "
         "and push out native plants.")


# =================================================================== sensors
# SensoBox pollen measurements correct the land-cover model where they exist.
#
# Airborne pollen is dominated by wind-pollinated plants (grasses, birch,
# pine...) that bees hardly use, while most bee plants release little pollen
# into the air. Counts of wind pollen run into the hundreds of grains/m³ and
# would swamp the bee signal even at a small weight, so they count zero.
# An untyped total can't tell bee food from grass and birch, so it is shown
# but NOT used to correct the map: that needs a sensor that identifies types.
# Weights are model assumptions.
POLLEN_BEE_WEIGHT = {
    "salix": 1.0, "tilia": 1.0, "brassicaceae": 0.9, "rosaceae": 1.0, "taraxacum": 1.0, "trifolium": 1.0,
    "phacelia": 1.0, "fabaceae": 0.9, "acer": 0.8, "castanea": 0.8, "aesculus": 0.8, "echium": 1.0,
    "hedera": 1.0, "corylus": 0.4, "alnus": 0.3, "quercus": 0.0, "plantago": 0.0, "betula": 0.0,
    "fagus": 0.0, "fraxinus": 0.0, "poaceae": 0.0, "pinaceae": 0.0, "urticaceae": 0.0,
    "artemisia": 0.0, "ambrosia": 0.0, "cupressaceae": 0.0, "other": 0.3,
}
UNTYPED_WEIGHT = 0.0          # untyped totals are not used (see above)
SENSOR_RANGE_M = 1500.0       # how far one box's evidence reaches (Gaussian scale)
PRIOR_WEIGHT = 0.5            # how strongly the model holds where boxes are far away
MIN_DAYS_PER_MONTH = 5


def bee_pollen(types: dict | None, total: float | None) -> tuple[float | None, bool]:
    """Bee-relevant pollen from one reading: (value, typed?)."""
    if types:
        return sum(float(v) * POLLEN_BEE_WEIGHT.get(k.lower(), POLLEN_BEE_WEIGHT["other"])
                   for k, v in types.items()), True
    return None, False


def monthly_index(daily: list[tuple[int, float]]) -> list[float | None]:
    """Mean daily bee-pollen per calendar month; None if too few days."""
    by = [[] for _ in range(12)]
    for month, v in daily:
        by[month - 1].append(v)
    return [sum(v) / len(v) if len(v) >= MIN_DAYS_PER_MONTH else None for v in by]


def flight_share(readings: list[dict]) -> float | None:
    """Share of daylight readings in which honeybees can fly: at least 12 °C,
    not saturated air (rain or fog), enough light. A rough rule of thumb."""
    day = [r for r in readings if (r.get("lux") or 0) >= 1000 and r.get("temp_c") is not None]
    if len(day) < 3:
        return None
    ok = [r for r in day if r["temp_c"] >= 12 and (r.get("humidity") is None or r["humidity"] < 90)]
    return len(ok) / len(day)


# =================================================================== raster
class World:
    """Raster of the city. Built once; plantings update the supply layer."""

    def __init__(self):
        lat0, lon0, lat1, lon1 = BBOX
        self.lat0, self.lon0 = lat0, lon0
        self.ky = geodata.M_PER_DEG
        self.kx = geodata.M_PER_DEG * math.cos(math.radians((lat0 + lat1) / 2))
        self.ny = int((lat1 - lat0) * self.ky / CELL_M)
        self.nx = int((lon1 - lon0) * self.kx / CELL_M)
        ys = (np.arange(self.ny) + 0.5) * CELL_M
        xs = (np.arange(self.nx) + 0.5) * CELL_M
        self.X, self.Y = np.meshgrid(xs, ys)

        kinds = list(PROFILES)
        self.kind_index = {k: i for i, k in enumerate(kinds)}
        lc = np.full((self.ny, self.nx), self.kind_index["background"], dtype=np.int16)
        for f in geodata.LANDCOVER:
            k = self.kind_index.get(f["kind"])
            if k is not None:
                self._paint(lc, f["polys"], k)
        self.landcover = lc
        self.water = lc == self.kind_index["water"]
        self.forest = lc == self.kind_index["forest"]
        self.wild = np.zeros_like(self.water)
        for z in geodata.WILD_BEE_ZONES:
            self._paint(self.wild, z["polys"], True)

        prof = np.array([PROFILES[k] for k in kinds], dtype=float)   # kinds x 12
        self.base_bloom = prof[lc]                                    # ny x nx x 12
        # hedges and tree rows: narrow strips, added as a share of each cell
        for ln in geodata.LINES:
            p = np.array(PROFILES.get(ln["kind"], PROFILES["hedge"]))
            pts = ln["coords"]
            for (la1, lo1), (la2, lo2) in zip(pts, pts[1:]):
                x1, y1 = self.xy(la1, lo1)
                x2, y2 = self.xy(la2, lo2)
                seg = math.hypot(x2 - x1, y2 - y1)
                n = max(1, int(seg // 10))
                for t in range(n):
                    f = (t + 0.5) / n
                    i, j = int((y1 + (y2 - y1) * f) // CELL_M), int((x1 + (x2 - x1) * f) // CELL_M)
                    if 0 <= i < self.ny and 0 <= j < self.nx:
                        share = (seg / n) * ln["width_m"] / (CELL_M * CELL_M)
                        self.base_bloom[i, j] = np.minimum(1.0, self.base_bloom[i, j] + share * p)
        self.kernel_fft = None
        self.plantings: list[dict] = []
        self.sensors: list[dict] = []
        self.correction = np.ones((self.ny, self.nx, 12))
        self.confidence = np.zeros((self.ny, self.nx, 12))
        self.wild_share = self._circle_mean(self.wild.astype(float))
        self.rebuild()

    # ---- geometry
    def xy(self, lat, lon):
        return (lon - self.lon0) * self.kx, (lat - self.lat0) * self.ky

    def ll(self, x, y):
        return self.lat0 + y / self.ky, self.lon0 + x / self.kx

    def cell(self, lat, lon):
        x, y = self.xy(lat, lon)
        i, j = int(y // CELL_M), int(x // CELL_M)
        if 0 <= i < self.ny and 0 <= j < self.nx:
            return i, j
        return None

    def _paint(self, target, polys, value):
        """Set target[cells inside the polygons] = value, holes excluded.
        Works on each polygon's bounding box only: OSM has thousands."""
        for p in polys:
            outer = np.array([self.xy(la, lo) for la, lo in p["outer"]])
            i0 = max(0, int(outer[:, 1].min() // CELL_M)); i1 = min(self.ny, int(outer[:, 1].max() // CELL_M) + 1)
            j0 = max(0, int(outer[:, 0].min() // CELL_M)); j1 = min(self.nx, int(outer[:, 0].max() // CELL_M) + 1)
            if i0 >= i1 or j0 >= j1:
                continue
            X, Y = self.X[i0:i1, j0:j1], self.Y[i0:i1, j0:j1]
            m = self._ring(outer, X, Y)
            for h in p.get("inner", []):
                m &= ~self._ring(np.array([self.xy(la, lo) for la, lo in h]), X, Y)
            target[i0:i1, j0:j1][m] = value

    @staticmethod
    def _ring(pts, x, y):
        inside = np.zeros(x.shape, dtype=bool)
        n = len(pts)
        for k in range(n):
            x1, y1 = pts[k]
            x2, y2 = pts[(k + 1) % n]
            cond = (y1 > y) != (y2 > y)
            with np.errstate(divide="ignore", invalid="ignore"):
                xint = (x2 - x1) * (y - y1) / (y2 - y1) + x1
            inside ^= cond & (x < xint)
        return inside

    def _mask(self, poly):
        pts = np.array([self.xy(la, lo) for la, lo in poly])
        x, y = self.X, self.Y
        inside = np.zeros(x.shape, dtype=bool)
        n = len(pts)
        for k in range(n):
            x1, y1 = pts[k]
            x2, y2 = pts[(k + 1) % n]
            cond = (y1 > y) != (y2 > y)
            with np.errstate(divide="ignore", invalid="ignore"):
                xint = (x2 - x1) * (y - y1) / (y2 - y1) + x1
            inside ^= cond & (x < xint)
        return inside

    # ---- 800 m circle sums via FFT convolution
    def _circle_mean(self, layer: np.ndarray) -> np.ndarray:
        r = int(RADIUS_M // CELL_M)
        if self.kernel_fft is None:
            k = np.zeros((self.ny + 2 * r, self.nx + 2 * r))
            yy, xx = np.mgrid[-r:r + 1, -r:r + 1]
            disk = (xx ** 2 + yy ** 2) <= r * r
            k[:2 * r + 1, :2 * r + 1] = disk
            self.disk_cells = disk.sum()
            self.kernel_fft = np.fft.rfft2(k)
            self._r = r
        padded = np.zeros((self.ny + 2 * r, self.nx + 2 * r))
        padded[r:r + self.ny, r:r + self.nx] = layer
        conv = np.fft.irfft2(np.fft.rfft2(padded) * self.kernel_fft, s=padded.shape)
        return conv[2 * r:2 * r + self.ny, 2 * r:2 * r + self.nx] / self.disk_cells

    def rebuild(self):
        """Recompute monthly supply around every cell (after new plantings)."""
        bloom = self.base_bloom.copy()
        cell_area = CELL_M * CELL_M
        for p in self.plantings:
            c = self.cell(p["lat"], p["lon"])
            if not c:
                continue
            plant = PLANTS_BY_ID.get(p["plant_id"])
            months = bloom_of(plant)["months"] if plant else range(4, 9)
            for m in months:
                bloom[c[0], c[1], m - 1] = min(1.0, bloom[c[0], c[1], m - 1] + p["area_m2"] / cell_area)
        self.model_supply = np.stack([self._circle_mean(bloom[:, :, m]) for m in range(12)], axis=-1)
        self._fuse()

    def set_sensors(self, sensors: list[dict]):
        """sensors: [{id, name, lat, lon, monthly: [12 x float|None], typed: bool}]"""
        self.sensors = sensors
        self._fuse()

    def _fuse(self):
        """Correct the modelled supply with measured bee-pollen.

        For each box and month: c = (measured / k) / modelled, where k converts
        pollen units to supply units (the median ratio over all boxes and
        months). With one box this corrects the *shape* of its season (which
        months are lean); with several it also corrects levels between places.
        Corrections are blended in log space, weighted by a Gaussian of the
        distance to each box, against a prior weight that keeps the model where
        no box is near."""
        corr = np.ones((self.ny, self.nx, 12))
        conf = np.zeros((self.ny, self.nx, 12))
        pts = []
        for b in self.sensors:
            c = self.cell(b["lat"], b["lon"])
            if c and any(v is not None for v in b["monthly"]):
                pts.append((b, c))
        pairs = [(b["monthly"][m], self.model_supply[c[0], c[1], m]) for b, c in pts for m in range(12)
                 if b["monthly"][m] is not None and self.model_supply[c[0], c[1], m] > 1e-4 and b["monthly"][m] > 0]
        if pairs:
            k = float(np.median([p / s for p, s in pairs]))
            num = np.zeros((self.ny, self.nx, 12))
            den = np.zeros((self.ny, self.nx, 12))
            for b, (i, j) in pts:
                bx, by = self.xy(b["lat"], b["lon"])
                w = np.exp(-0.5 * ((self.X - bx) ** 2 + (self.Y - by) ** 2) / SENSOR_RANGE_M ** 2)
                if not b.get("typed"):
                    continue                                      # untyped totals: not used
                for m in range(12):
                    p, s = b["monthly"][m], self.model_supply[i, j, m]
                    if p is None or s <= 1e-4:
                        continue
                    c = float(np.clip((max(p, 1e-6) / k) / s, 0.25, 4.0))
                    num[:, :, m] += w * math.log(c)
                    den[:, :, m] += w
            corr = np.exp(num / (den + PRIOR_WEIGHT))
            conf = den / (den + PRIOR_WEIGHT)
        self.correction, self.confidence = corr, conf
        self.supply = self.model_supply * corr
        self.ratio = np.where(TARGET > 0, self.supply / np.where(TARGET > 0, TARGET, 1), 1.0)

    def evidence(self, lat, lon, months) -> dict:
        """Is the flower supply here measured or modelled?"""
        c = self.cell(lat, lon)
        if not c or not self.sensors or not months:
            return {"kind": "modelled"}
        conf = float(np.mean([self.confidence[c[0], c[1], m] for m in months]))
        near = min(self.sensors, key=lambda b: dist_m((lat, lon), (b["lat"], b["lon"])))
        d = dist_m((lat, lon), (near["lat"], near["lon"]))
        has = any(near["monthly"][m] is not None for m in months)
        if conf >= 0.35 and has:
            corr = float(np.mean([self.correction[c[0], c[1], m] for m in months]))
            return {"kind": "measured", "box": near["name"], "box_id": near["id"], "distance_m": round(d, -1),
                    "confidence": round(conf, 2), "typed": True,
                    "vs_model": "less" if corr < 0.85 else "more" if corr > 1.15 else "about as"}
        return {"kind": "modelled", "nearest_box": near["name"], "distance_m": round(d, -1)}

    def set_plantings(self, plantings: list[dict]):
        self.plantings = plantings
        self.rebuild()


_world: World | None = None


def world() -> World:
    global _world
    if _world is None:
        _world = World()
    return _world


# =================================================================== helpers
def dist_m(a, b):
    kx = geodata.M_PER_DEG * math.cos(math.radians(a[0]))
    return math.hypot((b[1] - a[1]) * kx, (b[0] - a[0]) * geodata.M_PER_DEG)


def bearing_word(a, b):
    kx = geodata.M_PER_DEG * math.cos(math.radians(a[0]))
    ang = math.degrees(math.atan2((b[1] - a[1]) * kx, (b[0] - a[0]) * geodata.M_PER_DEG)) % 360
    return ["north", "north-east", "east", "south-east", "south", "south-west", "west", "north-west"][round(ang / 45) % 8]


def nest_edge_distance(lat, lon, nest):
    """Distance from a point to the edge of a nest area (0 if inside)."""
    return max(0.0, dist_m((lat, lon), (nest["lat"], nest["lon"])) - nest.get("radius_m", 300))


def place_name(lat, lon):
    if geodata.PLACES:     # OpenStreetMap district / village names
        p = min(geodata.PLACES, key=lambda q: dist_m((lat, lon), (q["lat"], q["lon"])))
        d = dist_m((lat, lon), (p["lat"], p["lon"]))
        if d <= 2500:
            return p["name"] if d <= 600 else f"Near {p['name']}"
    best, bd = None, 1e18
    for f in geodata.LANDCOVER + [dict(z, kind="wild") for z in geodata.WILD_BEE_ZONES]:
        if f["kind"] in ("arable", "water"):
            continue
        if not f.get("name"):
            continue
        pts = f["polys"][0]["outer"]
        c = (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))
        d = dist_m((lat, lon), c)
        if d < bd:
            best, bd = f["name"], d
    if not best:
        return f"{lat:.4f}, {lon:.4f}"
    short = best.split(", ")[-1] if ", " in best else best
    return f"Near {short}" if bd > 250 else best


def month_list(ms):
    """Month indices (0-based) as words; runs of 3+ become 'March to May'."""
    ms = sorted(ms)
    runs, start = [], None
    for k, m in enumerate(ms):
        if start is None:
            start = m
        if k == len(ms) - 1 or ms[k + 1] != m + 1:
            runs.append((start, m)); start = None
    parts = []
    for a, b in runs:
        if b - a >= 2:
            parts.append(f"{_full(a)} to {_full(b)}")
        else:
            parts += [_full(x) for x in range(a, b + 1)]
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]


def _full(m):
    return ["January", "February", "March", "April", "May", "June", "July", "August", "September",
            "October", "November", "December"][m]


def _month_list_old(ms):
    names = [MONTHS[m] for m in ms]
    full = {"Jan": "January", "Feb": "February", "Mar": "March", "Apr": "April", "May": "May", "Jun": "June",
            "Jul": "July", "Aug": "August", "Sep": "September", "Oct": "October", "Nov": "November", "Dec": "December"}
    names = [full[n] for n in names]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def gap_months(w: World, i: int, j: int, limit=0.85) -> list[int]:
    return [m for m in SEASON_MONTHS if w.ratio[i, j, m] < limit]


# =================================================================== spot check
def spot_check(lat, lon, colonies, apiaries, nests, detections, search_alternative=True, exclude_apiary=None):
    """Is this a fair place for honeybee colonies? Green / amber / red."""
    w = world()
    c = w.cell(lat, lon)
    if c is None:
        return {"error": "This spot is outside the mapped area."}
    i, j = c
    checks = []

    # 1. wild bees
    inside_wild = bool(w.wild[i, j])
    share = float(w.wild_share[i, j])
    zone = next((z for z in geodata.WILD_BEE_ZONES if in_polys(lat, lon, z["polys"])), None)
    if inside_wild:
        checks.append(_ck("wild", "bad", f"Inside a wild-bee zone", f"{zone['name'] if zone else 'Mapped habitat'}. "
                          "Honeybees here would compete directly with wild bees.", "✕ Inside"))
    elif share > 0.002:
        near = min(geodata.WILD_BEE_ZONES, key=lambda z: _zone_dist(lat, lon, z))
        checks.append(_ck("wild", "bad", "Overlaps a wild-bee zone",
                          f"Your {int(RADIUS_M)} m radius covers part of {near['name']} "
                          f"(about {max(1, round(share * 100))}% of the area).", "✕ Partly"))
    else:
        checks.append(_ck("wild", "ok", "Clear of wild-bee zones", f"No mapped habitat within {int(RADIUS_M)} m.", "✓ None"))

    # 2. competition with other honeybees
    near_ap = [a for a in apiaries if a.get("id") != exclude_apiary and dist_m((lat, lon), (a["lat"], a["lon"])) <= RADIUS_M]
    n_ap = len(near_ap)
    cols = sum(a.get("colonies", 1) for a in near_ap)
    detail = f"{cols + colonies} colonies would share the flowers here." if n_ap else "No other colonies close by."
    st = "bad" if n_ap >= APIARIES_BAD else "warn" if n_ap >= APIARIES_WARN else "ok"
    title = (f"{n_ap} apiar{'y' if n_ap == 1 else 'ies'} already within {int(RADIUS_M)} m" if n_ap
             else f"No apiaries within {int(RADIUS_M)} m")
    checks.append(_ck("apiaries", st, title, detail, f"{'✕' if st == 'bad' else '▲' if st == 'warn' else '✓'} {n_ap}"))

    # 3. food through the season, shared between colonies
    share_factor = (cols + colonies) / 6 if cols + colonies > 6 else 1.0   # crowding stretches the food
    eff = w.ratio[i, j, :] / share_factor
    spring = float(eff[2:4].mean())
    gaps = [m for m in SEASON_MONTHS if eff[m] < 0.85]
    st = "bad" if spring < 0.5 else "warn" if spring < 0.85 else "ok"
    detail = (f"Short on flowers in {month_list(gaps)}." if gaps else "Enough flowers from March to October.")
    if gaps and share_factor > 1:
        detail = f"With {cols + colonies} colonies sharing them, flowers run short in {month_list(gaps)}."
    if st != "ok":
        word = "Very low" if st == "bad" else "Low"
        checks.append(_ck("forage", st, f"Early-spring flower supply: {word.lower()}", detail, f"{'✕' if st == 'bad' else '▲'} {word}"))
    elif gaps:
        checks.append(_ck("forage", "warn", f"Flower gap in {month_list(gaps)}", detail + " Planting could close it.", "▲ Gap"))
    else:
        checks.append(_ck("forage", "ok", "Enough flowers all season", detail, "✓ Good"))

    # 4. hornets
    active = [n for n in nests if n.get("status") != "removed"]
    near_nest = min(active, key=lambda n: nest_edge_distance(lat, lon, n), default=None)
    dnest = nest_edge_distance(lat, lon, near_nest) if near_nest else math.inf
    recent = sum(1 for d in detections if d.get("lat") is not None
                 and dist_m((lat, lon), (d["lat"], d["lon"])) <= 1000)
    if dnest <= HORNET_NEAR_M:
        checks.append(_ck("hornet", "bad", "Active hornet nest area nearby",
                          "Asian hornets hunt at hives close to their nest. Wait until it is removed.", "✕ Yes"))
    elif dnest <= HORNET_WARN_M or recent:
        why = f"A suspected nest area is {round(dnest / 100) / 10:.1f} km away." if dnest <= HORNET_WARN_M \
            else f"{recent} hornet sighting{'s' if recent != 1 else ''} within 1 km recently."
        checks.append(_ck("hornet", "warn", "Hornets active in the area",
                          why + " Fit an entrance reducer; a smart-hive camera helps find the nest.", "▲ Nearby"))
    else:
        checks.append(_ck("hornet", "ok", "No hornet nest reported nearby", "Checked against today's alerts.", "✓ None"))

    bads = [c for c in checks if c["status"] == "bad"]
    warns = [c for c in checks if c["status"] == "warn"]
    blocker = inside_wild or dnest <= HORNET_NEAR_M or w.water[i, j]
    if blocker or len(bads) >= 3:
        verdict, label, headline = "red", "RED · NOT RECOMMENDED", "Please choose another spot"
    elif bads or len(warns) >= 2:
        verdict, label, headline = "amber", "AMBER · POSSIBLE WITH CHANGES", "Not ideal for wild bees here"
    else:
        verdict, label, headline = "green", "GREEN · GOOD SPOT", "Fair to wild bees here"
    if w.water[i, j]:
        headline = "This spot is in the river"

    ev = w.evidence(lat, lon, [2, 3] + gaps)
    fc = next(c for c in checks if c["key"] == "forage")
    if ev["kind"] == "measured":
        fc["detail"] += f" Measured by SensoBox {ev['box']} ({fmt_km(ev['distance_m'])} away)."
    out = {"lat": lat, "lon": lon, "place": place_name(lat, lon), "colonies": colonies, "radius_m": RADIUS_M,
           "evidence": ev,
           "verdict": verdict, "label": label, "headline": headline, "checks": checks,
           "apiaries_near": [{"id": a.get("id"), "name": a["name"], "lat": a["lat"], "lon": a["lon"],
                              "colonies": a.get("colonies", 1)} for a in near_ap],
           "monthly_supply": [round(float(x), 2) for x in eff]}
    if search_alternative and verdict != "green":
        out["alternative"] = find_green_spot(lat, lon, colonies, apiaries, nests, detections, exclude_apiary)
    return out


def _ck(key, status, title, detail, short):
    return {"key": key, "status": status, "title": title, "detail": detail, "short": short}


def _point_in(lat, lon, poly):
    inside = False
    n = len(poly)
    for k in range(n):
        (y1, x1), (y2, x2) = poly[k], poly[(k + 1) % n]
        if (y1 > lat) != (y2 > lat) and lon < (x2 - x1) * (lat - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def in_polys(lat, lon, polys):
    return any(_point_in(lat, lon, p["outer"]) and not any(_point_in(lat, lon, h) for h in p.get("inner", []))
               for p in polys)


def _zone_dist(lat, lon, z):
    return min(dist_m((lat, lon), q) for p in z["polys"] for q in p["outer"])


def find_green_spot(lat, lon, colonies, apiaries, nests, detections, exclude_apiary=None, max_m=1600):
    """Nearest spot that comes out green (or the best amber one)."""
    w = world()
    best = None
    for r in range(150, int(max_m) + 1, 150):
        ring = []
        for a in range(0, 360, 15):
            x, y = w.xy(lat, lon)
            la, lo = w.ll(x + r * math.sin(math.radians(a)), y + r * math.cos(math.radians(a)))
            c = w.cell(la, lo)
            if not c or w.water[c] or w.forest[c] or w.wild[c]:
                continue
            res = spot_check(la, lo, colonies, apiaries, nests, detections, search_alternative=False,
                             exclude_apiary=exclude_apiary)
            ring.append((res, r))
        greens = [x for x in ring if x[0]["verdict"] == "green"]
        if greens:
            res = max(greens, key=lambda x: float(np.mean(x[0]["monthly_supply"][2:9])))[0]
            return _alt(lat, lon, res)
        if best is None:
            ambers = [x for x in ring if x[0]["verdict"] == "amber"]
            if ambers:
                best = min(ambers, key=lambda x: sum(c["status"] == "bad" for c in x[0]["checks"]))[0]
    return _alt(lat, lon, best) if best else None


def _alt(lat, lon, res):
    d = dist_m((lat, lon), (res["lat"], res["lon"]))
    n_ap = len(res["apiaries_near"])
    wild = next(c for c in res["checks"] if c["key"] == "wild")["status"] == "ok"
    return {"lat": res["lat"], "lon": res["lon"], "verdict": res["verdict"], "distance_m": round(d, -1),
            "direction": bearing_word((lat, lon), (res["lat"], res["lon"])), "place": res["place"],
            "summary": f"{'Outside wild-bee zones' if wild else 'Touches a wild-bee zone'}, "
                       f"{n_ap or 'no'} apiar{'y' if n_ap == 1 else 'ies'} nearby"}


# =================================================================== city grids
def suitability_grid(apiaries, nests, step=3, colonies=2):
    """Green/amber/red for a hive everywhere in the city (coarse grid for the
    'where could I keep bees?' map). 0 green, 1 amber, 2 red, 3 not a site."""
    w = world()
    I, J = np.mgrid[0:w.ny:step, 0:w.nx:step]
    X, Y = w.X[I, J], w.Y[I, J]
    out = np.zeros(I.shape, dtype=np.uint8)
    # apiaries within radius
    n_ap = np.zeros(I.shape)
    for a in apiaries:
        ax, ay = w.xy(a["lat"], a["lon"])
        n_ap += (np.hypot(X - ax, Y - ay) <= RADIUS_M)
    # nests (distance to edge)
    dnest = np.full(I.shape, np.inf)
    for n in nests:
        if n.get("status") == "removed":
            continue
        nx_, ny_ = w.xy(n["lat"], n["lon"])
        dnest = np.minimum(dnest, np.maximum(0, np.hypot(X - nx_, Y - ny_) - n.get("radius_m", 300)))
    spring = w.ratio[I, J, 2:4].mean(axis=-1)
    gaps = (w.ratio[I, J][:, :, SEASON_MONTHS] < 0.85).any(axis=-1)
    wild_in = w.wild[I, J]
    wild_near = w.wild_share[I, J] > 0.002
    bad = (wild_near.astype(int) + (n_ap >= APIARIES_BAD) + (spring < 0.5) + (dnest <= HORNET_NEAR_M))
    warn = ((n_ap == APIARIES_WARN).astype(int) + ((spring >= 0.5) & ((spring < 0.85) | gaps))
            + ((dnest > HORNET_NEAR_M) & (dnest <= HORNET_WARN_M)))
    out[:] = 0
    out[(bad >= 1) | (warn >= 2)] = 1
    out[wild_in | (dnest <= HORNET_NEAR_M) | (bad >= 3)] = 2
    out[w.water[I, J] | w.forest[I, J]] = 3
    return _grid_payload(w, out, step)


def gap_grid(month: int, step=2):
    """Share of the monthly target reached, 0..255 (255 = 2x target or more)."""
    w = world()
    r = w.ratio[::step, ::step, month]
    vals = np.clip(r / 2 * 255, 0, 255).astype(np.uint8)
    vals[w.water[::step, ::step]] = 255
    return _grid_payload(w, vals, step)


def _grid_payload(w, arr, step):
    lat1 = w.lat0 + w.ny * CELL_M / w.ky
    lon1 = w.lon0 + w.nx * CELL_M / w.kx
    return {"rows": int(arr.shape[0]), "cols": int(arr.shape[1]),
            "bounds": [[w.lat0, w.lon0], [lat1, lon1]], "data": arr[::-1].flatten().tolist()}


# =================================================================== plant planner
def plant_plan(lat, lon, setting="garden", natives_only=False, planted_m2_nearby=0.0):
    w = world()
    c = w.cell(lat, lon)
    if c is None:
        return {"error": "This spot is outside the mapped area."}
    i, j = c
    ratio = w.ratio[i, j, :]
    gaps = [m for m in SEASON_MONTHS if ratio[m] < 0.85]
    if not gaps:   # no real gap: help the weakest months anyway
        gaps = sorted(SEASON_MONTHS, key=lambda m: ratio[m])[:2]
        found = False
    else:
        found = True
    worst = sorted(gaps, key=lambda m: ratio[m])
    gapset = set(m + 1 for m in gaps)

    def score(p: Plant):
        overlap = len(gapset & set(bloom_of(p)["months"]))
        return overlap * 3 + p.wild_bees + (1 if p.native else 0)

    pool = [p for p in PLANTS if setting in p.settings and (p.native or not natives_only)]
    ranked = sorted(pool, key=score, reverse=True)
    fills = [p for p in ranked if gapset & set(bloom_of(p)["months"])][:6]
    also = [p for p in ranked if p not in fills][:4]

    # neighbourhood goal: flowering area needed to lift the gap months to target
    circle = math.pi * RADIUS_M ** 2
    deficit = max(((TARGET[m] - w.supply[i, j, m]) for m in gaps), default=0.0)
    goal_m2 = max(0.0, float(deficit) * circle)
    season = _season_word(worst)
    if found:
        msg = (f"Your area is short on flowers in {month_list(sorted(gaps))}. " + {
            "early": "That's when many wild bees wake up and start nesting. Early bloomers help them most.",
            "summer": "Mid-summer gaps hit bumblebee colonies at their largest. Summer bloomers help most.",
            "late": "Late flowers feed bees before winter. Late bloomers help them most.",
        }[season])
    else:
        msg = f"No serious gap here. {month_list(sorted(gaps))} are the thinnest months, so these help most."
    return {
        "lat": lat, "lon": lon, "place": place_name(lat, lon), "radius_m": RADIUS_M, "setting": setting,
        "gap_found": found, "gap_months": [m + 1 for m in sorted(gaps)], "season": season, "message": msg,
        "monthly_ratio": [round(float(x), 2) for x in ratio],
        "goal_m2": round(goal_m2, -1), "planted_m2": round(planted_m2_nearby),
        "fills_gap": [_plant_json(p, gapset) for p in fills],
        "also_good": [_plant_json(p, gapset) for p in also],
        "avoid": AVOID,
        "bloom_source": bloom_summary(),
        "evidence": w.evidence(lat, lon, gaps),
    }


def fmt_km(m):
    return f"{m / 1000:.1f} km" if m >= 1000 else f"{int(round(m, -1))} m"


def bloom_summary() -> dict:
    obs = BLOOM.get("plants") or {}
    return {"observed": len(obs), "total": len(PLANTS), "fetched": BLOOM.get("fetched"),
            "places": sorted({d["place"] for d in obs.values()}), "attribution": BLOOM.get("attribution"),
            "n_obs": sum(d.get("n_obs", 0) for d in obs.values())}


def _season_word(months):
    m = months[0] if months else 3
    return "early" if m <= 4 else "summer" if m <= 7 else "late"


def _plant_json(p: Plant, gapset):
    b = bloom_of(p)
    gaps = sorted(gapset)
    return {"id": p.id, "name": p.name, "german": p.german, "latin": p.latin, "months": list(b["months"]),
            "peak": b["peak"], "share": b["share"], "bloom_source": b["source"], "n_obs": b.get("n_obs"),
            "bloom_place": b.get("place"), "taxon_id": b.get("taxon_id"), "curated_months": list(p.months),
            "kind": p.kind, "native": p.native, "wild_bees": p.wild_bees, "honeybees": p.honeybees,
            "tip": p.tip, "fills": sorted(gapset & set(b["months"])), "m2": p.m2, "settings": list(p.settings),
            "timing": timing(p, gaps)}
