#!/usr/bin/env python3
"""
Fetches live football data (standings, fixtures, results) from football-data.org
server-side, using the API key from an environment variable (GitHub Actions
secret) -- never exposed to visitors' browsers.

This replaces the previous architecture where every visitor's browser called
football-data.org directly with a hardcoded key sitting in the public page
source -- a real exposure, and one that meant every visitor's request shared
the same rate limit regardless of who was visiting.

IMPORTANT: this also trims the data down server-side (only the soonest
matchday's fixtures, only the last 15 results, only the fields actually used)
instead of shipping entire raw season match lists to every visitor. The first
version of this script did NOT do this and produced a 3.2MB JSON file -- way
too large to ship on every page load. Trimmed output is a small fraction of
that.

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


def trim_match(m):
    home = m.get("homeTeam") or {}
    away = m.get("awayTeam") or {}
    comp = m.get("competition") or {}
    return {
        "utcDate": m.get("utcDate"),
        "status": m.get("status"),
        "matchday": m.get("matchday"),
        "homeTeam": {"id": home.get("id"), "name": home.get("name"), "shortName": home.get("shortName")},
        "awayTeam": {"id": away.get("id"), "name": away.get("name"), "shortName": away.get("shortName")},
        "score": m.get("score"),
        "competition": {"name": comp.get("name")} if comp else None,
        "venue": m.get("venue"),
    }


def process_league_matches(all_matches):
    # REAL BUG FOUND (2026-09-04, confirmed via live data going stale mid-session, not
    # assumption): the old fixtures filter only matched status SCHEDULED/TIMED exactly.
    # The instant a matchday's games move to any OTHER non-finished status (IN_PLAY,
    # POSTPONED, SUSPENDED, or anything else the API might use) without yet being
    # FINISHED, they become invisible to BOTH this filter (not scheduled/timed) AND the
    # results filter below (not finished) - they silently vanish, and "soonest
    # scheduled matchday" jumps straight past them to whatever matchday happens to
    # still have plain-SCHEDULED games, which can be many gameweeks ahead. Caught this
    # exact failure directly: a live run jumped from Matchday 3 to Matchday 8 skipping
    # 3-7 entirely, despite the actual current gameweek (3) not even being finished
    # yet. Rebuilt to be robust regardless of the exact status label: group ALL matches
    # by matchday, find the LOWEST matchday number that still has at least one
    # not-finished match (any status), and show every not-finished match from that one
    # matchday as fixtures - a match's exact status no longer matters for whether its
    # gameweek gets found, only whether it's the specific matches shown as fixtures vs
    # results within that gameweek.
    by_matchday = {}
    for m in all_matches:
        md = m.get("matchday")
        if md is None:
            continue
        by_matchday.setdefault(md, []).append(m)

    fixtures = []
    if by_matchday:
        current_matchday = None
        for md in sorted(by_matchday.keys()):
            matches_this_md = by_matchday[md]
            if any(m.get("status") != "FINISHED" for m in matches_this_md):
                current_matchday = md
                break
        if current_matchday is not None:
            fixtures = sorted(
                [m for m in by_matchday[current_matchday] if m.get("status") != "FINISHED"],
                key=lambda m: m.get("utcDate") or "",
            )

    results = sorted(
        [m for m in all_matches if m.get("status") == "FINISHED"],
        key=lambda m: m.get("utcDate") or "",
        reverse=True,
    )[:15]
    return {
        "fixtures": [trim_match(m) for m in fixtures],
        "results": [trim_match(m) for m in results],
    }


def process_standings(data):
    total_table = next((s for s in data.get("standings", []) if s.get("type") == "TOTAL"), None)
    if not total_table or not total_table.get("table"):
        return None
    # REAL BUG FOUND (confirmed via actual live data, not assumption): checking
    # only endDate wasn't enough -- a season that hasn't started yet can still have
    # a future endDate, so that check alone let through stale not-yet-started
    # leagues (PL showed a fully-completed 38-game table, Arsenal 85pts, despite
    # the 2026-27 season not kicking off until Aug 21). Checking only startDate
    # isn't enough either -- Champions League's stale season started back in Sep
    # 2025 (safely in the past) but ALSO already ended, so startDate alone missed
    # it. The correct check is both together: only trust the standings if today
    # actually falls within [startDate, endDate] -- anything outside that window
    # is either a not-yet-started or already-finished season, and its table data
    # (whatever it shows) is not "current" no matter what the numbers look like.
    season = data.get("season") or {}
    start_date_str = season.get("startDate")
    end_date_str = season.get("endDate")
    now = datetime.utcnow()
    if start_date_str:
        try:
            if datetime.strptime(start_date_str, "%Y-%m-%d") > now:
                return None  # season hasn't started yet
        except ValueError:
            pass
    if end_date_str:
        try:
            if datetime.strptime(end_date_str, "%Y-%m-%d") < now:
                return None  # season has already finished
        except ValueError:
            pass
    if not any(row.get("playedGames", 0) > 0 for row in total_table["table"]):
        return None
    return [
        {
            "name": row["team"].get("shortName") or row["team"].get("name"),
            "pld": row.get("playedGames"), "w": row.get("won"), "d": row.get("draw"), "l": row.get("lost"),
            "gf": row.get("goalsFor"), "ga": row.get("goalsAgainst"), "pts": row.get("points"),
        }
        for row in total_table["table"]
    ]


def main():
    if not API_KEY:
        log("FATAL: FOOTBALL_DATA_API_KEY environment variable not set.")
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
            season_debug = data.get("season") or {}
            log(f"standings[{code}]: raw season field = {season_debug}")
            trimmed = process_standings(data)
            if trimmed:
                output["standings"][code] = trimmed
            log(f"standings[{code}]: OK ({len(trimmed) if trimmed else 0} rows)")
        except Exception as e:
            log(f"standings[{code}]: FAILED ({e})")
        time.sleep(REQUEST_DELAY_SECONDS)

        try:
            data = get(f"{BASE}/competitions/{code}/matches")
            all_matches = data.get("matches", [])
            output["matches"][code] = process_league_matches(all_matches)
            log(f"matches[{code}]: OK (trimmed from {len(all_matches)} to "
                f"{len(output['matches'][code]['fixtures'])} fixtures + "
                f"{len(output['matches'][code]['results'])} results)")
        except Exception as e:
            log(f"matches[{code}]: FAILED ({e})")
        time.sleep(REQUEST_DELAY_SECONDS)

    try:
        data = get(f"{BASE}/teams/{ARSENAL_TEAM_ID}/matches?status=SCHEDULED&limit=1")
        matches = data.get("matches", [])
        output["arsenalNextFixture"] = trim_match(matches[0]) if matches else None
        log("arsenalNextFixture: OK")
    except Exception as e:
        log(f"arsenalNextFixture: FAILED ({e})")
    time.sleep(REQUEST_DELAY_SECONDS)

    try:
        data = get(f"{BASE}/teams/{ARSENAL_TEAM_ID}/matches?status=FINISHED&limit=15")
        output["arsenalFinishedMatches"] = [trim_match(m) for m in data.get("matches", [])]
        log(f"arsenalFinishedMatches: OK ({len(output['arsenalFinishedMatches'])} matches)")
    except Exception as e:
        log(f"arsenalFinishedMatches: FAILED ({e})")

    os.makedirs("data", exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    size_kb = os.path.getsize(OUTPUT_PATH) / 1024
    log(f"Wrote {OUTPUT_PATH} ({size_kb:.1f} KB)")


if __name__ == "__main__":
    main()
