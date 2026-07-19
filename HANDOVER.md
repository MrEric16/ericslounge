# Mr Eric's Lounge — Handover Notes

Read this before making any changes. It exists because a lot of decisions in this
codebase look arbitrary from the code alone but were made deliberately, often after
trying the "obvious" approach first and having it rejected or breaking something.

## The basics

- **Live site:** https://mreric16.github.io/ericslounge/
- **Repo:** MrEric16/ericslounge, single file: `index.html` (everything — HTML, CSS, JS —
  lives in one file, no build step, no framework)
- **Other repo files:** `manifest.json`, `icon-192.png`, `icon-180.png`, `icon-512.png`
  (Add to Home Screen support — icon is a maroon/gold "Mr E." badge, matches current
  palette), `sw.js` (offline service worker, see below)
- **Architecture:** static SPA, hash-based routing (`#faculty/science`, `#watch/sport`,
  `#play/<videoId>`, etc.), everything rendered client-side from JS data arrays baked
  into the file
- **Push workflow:** base64-encode the file, PUT to GitHub Contents API with the
  person's PAT (stored in their Notes app, not repeated here for safety). Always
  `node --check` the extracted `<script>` block before pushing — this file is large
  and manual edits break syntax easily. Playwright is available in this environment —
  use it. Loading the actual file in a real headless browser and checking the DOM/
  console catches real bugs that `node --check` alone won't (e.g. a JS syntax check
  won't catch a timezone bug or a broken click handler). This was the difference
  between shipping and not shipping several fixes this session — use it before every
  push, not just for big changes.
- **GitHub Pages caching:** the CDN can lag a few seconds to minutes behind a push.
  Verify via `api.github.com/repos/.../contents/index.html` (raw content), not
  `raw.githubusercontent.com` (has its own separate short cache).
- **This is a hash-routed single-page app.** Navigating between sections does NOT
  reload the page or re-fetch index.html. If the person says a fix "isn't working,"
  the first thing to check is whether they've actually reloaded — not just navigated
  — since old JS can stay running in memory indefinitely otherwise.
- **Home screen app caching:** once installed via Add to Home Screen, iOS caches it
  separately from Safari. Force-quit + reopen picks up changes; full reinstall is
  only needed as a last resort.
- **Service worker is network-first, not cache-first.** (`sw.js`, `CACHE_NAME =
  'erics-lounge-v2'`.) This was a real bug found and fixed: an earlier cache-first
  version served the *stale* cached copy on the first load after every push, only
  updating in the background for next time — meaning every single push looked
  broken for one load. Network-first fixes that: always fetches fresh when online,
  cache is purely an offline fallback. If offline support is ever touched again,
  do not revert to cache-first — that regression is subtle and easy to reintroduce
  by accident.

## Deliberate decisions — do not "fix" these

- **Wind speed is in knots**, not km/h or mph. Open-Meteo call uses
  `&wind_speed_unit=kn` — don't strip it.
- **No video autoplay, anywhere, on purpose.** This was tried extensively (muted
  autoplay, playsinline, YouTube postMessage API) and ultimately abandoned because
  browser autoplay-with-sound policies can't be reliably overridden, and forcing
  muted autoplay just adds a confusing extra unmute step. Current state: tapping a
  thumbnail plays it (real user gesture = sound works). "Watch Something Random"
  goes to a dedicated single-video page (`#play/<id>`) with no autoplay attribute —
  it's a real tap on a real play button, nothing fancier. Don't reintroduce
  autoplay attempts; it was a deliberately closed dead end.
- **`--ink` vs `--ink-solid` are two different CSS variables on purpose.** `--ink` is
  the main text color and flips in dark mode. `--ink-solid` is used for elements
  that are meant to always be dark regardless of theme (Watch tile, All Domains tile,
  day badges, On This Day/Random Fact/Weather/Word of the Day/upcoming-birthday
  cards — all white text on a dark chip). Merging them back into one variable will
  break dark mode contrast — this exact bug happened once already.
- **On This Day pulls live from Wikipedia's API**, filtered by keyword lists
  (`OTD_NEGATIVE_KEYWORDS` / `OTD_POSITIVE_KEYWORDS`) to avoid war/death/violence
  content, with a curated 34-entry fallback pool only used if the live fetch fails
  or returns nothing safe. This was flip-flopped once already — don't silently swap
  it back to a static pool without asking. It must be genuinely date-accurate.
