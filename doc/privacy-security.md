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

## For a real deployment

- **Serve over HTTPS** (also required for phone geolocation and the compass).
- **GDPR:** nest-report photos may show private property or people. Set a retention period and a privacy notice; colony health data needs beekeepers' consent.
- **Store the admin key** in a secrets manager rather than a file. Rotate device tokens by re-registering.
