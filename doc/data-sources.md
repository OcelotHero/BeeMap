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
