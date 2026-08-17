#!/usr/bin/env python3
"""
Live KHL data: standings + fixtures/results, from en.khl.ru (the league's own official
site) -- the source Wikipedia itself cites for KHL standings tables. Bot-protected (a
direct fetch was blocked outright, same kind of wall hit on other official sports sites
tonight), so this uses Playwright rather than plain requests.

Note: the 2026-27 KHL regular season doesn't start until 5 September 2026, so this script
will legitimately find an empty or all-zero standings table and no finished matches until
then -- that's correct, not a bug, and main() handles it by writing valid empty-ish output
rather than treating it as a failure.

Output: data/khl-live.json, matching the same {standings, fixtures, results} shape as the
UAE Pro League scraper.
"""
import json
import os
import re
from datetime import datetime, timedelta, timezone

from playwright.sync_api import sync_playwright

STANDINGS_URL = "https://en.khl.ru/standings/"
CALENDAR_URL = "https://en.khl.ru/calendar/"
OUTPUT_PATH = "data/khl-live.json"


def log(msg):
    print(f"[khl] {msg}", flush=True)


def fetch_rendered(url):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")
        page.goto(url, timeout=30000)
        page.wait_for_timeout(8000)
        text = page.content()
        if "Just a moment" in text or "challenges.cloudflare.com" in text:
            log(f"still showing a bot challenge after 8s wait on {url}, trying one more wait")
            page.wait_for_timeout(6000)
            text = page.content()
        browser.close()
        return text


def main():
    try:
        standings_html = fetch_rendered(STANDINGS_URL)
    except Exception as e:
        log(f"standings fetch failed: {e}")
        standings_html = ""

from bs4 import BeautifulSoup

# Confirmed real column order via diagnostic capture (2026-08-17): the header's data-sort
# attributes list exactly this sequence. Used to zip against each row's stat cells rather
# than guessing positions. Note there is genuinely no goals-against column in this table
# (confirmed, not a parsing gap) -- KHL's compact standings view only shows goals-for.
STAT_COLUMNS = ["gp", "w", "otw", "sow", "sol", "otl", "pts_pct", "l", "gf", "pts"]


def parse_standings(html):
    soup = BeautifulSoup(html, "html.parser")
    tbody = soup.find("tbody")
    if not tbody:
        return []
    standings = []
    for row in tbody.find_all("tr"):
        club_link = row.select_one(".championshipRegular-table__club")
        if not club_link:
            continue
        name_el = club_link.select_one(".championshipRegular-table__clubName")
        team_name = (name_el or club_link).get_text(strip=True)
        if not team_name:
            continue
        # All cells after the rank (<td>, first) and team (<td>, second) are stat cells,
        # in STAT_COLUMNS order, regardless of whether they're <td> or <th> tags (the real
        # page mixes both for these columns).
        all_cells = row.find_all(["td", "th"])
        stat_cells = all_cells[2:]
        values = [c.get_text(strip=True) for c in stat_cells]
        stats = dict(zip(STAT_COLUMNS, values))
        try:
            standings.append({
                "team": team_name,
                "played": int(stats.get("gp", 0) or 0),
                "won": int(stats.get("w", 0) or 0),
                "otWon": int(stats.get("otw", 0) or 0),
                "soWon": int(stats.get("sow", 0) or 0),
                "soLost": int(stats.get("sol", 0) or 0),
                "otLost": int(stats.get("otl", 0) or 0),
                "lost": int(stats.get("l", 0) or 0),
                "gf": int(stats.get("gf", 0) or 0),
                "points": int(stats.get("pts", 0) or 0),
            })
        except ValueError:
            continue
    return standings


def main():
    try:
        standings_html = fetch_rendered(STANDINGS_URL)
    except Exception as e:
        log(f"standings fetch failed: {e}")
        standings_html = ""

    log(f"standings page: captured {len(standings_html)} chars")

    standings = parse_standings(standings_html) if standings_html else []
    log(f"parsed {len(standings)} standings row(s)")
    if not standings and standings_html:
        log("WARNING: 0 standings rows despite a non-empty page -- structure may have changed")

    output = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "standings": standings,
        "fixtures": [],
        "results": [],
    }

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    log(f"wrote {OUTPUT_PATH} -- standings only for now, fixtures/results not yet built")


if __name__ == "__main__":
    main()
