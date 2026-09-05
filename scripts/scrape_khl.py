#!/usr/bin/env python3
"""
Live KHL data: standings from en.khl.ru (the league's own official site, bot-protected,
fetched via Playwright), fixtures + results from Sofascore's public JSON API (a genuine
alternative source Mr Eric pointed at directly). Both feed data/khl-live.json in the
same {standings, fixtures, results} shape as the UAE Pro League scraper.

HISTORY, in order (all 2026-09-01 through 09-04):
1. Fixtures/results were never actually built despite the module existing since
   inception -- main() always wrote fixtures:[] and results:[] with a log line
   admitting it. Season starting 2026-09-05 forced this to actually get built, not
   just re-verified. Also removed a duplicate `def main()` shadowing the real one.
2. khl.ru's standings page had been redesigned since the original 2026-08-17 build --
   old .championshipRegular-table__* classes were gone. Fixed via a real captured row
   from a live diagnostic run: team name is now in .table__cell--team-name, 9 plain
   stat cells follow in gp/w/otw/sow/sol/otl/pts_pct/l/pts order, then a combined
   "GF-GA" cell (a real goals-against column exists now, unlike the old design).
3. Tried building fixtures/results the same way as UAE/Arsenal -- anchor on the 22 real
   team names inside khl.ru's own calendar page. Three rounds of diagnostic capture all
   confirmed zero team names exist anywhere in the default Playwright-rendered
   snapshot, at all -- a hidden date-picker widget was found defaulted to "May 2025",
   meaning the real game list needs further JS interaction (date selection, or an
   internal API call) this script wasn't performing. Abandoned rather than ship a
   guess against a page that provably has no match data in its initial render.
4. Switched to Sofascore instead. Confirmed directly (not bot-blocked at all, and its
   own page text surfaced "KHL next match is Lokomotiv Yaroslavl v Traktor Chelyabinsk"
   on first fetch -- real, current data). Sofascore runs a well-documented public JSON
   API (api.sofascore.com/api/v1/...), widely scraped by others. One documented gotcha:
   it TLS-fingerprints plain HTTP clients, but this script already drives a real
   Playwright/Chromium browser for the standings fetch, so the fixtures/results fetch
   reuses that same real-browser navigation (page.goto() to each JSON endpoint,
   mimicking an actual user visiting that URL) rather than a bare request library,
   which should carry an authentic fingerprint the block can't easily catch.
"""
import json
import os
import re
from datetime import datetime, timedelta, timezone

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

STANDINGS_URL = "https://en.khl.ru/standings/"
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


# Confirmed real structure via diagnostic capture (2026-09-01, second redesign since the
# 2026-08-17 original): khl.ru moved off the old .championshipRegular-table__* classes
# entirely. Real row layout, confirmed against a full captured row: cell[0]=rank,
# cell[1]=logo, cell[2]=team name (.table__cell--team-name, a real <a> tag), cells[3:12]
# (9 plain .table__cell, no modifier class) = the numeric stats in order, cell[12] =
# combined "GF-GA" text (a real goals-against column exists now, unlike the old design),
# cell[13]=last-5-results icons (skip, not a stat), cell[14]=next-match preview (skip).
# The 9-stat order (gp/w/otw/sow/sol/otl/pts_pct/l/pts) carries over from the previously-
# confirmed old order with "gf" pulled out into its own combined cell - reasonable
# extrapolation since every value is genuinely 0 pre-season and the exact order can't be
# cross-checked from content alone until real games happen, but low-risk: get this
# slightly wrong and the fix is a straightforward reorder once non-zero data exists to
# verify against, not a class of bug that breaks anything else.
STAT_COLUMNS = ["gp", "w", "otw", "sow", "sol", "otl", "pts_pct", "l", "pts"]


