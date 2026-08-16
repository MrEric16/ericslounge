#!/usr/bin/env python3
"""
Checks a small set of trustworthy, pre-approved sources for free, educational, live
online events, and queues any new ones it finds into Supabase's pending_events table for
Mr Eric to approve or reject in review.html. Nothing this script finds ever reaches the
live site directly -- see scripts/publish_approved_events.py, which is the only thing
that writes to data/events-virtual.json, and only for rows a human has actually approved.

WHY QUEUED RATHER THAN PUBLISHED DIRECTLY: this category was hand-curated from the start
specifically because "free + educational + appropriate for a teen audience + relevant"
needs real judgment per item that a scraper can't reliably make (see the note field
already in data/events-virtual.json). Queuing instead of auto-publishing keeps that
judgment step intact -- the scraper's job is just to surface candidates from sources
already trusted enough to be worth a human's five seconds to glance at, not to make the
appropriateness call itself.

SOURCES: starts with NASA's own live-events schedule (nasa.gov/live/), which lists
real, dated, free public events in consistently formatted text
("Tuesday, Aug. 18 · 7 a.m. | <description>") -- confirmed directly against the live
page's actual text before this pattern was written, same evidence-based approach used
for the UFC time-scraping fix. Intentionally starts narrow with one well-understood
source rather than many unverified ones; more sources can be added the same way once
each one's actual format has been confirmed against real output, not assumed from search
snippets alone.

Output: rows inserted into Supabase's pending_events table (see review.html's SQL note
for the schema). Writes nothing to the repo itself.
"""
import json
import os
import re
from datetime import datetime, timedelta, timezone

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

SUPABASE_URL = "https://uugjyucgeyopyvmhckdg.supabase.co"
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
NASA_LIVE_URL = "https://www.nasa.gov/live/"
EVENTS_VIRTUAL_PATH = "data/events-virtual.json"

# How far ahead to look for candidates -- wider than the site's own 8-day display window,
# since a candidate found today might not get reviewed and approved for a day or two.
WINDOW_DAYS = 12


def log(msg):
    print(f"[event-candidates] {msg}", flush=True)


NGA_NEWS_URL = "https://www.nga.gov/research/center/news-center"
NGA_DATE_PATTERN = re.compile(r"([A-Z][a-z]{2,8})\s+(\d{1,2})(?:[\u2013\-]\d{1,2})?,\s*(\d{4})")
NGA_HEADING_PATTERN = re.compile(r"^(.+?)\s*:\s*(.+)$")


def fetch_nga_candidates():
    """Covers art specifically, per Mr Eric's explicit ask. Confirmed real structure via
    direct fetch (2026-08-16), not guessed from a search snippet: each program entry is a
    heading link reading 'DATE : TITLE', followed by a description paragraph. The feed
    mixes genuinely virtual events ("This virtual panel...") with DC-only in-person ones
    (a bookstore book launch, gallery lecture hall talks) -- there's no single reliable
    online/in-person flag field, so only entries whose own title or description text
    explicitly says "virtual" or "online" are queued. Conservative on purpose: missing an
    ambiguous hybrid event is a much smaller problem than flooding the review queue with
    DC-only events Mr Eric's students can't actually attend."""
    candidates = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.goto(NGA_NEWS_URL, timeout=30000)
            page.wait_for_timeout(3000)
            text = page.content()
            browser.close()
    except Exception as e:
        log(f"NGA page fetch failed: {e}")
        return candidates

    log(f"NGA page: captured {len(text)} chars")

    soup = BeautifulSoup(text, "html.parser")
    headings = soup.find_all(["h2", "h3"])
    log(f"NGA page: found {len(headings)} h2/h3 headings to check")
    now = datetime.now(timezone.utc)

    checked = 0
    for h in headings:
        a = h.find("a")
        if not a:
            continue
        heading_text = a.get_text(strip=True)
        m = NGA_HEADING_PATTERN.match(heading_text)
        if not m:
            continue
        date_part, title = m.groups()
        date_match = NGA_DATE_PATTERN.search(date_part)
        if not date_match:
            continue
        checked += 1

        desc_el = h.find_next_sibling("p")
        description = desc_el.get_text(strip=True) if desc_el else ""
        combined = f"{title} {description}".lower()
        if "virtual" not in combined and "online" not in combined:
            continue

        month_name, day, year = date_match.groups()
        try:
            candidate_date = datetime.strptime(f"{month_name} {day} {year}", "%B %d %Y")
        except ValueError:
            try:
                candidate_date = datetime.strptime(f"{month_name} {day} {year}", "%b %d %Y")
            except ValueError:
                continue
        if candidate_date.date() < now.date():
            continue  # NGA's feed includes recent past events too, only want upcoming

        url = a.get("href", "")
        if url.startswith("/"):
            url = "https://www.nga.gov" + url

        candidates.append({
            "title": title.strip()[:200],
            "org": "National Gallery of Art",
            "description": description.strip()[:500],
            "start_date": candidate_date.strftime("%Y-%m-%d"),
            "time_text": "",
            "category": "culture",
            "url": url or NGA_NEWS_URL,
            "source": "NGA News from the Center",
        })

    log(f"NGA page: checked {checked} dated headings, found {len(candidates)} explicitly-virtual/online candidate(s)")
    return candidates


