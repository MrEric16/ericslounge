# Mr Eric's Lounge — Handover Notes

Read this before making any changes. It exists because a lot of decisions in this
codebase look arbitrary from the code alone but were made deliberately, often after
trying the "obvious" approach first and having it rejected or breaking something.

## The basics

- **Live site:** https://mreric16.github.io/ericslounge/
- **Repo:** MrEric16/ericslounge, main file: `index.html` (everything — HTML, CSS, JS —
  lives in one file, no build step, no framework)
- **Other key files:** `manifest.json`, `icon-192.png`, `icon-180.png`, `icon-512.png`
  (Add to Home Screen support), `sw.js` (offline service worker), `review.html`
  (private admin app — see Review App section), `dashboard.html` (private analytics
  dashboard — see Analytics Dashboard section), `manifest-dashboard.json` (separate
  PWA manifest so the dashboard installs as its own distinct home-screen app, "Lounge
  Stats", not confused with the main site)
- **Scripts:** `scripts/scrape_khl.py`, `scripts/fetch_football_data.py`,
  `scripts/scrape_goal_results.py`, plus scrapers for weather, UAE league, UFC, UZ
  league, Tashkent events — all run via GitHub Actions workflows in
  `.github/workflows/`, each on its own schedule (check the workflow YAML for exact
  cadence, e.g. KHL is every 6 hours).
- **Architecture:** static SPA, hash-based routing (`#faculty/science`, `#watch/sport`,
  `#play/<videoId>`, etc.), everything rendered client-side from JS data arrays baked
  into the file.
- **GitHub PAT:** the person will provide this at the start of a session. **Do not
  attempt to recall or reconstruct a PAT from memory across a long conversation or a
  new session — this failed badly once already** (see "Hard lessons" below). If it
  isn't in context, ask for it plainly rather than guessing.
- **Push workflow for `index.html`:** base64-encode, PUT to GitHub Contents API.
  ALWAYS `node --check` the extracted `<script>` block before pushing. **Every push
  that changes `index.html` must bump BOTH `version.json` and
  `<meta name="app-build">` to the same fresh timestamp** — this is the PWA
  update-detection pair; a push with only one updated silently breaks auto-update
  for installed users. After every push, byte-diff verify by re-fetching the raw
  content via `api.github.com/.../contents/index.html` (not
  `raw.githubusercontent.com`, which has its own separate cache) and comparing to
  what was sent — don't just trust the API's 200 response.
- **Fetch the current HEAD SHA immediately before every commit** — bot/workflow
  commits (scrapers) land constantly and will cause stale-SHA conflicts otherwise.
- **This is a hash-routed single-page app.** Navigating between sections does NOT
  reload the page. If a fix "isn't working," check whether the person has actually
  reloaded — not just navigated.
- **Home screen app caching:** once installed, iOS caches separately from Safari.
  Force-quit + reopen picks up changes.
- **Service worker is network-first, not cache-first** (`sw.js`,
  `CACHE_NAME = 'erics-lounge-v2'`). Do not revert to cache-first — that was a real,
  previously-fixed bug (stale copy served on first load after every push).

## Hard lessons from this session — read before touching live data or claiming a fix works

- **Never claim something is fixed without testing it against real, live data
  first.** This session had a long, painful stretch where the analytics dashboard was
  fixed three separate times (RLS policy, then pagination logic, then a second
  pagination bug, then a caching issue) because each "fix" was asserted without
  actually re-running it against the real dataset at real scale. The lesson that
  stuck: after any fix to a data-fetching or data-processing path, simulate it
  against a *realistic reproduction of the actual failure*, not just a clean happy-
  path mock — the bugs that actually bit were edge cases (server response caps below
  the requested page size, RLS applying to `anon` but not `authenticated` roles,
  Cyrillic characters counting as `\w` in Python regex so `\b` boundaries silently
  never matched, a leading timestamp being greedily matched by a score-shaped regex)
  that a clean-case test would never have caught.
- **Don't try to reconstruct a lost secret (API key, PAT) from memory.** If it's not
  visibly in context, ask for it again rather than guessing repeatedly — guessing
  burns turns and looks worse than just asking.
- **When told to "make absolutely sure" something is correct, actually re-verify from
  scratch — don't just re-assert the previous answer.** The Chess-tracking-bug
  investigation is the model to follow: re-ran the actual query against live data
  before concluding, then traced the exact code path (not just the data) to find why
  it was zero, rather than assuming the earlier explanation still held.
- **When fixing one instance of a bug, check whether the same bug pattern exists
  elsewhere before calling it done.** The Arsenal fixture fix is the model: the
  actual reported bug (Ipswich sorting before Napoli) was one wrong hardcoded date,
  but while fixing it, a second class of bug was found (static fallback fixture
  titles like "SSC Napoli"/"LOSC Lille"/"FC Bayern München" didn't match the live
  API's actual `shortName` field, e.g. "Napoli"/"Lille"/"Bayern" — silently breaking
  the live/static duplicate-detection). Both Lille's and Bayern's entries were fixed
  preemptively even though neither had been reported yet, since the pattern was now
  understood.

## Deliberate decisions — do not "fix" these

- **Wind speed is in knots**, not km/h or mph. Open-Meteo call uses
  `&wind_speed_unit=kn` — don't strip it.
- **No video autoplay, anywhere, on purpose.** Tried extensively and abandoned —
  browser autoplay-with-sound policies can't be reliably overridden. Tapping a
  thumbnail plays it; that's the whole mechanism. Don't reintroduce autoplay
  attempts.
- **`--ink` vs `--ink-solid` are two different CSS variables on purpose** (dark-mode
  contrast). Merging them broke dark mode once already.
- **On This Day pulls live from Wikipedia's API**, filtered by keyword lists to avoid
  war/death/violence content, with a curated fallback pool only if the live fetch
  fails. Must stay genuinely date-accurate — don't silently swap to a static pool.
- **Random Fact, Word of the Day, and On This Day are three separate systems** with
  different update rules (fully random / deterministic-by-day-of-year / live-fetched)
  — don't conflate them.
- **No emoji for site iconography except a specific whitelist** — nearly everything
  is hand-drawn SVG line icons (`ICONS`/`FLAGS` objects, `icon()`/`flagIcon()`
  helpers) because emoji "looked too AI." Kept as literal emoji on purpose (flavor,
  not interface): Arsenal's trophy/Tottenham's poo emoji in league tables, confetti,
  achievement toasts.
- **Flags are hand-built SVGs, not emoji** (`FLAGS` object) — no copyright issue,
  more accurate. Don't revert.
- **No real photos/logos for trophies, crests, or club badges** beyond the Arsenal
  crest (one explicitly-approved exception). Copyright-risk reasoning — use original
  SVG line art unless the person explicitly provides/approves an image.
- **Scroll position is remembered per-hash across navigation**
  (`scrollMemory`/sessionStorage). Don't reintroduce unconditional
  `window.scrollTo(0,0)` in `renderRoute()`.
- **Swipe navigation is cyclable, wraps at the ends**, via `wireSwipeNav()` with two
  independent groups (`DOMAIN_SWIPE_ORDER`, `HUB_SWIPE_ORDER`). Don't merge them.
- **Birthdays render one name per line (vertical stack), never comma/dot-joined on
  one line.** Applies to both the "today is X's birthday" popup/tile state and the
  "upcoming" tile state — this is a fixed pattern, apply it to any future birthday
  display too.
- **Bottom nav labels are "Mind" and "Brain"** (not "Temple"/"Train" — renamed this
  session). Routes unchanged (`#mind-temple`, `#geography-hub`), only the visible
  labels changed.
- **QA/questions section shows "Name:" or "Anonymous:", never "Name asked:"** —
  submissions aren't always phrased as questions, so "asked" was wrong. Fixed this
  session; keep this pattern for any similar display.
- **Birthdays and Superstars are the two "recurring, mostly-autonomous" systems** —
  see their own section below.

## Sports data — KHL and football ARE now genuinely live-scraped (this changed this session)

The old handover said sports tables were manual-only because live-data sites block
scraping. **That's now only true for some leagues, not all.** This session built
working live scrapers for KHL and confirmed football-data.org's API integration
works well. Don't assume "manual only" applies broadly without checking
`scripts/` first.

### KHL (`scripts/scrape_khl.py`)
- **Standings**: scraped from khl.ru directly via Playwright, confirmed reliably
  working, 22 teams. **Points are computed, not scraped** — khl.ru's own displayed
  points figure was unreliable early in the season (returned impossible values like
  0 points for a team with 2 wins). Points are calculated from the independently-
  verified win/loss breakdown using the real KHL formula: 2 points for any win
  (regulation/OT/shootout), 1 point for an OT/shootout loss, 0 for a regulation
  loss. Don't switch back to trusting a raw scraped points cell without re-verifying
  it against this formula first.
- **Fixtures/results**: scraped from **liveresult.ru** (switched from Flashscore and
  365scores, both dead ends — 365scores served stale prior-season data; Flashscore's
  date text couldn't be reliably isolated per match after seven diagnostic rounds).
  liveresult.ru is server-rendered, has clean `/results` and `/scheduled` pages, and
  each match is one `<a>` tag containing team slugs (in the URL) plus a leading
  kickoff time and (for finished games) a score in the link text. Team name mapping
  is a fuzzy alias table (`LIVERESULT_TEAM_ALIASES`) — not every real team's exact
  slug has been observed, so matching falls back to substring comparison. **Known
  real bugs already fixed, don't reintroduce:** Python's `\b` regex boundary treats
  Cyrillic letters as `\w`, so a date immediately followed by Cyrillic text (no
  space) never matched with a literal `\b` — fixed with `(?<!\d)...(?!\d)` lookarounds
  instead. The leading kickoff time (e.g. "10:00") has the same N:N shape as a score
  and was being caught by the score regex before the real score later in the string
  — fixed by matching the leading time first and only searching the remainder for a
  score. `UHC_Dinamo` is Dynamo **Moscow**'s slug, not Dinamo Minsk's, despite
  sounding closer to Minsk — confirmed via a real match result, don't re-flip this.
- **Conference split**: KHL standings render as two separate tables, Western and
  Eastern Conference (`KHL_WESTERN_TEAMS` set in `index.html`), confirmed against
  the real current 2026-27 season divisions via Wikipedia. Applies regardless of
  whether the underlying data is live or static.
- **Known open item, not yet fixed:** a small number of teams occasionally show
  `played: 0` alongside a non-zero `won` count — likely a timing artifact from the
  scraper running right as a game finishes. Lower severity than the points bug, not
  yet investigated.

### Football (`scripts/fetch_football_data.py`, `scripts/scrape_goal_results.py`)
- Fixtures/results come from the football-data.org API (server-side, key not
  exposed client-side). Scorer detail (goal-scorer popups) comes from a separate
  goal.com scraper (Arsenal + 10 other big clubs get dedicated team-page scraping,
  plus league-wide scraping for everyone else).
- **Real bug fixed this session, don't reintroduce:** the scorer-popup matching
  logic required an *exact* date match between the football-data.org result and the
  goal.com-scraped result. But ~20-30% of goal.com's own league-page match reports
  don't expose a parseable date on their page at all (`date: null` in the scraped
  data) — those could never produce a popup no matter how good the scorer data was.
  Fixed with a fallback match path: when the exact date+team-name match misses, try
  team-name+final-score instead (`findGoalResultDetail()` in `index.html`). This
  sidesteps needing a reliable date from goal.com for that fallback case entirely.
- **Arsenal fixture order bugs fixed this session** (see `ARSENAL_FIXTURES` array):
  a hardcoded Carabao Cup date for Ipswich was wrong by six days, which sorted it
  ahead of Napoli in the "next fixtures" list — the actual reported bug. While
  fixing it, found and fixed a second, broader bug: several static fallback fixture
  titles used full club names ("SSC Napoli", "LOSC Lille", "FC Bayern München")
  that don't match football-data.org's real `shortName` field ("Napoli", "Lille",
  "Bayern") — this silently broke the live/static duplicate-detection logic
  (`arsenalFixtureOpponent()`), which could let both a live and a stale static
  entry for the same match appear at once. Verify against the real API's
  `shortName` field (not the display name) whenever adding a new opponent to this
  array.

## Analytics Dashboard (`dashboard.html`) — private, separate PWA

Built this session: a private usage-analytics dashboard, separate from the main
site, gated by the same Supabase Auth login used by `review.html`. Installs to
Home Screen as its own distinct app ("Lounge Stats") via `manifest-dashboard.json`.

- **Data source:** the `analytics_events` table (Supabase), the same table the main
  site's own public `#stats` page reads anonymously — **don't tighten that table's
  RLS to authenticated-only without also fixing (or breaking) the public page**.
  Current policy: `anon` can SELECT (needed by the public page) *and*
  `authenticated` can SELECT (needed by the dashboard) — both policies coexist,
  neither replaces the other. This was broken once this session by replacing the
  anon policy instead of adding an authenticated one alongside it.
- **Fetching all rows requires pagination — Supabase/PostgREST caps a single
  `select()` at 1000 rows by default**, and this table has 44,000+ rows and
  growing. The working pattern (see `fetchAllAnalyticsEvents()`): get the exact row
  count first via a `{ count: 'exact', head: true }` request, then fire every
  needed page **in parallel** (not sequentially — sequential pagination over 40+
  pages is slow enough to look hung), and wrap the whole thing in a hard timeout
  (25s) so a genuine hang produces a visible error instead of an endless spinner.
  Do not go back to sequential one-page-at-a-time fetching or to comparing
  received-length against requested-length to decide when to stop (both were real,
  separately-diagnosed bugs this session).
- **No-cache headers are set in the `<head>`** — a saved Home Screen install can
  serve a stale cached version otherwise; this bit once this session too.

## Content Review System — `pending_content` table + Review App Content tab

Built this session for sourcing new articles/videos. **This is a real pipeline now,
not a one-off.**

- **Table:** `pending_content` (Supabase) — columns: `content_type`
  ('article'/'video'), `title`, `url`, `source`, `faculty`, `description`, `status`
  ('pending'/'approved'/'rejected'). RLS: `authenticated` can SELECT/UPDATE/DELETE;
  inserts happen via direct SQL (same pattern as `pending_events`), not through a
  public-facing form.
- **Review App tab:** `review.html` has a "Content" tab, mirroring the existing
  "Events" tab pattern exactly (same `pevent-*` CSS classes reused). Approve marks
  `status = 'approved'` — **there is no automated publish-to-site step for content**
  (unlike events, which have a scheduled workflow). Approved rows need to be added
  to the site's actual `WATCH_VIDEOS`/article arrays by hand from here.
- **Content rules, must be followed for every new item sourced:**
  - No LGBT content, no anti-religion content, no anti-establishment content.
  - No WIRED as a source (hard rule) — **unless the person explicitly overrides it**
    (happened once this session, for a specific Pyramids video, placed in History).
  - No "100 Questions for [profession]" video format.
  - Must be genuinely educational/engaging, not clickbait or a generic hub/category
    page — every article/video must link to one specific, individually verifiable
    piece of content, never a channel page, playlist, search-results page, or
    course-listing page.
  - No duplicate source/channel within the same faculty domain, **and no duplicate
    URL anywhere in the whole file** (not just within one domain) — checked this
    session and found real violations (a channel-suggested spreadsheet reused the
    exact same article across two different domains).
  - Current politics involving a real, contested, currently-sitting figure is
    excluded by default (a Bukele "dictator" video and a "petrodollar geopolitics"
    video were both excluded this session on this basis) — the person can override
    per-item if they choose to.
- **Verifying videos is the hard part.** Plain web search reliably finds article
  URLs but does NOT reliably surface a specific `youtube.com/watch?v=` URL from a
  channel-name search — it surfaces channel pages, playlists, and stats sites
  instead. **Working techniques found this session, use these:**
  1. Search the bare video ID in quotes (`"abc123XYZ0"`) — sometimes surfaces a
     direct citation (course syllabus, Wikipedia reference, forum post) confirming
     title+channel.
  2. **TheTVDB.com** catalogs YouTube channels as "series" with individual videos as
     "episodes," and its "Production Code" field is literally the real YouTube
     video ID for many science/education channels (confirmed working for
     Veritasium, Kurzgesagt, "Be Smart"/"It's Okay to Be Smart"). Search
     `thetvdb.com "<channel name>" "<video title>"`.
  3. A specific-topic query (not a channel-name query) is more likely to surface a
     direct `watch?v=` link on page one than searching by channel name alone.
  4. If a title has near-duplicate versions across many channels (common for viral
     psychology/self-help topics), match on the *exact* title string the person
     gave, not just the general topic.
  5. **Never fabricate or guess a video ID.** If it can't be verified, say so and
     leave it out rather than include an unconfirmed one — this was tested and
     confirmed to be the right call (ChatGPT, asked to do the same sourcing task
     independently, refused to fabricate unverified rows rather than pad a
     spreadsheet — that was the correct behavior, not a failure).

### Existing sources per domain (as of this session — regenerate before reusing,
this list is now stale the moment more content gets approved)

