#!/usr/bin/env python3
"""
Scrapes upcoming in-person Tashkent events for Mr. Eric's Lounge.

Sources:
  - tashkent.uz/en/afisha/{cat}  (plain HTTP, server-rendered, English labels)
  - afisha.uz/ru/{cat}           (plain HTTP, server-rendered, Russian, backup/extra coverage)
  - iticket.uz/en/events/{cat}   (JS-rendered SPA -> Playwright)
  - ticketon.uz/en/tashkent      (bot-detection protected -> Playwright, best effort)
  - eventbrite.com/d/uzbekistan--tashkent.../events/  (blocks plain requests -> Playwright, best effort)

Each source is wrapped in try/except so one broken source does not kill the
whole run. Every source's success/failure and event count is logged clearly
to stdout (visible in the GitHub Actions run log) rather than failing silently.

Output: data/events-tashkent.json
  [{ "title": str, "titleRu": str|None, "category": str, "venue": str|None,
     "startDate": "YYYY-MM-DD", "endDate": "YYYY-MM-DD"|None,
     "url": str, "source": str }]
"""
import json
import re
import sys
import time
import unicodedata
from datetime import datetime, timedelta

import requests
from bs4 import BeautifulSoup

TODAY = datetime.utcnow() + timedelta(hours=5)  # Tashkent is UTC+5, no DST
HORIZON_DAYS = 14  # pull a wider window than the 7 shown; lets the site roll forward without a re-scrape
WINDOW_END = TODAY + timedelta(days=HORIZON_DAYS)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9,ru;q=0.8",
}

# ---------------------------------------------------------------------------
# Category taxonomy + filtering keywords
# ---------------------------------------------------------------------------
INCLUDE_HINT_RU = [
    "концерт", "фестивал", "опера", "балет", "театр", "спектакл", "джаз",
    "классическ", "выставк", "музык", "марафон", "турнир", "матч", "лекци",
    "стендап", "оркестр", "филармони", "симфони",
]
EXCLUDE_KEYWORDS_RU = [
    "кино", "кинопоказ", "премьера фильма",           # cinema
    "вечеринк", "клуб", "паб", "бар ", "dj ", "ночн",  # nightclub/nightlife
    "квиз",                                             # quiz nights
    "квест", "escape room", "квест-рум",               # quests
    "аквапарк", "waterpark",                            # waterparks
    "экскурси", " тур ", "туристич",                   # tours
    "скидк", "распродаж",                                # shop discounts (not events)
    "билет в музей", "музейный билет",                  # permanent museum tickets
]
EXCLUDE_KEYWORDS_EN = [
    "cinema", "movie premiere", "screening",
    "nightclub", "night club", "party", "club night",
    "quiz night", "trivia night",
    "quest room", "escape room", "horror quest",
    "waterpark", "water park",
    "guided tour", "city tour", "excursion",
    "discount", "sale",
]

CATEGORY_MAP_TASHKENT_UZ = {
    "11": "concert",
    "18": "theatre",       # includes opera/ballet, tagged by tashkent.uz itself
    "15": "exhibition",
    "13": "sport",
    "10": "lecture",
    "6": "festival",       # mixed "City" category -- filtered hard below
}

ITICKET_CATEGORIES = {
    "concerts": "concert",
    "theaters-tashkent": "theatre",
    "cultural-events": "exhibition",
}


def log(msg):
    print(f"[scrape] {msg}", flush=True)


