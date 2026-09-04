#!/usr/bin/env python3
"""
Live KHL data: standings + fixtures/results, from en.khl.ru (the league's own official
site) -- the source Wikipedia itself cites for KHL standings tables. Bot-protected (a
direct fetch was blocked outright, same kind of wall hit on other official sports sites
this session -- confirmed again 2026-09-01 via a direct fetch attempt, still blocked),
so this uses Playwright rather than plain requests.

UPDATE 2026-09-01: fixtures/results were never actually built despite the module
existing since inception -- main() always wrote fixtures:[] and results:[] with a log
line admitting it. Season starts 2026-09-05, so this had to actually get built now, not
just re-verified. Also removed a duplicate `def main()` that silently shadowed the first
(dead code, not a live bug since both had identical standings-fetch logic, but real
enough to be worth cleaning up while in here).

Cannot verify the calendar page's exact HTML structure directly (bot-blocked the same
way as the standings page always was, and unlike the UAE/Arsenal scrapers built earlier
this session, there was no way to inspect the real markup by hand first). Built instead
to anchor on the one thing guaranteed not to change with a CSS/markup refresh: the 22
real team names themselves (already confirmed correct, since they're the same list the
existing hardcoded opening-day fixtures use). Finds every element whose text exactly
matches a known team name, pairs up ones that share a close-enough common ancestor
(same technique used for the UAE Pro League match-card parser), then looks for a score
pattern (finished) or a time pattern (upcoming) within that shared container. If this
finds zero matches on the real page, the diagnostic logging below is deliberately heavy
(mirrors the standings parser's own "diagnostic capture" precedent from 2026-08-17) so
the next run's log says exactly what needs adjusting, rather than another silent empty
result.

Output: data/khl-live.json, matching the same {standings, fixtures, results} shape as
the UAE Pro League scraper.
"""
import json
import os
import re
from datetime import datetime, timedelta, timezone

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

STANDINGS_URL = "https://en.khl.ru/standings/"
CALENDAR_URL = "https://en.khl.ru/calendar/"
OUTPUT_PATH = "data/khl-live.json"

# Confirmed correct (same list the existing hardcoded opening-day fixtures use, which
# were themselves confirmed directly against khl.ru's calendar when first built).
KHL_TEAMS = [
    "CSKA Moscow", "Dinamo Minsk", "Dynamo Moscow", "Lada Togliatti",
    "Lokomotiv Yaroslavl", "HC Sochi", "Severstal Cherepovets", "Shanghai Dragons",
    "SKA Saint Petersburg", "Spartak Moscow", "Torpedo Nizhny Novgorod",
    "Admiral Vladivostok", "Ak Bars Kazan", "Amur Khabarovsk", "Avangard Omsk",
    "Avtomobilist Ekaterinburg", "Barys Astana", "Metallurg Magnitogorsk",
    "Neftekhimik Nizhnekamsk", "Salavat Yulaev Ufa", "Sibir Novosibirsk",
    "Traktor Chelyabinsk",
]
TEAM_SET = set(KHL_TEAMS)

SCORE_RE = re.compile(r"^(\d+)\s*[:\-]\s*(\d+)$")
TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
DATE_RE = re.compile(r"(\d{1,2})\s+(January|February|March|April|May|June|July|August|"
                      r"September|October|November|December)\s*,?\s*(\d{4})?", re.IGNORECASE)
MONTHS = {m: i + 1 for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July", "August",
     "September", "October", "November", "December"])}


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


# Confirmed real column order via diagnostic capture (2026-08-17): the header's
# data-sort attributes list exactly this sequence. Used to zip against each row's stat
# cells rather than guessing positions. Note there is genuinely no goals-against column
# in this table (confirmed, not a parsing gap) -- KHL's compact standings view only
# shows goals-for.
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
        all_cells = row.find_all(["td", "th"])
        stat_cells = all_cells[2:]
        values = [c.get_text(strip=True) for c in stat_cells]
        stats = dict(zip(STAT_COLUMNS, values))

        def safe_int(key):
            v = stats.get(key, "0")
            try:
                return int(v)
            except (ValueError, TypeError):
                return 0

        standings.append({
            "team": team_name,
            "played": safe_int("gp"),
            "won": safe_int("w"),
            "otWon": safe_int("otw"),
            "soWon": safe_int("sow"),
            "soLost": safe_int("sol"),
            "otLost": safe_int("otl"),
            "lost": safe_int("l"),
            "gf": safe_int("gf"),
            "points": safe_int("pts"),
        })
    return standings


def find_ancestor_with_two_teams(el, max_hops=6):
    """Walk up from a team-name element to find the smallest ancestor that also
    contains exactly one OTHER team name (i.e. this looks like a single match card
    with a home team and an away team, not a wider container with many matches)."""
    node = el.parent
    for _ in range(max_hops):
        if node is None:
            return None
        text = node.get_text(" ", strip=True)
        found = [t for t in KHL_TEAMS if t in text]
        if len(found) == 2:
            return node
        if len(found) > 2:
            return None  # too wide, contains multiple matches - not a single card
        node = node.parent
    return None


