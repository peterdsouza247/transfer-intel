# Changelog

## v1.0.0

First tagged release. The site has been live through the 2026 summer window;
this is the point at which the pipeline behind it became something that can be
handed to someone else.

### The defect that shaped this release

Deals were being promoted to Completed with credibility 100 on no positive
evidence at all. The pipeline treated *absence of contradicting news* as
confirmation, so any record it re-encountered in a feed, or simply never heard
had collapsed, drifted to the top of the ladder. Six deals sat on the live
homepage marked Completed while their own summary text said "announcement
imminent" and, in one case, "then silence".

Fixing it properly took a new status, a new class of evidence, and a gate that
assumes the rest of the pipeline is lying.

- **`confirmed` status**, between `medical` and `done`. A tier 1 source saying
  a deal is finished now reaches "Confirmed pending announcement", capped at
  credibility 90. It decays with silence, floored so the number cannot
  contradict the badge printed next to it.
- **Completion markers** (`markers.py`). Reaching `done` requires a phrase that
  only applies after the fact, matched with hedge detection so "completes his
  move" counts and "set to complete his move" does not. Romano's "here we go"
  is scoped to Romano. A club's own domain is the strongest marker and is
  recorded separately as `official`.
- **Consistency gate.** Every `done` record must name the phrase and the tier 1
  source proving it, carry a `completed_date`, and not appear in the collapsed
  set. Credibility 100 is impossible outside `done`. The build fails otherwise.
- **`last_verified_at`**, which moves only when a source reasserts a deal. This
  is what makes silence measurable, and it is the field the decay curve reads.
- **Backfill** (`backfill_completions.py`). Audited 31 completed records by
  rule rather than by eye and demoted 7, including all six known bad ones plus
  one completed on a tier 2 source. Survivors that cannot be machine-verified
  carry an explicit `hand-verified at migration` marker rather than a silent
  pass.

### Also in this release

**Rumour decay.** Multiplicative rather than subtractive, so a weak rumour and
a strong one lose proportionally. One config constant: flat to day 3, linear to
0.60 by day 14, 0.40 from day 15 with a Stale badge. Both numbers display:
"base 45, currently 31 after 11 days without movement."

**Email capture and the daily digest.** Three placements, one on mobile.
Provider-agnostic, hosted-form based, no backend. Per-club segments generated
rather than looped, instant alerts capped at two a day, and a quiet-day edition
that says nothing moved in two lines rather than padding. Never sends twice for
one date.

**Analytics.** Cloudflare Web Analytics plus GoatCounter, cookieless so no
consent banner, six named events, under 5KB.

**Cost controls.** A keyword prefilter in front of the extractor, which keeps
100 percent of known transfer articles and drops about 90 percent of the rest;
a seen-article cache so the overlap between a 36 hour window and a 24 hour
schedule is not paid for twice; and notes moved from Sonnet to Haiku.

**Ingest health check.** The ingest step is allowed to fail so a dead feed
cannot block the decay pass, and an empty evidence file is written when none
exists. Together those meant a pipeline with no API key ran green indefinitely,
publishing decay and never learning anything. `check_ingest.py` turns that back
into a failed job.

**Manual ingest path.** `run_ingest.py --claims` loads pre-extracted claims and
skips phase 2, for running without an API key. Everything downstream is
unchanged, so a curated claim gets no privileges at the gate.

**Frontend.** Sortable columns on the verdicts table and a feed order control on
Window Pulse, both keyed by deal id so sorting cannot mis-target a row. Club
dashboards default to activity with zero-activity clubs grouped out of the way,
and show spend in, out and net. On mobile the sections stack and the nav is a
fixed bottom bar rather than tabs swapping panels in a viewport with nothing
below the fold.

### Fixed

- The pre-rendered pulse feed and the browser feed sorted differently, so the
  list visibly reshuffled on load and readers without JavaScript got twelve
  settled completions in reverse alphabetical order.
- The value chart and verdicts table filtered independently, so a table
  showing nothing under a chart showing points was a legitimate state of the
  code. Both now read one collection and a test asserts the counts agree.
- All four render blocks shared one script scope, so a throw anywhere killed
  every block after it. Sections now fail independently.
- `position: sticky` on a nav inside `<header>` cannot outlive the header, so
  the mobile nav scrolled away and the only way back was to scroll to the top.

### Repository hygiene

- `conftest.py` and `pytest.ini` pin test imports to their own checkout. Two
  copies of the repository on disk used to produce `cannot import name
  'digest' from 'transferintel' (unknown location)`, which names neither copy.
- Root-level docs consolidated into `docs/`. Only `README.md`, `CHANGELOG.md`
  and `LICENSE` remain at the top level.
- Unused imports removed; the tree is clean under pyflakes.

### Licence

Rewritten. The previous file was inherited from a game project: it covered
"game design" and "personal play", named `data.js` rather than `data.json` as
the home of the editorial content, and forbade scraping and republication
while every generated page declared the dataset CC BY 4.0 in its Schema.org
markup. The machine-readable claim and the human-readable one said opposite
things.

Now explicitly split: the code is proprietary, the dataset is CC BY 4.0 with
attribution, and the licence states that CC BY covers the compilation rather
than the underlying reporting, which belongs to the outlets cited.

### Added after v1.0

- `scripts/draft_claims.py` drafts `manual/claims.json` from fetched articles
  by pattern matching, so the by-hand routine becomes correcting claims rather
  than writing them. Every entry is marked `_draft: true`, and `run_ingest`
  refuses a file that still contains one: the draft saves the typing, not the
  judgment.
