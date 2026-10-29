# Hardware: smart hives and SensoBoxes

## Smart hive (Raspberry Pi)

| Part | Purpose | Notes |
|---|---|---|
| Raspberry Pi 4 or 5 | Runs `pi/beemap_agent.py` | The Pi 5 has two camera ports |
| Detection camera | VespAI YOLOv5 at the entrance or bait station | `camera = picamera2:0`, or `opencv:0` (USB), or `file:<video>` for tests |
| Heading camera | Follows the departing hornet (motion only, no AI) | *sky*: fisheye looking up (all directions). *horizon*: looking outward (more precise, but only within its field of view) |
| In-hive sensor | Temperature and humidity | `bme280` (I²C, 0x76/0x77), `sht31` (I²C) or `dht22` (GPIO). Placement matters: above the brood reads very differently from the side. |

### Setup

1. **Register the hive** on the server:
   `python3 tools/admin.py station-add hive20 "Hive 20, Biberach" 49.105 9.150 --heading 40`.
   The heading is the compass direction of the heading camera's image *top* (sky mode) or image *centre* (horizon mode). Measure it carefully once: this calibration limits precision more than the cameras do.
2. **Copy the config:** `pi/beemap.example.ini` → `beemap.ini`. Fill in `server`, `station_id` and `token` (from `python3 tools/admin.py station-token hive20`), and put the VespAI weights at `models/vespai.pt`.
3. **Install the dependencies:** `pip install -r pi/requirements.txt`, plus the VespAI / YOLOv5 dependencies (torch), plus your sensor's library.
4. **Test the connection:** `python3 beemap_agent.py --config beemap.ini --test`.
5. **Start on boot:** copy `beemap-agent.service` to `/etc/systemd/system/`, then `systemctl enable --now beemap-agent`.

`--simulate` runs without cameras or a model, sending fake hornets towards `sim_nest_bearing`.

### How the Pi talks to the server

Plain HTTP(S) requests from the Pi to the server, each with the station's `X-Station-Token`; the server never connects to the Pi.

| What | Request | When |
|---|---|---|
| Hornet visit | `POST /api/stations/{id}/detections`: JSON `meta` plus the snapshot | When a tracked hornet leaves |
| Heartbeat | `POST /api/stations/{id}/heartbeat`: status plus a private live frame; the reply carries the heading calibration | Every `heartbeat_s` (60 s) |
| In-hive readings | `POST /api/stations/{id}/hive` | Every `hive_post_s` (300 s) |

The server turns a visit into a compass bearing, stores it as a pending alert, recomputes the nest areas, and pushes `detection`, `nests` and `lines` over the WebSocket. Every open map shows an "Asian hornet seen at …" notice and redraws. A visit the server doesn't accept (network down, or the station unknown or its token wrong) is queued on the SD card and retried, oldest first, backing off from 10 s to 5 minutes. The event ID stops a retry being counted twice.

### A real Pi during the pitch demo

`pitch.sh` deletes the database on every start, so a real Pi's station and token would be gone and its visits refused. List the Pi in `tools/real_stations.ini` (copy `real_stations.example.ini`; not in git):

```ini
[hornet-pi]
name = HornTrac station, OpenSpace
lat = 49.146
lon = 9.212
heading = 0
token = <the same token as in the Pi's beemap.ini>
```

`tools/demo.py` registers every station listed there with that fixed token, next to the simulated hives, which it leaves unchanged. The real Pi's hornets then arrive live on top of the simulated ones, feed the same nest search, and are **not** auto-confirmed like the older simulated alerts. If `token` is left empty, the server makes one and the demo prints it, but it changes at every start. Set the Pi's `server` to the laptop's address. Anything it queued while refused is sent within a few minutes of registration.

`hornet-pi` is also where `make beemap` puts the OpenSpace colonies' real sensor data (see [pipeline.md](pipeline.md#real-hive-data-into-beemap-make-beemap)). Start `pitch.sh` first, so the demo registers the station with the Pi's token, then run `make beemap`; it finds the station, sends into it, and notices the fresh database by the station's registration time.

### Raspberry Pi 4 with a Camera Module 3

Tested on 28 Oct 2026 (the HornTrac station). Getting VespAI running on this hardware needs:

- **The pinned YOLOv5.** The VespAI weights are a whole pickled model and only load with YOLOv5 at commit `23701eac`. Check that commit out on the Pi and set `yolov5_dir` to it. On torch 2.6 or newer, the agent sets `TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD` itself.
- **`min_confidence = 0.8`.** At 0.5 the model boxes hair, flowers and bottles. It's only reliable on its training view, looking straight down at a plain bait board, and misses close-ups and busy scenes.
- **Camera:** `camera = picamera2:0` with `autofocus = true`. Use `rotate` if the camera is mounted sideways.
- **Speed:** on a Pi 4 CPU the model runs at about 1 frame per second. That's enough to log a hornet at a bait station, but the exit direction is rough; a Pi 5 or a second heading camera does better.
- **Live view:** `stream_port = 8080` shows the camera with its boxes at `http://<pi>:8080/` while aiming. Leave it at 0 outside a trusted network.
- **Python environment:**
  - create it with `python3 -m venv --system-site-packages`, so Picamera2 from apt is visible;
  - install the CPU build of torch with `pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu` (the default ARM build pulls in gigabytes of unused GPU libraries);
  - install OpenCV from apt (`sudo apt install python3-opencv`), so it matches the system numpy.

