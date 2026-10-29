# BeeMap: a pollinator map for Heilbronn

Proof of concept for the Green City Hackathon Heilbronn.

BeeMap helps a city, its beekeepers and its residents look after **all** pollinators, honeybees and wild bees alike:

* **Where can I keep bees?** The hive spot check rates any place green, amber or red for a new apiary. It looks for low predation from Asian hornets, enough distance from wild-bee habitats and from other apiaries (so honeybees don't compete with wild bees for the same flowers), and enough forage through the season. If a spot isn't good, it suggests the nearest green one.
* **What should I plant?** The plant planner works out which months your neighbourhood is short of flowers and recommends plants that fill exactly that gap: native wildflowers, shrubs and trees for balconies, gardens, public green and hedges, and bee-friendly crops, cover crops and regional flower strips for farm fields. "I planted something" adds it to the map, and the neighbourhood goal fills up.
* **Asian hornet alerts and nest finding** (one module of the app). Smart hives recognise the hornet and measure which way it flies home; their lines cross at the nest. More on this below.
* **One public map, no login.** Everything above lives on a single page anyone can open, like Google Maps: search, a panel with tabs, layers with filters, and a card for any spot you click. City operations (confirming alerts, sending teams to nests, registering hives and sensors) happen on the server with `tools/admin.py`, not on the website.

### The public map

* **Click anywhere** for a place card: which months bees go hungry within 800 m and what to plant there (with when to plant it), whether it's a fair spot for a hive (and the nearest green one), hornet activity nearby, the nearest SensoBox, and whether a smart hive there would help find nests. "I planted something" adds an anonymous planting.
* **Tabs:** Overview, Hornets (alerts with photos, nest areas), Flowers (flower gaps by month, a plant guide filterable by place, month and native plants), Hives (smart hives, registered apiaries, good spots, suggested smart-hive sites), Sensors (SensoBoxes).
* **Report a nest** (orange button): see "Nest reports from the public" below.
* **Layers** (top right), each with its own filters: hornet alerts (period, cameras or reports, confirmed only), flight lines (with or without direction-only corridors), nest areas (by status), hives (smart, planned, registered; 800 m radius), wild-bee zones, good hive spots, suggested smart-hive sites, flower gaps (month), land cover (by kind), plantings, SensoBoxes (pollen or weather; where they correct the map). The panel lists use the same filters as the layers.
* **Shareable:** the address bar keeps the map position, layers, tab, month and clicked spot, so any view can be sent as a link.
* **Phones:** the panel becomes a bottom sheet.
* **Privacy:** no beekeeper names; registered apiaries shown only to about 100 m; reporters anonymous; camera calibration frames and colony health stay private. Only Pis and SensoBoxes (with device tokens) can upload data.

### City operations: `tools/admin.py`

Run on the server (it reads the admin key the server generates in `server/data/admin_key.txt`, or `BEEMAP_ADMIN_KEY`):

```bash
python3 tools/admin.py alerts                 # alerts waiting for confirmation
python3 tools/admin.py confirm 12             # or: dismiss 12 (not an Asian hornet)
python3 tools/admin.py nests                  # nest areas
python3 tools/admin.py assign 3 "Feuerwehr Team 2"   # then: removed 3
python3 tools/admin.py station-add hive20 "Hive 20, Biberach" 49.105 9.150 --heading 40 [--planned]
python3 tools/admin.py box-add sb-x "Kirchhausen" 49.148 9.125 [--station hive20]
python3 tools/admin.py health [hive7-sontheim]   # colony health (private)
python3 tools/admin.py nest-reports [--status pending]   # reported nests: exact spot, note, photo, photo check
python3 tools/admin.py nest-report 4 asian    # checked on site (or: other --species "European hornet", not-found, removed)
python3 tools/admin.py reports-recheck        # run the photo check again, e.g. after installing a model
python3 tools/admin.py reports on|off         # public nest reports (on by default)
```

### Bloom times from public data

The plant planner's bloom calendar comes from **iNaturalist**: for each plant, the server asks the public iNaturalist API how many observations were annotated *flowering* in each month, in Baden-Württemberg (or all of Germany if there are fewer than 30). Bloom months are the months with at least 20% of the peak month's flowering photos. The calendar shades them by intensity, and every plant shows how many observations its calendar rests on, with a link to its species page.

* **Fetched on demand:** nothing to download first. The server fetches bloom times in the background, one plant per few seconds, saves each as it arrives (`server/bloom_data.json`), and re-checks each plant after 90 days. The map shows the progress in a small status note. To refresh by hand: `python3 tools/update_bloom_data.py`.
* Plants without enough observations, and seed mixes, keep bloom months from gardening references, and the app labels them as such.
* The planner also says **when to plant or sow** each plant so it flowers in your gap months: shrubs and trees from October to March; wild perennials in spring or autumn (many flower fully only from their second year); crops counted back from their time to flowering (phacelia about 7 weeks, buckwheat about 5). These planting windows are general gardening guidance, not data.
* Data: iNaturalist (www.inaturalist.org). Observations belong to their observers; BeeMap stores only aggregated monthly counts.

### Measured forage: SensoBoxes

Where a **SensoBox** measures pollen, the forage map uses the measurement instead of relying only on the land-cover estimate and what app users report planting. Each box's bee-relevant pollen per month (last 12 months) is compared with what the model predicts at that spot; the difference becomes a correction that fades out over about 1.5 km (Gaussian), blended in log space against the model. Near a box the measurement dominates; far from any box the model is unchanged. The plant planner, spot check and gap map say which applies: *"Measured by SensoBox Böckingen Rathaus: less bee pollen than the map suggests"* or *"Estimated from the land-cover map"*.

Boxes with only weather sensors (pressure, humidity, light/UV, temperature, as today) still help: they give **bee flight weather**, the share of daylight in which bees can fly (at least 12 °C, not saturated air).

**For the hardware team: the pollen sensor needs to identify pollen types.** Airborne pollen is dominated by wind-pollinated plants (grasses, birch, pine, nettle) that bees hardly use, with counts in the hundreds of grains/m³, while most bee plants release little pollen into the air. A total pollen count therefore can't show bee food: it would make the grass season look like a feast. BeeMap shows untyped totals but does not use them for the forage map. With typed counts, each type is weighted by its value to bees (`POLLEN_BEE_WEIGHT` in `server/ecology.py`: willow, lime, fruit blossom, clover high; hazel and alder some; grass, birch, pine zero). Even then, insect-pollinated plants are under-represented in air samples, so the model is corrected by *relative* changes (which months, which places are leaner than expected) rather than read as absolute amounts. For a direct measure of what bees collect, pollen traps at smart-hive entrances would complement the boxes.

**Upload format.** Register a box under **SensoBoxes > Add SensoBox**, then post readings, one or in batches (every field optional):

```bash
curl -X POST http://localhost:8000/api/sensoboxes/sb-boeckingen/readings \
  -H "X-Box-Token: <token from the app>" -H "Content-Type: application/json" \
  -d '{"readings": [{"ts": "2026-05-04T12:00:00Z", "temp_c": 17.2, "humidity": 61, "pressure_hpa": 1016.4,
       "lux": 42000, "uv_index": 4.1, "pollen_total": 210.5,
       "pollen": {"salix": 12, "rosaceae": 30, "poaceae": 160, "betula": 8.5}}]}'
```

`pollen` is grains/m³ by type (lower-case family or genus). The demo simulates four boxes with a year of readings: three with typed pollen whose "real" forage deliberately differs from the map (a lean March in Böckingen, mown parks in the centre, lime trees in Sontheim), and one with weather sensors only.

### Colony health

Each smart hive has, besides its cameras, a **temperature and humidity sensor inside the hive** and **its own SensoBox** outside. BeeMap correlates the colony's activity with the weather:

| Signal | How | What it can point to |
|---|---|---|
| Flight activity vs weather | Learns each colony's own activity-per-degree from its earlier weeks, then compares the last 3 days with what that weather predicts | Weak colony, queen loss, poisoning or a swarm (sudden drop) |
| Weather coupling | Correlation of activity with outside temperature over 30 days | Healthy colonies fly much more on warm hours |
| Brood nest (Mar–Sep) | Share of time at 33–37 °C, and whether hive temperature just follows the weather | Loss of brood or queen |
| Winter cluster (Oct–Feb) | Hive temperature minus outside temperature | Hive as cold as outside: possible colony loss |
| Humidity inside | Time above 85% | Damp hive, ventilation |
| Hornet pressure | Hornet visits at this hive plus reduced activity | Bees stay in while hornets hover at the entrance |
| Forage | The flower model (corrected by SensoBox pollen) at the hive | Low activity from hunger, not illness |

**Activity** comes from the detection camera: the Pi counts moving bees in the entrance area (`entrance_roi`) by motion, no extra AI. It's a relative number: each colony is compared only with itself, so camera position doesn't matter as long as it stays put. **In-hive sensor:** set `hive_sensor = bme280 | sht31 | dht22` in `beemap.ini`. The Pi posts both every 5 minutes to `/api/stations/<id>/hive` and keeps them if the network drops. Link a SensoBox to its hive with `station_id` when adding it; hives without their own box use the nearest box within 3 km for outside weather.

Colony health is the beekeepers' data, so it is **not on the public map**. Operators see it with `python3 tools/admin.py health`; showing it to each beekeeper would need a private beekeeper view, which this public site deliberately leaves out.

These are **indicators to prompt an inspection, not diagnoses.** Thresholds are beekeeping rules of thumb (named constants in `server/health.py`) and haven't been validated against real colonies. Sensor placement matters a lot: a sensor above the brood nest reads very differently from one at the side.

### Background map

The streets, buildings and labels come from **OpenStreetMap's own map tiles** (toned down a little so BeeMap's layers read on top). **Layers → Background map** also offers *OpenStreetMap Humanitarian*, and CARTO's calmer *Voyager* and *Positron* styles if you set a free CARTO key (`BEEMAP_CARTO_KEY=…`, from carto.com/basemaps; CARTO now requires one outside its own platform). If a background's tiles don't load, the page switches to the next one and says so; if none load, it says the browser can't reach the map servers.

