# Privacy and security

BeeMap is a public website without user accounts. These rules decide what is public.

## What the public map shows and doesn't show

| Data | Public | Kept private (admin only) |
|---|---|---|
| Smart hives | Name, position, online status, hornet visits | Beekeeper name, device token, live camera frames |
| Registered apiaries | Position to ~100 m (3 decimal places), colony count | Owner, exact position |
| Hornet alerts | Snapshot, time, flight direction, confirmed or not | none |
| Nest reports | Position to ~100 m, details, status, how confirmed, match to flight lines | Exact position, free-text note, photo, model scores |
| Plantings | Position, area, plant | none (anonymous; no names collected) |
| Colony health | none | Everything (`tools/admin.py health`): it's the beekeepers' data |
| City data from the data pipeline | Bee Balance squares, schools and neighbourhoods; street heat on 100 m cells; flight hours; the hive camera study's summary numbers | Raw hive sensor series, the walkers' GPS tracks (both kept out of the public repo) |
| Public sightings (GBIF) | Records as published on GBIF, with source and licence | none: they're already public |

## Photos

- **Nest-report photos** are re-encoded on upload: metadata is stripped (phones store the GPS position, time and device model), the image is rotated upright and shrunk to at most 1600 px, and files that aren't images or are over 10 MB are refused.
- They're stored outside the public `/images` folder.
- **Detection snapshots** come from the hive cameras and are public, since they show hornets at a bait station. Place cameras so they don't capture people.

## Authentication

| Who | How | Scope |
|---|---|---|
| Public | none | Read the public API; add plantings and nest reports |
| Smart hive | `X-Station-Token`, a random token per station | Its own heartbeats, detections and in-hive readings |
| SensoBox | `X-Box-Token`, a random token per box | Its own readings |
| City staff | `X-Admin-Key`, generated on first start into `server/data/admin_key.txt` (or `BEEMAP_ADMIN_KEY`) | Everything else, via `tools/admin.py` |

Tokens and keys are compared in constant time. The website never asks for the admin key; all admin work goes through the command-line tool on the server.

## Abuse protection

- **Anonymous submissions** are limited to 20 per 10 minutes per address (in memory). They can still be spammed on a public deployment; add a captcha or proxy-level limits for a real launch.
- **Nest reports can't fake a nest area:** reports only feed the camera-based nest search once the city confirms them. Automatic photo confirmation needs a confident model *and* good photo quality.
- **Uploads:** at most 20,000 readings per request, photos at most 10 MB, and coordinates outside Heilbronn are refused for nest reports.

## Data pipeline and the public repository

The repository is public, so the pipeline commits only small, shareable results:

- **Not in git:** the organisers' raw hive series (`output/hourly.csv`, `output/hive_health.csv`), the walkers' GPS tracks (`output/heat_walks_points.csv`) and all raw downloads (`pipeline/data/`).
- **Street heat** is published only as 100 m cell averages with at least 20 readings, which don't trace an individual route.
- **Share tokens** for the Nextcloud downloads come from the environment (`BEEMAP_NC_SHARE`, `BEEMAP_WALKS_SHARE`), never from the code.
- **Station camera frames** (`server/data/live/`) and the rest of `server/data/` are ignored by git.
- **The real colonies' health** stays private like any other: `make beemap` uploads through the device API, and health is admin-only.

## Pi live view

`stream_port` on the Pi agent serves the camera with VespAI's boxes over plain HTTP **without a login**, to anyone on the network. It's off by default. Use it only while aiming the camera on a trusted network, and switch it off afterwards.

## For a real deployment

- **Serve over HTTPS** (also required for phone geolocation and the compass).
- **GDPR:** nest-report photos may show private property or people. Set a retention period and a privacy notice; colony health data needs beekeepers' consent.
- **Store the admin key** in a secrets manager rather than a file. Rotate device tokens by re-registering.
