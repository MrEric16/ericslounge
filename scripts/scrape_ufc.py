#!/usr/bin/env python3
"""
Fetches upcoming and recent past UFC events from Wikipedia's "List of UFC
events" page (name, date, venue, location -- reliable, simple, no browser
needed), then makes a SECOND pass over ufc.com/events with a real browser
(Playwright) to attach an actual Main Card start time to each upcoming event
Wikipedia's table doesn't carry.

WHY A SECOND SOURCE FOR TIME SPECIFICALLY (2026-08): Wikipedia's UFC event
coverage -- both the summary table AND every individual event page checked --
only ever carries a DATE, never a time. Confirmed directly, not assumed. The
site used to show accurate Tashkent times because a hand-maintained list had
someone look them up and type them in -- which is exactly the kind of
un-self-healing manual step this whole scraper exists to eliminate. ufc.com's
own event pages DO publish real "Start Times" (Early Prelims / Prelims / Main
Card, in ET) -- confirmed via direct research -- but that page is JS-rendered,
which is why the original version of this script avoided it entirely in favor
of Wikipedia's plain server-rendered HTML. Playwright (already used elsewhere
in this repo's CI, see uz-league-fetch.yml) solves that.

RESILIENCE NOTE: this scrapes ufc.com's rendered TEXT content with a regex
pattern ("Main Card ... H:MM PM ET/EST/EDT"), not fixed CSS selectors --
deliberately, since selectors tied to an unfamiliar site's exact DOM structure
are far more likely to silently break than a pattern matching wording that's
been independently confirmed stable and specific. If ufc.com's wording or
markup changes enough to break the pattern, this fails closed: events simply
keep their date with no time attached (see main.py's handling below), never a
wrong or fabricated time. A missing time is an honest gap; a wrong one is not.

Output: data/ufc-live.json
"""
import json
import re
import time
from datetime import datetime, timedelta, timezone

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

URL = "https://en.wikipedia.org/wiki/List_of_UFC_events"
UFC_EVENTS_URL = "https://www.ufc.com/events"
OUTPUT_PATH = "data/ufc-live.json"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; EricsLoungeBot/1.0)"}


def log(msg):
    print(f"[ufc-fetch] {msg}", flush=True)


DATE_PATTERN = re.compile(r"[A-Z][a-z]{2}\s+\d{1,2},\s*\d{4}")


def parse_table(table):
    events = []
    rows = table.find_all("tr")[1:]  # skip header row
    for row in rows:
        cells = row.find_all(["td", "th"])
        if len(cells) < 3:
            continue
        try:
            texts = [c.get_text(strip=True) for c in cells]
            # BUG FIXED VIA TWO REAL TEST RUNS: fixed positional indices broke
            # because "Scheduled events" and "Past events" have DIFFERENT
            # column layouts -- Past events has an extra leading event-number
            # column ("783") that Scheduled events doesn't have. Rather than
            # hard-code a position per table, find the date cell by content
            # pattern (e.g. "Oct 24, 2026") and work outward from there --
            # this is robust to either table's actual column count.
            date_idx = None
            for i, t in enumerate(texts):
                if DATE_PATTERN.search(t):
                    date_idx = i
                    break
            if date_idx is None:
                continue
            date_text = texts[date_idx]
            # Event name: the nearest cell before the date that isn't a bare
            # number (a bare number is the event-index column, not the name).
            event_name = None
            for i in range(date_idx - 1, -1, -1):
                if texts[i] and not texts[i].isdigit():
                    event_name = texts[i]
                    break
            venue = texts[date_idx + 1] if len(texts) > date_idx + 1 else None
            location = texts[date_idx + 2] if len(texts) > date_idx + 2 else None
            if not event_name or not date_text:
                continue
            events.append({
                "name": event_name,
                "date_text": date_text,
                "venue": venue,
                "location": location,
            })
        except Exception as e:
            log(f"  could not parse a row: {e}")
    return events


# Matches "Main Card" (any spacing/punctuation around it) followed reasonably
# soon by a time like "9:00 PM" and a US Eastern zone abbreviation. Loose on
# purpose -- the exact separator characters between label and time can't be
# verified locally (see module docstring), so this tolerates bullets,
# newlines, or plain spaces between them.
MAIN_CARD_TIME_PATTERN = re.compile(
    r"Main\s*Card.{0,20}?(\d{1,2}):(\d{2})\s*(AM|PM)\s*(EDT|EST)",
    re.IGNORECASE | re.DOTALL,
)
# UFC event names/numbers as they tend to appear standalone in the page text,
# e.g. "UFC 330" or "UFC Fight Night 289" -- used to figure out which event a
# matched Main Card time belongs to by taking the nearest one appearing
# BEFORE that match in the page's reading order.
EVENT_HEADING_PATTERN = re.compile(r"UFC(?:\s+Fight\s+Night)?\s*\d+\b|UFC\s+\d+\b")


def et_clock_to_utc_parts(hour_12, minute, meridiem, zone_abbr):
    """Converts a 12-hour ET clock time + zone abbreviation into (hour_24,
    minute, utc_offset_hours). Uses the zone abbreviation ufc.com itself
    publishes (EDT vs EST) rather than computing DST locally, so this is only
    ever as wrong as UFC's own published time, never wrong due to a DST
    calculation bug on this end."""
    hour_24 = hour_12 % 12
    if meridiem.upper() == "PM":
        hour_24 += 12
    utc_offset = 4 if zone_abbr.upper() == "EDT" else 5
    return hour_24, minute, utc_offset


