# Running and operating BeeMap

## Requirements

- **Python 3.10 or newer** (the server won't start on older versions).
- Server packages: `pip install -r server/requirements.txt` (FastAPI, uvicorn, requests, numpy, Pillow, OpenCV). `onnxruntime` is optional, for the nest-photo model.
- Internet for the **server** (OpenStreetMap and iNaturalist data) and for the **browser** (map tiles, web fonts). Both are optional: without them, BeeMap falls back to built-in data, and the map has no streets.

## Quick start

```bash
./pitch.sh                      # fresh demo city at http://localhost:8000
PORT=8010 ./pitch.sh            # another port
BEEMAP_GEODATA=demo ./pitch.sh  # keep the demo map shapes (the pitch script's examples assume these)
```

`pitch.sh` checks the Python version, installs packages, checks the port is free, starts the server, waits until it answers (printing its log if it crashes), then runs `tools/demo.py --live`.

**By hand** (and on Windows):

```bash
cd server && python3 -m uvicorn app:app --host 0.0.0.0 --port 8000
python3 tools/demo.py --live              # second terminal, from the project folder
```

**Demo options:**
- `--grow`: install hives at the suggested sites and show the effect.
- `--install-planned`: switch on planned hives (a live pitch moment).
- `--seed N`: a different random demo.
- `--server`: another server address.
- `--real-stations PATH`: real Pis to register next to the simulated hives, with fixed tokens (default `tools/real_stations.ini`, used by `pitch.sh` automatically when it exists). See [hardware.md](hardware.md#a-real-pi-during-the-pitch-demo).

## Configuration (environment variables)

| Variable | Default | Effect |
|---|---|---|
| `BEEMAP_DATA` | `server/data` | Database, photos, admin key |
| `BEEMAP_ADMIN_KEY` | generated into `data/admin_key.txt` | Admin key for `tools/admin.py` |
| `BEEMAP_ONLINE` | `on` | `off`: no OpenStreetMap / iNaturalist fetching (offline demo) |
| `BEEMAP_GEODATA` | `auto` | `demo`: always the demo map; `osm`: require OpenStreetMap data |
| `BEEMAP_REPORTS` | `on` | Default for public nest reports (the admin setting overrides it) |
| `BEEMAP_CARTO_KEY` | none | Enables the CARTO background maps |
| `BEEMAP_NEST_MODEL` | `server/models/nest_classifier.onnx` | Nest-photo model path |
| `BEEMAP_WINDOW_DAYS` | 14 | Departures counted for nest areas |
| `BEEMAP_MAX_RANGE_M` | 3000 | Longest flight line considered |
| `BEEMAP_M_PER_MIN` | 100 | Metres per minute for round-trip distance estimates |
| `BEEMAP_WEB` | `web/` | Folder served as the website |
| `BEEMAP_SERVER` | `http://127.0.0.1:8000` | Server address used by `tools/admin.py` and `pipeline/scripts/beemap_sync.py` |
| `BEEMAP_PIPELINE_OUT` | `pipeline/output` | Folder of data pipeline results the server reads for `/api/city/*` |

The data pipeline also reads `BEEMAP_NC_SHARE` and `BEEMAP_WALKS_SHARE` (private share tokens for its downloads); see [pipeline.md](pipeline.md).

## The admin tool

`tools/admin.py` runs on the server machine and reads the admin key automatically.

```bash
python3 tools/admin.py alerts                       # alerts waiting for confirmation
python3 tools/admin.py confirm 12                   # or: dismiss 12
python3 tools/admin.py nests                        # nest areas
python3 tools/admin.py assign 3 "Feuerwehr Team 2"  # then: removed 3
python3 tools/admin.py nest-reports [--status pending]
python3 tools/admin.py nest-report 4 asian          # or: other --species "European hornet", not-found, removed, pending
python3 tools/admin.py reports-recheck              # photo check again (e.g. after installing a model)
python3 tools/admin.py reports on|off
python3 tools/admin.py station-add ID "Name" LAT LON --heading DEG [--planned]
python3 tools/admin.py station-token ID
python3 tools/admin.py box-add ID "Name" LAT LON [--station HIVE]
python3 tools/admin.py box-token ID
python3 tools/admin.py health [HIVE] [--hive NAME]  # colony health (private); --hive: one colony at a station with several
```

`health` without an ID lists every colony. A station with several colonies (such as `hornet-pi`, which gets the three OpenSpace hives from the data pipeline) shows one line per colony, named after the station and the colony, e.g. `OpenSpace hives · Beehive No.2`. `health hornet-pi` details every colony at that station; `health hornet-pi --hive "Beehive No.2"` details just one.

## Refreshing the city data (data pipeline)

The Bee Balance, public sightings, street heat, flight hours and hive camera study come from `pipeline/`. Their results are committed in `pipeline/output/`, so **nothing needs running for a normal start or a pitch**. Rerun a step only when new data arrives (a new season of hive data, new recordings, fresh sightings); the server picks up changed files without a restart. To send the latest real hive sensors into a running server: `cd pipeline && make beemap BEEMAP=http://host:8000`. Setup, steps and caveats are in [pipeline.md](pipeline.md).

## Installing a nest-photo model

1. Train an image classifier on labelled nest photos: Asian hornet, European hornet, wasp, not a nest. Fine-tuning a small pretrained network (e.g. MobileNet or EfficientNet) is a good start.
2. Export it to ONNX with input `1×3×224×224`, ImageNet-normalised.
3. Save it as `server/models/nest_classifier.onnx`, with class names one per line in `nest_classifier.labels.txt`.
4. Run `pip install onnxruntime`, then `python3 tools/admin.py reports-recheck`.

Check its accuracy on photos it never saw during training before relying on the 90% / 75% thresholds.

## Deployment notes

- **One process, one SQLite file.** Fine for a city prototype; back up `server/data/`. It runs behind any reverse proxy (HTTPS needed for phone geolocation).
- **Map tiles:** OpenStreetMap's tile servers are for light use. A public launch needs a tile provider with a plan, or CARTO with a key.
- **Rate limits** (20 anonymous submissions per 10 minutes per address) are in memory and reset on restart.
- **First start online:** OpenStreetMap data arrives in about a minute; iNaturalist bloom times take a few minutes (one plant every couple of seconds). Run it once with internet before a pitch so the data is cached.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `Connection refused` on port 8000 | Server not running, or crashed on start | Run `./pitch.sh` (it prints the server's error), or start the server by hand and read its output |
| Server won't start | Python < 3.10, or port taken | `python3 --version`; `PORT=8010 ./pitch.sh` |
| WSL: page can't reach the server | Server and browser in different worlds | Start the server inside WSL with `--host 0.0.0.0`; open `localhost:8000` in Windows |
| `bash\r: No such file or directory` | Windows line endings | `sed -i 's/\r$//' pitch.sh` |
| **No streets on the map** | Browser can't load map tiles | Check the *browser's* internet; disable blockers for `tile.openstreetmap.org`; try another background under Layers. The note bottom left says what failed. |
| "Couldn't reach OpenStreetMap / iNaturalist" | Server has no internet, or the service is busy | Retries automatically every 10 minutes; built-in data meanwhile. `BEEMAP_ONLINE=off` to stop trying. |
| Nest report photos "need a check by a person" | No model installed, or photo too small, blurry or dark | Expected without a model; see above |
| Bee Balance, sightings or street heat layer says "run make …"; their cards are missing | `pipeline/output/` lacks that file, or `BEEMAP_PIPELINE_OUT` points elsewhere | `GET /api/city/status` lists what's there; restore the files from git, or run that pipeline step |
| `make beemap` sends 0 new hive readings to a fresh server | Sync state from before registration times were tracked, or the server is older than the pipeline | Delete `pipeline/output/beemap_sync_state.json` (only for a fresh database, or readings are stored twice) and update the server |
| `make beemap`: "hourly.csv missing" | The raw hive series are kept local, not in git | Run `make fetch analyse` on that machine first |
| Pi: VespAI weights won't load | Current YOLOv5 from GitHub | Set `yolov5_dir` to a checkout of commit `23701eac` (see [hardware.md](hardware.md)) |
