# Data sources

| Data | Source | How it gets in | Refresh | Licence / credit |
|---|---|---|---|---|
| Background map (streets, labels) | OpenStreetMap tiles (default), OSM Humanitarian, CARTO with a key | Loaded by the browser | Live | © OpenStreetMap contributors (ODbL); CARTO terms |
| Land cover, hedges, tree rows | OpenStreetMap via the Overpass API | Server, in the background, on first need | Every 30 days | © OpenStreetMap contributors, ODbL |
| Wild-bee zones | OpenStreetMap protected areas (nature reserves, natural monuments, protected landscape features) | Same request | Same | Same |
| Place names | OpenStreetMap `place=*` nodes | Same request | Same | Same |
| Bloom months | iNaturalist API, flowering annotations | Server, in the background, plant by plant | Each plant every 90 days | Data: iNaturalist; only aggregated counts stored |
| Hornet detections, in-hive data | Smart hives (Pi agent) | Device uploads | Live | Project data |
| Weather and pollen | SensoBoxes | Device uploads | Live | Project data |
| Nest reports, plantings | The public | Anonymous submissions | Live | Project data |
| Bee Balance land use | OpenStreetMap via OpenFreeMap vector tiles; city boundary from Nominatim | Data pipeline (`make balance`) | When the pipeline is rerun | © OpenStreetMap contributors, ODbL |
| Public sightings (Asian hornet, European hornet, honey bee) | GBIF, aggregating iNaturalist, naturgucker (NABU) and Observation.org | Data pipeline (`make hornets`) | When rerun | Per record (currently CC BY 4.0 or CC BY-NC 4.0); kept in each record's `license`. The NC records rule out commercial reuse. |
| Weather for flight hours and street heat | Open-Meteo historical archive | Data pipeline | When rerun | Open-Meteo, CC BY 4.0 |
| Real hive sensors and hive weather station | BeeHappy (three OpenSpace hives) and its SenseCAP S2120 station, via the project's Grafana | Data pipeline (`make fetch`, `make beemap`) | When rerun | Organisers' data; raw series kept local |
| Hive camera recordings and labels | Organisers' Nextcloud share | Data pipeline (`make fetch`) | When rerun | Organisers' data; share token from the environment |
| City weather stations | senseBox:home stations in Heilbronn, via an OGC API Features service | Data pipeline | When rerun | openSenseMap data |
| Street heat walks | Heat-Island Summer School dataloggers (1–3 Sep 2026), Nextcloud share | Data pipeline (`make walks`) | When rerun | Summer-school data; GPS tracks kept local |
| **Apiary register** | **Demo data** (`server/geodata.py`) | Seeded on first start | none | Should come from the veterinary office's register |
| Land cover / zones fallback | **Demo shapes** (`server/geodata.py`) | Used until OpenStreetMap data arrives, or offline | none | Hand-placed approximations |

## OpenStreetMap (land cover)

- **Query:** one Overpass request for Heilbronn's bounding box. It asks for `landuse` (orchard, meadow, vineyard, forest, farmland, allotments, grass, residential and other built-up kinds, reservoir, village green, flower beds, nurseries), `natural` (wood, scrub, heath, water, grassland), `leisure` (park, garden, nature reserve), riverbanks, protected areas, `barrier=hedge`, `natural=tree_row` and `place` nodes.
- **Conversion** (`osm_import.py`):
  - split multipolygon outlines are joined into rings, and holes kept;
  - shapes are simplified to about 4 m;
  - areas under 150 m² are dropped;
  - features are drawn coarse first, fine on top.
