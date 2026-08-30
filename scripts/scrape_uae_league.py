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

UPDATE 2026-08-31: parse_matches was rewritten. It had been finding 0 matches (standings
still parsed fine, since that function walks every <tr> generically) -- the div[data-match_id]
+ .team-name-home/-away + data-datetime attributes it depended on evidently no longer matched
the live page, confirmed by directly re-fetching the page. Rewritten to anchor on
/match-report/co1183/.../maNNNNNNN/home-slug_away-slug/ links and adjacent team-badge
<img alt="Team Name"> elements instead -- both are semantic/URL-level details far less likely
to break with a CSS/markup refresh than specific class and data-attribute names. Date is
tracked from the visible DD.MM.YYYY header text instead of a data-datetime attribute, since
that attribute'''s continued existence could not be confirmed either.

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
TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
SCORE_RE = re.compile(r"^(-|\d+):(-|\d+)$")
MATCH_LINK_RE = re.compile(r"/match-report/co1183/[^/]+/ma(\d+)/([a-z0-9-]+)_([a-z0-9-]+)/")


def parse_matches(soup):
    """Anchored on /match-report/.../maNNNNNNN/home-slug_away-slug/ links and the team
    badge <img alt="Team Name"> elements next to them -- both are semantic/URL-level
    details unlikely to change with a CSS redesign, unlike the specific div attribute and
    class names (data-match_id, .team-name-home/-away) this scraper originally relied on,
    which returned 0 matches as of 2026-08-30 despite standings parsing fine from the same
    page -- meaning that markup had changed or was never correctly identified. Confirmed
    directly against the live all-matches page on 2026-08-31 before rewriting: match-report
    links and adjacent team-badge alt text are present and consistent across every match
    card, played and unplayed alike.

    Date is tracked by walking the page in document order and remembering the most recent
    standalone DD.MM.YYYY text seen (matches are grouped under date headers), since that
    doesn't depend on a data-datetime attribute that may or may not still exist.
    """
    matches = []
    seen_ids = set()
    current_date = None

    for el in soup.find_all(True):
        text = el.get_text(strip=True) if el.name not in ("script", "style") else ""
        if el.name not in ("div", "td", "th", "span", "p") :
            pass
        # track date headers: standalone DD.MM.YYYY text with no other content in this tag
        if text and MONTHS_NUM.fullmatch(text):
            current_date = text

        if el.name != "a":
            continue
        href = el.get("href", "")
        m = MATCH_LINK_RE.search(href)
        if not m:
            continue
        match_id, home_slug, away_slug = m.groups()
        if match_id in seen_ids:
            continue

        # walk up to find the smallest ancestor containing both team badge images
        container = el
        home_name = away_name = None
        for _ in range(8):
            if container is None:
                break
            imgs = container.find_all("img", alt=True)
            names = [i["alt"].strip() for i in imgs if i.get("alt", "").strip()]
            # dedupe consecutive identical alts (badge + link both carrying the same name)
            uniq = []
            for n in names:
                if not uniq or uniq[-1] != n:
                    uniq.append(n)
            if len(uniq) >= 2:
                home_name, away_name = uniq[0], uniq[1]
                break
            container = container.parent

        if not home_name or not away_name:
            continue

        # score: this link's own text, or a sibling link's text matching the score pattern
        score_text = el.get_text(strip=True)
        if not SCORE_RE.match(score_text):
            score_link = container.find("a", string=SCORE_RE) if container else None
            score_text = score_link.get_text(strip=True) if score_link else ""

        # kick-off time: any HH:MM text within the same container
        time_text = None
        if container:
            for cand in container.find_all(string=True):
                s = cand.strip()
                if TIME_RE.match(s):
                    time_text = s
                    break

        if not current_date or not time_text:
            continue

        day, month, year = MONTHS_NUM.match(current_date).groups()
        hour, minute = time_text.split(":")
        # site displays UAE local time (UTC+4); convert to Tashkent (UTC+5) = +1 hour
        uae_dt = datetime(int(year), int(month), int(day), int(hour), int(minute), tzinfo=timezone(timedelta(hours=4)))
        tashkent_dt = uae_dt.astimezone(timezone(timedelta(hours=5)))
        start = tashkent_dt.strftime("%Y-%m-%dT%H:%M:00+05:00")

        entry = {"home": home_name, "away": away_name, "start": start}
        if score_text and SCORE_RE.match(score_text) and "-" not in score_text:
            h, a = score_text.split(":")
            entry["homeScore"] = int(h)
            entry["awayScore"] = int(a)
            entry["finished"] = True

        matches.append(entry)
        seen_ids.add(match_id)

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
