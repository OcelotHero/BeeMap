# Limitations and roadmap

## What is not real yet

- **Simulated data.** Hornets, SensoBox readings, colony data, nest reports and plantings in the demo are simulated. All accuracy figures come from simulation.
- **Apiary register** is demo data. The hive competition check depends on it.
- **No nest-photo model** is included; until one is trained, every report is checked by hand.
- **External services** (Overpass, iNaturalist, map tiles) were tested only against mock responses; the first real run happens online.
- **The hive camera study is tiny.** Only 6 hourly bins of footage carry a signal, the frame interval is assumed (1 s), and the camera's hive is unknown. Its weather links are a direction check; the flight-hours humidity factor was shaped after seeing those hours.
- **Real hive sensors** reach BeeMap only for the three OpenSpace hives, and their S31 sensor sits in the hive body, not necessarily the brood nest, so brood-nest warnings for them are unreliable.
- **VespAI on a Pi 4** runs at about 1 frame per second and is only reliable looking straight down at a plain bait board.
- **The heading camera** (motion tracking against the sky) is untested with real hornets. Wind-blown leaves and other insects may cause false tracks; pairing with the VespAI departure time filters most of them.

## Assumptions to validate

| Assumption | Where | How to validate |
|---|---|---|
| 800 m radius for all bee checks | `ecology.RADIUS_M` | Honeybees often forage 1–3 km; compare against hive-scale data |
| Bloom profiles per land cover and monthly targets | `ecology.PROFILES`, `TARGET` | Hive scales, pollen traps, expert review |
| Pollen weights per type; 1.5 km reach of a SensoBox | `ecology.POLLEN_BEE_WEIGHT`, `SENSOR_RANGE_M` | A season of typed pollen data next to hive scales |
| Spot-check thresholds (apiary counts, 300 m / 1.5 km hornet distances) | `ecology.APIARIES_*`, `HORNET_*` | Compare verdicts with how existing apiaries actually do |
| Colony-health thresholds | `health.py` | Beekeepers' inspection records for the same hives |
| ±50 m pin accuracy; 75% combined photo threshold; 150-day season | `correlate.py`, `nest_classifier.py` | Confirmed nests after a season |
| Foraging-distance model for smart-hive planning | `triangulate.FORAGE_SCALE_M` | Distances between real nests and the hives that saw them |
| Bee Balance food weights per land use; verdicts by city quartiles | `pipeline/scripts/bee_balance.py` (`FORAGE_WEIGHT`, `THRESHOLDS`) | Compare verdicts with hive-scale weight gain; agree fixed thresholds with beekeepers |
| Flight-hours rule factors, especially humidity | `pipeline/scripts/activity_model.py` (`factors`) | Weeks of continuous hive-camera footage |
| 0.9 / 0.7 median-confidence thresholds for confirming and dismissing camera alerts | `BEEMAP_AUTO_CONFIRM`, `BEEMAP_AUTO_DISMISS` | Have people check a season's clips and snapshots blind, then set the thresholds from their verdicts |
| 1 s frame interval of the hive recordings | `pipeline/scripts/common.py` (`FRAME_INTERVAL_S`) | Ask the organisers, or timestamp new recordings |

## Known gaps

- **OpenStreetMap completeness:** private gardens and lawns are often unmapped, and land cover is classified by tag, not checked on the ground.
- **Protected areas ≠ wild-bee habitat.** Real habitats (sand banks, dry-stone walls, flower-rich verges) need expert mapping.
- **Bloom times from citizen science** are thin for rare plants and lean towards towns and weekends.
- **Missing site factors:** water sources, pesticide exposure, terrain and microclimate, landowner permission, distance to paths.
- **Infrastructure:** one SQLite server; in-memory rate limits; a single shared admin key; anonymous submissions could be spammed.
- **No beekeeper view.** Colony health has no per-beekeeper view on the public site, by design.
- **Leftover table:** the database still creates an unused `hive_requests` table from an earlier version.
- **Two food models** (800 m flower model and 1.5 km Bee Balance) use different land-use sources and weights, and can disagree. They aren't reconciled yet.
- **Pipeline output isn't checked automatically.** A rerun that writes odd numbers goes straight onto the map; review `findings.md` and the plots before committing new results.
- **3D data** (`make lights`) is produced but not shown by the web page.
- **Clips are slideshows on a Pi 4.** The model runs at about 1 frame a second there, and the clip only has the frames the model saw. No clip review on the website; the city watches them through `tools/admin.py clip`.
- **Readings aren't de-duplicated.** Hive and SensoBox readings sent twice are stored twice; `make beemap` relies on its local state file to send each hour once.

## Roadmap

**Before a pilot**
1. Connect the real apiary register (veterinary office) and LUBW biotope data.
2. Field-test the two-camera smart hive on a few hives; measure heading accuracy and false-track rates.
3. Collect and label nest photos (city confirmations, beekeeping associations); train and evaluate the photo model.
4. Add hive scales to the smart hives as ground truth for forage.

**During a season**
5. Validate the thresholds in the table above; tune and record them.
6. Run the hive spot check against existing apiaries with scales: do green spots do better?
7. Add typed pollen sensors to SensoBoxes; compare their corrections with hive-scale data.

**For a public launch**
8. HTTPS, a tile provider with a plan, persistent rate limits or captcha, backups, a privacy notice and retention rules for photos.
9. An optional private beekeeper view (own hives' health and alerts) with proper accounts.
10. Crop data (field declarations) and terrain for better siting; farmer-facing planting campaigns in gap areas.
