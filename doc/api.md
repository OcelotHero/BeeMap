# API reference

Base URL: the server root, e.g. `http://localhost:8000`. JSON unless noted. The interactive OpenAPI docs are at `/docs` while the server runs.

**Authentication**
- **Public** endpoints need nothing.
- **Device** endpoints need `X-Station-Token` (smart hive) or `X-Box-Token` (SensoBox).
- **Admin** endpoints need `X-Admin-Key`. The key is generated into `server/data/admin_key.txt`, or set with `BEEMAP_ADMIN_KEY`.

## Public: reading

| Method and path | Parameters | Returns |
|---|---|---|
| `GET /api/summary` | none | Counts for the overview, whether reports are on, optional CARTO key |
| `GET /api/health` | none | Liveness check `{"ok": true}` |
| `GET /api/stations` | none | Smart hives: name, position, heading, online, planned (no tokens, no owner names) |
| `GET /api/detections` | `limit`, `station_id`, `status` | Hornet alerts with snapshot URL, bearing, uncertainty, status |
| `GET /api/nests` | `include_removed` | Nest areas: centre, 95% ellipse, sources, status, matching `reports`, `located`, `located_at` |
| `GET /api/lines` | none | Flight lines per hive (bearing, uncertainty, `corridor` = direction only) |
| `GET /api/nest-reports` | none | Reported nests: position to ~100 m, details, status, how confirmed, `match` to flight lines (no photos, no notes) |
| `GET /api/planning` | none | Open searches and suggested smart-hive sites |
| `POST /api/planning/evaluate` | `{lat, lon}` | What a smart hive at that spot would add |
| `GET /api/settings` | none | `{reports: bool}` |
| `GET /api/eco/status` | none | Background-fetch status for OpenStreetMap and iNaturalist |
| `GET /api/eco/layers` | none | Wild-bee zones, apiaries (camera hives exact, registered ones to ~100 m), plantings, data source |
| `GET /api/eco/landcover` | `bbox=s,w,n,e` (optional) | Land-cover shapes and hedges/tree rows, only those in view |
| `GET /api/eco/place` | `lat, lon` | Place name; whether hornets are nearby |
| `GET /api/eco/spot` | `lat, lon, colonies` | Hive spot check: verdict, checks, better spot, evidence |
| `GET /api/eco/grid` | `kind=hive\|gap`, `month` | City-wide grid for "good hive spots" or "flower gaps" |
| `GET /api/eco/plants` | `lat, lon, setting, natives_only` | Plant plan: gap months, plants filling them, planting times, goal |
| `GET /api/eco/plant-list` | none | All plants with bloom months, settings and timing |
| `GET /api/sensoboxes` | none | Boxes: latest readings, monthly bee-pollen index, flight weather |
| `GET /api/city/status` | none | Which pipeline datasets exist, when each was last written, and the output folder |
| `GET /api/city/balance` | none | Bee Balance 250 m squares as compact rows `[lat, lon, food_1500, food_300, category, street_heat_c, cell_id]`, plus thresholds, shares and the OpenSpace hive's square |
| `GET /api/city/places` | none | `{schools, neighbourhoods}`, each with its square's food shares and verdict (empty lists when missing) |
| `GET /api/city/sightings` | none | GBIF records (species, date, position, precision, source, licence, GBIF link) and a summary by year |
| `GET /api/city/heat` | none | Street heat, 100 m cells `[lat, lon, delta_c, n]` |
| `GET /api/city/flight-hours` | none | Bee flight hours: Heilbronn by month (2025) and 8 cities ranked |
| `GET /api/city/study` | none | Hive camera study: correlations with weather and the measured hours |
| `GET /images/{file}` | none | Detection snapshots |
| `WS /ws` | none | Live events (see below) |

**City data (`/api/city/*`).** These endpoints serve the results of the offline data pipeline ([pipeline.md](pipeline.md)). A dataset that hasn't been produced answers `404` with a hint to run the pipeline. Files are re-read when they change, so there's no WebSocket event for them.

## Public: contributing (anonymous, rate-limited)

At most 20 submissions per 10 minutes per address.

