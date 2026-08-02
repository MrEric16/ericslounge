#!/usr/bin/env python3
"""
Scrapes Arsenal match results (score + scorers + minutes) from goal.com.

Source: https://www.goal.com/en/team/arsenal/{TEAM_ID} (Overview page) --
this compact page reliably shows the most recent finished match plus
upcoming fixtures with direct links, confirmed via manual inspection on
2026-08-02 (correctly showed "Girona 1-4 Arsenal FT" same day it happened).

For each newly-finished match found there, fetches the match detail page
and extracts score, HT score, and scorer name + minute for both teams from
the scorer summary line at the top of the page (e.g. "K. Havertz 16'C.
Tzolis 30'...").

KNOWN LIMITATION: goal.com's scorer summary line does not include assists.
Only scorer name + minute are captured. Assists are not available from
this source and are not represented in the output.

Output: data/arsenal-results.json
"""
import json
import re
import sys
from datetime import datetime

import requests
from bs4 import BeautifulSoup

TEAM_URL = "https://www.goal.com/en/team/arsenal/4dsgumo7d4zupm2ugsvm4zm4d"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
}
OUTPUT_PATH = "data/arsenal-results.json"


def log(msg):
    print(f"[arsenal-scraper] {msg}", flush=True)


def load_existing():
    try:
        with open(OUTPUT_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"results": []}


def find_recent_match_links(html):
    """Finds match links on the team overview page tagged FT (finished)."""
    soup = BeautifulSoup(html, "html.parser")
    links = soup.find_all("a", href=re.compile(r"/en/match/"))
    finished = []
    for a in links:
        text = a.get_text(" ", strip=True)
        href = a.get("href", "")
        if not href.startswith("http"):
            href = f"https://www.goal.com{href}"
        if re.search(r"\bFT\b", text):
            finished.append(href)
    # dedupe, preserve order
    seen = set()
    out = []
    for href in finished:
        if href in seen:
            continue
        seen.add(href)
        out.append(href)
    return out


def parse_match_page(html, url):
    soup = BeautifulSoup(html, "html.parser")
    full_text = soup.get_text("\n", strip=True)

    # Team names: appear near the top as "Girona v Arsenal | Goal.com" in <title>,
    # more reliably from canonical URL slug "girona-vs-arsenal"
    slug_match = re.search(r"/en/match/([a-z0-9\-]+)/", url)
    home_name, away_name = None, None
    if slug_match:
        parts = slug_match.group(1).split("-vs-")
        if len(parts) == 2:
            home_name = parts[0].replace("-", " ").title()
            away_name = parts[1].replace("-", " ").title()

    # Score + HT: look for "(HT X-Y) (FT A-B)" pattern
    ht_ft = re.search(r"\(HT\s*(\d+)\s*-\s*(\d+)\)\s*\(FT\s*(\d+)\s*-\s*(\d+)\)", full_text)
    if not ht_ft:
        log(f"COULD NOT FIND HT/FT SCORE PATTERN for {url}")
        return None
    ht_home, ht_away, ft_home, ft_away = map(int, ht_ft.groups())

    # Date: "1 Aug 2026" style, appears before the score
    date_match = re.search(r"(\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4})", full_text)
    match_date = None
    if date_match:
        for fmt in ("%d %B %Y", "%d %b %Y"):
            try:
                match_date = datetime.strptime(date_match.group(1), fmt).strftime("%Y-%m-%d")
                break
            except ValueError:
                continue

    # Scorer summary block: sits between the FT/score area and the "(HT ...)" marker.
    # Format observed: "A. Martinez 50'" (one block) then
    # "K. Havertz 16'C. Tzolis 30'M. Dowman 53'G. Jesus 55'" (second block, concatenated).
    # Both blocks together, in page order, sit just before "(HT".
    pre_ht = full_text.split("(HT")[0]
    # take the tail of pre_ht -- last ~500 chars should contain both scorer blocks
    tail = pre_ht[-600:]
    scorer_pattern = re.compile(r"([A-Z]\.\s?[A-Za-z\-']+)\s*(\d+)'")
    all_scorers = scorer_pattern.findall(tail)

    # Use the Key Events section to determine which scorers belong to which team,
    # via the score-before-name (away) vs name-before-score (home) signal confirmed
    # by manual inspection: home team's goals show "PlayerName X-Y", away team's
    # goals show "X-Y PlayerName".
    key_events_section = full_text.split("Key Events", 1)
    home_scorers_from_events = set()
    away_scorers_from_events = set()
    if len(key_events_section) > 1:
        ke_text = key_events_section[1][:2000]
        for m in re.finditer(r"(\d+\s*-\s*\d+)\s*\n?\s*([A-Z]\.\s?[A-Za-z\-']+)", ke_text):
            away_scorers_from_events.add(m.group(2).strip())
        for m in re.finditer(r"([A-Z]\.\s?[A-Za-z\-']+)\s*\n?\s*(\d+\s*-\s*\d+)", ke_text):
            home_scorers_from_events.add(m.group(1).strip())

    home_goals, away_goals = [], []
    for name, minute in all_scorers:
        name = name.strip()
        entry = {"scorer": name, "minute": int(minute)}
        if name in away_scorers_from_events:
            away_goals.append(entry)
        elif name in home_scorers_from_events:
            home_goals.append(entry)
        else:
            log(f"WARNING: could not assign scorer '{name}' to home or away for {url}")

    return {
        "url": url,
        "date": match_date,
        "home": home_name,
        "away": away_name,
        "htHome": ht_home, "htAway": ht_away,
        "ftHome": ft_home, "ftAway": ft_away,
        "homeGoals": home_goals,
        "awayGoals": away_goals,
    }


def main():
    log("Run started.")
    existing = load_existing()
    existing_urls = {r["url"] for r in existing["results"]}

    try:
        r = requests.get(TEAM_URL, headers=HEADERS, timeout=20)
        r.raise_for_status()
    except Exception as e:
        log(f"FAILED to fetch team overview page: {e}")
        sys.exit(1)

    with open("scripts/debug-goal-com-overview.html", "w", encoding="utf-8") as f:
        f.write(r.text)

    finished_links = find_recent_match_links(r.text)
    log(f"Found {len(finished_links)} finished-match links on overview page")

    new_count = 0
    for url in finished_links:
        if url in existing_urls:
            continue
        try:
            mr = requests.get(url, headers=HEADERS, timeout=20)
            mr.raise_for_status()
        except Exception as e:
            log(f"FAILED to fetch match page {url}: {e}")
            continue
        with open("scripts/debug-goal-com-match.html", "w", encoding="utf-8") as f:
            f.write(mr.text)
        parsed = parse_match_page(mr.text, url)
        if parsed:
            existing["results"].append(parsed)
            existing_urls.add(url)
            new_count += 1
            log(f"Added result: {parsed['home']} {parsed['ftHome']}-{parsed['ftAway']} {parsed['away']}")
        else:
            log(f"Could not parse match page: {url}")

    log(f"New results added this run: {new_count}")
    existing["generatedAt"] = datetime.utcnow().isoformat() + "Z"
    # keep most recent 20
    existing["results"] = existing["results"][-20:]

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)
    log(f"Wrote {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
