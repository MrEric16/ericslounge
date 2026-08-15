#!/usr/bin/env python3
"""
Fetches current weather for Tashkent + 2 rotating random world cities from
Open-Meteo, server-side, and writes data/weather-live.json for the client to
read same-origin.

WHY THIS EXISTS (2026-08): the weather card used to call a weather API
directly from the visitor's browser. On one real device (tested on both wifi
AND cellular), that consistently hung and timed out -- not for one provider,
but for TWO completely unrelated ones tried in sequence (Open-Meteo, then
wttr.in), both failing identically. Every OTHER piece of live data on this
site (Arsenal results, UZ league, events, football standings) has been
reliable throughout, and all of those are same-origin fetches to this site's
own /data/*.json files -- the only thing that was ever failing was a
third-party cross-origin fetch straight from the browser. Moving weather to
the same server-side-fetch-then-same-origin-read pattern as everything else
sidesteps whatever was blocking direct third-party calls, without needing to
know exactly what that was.

Open-Meteo was chosen for the SERVER-SIDE fetch specifically because a GitHub
Actions runner's network isn't subject to whatever was affecting that one
device -- their infrastructure and documented current= parameter are fine to
use from a normal, unrestricted connection.
"""
import json
import os
import random
from datetime import datetime

import requests

OUTPUT_PATH = "data/weather-live.json"
TASHKENT = {"city": "Tashkent", "country": "Uzbekistan", "lat": 41.2995, "lon": 69.2401}

WEATHER_CODES = {
    0: ["clear-day", "Clear sky"], 1: ["partly-cloudy", "Mostly clear"],
    2: ["partly-cloudy", "Partly cloudy"], 3: ["cloudy", "Overcast"],
    45: ["fog", "Foggy"], 48: ["fog", "Foggy"],
    51: ["drizzle", "Light drizzle"], 53: ["drizzle", "Drizzle"], 55: ["drizzle", "Heavy drizzle"],
    61: ["rain", "Light rain"], 63: ["rain", "Rain"], 65: ["rain", "Heavy rain"],
    71: ["snow", "Light snow"], 73: ["snow", "Snow"], 75: ["snow", "Heavy snow"],
    80: ["rain", "Rain showers"], 81: ["rain", "Rain showers"], 82: ["storm", "Heavy showers"],
    95: ["storm", "Thunderstorm"],
}