| Method and path | Body | Notes |
|---|---|---|
| `POST /api/eco/plantings` | `{lat, lon, plant_id, area_m2}` | Counts towards the flower model immediately |
| `POST /api/nest-reports` | form: `lat, lon, location, height, size, note, image` | `location`: tree, building, hedge, ground, other. `height`: reach, 2-5m, above-5m, unknown. `size`: fist, football, bigger, unknown. The photo is re-encoded without metadata (max 10 MB), kept private, and run through the photo check. Returns `403` when reports are switched off. |

## Devices

| Method and path | Auth | Body |
|---|---|---|
| `POST /api/stations/{id}/heartbeat` | station | form: `status` (JSON), optional `image` (stored privately). Returns the station's heading calibration. |
| `POST /api/stations/{id}/detections` | station | form: `meta` (JSON), optional `image` |
| `POST /api/stations/{id}/hive` | station | `{"readings": [{ts, hive_temp_c, hive_humidity, activity, hive}]}`. `hive` (optional, up to 60 characters) names the colony when one station watches several. |
| `POST /api/sensoboxes/{id}/readings` | box | `{"readings": [{ts, temp_c, humidity, pressure_hpa, lux, uv_index, pollen_total, pollen: {type: grains_m3}}]}` |

**Detection `meta` fields**
- `event_id`: a retried upload with the same ID isn't counted twice.
- `ts`, `arrived_ts`, `confidence`, `track` (`[[t, x, y], …]`, image-normalised).
- Two-camera stations: `heading_rel_deg`, `heading_sigma_deg`, `heading_source`.
- Single-camera stations: `exit_angle_img`, `straightness`.

**Several colonies per station.** A station at an apiary can report several colonies by naming each reading's `hive` (for example `Beehive No.2`). Readings without a name belong to the station's only colony, as before. Each named colony gets its own health entry, shown as `Station name · hive`.

Readings may be sent one at a time or in batches of up to 20,000. Every field is optional, and `ts` can be Unix time or ISO 8601. Details are in [hardware.md](hardware.md).

## Admin

| Method and path | Purpose |
|---|---|
| `POST /api/stations` | Register a smart hive (`planned` for a site not yet installed); returns its token |
| `PATCH /api/stations/{id}` | Name, position, heading calibration (past bearings are recomputed) |
| `GET /api/stations/{id}/token` | Device token for the Pi config |
| `DELETE /api/stations/{id}` | Remove a station |
| `POST /api/detections/{id}/review` | `{status: confirmed\|rejected\|pending, species?}` |
| `PATCH /api/nests/{id}` | `{status: suspected\|assigned\|removed, assigned_to?, place?}`. Removed areas stop counting their departures. |
| `GET /api/admin/nest-reports` | All reports with exact position, note, photo path, photo check and match; flight-line-backed reports first |
| `PATCH /api/admin/nest-reports/{id}` | `{status: pending\|asian_hornet\|other_species\|not_found\|removed, species?}`. Removing a matched report also closes its nest area. |
| `POST /api/admin/nest-reports/recheck` | Run the photo check again on waiting reports |
| `PATCH /api/settings` | `{reports: bool}` |
| `POST /api/sensoboxes` | Register a box (`station_id` links it to a hive); returns its token |
| `GET /api/sensoboxes/{id}/token` | Device token |
| `DELETE /api/sensoboxes/{id}` | Remove a box and its readings |
| `GET /api/admin/health` | Colony health of all smart hives, one entry per colony |
| `GET /api/admin/health/{station_id}` | One colony, with 30-day series. `?hive=<name>` picks a colony at a station with several; otherwise the first is returned. `hives` lists them all; an unknown name gives `404`. |

## WebSocket events (`/ws`)

Messages are `{"type": …, "data": …}`:

| Type | Sent when | Data |
|---|---|---|
| `detection` | A new or reviewed alert | The alert |
| `station`, `station_deleted` | A hive changes, goes online or offline, or is removed | The station, or `{id}` |
| `nests`, `lines` | Nest areas recomputed | All nest areas, or all flight lines |
| `planning_changed` | Hives added or installed, or nest areas changed | none |
| `nest_reports` | A report added or reviewed | none |
| `eco_changed` | Plantings, SensoBox data, or OpenStreetMap / iNaturalist data changed | `{source}` |
| `health_changed` | New in-hive readings | `{station_id}` |
| `settings` | Reports switched on or off | Settings |

Clients may send any text as a keep-alive.
