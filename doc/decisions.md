# Design decisions

Short records of the main choices and why they were made.

**1. Two cameras per smart hive: AI for detection, motion tracking for direction.**
A camera above a small bait dish sees only centimetres of a departure (±15–50°). A second camera that follows the hornet into the open sky gets ±3–8°. VespAI decides *what* left and *when*; the second camera only has to follow a moving blob that starts at that moment, so it needs no AI. Single-camera stations still work, more roughly.

**2. The Pi sends the direction relative to its camera; the server applies the heading.**
Compass calibration is the weakest link. Keeping the raw relative angle lets a corrected heading recompute every past bearing.

**3. Statistical thresholds instead of fixed distances.**
Nest areas are ellipses with known uncertainty; a long, thin ellipse is precise sideways but not lengthwise. Matching reports, merging candidates and dropping outliers all use distances measured in that uncertainty, plus a stated allowance for pin placement.

**4. Prefer the simpler story.**
If every line behind a candidate nest fits another one, the candidate is dropped. Fewer, honest nest areas beat many confident wrong ones. Ellipses are inflated when lines disagree, and kept at least 100 m.

**5. Planning plans for what isn't known.**
Before a new hive exists, nobody knows where along a corridor the nest is. Suggested sites are scored over all plausible nest positions, never inside the corridor's uncertainty band, and predictions are shown as ranges.

**6. Automation only confirms; it never closes.**
An image model or flight-line match can confirm an Asian hornet nest (≥90%, or ≥75% with a match). A "not an Asian hornet nest" result never closes a report by itself: a missed nest costs far more than a visit.

**7. Anonymous reports don't move the nest search until confirmed.**
Otherwise a few made-up reports could invent a nest. Reports are shown immediately but only count once the city confirms them.

**8. Public map, no login; operations on the command line.**
A single read-only website is simple to share and has no accounts to protect or leak. City staff work through `tools/admin.py` on the server; devices authenticate with their own tokens.

**9. Personal data stays off the public map.**
No beekeeper names; registered apiaries and nest reports are shown to ~100 m; report photos and notes are private and photo metadata is stripped; colony health is private.

**10. External data is fetched by the server, on demand, with fallbacks.**
The flower model, spot check and plant planner run on the server, so the server must have the same land cover the map shows. Data is fetched in the background on first need, cached, refreshed (OpenStreetMap after 30 days, iNaturalist per plant after 90), swapped in whole, and never replaced by a broken answer. Without internet, built-in data is used and the app says so.

**11. Airborne pollen counts only when typed, weighted by value to bees.**
Wind pollen (grass, birch, pine) dominates air samples and isn't bee food. Untyped totals are shown but don't change the flower map. Corrections are partial and fade with distance, so one box can't override the map.

**12. Calibrate to local reality, then label the assumptions.**
Flower targets are set so a typical Heilbronn neighbourhood just meets them in the main season. Every threshold is a named constant, and the app shows whether a result is *measured* or *modelled*.

**13. OpenStreetMap tiles by default, with automatic fallback.**
CARTO now needs a key outside its own platform, and an earlier misspelt CARTO address meant no streets at all. OpenStreetMap's tiles need no key for light use; failures switch to the next background and say so.

**14. No build step for the web page.**
Plain JavaScript and a bundled Leaflet: anyone at the hackathon can read and change it, and it runs offline apart from tiles and fonts.

**15. Heavy analysis runs offline; the server only reads its results.**
The data pipeline needs pandas, scipy and ultralytics, a GPU for training, and gigabytes of raw data. The server needs none of that: it reads small CSV and JSON files with the standard library and re-reads them when they change. The results are committed, so a fresh checkout shows every layer, and the pipeline is rerun only when new data arrives. A missing file disables one layer, never the server.

**16. Real hive data arrives through the device API.**
`make beemap` sends the real BeeHappy sensors and weather station the way a Pi or SensoBox would, instead of writing to the database. The same authentication, validation and colony-health code applies, and the sync works against any BeeMap server, local or deployed.

**17. One station, several colonies.**
An apiary often has one camera or gateway but several hives. Readings carry an optional colony name rather than needing a station per hive; unnamed readings behave as before, so existing Pis need no change.

**18. Two food models, both shown.**
The server's 800 m monthly flower model and the pipeline's 1.5 km / 300 m Bee Balance answer different questions (seasonal gaps at a spot versus a city-wide verdict) from different land-use sources. They're kept separate: the place card shows Bee Balance as a labelled second opinion below the spot check, with its wider range stated.

**19. VespAI at its own field setting.**
The Pi example config uses VespAI's own 0.8 confidence and the YOLOv5 version its weights were saved with. On a real Pi 4 with a Camera Module 3, lower thresholds boxed hair, flowers and bottles, and newer YOLOv5 code couldn't load the weights at all.
