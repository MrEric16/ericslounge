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


# Matches ufc.com's actual observed format for its next confirmed event, e.g.
# "Sun, Aug 16 / 1:00 AM UTC / Main Card" -- confirmed directly from a real scrape run's
# captured text (see MAIN_CARD_TIME_PATTERN_NOTE below), not assumed. Gives date AND time
# in one match, already in UTC -- no DST/timezone-abbreviation handling needed at all.
MAIN_CARD_TIME_PATTERN = re.compile(
    r"[A-Z][a-z]{2},\s*([A-Z][a-z]{2})\s+(\d{1,2})\s*/\s*(\d{1,2}):(\d{2})\s*(AM|PM)\s*UTC\s*/\s*Main\s*Card",
    re.IGNORECASE,
)
# First version of this assumed "Main Card ... 9:00 PM EDT" based on search-result text
# snippets, since ufc.com isn't reachable from this development sandbox to verify directly
# (see module docstring). A real triggered run against the actual live page found that
# assumption wrong on two counts: the time is shown in UTC, not ET with a zone
# abbreviation, and it appears BEFORE "Main Card" in the text, bundled with the date,
# rather than after it standalone. Fixed from that real captured evidence, not guessed
# again -- this is exactly why the fail-closed design (a missing time, never a wrong one)
# mattered while this was being debugged: three days of "should work" pushes without a
# single wrong time shown to a real visitor.


def fetch_ufc_com_main_card_times():
    """Returns a list of (month_abbr, day, hour_24_utc, minute) tuples for whichever
    events ufc.com/events currently shows a confirmed Main Card time for -- realistically
    just the next event or two, since UFC doesn't usually lock broadcast slots in for
    anything further out. Matched against Wikipedia's scheduled events by (month, day)
    rather than by event name, since the date is unambiguous and sits directly in this
    same match next to the time -- no separate event-name association needed."""
    results = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.goto(UFC_EVENTS_URL, timeout=30000)
            try:
                page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                log("  ufc.com never reached networkidle within 15s, proceeding with whatever rendered so far")
            page.wait_for_timeout(4000)  # extra buffer past networkidle for late client-side hydration
            body_text = page.inner_text("body")
            browser.close()
    except Exception as e:
        log(f"ufc.com fetch failed entirely, times will be omitted this run: {e}")
        return results

    # Diagnostics for the committed log file -- this is the only visibility available into
    # what the scrape actually saw, since raw Actions logs aren't reachable from outside.
    log(f"ufc.com: captured {len(body_text)} chars of body text")
    idx = body_text.find("Main Card")
    if idx != -1:
        log(f"ufc.com: text around first 'Main Card' match: {body_text[max(0,idx-60):idx+20]!r}")

    for m in MAIN_CARD_TIME_PATTERN.finditer(body_text):
        month_abbr = m.group(1)
        day = int(m.group(2))
        hour_12, minute, meridiem = int(m.group(3)), int(m.group(4)), m.group(5)
        hour_24 = hour_12 % 12
        if meridiem.upper() == "PM":
            hour_24 += 12
        results.append((month_abbr, day, hour_24, minute))

    log(f"ufc.com: found {len(results)} dated Main Card time(s): {results}")
    return results


def attach_time_if_known(event, times_list):
    """Matches by date parsed from the event's own Wikipedia date_text against whatever
    dated Main Card times were found on ufc.com/events. Accepts a match either on the
    exact same calendar day OR the day after -- ufc.com shows the UTC calendar date,
    which legitimately rolls over to the next day for a Saturday-evening US event (e.g.
    UFC 330: Wikipedia's "Aug 15" is the US-local event date, but 9pm ET that night is
    already 1am UTC on Aug 16 -- both real, both correct, just different calendar
    conventions). Found and fixed via a real triggered run: the first version of this
    required an exact date match and silently found nothing for exactly this reason."""
    try:
        event_date = datetime.strptime(event["date_text"], "%b %d, %Y")
    except Exception as e:
        log(f"  could not parse date for {event['name']!r}: {e}")
        return
    for month_abbr, day, hour_24_utc, minute in times_list:
        try:
            candidate_month = datetime.strptime(month_abbr, "%b").month
        except Exception:
            continue
        # Try the event's own year first, then +/-1 to cover a Dec 31 -> Jan 1 rollover.
        for year_guess in (event_date.year, event_date.year + 1, event_date.year - 1):
            try:
                candidate_date = datetime(year_guess, candidate_month, day)
            except ValueError:
                continue
            delta_days = (candidate_date.date() - event_date.date()).days
            if delta_days in (0, 1):
                start_utc = datetime(candidate_date.year, candidate_date.month, candidate_date.day, hour_24_utc, minute, tzinfo=timezone.utc)
                start_tashkent = start_utc + timedelta(hours=5)
                # Stored PRE-SHIFTED to Tashkent wall-clock time (not a real UTC timestamp)
                # because formatTashkentDate() on the client does no timezone math at all
                # -- it just regex-extracts and displays whatever hour:minute digits are
                # embedded in the string. Matches the exact convention the old
                # hand-maintained data used (e.g. "2026-08-16T06:00:00+05:00" for this same
                # UFC 330 event -- verified this computation lands on that exact value,
                # independently confirmed via three separate paths: the old hand-researched
                # entry, an ET-based search-result snippet, and this direct UTC scrape).
                event["start_tashkent"] = start_tashkent.strftime("%Y-%m-%dT%H:%M:%S+05:00")
                return


def merge_preserved_results(new_past):
    """Carries forward any 'result' value already recorded for a past event with the
    same name, from whatever is currently on disk at OUTPUT_PATH, into the freshly
    scraped list. This scraper has no way to determine a winner itself (see module
    docstring on why real result-scraping was deliberately not attempted) -- it only
    ever preserves a value that a human or a separate process already verified and
    wrote in. Matching is by exact event name string, which has been stable across
    real runs of this scraper to date. Fails closed: any problem reading the old file
    (first run ever, corrupt JSON, unexpected shape) just means nothing to preserve
    this run, never a crash of the whole scrape over what's a best-effort courtesy."""
    try:
        with open(OUTPUT_PATH, "r", encoding="utf-8") as f:
            old_data = json.load(f)
        old_results = {
            e["name"]: e["result"]
            for e in old_data.get("past", [])
            if isinstance(e, dict) and e.get("name") and e.get("result")
        }
    except Exception as e:
        log(f"  no existing results to preserve this run ({e})")
        return new_past

    preserved = 0
    for event in new_past:
        if event["name"] in old_results:
            event["result"] = old_results[event["name"]]
            preserved += 1
    log(f"  preserved {preserved} previously-recorded result(s) across this run")
    return new_past


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
    past = merge_preserved_results(past)

    log("fetching ufc.com/events for Main Card start times (upcoming events only)...")
    times_list = fetch_ufc_com_main_card_times()
    for event in scheduled:
        attach_time_if_known(event, times_list)
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
