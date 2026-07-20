# Delivery notes, backlog TI-001 to TI-020

What was built, what was rejected, what needs your judgment, and what is
still open. Read the three items under "Needs your decision" before deploying.

---

## Delivered

| Ticket | Status | Where |
|---|---|---|
| TI-001 false completions | done | `markers.py`, `scoring.py`, `validate.py`, `backfill_completions.py` |
| TI-002 empty value table | done, cause differed | `index.html`, `site.py` |
| TI-003 mobile scroll | done, cause differed | `index.html` |
| TI-010 capture and digest | done, needs a provider key | `digest.py`, `run_digest.py`, `digest.yml` |
| TI-011 subscriber filters | done | `digest.py` |
| TI-012 analytics | done, needs a token | `site.py`, `index.html` |
| TI-013 club sorting | done | `index.html` |
| TI-020 rumour decay | done | `scoring.py`, `index.html` |

145 tests pass, up from 94. Golden set 3 of 3, zero false completions.

---

## Two tickets were rediagnosed

Both were written from observation of the live site, which is reasonable and
in both cases pointed at the wrong layer.

### TI-002 was not reproducible as described

The ticket lists three candidate causes: a renamed field in the filter, the
template looping the wrong collection, and free transfers throwing during
sort. None of them are present. Running the committed `index.html` against the
committed `data.js` in a headless browser renders 25 table rows and 25 scatter
points.

What is real is the mechanism that would produce exactly the reported
symptom. All four render blocks were bare IIFEs inside a single `<script>`
tag, so a throw anywhere aborted every block after it, and the page kept
whatever the build had pre-rendered into those containers. A crawler-facing
pre-render existed for the rumour list and the club grid but not the value
table, so a failure upstream showed as a populated chart above a table with
headers and no body.

That is now impossible in three independent ways: each section runs inside an
error boundary that logs and fires a `render_error` event; the chart and the
table read one `VALUED` collection instead of filtering separately; and the
table is pre-rendered server-side from the same collection, so the build can
count the rows and a test asserts the counts agree.

If the live site still shows an empty table after deploying, it was serving a
stale build and this deploy fixes it. If it shows one *and* the console is
clean, tell me, because that would mean a fourth mechanism.

### TI-003's cause was worse than the ticket assumed

The ticket suspects `overflow: hidden`, `100vh` wrappers, or horizontal rails
capturing the gesture, and asks for fade masks and partial rows as
affordances.

None of those are present either. The cause is `.tab{display:none}` with
`.tab.active{display:block}`. The four nav buttons swapped panels in place. On
a phone there was genuinely nothing below the fold, so scroll affordances
would have been advertising content that did not exist.

Below 860px every section now renders stacked, the nav is sticky and scrolls
to its target rather than swapping, and an IntersectionObserver keeps the
active button honest as you scroll. Desktop keeps tab behaviour, where the
viewport is tall enough for a panel to earn its place. The fade mask class
exists for any section that does end up clipped, and `touch-action: pan-x
pan-y` is set on the horizontal rails as the ticket asked, but neither is
doing the real work.

---

## Needs your decision

### 1. `data.json` and `data.js` have been modified in place

The backfill demoted 7 records from `done`. Six are the ones named in the
ticket. The seventh is Bazoumana Touré, caught by the tier rule: marked
completed on a tier 2 source, which the new gate forbids.

```
Andrey Santos      note said "all done bar the unveiling"
Youri Tielemans    note said "announcement expected this week"
Luka Vuskovic      note said "announcement imminent"
Johan Manzambi     note carried an auto-update stamp
Leandro Trossard   note said "personal terms being discussed"
Alvaro Rodriguez   note said "then silence"
Bazoumana Toure    completed on a tier 2 source
```

All seven moved to `confirmed`, credibility capped at 90, notes untouched.
Not to `collapsed` and not backwards to `medical`: the pipeline does not know
these fell through, only that it cannot show they completed. Overstating a
retraction is the same class of error as overstating the original claim.

