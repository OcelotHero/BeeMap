"""Small helpers shared by all scripts: paths, timezone, loaders.

Keep this file boring.  Anything that two scripts need goes here.
"""
import math
import re
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUTPUT = ROOT / "output"
MODEL_PATH = ROOT / "models" / "bee_yolov8n.pt"   # written by train.py, read by count_bees.py / app.py
TZ = "Europe/Berlin"

# Hives stand at OpenSpace Heilbronn, Edisonstrasse.
HIVE_LAT, HIVE_LON = 49.146, 9.212

# Seconds between two consecutive composite frames of a recording session.
# NOT documented in the data share (frames carry no timestamps).  Every
# "bees per minute" number scales with this value, so confirm it with the
# people who built the recorder and change it here.
FRAME_INTERVAL_S = 1.0

# Columns of the senseBox export we actually use (API name -> our name).
SENSEBOX_COLS = {
    "Temperature": "sb_temp",
    "Rel. Humidity": "sb_humidity",
    "Pressure": "sb_pressure",
    "Finedust PM2.5": "pm25",
    "Finedust PM10": "pm10",
}


def floor_hour(ts):
    """Floor a tz-aware datetime Series to the hour (via UTC, so DST switches don't break)."""
    return ts.dt.tz_convert("UTC").dt.floor("h").dt.tz_convert(TZ)


def haversine_km(lat1, lon1, lat2, lon2):
    """Great-circle distance in km."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi, dlmb = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(a))


def session_start(session_name):
    """Parse a recording session folder name like '20260812-1720' or '20260903_1'.

    Returns a tz-aware Timestamp (Europe/Berlin) or None.  Sessions named
    '<date>_<n>' only carry a date, so they get midnight and need the frame
    timestamps to be placed within the day.
    """
    m = re.match(r"(\d{4})(\d{2})(\d{2})[-_](\d{2})(\d{2})$", session_name)
    if m:
        y, mo, d, h, mi = map(int, m.groups())
        return pd.Timestamp(y, mo, d, h, mi, tz=TZ)
    m = re.match(r"(\d{4})(\d{2})(\d{2})[-_]\d+$", session_name)
    if m:
        y, mo, d = map(int, m.groups())
        return pd.Timestamp(y, mo, d, tz=TZ)
    return None


def load_hive_sensors():
    """Long-format hive sensor table with a tz-aware 'ts' column (Europe/Berlin)."""
    df = pd.read_csv(DATA / "hive_sensors.csv")
    df["ts"] = pd.to_datetime(df["ts"], utc=True, format="ISO8601").dt.tz_convert(TZ)
    # The database stores every uplink twice (identical values); drop the copies.
    return df.drop_duplicates(["hive", "sensor_type", "unit", "ts"]).reset_index(drop=True)


def load_sensebox():
    """All senseBox rows with a tz-aware 'ts' column and the renamed columns."""
    df = pd.read_csv(DATA / "sensebox_all.csv.gz", low_memory=False)
    # createdAt is the measurement time in UTC (no offset in the string).
    df["ts"] = pd.to_datetime(df["createdAt"], utc=True, format="ISO8601").dt.tz_convert(TZ)
    df = df.rename(columns=SENSEBOX_COLS)
    df["dist_km"] = [haversine_km(HIVE_LAT, HIVE_LON, la, lo) for la, lo in zip(df["lat"], df["lon"])]
    return df


def closest_sensebox_station(df, start=None, end=None):
    """Name of the nearest station that has PM2.5 data, optionally within [start, end].

    Pass the activity period so a station that only came online after the
    recordings (e.g. 'GreenCity OpenSpace', 0.7 km, from 2026-09-17) is not
    chosen over one that actually overlaps ('Innenstadt', 0.8 km).
    """
    has_pm = df.dropna(subset=["pm25"])
    if start is not None:
        has_pm = has_pm[has_pm.ts >= start]
    if end is not None:
        has_pm = has_pm[has_pm.ts <= end]
    if has_pm.empty:
        raise ValueError("no senseBox station with PM2.5 data in the requested period")
    return has_pm.groupby("boxName")["dist_km"].first().idxmin()


OPEN_METEO_VARS = ["temperature_2m", "relative_humidity_2m", "precipitation", "wind_speed_10m",
                   "shortwave_radiation", "surface_pressure", "is_day"]
OPEN_METEO_RENAME = {"temperature_2m": "temp", "relative_humidity_2m": "rh", "precipitation": "rain",
                     "wind_speed_10m": "wind", "shortwave_radiation": "radiation",
                     "surface_pressure": "pressure", "is_day": "is_day"}


def open_meteo_hourly(lat, lon, start, end):
    """Hourly Open-Meteo archive weather (ERA5/ICON reanalysis) for a point, cached under data/.

    Columns: ts (tz-aware, Europe/Berlin), temp (°C), rh (%), rain (mm), wind (m/s at 10 m),
    radiation (W/m² shortwave), pressure (hPa surface), is_day (0/1).
    Times are fetched in UTC and converted, so DST never produces duplicate or missing hours.
    """
    import requests  # local import: most scripts never need it

    cache = DATA / f"openmeteo_{lat:.3f}_{lon:.3f}_{start}_{end}.csv"
    if not cache.exists():
        r = requests.get("https://archive-api.open-meteo.com/v1/archive", timeout=120, params={
            "latitude": lat, "longitude": lon, "start_date": start, "end_date": end,
            "hourly": ",".join(OPEN_METEO_VARS), "timezone": "UTC", "wind_speed_unit": "ms"})
        r.raise_for_status()
        df = pd.DataFrame(r.json()["hourly"]).rename(columns=OPEN_METEO_RENAME)
        df["ts"] = pd.to_datetime(df.pop("time"), utc=True)
        df.to_csv(cache, index=False)
    df = pd.read_csv(cache)
    df["ts"] = pd.to_datetime(df["ts"], utc=True).dt.tz_convert(TZ)
    return df
