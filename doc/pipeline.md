# Data pipeline

`pipeline/` is the offline analysis behind BeeMap's measured city layers. It moved here from the separate `bee-commute-tracker` repo on 28 Oct 2026; that repo's Streamlit dashboard stayed behind, because BeeMap's web map replaces it.

The pipeline does the heavy work: downloading raw data, training a bee detector, rasterising land use and so on. It writes small result files to `pipeline/output/`. The server reads those files with the standard library (`server/citydata.py`) and serves them under `/api/city/*`. The server needs none of the pipeline's packages.

## When to run it

**Not routinely.** The results the server needs are committed in `pipeline/output/`, so a fresh checkout shows every layer without running anything. Rerun a step only when new data arrives, for example:

- new hive camera recordings or labels, to retrain and recount (`make dataset train count analyse`);
- a new season of hive sensor or weather data (`make fetch analyse validate`, then `make beemap`);
- refreshed OpenStreetMap land use or GBIF sightings (`make balance`, `make hornets`).

The server re-reads a file when its modification time changes, so rerunning a step updates the map without a restart.

## Setup

The pipeline needs **Python 3.11** and heavier packages than the server: pandas, scipy, matplotlib, OpenCV, ultralytics and others (`pipeline/requirements.txt`).

```bash
cd pipeline
python3.11 -m venv .venv && .venv/bin/pip install -r requirements.txt
make help
```

The Makefile calls `.venv/bin/python` by default. Use `make PY=python3 …` for a different interpreter.

**Data shares.** The Nextcloud links for the hive recordings and the summer-school walks are private and are not in the repo. Set them in the environment before `make fetch`:

| Variable | Used by | What it is |
|---|---|---|
| `BEEMAP_NC_SHARE` | `fetch_data.py` | Share token for the hive entrance recordings and labelled crops (cloud.reedu.de) |
| `BEEMAP_WALKS_SHARE` | `fetch_data.py` | Share token for the heat-island summer-school datalogger walks |

## Steps

| `make …` | Script | Reads | Writes (in `output/` unless noted) |
|---|---|---|---|
| `fetch` | `fetch_data.py` | Nextcloud, BeeHappy Grafana, senseBox, walks | `data/` (raw, ~1.3 GB). Each download is skipped if its file exists; `--only videos\|hive\|sensebox\|walks` |
| `inspect` | `inspect_data.py` | `data/` | `inspect_report.txt`: sessions, labels, sensor ranges and gaps |
| `dataset` | `make_dataset.py` | labelled 32×32 crops | `data/yolo/`, `data/bees.yaml`: YOLO dataset, one class (bee) |
| `train` | `train.py` | `data/bees.yaml` | `models/bee_yolov8n.pt`. `EPOCHS=50` by default, about 1.5 h; GPU when available, else CPU |
| `count` | `count_bees.py` | recordings, detector | `data/activity.csv` (bees per minute), `data/detections.csv` |
| `analyse` | `analyse.py` | activity, hive sensors, senseBox | `hourly.csv`, `correlations.csv`, `daily_curve.*`, `hive_health*`, `findings.md` |
| `validate` | `validate_weather.py`, `activity_model.py` | hive station, senseBox, Open-Meteo | `weather_validation.*`, `weather_offsets.csv`, `activity_validation.*`, `flight_hours.*`, `city_flight_hours.csv` |
| `walks` | `heat_walks.py` | datalogger walks, Open-Meteo | `heat_walks_grid.csv`, `heat_walks_summary.csv`, `heat_walks_stations.csv`, `heat_walks.png` |
| `sitemap` | `site_map.py` | OpenStreetMap (Overpass), senseBox | `site_scores.geojson`, `site_stations.csv`, `site_top.csv`: older 1 km site map, kept as a check |
| `balance` | `bee_balance.py` | OpenFreeMap tiles, Nominatim boundary, senseBox, walks | `bee_balance_grid.*`, `bee_balance_schools.csv`, `bee_balance_neighbourhoods.csv`, `bee_balance_summary.json` |
| `hornets` | `hornet_sightings.py` | GBIF | `hornet_sightings.csv`, `hornet_summary.json`, `hornet_sightings.png` |
| `lights` | `make_3d.py` | Bee Balance, detections | `commute_trips.json`, `detections_cloud.json`, `city3d.csv`: data for 3D views (not used by the web page yet) |
| `beemap` | `beemap_sync.py` | `hourly.csv` | Real hive sensors into a running BeeMap server (below) |
| `hornet-setup`, `hornet-live` | `hornet_setup.sh`, `hornet_live.py` | webcam or video | Stand-alone VespAI hornet alarm for testing the model; frames to `output/hornet_live/` |