def parse_calendar(html):
    soup = BeautifulSoup(html, "html.parser")
    # Find every element whose OWN direct text (not descendants') is exactly a known
    # team name - these are the leaf nodes actually naming a team, not a wrapping div.
    team_els = []
    for el in soup.find_all(string=True):
        text = el.strip()
        if text in TEAM_SET:
            team_els.append(el.parent)

    log(f"found {len(team_els)} team-name element(s) on the calendar page")

    matches = []
    seen_pairs = set()
    current_date = None

    for node in soup.find_all(True):
        text = node.get_text(strip=True) if node.name not in ("script", "style") else ""
        date_match = DATE_RE.match(text) if text and len(text) < 40 else None
        if date_match:
            day = int(date_match.group(1))
            month = MONTHS.get(date_match.group(2).capitalize())
            year = int(date_match.group(3)) if date_match.group(3) else 2026
            if month:
                current_date = (year, month, day)
            continue

        if node not in team_els:
            continue
        card = find_ancestor_with_two_teams(node)
        if card is None:
            continue
        card_text = card.get_text(" ", strip=True)
        found_teams = [t for t in KHL_TEAMS if t in card_text]
        if len(found_teams) != 2:
            continue
        pair_key = (id(card),)
        if pair_key in seen_pairs:
            continue
        seen_pairs.add(pair_key)

        # order the two teams by their actual position in the card's text
        positions = sorted(found_teams, key=lambda t: card_text.find(t))
        home, away = positions[0], positions[1]

        score_m = None
        time_m = None
        for cand in card.find_all(string=True):
            s = cand.strip()
            # Check TIME_RE first: "19:00" matches the loose score pattern too (19-0),
            # and a genuine hockey score practically never zero-pads to exactly 2 digits
            # on each side the way a time always does - caught this exact bug in testing
            # before it ever reached a real run (a fixture's kickoff time was getting
            # parsed as a finished-match score).
            if TIME_RE.match(s) and time_m is None:
                time_m = TIME_RE.match(s)
            elif SCORE_RE.match(s) and score_m is None:
                score_m = SCORE_RE.match(s)

        if not current_date:
            continue
        year, month, day = current_date
        # KHL games are played Moscow time; site displays local (Moscow, UTC+3).
        # Tashkent is UTC+5, so add 2 hours.
        if time_m:
            hour, minute = int(time_m.group(1)), int(time_m.group(2))
        else:
            hour, minute = 19, 0  # reasonable estimate if no time found, same as the
            # existing hardcoded opening-day fixtures already used
        moscow_dt = datetime(year, month, day, hour, minute, tzinfo=timezone(timedelta(hours=3)))
        tashkent_dt = moscow_dt.astimezone(timezone(timedelta(hours=5)))
        start = tashkent_dt.strftime("%Y-%m-%dT%H:%M:00+05:00")

        entry = {"home": home, "away": away, "start": start}
        if score_m:
            entry["homeScore"] = int(score_m.group(1))
            entry["awayScore"] = int(score_m.group(2))
            entry["finished"] = True
        matches.append(entry)

    return matches


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
        soup = BeautifulSoup(standings_html, "html.parser")
        tbodies = soup.find_all("tbody")
        log(f"WARNING: 0 standings rows. Found {len(tbodies)} <tbody> element(s) total")

    try:
        calendar_html = fetch_rendered(CALENDAR_URL)
    except Exception as e:
        log(f"calendar fetch failed: {e}")
        calendar_html = ""

    log(f"calendar page: captured {len(calendar_html)} chars")
    all_matches = parse_calendar(calendar_html) if calendar_html else []
    log(f"parsed {len(all_matches)} match(es) from the calendar page")
    if not all_matches and calendar_html:
        soup = BeautifulSoup(calendar_html, "html.parser")
        body_text = soup.get_text(" ", strip=True)
        teams_found_anywhere = [t for t in KHL_TEAMS if t in body_text]
        log(f"WARNING: 0 matches parsed. {len(teams_found_anywhere)}/22 known team "
            f"names appear somewhere in the page text: {teams_found_anywhere[:6]}...")
        log(f"page text sample (first 800 chars): {body_text[:800]!r}")

    fixtures = [m for m in all_matches if not m.get("finished")]
    results = [m for m in all_matches if m.get("finished")]
    results.sort(key=lambda m: m["start"], reverse=True)
    fixtures.sort(key=lambda m: m["start"])

    output = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "standings": standings,
        "fixtures": fixtures,
        "results": results,
    }

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    log(f"wrote {OUTPUT_PATH}: {len(standings)} standings, {len(fixtures)} fixtures, {len(results)} results")


if __name__ == "__main__":
    main()
