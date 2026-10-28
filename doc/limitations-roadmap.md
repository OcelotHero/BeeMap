# Limitations and roadmap

## What is not real yet

- **Simulated data.** Hornets, SensoBox readings, colony data, nest reports and plantings in the demo are simulated. All accuracy figures come from simulation.
- **Apiary register** is demo data. The hive competition check depends on it.
- **No nest-photo model** is included; until one is trained, every report is checked by hand.
- **External services** (Overpass, iNaturalist, map tiles) were tested only against mock responses; the first real run happens online.
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

## Known gaps

- **OpenStreetMap completeness:** private gardens and lawns are often unmapped, and land cover is classified by tag, not checked on the ground.
- **Protected areas ≠ wild-bee habitat.** Real habitats (sand banks, dry-stone walls, flower-rich verges) need expert mapping.
- **Bloom times from citizen science** are thin for rare plants and lean towards towns and weekends.
- **Missing site factors:** water sources, pesticide exposure, terrain and microclimate, landowner permission, distance to paths.
- **Infrastructure:** one SQLite server; in-memory rate limits; a single shared admin key; anonymous submissions could be spammed.
- **No beekeeper view.** Colony health has no per-beekeeper view on the public site, by design.
- **Leftover table:** the database still creates an unused `hive_requests` table from an earlier version.

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
