#!/usr/bin/env python3
"""
Live UAE Pro League data: standings + current matchday's fixtures and results.

Source: worldfootball.net's all-matches page (not the base competition page -- confirmed
directly, 2026-08-18, that the base page was stuck showing only Matchday 1 even though
Matchday 2, 3, and 4 were already published with real dates on the all-matches page; the
"auto-shows current matchday" assumption this scraper originally relied on turned out not
to hold). The all-matches page has the full published schedule plus the same standings
table, so this fixes the root cause rather than working around it.
Chosen over the official uaeproleague.ae site because the official site's standings/
fixtures are rendered client-side via JS (confirmed directly: fetching it returns a
"Loading..." spinner and "No records" placeholder, not real data).

Replaces the hardcoded SPORTS_LEAGUES entry for UAE Pro League in index.html, which had a
fixtures array with no results array at all -- meaning nothing ever moved from fixtures to
results once matches were played, no matter how much time passed.

Output: data/uae-league-live.json, matching the same {teams, fixtures, results} shape the
client already knows how to render (see uzMatchToRow's row shape in index.html).
"""
import json
import os
import re
from datetime import datetime, timedelta, timezone

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

URL = "https://www.worldfootball.net/competition/co1183/ua-emirates-uae-pro-league/all-matches/"
OUTPUT_PATH = "data/uae-league-live.json"


def log(msg):
    print(f"[uae-league] {msg}", flush=True)


def parse_standings(soup):
    """Each team row is a <tr> containing a team-page link (/teams/teNNNNN/slug/) and,
    per the confirmed real page, 7 numeric/score cells after the team name: M, W, D, L,
    Score (goals-for:goals-against), Diff, Pts. Parsed by walking every row on the page and
    checking for that shape, rather than assuming a specific table CSS class -- the class
    name isn't visible in the fetched/converted version of this page, so guessing at it
    would be exactly the kind of unverified assumption that broke the NGA scraper earlier.
    The fixtures table on the same page also contains team links, but its rows won't have
    7 trailing numeric cells in this shape, so this naturally only matches standings rows."""
    standings = []
    seen_teams = set()

    for row in soup.find_all("tr"):
        team_links = row.find_all("a", href=re.compile(r"/teams/te\d+/"))
        team_link = next((l for l in team_links if l.get_text(strip=True)), None)
        if not team_link:
            continue
        team_name = team_link.get_text(strip=True)
        if team_name in seen_teams:
            continue

        nums = []
        for c in row.find_all("td"):
            t = c.get_text(strip=True)
            if re.match(r"^-?\d+:-?\d+$", t) or re.match(r"^-?\d+$", t):
                nums.append(t)
        if len(nums) < 7:
            continue

        played, won, drawn, lost, score, diff, pts = nums[-7:]
        if ":" in score:
            gf, ga = score.split(":")
        else:
            gf, ga = score, "0"
        try:
            standings.append({
                "team": team_name,
                "played": int(played), "won": int(won), "drawn": int(drawn), "lost": int(lost),
                "gf": int(gf), "ga": int(ga), "gd": int(diff), "points": int(pts),
            })
            seen_teams.add(team_name)
        except ValueError:
            continue
    return standings


MONTHS_NUM = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})")


def parse_matches(soup):
    """Confirmed real structure (2026-08-16): each match is a
    <div data-match_id="..." data-datetime="2026-08-14T14:10:00Z" data-liveticker-status="result" ...>
    containing team names in .team-name-home / .team-name-away and the score as the link
    text inside .match-result (e.g. "2:2", or "-:-" for a match not yet played).
    data-datetime is a real UTC ISO timestamp, which is a much more reliable time source
    than parsing the separately-displayed "16:10" local-time text would be."""
    matches = []
    for div in soup.find_all("div", attrs={"data-match_id": True}):
        home_el = div.select_one(".team-name-home a")
        away_el = div.select_one(".team-name-away a")
        if not home_el or not away_el:
            continue
        home_name = home_el.get_text(strip=True)
        away_name = away_el.get_text(strip=True)
        if not home_name or not away_name:
            continue

        dt_utc = div.get("data-datetime", "")
        m = re.match(r"(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})", dt_utc)
        if not m:
            continue
        year, month, day, hour, minute = m.groups()
        utc_dt = datetime(int(year), int(month), int(day), int(hour), int(minute), tzinfo=timezone.utc)
        tashkent_dt = utc_dt + timedelta(hours=5)
        start = tashkent_dt.strftime("%Y-%m-%dT%H:%M:00+05:00")

        entry = {"home": home_name, "away": away_name, "start": start}

        status = div.get("data-liveticker-status", "")
        score_el = div.select_one(".match-result a")
        score_text = score_el.get_text(strip=True) if score_el else ""
        if status == "result" and re.match(r"^-?\d+:-?\d+$", score_text):
            h, a = score_text.split(":")
            entry["homeScore"] = int(h)
            entry["awayScore"] = int(a)
            entry["finished"] = True

        matches.append(entry)
    return matches


def main():
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")
            page.goto(URL, timeout=45000, wait_until="domcontentloaded")
            page.wait_for_timeout(8000)
            text = page.content()
            if "Just a moment" in text or "challenges.cloudflare.com" in text:
                log("still showing Cloudflare challenge after 8s wait, trying one more wait")
                page.wait_for_timeout(6000)
                text = page.content()
            browser.close()
    except Exception as e:
        log(f"fetch failed: {e}")
        return

    log(f"captured {len(text)} chars")
    soup = BeautifulSoup(text, "html.parser")

    standings = parse_standings(soup)
    log(f"parsed {len(standings)} standings row(s)")
    if not standings:
        log(f"WARNING: 0 standings rows -- page structure may not match what this parser expects, sample: {text[:500]!r}")

    matches = parse_matches(soup)
    log(f"parsed {len(matches)} match row(s)")
    for m in matches:
        log(f"  match: {m}")
    if not matches:
        log("WARNING: 0 match rows -- page structure may have changed since this was last verified")

    fixtures = [m for m in matches if not m.get("finished")]
    results = [m for m in matches if m.get("finished")]
    log(f"{len(fixtures)} fixture(s), {len(results)} result(s)")

    output = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "teams": [s["team"] for s in standings] if standings else None,
        "standings": standings,
        "fixtures": fixtures,
        "results": results,
    }

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    log(f"wrote {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