def fetch_ufc_com_main_card_times():
    """Returns {event_heading_text: (hour_24_et, minute, utc_offset_hours)}
    for whichever events ufc.com/events currently has confirmed Main Card
    times for. Not every upcoming event will have one -- UFC often doesn't
    lock in exact broadcast slots until closer to the date, and this
    deliberately doesn't guess for those; they just keep date-only."""
    times_by_event = {}
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.goto(UFC_EVENTS_URL, timeout=30000)
            page.wait_for_timeout(3000)
            body_text = page.inner_text("body")
            browser.close()
    except Exception as e:
        log(f"ufc.com fetch failed entirely, times will be omitted this run: {e}")
        return times_by_event

    # Walk the page text tracking the most recent event heading seen, so a
    # Main Card time found later in reading order gets attributed correctly.
    tokens = []
    for m in EVENT_HEADING_PATTERN.finditer(body_text):
        tokens.append((m.start(), "heading", m.group(0)))
    for m in MAIN_CARD_TIME_PATTERN.finditer(body_text):
        tokens.append((m.start(), "time", m))
    tokens.sort(key=lambda t: t[0])

    current_heading = None
    for _, kind, val in tokens:
        if kind == "heading":
            current_heading = val
        elif kind == "time" and current_heading and current_heading not in times_by_event:
            hour_12, minute, meridiem, zone_abbr = int(val.group(1)), int(val.group(2)), val.group(3), val.group(4)
            hour_24, minute, utc_offset = et_clock_to_utc_parts(hour_12, minute, meridiem, zone_abbr)
            times_by_event[current_heading] = (hour_24, minute, utc_offset)

    log(f"ufc.com: found Main Card times for {len(times_by_event)} event heading(s): {list(times_by_event.keys())}")
    return times_by_event


def attach_time_if_known(event, times_by_event):
    """Best-effort match between a Wikipedia-sourced event name and a
    ufc.com heading token -- matches if the ufc.com heading text appears
    inside the Wikipedia event name (handles "UFC 330" matching
    "UFC 330: Makhachev vs. Machado Garry") or vice versa."""
    for heading, (hour_24, minute, utc_offset) in times_by_event.items():
        if heading.lower() in event["name"].lower() or event["name"].lower() in heading.lower():
            try:
                # date_text like "Aug 15, 2026" -- parse just the calendar date, attach
                # the ET clock time, convert to UTC, then shift +5h to Tashkent wall-clock
                # time. Stored PRE-SHIFTED (not as a real UTC timestamp) because
                # formatTashkentDate() on the client does no timezone math at all -- it
                # just regex-extracts and displays whatever hour:minute digits are embedded
                # in the string. This has to match that exact convention, the same one the
                # old hand-maintained data used (e.g. "2026-08-16T06:00:00+05:00" for a
                # 9pm EDT Aug 15 fight -- verified this computation lands on the exact same
                # value the old hardcoded entry had, independently confirming it's correct).
                event_date = datetime.strptime(event["date_text"], "%b %d, %Y")
                start_utc = datetime(
                    event_date.year, event_date.month, event_date.day,
                    hour_24, minute, tzinfo=timezone.utc
                ) + timedelta(hours=utc_offset)
                start_tashkent = start_utc + timedelta(hours=5)
                event["start_tashkent"] = start_tashkent.strftime("%Y-%m-%dT%H:%M:%S+05:00")
            except Exception as e:
                log(f"  could not attach time to {event['name']!r}: {e}")
            return


def main():
    log("fetching Wikipedia List of UFC events...")
    r = requests.get(URL, headers=HEADERS, timeout=30)
    r.raise_for_status()
    with open("scripts/debug-ufc-wiki.html", "w", encoding="utf-8") as f:
        f.write(r.text)

    soup = BeautifulSoup(r.text, "html.parser")
    tables = soup.find_all("table", class_="wikitable")
    log(f"found {len(tables)} wikitables on the page")

    scheduled = []
    past = []
    for table in tables:
        caption = table.find("caption")
        heading_text = caption.get_text(strip=True).lower() if caption else ""
        if not heading_text:
            prev = table.find_previous(["h2", "h3"])
            heading_text = prev.get_text(strip=True).lower() if prev else ""
        log(f"  table preceded by/captioned: {heading_text!r}")
        parsed = parse_table(table)
        if "scheduled" in heading_text or "upcoming" in heading_text:
            scheduled.extend(parsed)
        elif "past" in heading_text or "event" in heading_text:
            past.extend(parsed)

    scheduled = scheduled[:15]
    past = past[:15]

    log("fetching ufc.com/events for Main Card start times (upcoming events only)...")
    times_by_event = fetch_ufc_com_main_card_times()
    for event in scheduled:
        attach_time_if_known(event, times_by_event)
    with_time = sum(1 for e in scheduled if "start_tashkent" in e)
    log(f"attached a confirmed start time to {with_time}/{len(scheduled)} upcoming events")

    output = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "scheduled": scheduled,
        "past": past,
    }

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    log(f"done: {len(scheduled)} scheduled events, {len(past)} past events found (kept most recent slices)")


if __name__ == "__main__":
    main()
