#!/usr/bin/env python3
"""
Fetches live football data (standings, fixtures, results) from football-data.org
server-side, using the API key from an environment variable (GitHub Actions
secret) -- never exposed to visitors' browsers.

This replaces the previous architecture where every visitor's browser called
football-data.org directly with a hardcoded key sitting in the public page
source. That was a real exposure: anyone could copy the key from view-source,
and every visitor's own request shared the same rate limit regardless.

Output: data/football-live.json -- the client reads this static file instead
of calling football-data.org at all.
"""
import json
import os
import sys
import time
from datetime import datetime

import requests

API_KEY = os.environ.get("FOOTBALL_DATA_API_KEY")
HEADERS = {"X-Auth-Token": API_KEY} if API_KEY else {}
BASE = "https://api.football-data.org/v4"

LEAGUE_CODES = ["PL", "PD", "BL1", "SA", "FL1", "CL"]
ARSENAL_TEAM_ID = 57
OUTPUT_PATH = "data/football-live.json"

# Free tier is rate-limited to roughly 10 requests/minute -- confirmed for real via
# 429 errors on the first live run of this script (3 of 13 requests got rate-limited
# because they all fired back-to-back with no pacing). 7 seconds between requests
# keeps us safely under that regardless of exact limit, at the cost of this script
# taking a bit longer to run -- worth it for reliability over speed here.
REQUEST_DELAY_SECONDS = 7


def log(msg):
    print(f"[football-fetch] {msg}", flush=True)


def get(url):
    for attempt in range(3):
        r = requests.get(url, headers=HEADERS, timeout=20)
        if r.status_code == 429:
            wait = int(r.headers.get("Retry-After", 15))
            log(f"429 rate-limited, waiting {wait}s before retry (attempt {attempt+1}/3)")
            time.sleep(wait)
            continue
        r.raise_for_status()
        return r.json()
    r.raise_for_status()
    return r.json()


def main():
    if not API_KEY:
        log("FATAL: FOOTBALL_DATA_API_KEY environment variable not set. "
            "Add it as a GitHub Actions repository secret.")
        sys.exit(1)

    output = {
        "generatedAt": datetime.utcnow().isoformat() + "Z",
        "standings": {},
        "matches": {},
        "arsenalNextFixture": None,
        "arsenalFinishedMatches": [],
    }

    for code in LEAGUE_CODES:
        try:
            data = get(f"{BASE}/competitions/{code}/standings")
            output["standings"][code] = data
            log(f"standings[{code}]: OK")
        except Exception as e:
            log(f"standings[{code}]: FAILED ({e})")
        time.sleep(REQUEST_DELAY_SECONDS)

        try:
            data = get(f"{BASE}/competitions/{code}/matches")
            output["matches"][code] = data.get("matches", [])
            log(f"matches[{code}]: OK ({len(output['matches'][code])} matches)")
        except Exception as e:
            log(f"matches[{code}]: FAILED ({e})")
        time.sleep(REQUEST_DELAY_SECONDS)

    try:
        data = get(f"{BASE}/teams/{ARSENAL_TEAM_ID}/matches?status=SCHEDULED&limit=1")
        matches = data.get("matches", [])
        output["arsenalNextFixture"] = matches[0] if matches else None
        log("arsenalNextFixture: OK")
    except Exception as e:
        log(f"arsenalNextFixture: FAILED ({e})")
    time.sleep(REQUEST_DELAY_SECONDS)

    try:
        data = get(f"{BASE}/teams/{ARSENAL_TEAM_ID}/matches?status=FINISHED&limit=15")
        output["arsenalFinishedMatches"] = data.get("matches", [])
        log(f"arsenalFinishedMatches: OK ({len(output['arsenalFinishedMatches'])} matches)")
    except Exception as e:
        log(f"arsenalFinishedMatches: FAILED ({e})")

    os.makedirs("data", exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    log(f"Wrote {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
