#!/usr/bin/env python3
"""
Fetches current weather for Tashkent + the full world-cities pool from
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

WHY THE FULL CITY LIST, NOT JUST 2 RANDOM ONES (2026-08, second pass): the
original client-side version picked 2 random world cities fresh on EVERY
page load, specifically so repeat visitors would see different cities each
time -- a deliberate, explicit design goal ("get kids to subconsciously learn
a bit about geography"). The first version of this server-side rewrite only
fetched 2 random cities per scheduled run, which meant every visitor saw the
SAME two cities for that whole run's duration (up to 30 min) -- refreshing
did nothing, since it was just re-reading the same static file. Fetching the
WHOLE pool here and letting the client pick 2 random ones from it on every
render (see loadWeather() in index.html) restores the original per-refresh
rotation while keeping every actual network call server-side.

Open-Meteo was chosen for the SERVER-SIDE fetch specifically because a GitHub
Actions runner's network isn't subject to whatever was affecting that one
device -- their infrastructure and documented current= parameter are fine to
use from a normal, unrestricted connection.

API CALL BUDGET: ~187 sequential calls per run (Tashkent + 186 world cities).
Open-Meteo's free tier allows 10,000 calls/day. Running this every 30 min
(48x/day) would burn ~8,976 calls/day -- too close to the ceiling, especially
allowing room for manual re-triggers. The schedule (see
.github/workflows/weather-fetch.yml) runs every 2 hours instead (12x/day,
~2,244 calls/day), which is comfortable headroom -- weather doesn't change
fast enough for 2-hour-old data to matter for what is fundamentally a fun
homepage feature, not a forecasting tool.
"""
import json
import os
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