*If you see no streets at all:* the browser can't load map tiles. Check that the browser itself is online (in WSL, the Windows browser needs internet, not WSL), that no ad blocker or company proxy blocks `tile.openstreetmap.org`, and try the other background under Layers. OpenStreetMap's tile servers are for light use: fine for a demo, but a public deployment should use a tile provider with a plan (or CARTO with a key).

### Real map data from OpenStreetMap

The server fetches Heilbronn's actual land cover from **OpenStreetMap** (one Overpass API request, in the background, the first time it starts) and uses it instead of the demo shapes, both for the forage model and for a **Land cover** map layer (**Layers → Land cover**, filterable by kind):

* areas: orchards, meadows and grassland, vineyards, forest, fields (rapeseed where tagged), parks, gardens and allotments, scrub, flower beds, lawns, water, built-up land
* lines: hedges (3 m strips) and tree rows (8 m), added to the map as a share of each 50 m cell
* **wild-bee zones**: nature reserves, natural monuments and protected landscape features. Large landscape protection areas (Landschaftsschutzgebiete) are left out by default because they'd cover half the city; add `--include-landscape` to include them. Protected areas are only a starting point: real wild-bee habitats (sand banks, dry-stone walls, flower-rich verges) need local experts to map them.
* **place names**: districts and villages, for labels like "Böckingen"

