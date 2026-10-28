# Project description

## The problem

Heilbronn's pollinators face three problems at once:

- **Asian hornets** (*Vespa velutina*) hunt honeybees and wild insects. Destroying the nest is the only measure that really helps, but nests sit high in trees and are hard to find.
- **Too many honeybee colonies in the wrong places** compete with wild bees, many of which forage only a few hundred metres from their nest.
- **Flower gaps**: towns often have too few flowers in early spring and late summer, exactly when bees need them.

## What BeeMap does

BeeMap is **one public website**, a full-screen map like Google Maps, open to everyone without a login.

**Asian hornets**
- Smart hives with two cameras detect the hornet (VespAI AI model) and measure **which way it flies home**. Hornets fly back to the nest in a nearly straight line, so lines from several hives cross near the nest. BeeMap draws that **nest area** as an ellipse with its uncertainty.
- Residents can **report a nest** they found, with an optional photo. The city confirms it on site, or automatically when a good photo passes an image model.
- Reports and flight lines **back each other up**: a report inside a nest area is checked first, and once confirmed, the area counts as found.
- The city sees where a **new smart hive** would help most to locate a nest.

**Bees and flowers**
- **Hive spot check**: click anywhere to see whether it's a fair place for honeybee colonies (wild-bee zones, other apiaries, flowers through the season, hornet risk), and where the nearest better spot is.
- **Flower gaps and plant planner**: which months bees go hungry around any point, which plants fill that gap (natives for gardens and balconies, crops and flower strips for farms), and when to plant or sow them. Bloom times come from iNaturalist flowering observations.
- **Plantings**: anyone can add what they planted; it counts towards the neighbourhood's flower goal.

**Sensors and data**
- **SensoBoxes** measure weather (and, when fitted, pollen). Pollen counts correct the flower model near each box; weather gives bee flight hours.
- **Colony health** (private, for the city and beekeepers): in-hive temperature and humidity and entrance activity, compared with the weather, flag colonies that need a look.
- Real land cover, nature reserves and place names are fetched automatically from **OpenStreetMap**.

**Measured city data** (from the offline [data pipeline](pipeline.md))
- **Bee Balance**: every 250 m square of Heilbronn rated hive-ready, plant first or no extra hives by the bee food within 1.5 km, with the food within 300 m for wild bees. Schools and neighbourhoods get their square's verdict.
- **Public sightings**: Asian hornet, European hornet and honey bee records from iNaturalist, naturgucker and Observation.org (via GBIF).
- **Street heat** from the summer-school temperature walks, and **bee flight hours** by month for Heilbronn and 8 other cities.
- **Real hives**: the OpenSpace hives' camera (a trained bee detector) and their BeeHappy sensors, which feed colony health.

## Who it's for

| Who | What they use |
|---|---|
| Residents | The map, flower gaps, plant planner, "I planted something", nest reports |
| Beekeepers | Hive spot check, flower supply, hornet activity near their hives |
| Farmers | Plant planner for fields (crops, cover crops, flower strips) |
| The city | Everything above, plus `tools/admin.py`: confirm alerts and nest reports, send removal teams, register hives and sensors, colony health |

## Status

A **working proof of concept** for a hackathon. Everything runs end to end, and simulated hornets go through the real Raspberry Pi code. But:

- Hornets, SensoBox readings and colony data in the demo are **simulated**. The exceptions are the data pipeline's layers and the three OpenSpace hives, which are real but rest on little data (six hours of usable camera footage, one apiary).
- The **apiary register is demo data**; land cover and nature areas are real when OpenStreetMap is reachable.
- The bundled **nest-photo image model doesn't exist yet**; the code to use one does.
- Thresholds are **rules of thumb** that have not been validated against real colonies or nests.

See [limitations-roadmap.md](limitations-roadmap.md) for the full list.
