#!/usr/bin/env python3
"""
Watches Supabase's pending_events table for rows Mr Eric has approved in review.html
(status='approved') and publishes them into data/events-virtual.json's "dated" list --
the file the live site actually reads. Runs frequently (see the workflow's cron) so an
approval reaches the live site within a reasonable time without needing an instant push
from review.html itself, which has no repo-write access on purpose (see review.html's
own comments on why a repo-write token never belongs in client-side code).

Once a row is successfully published, it's deleted from pending_events entirely -- there's
no need to keep it around afterward, and this keeps the review queue showing only what
actually still needs a decision.
"""
import json
import os
from datetime import datetime, timezone

import requests

SUPABASE_URL = "https://uugjyucgeyopyvmhckdg.supabase.co"
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
EVENTS_VIRTUAL_PATH = "data/events-virtual.json"


def log(msg):
    print(f"[event-publish] {msg}", flush=True)


def fetch_approved():
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/pending_events?status=eq.approved&select=*",
        headers={"apikey": SUPABASE_SERVICE_ROLE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}"},
        timeout=20,
    )
    r.raise_for_status()
    return r.json()


def delete_row(row_id):
    r = requests.delete(
        f"{SUPABASE_URL}/rest/v1/pending_events?id=eq.{row_id}",
        headers={"apikey": SUPABASE_SERVICE_ROLE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}"},
        timeout=20,
    )
    r.raise_for_status()


def main():
    if not SUPABASE_SERVICE_ROLE_KEY:
        log("SUPABASE_SERVICE_ROLE_KEY is not set -- nothing to do, exiting without error")
        return

    try:
        approved = fetch_approved()
    except Exception as e:
        log(f"could not fetch approved rows: {e}")
        return

    if not approved:
        log("nothing approved and waiting, nothing to publish")
        return

    try:
        with open(EVENTS_VIRTUAL_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        log(f"could not read {EVENTS_VIRTUAL_PATH}, aborting rather than risk overwriting it wrong: {e}")
        return

    existing_keys = {(e.get("title", "").strip().lower(), e.get("startDate", "")) for e in data.get("dated", [])}

    published_ids = []
    for row in approved:
        key = (row.get("title", "").strip().lower(), row.get("start_date", ""))
        if key in existing_keys:
            log(f"  {row['title']!r} already published somehow, just clearing it from the queue")
            published_ids.append(row["id"])
            continue
        data.setdefault("dated", []).append({
            "title": row.get("title", ""),
            "org": row.get("org", ""),
            "description": row.get("description", ""),
            "startDate": row.get("start_date", ""),
            "time": row.get("time_text", ""),
            "category": row.get("category", ""),
            "url": row.get("url", ""),
        })
        existing_keys.add(key)
        published_ids.append(row["id"])
        log(f"  publishing: {row['title']!r} ({row.get('start_date')})")

    data["dated"].sort(key=lambda e: e.get("startDate", ""))
    data["curatedAt"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    with open(EVENTS_VIRTUAL_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    for row_id in published_ids:
        try:
            delete_row(row_id)
        except Exception as e:
            log(f"  published but could not clear row {row_id} from the queue: {e}")

    log(f"done: published {len(published_ids)} approved event(s)")


if __name__ == "__main__":
    main()