This is **not** the background map: it's the land use and nature areas BeeMap's flower model needs (roads come from the background tiles). **Fetched on demand:** nothing to download first. Overpass sometimes answers "OK" with a timeout inside, or a mirror is busy; BeeMap then tries the next mirror and never replaces good data with a near-empty answer. Until it arrives (usually under a minute), or without internet, the map uses the demo shapes and says so; failed fetches are retried at most every 10 minutes. It's kept in `server/osm_data.json` and refreshed after 30 days. The land-cover layer loads only the shapes in view as you pan. To refresh by hand: `python3 tools/update_osm_data.py`. Multipolygon outlines split across several ways are joined, and holes (a clearing in a forest) are kept. Map data © OpenStreetMap contributors, under the ODbL; the app shows this credit whenever the data is in use.

`BEEMAP_GEODATA=demo ./pitch.sh` keeps the hand-placed demo shapes. `BEEMAP_ONLINE=off` stops all fetching (fully offline demo; the built-in data is used). The pitch script's spot-check example (Wertwiesenpark comes out amber) was written for those, so with real data the verdicts can differ: Wertwiesenpark's demo wild-bee meadow isn't a protected area in OpenStreetMap. Delete `server/osm_data.json` to switch back to the demo data.

### Pollinator model in one paragraph

The city map is a 50 m raster of land cover (orchard meadows, meadows, gardens, parks, lime avenues, hedges, vineyards, forest, fields, rapeseed…). Each type has a typical monthly flowering profile. Summed over an **800 m radius** this gives the flowers available around any point, month by month, plus everything citizens and farmers report planting. A spot check combines four things: overlap with wild-bee zones, apiaries within 800 m (and how many colonies would share the flowers), the season's flower supply, and distance to active hornet nest areas.