- `scripts/add_candidates.py` promotes a detected candidate into a tracked
  deal, with the gate applied.
- `run_editorial.py --recent-days auto` sizes the evidence window from the age
  of the evidence in hand, so a run after a gap widens itself instead of
  scoring last week's announcements as history and reporting nothing changed.
  The scheduled workflow passes it.

### Changed after v1.0

- Editorial moved to 04:00 UTC and the digest to 06:30 UTC. The digest used to
  run an hour *before* the refresh, so every edition described yesterday.

### Added after v1.0

- Position badges on player names throughout the index, the pulse feed and
  the pre-rendered pages.
- Source links. Where a record carries evidence with a real URL, the reader
  can open the article that produced the score. Migrated records hold `urn:`
  seeds rather than links and correctly show nothing: a dead link is worse
  than no link.
- `docs/PARTNERSHIP.md`, the pitch for outlets, including the independence
  problem that comes with taking their money, and a section setting out the
  scoring model in full: tier bases, corroboration cap, stage bonuses, the
  decay curve, and four worked examples from the live dataset. A test asserts
  those numbers still match `scoring.py`, because a checkable claim that has
  drifted is worse than a vague one.

### Fixed after v1.0

- A deal at `agreed` with tier 1 sources reporting completion needed two
  daily runs, and therefore two days, to reach `done`. The sanctioned skip
  now starts at `agreed` rather than `medical`: the clubs have settled terms,
  so completion from there is the expected next event. `rumor` and `talks`
  still walk one rung at a time.
- Flags were reported as a count and nothing else, so a deal held back by the
  ladder gave no visible reason. They are now printed with the deal and the
  explanation, and a run that changes nothing says what to check.

- **Kit rejected every segmented edition.** `subscriber_filter` was sent as an
  object where Kit wants an array, so the unsegmented digest went out and all
  six club editions returned 422. The digest also now checks that the tags a
  segment targets exist before sending, because a filter naming a tag nobody
  created matches zero subscribers and reports success.
- Per-club editions are opt-in via the `DIGEST_SEGMENTS` repository variable.
  On a new list they were six broadcasts a day to nobody.
- **A claim whose article was missing was dropped silently.** It now appears
  in `needs_review.json` and in the run output. This was the reason a
  correct-looking manual ingest produced nothing.
- `draft_claims.py` writes `manual/articles.json` alongside the claims, as a
  matched pair. Nothing previously produced that file, so it had to be
  assembled by hand and was the easiest step in the routine to get wrong.
- Failing feeds are named, and each feed's article count is reported.
- `scripts/check_feeds.py` tests configured and candidate feeds, separates a
  stale feed from a dead one, and prints the lines to paste for any worth
  adding. Football365 (no response) and the Telegraph (120 articles, none
  inside the window) were removed on its evidence.

- Two stray dropdowns at the foot of the Rumour Credibility section: an
  orphaned `capture-prefs` block left behind when the second capture form was
  removed. The earlier cleanup caught the form tails and missed this.

- Removing the two extra capture forms left three orphaned fragments in
  `index.html`. The deletion used a non-greedy `<div id="...">.*?</div>`,
  which stops at the first *inner* closing div, so each form's tail survived:
  a hidden input, a stray paragraph and an unmatched `</form>`, rendering
  "Double opt-in. One click to unsubscribe." as loose text three times down
  the page. A test now asserts the form, details and div tags balance and
  that each give-away phrase appears exactly once.

- **New deals had no way into the dataset.** `run_ingest.py` wrote detected
  transfers for untracked players to `build/candidates.json` and nothing ever
  read that file, so the only route in was hand-editing `data.json`. In
  practice new deals never entered at all: the site kept scoring the same
  players while the window moved on. `scripts/add_candidates.py` is the
  missing step.
- Club aliases canonicalised to strings the dataset does not use: "Wolves"
  became "Wolverhampton", "Atletico Madrid" became "Atletico", "PSG" became
  "Paris Saint-Germain". Since dashboards match on exact string equality, an
  ingested deal and a migrated one described the same club two ways. A test
  now asserts every club name in `data.json` is its own canonical form.

- `migrate_data.parse_display_date` called `strptime` without a year, which
  emits a DeprecationWarning under Python 3.14 and will stop working in 3.15.
  The warning was hiding a real defect: the implicit year is 1900, which was
  not a leap year, so "Feb 29" raised and fell through to the fallback date
  even in years where it exists. The year is now supplied to the parse.
- The test suite treats DeprecationWarning as an error, so the next one is
  found by the build rather than by a user on a newer Python.

### Changed after v1.0

- The email capture form is now a single instance under the site header,
  rather than three placements. TI-011's club and threshold preferences move
  onto it, folded behind a disclosure that is closed by default, since the
  placement that used to carry them no longer exists.

### Removed

- `scripts/update_data.py` and its workflow. A Wikipedia scraper that promoted
  deals to `done` if a player appeared on a list, wrote to `data.js` only, and
  pushed straight to `main` with no gate. It was a second, independent route to
  the defect this release exists to fix.
- `scripts/entities.py`, a byte-identical orphaned copy of the module inside
  the package.

### Known limitations

- The backlog's TI-021 through TI-024 and TI-028 are open. TI-021, the source
  accuracy leaderboard, has its scoring hook already wired and should be next:
  it needs outcome data accumulating from now to be worth anything at window
  close.
- 24 completed records trace to the original migration rather than to a
  fetchable source. They are marked as such and are worth spot-checking.
- The prefilter is tuned for recall over precision by design. It will pass some
  articles that are not transfer news, which costs a fraction of a penny each.