Approved/live sources are baked into `index.html`'s `WATCH_VIDEOS` and article
arrays directly — extract fresh via:
```
grep -o 'faculty:"<domain>"' index.html   # then trace back to source/channel fields
```
**Also check `pending_content` for sources already queued but not yet approved** —
avoid re-suggesting those too. As of this session's end, still-pending (not yet
approved) items exist in: biochem (2 articles), psychology (1 article's worth of
videos — "irrational decisions" TED-Ed, "12 Cognitive Biases", "Dunning-Kruger",
"Placebo Effect"), science (2 articles: ScienceDaily, Sci.News). Query
`pending_content` directly for the current exact state rather than trusting this
snapshot.

### Domain-by-domain sourcing progress (16 domains total: science, biochem,
economics, business, it, technology, design, languages, jurisprudence,
international-relations, psychology, history, travel, sport, space-astronomy,
aviation — target 5 articles + 5 videos each)

- **Science: COMPLETE** — 5/5 articles, 5/5 videos, all approved.
- **Biochem: partial** — 2/5 articles pending approval, 0/5 videos found yet.
- **Psychology: partial** — videos at 4/5 approved + 1 more from the person's own
  list still pending your own further additions if any; 0/5 articles sourced yet
  (only got videos so far, from the person providing titles/channels directly).