# Full pool, one entry per country roughly -- same list the client used to hold and pick 2
# from randomly on every page load. Kept here in full now that the fetch happens
# server-side; the client still does its own random pick from whatever's in the output file.
WORLD_CITIES = [
{"city": "Kabul", "country": "Afghanistan", "lat": 34.5553, "lon": 69.2075},
    {"city": "Tirana", "country": "Albania", "lat": 41.3275, "lon": 19.8187},
    {"city": "Algiers", "country": "Algeria", "lat": 36.7538, "lon": 3.0588},
    {"city": "Andorra la Vella", "country": "Andorra", "lat": 42.5063, "lon": 1.5218},
    {"city": "Luanda", "country": "Angola", "lat": -8.8390, "lon": 13.2894},
    {"city": "Buenos Aires", "country": "Argentina", "lat": -34.6037, "lon": -58.3816},
    {"city": "Yerevan", "country": "Armenia", "lat": 40.1792, "lon": 44.4991},
    {"city": "Canberra", "country": "Australia", "lat": -35.2809, "lon": 149.1300},
    {"city": "Vienna", "country": "Austria", "lat": 48.2082, "lon": 16.3738},
    {"city": "Baku", "country": "Azerbaijan", "lat": 40.4093, "lon": 49.8671},
    {"city": "Nassau", "country": "The Bahamas", "lat": 25.0343, "lon": -77.3963},
    {"city": "Manama", "country": "Bahrain", "lat": 26.2285, "lon": 50.5860},
    {"city": "Dhaka", "country": "Bangladesh", "lat": 23.8103, "lon": 90.4125},
    {"city": "Minsk", "country": "Belarus", "lat": 53.9006, "lon": 27.5590},
    {"city": "Brussels", "country": "Belgium", "lat": 50.8503, "lon": 4.3517},
    {"city": "Belmopan", "country": "Belize", "lat": 17.2510, "lon": -88.7590},
    {"city": "Porto-Novo", "country": "Benin", "lat": 6.4969, "lon": 2.6289},
    {"city": "Thimphu", "country": "Bhutan", "lat": 27.4728, "lon": 89.6390},
    {"city": "La Paz", "country": "Bolivia", "lat": -16.5000, "lon": -68.1500},
    {"city": "Sarajevo", "country": "Bosnia and Herzegovina", "lat": 43.8563, "lon": 18.4131},
    {"city": "Gaborone", "country": "Botswana", "lat": -24.6282, "lon": 25.9231},
    {"city": "Brasilia", "country": "Brazil", "lat": -15.8267, "lon": -47.9218},
    {"city": "Bandar Seri Begawan", "country": "Brunei", "lat": 4.9031, "lon": 114.9398},
    {"city": "Sofia", "country": "Bulgaria", "lat": 42.6977, "lon": 23.3219},
    {"city": "Ouagadougou", "country": "Burkina Faso", "lat": 12.3714, "lon": -1.5197},
    {"city": "Gitega", "country": "Burundi", "lat": -3.4271, "lon": 29.9246},
    {"city": "Phnom Penh", "country": "Cambodia", "lat": 11.5564, "lon": 104.9282},
    {"city": "Yaounde", "country": "Cameroon", "lat": 3.8480, "lon": 11.5021},
    {"city": "Ottawa", "country": "Canada", "lat": 45.4215, "lon": -75.6972},
    {"city": "Praia", "country": "Cabo Verde", "lat": 14.9330, "lon": -23.5133},
    {"city": "Bangui", "country": "Central African Republic", "lat": 4.3947, "lon": 18.5582},
    {"city": "N'Djamena", "country": "Chad", "lat": 12.1348, "lon": 15.0557},
    {"city": "Santiago", "country": "Chile", "lat": -33.4489, "lon": -70.6693},
    {"city": "Beijing", "country": "China", "lat": 39.9042, "lon": 116.4074},
    {"city": "Bogota", "country": "Colombia", "lat": 4.7110, "lon": -74.0721},
    {"city": "Moroni", "country": "Comoros", "lat": -11.7172, "lon": 43.2473},
    {"city": "Kinshasa", "country": "DR Congo", "lat": -4.4419, "lon": 15.2663},
    {"city": "Brazzaville", "country": "Congo", "lat": -4.2634, "lon": 15.2429},
    {"city": "San Jose", "country": "Costa Rica", "lat": 9.9281, "lon": -84.0907},
    {"city": "Zagreb", "country": "Croatia", "lat": 45.8150, "lon": 15.9819},
    {"city": "Havana", "country": "Cuba", "lat": 23.1136, "lon": -82.3666},
    {"city": "Nicosia", "country": "Cyprus", "lat": 35.1856, "lon": 33.3823},
    {"city": "Prague", "country": "Czechia", "lat": 50.0755, "lon": 14.4378},
    {"city": "Copenhagen", "country": "Denmark", "lat": 55.6761, "lon": 12.5683},
    {"city": "Djibouti City", "country": "Djibouti", "lat": 11.5721, "lon": 43.1456},
    {"city": "Roseau", "country": "Dominica", "lat": 15.3092, "lon": -61.3794},
    {"city": "Santo Domingo", "country": "Dominican Republic", "lat": 18.4861, "lon": -69.9312},
    {"city": "Quito", "country": "Ecuador", "lat": -0.1807, "lon": -78.4678},
    {"city": "Cairo", "country": "Egypt", "lat": 30.0444, "lon": 31.2357},
    {"city": "San Salvador", "country": "El Salvador", "lat": 13.6929, "lon": -89.2182},
    {"city": "Malabo", "country": "Equatorial Guinea", "lat": 3.7523, "lon": 8.7742},
    {"city": "Asmara", "country": "Eritrea", "lat": 15.3229, "lon": 38.9251},
    {"city": "Tallinn", "country": "Estonia", "lat": 59.4370, "lon": 24.7536},
    {"city": "Mbabane", "country": "Eswatini", "lat": -26.3054, "lon": 31.1367},
    {"city": "Addis Ababa", "country": "Ethiopia", "lat": 9.0301, "lon": 38.7407},
    {"city": "Suva", "country": "Fiji", "lat": -18.1416, "lon": 178.4419},
    {"city": "Helsinki", "country": "Finland", "lat": 60.1699, "lon": 24.9384},
    {"city": "Paris", "country": "France", "lat": 48.8566, "lon": 2.3522},
    {"city": "Libreville", "country": "Gabon", "lat": 0.4162, "lon": 9.4673},
    {"city": "Banjul", "country": "The Gambia", "lat": 13.4549, "lon": -16.5790},
    {"city": "Tbilisi", "country": "Georgia", "lat": 41.7151, "lon": 44.8271},
    {"city": "Berlin", "country": "Germany", "lat": 52.5200, "lon": 13.4050},
    {"city": "Accra", "country": "Ghana", "lat": 5.6037, "lon": -0.1870},
    {"city": "Athens", "country": "Greece", "lat": 37.9838, "lon": 23.7275},
    {"city": "St. George's", "country": "Grenada", "lat": 12.0561, "lon": -61.7488},
    {"city": "Guatemala City", "country": "Guatemala", "lat": 14.6349, "lon": -90.5069},
    {"city": "Conakry", "country": "Guinea", "lat": 9.6412, "lon": -13.5784},
    {"city": "Bissau", "country": "Guinea-Bissau", "lat": 11.8636, "lon": -15.5977},
    {"city": "Georgetown", "country": "Guyana", "lat": 6.8013, "lon": -58.1551},
    {"city": "Port-au-Prince", "country": "Haiti", "lat": 18.5944, "lon": -72.3074},
    {"city": "Tegucigalpa", "country": "Honduras", "lat": 14.0723, "lon": -87.1921},
    {"city": "Budapest", "country": "Hungary", "lat": 47.4979, "lon": 19.0402},
    {"city": "Reykjavik", "country": "Iceland", "lat": 64.1466, "lon": -21.9426},
    {"city": "New Delhi", "country": "India", "lat": 28.6139, "lon": 77.2090},
    {"city": "Jakarta", "country": "Indonesia", "lat": -6.2088, "lon": 106.8456},
    {"city": "Tehran", "country": "Iran", "lat": 35.6892, "lon": 51.3890},
    {"city": "Baghdad", "country": "Iraq", "lat": 33.3152, "lon": 44.3661},
    {"city": "Dublin", "country": "Ireland", "lat": 53.3498, "lon": -6.2603},
    {"city": "Jerusalem", "country": "Israel", "lat": 31.7683, "lon": 35.2137},
    {"city": "Rome", "country": "Italy", "lat": 41.9028, "lon": 12.4964},
    {"city": "Kingston", "country": "Jamaica", "lat": 17.9712, "lon": -76.7936},
    {"city": "Tokyo", "country": "Japan", "lat": 35.6762, "lon": 139.6503},
    {"city": "Amman", "country": "Jordan", "lat": 31.9454, "lon": 35.9284},
    {"city": "Astana", "country": "Kazakhstan", "lat": 51.1694, "lon": 71.4491},
    {"city": "Nairobi", "country": "Kenya", "lat": -1.2921, "lon": 36.8219},
    {"city": "Tarawa", "country": "Kiribati", "lat": 1.3382, "lon": 173.0176},
    {"city": "Pyongyang", "country": "North Korea", "lat": 39.0392, "lon": 125.7625},
    {"city": "Seoul", "country": "South Korea", "lat": 37.5665, "lon": 126.9780},
    {"city": "Kuwait City", "country": "Kuwait", "lat": 29.3759, "lon": 47.9774},
    {"city": "Bishkek", "country": "Kyrgyzstan", "lat": 42.8746, "lon": 74.5698},
    {"city": "Vientiane", "country": "Laos", "lat": 17.9757, "lon": 102.6331},
    {"city": "Riga", "country": "Latvia", "lat": 56.9496, "lon": 24.1052},
    {"city": "Beirut", "country": "Lebanon", "lat": 33.8938, "lon": 35.5018},
    {"city": "Maseru", "country": "Lesotho", "lat": -29.3151, "lon": 27.4869},
    {"city": "Monrovia", "country": "Liberia", "lat": 6.2907, "lon": -10.7605},
    {"city": "Tripoli", "country": "Libya", "lat": 32.8872, "lon": 13.1913},
    {"city": "Vaduz", "country": "Liechtenstein", "lat": 47.1410, "lon": 9.5209},
    {"city": "Vilnius", "country": "Lithuania", "lat": 54.6872, "lon": 25.2797},
    {"city": "Luxembourg City", "country": "Luxembourg", "lat": 49.6116, "lon": 6.1319},
    {"city": "Antananarivo", "country": "Madagascar", "lat": -18.8792, "lon": 47.5079},
    {"city": "Lilongwe", "country": "Malawi", "lat": -13.9626, "lon": 33.7741},
    {"city": "Kuala Lumpur", "country": "Malaysia", "lat": 3.1390, "lon": 101.6869},
    {"city": "Male", "country": "Maldives", "lat": 4.1755, "lon": 73.5093},
    {"city": "Bamako", "country": "Mali", "lat": 12.6392, "lon": -8.0029},
    {"city": "Valletta", "country": "Malta", "lat": 35.8989, "lon": 14.5146},
    {"city": "Majuro", "country": "Marshall Islands", "lat": 7.1164, "lon": 171.1858},
    {"city": "Nouakchott", "country": "Mauritania", "lat": 18.0735, "lon": -15.9582},
    {"city": "Port Louis", "country": "Mauritius", "lat": -20.1609, "lon": 57.5012},
    {"city": "Mexico City", "country": "Mexico", "lat": 19.4326, "lon": -99.1332},
    {"city": "Palikir", "country": "Micronesia", "lat": 6.9248, "lon": 158.1611},
    {"city": "Chisinau", "country": "Moldova", "lat": 47.0105, "lon": 28.8638},
    {"city": "Monaco", "country": "Monaco", "lat": 43.7384, "lon": 7.4246},
    {"city": "Ulaanbaatar", "country": "Mongolia", "lat": 47.8864, "lon": 106.9057},
    {"city": "Podgorica", "country": "Montenegro", "lat": 42.4304, "lon": 19.2594},
    {"city": "Rabat", "country": "Morocco", "lat": 34.0209, "lon": -6.8416},
    {"city": "Maputo", "country": "Mozambique", "lat": -25.9692, "lon": 32.5732},
    {"city": "Naypyidaw", "country": "Myanmar", "lat": 19.7633, "lon": 96.0785},
    {"city": "Windhoek", "country": "Namibia", "lat": -22.5609, "lon": 17.0658},
    {"city": "Kathmandu", "country": "Nepal", "lat": 27.7172, "lon": 85.3240},
    {"city": "Amsterdam", "country": "Netherlands", "lat": 52.3676, "lon": 4.9041},
    {"city": "Wellington", "country": "New Zealand", "lat": -41.2865, "lon": 174.7762},
    {"city": "Managua", "country": "Nicaragua", "lat": 12.1150, "lon": -86.2362},
    {"city": "Niamey", "country": "Niger", "lat": 13.5127, "lon": 2.1128},
    {"city": "Abuja", "country": "Nigeria", "lat": 9.0765, "lon": 7.3986},
    {"city": "Skopje", "country": "North Macedonia", "lat": 41.9981, "lon": 21.4254},
    {"city": "Oslo", "country": "Norway", "lat": 59.9139, "lon": 10.7522},
    {"city": "Muscat", "country": "Oman", "lat": 23.5859, "lon": 58.4059},
    {"city": "Islamabad", "country": "Pakistan", "lat": 33.6844, "lon": 73.0479},
    {"city": "Melekeok", "country": "Palau", "lat": 7.5006, "lon": 134.6242},
    {"city": "Panama City", "country": "Panama", "lat": 8.9824, "lon": -79.5199},
    {"city": "Port Moresby", "country": "Papua New Guinea", "lat": -9.4438, "lon": 147.1803},
    {"city": "Asuncion", "country": "Paraguay", "lat": -25.2637, "lon": -57.5759},
    {"city": "Lima", "country": "Peru", "lat": -12.0464, "lon": -77.0428},
    {"city": "Manila", "country": "Philippines", "lat": 14.5995, "lon": 120.9842},
    {"city": "Warsaw", "country": "Poland", "lat": 52.2297, "lon": 21.0122},
    {"city": "Lisbon", "country": "Portugal", "lat": 38.7223, "lon": -9.1393},
    {"city": "Doha", "country": "Qatar", "lat": 25.2854, "lon": 51.5310},
    {"city": "Bucharest", "country": "Romania", "lat": 44.4268, "lon": 26.1025},
    {"city": "Moscow", "country": "Russia", "lat": 55.7558, "lon": 37.6173},
    {"city": "Kigali", "country": "Rwanda", "lat": -1.9403, "lon": 29.8739},
    {"city": "Apia", "country": "Samoa", "lat": -13.8506, "lon": -171.7513},
    {"city": "San Marino", "country": "San Marino", "lat": 43.9424, "lon": 12.4578},
    {"city": "Sao Tome", "country": "Sao Tome and Principe", "lat": 0.3302, "lon": 6.7333},
    {"city": "Riyadh", "country": "Saudi Arabia", "lat": 24.7136, "lon": 46.6753},
    {"city": "Dakar", "country": "Senegal", "lat": 14.7167, "lon": -17.4677},
    {"city": "Belgrade", "country": "Serbia", "lat": 44.7866, "lon": 20.4489},
    {"city": "Victoria", "country": "Seychelles", "lat": -4.6191, "lon": 55.4513},
    {"city": "Freetown", "country": "Sierra Leone", "lat": 8.4657, "lon": -13.2317},
    {"city": "Singapore", "country": "Singapore", "lat": 1.3521, "lon": 103.8198},
    {"city": "Bratislava", "country": "Slovakia", "lat": 48.1486, "lon": 17.1077},
    {"city": "Ljubljana", "country": "Slovenia", "lat": 46.0569, "lon": 14.5058},
    {"city": "Honiara", "country": "Solomon Islands", "lat": -9.4280, "lon": 159.9498},
    {"city": "Mogadishu", "country": "Somalia", "lat": 2.0469, "lon": 45.3182},
    {"city": "Pretoria", "country": "South Africa", "lat": -25.7479, "lon": 28.2293},
    {"city": "Juba", "country": "South Sudan", "lat": 4.8594, "lon": 31.5713},
    {"city": "Madrid", "country": "Spain", "lat": 40.4168, "lon": -3.7038},
    {"city": "Colombo", "country": "Sri Lanka", "lat": 6.9271, "lon": 79.8612},
    {"city": "Khartoum", "country": "Sudan", "lat": 15.5007, "lon": 32.5599},
    {"city": "Paramaribo", "country": "Suriname", "lat": 5.8520, "lon": -55.2038},
    {"city": "Stockholm", "country": "Sweden", "lat": 59.3293, "lon": 18.0686},
    {"city": "Bern", "country": "Switzerland", "lat": 46.9480, "lon": 7.4474},
    {"city": "Damascus", "country": "Syria", "lat": 33.5138, "lon": 36.2765},
    {"city": "Dushanbe", "country": "Tajikistan", "lat": 38.5598, "lon": 68.7870},
    {"city": "Dodoma", "country": "Tanzania", "lat": -6.1630, "lon": 35.7516},
    {"city": "Bangkok", "country": "Thailand", "lat": 13.7563, "lon": 100.5018},
    {"city": "Dili", "country": "Timor-Leste", "lat": -8.5569, "lon": 125.5603},
    {"city": "Lome", "country": "Togo", "lat": 6.1725, "lon": 1.2314},
    {"city": "Nuku'alofa", "country": "Tonga", "lat": -21.1789, "lon": -175.1982},
    {"city": "Port of Spain", "country": "Trinidad and Tobago", "lat": 10.6549, "lon": -61.5019},
    {"city": "Tunis", "country": "Tunisia", "lat": 36.8065, "lon": 10.1815},
    {"city": "Ankara", "country": "Turkey", "lat": 39.9334, "lon": 32.8597},
    {"city": "Ashgabat", "country": "Turkmenistan", "lat": 37.9601, "lon": 58.3261},
    {"city": "Funafuti", "country": "Tuvalu", "lat": -8.5211, "lon": 179.1983},
    {"city": "Kampala", "country": "Uganda", "lat": 0.3476, "lon": 32.5825},
    {"city": "Kyiv", "country": "Ukraine", "lat": 50.4501, "lon": 30.5234},
    {"city": "Abu Dhabi", "country": "UAE", "lat": 24.4539, "lon": 54.3773},
    {"city": "London", "country": "United Kingdom", "lat": 51.5074, "lon": -0.1278},
    {"city": "Washington, D.C.", "country": "United States", "lat": 38.9072, "lon": -77.0369},
    {"city": "Montevideo", "country": "Uruguay", "lat": -34.9011, "lon": -56.1645},
    {"city": "Port Vila", "country": "Vanuatu", "lat": -17.7333, "lon": 168.3273},
    {"city": "Vatican City", "country": "Vatican City", "lat": 41.9029, "lon": 12.4534},
    {"city": "Caracas", "country": "Venezuela", "lat": 10.4806, "lon": -66.9036},
    {"city": "Hanoi", "country": "Vietnam", "lat": 21.0278, "lon": 105.8342},
    {"city": "Sana'a", "country": "Yemen", "lat": 15.3694, "lon": 44.1910},
    {"city": "Lusaka", "country": "Zambia", "lat": -15.3875, "lon": 28.3228},
    {"city": "Harare", "country": "Zimbabwe", "lat": -17.8252, "lon": 31.0335},
]