# Same list used client-side previously for the "2 random world cities" flourish,
# kept here so the rotation stays varied without needing the full list duplicated
# elsewhere -- trimmed to a representative spread since the script only needs a
# random sample each run, not global coverage.
WORLD_CITIES = [
    {"city": "Kabul", "country": "Afghanistan", "lat": 34.5553, "lon": 69.2075},
    {"city": "Tirana", "country": "Albania", "lat": 41.3275, "lon": 19.8187},
    {"city": "Buenos Aires", "country": "Argentina", "lat": -34.6037, "lon": -58.3816},
    {"city": "Canberra", "country": "Australia", "lat": -35.2809, "lon": 149.1300},
    {"city": "Vienna", "country": "Austria", "lat": 48.2082, "lon": 16.3738},
    {"city": "Baku", "country": "Azerbaijan", "lat": 40.4093, "lon": 49.8671},
    {"city": "Dhaka", "country": "Bangladesh", "lat": 23.8103, "lon": 90.4125},
    {"city": "Brussels", "country": "Belgium", "lat": 50.8503, "lon": 4.3517},
    {"city": "La Paz", "country": "Bolivia", "lat": -16.5000, "lon": -68.1500},
    {"city": "Brasilia", "country": "Brazil", "lat": -15.8267, "lon": -47.9218},
    {"city": "Ottawa", "country": "Canada", "lat": 45.4215, "lon": -75.6972},
    {"city": "Santiago", "country": "Chile", "lat": -33.4489, "lon": -70.6693},
    {"city": "Beijing", "country": "China", "lat": 39.9042, "lon": 116.4074},
    {"city": "Bogota", "country": "Colombia", "lat": 4.7110, "lon": -74.0721},
    {"city": "Cairo", "country": "Egypt", "lat": 30.0444, "lon": 31.2357},
    {"city": "Helsinki", "country": "Finland", "lat": 60.1699, "lon": 24.9384},
    {"city": "Paris", "country": "France", "lat": 48.8566, "lon": 2.3522},
    {"city": "Berlin", "country": "Germany", "lat": 52.5200, "lon": 13.4050},
    {"city": "Athens", "country": "Greece", "lat": 37.9838, "lon": 23.7275},
    {"city": "New Delhi", "country": "India", "lat": 28.6139, "lon": 77.2090},
    {"city": "Jakarta", "country": "Indonesia", "lat": -6.2088, "lon": 106.8456},
    {"city": "Rome", "country": "Italy", "lat": 41.9028, "lon": 12.4964},
    {"city": "Tokyo", "country": "Japan", "lat": 35.6762, "lon": 139.6503},
    {"city": "Astana", "country": "Kazakhstan", "lat": 51.1694, "lon": 71.4491},
    {"city": "Mexico City", "country": "Mexico", "lat": 19.4326, "lon": -99.1332},
    {"city": "Rabat", "country": "Morocco", "lat": 34.0209, "lon": -6.8417},
    {"city": "Amsterdam", "country": "Netherlands", "lat": 52.3676, "lon": 4.9041},
    {"city": "Oslo", "country": "Norway", "lat": 59.9139, "lon": 10.7522},
    {"city": "Islamabad", "country": "Pakistan", "lat": 33.6844, "lon": 73.0479},
    {"city": "Manila", "country": "Philippines", "lat": 14.5995, "lon": 120.9842},
    {"city": "Warsaw", "country": "Poland", "lat": 52.2297, "lon": 21.0122},
    {"city": "Lisbon", "country": "Portugal", "lat": 38.7223, "lon": -9.1393},
    {"city": "Moscow", "country": "Russia", "lat": 55.7558, "lon": 37.6173},
    {"city": "Seoul", "country": "South Korea", "lat": 37.5665, "lon": 126.9780},
    {"city": "Madrid", "country": "Spain", "lat": 40.4168, "lon": -3.7038},
    {"city": "Stockholm", "country": "Sweden", "lat": 59.3293, "lon": 18.0686},
    {"city": "Bangkok", "country": "Thailand", "lat": 13.7563, "lon": 100.5018},
    {"city": "Ankara", "country": "Turkey", "lat": 39.9334, "lon": 32.8597},
    {"city": "London", "country": "United Kingdom", "lat": 51.5074, "lon": -0.1278},
    {"city": "Washington D.C.", "country": "United States", "lat": 38.9072, "lon": -77.0369},
    {"city": "Hanoi", "country": "Vietnam", "lat": 21.0285, "lon": 105.8542},
]


def log(msg):
    print(f"[weather-fetch] {msg}", flush=True)


def fetch_one(loc):
    url = (
        f"https://api.open-meteo.com/v1/forecast?latitude={loc['lat']}&longitude={loc['lon']}"
        f"&current=temperature_2m,wind_speed_10m,weather_code&wind_speed_unit=kn"
    )
    r = requests.get(url, timeout=20)
    r.raise_for_status()
    data = r.json()
    cw = data.get("current")
    if not cw:
        raise ValueError("no current field in response")
    code = cw.get("weather_code")
    icon_key, label = WEATHER_CODES.get(code, ["other", "Current conditions"])
    return {
        "city": loc["city"],
        "country": loc["country"],
        "lat": loc["lat"],
        "lon": loc["lon"],
        "tempC": round(cw["temperature_2m"]),
        "windKn": round(cw["wind_speed_10m"]),
        "iconKey": icon_key,
        "label": label,
    }


def main():
    output = {"generatedAt": datetime.utcnow().isoformat() + "Z", "locations": []}

    try:
        output["locations"].append(fetch_one(TASHKENT))
        log("Tashkent: OK")
    except Exception as e:
        log(f"Tashkent: FAILED ({e}) -- this is the one that actually matters, logging loudly")

    for loc in random.sample(WORLD_CITIES, 2):
        try:
            output["locations"].append(fetch_one(loc))
            log(f"{loc['city']}: OK")
        except Exception as e:
            log(f"{loc['city']}: failed ({e}), skipping")

    os.makedirs("data", exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    log(f"Wrote {OUTPUT_PATH} with {len(output['locations'])} location(s)")


if __name__ == "__main__":
    main()
