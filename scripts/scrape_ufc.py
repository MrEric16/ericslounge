#!/usr/bin/env python3
"""
Fetches upcoming and recent past UFC events from Wikipedia's "List of UFC
events" page. Runs daily via GitHub Actions so this stays fresh without
manual updates.

Wikipedia was chosen over scraping ufc.com directly because its event tables
are plain server-rendered HTML (a standard MediaWiki wikitable) rather than a
JS-heavy calendar UI -- confirmed reliably fetchable with a plain requests.get(),
no browser automation needed, and it's kept current by an active editor base
(the real bug found and fixed by hand earlier -- "Medic vs Rodriguez" already
having happened -- was itself confirmed via this exact page).

Output: data/ufc-live.json
"""
import json
import re
import time
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

URL = "https://en.wikipedia.org/wiki/List_of_UFC_events"
OUTPUT_PATH = "data/ufc-live.json"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; EricsLoungeBot/1.0)"}


def log(msg):
    print(f"[ufc-fetch] {msg}", flush=True)


def parse_table(table):
    events = []
    rows = table.find_all("tr")[1:]  # skip header row
    for row in rows:
        cells = row.find_all(["td", "th"])
        if len(cells) < 4:
            continue
        try:
            event_name = cells[1].get_text(strip=True) if len(cells) > 1 else None
            date_text = cells[2].get_text(strip=True) if len(cells) > 2 else None
            venue = cells[3].get_text(strip=True) if len(cells) > 3 else None
            location = cells[4].get_text(strip=True) if len(cells) > 4 else None
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
        # Fall back to checking the preceding heading if no caption
        if not heading_text:
            prev = table.find_previous(["h2", "h3"])
            heading_text = prev.get_text(strip=True).lower() if prev else ""
        log(f"  table preceded by/captioned: {heading_text!r}")
        parsed = parse_table(table)
        if "scheduled" in heading_text or "upcoming" in heading_text:
            scheduled.extend(parsed)
        elif "past" in heading_text or "event" in heading_text:
            past.extend(parsed)

    output = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "scheduled": scheduled[:15],
        "past": past[-15:] if len(past) > 15 else past,
    }

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    log(f"done: {len(scheduled)} scheduled events, {len(past)} past events found (kept most recent slices)")


if __name__ == "__main__":
    main()