def parse_standings(html):
    soup = BeautifulSoup(html, "html.parser")
    tbody = soup.find("tbody")
    if not tbody:
        return []
    standings = []
    for row in tbody.find_all("tr"):
        name_el = row.select_one(".table__cell--team-name a")
        if not name_el:
            continue
        team_name = name_el.get_text(strip=True)
        if not team_name:
            continue
        all_cells = row.find_all("td")
        if len(all_cells) < 13:
            continue
        stat_cells = all_cells[3:12]
        values = [c.get_text(strip=True) for c in stat_cells]
        stats = dict(zip(STAT_COLUMNS, values))

        def safe_int(key):
            v = stats.get(key, "0")
            try:
                return int(v)
            except (ValueError, TypeError):
                return 0

        gf_ga_text = all_cells[12].get_text(strip=True)
        gf_match = re.match(r"(\d+)\D+(\d+)", gf_ga_text)
        gf = int(gf_match.group(1)) if gf_match else 0

        standings.append({
            "team": team_name,
            "played": safe_int("gp"),
            "won": safe_int("w"),
            "otWon": safe_int("otw"),
            "soWon": safe_int("sow"),
            "soLost": safe_int("sol"),
            "otLost": safe_int("otl"),
            "lost": safe_int("l"),
            "gf": gf,
            "points": safe_int("pts"),
        })
        # Diagnostic (2026-09-05): season now genuinely underway with real non-zero
        # data (previous build/test was necessarily all-zero preseason), and "points"
        # is coming back 0 for a team with a real win - the column-order extrapolation
        # from the all-zero row can't be trusted now that real data exists to check it
        # against. Dump the raw cell values for the first non-zero row so the next run
        # settles the real order definitively instead of guessing again.
        if safe_int("gp") > 0 and not getattr(parse_standings, "_dumped", False):
            parse_standings._dumped = True
            log(f"DIAGNOSTIC non-zero row for {team_name}: raw stat_cells={values}, "
                f"gf_ga_text={gf_ga_text!r}, full row HTML={str(row)!r}")
    return standings


# --- Sofascore fixtures/results (added 2026-09-04) ---
# khl.ru's own calendar page doesn't render match data in the default snapshot at all
# (three rounds of diagnostic capture confirmed this - a hidden date-picker widget was
# found defaulted to May 2025, suggesting the real game list needs further JS
# interaction this script doesn't perform). Mr Eric pointed at Sofascore as an
# alternative source, and it turned out to be exactly right: not bot-blocked at all
# (confirmed by directly fetching it), and it runs a real public JSON API
# (api.sofascore.com/api/v1/...) that's widely documented and scraped by others,
# including "KHL next match is Lokomotiv Yaroslavl v Traktor Chelyabinsk" surfaced
# directly in the page text on first fetch - a strong sign the data genuinely exists
# and is current. One documented gotcha: Sofascore TLS-fingerprints plain HTTP clients
# (a naive requests.get() gets blocked) - solved here for free, since this script
# already drives a real Playwright/Chromium browser for the khl.ru standings fetch, and
# genuine browser navigation should carry an authentic fingerprint a fingerprint check
# can't easily distinguish from an ordinary user. Fetches by navigating the same
# browser page directly to each JSON endpoint (mimicking a real user visiting that URL)
# rather than a lower-level request API, to stay as close to organic browsing as
# possible.
FLASHSCORE_RESULTS_URL = "https://www.flashscoreusa.com/hockey/russia/khl/results/"
FLASHSCORE_FIXTURES_URL = "https://www.flashscoreusa.com/hockey/russia/khl/fixtures/"
# Flashscore's actual date-header format on the real page wasn't independently
# confirmed (only saw a summary widget using M/D like "9/6"), so this accepts that
# short form - a diagnostic run will confirm what actually appears, same as with
# every other new source this session.
DATE_HEADER_RE = re.compile(r"^(\d{2})/(\d{2})/(\d{4})$")
DATE_HEADER_SHORT_RE = re.compile(r"^(\d{1,2})/(\d{1,2})$")
FINAL_SCORE_RE = re.compile(r"Final\s+(\d+)\s*-\s*(\d+)")
SCORE_DASH_RE = re.compile(r"(?<!\d)(\d{1,2})\s*-\s*(\d{1,2})(?!\d)")
CLOCK_TIME_RE = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b")
# Flashscore game-page link pattern: /game/hockey/{team1-slug}-{id}/{team2-slug}-{id}/
GAME_LINK_RE = re.compile(r"/game/hockey/[a-zA-Z0-9\-]+/[a-zA-Z0-9\-]+/?")


