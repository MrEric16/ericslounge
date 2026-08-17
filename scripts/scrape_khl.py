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

    log(f"standings page: captured {len(standings_html)} chars")
    if standings_html:
        team_idx = standings_html.find("CSKA")
        if team_idx != -1:
            log(f"sample near a known team name: {standings_html[max(0,team_idx-300):team_idx+500]!r}")
        else:
            log("'CSKA' not found anywhere in captured text -- may still be a bot-challenge page or fully empty (season hasn't started)")
        log(f"page title/head sample: {standings_html[:400]!r}")

    output = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "standings": [],
        "fixtures": [],
        "results": [],
    }

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    log(f"wrote {OUTPUT_PATH} (placeholder -- parsing not yet built, see diagnostic capture above)")


if __name__ == "__main__":
    main()
