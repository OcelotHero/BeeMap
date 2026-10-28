# BeeMap data pipeline

Offline analysis for BeeMap: the hive camera, the BeeHappy hive sensors, senseBox
stations, the summer-school heat walks, OpenStreetMap land use and GBIF sightings.
Results land in `output/`, which the BeeMap server reads (`server/citydata.py`).
See the "Data pipeline" section of the main README for what appears where on the map.

```bash
python3.11 -m venv .venv && .venv/bin/pip install -r requirements.txt
make help
make all
```

Moved here from the `bee-commute-tracker` repo on 28 Oct 2026. The Streamlit dashboard
stayed behind; BeeMap's web map replaces it. Things to know before touching the numbers:

- The frame interval of the hive recordings is undocumented (`FRAME_INTERVAL_S` in
  `scripts/common.py`, 1 s assumed). Bees per minute scale with it; rankings don't.
- Only 6 hourly bins of camera footage carry a motion signal, so the weather → activity
  link is a direction check, not a trained model.
- "Detections" are not individual bees: a fast bee can show up as three coloured ghosts.
- The camera watches one entrance; the data share doesn't say which of the three hives.
