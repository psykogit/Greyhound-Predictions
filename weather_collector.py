"""
Weather Collector service for Australian Greyhound Tracks.
Uses Open-Meteo API (free, reliable, real-time meteorological forecasts & observations).
Tracks real-time rainfall, temperature, wind, and conditions to detect wet/heavy tracks.
"""

import httpx
import sqlite3
import math
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List
from db import get_connection

AEDT_TZ = timezone(timedelta(hours=11))

WEATHER_CODE_MAP = {
    0: "Clear",
    1: "Mainly Clear",
    2: "Partly Cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Depositing Rime Fog",
    51: "Light Drizzle",
    53: "Moderate Drizzle",
    55: "Dense Drizzle",
    56: "Light Freezing Drizzle",
    57: "Dense Freezing Drizzle",
    61: "Slight Rain",
    63: "Moderate Rain",
    65: "Heavy Rain",
    66: "Light Freezing Rain",
    67: "Heavy Freezing Rain",
    71: "Slight Snow Fall",
    73: "Moderate Snow Fall",
    75: "Heavy Snow Fall",
    77: "Snow Grains",
    80: "Slight Rain Showers",
    81: "Moderate Rain Showers",
    82: "Violent Rain Showers",
    85: "Slight Snow Showers",
    86: "Heavy Snow Showers",
    95: "Thunderstorm",
    96: "Thunderstorm with Slight Hail",
    99: "Thunderstorm with Heavy Hail"
}

WET_WEATHER_CODES = {51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82, 95, 96, 99}


def weather_code_to_desc(code: Optional[int]) -> str:
    if code is None:
        return "Unknown"
    return WEATHER_CODE_MAP.get(code, "Overcast" if code > 0 else "Clear")


