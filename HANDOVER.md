# Mr Eric's Lounge — Handover Notes

Read this before making any changes. It exists because a lot of decisions in this
codebase look arbitrary from the code alone but were made deliberately, often after
trying the "obvious" approach first and having it rejected or breaking something.

## The basics

- **Live site:** https://mreric16.github.io/ericslounge/
- **Repo:** MrEric16/ericslounge, single file: `index.html` (everything — HTML, CSS, JS —
  lives in one file, no build step, no framework)
- **Other repo files:** `manifest.json`, `icon-192.png`, `icon-180.png`, `icon-512.png`
  (Add to Home Screen support), `sw.js` (offline service worker)
- **Architecture:** static SPA, hash-based routing (`#faculty/science`, `#watch/sport`,
  etc.), everything rendered client-side from JS data arrays baked into the file
- **Push workflow:** base64-encode the file, PUT to GitHub Contents API with the
  person's PAT (stored in their Notes app, not repeated here for safety). Always
  `node --check` the extracted `<script>` block before pushing — this file is large
  and manual edits break syntax easily.
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
  day badges, On This Day/Random Fact/Weather/Word of the Day cards — all white text
  on a dark chip). Merging them back into one variable will break dark mode contrast.
- **On This Day pulls live from Wikipedia's API**, filtered by keyword lists
  (`OTD_NEGATIVE_KEYWORDS` / `OTD_POSITIVE_KEYWORDS`) to avoid war/death/violence
  content, with a curated 34-entry fallback pool only used if the live fetch fails
  or returns nothing safe. This was flip-flopped once already (see history below) —
  don't silently swap it back to a static pool without asking. It must be genuinely
  date-accurate.
- **Random Fact, Word of the Day, and On This Day are three separate systems with
  different update rules** — don't conflate them:
  - Random Fact: 300 entries, genuinely random every visit, not date-tied.
  - Word of the Day: 365 entries, deterministic by day-of-year (`dayOfYear %
    pool.length`), same word for everyone all day, changes at midnight.
  - On This Day: live-fetched, real date accuracy (see above).
- **No emoji for site iconography, except a specific whitelist.** Nearly every emoji
  used for navigation/branding was replaced with hand-drawn SVG line icons (see
  `ICONS` and `FLAGS` objects near the top of the script) because emoji "looked too
  AI." Kept as literal emoji, on purpose: reaction buttons' visual flourish is now
  icon-based too (see below), but Arsenal's 🏆, Tottenham's 💩, confetti, and
  achievement-toast easter eggs stay as real emoji — those are personality/flavor,
  not interface icons, and were explicitly kept. If asked to add a new icon, follow
  the existing pattern in `ICONS` (stroke-based, `currentColor`, minimal line art) —
  don't reach for an emoji by default.
- **Flags are hand-built SVGs, not emoji** (`FLAGS` object) — flags carry no
  copyright, so this was safe to do accurately. Same reasoning: don't revert to flag
  emoji.
- **No real photos/logos for trophies, crests, or club badges** beyond the Arsenal
  crest (one exception, explicitly approved, sourced from a logo site). General rule
  discussed at length: real team logos/photos are copyright-risky to source and
  embed at scale, so anything needing "iconography" for a team/league/trophy uses
  original SVG line art instead, unless the person explicitly provides/approves an
  image.
- **Scroll position is remembered per-hash across navigation** (`scrollMemory` in
  sessionStorage, keyed by hash). Don't reintroduce an unconditional
  `window.scrollTo(0,0)` at the end of `renderRoute()` — that was the exact bug this
  fixed.
- **Swipe navigation is cyclable in both directions**, wraps around at the ends.
  Two independent swipe groups: `DOMAIN_SWIPE_ORDER` (the 12 faculties, used inside
  `#faculty/<key>` and `#watch/<key>` sub-pages) and `HUB_SWIPE_ORDER` (`watch`,
  `photos`, `sports`, `saved`, `superstars`, `birthdays` — the six top-level hub
  pages). Don't merge these into one order; they're semantically different levels
  of navigation.

## Content submission model — nothing here is truly autonomous

There is no backend, no database, no user-facing upload form, and the shared
key-value storage API available to artifacts has been unreliable in this session
whenever tried — don't assume it works. Every piece of "changing" content
(Photo Library images, Superstars photos, Birthdays list) is added by *the person
sending Claude the content in chat*, and Claude manually commits it to the repo each
time. This is expected and by design, not a limitation to solve — just don't imply
to the person that any of this updates itself from an external source. The things
that genuinely run themselves with zero further input once seeded: weather (live
API), On This Day (live API), Random Fact / Word of the Day (large static pools,
rotate on their own), the birthday tile's auto-advance logic, and the birthday
popup — all of those are real automation. Photos and names are not.

## Feature-specific notes

- **Weather:** Tashkent + 2 random cities from `WORLD_CITIES` (all ~186 world
  capitals), reshuffled every visit. Tashkent is always first and hardcoded
  separately — don't fold it into the random pool.
- **Superstars:** `STUDENTS_OF_WEEK` array, each entry `{name, note, photoUrl, date}`.
  Date format is `dd/mm/yyyy`, recorded as *the date it's added to the site*
  (confirmed with the person — not a date they specify separately). Homepage shows
  only the most recent entry; `#superstars` shows the full archive, newest first.
- **Birthdays:** `BIRTHDAYS` array, each entry `{name, month, day}` — no year, it's
  a recurring annual thing. Homepage tile shows "Upcoming: Name, dd/mm" and
  auto-switches to a celebratory state on the actual day. Popup fires (a) every
  single time the app loads if today matches someone's birthday (explicitly
  requested — not once-per-day, every open) and (b) whenever a birthday person's
  name is tapped. `#birthdays` shows the full class list sorted from today's
  soonest upcoming, wrapping around the full year to whoever's birthday just passed.
- **Sports/League tables:** manual update only, on request ("update league tables").
  Off-season leagues show zeroed 0-played tables with start-date reminders already
  set in the person's Reminders app. BBC Sport football news could not be automated
  (no public API) — the Sports hub instead links out to the real BBC/league sites
  directly rather than trying to mirror their content.
- **Color palette:** currently "Maroon & Gold Library" (see `:root` CSS variables).
  The person went through ~20 generated palette mockups before landing here — if
  asked to change it again, generate mockups first (a Playwright-rendered HTML
  template exists as a pattern in this session's history) rather than applying
  directly to the live site.
- **Dark mode:** toggle persists via `localStorage('darkMode')`. Remember the
  `--ink`/`--ink-solid` split above when touching this.

## Standing tone/workflow preferences (from userPreferences + this project)

- Brutally honest, no softening, no over-apologizing.
- Match response length to the question; don't pad.
- Verify before stating, especially for live/technical claims — this person will
  push back hard (and has) if told something works without it being tested first.
  Since this session gained Playwright access mid-project, **use it**: render
  changes in a real headless browser and check the DOM/console before claiming
  something works, not just `node --check` for syntax.
- No exam-prep branding anywhere (this is for general fluency/speaking, not
  IELTS/TOEFL prep) — carries over from the person's other teaching materials
  projects, may not be directly relevant to the site but reflects their general
  standards.