def log(msg):
    print(f"[weather-fetch] {msg}", flush=True)


def fetch_one(loc):
    url = (
        f"https://api.open-meteo.com/v1/forecast?latitude={loc['lat']}&longitude={loc['lon']}"
        f"&current=temperature_2m,wind_speed_10m,weather_code&wind_speed_unit=kn"
    )
    r = requests.get(url, timeout=15)
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
    failures = 0

    # Tashkent first and always -- if nothing else in this run succeeds, at least the one
    # city that actually matters for "what's the weather right now" still gets through.
    try:
        output["locations"].append(fetch_one(TASHKENT))
        log("Tashkent: OK")
    except Exception as e:
        failures += 1
        log(f"Tashkent: FAILED ({e}) -- this is the one that actually matters, logging loudly")

    for loc in WORLD_CITIES:
        try:
            output["locations"].append(fetch_one(loc))
        except Exception as e:
            failures += 1
            log(f"{loc['city']}: failed ({e}), skipping")

    log(f"Done: {len(output['locations'])} succeeded, {failures} failed, out of {len(WORLD_CITIES) + 1} total")

    os.makedirs("data", exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    size_kb = os.path.getsize(OUTPUT_PATH) / 1024
    log(f"Wrote {OUTPUT_PATH} ({size_kb:.1f} KB)")


if __name__ == "__main__":
    main()
