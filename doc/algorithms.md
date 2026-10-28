# Algorithms and models

Every number below is a named constant in the code. Unless noted, thresholds are **rules of thumb chosen for the prototype**, not validated values; [limitations-roadmap.md](limitations-roadmap.md) lists what needs validating.

## 1. Flight direction (Pi agent)

- **Two-camera stations.**
  - The detection camera's track ending marks the moment the hornet leaves.
  - The heading camera's motion track that starts within `match_window_s` (3 s) of that moment gives the direction.
  - *Sky* mode (fisheye pointing up): the exit direction across the image, mirrored, because looking up flips east and west.
  - *Horizon* mode (camera looking outward): `atan((x − 0.5) · 2 · tan(hfov/2))` from where the hornet shrinks to.
  - The agent sends the direction relative to the camera, plus an uncertainty: sky 6–26° (from how straight the track is), horizon 3–20°.
- **Single-camera stations.** The exit direction is measured from the last part of the track on the detection camera, with a least-squares fit of position over time. Straightness (net ÷ path length) sets the uncertainty: 15–50°.
- **Server.** `bearing = station heading + relative direction`. Re-measuring a camera's heading in the app recomputes all its past bearings.

## 2. Nest areas from flight lines (`server/triangulate.py`)

1. **Lines per hive.** Each hive's departure bearings are grouped by direction, so one hive between two nests gives two lines. A line's uncertainty is the spread of its bearings ÷ √n, with:
   - a floor at the camera's heading-calibration uncertainty;
   - a small-sample factor `1 + 1/(n−1)` for n < 30, because a few departures underestimate their spread.

   A single camera departure in another direction is treated as a stray, not a line.