- **Economics, business, it, technology, design, languages, jurisprudence,
  international-relations, history, travel, sport: each has exactly 1 item** (from
  the person's original 15-video list), nowhere near 5+5.
- **Space-astronomy, aviation: 0 items sourced yet this round.**
- Given how slow per-video verification is (often 3-5+ searches per video, and
  sometimes still fails), doing this properly is realistically many more sessions
  of work, not a single push — say so plainly if asked to "just finish it."

## Content submission model — nothing here is truly autonomous (except what's noted)

There is no user-facing upload form. Every piece of "changing" content (Photo
Library images, Superstars photos, Birthdays list additions/corrections) is added
by *the person sending Claude the content in chat*, committed manually each time —
this is by design, not a limitation. What genuinely runs itself once seeded:
weather (live API), On This Day (live API), Random Fact / Word of the Day (large
static pools), birthday tile auto-advance/popup, Arsenal fixture auto-advance,
**and now KHL + football data (see above) — this is new as of this session.**
Content review (articles/videos) has a real pipeline now (see above) but still
requires a human approval step and manual publish — not fully autonomous.

## Feature-specific notes

- **Weather:** Tashkent + 2 random cities from `WORLD_CITIES`, reshuffled every
  visit. Tashkent always first, hardcoded separately.
- **Superstars (`STUDENTS_OF_WEEK`):** `{name, note, photoUrl, date}`. Date = the
  date it's added (confirmed with the person), `dd/mm/yyyy`. Homepage shows most
  recent only; `#superstars` shows full archive, newest first.
- **Birthdays (`BIRTHDAYS`):** `{name, month, day}`, no year, recurring annually.
  Popup fires on every app load (not once/day) via `checkTodaysBirthdays()`.
  Display is one-name-per-line vertically (see Deliberate Decisions above).
- **Chess (arcade overlay) had broken view-tracking, fixed this session:** Chess is
  the only game whose `<a href="#arcade/chess">` click handler calls
  `e.preventDefault()` and never actually changes `location.hash` (needed for the
  overlay's swipe-back behavior). The generic page-view logger reads
  `location.hash` to know what to log, so every Chess open was being silently
  miscounted under whatever page the user opened it from — confirmed via direct
  query showing zero real Chess events despite real reported play. Fixed by adding
  an explicit `logEvent('view', 'arcade/chess')` call at the actual click point.
  This does NOT retroactively fix past data — the counter is only accurate from
  this push onward.
- **Color palette:** "Maroon & Gold Library." Generate mockups first if asked to
  change again, don't apply directly to the live site.
- **Dark mode:** persists via `localStorage('darkMode')`. Remember `--ink`/
  `--ink-solid` split.
- **Video additions (general, non-domain-content):** always verify the exact
  current YouTube video ID + channel before adding, never guess from memory.

## Standing tone/workflow preferences

- Brutally honest, no softening, no over-apologizing. This person will push back
  hard — and has, repeatedly, this session — if told something works without it
  actually being verified against live/real data first.
- Match response length to the question; don't pad. When told explicitly to be
  terse, actually cut to a handful of words per reply until told otherwise.
- Verify before stating, especially for live/technical claims.
- No exam-prep branding anywhere on the site (general fluency/speaking focus, not
  IELTS/TOEFL prep).
- When something breaks or turns out wrong, own it plainly and fix it — don't be
  vague or defensive.
- The person tests things themselves (screenshots included) and will call out a
  claimed fix that isn't actually fixed — treat every "it's fixed" statement as one
  that needs to survive that scrutiny before saying it.
