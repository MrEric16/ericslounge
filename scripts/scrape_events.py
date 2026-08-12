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
from deep_translator import GoogleTranslator

TODAY = datetime.utcnow() + timedelta(hours=5)  # Tashkent is UTC+5, no DST
TODAY_MIDNIGHT = TODAY.replace(hour=0, minute=0, second=0, microsecond=0)
# All scraped event dates default to midnight (no time-of-day data available).
# Comparing them against TODAY (which has a real time-of-day) would wrongly
# exclude events happening later today, since "today 00:00" < "today 14:32".
# Use TODAY_MIDNIGHT for all "is this event still upcoming" checks.
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
    "ночной клуб", "паб", "dj-сет", "dj сет",           # nightclub/nightlife (narrowed --
                                                          # bare "клуб"/"party" was too broad,
                                                          # caught "Muzqaymoq Party" (an ice
                                                          # cream festival) and would catch
                                                          # any "stand-up club" venue name too.
                                                          # "бар " moved to the word-boundary
                                                          # list below -- as a trailing-space
                                                          # substring it missed "Бар!"/"Бар,"/
                                                          # "Бар." (anything without a space
                                                          # right after the word)
    "алкогол", "спиртн", "пиво", "коктейл", "вино",     # alcohol, specifically
    "квиз",                                             # quiz nights
    "квест", "escape room", "квест-рум",               # quests
    "аквапарк", "waterpark",                            # waterparks
    "экскурси", " тур ", "туристич",                   # tours
    "аудиоспектакль",                                    # self-guided audio walking tours (recurring daily, not a real event)
    "скидк", "распродаж",                                # shop discounts (not events)
    "билет в музей", "музейный билет",                  # permanent museum tickets
    "мозгобойня",                                        # "Brain Slaughter" -- a pub-quiz trivia
                                                          # night franchise hosted at bars. Reported
                                                          # directly (2026-08-11): slipped through
                                                          # because the brand name itself doesn't
                                                          # contain "бар"/"квиз"/any other banned
                                                          # word -- it's a proper noun, not a
                                                          # description. Matches every variant
                                                          # ("Classic", etc.) since it's the whole
                                                          # brand name being matched, not a suffix.
]
EXCLUDE_KEYWORDS_EN = [
    "cinema", "movie premiere", "screening",
    "nightclub", "night club",                          # narrowed from bare "party"/"club night" --
                                                          # too many false positives on legitimate
                                                          # brand-name events (e.g. "X Party" fairs)
    "alcohol", "cocktail night", "beer fest", "wine tasting", "wine", "beer",
    "quiz night", "trivia night",
    "quest room", "escape room", "horror quest",
    "waterpark", "water park",
    "guided tour", "city tour", "excursion",
    "walk around tashkent", "tashkent speaks", "audio walk", "self-guided",
    "discount", "sale",
    "brain slaughter",                                   # English name for the same trivia franchise, see above
]
# Short/common words matched as whole words only (see is_excluded()) -- a bare
# substring match on these would false-positive constantly, e.g. "bar" inside
# "barbecue"/"barber", "dj" inside random slugs or unrelated text.
EXCLUDE_KEYWORDS_WORD_BOUNDARY = [
    "bar", "бар",            # standalone bar/nightclub-venue mentions
    "dj", "диджей",          # DJ events -- a reliable nightlife signal
]
# Known bar/nightlife venues, blocked by name regardless of what event or brand is hosted
# there -- a keyword match on the event's own text can't catch this, since the event's
# name/description often has nothing bar-related in it at all (see "мозгобойня" above,
# reported directly 2026-08-11: hosted at Terrace100, a bar, with a brand name that
# contains no banned keyword). Checked against the venue field specifically.
#
# Entries are either a plain string (substring match, for names distinctive enough that
# false positives aren't a real risk) or a (name, address_hint) tuple, requiring BOTH
# fragments present -- for shorter/more generic names where a bare substring match risks
# collateral damage. "cultura" is exactly this case: as a plain substring it would also
# match "Cultural Center" (the word "cultural" starts with those same 7 letters), so it's
# paired with the confirmed street address instead.
VENUE_BLACKLIST = [
    "terrace100",
    ("cultura", "рашидова"),  # Cultura, просп. Шарафа Рашидова, 40 -- confirmed bar, reported 2026-08-11
    "steinbrau",  # Steinbrau Restaurant -- "Brau" is German for "brew", a beer-hall/brewery-restaurant. Reported 2026-08-13. No collision risk as a bare substring.
    "blablabar",  # BlaBlaBar -- literally has "bar" in the name. Reported 2026-08-13. No collision risk as a bare substring.
]
def is_blacklisted_venue(venue):
    v = unicodedata.normalize("NFKD", (venue or "")).lower()
    v = re.sub(r"[^a-zа-яё0-9]", "", v)  # strip spaces/punctuation so "Terrace 100" / "Terrace-100" also match
    for entry in VENUE_BLACKLIST:
        if isinstance(entry, tuple):
            if all(fragment in v for fragment in entry):
                return True
        elif entry in v:
            return True
    return False