`make all` runs everything from `fetch` to `beemap` in that order.

## What the server reads

`server/citydata.py` groups the files into datasets. Each is optional: a missing dataset makes its endpoint answer `404` with a hint to run the pipeline, its tab cards stay hidden, and its map layer says which `make` step to run.

| Dataset | Files | Endpoint | On the map |
|---|---|---|---|
| `balance` | `bee_balance_grid.csv`, `bee_balance_summary.json` | `/api/city/balance` | Layers → **Bee Balance (250 m)**; Hives tab card; place card |
| `schools`, `neighbourhoods` | `bee_balance_schools.csv`, `bee_balance_neighbourhoods.csv` | `/api/city/places` | School dots on the Bee Balance layer; Hives tab lists |
| `sightings` | `hornet_sightings.csv`, `hornet_summary.json` | `/api/city/sightings` | Layers → **Public sightings (GBIF)**; Hornets tab card |
| `heat` | `heat_walks_grid.csv` | `/api/city/heat` | Layers → **Street heat (walks)**; Sensors tab card; place card |
| `flight` | `flight_hours.csv`, `city_flight_hours.csv` | `/api/city/flight-hours` | Sensors tab: **Bee flight hours** |
| `study` | `correlations.csv`, `activity_validation.csv` | `/api/city/study` | Sensors tab: **Measured at the OpenSpace hives** |

Every other file in `output/` (plots, `findings.md`, the validation tables, the 1 km site map, the 3D data) is for people reading the analysis, not for the server. `BEEMAP_PIPELINE_OUT` points the server at another output folder (the tests use this).

## Real hive data into BeeMap (`make beemap`)

`beemap_sync.py` sends the OpenSpace hives' real sensor data through BeeMap's normal upload API, as a Pi or SensoBox would:

- **BeeHappy in-hive sensors** of the three colonies go to station `hornet-pi` as `POST /api/stations/hornet-pi/hive`, with `hive` = `Beehive No.1`, `No.2` and `No.3`. Colony health then lists each one separately (see [api.md](api.md)).
- **The hive's SenseCAP weather station** becomes SensoBox `openspace-weather`, linked to that station, so colony health compares against measured outside weather.

Details:

- If the station or box doesn't exist yet, the script registers it at the hives' position (the station as *planned*, since it has no camera yet). It needs the admin key: `--admin-key`, `BEEMAP_ADMIN_KEY`, or `server/data/admin_key.txt`.
- Only hours newer than the last sync are sent. Progress is kept per server and per device token in `output/beemap_sync_state.json`, so a reset BeeMap database gets everything again.
- `--temp body` (default) uses the Dragino S31 hive-body sensor, which has humidity. `--temp brood` uses the D23 brood-nest probes instead.
- `--server` sets the address (or `make beemap BEEMAP=http://host:8000`); `--station` sets another station ID.

**It needs `output/hourly.csv`, which is not in git** (the organisers' raw series stay local). Run `make fetch analyse` on the machine first.

## What stays out of git

The repository is public, so only the small, shareable results are committed. Ignored (see `.gitignore`):

- raw data `pipeline/data/`, the `.venv`, training runs, model weights (`models/*.pt`) and the pinned YOLOv5 in `vendor/`: all rebuilt or fetched by `make`;
- `output/hourly.csv` and `output/hive_health.csv`: the organisers' raw hive sensor series;
- `output/heat_walks_points.csv`: the walkers' GPS tracks;
- `output/hornet_live/`, `output/*.mp4`, `output/train_log.txt` and `output/beemap_sync_state.json`.

## Before trusting the numbers

- **Frame interval.** The hive recordings don't document their frame interval; `FRAME_INTERVAL_S` in `scripts/common.py` assumes 1 s. Bees per minute scale with it; rankings don't.
- **Six hours of signal.** Only 6 hourly bins of camera footage carry a motion signal, so the weather → activity link is a direction check, not a trained model. Sessions with no coloured motion ghosts at all are excluded, not counted as zero bees.
- **Detections aren't bees.** A fast bee can show up as three coloured ghosts in one composite frame.
- **Which hive?** The camera watches one entrance; the data share doesn't say which of the three hives.
- **Hive-body sensor.** The S31 sits in the hive body, not necessarily the brood nest, so the 33–37 °C brood band in `analyse.py` is breached most of the year (all three hives show red in `findings.md`). BeeMap's colony health uses the same band for its brood-nest check, so read its brood warnings for the real hives with this in mind, or sync with `--temp brood` once the organisers confirm where the brood probes sit.

The methods are described in [algorithms.md](algorithms.md#12-data-pipeline-pipeline).