def fetch_all_candidates():
    return fetch_nasa_candidates() + fetch_nga_candidates()


MANUAL_CANDIDATES = [
    {
        "title": "The Willow Island Cooling Tower Collapse: Engineering Ethics and Modern Safety Standards",
        "org": "NoonPi",
        "description": "Free webinar on the 1978 Willow Island Cooling Tower collapse, its wide-reaching implications, and how it influenced modern OSHA standards and engineering ethics.",
        "start_date": "2026-08-19",
        "time_text": "12:00 PM Eastern",
        "category": "history",
        "url": "https://noonpi.com/upcoming-webinars/",
        "source": "Manual search, NoonPi upcoming webinars",
    },
]


def queue_manual_candidates():
    published_keys = load_existing_published_keys()
    pending_keys = load_existing_pending_keys()
    already_seen = published_keys | pending_keys
    queued = 0
    for c in MANUAL_CANDIDATES:
        key = dedup_key(c["title"], c["start_date"])
        if key in already_seen:
            log(f"  manual candidate already queued/published, skipping: {c['title']!r}")
            continue
        try:
            insert_candidate(c)
            queued += 1
            log(f"  queued manual candidate: {c['title']!r} ({c['start_date']})")
        except Exception as e:
            log(f"  could not queue manual candidate {c['title']!r}: {e}")
    log(f"manual candidates: queued {queued}/{len(MANUAL_CANDIDATES)}")


MONTHS = {
    'jan':1,'feb':2,'mar':3,'apr':4,'may':5,'jun':6,
    'jul':7,'aug':8,'sep':9,'oct':10,'nov':11,'dec':12,
}
# Confirmed against the real page's actual HTML (2026-08-16): the date sits alone inside
# a <p><strong>Tuesday, Aug. 18</strong></p>, and the time + description sit in the very
# next <p> tag as plain text: "7 a.m. | Coverage of <a href=...>description</a>...". They
# are NOT on one joined line -- that flattened appearance only came from how a search
# engine's snippet extraction displays HTML, not the real markup. Parsed structurally
# (paragraph by paragraph) rather than with one regex spanning both, since the two pieces
# of information are genuinely in separate elements.
DATE_PATTERN = re.compile(r"[A-Z][a-z]+,\s*([A-Z][a-z]{2})\.?\s+(\d{1,2})\b")
TIME_DESC_PATTERN = re.compile(r"(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m\.?\s*\|\s*(.+)", re.IGNORECASE | re.DOTALL)