def norm(s):
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s).lower()
    s = re.sub(r"[^\w\s]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def is_excluded(text):
    t = (text or "").lower()
    for kw in EXCLUDE_KEYWORDS_RU + EXCLUDE_KEYWORDS_EN:
        if kw in t:
            return True
    return False


def parse_ru_date_range(text, ref_year=None):
    """Parses Russian date strings like '25 июля', 'с 15 по 16 августа' into (start,end)."""
    months = {
        "январ": 1, "феврал": 2, "март": 3, "апрел": 4, "ма": 5, "июн": 6,
        "июл": 7, "август": 8, "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12,
    }
    ref_year = ref_year or TODAY.year
    text = text.strip().lower()
    nums = re.findall(r"\d{1,2}", text)
    month = None
    for stem, m in months.items():
        if stem in text:
            month = m
            break
    if not month or not nums:
        return None, None
    day_start = int(nums[0])
    day_end = int(nums[1]) if len(nums) > 1 else day_start
    try:
        start = datetime(ref_year, month, day_start)
        end = datetime(ref_year, month, day_end)
    except ValueError:
        return None, None
    # roll year forward if the parsed date is more than ~2 months in the past
    # (source pages sometimes list a date without a year, defaulting to "this year")
    if start < TODAY - timedelta(days=60):
        start = start.replace(year=ref_year + 1)
        end = end.replace(year=ref_year + 1)
    return start, end


def parse_en_date_range(text, ref_year=None):
    """Parses tashkent.uz English date strings like '06 August' or 'С 01 August по 02 August'."""
    ref_year = ref_year or TODAY.year
    text = text.strip()
    m = re.findall(r"(\d{1,2})\s+([A-Za-z]+)", text)
    if not m:
        return None, None
    try:
        d1, mon1 = m[0]
        start = datetime.strptime(f"{d1} {mon1} {ref_year}", "%d %B %Y")
    except ValueError:
        return None, None
    end = start
    if len(m) > 1:
        try:
            d2, mon2 = m[1]
            end = datetime.strptime(f"{d2} {mon2} {ref_year}", "%d %B %Y")
        except ValueError:
            end = start
    if start < TODAY - timedelta(days=60):
        start = start.replace(year=ref_year + 1)
        end = end.replace(year=ref_year + 1)
    return start, end


# ---------------------------------------------------------------------------
# Source 1: tashkent.uz/en/afisha/{id}  (plain HTTP, English labels)
# ---------------------------------------------------------------------------
def scrape_tashkent_uz():
    events = []
    for cat_id, category in CATEGORY_MAP_TASHKENT_UZ.items():
        url = f"https://tashkent.uz/en/afisha/{cat_id}"
        try:
            r = requests.get(url, headers=HEADERS, timeout=20)
            r.raise_for_status()
        except Exception as e:
            log(f"tashkent.uz cat={category}: REQUEST FAILED ({e})")
            continue
        soup = BeautifulSoup(r.text, "html.parser")
        cards = soup.select("a[href*='afisha.uz']")
        seen_urls = set()
        count = 0
        for a in cards:
            href = a.get("href", "")
            if not href.startswith("http") or href in seen_urls:
                continue
            title = a.get("title") or a.get_text(strip=True)
            if not title or len(title) < 3:
                continue
            # date text usually sits in a sibling/parent block; search nearby text
            block = a.find_parent()
            block_text = block.get_text(" ", strip=True) if block else ""
            start, end = parse_en_date_range(block_text)
            if not start:
                continue
            if start > WINDOW_END or (end or start) < TODAY:
                continue
            if is_excluded(title) or is_excluded(block_text):
                continue
            seen_urls.add(href)
            events.append({
                "title": title,
                "titleRu": None,
                "category": category,
                "venue": None,
                "startDate": start.strftime("%Y-%m-%d"),
                "endDate": end.strftime("%Y-%m-%d") if end and end != start else None,
                "url": href,
                "source": "tashkent.uz",
            })
            count += 1
        log(f"tashkent.uz cat={category}: {count} events kept")
    return events


# ---------------------------------------------------------------------------
# Source 2: afisha.uz/ru/{cat}  (plain HTTP, Russian, extra coverage)
# ---------------------------------------------------------------------------
AFISHA_CATEGORIES = {
    "concerts": "concert",
    "theatres": "theatre",
    "exhibitions": "exhibition",
    "sport": "sport",
    "znaniya": "lecture",
}


def scrape_afisha_uz():
    events = []
    for slug, category in AFISHA_CATEGORIES.items():
        url = f"https://www.afisha.uz/ru/{slug}"
        try:
            r = requests.get(url, headers=HEADERS, timeout=20)
            r.raise_for_status()
        except Exception as e:
            log(f"afisha.uz cat={category}: REQUEST FAILED ({e})")
            continue
        soup = BeautifulSoup(r.text, "html.parser")
        links = soup.select(f"a[href*='/ru/{slug}/']")
        seen = set()
        count = 0
        for a in links:
            href = a.get("href", "")
            if href in seen:
                continue
            title = a.get_text(" ", strip=True)
            title = re.sub(r"Купить билеты", "", title).strip()
            if not title or len(title) < 3:
                continue
            m = re.search(r"\*\s*([^*]+?)(?:\s*\*|$)", title)
            date_text = m.group(1) if m else title
            start, end = parse_ru_date_range(date_text)
            if not start:
                continue
            if start > WINDOW_END or (end or start) < TODAY:
                continue
            if is_excluded(title):
                continue
            seen.add(href)
            full_url = href if href.startswith("http") else f"https://www.afisha.uz{href}"
            clean_title = re.split(r"\s*\*\s*\d", title)[0].strip()
            events.append({
                "title": clean_title,
                "titleRu": clean_title,
                "category": category,
                "venue": None,
                "startDate": start.strftime("%Y-%m-%d"),
                "endDate": end.strftime("%Y-%m-%d") if end and end != start else None,
                "url": full_url,
                "source": "afisha.uz",
            })
            count += 1
        log(f"afisha.uz cat={category}: {count} events kept")
    return events


# ---------------------------------------------------------------------------
# Source 3, 4, 5: JS-rendered / bot-protected sites via Playwright
# ---------------------------------------------------------------------------
def scrape_with_playwright():
    events = []
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log("playwright NOT INSTALLED -- skipping iticket.uz, ticketon.uz, eventbrite.com entirely")
        return events

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=HEADERS["User-Agent"],
            viewport={"width": 1280, "height": 1600},
            locale="en-US",
        )

        # --- iticket.uz ---
        for slug, category in ITICKET_CATEGORIES.items():
            page = context.new_page()
            url = f"https://iticket.uz/en/events/{slug}"
            try:
                page.goto(url, wait_until="networkidle", timeout=30000)
                # click "Load more" a few times if present, to widen coverage
                for _ in range(3):
                    try:
                        btn = page.get_by_text("Load more", exact=False)
                        if btn.count() > 0 and btn.first.is_visible():
                            btn.first.click()
                            page.wait_for_timeout(1200)
                        else:
                            break
                    except Exception:
                        break
                anchors = page.query_selector_all("a[href*='/en/event/']")
                count = 0
                seen = set()
                for a in anchors:
                    href = a.get_attribute("href") or ""
                    if not href or href in seen:
                        continue
                    text = (a.inner_text() or "").strip()
                    if not text or len(text) < 3:
                        continue
                    if is_excluded(text):
                        continue
                    date_match = re.search(r"\d{1,2}\s+[A-Za-z]{3,}", text)
                    start = None
                    if date_match:
                        try:
                            start = datetime.strptime(f"{date_match.group(0)} {TODAY.year}", "%d %B %Y")
                        except ValueError:
                            try:
                                start = datetime.strptime(f"{date_match.group(0)} {TODAY.year}", "%d %b %Y")
                            except ValueError:
                                start = None
                    if start and start < TODAY - timedelta(days=60):
                        start = start.replace(year=TODAY.year + 1)
                    if not start or start > WINDOW_END or start < TODAY - timedelta(days=1):
                        continue
                    full_url = href if href.startswith("http") else f"https://iticket.uz{href}"
                    seen.add(href)
                    title_clean = re.split(r"\d{1,2}\s+[A-Za-z]{3,}", text)[0].strip() or text
                    events.append({
                        "title": title_clean,
                        "titleRu": None,
                        "category": category,
                        "venue": None,
                        "startDate": start.strftime("%Y-%m-%d"),
                        "endDate": None,
                        "url": full_url,
                        "source": "iticket.uz",
                    })
                    count += 1
                log(f"iticket.uz cat={category}: {count} events kept")
            except Exception as e:
                log(f"iticket.uz cat={category}: FAILED ({e})")
            finally:
                page.close()

        # --- ticketon.uz (best effort, bot-detection likely to still block this) ---
        page = context.new_page()
        try:
            page.goto("https://ticketon.uz/en/tashkent", wait_until="networkidle", timeout=30000)
            title_check = page.title()
            if "access denied" in title_check.lower() or "blocked" in title_check.lower():
                log("ticketon.uz: BLOCKED even via headless browser (bot detection). 0 events.")
            else:
                anchors = page.query_selector_all("a[href*='/event']")
                count = 0
                seen = set()
                for a in anchors:
                    href = a.get_attribute("href") or ""
                    text = (a.inner_text() or "").strip()
                    if not href or href in seen or not text or len(text) < 3:
                        continue
                    if is_excluded(text):
                        continue
                    seen.add(href)
                    # ticketon's date formatting unknown ahead of time; only keep
                    # if we can find a plausible day-month pattern nearby
                    date_match = re.search(r"\d{1,2}\s+[A-Za-z]{3,}", text)
                    if not date_match:
                        continue
                    try:
                        start = datetime.strptime(f"{date_match.group(0)} {TODAY.year}", "%d %B %Y")
                    except ValueError:
                        continue
                    if start < TODAY - timedelta(days=60):
                        start = start.replace(year=TODAY.year + 1)
                    if start > WINDOW_END or start < TODAY - timedelta(days=1):
                        continue
                    full_url = href if href.startswith("http") else f"https://ticketon.uz{href}"
                    events.append({
                        "title": re.split(r"\d{1,2}\s+[A-Za-z]{3,}", text)[0].strip() or text,
                        "titleRu": None,
                        "category": "concert",
                        "venue": None,
                        "startDate": start.strftime("%Y-%m-%d"),
                        "endDate": None,
                        "url": full_url,
                        "source": "ticketon.uz",
                    })
                    count += 1
                log(f"ticketon.uz: {count} events kept")
        except Exception as e:
            log(f"ticketon.uz: FAILED ({e})")
        finally:
            page.close()

        # --- eventbrite.com (best effort) ---
        page = context.new_page()
        try:
            page.goto(
                "https://www.eventbrite.com/d/uzbekistan--tashkent--85680429/events/",
                wait_until="networkidle", timeout=30000,
            )
            anchors = page.query_selector_all("a[href*='eventbrite.com/e/']")
            count = 0
            seen = set()
            for a in anchors:
                href = a.get_attribute("href") or ""
                text = (a.inner_text() or "").strip()
                if not href or href in seen or not text or len(text) < 3:
                    continue
                if is_excluded(text):
                    continue
                seen.add(href)
                events.append({
                    "title": text.split("\n")[0].strip(),
                    "titleRu": None,
                    "category": "concert",
                    "venue": None,
                    "startDate": None,  # eventbrite date format needs a follow-up pass once we see real output
                    "endDate": None,
                    "url": href,
                    "source": "eventbrite.com",
                })
                count += 1
            log(f"eventbrite.com: {count} events found (dates NOT yet parsed -- see note in script)")
        except Exception as e:
            log(f"eventbrite.com: FAILED ({e})")
        finally:
            page.close()

        browser.close()
    return events


