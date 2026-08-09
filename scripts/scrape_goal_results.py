#!/usr/bin/env python3
"""
Scrapes match results (score + scorers + minutes) from goal.com for all
major leagues tracked by the site, plus Arsenal specifically (to catch
pre-season friendlies, which aren't part of any league fixtures page).

Source pattern: https://www.goal.com/en/{league-slug}/fixtures-results/{id}
This page defaults to showing the current/most recent matchweek
server-rendered -- exactly what's needed to detect newly-finished matches,
no historical archive browsing required.

For each match tagged FT found on a league page, fetches the match detail
page and extracts score, HT score, and scorer name + minute for both teams
from the scorer summary line (e.g. "K. Havertz 16'C. Tzolis 30'...").

KNOWN LIMITATION: goal.com's scorer summary line does not include assists.
Only scorer name + minute are captured.

Output: data/goal-results.json (single shared pool across all leagues +
Arsenal friendlies; matched client-side by date + team names regardless
of which scrape produced them).
"""
import json
import re
import sys
from datetime import datetime, timedelta

import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
}
OUTPUT_PATH = "data/goal-results.json"

# Arsenal team page -- covers pre-season friendlies not in any league's fixtures page.
ARSENAL_URL = "https://www.goal.com/en/team/arsenal/4dsgumo7d4zupm2ugsvm4zm4d"

# Major leagues tracked by the site (football only -- KHL is ice hockey, not
# covered by goal.com at all; Uzbek/UAE leagues have uncertain goal.com
# coverage and are left out of this pass, can be added later if confirmed).
LEAGUES = {
    "PL": "https://www.goal.com/en/premier-league/fixtures-results/2kwbbcootiqqgmrzs6o5inle5",
    "PD": "https://www.goal.com/en/primera-divisi%C3%B3n/fixtures-results/34pl8szyvrbwcmfkuocjm3r6t",
    "BL1": "https://www.goal.com/en/bundesliga/fixtures-results/6by3h89i2eykc341oz7lv1ddd",
    "SA": "https://www.goal.com/en/serie-a/fixtures-results/1r097lpxe0xn03ihb7wi98kao",
    "FL1": "https://www.goal.com/en/ligue-1/fixtures-results/dm5ka0os1e3dxcp3vh05kmp33",
    "CL": "https://www.goal.com/en/uefa-champions-league/fixtures-results/4oogyu6o156iphvdvphwpck10",
}


def log(msg):
    print(f"[goal-scraper] {msg}", flush=True)


def load_existing():
    try:
        with open(OUTPUT_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"results": []}


def find_recent_match_links(html):
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

    slug_match = re.search(r"/en/match/([a-z0-9\-]+)/", url)
    home_name, away_name = None, None
    if slug_match:
        parts = slug_match.group(1).split("-vs-")
        if len(parts) == 2:
            home_name = parts[0].replace("-", " ").title()
            away_name = parts[1].replace("-", " ").title()

    ht_ft = re.search(r"\(HT\s*(\d+)\s*-\s*(\d+)\)\s*\(FT\s*(\d+)\s*-\s*(\d+)\)", full_text)
    if not ht_ft:
        log(f"COULD NOT FIND HT/FT SCORE PATTERN for {url}")
        return None
    ht_home, ht_away, ft_home, ft_away = map(int, ht_ft.groups())

    date_match = re.search(r"(\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4})", full_text)
    match_date = None
    if date_match:
        for fmt in ("%d %B %Y", "%d %b %Y"):
            try:
                match_date = datetime.strptime(date_match.group(1), fmt).strftime("%Y-%m-%d")
                break
            except ValueError:
                continue

    pre_ht = full_text.split("(HT")[0]
    tail = pre_ht[-600:]
    scorer_pattern = re.compile(r"([A-Z]\.\s?[A-Za-z\-']+)\s*(\d+)'")
    all_scorers = scorer_pattern.findall(tail)

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

    # If the scoreline shows goals happened but scorer parsing came back completely empty
    # on a side, that's a parse failure, not a real 0-0-style result -- and it happened for
    # real once already (Arsenal 2-3 Dortmund shipped with "No goals" under both teams
    # despite 5 goals being scored). Logging it loudly here means a future occurrence shows
    # up in the run log immediately instead of only being noticed when someone taps the
    # popup and sees something that contradicts the score right above it.
    if ft_home > 0 and not home_goals:
        log(f"WARNING: {home_name} scored {ft_home} but 0 scorers were parsed for {url}")
    if ft_away > 0 and not away_goals:
        log(f"WARNING: {away_name} scored {ft_away} but 0 scorers were parsed for {url}")

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


def process_source(source_url, existing_urls, results_out, debug_tag):
    try:
        r = requests.get(source_url, headers=HEADERS, timeout=20)
        r.raise_for_status()
    except Exception as e:
        log(f"FAILED to fetch {source_url}: {e}")
        return 0

    with open(f"scripts/debug-goal-{debug_tag}.html", "w", encoding="utf-8") as f:
        f.write(r.text)

    finished_links = find_recent_match_links(r.text)
    log(f"[{debug_tag}] Found {len(finished_links)} finished-match links")

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
        parsed = parse_match_page(mr.text, url)
        if parsed:
            if parsed.get("date"):
                match_dt = datetime.strptime(parsed["date"], "%Y-%m-%d")
                if match_dt < datetime.utcnow() - timedelta(days=45):
                    log(f"[{debug_tag}] Skipping stale match (older than 45 days): "
                        f"{parsed['home']} vs {parsed['away']} on {parsed['date']}")
                    existing_urls.add(url)  # don't retry it every run
                    continue
            parsed["competition"] = debug_tag
            results_out.append(parsed)
            existing_urls.add(url)
            new_count += 1
            log(f"[{debug_tag}] Added: {parsed['home']} {parsed['ftHome']}-{parsed['ftAway']} {parsed['away']}")
        else:
            log(f"[{debug_tag}] Could not parse: {url}")
    return new_count


def main():
    log("Run started.")
    existing = load_existing()
    existing_urls = {r["url"] for r in existing["results"]}
    total_new = 0

    total_new += process_source(ARSENAL_URL, existing_urls, existing["results"], "arsenal-friendlies")

    for code, url in LEAGUES.items():
        total_new += process_source(url, existing_urls, existing["results"], code)

    log(f"TOTAL new results added this run: {total_new}")
    existing["generatedAt"] = datetime.utcnow().isoformat() + "Z"
    existing["results"] = existing["results"][-300:]  # keep a healthy rolling window

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)
    log(f"Wrote {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