# Age-restriction markers -- any of these anywhere in the title/venue/category
# text means the event is not for this site, full stop, regardless of what
# category afisha.uz filed it under. Safe as bare substrings: the "+" makes
# false positives essentially impossible.
AGE_RESTRICTION_MARKERS = [
    "18+", "19+", "20+", "21+", "22+", "25+",
]
# Uzbek-language equivalents, best effort. afisha.uz's actual listings have
# been Russian-language every time this scraper has touched them so far, so
# this is precautionary coverage rather than a confirmed gap -- flagged for
# Mr Eric to correct/expand if he spots real Uzbek-language listings using
# different wording than this.
EXCLUDE_KEYWORDS_UZ = [
    "alkogol", "pivo", "vino", "sharob", "kokteyl", "tungi klub",
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


def guess_category(title, source_category):
    """Some sources file things under the wrong category by URL (e.g. iticket.uz
    lists 'Bunyodkor vs OKMK' under /events/concerts). Override with a content
    check for unambiguous sport markers."""
    t = title.lower()
    sport_markers = [" vs ", " vs. ", "матч", "марафон", "полумарафон", "турнир",
                      "чемпионат", "кубок", "финал"]
    if any(m in t for m in sport_markers):
        return "sport"
    return source_category


def log(msg):
    print(f"[scrape] {msg}", flush=True)


_translator = GoogleTranslator(source="ru", target="en")


def translate_ru_to_en(text):
    """Translates a Russian title to English. Falls back to the original
    Russian text on any failure (network issue, translation service down,
    etc.) rather than dropping the event or crashing the whole run."""
    if not text:
        return text
    try:
        result = _translator.translate(text)
        return result if result else text
    except Exception as e:
        log(f"Translation failed for {text[:40]!r}: {e}")
        return text


def norm(s):
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s).lower()
    s = re.sub(r"[^\w\s]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


import re


def is_excluded(text):
    t = (text or "").lower()
    for kw in EXCLUDE_KEYWORDS_RU + EXCLUDE_KEYWORDS_EN + EXCLUDE_KEYWORDS_UZ:
        if kw in t:
            return True
    # Word-boundary matched separately from the substring list above, because
    # short/common words like "bar" or "dj" would false-positive constantly
    # as bare substrings (e.g. "bar" inside "barbecue", "dj" inside random
    # text/slugs). Matched as whole words only.
    for kw in EXCLUDE_KEYWORDS_WORD_BOUNDARY:
        if re.search(rf"\b{re.escape(kw)}\b", t):
            return True
    # Age-restriction markers (18+, 19+, ..., 25+): NOT a bare substring check
    # anywhere on the page. Real bug found via an actual test run -- afisha.uz
    # puts a mandatory age-rating disclaimer in the footer of literally every
    # page on the site regardless of content (confirmed on a children's show
    # page), and a bare substring check matched that footer 100% of the time,
    # rejecting every single event. A real restriction (like Bla Bla Bar's
    # "Вход: мужчины — 25+, женщины — 21+") always sits next to actual
    # restriction-context words -- вход (entry), face control, дресс-код,
    # лица (persons). Require that context nearby, not just the bare marker
    # anywhere on the page.
    AGE_CONTEXT_WORDS = ("вход", "face control", "фейс-контрол", "фейсконтрол",
                          "дресс-код", "dress code", "лица", "лицам")
    for marker in AGE_RESTRICTION_MARKERS:
        idx = t.find(marker)
        while idx != -1:
            window = t[max(0, idx - 60):idx + 60]
            if any(ctx in window for ctx in AGE_CONTEXT_WORDS):
                return True
            idx = t.find(marker, idx + 1)
    return False


# Permanently banned, regardless of category or any other rule. Confirmed bad
# by direct instruction -- these should never appear again.
BANNED_TERMS_SUBSTRING = [
    "layner",               # Layner Resort
    "beerbasha",            # bar (also covered by the "beer" keyword, kept
                             # here too for clarity and redundancy)
    "spletni", "сплетни",   # Spletni Bar ("Gossip Bar")
    "kalin", "калин",       # Kalin Brothers
    "2000-х", "2000s",      # decade-themed nightlife party nights
    "islamic civilization", "исламской цивилизации",  # perpetual venue-ticket
                             # listing, not a real dated event -- was only
                             # banned in English before, but this check runs
                             # on the original Russian title (translation
                             # happens afterward), so the English-only entry
                             # in PERPETUAL_VENUE_TITLES_EXACT never actually
                             # matched anything on this scraper
    "bla bla bar",          # 21+/25+ face-control nightclub, confirmed via
                             # its own listed dress code and age requirement
    "twelve",                # 21+/25+ face-control nightclub, same venue
                             # cluster as Bla Bla Bar -- removed as a
                             # precaution pending direct verification
    "h.a.v.o",               # same precautionary removal as Twelve
]
BANNED_TERMS_WORD_BOUNDARY = [
    # short/ambiguous words that need a word-boundary match, not a bare
    # substring match (e.g. "oko" is inside the unrelated Russian word "около")
    "oko", "око",           # Oko restaurant parties
    "vm",                    # VM Bar, a nightclub -- word-boundary matched
                             # since "vm" alone risks false positives as a
                             # bare substring inside unrelated words
]


def is_banned(text):
    t = (text or "").lower()
    for term in BANNED_TERMS_SUBSTRING:
        if term in t:
            return True
    for term in BANNED_TERMS_WORD_BOUNDARY:
        if re.search(rf"\b{re.escape(term)}\b", t):
            return True
    return False

# Confirmed by direct observation (2026-08-02): these are standing "buy general
# admission" listings on iticket.uz, not dated events -- they show up with
# every single day's date because there's no real event date, just a
# perpetual ticket-sales page. One is even in Samarkand, not Tashkent.
PERPETUAL_VENUE_TITLES_EXACT = {
    "imam bukhari innovation museum",
    "center of islamic civilization",
}


def is_generic_venue_listing(title, venue):
    """Catches the general pattern behind the two confirmed cases above: when
    an event's title IS the venue name (a 'buy a ticket to this place' page),
    it's not a real dated event. Legitimate concerts/shows almost always have
    a title that differs from their venue."""
    if norm(title) in PERPETUAL_VENUE_TITLES_EXACT:
        return True
    if not venue:
        return False
    nt, nv = norm(title), norm(venue)
    if not nt or not nv:
        return False
    if nt == nv:
        return True
    # significant word overlap between title and venue (e.g. "Imam Bukhari
    # Innovation Museum" vs "Memorial Complex of Imam Al Bukhari")
    t_words = set(w for w in nt.split() if len(w) > 3)
    v_words = set(w for w in nv.split() if len(w) > 3)
    if t_words and v_words:
        overlap = t_words & v_words
        if len(overlap) >= 2 and len(overlap) >= len(t_words) * 0.6:
            return True
    return False


MONTHS_RU = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}
_MONTH_PATTERN_RU = "|".join(MONTHS_RU.keys())


