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

**Several colonies per station.** Readings can name their colony (`hive`). Each named colony is analysed on its own, against the same outside weather and hornet visits of its station. The real OpenSpace hives arrive this way from the data pipeline (section 12).

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

## 12. Data pipeline (`pipeline/`)

These run offline (see [pipeline.md](pipeline.md)); the server only serves their results. Unlike sections 1–11 they work on **real measurements** from Heilbronn, but several rest on very little data, as noted.

### Bee Balance (`bee_balance.py`)

A second, coarser opinion on where a honeybee hive has enough food, next to the 800 m spot check (section 6).

1. **Land use** comes from OpenFreeMap vector tiles (OpenMapTiles schema, zoom 14): the same OpenStreetMap data, but always reachable. Every polygon gets a bee-food weight (`FORAGE_WEIGHT`), for example:
   - meadow, orchard, heath, flower bed 1.0; allotments 0.8; garden and scrub 0.7;
   - wood, park, cemetery 0.5; grass 0.2; residential 0.15; farmland and vineyard 0.1.

   Polygons are rasterised on a 25 m grid. Where they overlap, the higher weight wins.
2. **Food within reach:** the mean weight within **1.5 km** (honeybees) and **300 m** (wild bees) of every 250 m square's centre, by disc convolution.
3. **City only:** squares at least 40% inside Heilbronn's boundary (from Nominatim) are kept: 1,632 squares, 102 km².
4. **Verdict** from food within 1.5 km:
   - **hive-ready** (green) from the 75th percentile of the city's squares (currently 33.8%);
   - **plant first** (yellow) from the 25th percentile (25.4%);
   - **no extra hives** (red) below.

   So the city splits into a quarter, a half and a quarter by design. `THRESHOLDS = (0.28, 0.20)` switches to the fixed values of the team's original raster, whose level differs.
5. **Extras per square:**
   - an environment score from the senseBox stations: share of daytime hours at bee-flight temperature, share of very humid hours and mean PM2.5, weighted by their correlation with the camera's activity counts (|Spearman r|, floored at 0.1), and interpolated by inverse distance;
   - street heat from the walks (below).
6. **Schools and neighbourhoods** get their square's numbers, for "which school can host a hive" and "where to plant first".

The web page's **Wild bees (300 m)** view of the layer colours the squares by the city's quartiles of food within 300 m, computed in the browser.

### Street heat (`heat_walks.py`)

Summer-school datalogger walks on 1–3 Sep 2026: 61,546 one-second readings of temperature and humidity with GPS.

- The first **15 minutes** of each recording are dropped, because the loggers start indoors and need time to settle. Recordings with less than 10 minutes left after that, and readings faster than walking pace (3 m/s), are dropped too.
- For each reading, `delta = (T − M) − median over the recording of (T − M)`, with `M` the Open-Meteo temperature at the hives, interpolated to the minute. Subtracting the model removes the day's warming; subtracting the recording's own median removes each logger's bias (they run 2–4 °C warm).
- **Map:** mean delta on **100 m** cells with at least 20 readings. It shows how much hotter or cooler a street is than the rest of that walk, not an absolute temperature.

### Bee flight hours (`activity_model.py`)

A rule model: hourly activity (0–1) is the product of factors, each 0–1:

| Factor | Rule |
|---|---|
| warmth | 0 below 10 °C, full from 20 °C |
| not too hot | fades above 32 °C, 0 at 38 °C |
| calm | fades above 3 m/s wind, 0 at 8 m/s |
| sun | radiation ÷ 300 W/m², at least 0.3 (overcast) |
| dry | 0 in rain (≥ 0.2 mm/h) |
| daylight | 0 at night |
| humidity (the *humidity* variant only) | full below 60% RH, 0 at 90% |

**Flight hours** are the sum of hourly activity over a month or year, on Open-Meteo 2025 weather, for Heilbronn by month and for 8 German cities. **Heat hours** are daylight hours at 30 °C or more.

The humidity factor was added because the camera counts showed the humid day nearly dead, with humidity as strong a correlate as temperature. Its shape was chosen *after* seeing those hours, so the agreement with them **isn't an independent test**.

### Weather check (`validate_weather.py`)

Flight hours rely on Open-Meteo, so it's compared against the hives' SenseCAP S2120 station, which has a radiation shield, and against senseBox stations within 3 km with at least 500 hours (summer 2026). The metrics are bias, MAE, RMSE and Pearson r per variable, plus the temperature offset by hour of day; the daytime offset is the median over 10:00–17:00. If the shielded station agrees with Open-Meteo while the senseBoxes read warm, their offsets are mostly siting rather than urban heat.

### Bee detector and hive camera study (`make_dataset.py`, `train.py`, `count_bees.py`, `analyse.py`)

- **Labels:** the organisers' 32×32 crops (`bee_motion`, `bee_slow`, `background`) become 32×32 boxes on their source frames. Crops whose names lack the session are matched to it by comparing pixels. Frames with only background crops are negative examples. The split is 80/20 (seed 42).
- **Model:** YOLOv8n, 50 epochs. mAP50 about 0.8 on held-out frames (0.81, precision 0.76, recall 0.73 on 683 frames in the last run).
- **Counting:** detections per frame, averaged per minute, and detections per minute (which scales with the assumed frame interval). Sessions with no coloured motion at all carry no signal and are excluded, not counted as zero.
- **Study:** activity joined to hive-station weather and senseBox PM2.5 in hourly bins; Spearman and Pearson correlations. Only **6 hourly bins** exist, so this is a direction check (warmer and drier = busier), not a model.

### Public sightings (`hornet_sightings.py`)

GBIF occurrence records with coordinates, status *present*, in the box 49.05–49.25 N, 9.05–9.35 E (about 22 km around Heilbronn), for the Asian hornet, the European hornet and the honey bee. Each record keeps its coordinate uncertainty (8–250 m for every Asian hornet record so far), source and licence. The source is the record's institution code, or else its publisher's name looked up on GBIF (Observation.org records carry only a publisher key). Licence URLs are shortened to names such as `CC BY-NC 4.0`. Counts per year for Baden-Württemberg come from a GBIF facet query. **Nothing is modelled:** these are what people reported, unchecked by the city, and they don't feed the nest search.

## 13. Camera alerts sorted by confidence (`server/app.py`, `auto_review`)

- **Score:** the median VespAI confidence over every box of the visit (`confidence_median`, sent by the Pi). The best box alone (`confidence`) is easily lifted by one lucky frame, for example a wasp turned the right way.
- **Decision:**
  - median ≥ **0.9** (`BEEMAP_AUTO_CONFIRM`): confirmed automatically;
  - median < **0.7** (`BEEMAP_AUTO_DISMISS`): dismissed automatically, so it stops counting for the nest search (section 2);
  - in between, or no median (older agents): pending. The server asks the Pi for a clip of the visit, and a person confirms or dismisses it.
- **A person always wins:** a manual review replaces the automatic decision and is marked `manual`.
- Unlike nest reports (section 9), camera alerts can be dismissed automatically: one dismissed departure only removes one flight line, while a missed nest report could hide a whole nest.