- **Wild-bee zones:** nature reserves, protect classes 1–4 and 7, and titles like *Naturschutzgebiet*, *Naturdenkmal* or *geschützter Landschaftsbestandteil*. **Landscape protection areas are excluded** by default (they'd cover half the city); `tools/update_osm_data.py --include-landscape` includes them. Protected areas are only a starting point for wild-bee habitat.
- **Robustness:**
  - mirrors are tried in turn (overpass-api.de, lz4, z, overpass.kumi.systems);
  - "200 OK" answers that carry a timeout or error remark count as failures;
  - an answer with fewer than 50 land-cover areas never replaces existing data.
- **Caching:** `server/osm_data.json`. Delete it to fetch again; `BEEMAP_GEODATA=demo` forces the demo shapes.

## iNaturalist (bloom months)

- For each plant: an exact scientific-name lookup, then `GET /v1/observations/histogram` with `term_id=12&term_value_id=13&interval=month_of_year`, for Baden-Württemberg first, then Germany.
- Plants with fewer than 30 flowering observations, and seed mixes, keep the curated months. The app labels which months are observed and which are curated.
- The rate is about one request per second, as the API asks. Results are cached in `server/bloom_data.json`, saved after each plant.

## Data pipeline sources

These are fetched by the offline pipeline (`pipeline/scripts/fetch_data.py` and the step scripts), not by the server. See [pipeline.md](pipeline.md) for the steps and [algorithms.md](algorithms.md#12-data-pipeline-pipeline) for the methods.

- **Hive camera recordings and labels.** Entrance recordings of one OpenSpace hive (Aug–Sep 2026) and the organisers' 32×32 labelled crops, from a Nextcloud share (`BEEMAP_NC_SHARE`). The frame interval isn't documented; 1 s is assumed.
- **BeeHappy hive sensors.** Inside temperature and humidity (Dragino S31 in the hive body), brood-nest probes (D23) and the hives' SenseCAP S2120 weather station, for three hives from June 2025. They're read from an anonymous Grafana instance with the same SQL its panels use, then sent into BeeMap by `make beemap`.
- **senseBox:home stations** in Heilbronn: temperature, humidity, pressure, PM2.5 and PM10, read through an OGC API Features service.
- **Summer-school walks.** Mobile dataloggers carried from the OpenSpace to the Wartberg, along the Neckar and into the centre, 1–3 Sep 2026 (`BEEMAP_WALKS_SHARE`).
- **Open-Meteo** historical weather (archive API) at the hives and in 8 German cities.
- **OpenFreeMap** vector tiles (OpenMapTiles schema) for Bee Balance land use. They hold the same OpenStreetMap data as the Overpass API, but are always reachable. The older 1 km site map still uses Overpass.
- **GBIF** occurrence search for the sightings, plus a facet query for Baden-Württemberg counts by year.

Two land-use models now exist side by side. The server's flower model (800 m, monthly bloom, from Overpass) drives the spot check and the plant planner. The pipeline's Bee Balance (1.5 km and 300 m, a single food weight per land use, from OpenFreeMap) is a second opinion shown next to it.

## Plants

`ecology.PLANTS` lists 28 entries: natives (goat willow, cornelian cherry, blackthorn, lungwort, meadow sage, viper's bugloss, knapweed, ivy and others) and farm options (phacelia, buckwheat, white mustard, sunflower, red clover, sainfoin, field bean, a regional flower strip, orchard trees). Each has curated bloom months, the settings it suits, native status, value for wild bees and honeybees, and a tip. Planting windows are general gardening guidance.

## Demo data (`server/geodata.py`, `tools/demo.py`)

- **Map:** hand-placed shapes for land cover and wild-bee zones, with real district names, plus 7 registered apiaries.
- **The demo city:**
  - 6 smart hives, 1 planned, and 3 hidden nests;
  - simulated hornets that go through the real Pi code;
  - 4 standalone SensoBoxes plus one per smart hive, with a year of readings; their "real" forage deliberately differs from the map in places;
  - 30 days of in-hive data with different colony stories (healthy, under hornet pressure, colony lost, damp);
  - 3 nest reports and 5 plantings.

## Data for real use (not yet connected)

- **Apiary register:** the veterinary office (beekeepers must register their colonies). Needs a data-sharing agreement.
- **Wild-bee habitat:** LUBW biotope maps and local wild-bee surveys.
- **Crops:** farmers' field declarations (flowering crops, likely pesticide exposure).
- **Terrain:** an elevation model for slope, aspect and frost hollows.
- **Ground truth for forage:** hive scales (weight gain = nectar flow) and pollen traps at smart hives.