def parse_ru_date_range(text, ref_year=None):
    """Parses Russian date strings, including cross-month ranges like
    'с 24 июля по 2 августа', same-month ranges 'с 1 по 2 августа', and
    single dates '25 июля'. Uses full genitive month words (not short stems)
    to avoid false substring matches (e.g. 'ма' inside 'ярмарка')."""
    ref_year = ref_year or TODAY.year
    text = text.strip().lower()

    m = re.search(rf"(\d{{1,2}})\s+({_MONTH_PATTERN_RU})\s*(?:по|-)\s*(\d{{1,2}})\s+({_MONTH_PATTERN_RU})", text)
    if m:
        d1, mon1, d2, mon2 = m.groups()
        try:
            start = datetime(ref_year, MONTHS_RU[mon1], int(d1))
            end = datetime(ref_year, MONTHS_RU[mon2], int(d2))
        except ValueError:
            return None, None
        if start < TODAY - timedelta(days=60):
            start = start.replace(year=ref_year + 1)
            end = end.replace(year=ref_year + 1)
        return start, end

    m = re.search(rf"(\d{{1,2}})\s*(?:по|-)\s*(\d{{1,2}})\s+({_MONTH_PATTERN_RU})", text)
    if m:
        d1, d2, mon = m.groups()
        try:
            start = datetime(ref_year, MONTHS_RU[mon], int(d1))
            end = datetime(ref_year, MONTHS_RU[mon], int(d2))
        except ValueError:
            return None, None
        if start < TODAY - timedelta(days=60):
            start = start.replace(year=ref_year + 1)
            end = end.replace(year=ref_year + 1)
        return start, end

    m = re.search(rf"(\d{{1,2}})\s+({_MONTH_PATTERN_RU})", text)
    if m:
        d, mon = m.groups()
        try:
            start = datetime(ref_year, MONTHS_RU[mon], int(d))
        except ValueError:
            return None, None
        if start < TODAY - timedelta(days=60):
            start = start.replace(year=ref_year + 1)
        return start, start

    return None, None


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
        if cat_id == "11":  # dump one category's raw HTML for pagination inspection
            with open("scripts/debug-tashkent-uz.html", "w", encoding="utf-8") as f:
                f.write(r.text)
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
            if start > WINDOW_END or (end or start) < TODAY_MIDNIGHT:
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
    "gorod": "festival",
    "children": "kids",
    "restaurants": "concert",  # confirmed legitimate content here too (e.g. PAUL's
                                # free live music evenings) -- relies on the alcohol-
                                # specific exclusion keywords to filter out actual
                                # nightlife/drinking content within this category
    # "standup" deliberately NOT included: afisha.uz has no reliable per-event
    # 18+ marker to detect against (confirmed by inspecting real event pages --
    # "18+" only appears in the site-wide footer disclaimer, identically on
    # family-friendly pages too, so it's not a usable signal). Standup comedy
    # skews adult-content by genre convention regardless of explicit labeling,
    # so this category is excluded by default rather than guessed at per-event.
}