class WeatherCollector:
    BASE_URL = "https://api.open-meteo.com/v1/forecast"

    def __init__(self, client: Optional[httpx.Client] = None):
        self.client = client or httpx.Client(timeout=10.0)

    def fetch_current_weather(self, lat: float, lon: float) -> Optional[Dict[str, Any]]:
        """Fetch current weather observation for track coordinates."""
        params = {
            "latitude": lat,
            "longitude": lon,
            "current": "temperature_2m,apparent_temperature,relative_humidity_2m,precipitation,wind_speed_10m,weather_code"
        }
        try:
            resp = self.client.get(self.BASE_URL, params=params)
            resp.raise_for_status()
            data = resp.json().get("current", {})
            w_code = data.get("weather_code")
            t_val = data.get("temperature_2m")
            app_temp = data.get("apparent_temperature")
            if app_temp is None and t_val is not None:
                rh = data.get("relative_humidity_2m", 50.0)
                ws = data.get("wind_speed_10m", 10.0)
                e = (rh / 100.0) * 6.105 * math.exp((17.27 * t_val) / (237.7 + t_val))
                v = ws / 3.6
                app_temp = round(t_val + 0.33 * e - 0.70 * v - 4.00, 1)
            return {
                "recorded_at": data.get("time"),
                "temperature": t_val,
                "apparent_temperature": app_temp,
                "humidity": data.get("relative_humidity_2m"),
                "precipitation": data.get("precipitation", 0.0),
                "wind_speed": data.get("wind_speed_10m"),
                "weather_code": w_code,
                "condition_desc": weather_code_to_desc(w_code),
                "is_wet": (data.get("precipitation", 0.0) >= 0.2) or (w_code in WET_WEATHER_CODES)
            }
        except Exception as e:
            print(f"[WeatherCollector] Error fetching weather for ({lat}, {lon}): {e}")
            return None

    def record_track_weather(self, track_name: str, meeting_id: Optional[int] = None) -> Optional[int]:
        """Fetch and record current weather for a specific track into the database."""
        conn = get_connection()
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Find track coordinates by exact or fuzzy match
        cursor.execute("SELECT latitude, longitude, name FROM tracks WHERE name LIKE ? OR ? LIKE ('%' || name || '%') LIMIT 1",
                       (f"%{track_name}%", track_name))
        row = cursor.fetchone()
        if not row or not row["latitude"]:
            print(f"[WeatherCollector] No coordinates configured for track: {track_name}")
            conn.close()
            return None

        weather = self.fetch_current_weather(row["latitude"], row["longitude"])
        if not weather:
            conn.close()
            return None

        cursor.execute("""
        INSERT INTO weather_records (meeting_id, track_name, recorded_at, temperature, apparent_temperature, humidity, precipitation, wind_speed, condition_desc)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            meeting_id,
            track_name,
            weather["recorded_at"],
            weather["temperature"],
            weather.get("apparent_temperature"),
            weather["humidity"],
            weather["precipitation"],
            weather["wind_speed"],
            weather["condition_desc"]
        ))
        conn.commit()
        record_id = cursor.lastrowid
        conn.close()
        return record_id

    def get_latest_track_weather(self, track_name: str) -> Optional[Dict[str, Any]]:
        """Retrieve the most recent weather observation for a track."""
        conn = get_connection()
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, track_name, recorded_at, temperature, apparent_temperature, humidity, precipitation, wind_speed, condition_desc
            FROM weather_records
            WHERE track_name LIKE ? OR ? LIKE ('%' || track_name || '%')
            ORDER BY id DESC LIMIT 1
        """, (f"%{track_name}%", track_name))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

    def ensure_track_weather(self, track_name: str, max_age_minutes: int = 20) -> Optional[Dict[str, Any]]:
        """
        Check if we have a fresh weather reading (within max_age_minutes).
        If missing or stale, fetch immediately from Open-Meteo.
        """
        latest = self.get_latest_track_weather(track_name)
        now_utc = datetime.now(timezone.utc)

        is_stale = True
        if latest and latest.get("recorded_at"):
            try:
                rec_dt = datetime.fromisoformat(latest["recorded_at"]).replace(tzinfo=timezone.utc)
                age_minutes = (now_utc - rec_dt).total_seconds() / 60.0
                if age_minutes <= max_age_minutes:
                    is_stale = False
            except Exception:
                is_stale = True

        if is_stale:
            rec_id = self.record_track_weather(track_name)
            if rec_id:
                latest = self.get_latest_track_weather(track_name)

        return latest

    def sync_imminent_race_weather(self, within_minutes: int = 35) -> List[str]:
        """
        Finds all tracks hosting races jumping in the next `within_minutes`
        (or jumping right now), and updates weather if older than 15 minutes.
        Returns list of updated tracks.
        """
        conn = get_connection()
        conn.row_factory = sqlite3.Row
        c = conn.cursor()

        now_local = datetime.now(AEDT_TZ)
        today_str = now_local.strftime("%Y-%m-%d")
        yesterday_str = (now_local - timedelta(days=1)).strftime("%Y-%m-%d")

        # Get all distinct active tracks for today's and yesterday's late races
        c.execute("""
            SELECT DISTINCT m.track_name, r.start_time, m.meeting_date
            FROM races r
            JOIN meetings m ON r.meeting_id = m.id
            WHERE m.meeting_date IN (?, ?)
        """, (today_str, yesterday_str))
        rows = c.fetchall()
        conn.close()

        updated_tracks = []
        tracks_to_check = set()

        for r in rows:
            track = r["track_name"]
            st_str = r["start_time"]
            m_date = r["meeting_date"]
            if not st_str:
                if m_date == today_str:
                    tracks_to_check.add(track)
                continue

            # Parse start time (e.g. '14:25' or '14:25:00')
            try:
                parts = st_str.strip().split(":")
                race_hour = int(parts[0])
                race_minute = int(parts[1])
                d_parts = list(map(int, str(m_date).split("-")))
                race_dt = datetime(d_parts[0], d_parts[1], d_parts[2], race_hour, race_minute, tzinfo=AEDT_TZ)
                if race_hour < 8:
                    race_dt += timedelta(days=1)
                diff_minutes = (race_dt - now_local).total_seconds() / 60.0
                # If race is within [-10m, +within_minutes]
                if -10 <= diff_minutes <= within_minutes:
                    tracks_to_check.add(track)
            except Exception:
                if m_date == today_str:
                    tracks_to_check.add(track)

        for track in tracks_to_check:
            # For imminent races, ensure weather is not older than 15 minutes
            latest = self.ensure_track_weather(track, max_age_minutes=15)
            if latest:
                updated_tracks.append(track)

        return updated_tracks

    def sync_all_today_tracks(self) -> int:
        """Fetch and record fresh weather for all tracks active today."""
        conn = get_connection()
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        now_local = datetime.now(AEDT_TZ)
        today_str = now_local.strftime("%Y-%m-%d")

        c.execute("SELECT DISTINCT track_name FROM meetings WHERE meeting_date = ?", (today_str,))
        today_tracks = [r["track_name"] for r in c.fetchall()]

        # If no meetings today, fallback to tracks in tracks table
        if not today_tracks:
            c.execute("SELECT name FROM tracks")
            today_tracks = [r["name"] for r in c.fetchall()]

        conn.close()

        updated_count = 0
        for track in today_tracks:
            res = self.record_track_weather(track)
            if res:
                updated_count += 1

        return updated_count


if __name__ == "__main__":
    wc = WeatherCollector()
    print("Testing Warragul...")
    res = wc.record_track_weather("Warragul")
    print(f"Recorded Warragul: {res}")
    latest = wc.get_latest_track_weather("Warragul")
    print("Latest Warragul:", latest)