To try the model on a laptop webcam or a video first, the data pipeline has a stand-alone alarm: `make hornet-setup` then `make hornet-live` in `pipeline/` (see [pipeline.md](pipeline.md)).

### Configuration (`[beemap]` in `beemap.ini`)

| Key | Default | Meaning |
|---|---|---|
| `server`, `station_id`, `token` | (required) | Where to send data, and as which hive |
| `weights`, `target_class`, `min_confidence` | `models/vespai.pt`, `velutina`, 0.6 | VespAI model and the class to track. The example config sets `min_confidence = 0.8`, VespAI's own field setting. |
| `yolov5_dir` | empty | Local checkout of ultralytics/yolov5 at commit `23701eac`; empty fetches the current GitHub version, which can't load the VespAI weights |
| `camera`, `width`, `height`, `fps` | `picamera2`, 1280, 720, 10 | Detection camera |
| `rotate` | 0 | Rotate the detection image clockwise: 0, 90, 180 or 270. Set the heading in the app after rotating. |
| `autofocus` | true | Continuous autofocus on a Camera Module 3 (ignored by cameras without it) |
| `stream_port` | 0 | Above 0: live view with VespAI's boxes at `http://<pi>:<port>/` for aiming the camera. **No login.** |
| `lost_after_s`, `min_track_points`, `max_jump` | 0.6, 4, 0.25 | When a track counts as "left", and tracking limits |
| `heading_camera` | `none` | `picamera2:1`, `opencv:1`, a file, or `none` (single-camera station) |
| `heading_mode`, `heading_hfov`, `heading_flip` | `sky`, 120, false | Heading camera geometry |
| `heading_width`, `heading_height`, `heading_fps` | 1280, 720, 30 | Heading camera resolution |
| `match_window_s` | 3 | How soon after leaving the heading track must start |
| `min_blob_px`, `max_blob_px` | 6, 6000 | Motion blob size for hornets (heading camera) |
| `hive_sensor`, `hive_sensor_addr`, `hive_sensor_pin` | `none`, 0x76, D4 | In-hive sensor |
| `entrance_roi` | `0,0.55,1,1` | Entrance area in the detection image (fractions), for activity counting |
| `bee_blob_min_px`, `bee_blob_max_px` | 20, 3000 | Motion blob size for bees |
| `hive_post_s`, `heartbeat_s` | 300, 60 | Upload intervals |
| `send_live_frame`, `queue_dir` | true, `queue` | Calibration frame with heartbeats; offline queue folder |

### What the Pi sends

- **Detections:** snapshot, detection confidence, track, relative heading and its uncertainty (or the single-camera exit angle), and an event ID so retries aren't counted twice. Queued on disk when offline.
- **Heartbeat** every minute: status (fps, Pi temperature, queue length, heading-camera statistics), plus a frame kept privately on the server. The reply carries the current heading calibration.
- **In-hive readings** every 5 minutes: average entrance activity (bees in view), hive temperature and humidity. Kept in memory and retried if sending fails. A station that watches several colonies can name each reading's `hive` (see [api.md](api.md)); the Pi agent itself reports one colony.

Entrance activity is a **relative** number: each colony is compared only with itself, so camera position doesn't matter as long as it stays put.

## SensoBox

The sensor box measures air pressure, humidity, light/UV and temperature today; a pollen sensor is planned. **The pollen sensor needs to identify pollen types**: total counts are mostly grass, birch and pine and can't show bee food, so BeeMap doesn't use them for the flower map.

1. **Register:** `python3 tools/admin.py box-add sb-boeckingen "Böckingen Rathaus" 49.133 9.183 [--station hive2-boeckingen]`. Linking a box to a smart hive makes it that hive's outside weather.
2. **Get the token:** `python3 tools/admin.py box-token sb-boeckingen`.
3. **Post readings,** one at a time or in batches (every field optional):

```bash
curl -X POST http://SERVER:8000/api/sensoboxes/sb-boeckingen/readings \
  -H "X-Box-Token: <token>" -H "Content-Type: application/json" \
  -d '{"readings": [{"ts": "2026-05-04T12:00:00Z", "temp_c": 17.2, "humidity": 61,
       "pressure_hpa": 1016.4, "lux": 42000, "uv_index": 4.1,
       "pollen_total": 210.5, "pollen": {"salix": 12, "rosaceae": 30, "poaceae": 160}}]}'
```

`pollen` is in grains/m³ by type, using lower-case family or genus names (`salix`, `tilia`, `rosaceae`, `brassicaceae`, `poaceae`, `betula`, …).