def fetch_event_detail(url):
    """Fetches an event's own detail page and returns (clean_title, occurrences,
    content_text, venue), where occurrences is a list of (date, time_str)
    tuples -- one per date the event actually runs. This is the real fix for
    two separate problems found in the same night: (1) recurring/multi-date
    events (weekly shows, long-running exhibitions) collapsing to a single
    guessed date from the calendar listing page, and (2) content-safety
    checks that only ever saw the title/venue snippet, never the full page --
    which is exactly how a nightclub's actual age-restriction policy (stated
    separately from its title) slipped through undetected.

    content_text is the page's own article content ONLY -- extracted
    structurally starting from the <h1> and walking forward in document
    order, capped to a reasonable length. This deliberately excludes the
    site's shared nav menu (which sits before the h1, and itself contains
    banned keywords like "Кино"/"Скидки" as normal menu items) and the
    site-wide footer (which carries a mandatory age-rating disclaimer on
    literally every page regardless of content). A first attempt at this fix
    tried trimming the whole-page text at footer marker strings, which still
    let the nav-menu false positive through untouched and turned out to be
    unreliable to begin with -- confirmed broken via two real test runs that
    rejected 100% of events. Structural extraction from the h1 forward is the
    correct fix, not a text-matching patch.
    Returns (None, [], "", None) on any fetch failure.
    """
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        r.raise_for_status()
    except Exception as e:
        log(f"  detail fetch failed for {url}: {e}")
        return None, [], "", None

    # TEMPORARY debug dump -- confirming or ruling out whether this site
    # renders its schedule section via client-side JS (which plain requests.get()
    # would never see) vs. server-rendered HTML. Remove once confirmed.
    if "madagaskar-tashkent-city" in url or "zanmakon" in url:
        debug_name = "madagascar" if "madagaskar" in url else "qimmat"
        with open(f"scripts/debug-{debug_name}-raw.html", "w", encoding="utf-8") as f:
            f.write(r.text)
        log(f"  DEBUG: dumped raw HTML for {debug_name} page, {len(r.text)} chars")

    soup = BeautifulSoup(r.text, "html.parser")
    h1 = soup.find("h1")
    clean_title = h1.get_text(strip=True) if h1 else None
    if ("our-heritage" in url or "photo-chronicle-xx" in url):
        log(f"  TARGET DETAIL CHECK {url}: HTTP {r.status_code}, {len(r.text)} chars received, "
            f"h1 found={h1 is not None}, title={clean_title!r}")
        if h1 is None:
            with open(f"scripts/debug-target-{'heritage' if 'heritage' in url else 'photochronicle'}.html", "w", encoding="utf-8") as f:
                f.write(r.text)
    full_text = soup.get_text("\n", strip=True)  # still needed below for the
                                                   # "Расписание" schedule search,
                                                   # which can sit further down
                                                   # the page than a length cap
                                                   # on content_text would reach

    if h1:
        parts = []
        total_len = 0
        for el in h1.find_all_next(string=True):
            piece = el.strip()
            if not piece:
                continue
            parts.append(piece)
            total_len += len(piece)
            if total_len > 6000:  # generous cap -- real article content on
                                   # these pages is short; this comfortably
                                   # covers title+description+schedule while
                                   # staying well clear of the footer
                break
        content_text = "\n".join(parts)
    else:
        content_text = full_text  # no h1 found -- fall back rather than skip
                                    # the content check entirely

    # Venue: event detail pages consistently link to their location as
    # /ru/places/<slug> with the venue name as the link text (confirmed on
    # multiple pages, e.g. the Madagascar show links "Tashkent City" this way).
    venue = None
    place_link = soup.find("a", href=re.compile(r"/ru/places/"))
    if place_link:
        venue = place_link.get_text(strip=True) or None

    occurrences = []
    idx = full_text.rfind("Расписание")
    if idx != -1:
        section = full_text[idx:idx + 4000]
        for stop_marker in ("Подпишитесь на наш Telegram", "©"):
            cut = section.find(stop_marker)
            if cut != -1:
                section = section[:cut]
                break

        # Real bug found and fixed via a real test run: some events (Qimmat
        # confirmed directly) list their "Расписание" as a continuous date
        # range ("с 6 по 25 августа") rather than individual dates. The old
        # code went straight to the discrete day+month parser below, which
        # only matches a bare "day month" pattern -- for "с 6 по 25 августа"
        # that means it grabbed just "25 августа" as if it were the one and
        # only occurrence, completely missing "6" (not immediately followed
        # by a month word in this phrasing) and every date in between. A
        # long-running exhibition's actual END date frequently falls outside
        # the scraper's ~14-day window even though the exhibition is running
        # RIGHT NOW -- which is exactly why it kept showing "no dates found"
        # despite being live. Check for the range pattern first; only fall
        # back to the discrete list parser if this section isn't a range.
        range_start, range_end = parse_ru_date_range(section[:200])
        if range_start and range_end and range_start != range_end:
            day = range_start
            while day <= range_end:
                occurrences.append((day, None))
                day += timedelta(days=1)

        if not occurrences:
            date_pattern = re.compile(rf"(\d{{1,2}})\s+({_MONTH_PATTERN_RU})", re.IGNORECASE)
            time_pattern = re.compile(r"^(\d{1,2}):(\d{2})")
            matches = list(date_pattern.finditer(section))
            for i, m in enumerate(matches):
                day = int(m.group(1))
                month = MONTHS_RU[m.group(2).lower()]
                tail_start = m.end()
                tail_end = matches[i + 1].start() if i + 1 < len(matches) else len(section)
                tail = section[tail_start:tail_end].strip()[:20]
                time_match = time_pattern.search(tail)
                time_str = f"{time_match.group(1)}:{time_match.group(2)}" if time_match else None
                try:
                    year = TODAY.year
                    date_obj = datetime(year, month, day)
                except ValueError:
                    continue
                if date_obj < TODAY_MIDNIGHT - timedelta(days=60):
                    date_obj = date_obj.replace(year=year + 1)
                occurrences.append((date_obj, time_str))

    if not occurrences:
        # No discrete "Расписание" list -- likely a long-running exhibition
        # stated as a continuous date range instead (e.g. "с 6 по 25 августа").
        # This exact gap is why 4 real exhibitions were missing from the site
        # on 2026-08-04 despite being live and correctly categorized -- the
        # calendar view doesn't reliably re-list them on every day of their
        # run, and until now nothing filled that gap.
        start, end = parse_ru_date_range(content_text[:2000])
        if start and end:
            day = start
            while day <= end:
                occurrences.append((day, None))
                day += timedelta(days=1)

    return clean_title, occurrences, content_text, venue


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
        seen_hrefs = set()
        candidates = []
        for a in links:
            href = a.get("href", "")
            if not href or href in seen_hrefs or href.rstrip("/").endswith(f"/{slug}"):
                continue
            seen_hrefs.add(href)
            full_url = href if href.startswith("http") else f"https://www.afisha.uz{href}"
            candidates.append(full_url)

        count = 0
        for full_url in candidates:
            clean_title, occurrences, full_text, venue = fetch_event_detail(full_url)
            time.sleep(0.3)  # polite pacing -- now visiting far more pages per run than before
            if not clean_title or is_excluded(clean_title) or is_excluded(full_text) or is_banned(full_text):
                continue
            if is_blacklisted_venue(venue):
                continue
            english_title = translate_ru_to_en(clean_title)
            resolved_category = guess_category(clean_title, category)
            in_window = [(d, t) for d, t in occurrences if TODAY_MIDNIGHT <= d <= WINDOW_END]
            for date_obj, time_str in in_window:
                events.append({
                    "title": english_title,
                    "titleRu": clean_title,
                    "category": resolved_category,
                    "venue": venue,
                    "startDate": date_obj.strftime("%Y-%m-%d"),
                    "endDate": None,
                    "time": time_str,
                    "url": full_url,
                    "source": "afisha.uz",
                })
                count += 1
        log(f"afisha.uz cat={category}: {count} events kept ({len(candidates)} pages checked)")
    return events


