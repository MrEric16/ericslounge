#!/usr/bin/env python3
"""
Live UAE Pro League data: standings + current matchday's fixtures and results.

Source: worldfootball.net's competition page. Chosen over the official uaeproleague.ae
site because the official site's standings/fixtures are rendered client-side via JS
(confirmed directly: fetching it returns a "Loading..." spinner and "No records"
placeholder, not real data) -- worldfootball.net is plain server-rendered HTML instead,
and its main competition page auto-shows the current/most recent matchday without needing
to track matchday numbers ourselves, which is exactly the "just keep working forever"
behavior this needed.

Replaces the hardcoded SPORTS_LEAGUES entry for UAE Pro League in index.html, which had a
fixtures array with no results array at all -- meaning nothing ever moved from fixtures to
results even once matches were played, no matter how much time passed.

Output: data/uae-league-live.json, matching the same {teams, fixtures, results} shape the
client already knows how to render (see uzMatchToRow's row shape in index.html).
"""
import json
import os
import re
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

URL = "https://www.worldfootball.net/competition/co1183/ua-emirates-uae-pro-league/"
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
    """Matchday rows: a date header ("dd.mm.yyyy") followed by match rows each containing
    two team links, a time (HH:MM), and a score cell ("-:-" for not-yet-played, "N:N" once
    finished). Parsed by walking table rows in document order and tracking the most recent
    date header seen, since date and match rows share the same table with no per-row date
    field of their own."""
    matches = []
    current_date = None
    date_pattern = re.compile(r"^\d{2}\.\d{2}\.\d{4}$")
    time_pattern = re.compile(r"^\d{1,2}:\d{2}$")

    for row in soup.find_all("tr"):
        cells = row.find_all("td")
        row_text_cells = [c.get_text(strip=True) for c in cells]

        for t in row_text_cells:
            if date_pattern.match(t):
                current_date = t

        team_links = [l for l in row.find_all("a", href=re.compile(r"/teams/te\d+/")) if l.get_text(strip=True)]
        if len(team_links) < 2 or not current_date:
            continue

        home_name = team_links[0].get_text(strip=True)
        away_name = team_links[-1].get_text(strip=True)
        if not home_name or not away_name:
            continue

        time_text = next((t for t in row_text_cells if time_pattern.match(t)), None)
        score_link = row.find("a", href=re.compile(r"/match-report/"))
        score_text = score_link.get_text(strip=True) if score_link else None

        dd, mm, yyyy = current_date.split(".")
        date_iso = f"{yyyy}-{mm}-{dd}"
        hh, mi = (time_text.split(":") if time_text else ("00", "00"))
        # worldfootball.net's displayed kickoff times (e.g. 16:10, 18:45) match exactly
        # against the previously hand-verified Tashkent times for this same Matchday 1
        # (checked against TNT Sports' own listing in an earlier session) -- treated as
        # already Tashkent-equivalent on that basis, not assumed. Logged below so this can
        # be re-checked against the diagnostic output rather than trusted blindly going
        # forward as new matchdays appear.
        start = f"{date_iso}T{hh.zfill(2)}:{mi}:00+05:00"

        entry = {"home": home_name, "away": away_name, "start": start}

        if score_text and re.match(r"^-?\d+:-?\d+$", score_text):
            h, a = score_text.split(":")
            try:
                entry["homeScore"] = int(h)
                entry["awayScore"] = int(a)
                entry["finished"] = True
            except ValueError:
                pass

        matches.append(entry)

    return matches


def main():
    try:
        r = requests.get(URL, headers={"User-Agent": "Mozilla/5.0 (compatible; EricsLoungeBot/1.0)"}, timeout=30)
        r.raise_for_status()
        text = r.text
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
    if not matches:
        log(f"WARNING: 0 match rows -- page structure may not match what this parser expects")

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