- **Random Fact, Word of the Day, and On This Day are three separate systems with
  different update rules** — don't conflate them:
  - Random Fact: 300 entries, genuinely random every visit, not date-tied.
  - Word of the Day: 365 entries, deterministic by day-of-year (`dayOfYear %
    pool.length`), same word for everyone all day, changes at midnight.
  - On This Day: live-fetched, real date accuracy (see above).
- **No emoji for site iconography, except a specific whitelist.** Nearly every emoji
  used for navigation/branding was replaced with hand-drawn SVG line icons (see
  `ICONS` and `FLAGS` objects near the top of the script, plus `icon('name')` and
  `flagIcon('name')` helper functions) because emoji "looked too AI." This includes
  the reaction buttons (like/surprised/thinking, stored internally as semantic
  string keys, not raw emoji) and the bookmark toggle (outline vs filled). Kept as
  literal emoji, on purpose, because they're personality/flavor, not interface
  icons: Arsenal's trophy and Tottenham's poo emoji in league tables, confetti
  particles, and achievement-toast easter eggs. If asked to add a new icon, follow
  the existing pattern in `ICONS` (stroke-based, `currentColor`, minimal line art)
  — don't reach for an emoji by default.
- **Flags are hand-built SVGs, not emoji** (`FLAGS` object) — flags carry no
  copyright, so this was safe to do accurately. Same reasoning: don't revert to flag
  emoji.
- **No real photos/logos for trophies, crests, or club badges** beyond the Arsenal
  crest (one exception, explicitly approved, sourced from a logo site). General rule:
  real team logos/photos are copyright-risky to source and embed at scale, so
  anything needing iconography for a team/league/trophy uses original SVG line art
  instead, unless the person explicitly provides/approves an image.
- **Scroll position is remembered per-hash across navigation** (`scrollMemory` in
  sessionStorage, keyed by hash, saved/restored in `renderRoute()`). Don't
  reintroduce an unconditional `window.scrollTo(0,0)` at the end of `renderRoute()`
  — that was the exact bug this fixed.
- **Swipe navigation is cyclable in both directions**, wraps around at the ends.
  Two independent swipe groups via the shared `wireSwipeNav()` helper:
  `DOMAIN_SWIPE_ORDER` (the 12 faculties, used inside `#faculty/<key>` and
  `#watch/<key>` sub-pages) and `HUB_SWIPE_ORDER` (`watch`, `photos`, `sports`,
  `saved`, `superstars`, `birthdays` — the six top-level hub pages). Don't merge
  these into one order; they're semantically different levels of navigation.
- **Arsenal fixtures are a real list (`ARSENAL_FIXTURES`), not a single hardcoded
  string.** `getNextArsenalFixture()` auto-selects whichever fixture is soonest
  still in the future — no manual "next fixture" updates needed until the list
  itself runs out. **Important gotcha already fixed once:** display the fixture
  date/time by parsing the ISO string's wall-clock components directly (see
  `arsenalFixtureText()`), never via `Date.getHours()`/`getDay()` etc. — those
  return the *viewer's own device timezone*, not Tashkent's, which would silently
  show the wrong kickoff time to anyone outside Tashkent despite the "(Tashkent
  time)" label. Re-sync fixtures from the person's Arsenal FC calendar subscription
  (`calendar_search_v0`/`event_search_v0` tools) whenever the list runs low or is
  asked for — note these calendar tools have intermittently timed out in this
  session; retry once or twice before concluding they're broken.
- **Birthdays and Superstars are the two "recurring, mostly-autonomous" systems** —
  see their own section below, don't treat them as static content.

## Content submission model — nothing here is truly autonomous

There is no backend, no database, no user-facing upload form, and the shared
key-value storage API available to artifacts has been unreliable in this session
whenever tried — don't assume it works. Every piece of "changing" content
(Photo Library images, Superstars photos, Birthdays list, league table refreshes,
new videos/articles) is added by *the person sending Claude the content in chat*,
and Claude manually commits it to the repo each time. This is expected and by
design, not a limitation to solve — just don't imply to the person that any of
this updates itself from an external source. The things that genuinely run
themselves with zero further input once seeded: weather (live API), On This Day
(live API), Random Fact / Word of the Day (large static pools, rotate on their
own), the birthday tile's auto-advance + popup logic, and the Arsenal fixture
auto-advance — all of those are real automation. Photos, names, and league
standings are not, and can't be without a live sports-data API (see below).