2. **Candidate nests.** Every pair of lines from different hives is intersected, keeping only crossings in front of both hives, within 3 km (`BEEMAP_MAX_RANGE_M`) and at least 12° apart. Crossings are grouped on a grid into seed points.
3. **Warm-up.** Each seed is refined using every line within 3.5σ of it. Seeds that are statistically indistinguishable (within each other's uncertainty, 95% level) merge.
4. **Assignment.** Each line is given to the candidate it points at best, iteratively.
5. **Fewest nests.** If every line behind a candidate also fits another candidate, that candidate is dropped. This stops two hives that happen to be in line with a nest, plus one skewed line, from inventing a second nest.
6. **Position and ellipse.** An iteratively reweighted least-squares fit of the angular residuals gives the nest position. Each line is weighted by `1 / (distance · σ)²`. The covariance gives the 95% ellipse.
   - If the lines disagree more than their uncertainties predict, the ellipse is scaled up by the reduced chi-square.
   - It's then inflated ×1.2 (3+ lines) or ×1.5 (2 lines), with a minimum of 100 m.
7. **Untangling nearby nests.** Every individual departure gets soft weights to every candidate nest, plus an "outlier" option. Lines are rebuilt per nest from those weights and solved again, up to 5 rounds. This separates two nests that a hive's averaging had blended.
8. **Leftovers.** Lines that still cross but can't each form their own nest are solved together with an honest, wide ellipse. A line no other line crosses stays a **direction-only corridor**.

Departures in the last 14 days count (`BEEMAP_WINDOW_DAYS`). Dismissed alerts don't count, and nor do departures already explained by a removed nest.

**In simulation**, with 3 hives about 1.5 km from a nest, ±20° per departure and 12 departures each: median error is about 175 m, and the 95% ellipse contains the true nest in about 95–97 of 100 runs.

## 3. Where a new smart hive helps (`triangulate.py`, planning)

The **open searches** are the direction-only corridors, and nest areas with a radius above 180 m. For each, candidate sites on rings around it are scored:

- **Visit chance:** `exp(−d / 800 m)`, none beyond 1.8 km. The hive needs at least 3 departures for a line; that probability is Poisson, assuming 14 expected departures right at the nest.
- **Expected search radius:** the 95% radius that the new line plus the existing ones would give. It's computed from geometry alone (the information matrix of the lines), assuming the new line has ±8° uncertainty.
- **Plausibility weighting:** for corridors, where along the line and how far to its side the nest could be is unknown. The score averages over plausible nest positions, weighting the middle of the corridor's uncertainty band most.
- **Hard rules:**
  - not inside the corridor's uncertainty band (or behind the hive on the same line);
  - at least a 30° crossing angle at the most plausible positions;
  - at least 250 m from existing and planned hives;
  - suggested sites at least 500 m apart.
- **Score:** `P(line) × usefulness(radius)`, averaged with the plausibility weights.

Predicted radii are shown as a range, because the nest's position along a corridor is unknown. Nest areas already **located** by a confirmed report are dropped.

**In simulation**, one hive at the top suggested site located the nest in 67 of 80 sparse corridors and 77 of 80 well-observed ones. An oracle that knows the nest manages 73 and 80.

## 4. Flower supply (`server/ecology.py`)

- **Raster:** 50 m cells over `BBOX = (49.080, 9.120, 49.200, 9.310)`.
- **Land cover:** each land-cover kind has a monthly "share of ground in useful bloom" profile (`PROFILES`, assumptions). Examples:
  - orchards peak at 0.85 in April;
  - lime avenues 0.9 in June;
  - arable fields about 0.02.

  Hedges and tree rows from OpenStreetMap are added as narrow strips (3 m and 8 m wide) proportional to each cell. Residents' plantings add their area in their bloom months.
- **Supply around a point:** the mean bloom share within **800 m** (`RADIUS_M`), computed for all cells at once by FFT convolution with a disc.
- **Target:** `TARGET` per month (March 0.025, April–July 0.032, August 0.025, September 0.022, October 0.008). It's calibrated so a typical Heilbronn neighbourhood just meets it in the main season, which makes early spring and late summer show up as the usual urban gaps.
- **Gap:** a month from March to October where supply ÷ target < 0.85.

## 5. SensoBox correction (`ecology.py`)

- **Bee-relevant pollen per reading** = Σ count × weight per pollen type (`POLLEN_BEE_WEIGHT`):
  - willow, lime, fruit blossom, clover, phacelia, ivy: 1.0;
  - rapeseed family 0.9, maple 0.8, hazel 0.4, alder 0.3;
  - grass, birch, pine, nettle, oak, ragweed and the like: 0.

  Wind pollen runs into the hundreds of grains/m³ and would swamp the signal even at a small weight. **Untyped totals are not used.**
- **Monthly index per box:** the mean of the daily values, needing at least 5 days in the month.
- **Correction:**
  1. `k` = the median ratio of pollen to modelled supply over all boxes and months.
  2. For each box and month, the correction is `c = (pollen / k) / modelled`, clipped to 0.25–4.
  3. Corrections spread out in log space with a Gaussian weight (σ = 1.5 km) against a prior weight of 0.5, so far from any box the model stands.
  4. Confidence = weight ÷ (weight + prior). A spot counts as "measured" at confidence ≥ 0.35.
  5. With one box, only the *shape* of its season is corrected; with several, levels between places too.
- **Bee flight weather:** the share of daylight readings (≥ 1000 lux) with ≥ 12 °C and humidity < 90%.

In the demo, a March at 45% of the modelled value was corrected to about 65% at the box. The correction is deliberately partial.

## 6. Hive spot check (`ecology.spot_check`)

Four checks within 800 m:

| Check | Bad | Warning |
|---|---|---|
| Wild-bee zones | spot inside one (**red**), or > 0.2% of the circle overlaps | none |
| Apiaries within 800 m | ≥ 3 | 2 |
| Flowers (supply ÷ target, divided by colonies/6 if more than 6 colonies share) | March–April mean < 0.5 | < 0.85, or any gap March–October |
| Hornet nest areas (distance to edge) | ≤ 300 m (**red**) | ≤ 1.5 km, or alerts within 1 km in 14 days |

**Verdict:**
- **Red** if the spot is inside a wild-bee zone, within 300 m of a nest area, in water, or has three bad checks.
- **Amber** if any check is bad, or two are warnings.
- **Green** otherwise.

**Better spot:** points on rings every 150 m out to 1.6 km, every 15°, skipping water, forest and wild-bee zones. The nearest green one wins; between equals, the one with the most flowers. Otherwise, the amber one with the fewest bad checks.

The **Good hive spots** layer is the same check, vectorised over a 150 m grid for 2 colonies.

## 7. Plant planner (`ecology.plant_plan`)

- **Ranking:** plants are filtered by setting (balcony, garden, public green, hedge, farm) and optionally to natives only. Score = 3 × (gap months the plant covers) + wild-bee value + 1 if native. Plants that cover a gap are "Fills your gap"; the rest are "Also good".
- **Bloom months:** observed iNaturalist months where available (see [data-sources.md](data-sources.md)), otherwise curated ones.
- **When to plant:** the next planting window, and the first gap month the plant will bloom in.
  - Shrubs and trees: October to March.
  - Perennials: March–May or September–October, flowering fully from the second year.
  - Crops: sowing windows counted back from the time to flowering, e.g. phacelia about 7 weeks, buckwheat about 5.
- **Neighbourhood goal:** the area of flowers needed to lift the worst gap month to target, `(target − supply) × π · 800²`.

## 8. Colony health (`server/health.py`)

Computed per hive over 30 days of hourly data, with the "recent" period being the last 72 hours:

- **Activity versus weather.**
  - Each colony's own activity per degree above 8 °C is learnt from earlier weeks; the recent median ratio to that gives the activity index.
  - Below 0.25 is an alert; below 0.6 is a warning.
  - A fall to under 40% of the previous 3 days within a day, in good weather, is a **sudden drop** alert.
- **Weather coupling:** correlation of activity with outside temperature over daylight hours. Healthy if r ≥ 0.5.
- **Brood nest (March–September):** share of time at 33–37 °C. Below 30% while the hive temperature follows the outside one (r > 0.6) is an alert; below 60% is a warning.
- **Winter cluster (October–February):** hive minus outside temperature. Under 2.5 °C (when it was over 5 °C before) is an alert ("as cold as outside"); under 6 °C is a warning.
- **Humidity:** above 85% for more than half the recent period is a warning ("damp hive").
- **Hornets:** 3 or more visits in 7 days. Combined with an activity index between 0.25 and 0.8 (and the colony not apparently lost), this is "hornets holding foragers back".
- **Forage:** supply below 0.85 of target is shown as an info note (low activity may be hunger, not illness).

These are indicators that suggest an inspection; they are never a diagnosis.

## 9. Nest-report photo check (`server/nest_classifier.py`)

- **Quality gate:**
  - short side ≥ 480 px;
  - sharpness (variance of the Laplacian on a 512 px greyscale copy) ≥ 60;
  - mean brightness 35–225.
- **Model:** an optional ONNX classifier with the classes Asian hornet nest, European hornet nest, wasp nest and not a nest. Input is 224×224, ImageNet-normalised; softmax is applied if needed.
- **Decision:**
  - confirm if P(Asian hornet nest) ≥ **0.90**;
  - confirm if ≥ **0.75** *and* the report matches a nest area or corridor (section 10);
  - **never** close a report automatically on a "not Asian" result, because a missed nest costs more than a visit.

## 10. Reports ↔ flight lines (`server/correlate.py`)

- **Nest areas.** The squared Mahalanobis distance of the report from the area's centre is computed in the area's own axes. Each axis σ = semi-axis ÷ √5.99, combined with **±50 m** for pin placement. *Strong* ≤ 2.30 (68%), *fits* ≤ 5.99 (95%), *near* ≤ 11.83 (99.7%).
- **Corridors.** The angle off the line, divided by `√(σ_line² + (50 m / distance)²)`, for reports 30 m–2.5 km in front of the hive. *Strong* ≤ 1, *fits* ≤ 2, *near* ≤ 3.
- **Eligible reports:** status pending or Asian hornet, less than 150 days old. Active nest areas only. A crossing beats a single line.
- **Flag:** a small (fist-sized) nest reported from July on may be an abandoned spring nest.
- **Effects:**
  - matching reports are checked first;
  - the photo threshold drops to 0.75;
  - a confirmed match marks the area **located** and removes it from planning;
  - removing a matched report closes the area.

## 11. Bloom months from iNaturalist (`server/bloom_import.py`)

- **Source:** monthly counts of observations annotated *Plant Phenology: Flowering* (`term_id=12, term_value_id=13`), for Baden-Württemberg, or Germany when there are fewer than 30.
- **Bloom months:** months with at least 20% of the peak month's count. Peak months: at least 60%.
- **Matching:** only exact scientific-name matches are accepted, so a similar species is never substituted.