# Narrow, specifically-approved exceptions to the normal category rules --
# not a blanket category opt-in (e.g. NOT "include all cinema content"), just
# this one confirmed-good recurring discount day, per explicit direction.
SPECIAL_TRACKED_EVENTS = [
    ("https://www.afisha.uz/ru/cinema/2026/07/16/kinoprazdnik-v-sredu", "festival"),
]


def scrape_special_tracked_events():
    events = []
    for url, category in SPECIAL_TRACKED_EVENTS:
        clean_title, occurrences, full_text, venue = fetch_event_detail(url)
        time.sleep(0.3)
        if not clean_title or is_excluded(full_text) or is_banned(full_text):
            log(f"special-tracked: FAILED to fetch or failed content check for {url}")
            continue
        english_title = translate_ru_to_en(clean_title)
        in_window = [(d, t) for d, t in occurrences if TODAY_MIDNIGHT <= d <= WINDOW_END]
        for date_obj, time_str in in_window:
            events.append({
                "title": english_title,
                "titleRu": clean_title,
                "category": category,
                "venue": venue,
                "startDate": date_obj.strftime("%Y-%m-%d"),
                "endDate": None,
                "time": time_str,
                "url": url,
                "source": "afisha.uz",
            })
        log(f"special-tracked: {clean_title!r} -> {len(in_window)} events kept")
    return events


# ---------------------------------------------------------------------------
# Source 3, 4, 5: JS-rendered / bot-protected sites via Playwright
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# NEW primary source: afisha.uz's own calendar view, one real day at a time.
# Replaces the old per-category-listing-page approach entirely -- that one
# could only ever see whatever was on the front page of each category, and
# had no way to discover older-but-still-relevant posts once newer ones
# pushed them down. The calendar view solves this by directly asking "what's
# happening on this exact date", which is exactly what we need, across every
# category in one place.
#
# The catch: calendar URLs use a #YYYY-MM-DD fragment, and fragments never
# reach the server at all (this is a basic property of how URLs work, not
# specific to this site) -- the real per-day content only renders after
# client-side JavaScript runs. So this needs a real browser (Playwright),
# same as iticket.uz already required elsewhere in this script.
#
# Category is inferred directly from each link's URL (e.g. /ru/concerts/...),
# which has proven to be a stable pattern across this whole project -- more
# robust than trying to match on visible section-header text.
# ---------------------------------------------------------------------------
CALENDAR_DAYS = 10  # small buffer beyond the 8 days the site actually displays

# None = category is deliberately excluded entirely, per explicit direction:
# no bars/clubs, no standup (no reliable 18+ signal, see earlier notes), no
# shops, no cinema (except the one separately-approved discount-day exception
# still handled by scrape_special_tracked_events), and a few categories that
# were simply never in scope (techno, fashion, tourism, media, premium, photo).
CALENDAR_CATEGORY_MAP = {
    "concerts": "concert",
    "theatres": "theatre",
    "exhibitions": "exhibition",
    "sport": "sport",
    "znaniya": "lecture",
    "gorod": "festival",
    "children": "kids",
    "restaurants": "concert",
    "cinema": None,
    "standup": None,
    "shops": None,
    "discount": None,
    "clubs": None,
    "techno": None,
    "fashion": None,
    "tourism": None,
    "media": None,
    "premium": None,
    "photo": None,
}