# ---------------------------------------------------------------------------
# Dedup + assemble
# ---------------------------------------------------------------------------
def dedup(events):
    seen = {}
    out = []
    for e in events:
        if not e.get("startDate"):
            continue  # can't place it on the 7-day list without a date
        key = (norm(e["title"])[:40], e["startDate"])
        if key in seen:
            continue
        seen[key] = True
        out.append(e)
    return out


def main():
    all_events = []
    log(f"Run started. Today (Tashkent): {TODAY.date()}  Window end: {WINDOW_END.date()}")

    try:
        all_events += scrape_tashkent_uz()
    except Exception as e:
        log(f"tashkent.uz: TOTAL FAILURE ({e})")

    try:
        all_events += scrape_afisha_uz()
    except Exception as e:
        log(f"afisha.uz: TOTAL FAILURE ({e})")

    try:
        all_events += scrape_with_playwright()
    except Exception as e:
        log(f"playwright sources: TOTAL FAILURE ({e})")

    deduped = dedup(all_events)
    deduped.sort(key=lambda e: e["startDate"])

    log(f"TOTAL before dedup: {len(all_events)}  |  after dedup: {len(deduped)}")

    with open("data/events-tashkent.json", "w", encoding="utf-8") as f:
        json.dump({
            "generatedAt": datetime.utcnow().isoformat() + "Z",
            "events": deduped,
        }, f, ensure_ascii=False, indent=2)

    log("Wrote data/events-tashkent.json")


if __name__ == "__main__":
    main()
