# Repository structure

```
beemap/
├── README.md                  Quick start, feature overview, pitch script
├── pitch.sh                   One-command demo: checks Python, installs, starts server + simulated city
├── doc/                       This documentation
│
├── server/                    The BeeMap server (Python, FastAPI, SQLite)
│   ├── app.py                 HTTP API, WebSocket hub, auth, database schema, public views
│   ├── triangulate.py         Nest areas from flight bearings; smart-hive site planning
│   ├── ecology.py             Flower model (50 m raster), SensoBox fusion, hive spot check,
│   │                          safe-area and gap grids, plant list and plant planner
│   ├── health.py              Colony-health indicators (in-hive data vs weather)
│   ├── nest_classifier.py     Nest-photo quality gate and pluggable ONNX image model
│   ├── correlate.py           Matches nest reports to nest areas and flight corridors
│   ├── ondemand.py            Fetches OpenStreetMap / iNaturalist data in the background
│   ├── osm_import.py          Overpass query, conversion to land cover, wild-bee zones, places
│   ├── bloom_import.py        iNaturalist flowering histograms per plant
│   ├── geodata.py             Demo map data (land cover, wild-bee zones, apiary register) and loader
│   ├── requirements.txt       Server dependencies (onnxruntime optional)
│   ├── tests/                 Test suites (see testing.md)
│   ├── models/                (optional) nest_classifier.onnx + .labels.txt: not included
│   ├── data/                  (created at run time) database, photos, admin key: not in the repo
│   ├── osm_data.json          (created at run time) cached OpenStreetMap data
│   └── bloom_data.json        (created at run time) cached iNaturalist bloom months
│
├── pi/                        Raspberry Pi smart-hive agent
│   ├── beemap_agent.py        Cameras, VespAI detection, heading camera, colony monitor, uploads
│   ├── beemap.example.ini     Commented configuration template (copy to beemap.ini)
│   ├── beemap-agent.service   systemd unit to start the agent on boot
│   ├── requirements.txt       Pi dependencies
│   └── test_agent.py          Two-camera pairing and heading maths
│
├── tools/                     Command-line tools
│   ├── admin.py               City operations: alerts, nests, nest reports, stations, SensoBoxes, health
│   ├── demo.py                Simulated city: hives, nests, hornets (through the real Pi code),
│   │                          SensoBoxes, colony data, nest reports, plantings
│   ├── update_osm_data.py     Manual refresh of OpenStreetMap data (normally automatic)
│   └── update_bloom_data.py   Manual refresh of iNaturalist bloom months (normally automatic)
│
└── web/                       The public map (no build step)
    ├── index.html             Page shell
    ├── spa.js                 Map, layers and filters, panel tabs, place card, reports, live updates
    ├── spa.css                Styles (BeeMap design tokens)
    └── vendor/leaflet/        Leaflet 1.9.4, bundled so the map library works offline
```

## Where to change things

| To change… | Look in |
|---|---|
| Hive spot-check rules or thresholds | `server/ecology.py` (`RADIUS_M`, `APIARIES_*`, `HORNET_*`, `TARGET`, `spot_check`) |
| Bloom profiles per land cover | `server/ecology.py` (`PROFILES`) |
| Plant list | `server/ecology.py` (`PLANTS`) |
| Pollen-type weights for bees | `server/ecology.py` (`POLLEN_BEE_WEIGHT`) |
| Nest-area maths | `server/triangulate.py` |
| Smart-hive site planning | `server/triangulate.py` (planning section) |
| Colony-health thresholds | `server/health.py` |
| Nest-photo thresholds | `server/nest_classifier.py` (`MIN_*`, `AUTO_CONFIRM*`) |
| Report-to-flight-line matching | `server/correlate.py` |
| Fetch and refresh intervals | `server/ondemand.py` |
| OpenStreetMap tags used | `server/osm_import.py` |
| Map layers, filters, panel | `web/spa.js` (`LAYERS`, `PANELS`) |
| Background maps | `web/spa.js` (`BASEMAPS`) |
| Pi behaviour | `pi/beemap_agent.py` (`Config`) and `beemap.ini` |
