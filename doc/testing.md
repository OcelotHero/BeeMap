# Testing

Each suite is a plain Python script (`pytest` also works). Run them from the project folder:

```bash
python3 server/tests/test_triangulate.py
python3 server/tests/test_api.py
python3 server/tests/test_eco.py
python3 server/tests/test_bloom.py
python3 server/tests/test_sensors.py
python3 server/tests/test_health.py
python3 server/tests/test_osm.py
python3 server/tests/test_ondemand.py
python3 server/tests/test_nest_reports.py   # model part needs: pip install onnx onnxruntime (else skipped)
python3 server/tests/test_correlate.py
python3 server/tests/test_city.py
cd pi && python3 test_agent.py
```

They use temporary data folders, `BEEMAP_ONLINE=off` and the demo map, so they need no internet and leave no files behind.

| Suite | What it proves |
|---|---|
| `test_triangulate` | Nest accuracy and ellipse calibration on simulated departures (one nest, two nests sharing a hive, collinear hives plus a skewed line); single line plus distance; site planner success rate |
| `test_api` | Device auth, a station registered with a fixed token (as the demo does for a real Pi) that gets a new `created` time when registered again, retry de-duplication, heading recalibration recomputing bearings, planned hive → installed on its first heartbeat, a lone corridor turned into a located nest by a planned hive, nest removal; nest reports on by default, public sees ~100 m without note or photo, photo kept private and clean |
| `test_eco` | Hive spot check (the design's Wertwiesenpark example comes out amber, with a green spot nearby); no owner names or tokens in public data; colony health admin-only; plant planner; plantings; grids |
| `test_bloom` | iNaturalist histogram parsing against a mock API, fallback from Baden-Württemberg to Germany, no species substitution |
| `test_sensors` | SensoBox pollen corrects the map near the box only; grass and birch count zero; untyped totals unused; weather-only boxes leave the map alone |
| `test_health` | Healthy colonies (October and June) pass; dead-out, hornet pressure, damp hive and failed brood nest are flagged |
| `test_osm` | Overpass conversion (split outlines, holes, hedges, protected areas, places); timeouts hidden in 200 answers, busy mirrors and empty answers caught |
| `test_ondemand` | Background fetching swaps data in, land cover only in view, bloom months saved progressively and not refetched, failed fetches wait before retrying |
| `test_nest_reports` | Photo quality gate; real ONNX Runtime with tiny stand-in models; confident model confirms; "not Asian" never closes a report; recheck; manual check; public view hides photo, note and exact spot |
| `test_correlate` | Reports at a triangulated nest match strongly and name the hives; 75% photo plus match confirms; located area leaves planning; removal closes the area; corridor matches; late small-nest flag |
| `test_city` | Pipeline results on the map from small hand-made output files: endpoints answer `404` with a hint before any data exists, serve the data once it does, and pick up a rerun; readings from several colonies at one station are stored and reported separately |
| `pi/test_agent` | Sky and horizon heading cameras recover directions within 3–4°; hornets outside the view become presence-only; flipped cameras |

## Limits of the tests

- **External services** (Overpass, iNaturalist, map tiles) were tested only with mock responses matching their documented formats. The first real run happens online.
- **No trained nest-photo model exists;** the tests use stand-in models with fixed answers.
- **All accuracy figures are from simulations,** not from real hornets, hives or nests.
- **The data pipeline has no automated tests.** `test_city` checks only how the server reads its files. The pipeline's own checks are the reports it writes (`inspect_report.txt`, `findings.md`, the validation tables and plots), and it's rerun only when new data arrives.
- **The Pi live view, autofocus and rotation** were tried by hand on a Pi 4 with a Camera Module 3, not in `test_agent`.

## The demo as an integration test

`tools/demo.py` drives the whole system the way real devices would. Simulated hornets go through the real Pi tracking, two-camera pairing and upload code; SensoBoxes and hives post through the device API. It prints how far each estimated nest is from the hidden truth. `--grow` and `--install-planned` exercise the planning loop.