Rerun the audit yourself any time:

```bash
python scripts/backfill_completions.py --data data.json     # report only
```

**If you would rather review before this touches your repo**, revert those two
files and apply it yourself:

```bash
git checkout main -- data.json data.js
python scripts/backfill_completions.py --data data.json > audit.md
```

### 2. The 24 surviving completions carry an honest fudge

They are migrated records whose evidence is a `urn:` seed rather than a link,
so they cannot be machine-verified. Demoting them would put a false statement
on the site in the other direction: Elliot Anderson to Man City plainly
happened.

The consistency gate requires every `done` record to name what proves it, and
exempting these would leave a hole exactly where the original defect lived.
So they are stamped:

```
completion_marker: "hand-verified at migration, no fetchable source"
```

Auditable, visible to anyone reading the record, and it fails no invariant.
Worth spot-checking a handful against reality when you have ten minutes.

### 3. Nothing is configured for email or analytics yet

Both features render nothing until you set config values. That is deliberate:
a capture form posting nowhere burns the one chance you get to ask a visitor
for their email.

- `config.newsletter.action`, plus a `NEWSLETTER_API_KEY` secret. See
  `docs/NEWSLETTER.md`.
- `config.analytics.cloudflareToken`. See `docs/ANALYTICS.md`.

---

## Design decisions worth knowing about

**A completion marker can skip the `confirmed` rung.** The one-rung-per-run
rule exists to stop thin evidence carrying a deal a long way. It should not
make a club's own announcement wait a day, which on deadline day would be
useless. So a tier 1 source with a recorded marker promotes straight from
`medical` to `done`, and the gate sanctions that specific jump and no other.
Deals further back still walk one rung at a time and raise a flag.

**Provenance fields do not count against the daily update limit.** Adding
`last_verified_at`, `completion_marker`, `completion_source`,
`completed_date` and `base_cred` meant a single genuine status change spent
five of the day's fifteen slots on its own paperwork. They are still checked
and still need reasons; they just stop the volume limit from measuring the
wrong thing.

**Decay multiplies rather than subtracts.** The old model subtracted up to 30
points, which flattened the difference between a strong rumour and a weak one
over a quiet fortnight. Multiplying preserves the ratio. The curve is one
frozen dataclass, `DecayCurve`, and matches the ticket exactly: flat to day 3,
linear to 0.60 at day 14, 0.40 from day 15 with a Stale badge.

**The value model is deliberately implemented twice**, once in `index.html`
for the reader and once in `site.py` for the build. A test asserts the browser
copy has not changed without the Python copy following. Duplication is the
mechanism here, not an oversight: it is what makes a build-time assertion
about reader-facing numbers possible at all.

---

## Still open

**TI-021 source accuracy leaderboard.** The scoring hook is already in:
`scoring.reliability_term` takes a measured hit rate per source, centres it on
0.5 so an unmeasured source contributes nothing, and is wired through
`score_all`. What remains is computing rates from resolved outcomes and
building `/sources/`. This is the most defensible asset on the backlog and the
one that survives past 1 September; it should be next.

**TI-022 contested targets.** Nothing started. `entities.fold` already handles
the accented-name grouping the ticket worries about, so the hard part is done.

**TI-023 collapse post-mortems.** Half landed with TI-001, because the data
model needed it: the `CollapseReason` enum, `collapse_narrative`, the
`pivot_from` and `pivot_to` links, rule-based classification in
`markers.collapse_reason`, and pivot pressure feeding credibility. The
`/collapsed/` view and the pivot chain rendering remain.

**TI-024 squad gap map.** Blocked here, not in principle: the build sandbox
cannot reach `fantasy.premierleague.com`. It runs fine from GitHub Actions. I
did not ship a fabricated squad cache to make it look finished.

Sequencing suggestion, given the window closes 1 September: TI-021 next
because it needs data accumulating from now to be worth anything at window
close, then TI-023's remaining views, then TI-022. TI-024 last, and only if
the FPL fetch proves reliable on a runner.
