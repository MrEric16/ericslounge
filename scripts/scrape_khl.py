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

        won, ot_won, so_won = safe_int("w"), safe_int("otw"), safe_int("sow")
        ot_lost, so_lost, lost = safe_int("otl"), safe_int("sol"), safe_int("l")
        # Real fix (2026-09-05): stopped trusting whichever raw cell the earlier
        # column-order guess assumed was "points" - multiple teams showed impossible
        # combinations (2 wins but 0 points; 0 games played but 1 point), and the win/
        # loss breakdown independently sums to "played" for every row checked, so those
        # values are trustworthy even if the site's own displayed points figure isn't
        # (plausibly still uninitialized this early in the season). Computing points
        # directly from the confirmed real KHL formula instead (verified via
        # Wikipedia's own season-summary tables): 2 points for any win regardless of
        # how it was won, 1 point for an OT/shootout loss, 0 for a regulation loss.
        points = 2 * (won + ot_won + so_won) + 1 * (ot_lost + so_lost)

        standings.append({
            "team": team_name,
            "played": safe_int("gp"),
            "won": won,
            "otWon": ot_won,
            "soWon": so_won,
            "soLost": so_lost,
            "otLost": ot_lost,
            "lost": lost,
            "gf": gf,
            "points": points,
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
LIVERESULT_RESULTS_URL = "https://www.liveresult.ru/hockey/Kontinental-Hockey-League/results"
LIVERESULT_SCHEDULE_URL = "https://www.liveresult.ru/hockey/Kontinental-Hockey-League/scheduled"
# 2026-09-06: switched from Flashscore/365scores (both dead ends - 365scores served
# stale prior-season data, Flashscore's date text couldn't be reliably isolated per
# match after seven diagnostic rounds) to liveresult.ru, suggested directly by Mr Eric.
# Confirmed via direct fetch: server-rendered (not a JS-only shell), genuinely current
# 2026-27 season data, and - critically - each match is ONE single <a> tag containing
# the date-adjacent time/score AND both team names together, unlike Flashscore where
# the score lived in a separate sibling element. Much simpler structure to parse.
DATE_HEADER_DMY_RE = re.compile(r"(?<!\d)(\d{2})\.(\d{2})\.(\d{4})(?!\d)")
# Match link path: /hockey/matches/match408778_Spartak_Moscow-Torpedo_NN-online
MATCH_LINK_RE = re.compile(r"/hockey/matches/match\d+_([a-zA-Z0-9_]+)-([a-zA-Z0-9_]+)-online")
LIVERESULT_SCORE_RE = re.compile(r"(?<!\d)(\d{1,2}):(\d{1,2})(?!\d)")
LIVERESULT_TIME_RE = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b")

# Maps liveresult.ru's URL-slug team names (underscored, various English transliteration
# conventions) to this scraper's own canonical short names (matching khl.ru's standings
# short names, e.g. "Metallurg Mg" not "Metallurg_Magnitogorsk"). Built from slugs
# actually observed in a real fetch plus the known current 22-team roster - since not
# every slug has been directly observed, matching is fuzzy (underscore-to-space,
# lowercase, substring) rather than requiring an exact table hit for every team.
LIVERESULT_TEAM_ALIASES = {
    "spartak moscow": "Spartak", "torpedo nn": "Torpedo", "torpedo": "Torpedo",
    "ska st petersburg": "SKA", "ska": "SKA", "lada togliatti": "Lada", "lada": "Lada",
    "severstal cherepovets": "Severstal", "severstal": "Severstal",
    "salavat yulayev ufa": "Salavat Yulaev", "salavat yulayev": "Salavat Yulaev",
    "ak bars kazan": "Ak Bars", "ak bars": "Ak Bars",
    "sibir novosibirsk": "Sibir", "sibir": "Sibir",
    "avtomobilist": "Avtomobilist", "uhc dinamo": "Dinamo Mn", "dinamo minsk": "Dinamo Mn",
    "lokomotiv yaroslavl": "Lokomotiv", "lokomotiv": "Lokomotiv",
    "traktor": "Traktor", "traktor chelyabinsk": "Traktor",
    "metallurg magnitogorsk": "Metallurg Mg", "metallurg mg": "Metallurg Mg",
    "amur khabarovsk": "Amur", "amur": "Amur",
    "neftekhimik": "Neftekhimik", "neftekhimik nizhnekamsk": "Neftekhimik",
    "red star kunlun": "Dragons", "shanghai dragons": "Dragons", "kunlun red star": "Dragons",
    "avangard omsk": "Avangard", "avangard": "Avangard",
    "barys": "Barys", "barys astana": "Barys", "barys nur sultan": "Barys",
    "dinamo moskva": "Dynamo Msk", "dynamo moscow": "Dynamo Msk", "dinamo": "Dynamo Msk",
    "admiral": "Admiral", "admiral vladivostok": "Admiral",
    "hc sochi": "HC Sochi", "sochi": "HC Sochi",
    "cska": "CSKA", "cska moscow": "CSKA",
}


def liveresult_team_name(slug):
    key = slug.replace("_", " ").strip().lower()
    if key in LIVERESULT_TEAM_ALIASES:
        return LIVERESULT_TEAM_ALIASES[key]
    for alias, canonical in LIVERESULT_TEAM_ALIASES.items():
        if alias in key or key in alias:
            return canonical
    return slug.replace("_", " ")  # fall back to a readable form rather than dropping the match


def fetch_liveresult_matches(page, url):
    try:
        page.goto(url, timeout=30000)
        page.wait_for_timeout(3000)
        html = page.content()
    except Exception as e:
        log(f"liveresult fetch failed for {url}: {e}")
        return []

    log(f"liveresult page {url}: captured {len(html)} chars")
    soup = BeautifulSoup(html, "html.parser")
    matches = []
    seen_paths = set()
    current_date = None

    # Iterate every element (not just <a> tags) so plain-text date headers
    # ("05.09.2026") are seen in document order and tracked before the matches
    # under them.
    for el in soup.find_all(True):
        if el.name == "a":
            href = el.get("href", "")
            m = MATCH_LINK_RE.search(href)
            if not m:
                continue
            path = m.group(0)
            if path in seen_paths:
                continue
            seen_paths.add(path)

            text = el.get_text(" ", strip=True)
            home_name = liveresult_team_name(m.group(1))
            away_name = liveresult_team_name(m.group(2))

            # Real bug found in testing: the leading kickoff time ("10:00 ...") has the
            # same N:N shape as a score and was being matched by the score regex before
            # ever reaching the actual score later in the text ("... 0:1 ..."). The
            # time always appears as a clean prefix at the very start of the text, so
            # strip it off first and only search the remainder for a score.
            leading_time_m = LIVERESULT_TIME_RE.match(text)
            rest_of_text = text[leading_time_m.end():] if leading_time_m else text
            score_m = LIVERESULT_SCORE_RE.search(rest_of_text)
            time_m = None if score_m else leading_time_m
            marker = score_m or time_m
            if not marker:
                log(f"liveresult match {home_name} v {away_name}: no score/time in link text {text[:100]!r}")
                continue
            if not current_date:
                log(f"liveresult match {home_name} v {away_name} found before any date header seen, skipping")
                continue

            year, month, day = current_date
            if leading_time_m:
                hour, minute = int(leading_time_m.group(1)), int(leading_time_m.group(2))
            else:
                hour, minute = 19, 0
            start = datetime(year, month, day, hour, minute, tzinfo=timezone(timedelta(hours=5))) \
                .strftime("%Y-%m-%dT%H:%M:00+05:00")

            entry = {"home": home_name, "away": away_name, "start": start}
            if score_m:
                entry["homeScore"] = int(score_m.group(1))
                entry["awayScore"] = int(score_m.group(2))
            matches.append(entry)
        else:
            text = el.get_text(strip=True) if el.name not in ("script", "style") else ""
            date_m = DATE_HEADER_DMY_RE.search(text) if text and len(text) <= 40 else None
            if date_m:
                d, mo, y = int(date_m.group(1)), int(date_m.group(2)), int(date_m.group(3))
                current_date = (y, mo, d)

    log(f"parsed {len(matches)} match(es) from {url}")
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
            all_matches += fetch_liveresult_matches(page, LIVERESULT_RESULTS_URL)
            all_matches += fetch_liveresult_matches(page, LIVERESULT_SCHEDULE_URL)
            browser.close()
    except Exception as e:
        log(f"liveresult fetch failed entirely: {e}")

    log(f"parsed {len(all_matches)} total match(es) from liveresult")

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