### Hornet module: two cameras per smart hive

Each smart hive has a Raspberry Pi with **two cameras**:

* a **detection camera** at the entrance or bait station, running **VespAI** to recognise the Asian hornet and tell when it leaves;
* an external **heading camera** that follows the hornet as it flies off and measures its direction. Two mountings are supported: **sky** (a fisheye looking straight up, which sees every direction) and **horizon** (looking outward with a known field of view, more precise but only within that view).

The Pi pairs the two: when VespAI sees a hornet leave, the heading track that starts at that moment gives its direction. If the heading camera misses it, the visit still counts as a presence alert. Stations with only one camera also work, more roughly.

**Nest reports from the public** are on by default: anyone who finds a nest can report it from the map (the orange **Report a nest** button, the Hornets tab, or a place card): where it is (clicked, the phone's location, or picked on the map), what it's in or on, how high, how big, an optional note and an optional photo, with a safety reminder and a guide to Asian hornet, European hornet and wasp nests. Reports are anonymous. The public map shows each report to about 100 m with its status; photos and notes are for the city only, and photos are re-saved without their metadata (phones store the GPS position and device in them).

**How a report gets confirmed:**

1. **Photo quality gate** (always): is the photo at least 480 px on its short side, sharp (variance of the Laplacian) and properly exposed? If not, the report says why ("photo too blurry: needs a check by a person").
2. **Image model** (when installed): a good photo goes to an image classifier (Asian hornet nest / European hornet nest / wasp nest / not a nest). If it is **at least 90% sure it's an Asian hornet nest**, the report is confirmed at once and marked *confirmed by photo check* (a team still verifies on site). A model that thinks it's *not* an Asian hornet nest **never closes a report by itself**: a missed nest costs far more than a visit, so those wait for a person.
3. **Manual check:** everything else is checked on site and recorded with `tools/admin.py nest-report <id> asian|other|not-found|removed`, shown as *checked by the city*.

**Reports and smart hives back each other up** (`server/correlate.py`). Each report is compared with the camera evidence:

* **Nest areas** (where flight lines from several hives cross): the report's distance from the centre is measured in the area's own uncertainty (a long thin ellipse is precise sideways but not lengthwise), allowing about ±50 m for where people drop the pin. *Strong* = inside the 68% zone, *fits* = inside the 95% ellipse, *near* = within 99.7% (shown, nothing automatic).
* **Direction-only corridors** (one hive's flight line): the angle off the line in units of its uncertainty, up to 2.5 km out. A report on the line gives the distance a single hive can't.
* Only reports that could still be Asian hornet nests and are from this season (about 5 months) count; a small nest reported from July on is flagged as possibly an abandoned spring nest (Asian hornets move from a small spring nest to a large summer one).

What it changes: reports that fit are **checked first** (the admin list puts them on top); a photo check that is **75% sure** confirms a report that fits (*photo check + flight lines*), instead of the 90% needed on its own; a nest area with a **confirmed** fitting report is shown as **located** with a pin, and drops out of smart-hive planning; marking that report **removed** also closes its nest area, so its departures stop counting. On the map, dotted lines join reports to the area they fit, and popups name the hives whose flight lines back them up.

**Installing a model.** BeeMap ships **no trained model**: put an ONNX image classifier at `server/models/nest_classifier.onnx` with its class names in `nest_classifier.labels.txt` (default `asian_hornet_nest, european_hornet_nest, wasp_nest, not_a_nest`), input 1×3×224×224 RGB, ImageNet-normalised, then `pip install onnxruntime` and run `python3 tools/admin.py reports-recheck` to check the waiting reports. Training one needs a labelled set of nest photos for each class (for example from beekeeping associations, the city's own confirmed reports, and research-grade iNaturalist observations of nests). Fine-tune a small pretrained network (e.g. MobileNet or EfficientNet), and check its accuracy on photos it never saw before trusting the 90% threshold. The wiring is tested with tiny stand-in models through real ONNX Runtime (`server/tests/test_nest_reports.py`). Switch reporting off with `python3 tools/admin.py reports off`.


**Documentation:** see [doc/](doc/README.md): description, architecture, repository structure, algorithms, API, data sources, hardware, operations, privacy and security, testing, design decisions, and limitations and roadmap.

## Data pipeline: real measurements behind the map

`pipeline/` is the analysis that used to live in the separate `bee-commute-tracker` repo. It does the heavy, offline work and writes small result files to `pipeline/output/`. The server reads those files (`server/citydata.py`, standard library only) and serves them under `/api/city/*`. It re-reads a file when it changes, so rerunning a step updates the map without a restart.

```bash
cd pipeline
python3.11 -m venv .venv && .venv/bin/pip install -r requirements.txt   # pandas, scipy, ultralytics…: heavier than the server
make help          # every step
make all           # fetch → … → balance → hornets → beemap (the detector training alone takes ~1.5 h)
make beemap        # just send the latest hive sensors to a running server (BEEMAP=http://host:8000)
```

| On the map | Pipeline step | What it is |
|---|---|---|
| **Layers → Bee Balance (250 m)**, Hives tab card, place card | `make balance` | Every 250 m square: share of bee food within 1.5 km (honeybees) and 300 m (wild bees) from OpenStreetMap land use, verdict hive-ready / plant first / no extra hives (city quarters). Schools and neighbourhoods get their square's verdict. A second opinion next to the 800 m spot check. |
| **Layers → Public sightings (GBIF)**, Hornets tab card | `make hornets` | Asian hornet, European hornet and honey bee records from iNaturalist, naturgucker and Observation.org, 22 km around Heilbronn. As reported; not checked by the city. |
| **Layers → Street heat (walks)**, Sensors tab card, place card | `make walks` | Summer-school datalogger walks (1–3 Sep 2026) on 100 m cells, each walk against its own median. |
| Sensors tab: **Bee flight hours** | `make validate` | Rule model on Open-Meteo 2025 weather, with the humidity term the hive camera supports; Heilbronn by month and 8 cities. |
| Sensors tab: **Measured at the OpenSpace hives** | `make count`, `make analyse` | YOLOv8n bee detector on the hive camera (mAP50 0.80); traffic vs weather over 6 hourly bins. |
| **Colony health** (`tools/admin.py health`) | `make analyse`, `make beemap` | The real BeeHappy sensors of the three OpenSpace hives (inside temperature and humidity, June 2025 →) and the hive's SenseCAP weather station, sent through the normal upload API: the colonies go to station `hornet-pi` as `Beehive No.1–3`, the weather station becomes SensoBox `openspace-weather`. |

**Several colonies per station.** Hive readings now carry an optional `hive` name, so one station (an apiary) can report several colonies. `GET /api/admin/health` lists each colony, and `/api/admin/health/<station>?hive=<name>` shows one; in the admin tool, `health <station>` details every colony there and `--hive <name>` picks one. Readings without a name work as before.

What's not in git: raw data (`pipeline/data/`, ~1.3 GB), training runs, model weights and the pinned YOLOv5 are fetched or rebuilt by `make`. The small results the server reads stay in `pipeline/output/`.

## Run the pitch demo

Needs Python 3.10+.

```bash
./pitch.sh                 # macOS / Linux
```

Windows, two terminals:

```bash
pip install -r server/requirements.txt
cd server && python -m uvicorn app:app --port 8000
python tools/demo.py --live          # second terminal, from the project folder
```

Open **http://localhost:8000**: one public map, on a laptop or a phone. On the first start the server fetches OpenStreetMap land cover and iNaturalist bloom times in the background; a small note on the map shows the progress. The demo builds a sparse network in Heilbronn: 6 smart hives (one offline), one planned, and three hidden nests the hornets fly home to. Most heading cameras are sky-facing; Hive 4 has an outward camera aimed towards Sontheim, so hornets leaving behind it arrive without a direction. Simulated hornets run through the real Raspberry Pi code (both cameras, pairing, upload), not straight into the database. The demo also adds a few nest reports in different states.

The map and fonts load from the internet. Everything else runs offline.

## Three-minute pitch script

1. **The map.** "This is Heilbronn for bees, open to everyone, no login: smart hives, wild-bee zones in green, hornet alerts in orange, SensoBoxes measuring weather and pollen."
2. **Click Böckingen.** "Bees here go hungry in March, and that's measured by the SensoBox, not guessed. So: goat willow, lungwort, cornelian cherry, planted this October." Switch the setting to *Farm field*: clover, sainfoin, a regional flower strip. Click **I planted something**: the neighbourhood goal moves.
3. **Click Wertwiesenpark.** "As a hive spot: amber. It overlaps a wild-bee meadow and three apiaries already share these flowers." **Show the green spot**: 750 m north-east.
4. **Hornets tab.** "Smart hives saw hornets today; their flight lines cross in Sontheim, so the nest is right there." Filter to 24 hours, then 30 days.
5. **Layers → Suggested smart-hive sites.** "In Frankenbach one hive only gives a direction. A camera hive here would locate that nest." Optional live moment: `python3 tools/admin.py station-add hive15 "Hive 15" <lat> <lon> --planned`, then `python3 tools/demo.py --install-planned`: the nest area appears.
6. **Share.** Copy the link: it opens the same view on any phone.
7. **The ask.** "One open map for bees in Heilbronn: fairer hive sites, food where it's missing, and hornet nests found faster."

(The spot-check verdicts in steps 2–3 are for the demo map data: run `BEEMAP_GEODATA=demo ./pitch.sh`. With OpenStreetMap data they differ.)

## What's inside

| Folder | What it is |
|---|---|
| `pi/` | Agent for the Raspberry Pi. Runs the VespAI YOLOv5 model on the detection camera, tracks the departing hornet on the heading camera (motion tracking, no AI needed), pairs the two, uploads with a snapshot, and queues offline. `--simulate` works without cameras. |
| `server/` | FastAPI + SQLite. Alerts, review, nest areas, network planning, live updates over WebSocket. |
| `server/triangulate.py` | Nest estimation and smart-hive site planning, pure Python. |
| `server/ecology.py` | Flower supply, hive spot check, safe-area map, plant planner and plant list. |
| `server/geodata.py` | **Demo** map data for Heilbronn: land cover, wild-bee zones, apiary register. |
| `server/correlate.py` | Matches nest reports to triangulated nest areas and flight corridors. |
| `server/nest_classifier.py` | Nest photo quality gate and the pluggable image-classifier check. |
| `server/ondemand.py` | Fetches OpenStreetMap and iNaturalist data in the background when needed (`osm_import.py`, `bloom_import.py`). |
| `web/` | The public map: `index.html`, `spa.js`, `spa.css` (no build step). |
| `tools/admin.py` | City operations from the server (alerts, nests, stations, SensoBoxes, colony health). |
| `tools/demo.py` | Demo city with simulated hornets. |

Connecting a real Pi: register it with `python3 tools/admin.py station-add ID "Name" LAT LON --heading DEG`, where the heading is which way the heading camera faces (sky: the top edge of its picture; horizon: the centre of its picture). Copy the Pi config into `pi/beemap.ini`, set the `heading_*` lines (see `beemap.example.ini`), put the VespAI weights at `pi/models/vespai.pt`, then run `python3 beemap_agent.py --test` and install `beemap-agent.service`.

**A real Pi during the pitch demo.** `pitch.sh` starts from an empty database, so a real Pi would be refused as "not registered". Copy `tools/real_stations.example.ini` to `tools/real_stations.ini` (not in git), and give each Pi its station ID, position, heading and a fixed token, the same one as in its `beemap.ini`. The demo then registers it next to the simulated hives. Its hornets appear live with the usual "Asian hornet seen" notice, on top of the simulated ones, and stay pending for a person to confirm. Point the Pi's `server` at the laptop's address (pitch.sh listens on all interfaces). Visits the Pi queued while it was refused are sent once it's registered. To show the real colonies' health as well, run `make beemap` in `pipeline/` after `pitch.sh` has started: it sends them into the same station, `hornet-pi`.

**A Raspberry Pi 4 with a Camera Module 3** (the HornTrac station, tested 28 Oct 2026). These settings come from getting VespAI to run on that hardware:

* **Use the pinned YOLOv5.** The VespAI weights only load with YOLOv5 at commit `23701eac`, so the current GitHub version fails. Check that commit out on the Pi and set `yolov5_dir` to it. On torch 2.6 or newer, the agent sets `TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD` itself.
* **Set `min_confidence = 0.8`**, which is VespAI's own field setting. At 0.5 the model boxes hair, flowers and bottles. It is only reliable on its training view, looking straight down at a plain bait board, and it misses close-ups and busy scenes.
* **Camera:** `camera = picamera2:0` with `autofocus = true`. Use `rotate = 90 | 180 | 270` if the camera is mounted sideways, and set the heading in the app after rotating.
* **Speed:** on a Pi 4 CPU the model runs at about 1 frame per second. That's enough to log a hornet at a bait station, but the exit direction is rough. A Pi 5 or a second heading camera does better.
* **Live view:** `stream_port = 8080` shows the camera with VespAI's boxes at `http://<pi>:8080/` for aiming. It has no login, so leave it at 0 outside a trusted network.
* **Python environment:** make it with `python3 -m venv --system-site-packages`, so Picamera2 from apt is visible. Install the CPU build of torch with `pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu`, because the default ARM build pulls in gigabytes of unused GPU libraries. Install OpenCV from apt with `sudo apt install python3-opencv`, so it matches the system numpy.

## How well it works (simulation, `server/tests/`)

* **Three hives about 1.5 km from a nest**, ±20° per departure, 12 departures each: median error 176 m. The true nest is inside the drawn 95% area 97 times in 100.
* **Network size**: 6 nests among 120 hives, all found in 18 ms. 300 hives: 13 of 15 nests found, 0.2 s.
* **Planner**: one new hive at the top suggested site turns a lone corridor into a located nest in 67 of 80 runs with few departures (an oracle that knows the nest manages 73) and in 77 of 80 runs with well-observed corridors (oracle: 80).
* **Heading camera** (simulated): a sky-facing camera recovers directions within about 4°, an outward camera within about 3° inside its view. In the demo the extra camera takes the newly located nest from 220 m to 76 m off.
* **Pollinator checks:** the example from the screen designs (2 colonies near Wertwiesenpark) comes out amber, overlapping a wild-bee meadow with 3 apiaries within 800 m, and a green spot is found 750 m away. The public API carries no owner names, tokens or exact apiary positions.
* Run `python3 server/tests/test_triangulate.py`, `python3 server/tests/test_api.py`, `python3 server/tests/test_eco.py`, `python3 server/tests/test_bloom.py`, `python3 server/tests/test_sensors.py`, `python3 server/tests/test_health.py`, `python3 server/tests/test_osm.py`, `python3 server/tests/test_ondemand.py`, `python3 server/tests/test_nest_reports.py`, `python3 server/tests/test_correlate.py`, `python3 server/tests/test_city.py` and `python3 pi/test_agent.py`.

## Honest limits of this proof of concept

* **Colony data in the demo is simulated**: one healthy hive, one under hornet pressure, one lost, one damp. The health rules have only been tested against these simulations.
* **SensoBox data in the demo is simulated.** The pollen weights by type, the 1.5 km reach and how strongly one box overrides the model are assumptions to tune once real boxes run for a season.
* **Map data:** with `tools/update_osm_data.py`, land cover and protected areas are real OpenStreetMap data, as complete as Heilbronn's mappers have made it (lawns and private gardens are often unmapped, and land cover is classified by tag, not checked on the ground). Without it, the shapes in `server/geodata.py` are hand-placed demo approximations. The **apiary register is always demo data**: it should come from the beekeeping association or the veterinary office. For official habitat data, add the LUBW protected-area and biotope maps.
* **Flowering profiles and targets are model assumptions**, plausible for south-west Germany but not measured. The 800 m radius follows the screen designs; honeybees often fly further. These are named constants in `server/ecology.py` for ecologists to tune.
* Observed bloom times depend on how many people photograph a plant in flower and annotate it; common, showy plants are well covered, rare ones fall back to the curated months. Citizen-science records also lean towards towns and weekends. The plant list contains well-known bee plants; Local nurseries and the nature-conservation office should check it for Heilbronn's soils, and for which species count as regional.

* All results above are **simulated**. Nothing has been tested with real hornets or real VespAI hardware yet. The VespAI model's class names and the camera setup need checking on a real station.
* The heading camera's motion tracking is untested with real hornets. A fisheye looking up sees the hornet against the sky only briefly, and wind-blown leaves or other insects can create false tracks. Pairing it with the VespAI departure time filters most of that, but this needs field trials.
* Directions are only as good as each heading camera's installation: measure its compass direction carefully once (±5°).
* Nests closer than about 600 m to each other can be estimated between the two.
* Foraging range (about 1.5 km) and visit rates in the planner are assumptions from the literature, not Heilbronn measurements.
* Not production ready: one SQLite database on one server, a single shared admin key for `tools/admin.py`, and simple per-address rate limits on anonymous plantings and reports (they could still be spammed).
* No user accounts: beekeepers are recognised by the name they enter in the app.
