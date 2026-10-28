PY ?= .venv/bin/python
BEEMAP ?= http://127.0.0.1:8000

.PHONY: help all fetch inspect dataset train count analyse validate walks sitemap balance hornets lights beemap hornet-setup hornet-live

help:
	@echo "BeeMap data pipeline (run from pipeline/). Results land in pipeline/output/, which the BeeMap server reads."
	@echo "make all      - the whole pipeline, then sync into BeeMap"
	@echo "make fetch    - download raw data into ./data (Nextcloud recordings, BeeHappy hive sensors, senseBox, walks)"
	@echo "make inspect  - print a report about the raw data"
	@echo "make dataset  - build the YOLO dataset from the labelled crops"
	@echo "make train    - train the YOLOv8n bee detector (EPOCHS=50 by default)"
	@echo "make count    - run the detector on every recording -> data/activity.csv"
	@echo "make analyse  - merge with sensors: hourly table, correlations, hive health, findings.md"
	@echo "make validate - hive station vs Open-Meteo; rule model vs camera; flight hours; 8 cities"
	@echo "make walks    - street-level heat from the summer-school datalogger walks"
	@echo "make sitemap  - older 1 km site map (Overpass), kept as a check"
	@echo "make balance  - Bee Balance map: 250 m verdict squares, schools, neighbourhoods"
	@echo "make hornets  - public Asian hornet sightings around Heilbronn (GBIF)"
	@echo "make lights   - data for the 3D views (commute, columns, point cloud)"
	@echo "make beemap   - send real hive sensors + hive weather station to the BeeMap server (BEEMAP=$(BEEMAP))"
	@echo "make hornet-setup / hornet-live - VespAI hornet alarm on a webcam or Pi camera"

all: fetch inspect dataset train count analyse validate walks sitemap balance hornets lights beemap

fetch:
	$(PY) scripts/fetch_data.py

inspect:
	$(PY) scripts/inspect_data.py

dataset:
	$(PY) scripts/make_dataset.py

EPOCHS ?= 50
train:
	$(PY) scripts/train.py --epochs $(EPOCHS)

count:
	$(PY) scripts/count_bees.py

analyse:
	$(PY) scripts/analyse.py

validate:
	$(PY) scripts/validate_weather.py
	$(PY) scripts/activity_model.py

walks:
	$(PY) scripts/heat_walks.py

sitemap:
	$(PY) scripts/site_map.py

balance:
	$(PY) scripts/bee_balance.py

hornets:
	$(PY) scripts/hornet_sightings.py

lights:
	$(PY) scripts/make_3d.py

beemap:
	$(PY) scripts/beemap_sync.py --server $(BEEMAP)

hornet-setup:
	bash scripts/hornet_setup.sh

hornet-live:
	$(PY) scripts/hornet_live.py
