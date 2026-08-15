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

SUPABASE_URL = "https://uugjyucgeyopyvmhckdg.supabase.co"
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
NASA_LIVE_URL = "https://www.nasa.gov/live/"
EVENTS_VIRTUAL_PATH = "data/events-virtual.json"

# How far ahead to look for candidates -- wider than the site's own 8-day display window,
# since a candidate found today might not get reviewed and approved for a day or two.
WINDOW_DAYS = 12


def log(msg):
    print(f"[event-candidates] {msg}", flush=True)


MONTHS = {
    'jan':1,'feb':2,'mar':3,'apr':4,'may':5,'jun':6,
    'jul':7,'aug':8,'sep':9,'oct':10,'nov':11,'dec':12,
}
# Matches NASA's confirmed live-page format: "Tuesday, Aug. 18 · 7 a.m. | <description>"
# -- weekday name is ignored (redundant with the date, and would need locale handling for
# no benefit), month is a 3-letter abbreviation with an optional period, time uses "a.m."/
# "p.m." with periods rather than "am"/"pm". Loose on separators (any whitespace/bullet)
# since exact characters can't be verified without live access to the page (see module
# docstring on the UFC scraper for why this matters).
NASA_EVENT_PATTERN = re.compile(
    r"[A-Z][a-z]+,\s*([A-Z][a-z]{2})\.?\s+(\d{1,2})\s*[·|]\s*(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m\.?\s*\|\s*([^\n]+)",
    re.IGNORECASE,
)


def fetch_nasa_candidates():
    candidates = []
    try:
        r = requests.get(NASA_LIVE_URL, headers={"User-Agent": "Mozilla/5.0 (compatible; EricsLoungeBot/1.0)"}, timeout=30)
        r.raise_for_status()
        text = r.text
    except Exception as e:
        log(f"NASA live page fetch failed: {e}")
        return candidates

    log(f"NASA page: captured {len(text)} chars")
    idx = text.find("Aug.")
    if idx == -1:
        idx = text.find("a.m.")
    if idx != -1:
        log(f"NASA page: text sample near a date/time: {text[max(0,idx-80):idx+120]!r}")
    else:
        log("NASA page: no 'Aug.' or 'a.m.' substring found anywhere in the fetched text")

    now = datetime.now(timezone.utc)
    for m in NASA_EVENT_PATTERN.finditer(text):
        month_abbr, day, hour_12, minute, meridiem, description = m.groups()
        month = MONTHS.get(month_abbr.lower())
        if not month:
            continue
        minute = int(minute) if minute else 0
        hour_24 = int(hour_12) % 12
        if meridiem.lower() == 'p':
            hour_24 += 12
        # Assume current year, roll to next year if that date has already passed --
        # handles the page listing events that span a year boundary.
        year = now.year
        try:
            candidate_date = datetime(year, month, int(day))
        except ValueError:
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

    published_keys = load_existing_published_keys()
    pending_keys = load_existing_pending_keys()
    already_seen = published_keys | pending_keys
    log(f"{len(published_keys)} already published, {len(pending_keys)} already pending review")

    now = datetime.now(timezone.utc)
    window_end = now + timedelta(days=WINDOW_DAYS)

    all_candidates = fetch_nasa_candidates()

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