## Feature-specific notes

- **Weather:** Tashkent + 2 random cities from `WORLD_CITIES` (all ~186 world
  capitals), reshuffled every visit. Tashkent is always first and hardcoded
  separately — don't fold it into the random pool.
- **Superstars (`STUDENTS_OF_WEEK`):** each entry `{name, note, photoUrl, date}`.
  Date is recorded as *the date it's added to the site* (confirmed with the person
  — not a date they specify separately), format `dd/mm/yyyy`. Homepage shows only
  the most recent entry (`studentsHtml()`); `#superstars` shows the full archive,
  newest first (`superstarsArchiveHtml()`). When the person sends a photo + name,
  upload the image to the repo and add an entry with today's date — same pattern
  as Photo Library.
- **Birthdays (`BIRTHDAYS`):** each entry `{name, month, day}` — no year, it's
  recurring annually. Homepage tile (`upcomingBirthdayTileHtml()`) shows "Upcoming:
  Name, dd/mm" and auto-switches to a celebratory state on the actual day.
  **Popup fires on every single app load** (confirmed explicitly with the person —
  not once-per-day, every open) via `checkTodaysBirthdays()` called once at script
  init with a 700ms delay, plus on tap via `.bday-trigger` elements (delegated
  click handler). `#birthdays` (`birthdaysHtml()`) shows the full class list sorted
  from today's soonest upcoming, wrapping around the full year to whoever's
  birthday just passed — the sort-by-next-occurrence-with-year-wraparound logic
  naturally produces this ordering, don't overthink it if revisiting.
- **Sports/League tables:** manual update only, on request. **Live sports-data
  sites (AiScore, Tribuna, Soccerway, etc.) block automated fetches** — this was
  tested extensively and failed every time. The reliable path for a table refresh
  is the person sending a screenshot from their phone (works great — got a fully
  accurate, internally-consistent 16-team Uzbek Super League table this way,
  cross-verified GD=GF-GA and Pts=W×3+D by hand before trusting it). Don't
  promise live-updating tables; don't waste many search calls trying to fetch
  current standings automatically — a couple of attempts is reasonable, then ask
  for a screenshot instead. Off-season leagues show zeroed 0-played tables with
  start-date reminders already set in the person's Reminders app. La Liga's 20th
  team was a "TBD (playoff winner)" placeholder for over a month before being
  fixed (Malaga won the promotion playoff) — worth periodically checking that kind
  of placeholder doesn't linger. BBC Sport football news could not be automated
  (no public API) — the Sports hub instead links out to the real BBC/league sites
  directly rather than trying to mirror their content.
- **Color palette:** currently "Maroon & Gold Library" (see `:root` CSS variables).
  The person went through ~20 generated palette mockups (rendered via Playwright
  screenshots of an HTML template) before landing here — if asked to change it
  again, generate mockups first rather than applying directly to the live site.
- **Dark mode:** toggle persists via `localStorage('darkMode')`. Remember the
  `--ink`/`--ink-solid` split above when touching this.
- **Video additions:** always verify each video against the actual current YouTube
  upload (search + confirm the exact video ID and channel) before adding — never
  guess an ID from memory or reconstruct one. This has been done reliably many
  times across this project; the standard is to state the categorization plainly
  (a title/channel/domain table) before pushing, not just silently add.

## Standing tone/workflow preferences (from userPreferences + this project)

- Brutally honest, no softening, no over-apologizing.
- Match response length to the question; don't pad.
- Verify before stating, especially for live/technical claims — this person will
  push back hard (and has) if told something works without it being tested first.
  Use Playwright (see above) rather than just eyeballing the code.
- No exam-prep branding anywhere (this is for general fluency/speaking, not
  IELTS/TOEFL prep) — carries over from the person's other teaching materials
  projects, may not be directly relevant to the site but reflects their general
  standards.
- When something breaks or turns out to be wrong, own it plainly and fix it —
  this person notices and calls out vague or defensive responses immediately.