def fetch_nasa_candidates():
    candidates = []
    try:
        r = requests.get(NASA_LIVE_URL, headers={"User-Agent": "Mozilla/5.0 (compatible; EricsLoungeBot/1.0)"}, timeout=30)
        r.raise_for_status()
        text = r.text
    except Exception as e:
        log(f"NASA live page fetch failed: {e}")
        return candidates

    soup = BeautifulSoup(text, "html.parser")
    paragraphs = soup.find_all("p")
    now = datetime.now(timezone.utc)

    pending_date = None  # (month, day) parsed from the most recent date-only paragraph
    for p in paragraphs:
        strong = p.find("strong")
        if strong:
            date_match = DATE_PATTERN.search(strong.get_text(strip=True))
            if date_match:
                month = MONTHS.get(date_match.group(1).lower())
                day = date_match.group(2)
                pending_date = (month, day) if month else None
                continue

        if not pending_date:
            continue

        ptext = p.get_text(" ", strip=True)
        m = TIME_DESC_PATTERN.search(ptext)
        if m:
            month, day = pending_date
            hour_12, minute, meridiem, description = m.groups()
            minute = int(minute) if minute else 0
            hour_24 = int(hour_12) % 12
            if meridiem.lower() == 'p':
                hour_24 += 12
            year = now.year
            try:
                candidate_date = datetime(year, month, int(day))
            except ValueError:
                pending_date = None
                continue
            if candidate_date.date() < now.date():
                candidate_date = datetime(year + 1, month, int(day))
            candidates.append({
                "title": description.strip()[:200],
                "org": "NASA",
                "description": description.strip()[:500],
                "start_date": candidate_date.strftime("%Y-%m-%d"),
                "time_text": f"{hour_12}:{minute:02d} {meridiem.upper()}M ET",
                "category": "science",
                "url": NASA_LIVE_URL,
                "source": "NASA live events page",
            })
        pending_date = None  # a date only ever applies to the paragraph right after it

    log(f"NASA live page: found {len(candidates)} raw candidate(s)")
    return candidates


def dedup_key(title, start_date):
    return f"{title.strip().lower()}|{start_date}"


def load_existing_published_keys():
    """(title, date) keys already published to the live site -- never re-suggest these."""
    try:
        with open(EVENTS_VIRTUAL_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return {dedup_key(e.get("title", ""), e.get("startDate", "")) for e in data.get("dated", [])}
    except Exception as e:
        log(f"could not read {EVENTS_VIRTUAL_PATH}, proceeding with an empty published set: {e}")
        return set()


def load_existing_pending_keys():
    """(title, date) keys already sitting in the review queue (any status) -- never queue
    a duplicate. Selects title+start_date, not url, since NASA's live page reuses one
    generic URL for every event -- url alone can't tell candidates apart."""
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/pending_events?select=title,start_date",
            headers={"apikey": SUPABASE_SERVICE_ROLE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}"},
            timeout=20,
        )
        r.raise_for_status()
        return {dedup_key(row["title"], row["start_date"]) for row in r.json()}
    except Exception as e:
        log(f"could not read existing pending_events, proceeding with an empty set: {e}")
        return set()


def insert_candidate(candidate):
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/pending_events",
        headers={
            "apikey": SUPABASE_SERVICE_ROLE_KEY,
            "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
            "Content-Type": "application/json",
            "Prefer": "return=minimal",
        },
        json=candidate,
        timeout=20,
    )
    r.raise_for_status()


def main():
    if not SUPABASE_SERVICE_ROLE_KEY:
        log("SUPABASE_SERVICE_ROLE_KEY is not set -- nothing to do, exiting without error so the workflow doesn't show a false failure before the secret is configured")
        return

    queue_manual_candidates()

    published_keys = load_existing_published_keys()
    pending_keys = load_existing_pending_keys()
    already_seen = published_keys | pending_keys
    log(f"{len(published_keys)} already published, {len(pending_keys)} already pending review")

    now = datetime.now(timezone.utc)
    window_end = now + timedelta(days=WINDOW_DAYS)

    all_candidates = fetch_all_candidates()

    queued = 0
    for c in all_candidates:
        try:
            c_date = datetime.strptime(c["start_date"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except Exception:
            continue
        if not (now.date() <= c_date.date() <= window_end.date()):
            continue
        key = dedup_key(c["title"], c["start_date"])
        if key in already_seen:
            continue
        try:
            insert_candidate(c)
            already_seen.add(key)
            queued += 1
        except Exception as e:
            log(f"  could not queue {c['title']!r}: {e}")

    log(f"done: queued {queued} new candidate(s) for review")


if __name__ == "__main__":
    main()