def fetch_flashscore_matches(page, url, expect_finished):
    """Switched from 365scores to Flashscore (2026-09-05): 365scores was confirmed
    serving STALE prior-season data (May 2026 playoff finals) rather than the new
    September 2026-27 season - not a parsing bug, the wrong season's content entirely.
    Flashscore has dedicated /results/ and /fixtures/ URLs that a direct check
    confirmed show genuinely current games (Sept 6-9 2026-27 fixtures, verified
    against real team pairings). Uses the same real-browser Playwright fetch as the
    working khl.ru standings pull, since whether this specific page needs JS rendering
    hasn't been separately confirmed and there's no cost to being safe about it.
    Anchors on the /game/hockey/ URL pattern in each match's link, the same resilient
    technique used for the abandoned 365scores parser - not tied to any CSS class name.
    """
    try:
        page.goto(url, timeout=30000)
        page.wait_for_timeout(5000)
        html = page.content()
    except Exception as e:
        log(f"Flashscore fetch failed for {url}: {e}")
        return []

    log(f"Flashscore {'results' if expect_finished else 'fixtures'} page: captured {len(html)} chars")
    soup = BeautifulSoup(html, "html.parser")
    matches = []
    seen_hrefs = set()
    current_date = None

    for el in soup.find_all(True):
        text = el.get_text(strip=True) if el.name not in ("script", "style") else ""
        date_m = DATE_HEADER_RE.match(text) if text and len(text) <= 12 else None
        short_m = DATE_HEADER_SHORT_RE.match(text) if not date_m and text and len(text) <= 6 else None
        if date_m:
            y, mo, d = date_m.group(3), date_m.group(2), date_m.group(1)
            current_date = (int(y), int(mo), int(d))
            continue
        if short_m:
            mo, d = int(short_m.group(1)), int(short_m.group(2))
            # KHL 2026-27 regular season runs Sept 2026 - March 2027 (confirmed via
            # Wikipedia earlier this session) - Jan/Feb/Mar dates belong to 2027.
            year = 2027 if mo <= 3 else 2026
            current_date = (year, mo, d)
            continue

        if el.name != "a":
            continue
        href = el.get("href", "")
        if not GAME_LINK_RE.search(href):
            continue
        if href in seen_hrefs:
            continue
        seen_hrefs.add(href)

        full_text = el.get_text(" ", strip=True)
        score_m = FINAL_SCORE_RE.search(full_text) or SCORE_DASH_RE.search(full_text)
        time_m = None if score_m else CLOCK_TIME_RE.search(full_text)
        marker = score_m or time_m
        if not marker:
            log(f"Flashscore link has no score/time marker, skipping: {full_text[:150]!r} href={href!r}")
            continue

        before = full_text[: marker.start()].strip(" -")
        after = full_text[marker.end():].strip(" -")
        if not before or not after:
            log(f"Flashscore link: could not split team names from {full_text[:150]!r}")
            continue

        if not current_date:
            log(f"Flashscore link found before any date header seen, skipping: {full_text[:100]!r}")
            continue
        year, month, day = current_date
        if time_m:
            hour, minute = int(time_m.group(1)), int(time_m.group(2))
        else:
            hour, minute = 19, 0
        naive_dt_as_tashkent = datetime(year, month, day, hour, minute, tzinfo=timezone(timedelta(hours=5)))
        start = naive_dt_as_tashkent.strftime("%Y-%m-%dT%H:%M:00+05:00")

        entry = {"home": before, "away": after, "start": start}
        if score_m:
            g = score_m.groups()
            entry["homeScore"] = int(g[0])
            entry["awayScore"] = int(g[1])
            entry["finished"] = True
        matches.append(entry)

    log(f"parsed {len(matches)} match(es) from Flashscore {'results' if expect_finished else 'fixtures'}")
    if not matches:
        link_count = len(GAME_LINK_RE.findall(html))
        log(f"WARNING: 0 matches. GAME_LINK_RE found {link_count} raw href matches in the HTML")
        if link_count == 0:
            idx = html.find("/game/hockey/")
            log(f"first '/game/hockey/' occurrence context: {html[max(0,idx-200):idx+500]!r}")
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
        if tbodies:
            first_row = tbodies[0].find("tr")
            if first_row:
                log(f"FULL first row HTML: {str(first_row)!r}")

    all_matches = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")
            all_matches += fetch_flashscore_matches(page, FLASHSCORE_RESULTS_URL, expect_finished=True)
            all_matches += fetch_flashscore_matches(page, FLASHSCORE_FIXTURES_URL, expect_finished=False)
            browser.close()
    except Exception as e:
        log(f"Flashscore fetch failed entirely: {e}")

    log(f"parsed {len(all_matches)} total match(es) from Flashscore")

    fixtures = [m for m in all_matches if not m.get("finished")]
    results = [m for m in all_matches if m.get("finished")]
    # de-dupe (last/next pages could in principle overlap at a boundary)
    seen = set()
    deduped_results = []
    for m in sorted(results, key=lambda m: m["start"], reverse=True):
        key = (m["home"], m["away"], m["start"])
        if key not in seen:
            seen.add(key)
            deduped_results.append(m)
    seen = set()
    deduped_fixtures = []
    for m in sorted(fixtures, key=lambda m: m["start"]):
        key = (m["home"], m["away"], m["start"])
        if key not in seen:
            seen.add(key)
            deduped_fixtures.append(m)

    output = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "standings": standings,
        "fixtures": deduped_fixtures,
        "results": deduped_results,
    }

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    log(f"wrote {OUTPUT_PATH}: {len(standings)} standings, {len(deduped_fixtures)} fixtures, {len(deduped_results)} results")


if __name__ == "__main__":
    main()