def scrape_afisha_calendar():
    events = []
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log("playwright NOT INSTALLED -- skipping calendar scrape entirely")
        return events

    # PHASE 1: discovery. Walk the calendar day by day purely to find which
    # event URLs exist -- this part already works well (it's how category-page
    # scraping's "scrolled off page 1" gap got fixed). What it must NOT do
    # anymore is trust the calendar's own title/date/venue snippet as final --
    # that's exactly how 4 real exhibitions and a recurring show went missing
    # on 2026-08-04, because the calendar doesn't reliably re-list an ongoing
    # event on every day of its actual run.
    discovered = {}  # full_url -> category (first one seen)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=HEADERS["User-Agent"],
            viewport={"width": 1280, "height": 2400},
            locale="ru-RU",
        )
        page = context.new_page()

        for i in range(CALENDAR_DAYS):
            date_obj = TODAY_MIDNIGHT + timedelta(days=i)
            date_str = date_obj.strftime("%Y-%m-%d")
            url = f"https://www.afisha.uz/ru/calendar#{date_str}"
            try:
                page.goto(url, wait_until="networkidle", timeout=30000)
                page.wait_for_timeout(2500)  # extra buffer for client-side render
            except Exception as e:
                log(f"calendar {date_str}: FAILED to load ({e})")
                continue

            if i == 0:
                with open("scripts/debug-afisha-calendar.html", "w", encoding="utf-8") as f:
                    f.write(page.content())

            anchors = page.query_selector_all("a[href]")
            day_new = 0
            for a in anchors:
                href = a.get_attribute("href") or ""
                m = re.search(r"/ru/([a-z]+)/\d{4}/\d{2}/\d{2}/", href)
                if not m:
                    continue
                slug = m.group(1)
                if slug not in CALENDAR_CATEGORY_MAP:
                    continue
                category = CALENDAR_CATEGORY_MAP[slug]
                if category is None:
                    continue  # deliberately excluded category
                full_url = href if href.startswith("http") else f"https://www.afisha.uz{href}"
                if full_url in discovered:
                    continue
                # Cheap first-pass filter on the calendar's own link text, just
                # to avoid queuing up an obviously-excluded page for a detail
                # visit later (saves time, not a substitute for the real check).
                raw_text = (a.inner_text() or "").strip()
                if is_excluded(raw_text) or is_banned(raw_text):
                    continue
                discovered[full_url] = category
                day_new += 1
            log(f"calendar {date_str}: {day_new} new event URLs discovered")
            time.sleep(0.5)

        # SUPPLEMENTARY discovery pass, exhibitions only: the day-by-day calendar
        # walk above just doesn't reliably re-surface a long-running exhibition on
        # every day of its actual run (documented in this file's own history --
        # this exact category of gap caused 4 real exhibitions to go missing on
        # 2026-08-04, and again caused 2 more specific ones -- "Наше наследие" and
        # "Фотохроника XX века" -- to go missing as of 2026-08-10, confirmed by
        # directly fetching https://www.afisha.uz/ru/exhibitions and finding both
        # clearly listed there with their full date ranges, while a real scraper
        # run's log showed neither ever reached the final output). The flat
        # category listing page doesn't have this problem -- it shows everything
        # currently running regardless of which specific day you happen to check
        # against a calendar. Scoped to exhibitions specifically since that's the
        # category actually reported broken, twice now; other categories move too
        # fast for a stale "front page" to matter the same way (see the switch
        # away from category-page scraping entirely, above), so widening this to
        # every category would reintroduce the exact problem the calendar switch
        # was meant to fix. Note: doesn't click "Показать ещё" (show more), so an
        # exhibition far enough down that page still won't be caught -- the two
        # actually-reported ones were both within the initial page load.
        try:
            page.goto("https://www.afisha.uz/ru/exhibitions", wait_until="networkidle", timeout=30000)
            page.wait_for_timeout(2500)
            with open("scripts/debug-afisha-exhibitions-listing.html", "w", encoding="utf-8") as f:
                f.write(page.content())
            listing_new = 0
            all_hrefs_seen = []
            for a in page.query_selector_all("a[href]"):
                href = a.get_attribute("href") or ""
                m = re.search(r"/ru/(exhibitions)/\d{4}/\d{2}/\d{2}/", href)
                if not m:
                    continue
                all_hrefs_seen.append(href)
                full_url = href if href.startswith("http") else f"https://www.afisha.uz{href}"
                if full_url in discovered:
                    continue
                raw_text = (a.inner_text() or "").strip()
                if is_excluded(raw_text) or is_banned(raw_text):
                    log(f"exhibitions listing: EXCLUDED {href!r} on link text {raw_text[:80]!r}")
                    continue
                discovered[full_url] = "exhibition"
                listing_new += 1
            log(f"exhibitions listing page (supplementary): {listing_new} new event URLs discovered, {len(all_hrefs_seen)} total exhibition links seen on page")
            for target in ("our-heritage", "photo-chronicle-xx"):
                matches = [h for h in all_hrefs_seen if target in h]
                log(f"  TARGET CHECK '{target}': {'FOUND in raw hrefs: ' + str(matches) if matches else 'NOT FOUND in any href on the rendered page'}")
        except Exception as e:
            log(f"exhibitions listing page (supplementary): FAILED to load ({e})")

        browser.close()

    log(f"calendar discovery complete: {len(discovered)} unique event URLs to check")

    # PHASE 2: for every discovered URL, visit its own page once. This gives
    # two things the calendar snippet never could: (1) the event's REAL full
    # list of dates, straight from its own "Расписание" section or its stated
    # date range -- not a guess based on which days the calendar happened to
    # show it, and (2) the full page text, so is_excluded()/is_banned() can
    # check everything on the page -- including a nightclub's age-restriction
    # policy stated separately from its title, which is exactly what got
    # missed before.
    for full_url, category in discovered.items():
        clean_title, occurrences, full_text, venue = fetch_event_detail(full_url)
        time.sleep(0.3)
        is_target = "our-heritage" in full_url or "photo-chronicle-xx" in full_url
        if not clean_title:
            if is_target:
                log(f"  TARGET DROPPED at 'not clean_title' check: {full_url}")
            continue
        if is_excluded(clean_title) or is_excluded(full_text) or is_banned(clean_title) or is_banned(full_text):
            if is_target:
                log(f"  TARGET DROPPED at exclude/ban check: {full_url} | title_excluded={is_excluded(clean_title)} full_text_excluded={is_excluded(full_text)} title_banned={is_banned(clean_title)} full_text_banned={is_banned(full_text)}")
            continue
        if is_blacklisted_venue(venue):
            log(f"  DROPPED (blacklisted venue): {full_url} | title={clean_title!r} venue={venue!r}")
            continue
        if is_generic_venue_listing(clean_title, venue):
            if is_target:
                log(f"  TARGET DROPPED at generic_venue_listing check: {full_url} title={clean_title!r} venue={venue!r}")
            continue
        if is_target:
            log(f"  TARGET PASSED ALL CHECKS: {full_url} title={clean_title!r} occurrences={occurrences}")
        try:
            half = len(clean_title) // 2
            if half > 4 and clean_title[:half].strip() == clean_title[half:].strip():
                clean_title = clean_title[:half].strip()
            english_title = translate_ru_to_en(clean_title)
            resolved_category = guess_category(clean_title, category)
            in_window = [(d, t) for d, t in occurrences if TODAY_MIDNIGHT <= d <= WINDOW_END]
            appended_this_url = 0
            for date_obj, time_str in in_window:
                events.append({
                    "title": english_title,
                    "titleRu": clean_title,
                    "category": resolved_category,
                    "venue": venue,
                    "startDate": date_obj.strftime("%Y-%m-%d"),
                    "endDate": None,
                    "time": time_str,
                    "url": full_url,
                    "source": "afisha.uz (calendar)",
                })
                appended_this_url += 1
            if is_target:
                log(f"  TARGET APPENDED {appended_this_url} entries to events[] for {full_url}, events[] length now {len(events)}")
        except Exception as e:
            if is_target:
                log(f"  TARGET EXCEPTION during translate/append for {full_url}: {type(e).__name__}: {e}")
                continue
            else:
                raise
        if not in_window:
            log(f"  {clean_title!r}: no dates found in window (no schedule/date-range parsed) -- kept 0 events")

    log(f"calendar detail pass complete: {len(events)} events kept from {len(discovered)} pages checked")
    return events


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
                if slug == "concerts":
                    with open("scripts/debug-iticket-uz.html", "w", encoding="utf-8") as f:
                        f.write(page.content())
                anchors = page.query_selector_all(f"a[href*='/en/events/{slug}/']")
                count = 0
                seen = set()
                for a in anchors:
                    href = a.get_attribute("href") or ""
                    if not href or href in seen or href.rstrip("/").endswith(f"/{slug}"):
                        continue
                    text = (a.inner_text() or "").strip().replace("\n", " ")
                    if not text or len(text) < 3:
                        continue
                    if is_excluded(text):
                        continue
                    # format: "from 200 000 UZS BY ИНДИЯ 01 August 2026 • BlaBlaBar"
                    m = re.match(
                        r"(?:from\s+[\d\s]+UZS\s+)?(.+?)\s+(\d{1,2}\s+[A-Za-z]+\s+\d{4})\s*(?:•\s*(.+))?$",
                        text,
                    )
                    if not m:
                        continue
                    title_raw, date_str, venue = m.group(1), m.group(2), m.group(3)
                    try:
                        start = datetime.strptime(date_str, "%d %B %Y")
                    except ValueError:
                        continue
                    if start > WINDOW_END or start < TODAY_MIDNIGHT:
                        continue
                    if is_generic_venue_listing(title_raw.strip(), venue):
                        continue
                    if is_blacklisted_venue(venue):
                        continue
                    full_url = href if href.startswith("http") else f"https://iticket.uz{href}"
                    seen.add(href)
                    events.append({
                        "title": title_raw.strip(),
                        "titleRu": None,
                        "category": guess_category(title_raw, category),
                        "venue": venue.strip() if venue else None,
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

        # --- ticketon.uz: CONFIRMED (2026-08-02) behind a Cloudflare "Just a moment..."
        # JS-challenge page. This is not simple bot detection -- it's an active
        # challenge that a plain headless browser cannot pass. Getting past it
        # would require stealth-automation tooling and/or paid CAPTCHA-solving
        # services, which is not appropriate to build into this. Left in place
        # as a monthly-recheck in case the site changes its protection, but do
        # not expect this to ever return events without a different approach. ---
        page = context.new_page()
        try:
            page.goto("https://ticketon.uz/en/tashkent", wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(3000)
            title_check = page.title()
            if any(w in title_check.lower() for w in ("just a moment", "access denied", "blocked", "verification")):
                log(f"ticketon.uz: CONFIRMED BLOCKED (Cloudflare challenge, page title='{title_check}'). "
                    f"0 events -- this is expected, not a bug to fix.")
            else:
                with open("scripts/debug-ticketon-uz.html", "w", encoding="utf-8") as f:
                    f.write(page.content())
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
                    if start > WINDOW_END or start < TODAY_MIDNIGHT:
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
            with open("scripts/debug-eventbrite.html", "w", encoding="utf-8") as f:
                f.write(page.content())
            page_title = page.title()
            if "verification" in page_title.lower() or "human" in page_title.lower():
                log(f"eventbrite.com: CONFIRMED BLOCKED (AWS WAF CAPTCHA, page title='{page_title}'). "
                    f"0 events -- this is expected, not a bug to fix. Same category of block as ticketon.uz.")
                anchors = []
            else:
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
    # Pass 1: exact-prefix dedup (same as before, catches identical-source repeats)
    seen = {}
    stage1 = []
    for e in events:
        is_target = "our-heritage" in e.get("url", "") or "photo-chronicle-xx" in e.get("url", "")
        if not e.get("startDate"):
            if is_target:
                log(f"  DEDUP TARGET DROPPED (no startDate): {e.get('url')}")
            continue  # can't place it on the 7-day list without a date
        key = (norm(e["title"])[:40], e["startDate"])
        if key in seen:
            if is_target:
                log(f"  DEDUP TARGET DROPPED at Pass 1 (key collision): {e.get('url')} key={key!r}")
            continue
        seen[key] = True
        stage1.append(e)
    target_count_after_pass1 = sum(1 for e in stage1 if "our-heritage" in e.get("url","") or "photo-chronicle-xx" in e.get("url",""))
    log(f"  DEDUP: {target_count_after_pass1} target entries survived Pass 1")

    # Pass 2: same-date fuzzy dedup across sources. Cross-source titles are often
    # in different languages (afisha.uz = Russian, iticket.uz = English), so we
    # can't rely on general string-similarity -- but Latin brand/artist names are
    # frequently left untranslated in both, so a shared substring of 8+ chars is
    # a reliable signal ("NE PROSTO CARTOONS", "IOSIS ROCK BATTLE", "Amirsoy
    # Paradiso" all matched this way). Full Cyrillic-transliteration mismatches
    # (e.g. "ABDIZHAPPAR ALKOZHA" vs "Абдижаппара Алкожи") are NOT caught by
    # this -- that needs a transliteration library, which isn't in place, so
    # those will still appear as separate entries. Flagging, not hiding, that gap.
    by_date = {}
    for e in stage1:
        by_date.setdefault(e["startDate"], []).append(e)

    SOURCE_PRIORITY = {"iticket.uz": 0, "afisha.uz": 1}  # lower = preferred when merging

    # Real bug found via debugging Madagascar/Qimmat directly: many event
    # titles embed their own venue name (e.g. "Спектакль «Мадагаскар» в парке
    # Tashkent City"), so two completely UNRELATED events sharing a popular
    # venue on the same date (Tashkent City park hosts many different things)
    # were sharing "Tashkent"+"City" as a "matching" Latin token and getting
    # wrongly merged by this dedup step -- one of the two silently discarded
    # even though they were different events. Generic venue/location words
    # aren't a reliable distinguishing signal and must not count here.
    VENUE_WORD_STOPLIST = {
        "tashkent", "city", "park", "uzbekistan", "center", "centre",
        "gallery", "hall", "theatre", "theater", "hotel", "resort",
    }

    def latin_tokens(title, min_len=8):
        return {w for w in re.findall(r"[A-Za-z]{3,}", title)
                if len(w) >= 3 and w.lower() not in VENUE_WORD_STOPLIST}

    final = []
    for date, group in by_date.items():
        merged_out = []
        used = [False] * len(group)
        for i, e1 in enumerate(group):
            if used[i]:
                continue
            cluster = [i]
            t1 = latin_tokens(e1["title"])
            for j in range(i + 1, len(group)):
                if used[j]:
                    continue
                e2 = group[j]
                # Real bug found via direct debugging (2026-08-10): this fuzzy match was
                # firing between two completely unrelated SAME-SOURCE afisha.uz events that
                # both got auto-translated to English and happened to share one generic
                # word (e.g. "Heritage") -- "Наше наследие" ("Our Heritage") and some
                # unrelated event both translating through a shared common word, at 8+
                # combined chars, was enough to wrongly merge them and silently drop one.
                # This whole fuzzy pass exists to catch the SAME event listed by TWO
                # DIFFERENT sources in two different languages (afisha.uz Russian vs
                # iticket.uz English) -- comparing an afisha.uz entry against another
                # afisha.uz entry was never the intent, and both are already Russian-
                # sourced, so genuine untranslated-brand-name matches (the actual intended
                # signal) can't occur between them anyway. Skip same-source comparisons
                # entirely rather than trying to raise the length threshold, which would
                # just shift the false-positive rate rather than fix the actual mismatch
                # between what this check is comparing and what it was built to catch.
                if e1.get("source") == e2.get("source"):
                    continue
                t2 = latin_tokens(e2["title"])
                shared = t1 & t2
                shared_len = sum(len(w) for w in shared)
                if shared and shared_len >= 8:
                    cluster.append(j)
            for idx in cluster:
                used[idx] = True
            # keep the best-priority source in the cluster
            best = min((group[idx] for idx in cluster),
                       key=lambda e: SOURCE_PRIORITY.get(e["source"], 9))
            merged_out.append(best)
        final.extend(merged_out)

    target_count_final = sum(1 for e in final if "our-heritage" in e.get("url","") or "photo-chronicle-xx" in e.get("url",""))
    log(f"  DEDUP: {target_count_final} target entries survived Pass 2 (final)")
    return final


def main():
    all_events = []
    log(f"Run started. Today (Tashkent): {TODAY.date()}  Window end: {WINDOW_END.date()}")
    log("NOTE: switched from per-category listing pages to afisha.uz's calendar view "
        "(one real day at a time, via Playwright) -- the old approach could only ever "
        "see whatever was on the current front page of each category, missing anything "
        "pushed down by newer posts. The calendar view asks for each specific date "
        "directly instead.")

    try:
        all_events += scrape_afisha_calendar()
    except Exception as e:
        log(f"afisha.uz calendar: TOTAL FAILURE ({e})")

    try:
        all_events += scrape_special_tracked_events()
    except Exception as e:
        log(f"special-tracked events: TOTAL FAILURE ({e})")

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
