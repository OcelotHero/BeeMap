"""Pipeline results on the map (citydata.py, /api/city/*) and colonies per station.

Runs against small hand-made pipeline output files in a temp folder, so it
doesn't need the real pipeline to have run. Calls the endpoint functions
directly (no HTTP test client needed)."""
import asyncio, json, os, sys, tempfile, time
TMP = tempfile.mkdtemp()
OUT = tempfile.mkdtemp()
os.environ.update(BEEMAP_DATA=TMP, BEEMAP_PIPELINE_OUT=OUT, BEEMAP_ADMIN_KEY="secret", BEEMAP_ONLINE="off", BEEMAP_GEODATA="demo")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi import HTTPException  # noqa: E402
import app as server  # noqa: E402


def write(name, text):
    with open(os.path.join(OUT, name), "w") as f:
        f.write(text)


def status_of(fn, *a, **kw):
    try:
        fn(*a, **kw)
        return 200
    except HTTPException as e:
        return e.status_code


# 1. nothing from the pipeline yet: status says so, endpoints answer 404 with a hint
st = server.city_status()
assert not any(d["available"] for d in st["datasets"].values()), st
assert status_of(server.city_balance) == 404
try:
    server.city_balance()
except HTTPException as e:
    assert "make" in e.detail
assert server.city_places() == {"schools": [], "neighbourhoods": []}

# 2. a tiny pipeline output
write("bee_balance_grid.csv", "cell_id,lat,lon,row,col,forage_1500,forage_300,category,dist_to_hive_km,environment,street_heat_c\n"
      "1_1,49.14,9.21,1,1,0.40,0.30,green,0.5,0.7,1.25\n1_2,49.14,9.214,1,2,0.20,0.10,red,0.8,0.6,\n")
write("bee_balance_summary.json", json.dumps({"n_cells": 2, "area_km2": 0.125, "shares": {"green": 0.5, "red": 0.5},
                                              "thresholds": {"green": 0.34, "yellow": 0.25}, "hive": {"forage_1500": 0.2}}))
write("bee_balance_schools.csv", "name,kind,lat,lon,forage_1500,forage_300,category,environment,street_heat_c,cell_id\n"
      "Test-Schule,school,49.14,9.21,0.4,0.3,green,0.7,,1_1\n")
write("bee_balance_neighbourhoods.csv", "name,kind,lat,lon,forage_1500,forage_300,category,environment,street_heat_c,cell_id\n")
write("hornet_sightings.csv", "species,common,gbif_key,date,year,month,lat,lon,uncertainty_m,source,dataset,locality,basis,license,dist_hive_km,precise\n"
      "Vespa velutina,Asian hornet,123,2025-09-01,2025,9,49.15,9.22,30,iNaturalist,x,Heilbronn,HUMAN_OBSERVATION,cc-by,1.2,True\n"
      "Vespa crabro,European hornet,,2024-08-01,2024,8,,,,naturgucker,x,,HUMAN_OBSERVATION,,,False\n")
write("hornet_summary.json", json.dumps({"asian_by_year": {"2025": 1}, "fetched": "2026-10-27"}))
write("heat_walks_grid.csv", "lat,lon,delta_c,n,recordings\n49.1419,9.2186,0.5055,1085,3\n")
write("flight_hours.csv", "year,month,base,humidity,heat_hours\n2025,6,300,280,20\n2025,7,290,250,30\n")
write("city_flight_hours.csv", "city,year,month,base,humidity,heat_hours\nHeilbronn,2025,6,300,280,20\nHamburg,2025,6,200,190,0\n")
write("correlations.csv", "variable,n_hours,pearson_r,pearson_p,spearman_r,spearman_p\nout_temp,6,0.74,0.09,0.886,0.019\n")
write("activity_validation.csv", "hour,activity,minutes,out_temp,out_humidity\n2026-08-12 17:00:00+02:00,312.06,16,33.78,17.2\n")

st = server.city_status()
assert all(d["available"] for d in st["datasets"].values()), st
b = server.city_balance()
assert len(b["cells"]) == 2 and b["cells"][0][4] == "green" and b["cells"][0][5] == 1.25 and b["cells"][1][5] is None, b
assert b["fields"][:5] == ["lat", "lon", "food_1500", "food_300", "category"]
p = server.city_places()
assert p["schools"][0]["name"] == "Test-Schule" and p["neighbourhoods"] == [], p
s = server.city_sightings()
assert len(s["sightings"]) == 1, "records without coordinates are dropped"
assert s["sightings"][0]["url"].endswith("/123") and s["summary"]["asian_by_year"] == {"2025": 1}
assert server.city_heat()["cells"] == [[49.1419, 9.2186, 0.51, 1085]]
f = server.city_flight_hours()
assert [x["city"] for x in f["cities"]] == ["Heilbronn", "Hamburg"] and f["cities"][0]["humidity"] == 280, f
assert server.city_study()["correlations"][0]["spearman_r"] == 0.89

# a pipeline rerun is picked up without a restart
write("heat_walks_grid.csv", "lat,lon,delta_c,n,recordings\n49.1419,9.2186,-0.5,10,1\n49.15,9.22,2.0,20,2\n")
os.utime(os.path.join(OUT, "heat_walks_grid.csv"), (time.time() + 5, time.time() + 5))
assert len(server.city_heat()["cells"]) == 2, "changed file should be re-read"
print("city data: endpoints, missing files and reruns OK")

# 3. one station, several colonies: readings and health per hive
with server.db() as con:
    con.execute("INSERT INTO stations (id,name,lat,lon,token,created) VALUES ('apiary','Apiary',49.14,9.21,'tok',?)", (time.time(),))
    st_row = dict(con.execute("SELECT * FROM stations WHERE id='apiary'").fetchone())
now = time.time()
rows = [{"ts": now - h * 3600, "hive_temp_c": 30 + (h % 3), "hive_humidity": 55, "hive": name}
        for name in ("Hive A", "Hive B") for h in range(48)]
r = asyncio.run(server.post_hive_readings("apiary", {"readings": rows}, st=st_row))
assert r["stored"] == 96, r
asyncio.run(server.post_hive_readings("apiary", {"ts": now, "activity": 12.0}, st=st_row))   # no hive: the station's own
allh = [h for h in asyncio.run(server.get_health_all()) if h["station_id"] == "apiary"]
assert sorted(h["hive"] for h in allh) == ["", "Hive A", "Hive B"], allh
assert {h["name"] for h in allh} == {"Apiary", "Apiary · Hive A", "Apiary · Hive B"}
one = asyncio.run(server.get_health_one("apiary", "Hive B"))
assert one["hive"] == "Hive B" and one["hives"] == ["", "Hive A", "Hive B"], one
try:
    asyncio.run(server.get_health_one("apiary", "nope"))
    raise AssertionError("unknown colony should be a 404")
except HTTPException as e:
    assert e.status_code == 404
print("colonies per station: stored and reported separately OK")
