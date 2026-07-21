# Catching up after missed runs

The pipeline assumes it ran yesterday. Three separate limits enforce that
assumption, and after a gap all three work against you at once. This is the
recovery procedure.

## Why a normal run finds nothing after a gap

**1. The ingest window is 36 hours.** `run_ingest.py --window-hours 36` drops
any article published before then, at fetch time. Miss four days and the news
from days two, three and four is discarded before scoring ever sees it.

**2. The scoring window is 3 days.** `ScoringConfig.recent_window_days`
controls which evidence is allowed to move a status. Evidence older than that
still counts toward the historical tier, but it cannot promote a deal. So even
if you widen ingest and pull in a five-day-old report that a fee was agreed,
the default scorer treats it as context and moves nothing.

**3. The gate caps a run at 15 editorial updates.** After a gap the backlog of
accumulated decay alone will exceed that, and the run aborts with
`GATE FAILED, nothing written`. Which is correct behaviour: it cannot tell
your four-day gap from a broken feed, so it refuses and waits for a human.

**4. RSS feeds have finite depth**, and this one is not recoverable. Most
outlet feeds carry somewhere between twenty and fifty recent items. News that
has fallen off the end is gone, whatever window you ask for. A gap of more
than about a week during a busy period means some deals will simply never be
picked up, and the honest options are to add them by hand or let them go.

## The procedure

Work out how many days you missed, then:

```bash
# 1. Reach back far enough to refetch. Add a day of overlap.
python scripts/run_ingest.py --window-hours 168        # 7 days

# 2. Widen the scoring window to match, and lift the gate for one run.
python scripts/run_editorial.py \
  --data data.json --out build \
  --recent-days 8 \
  --max-changes 80

# 3. READ THE PATCH before applying it. This is the step that matters.
cat build/patch.md

# 4. Apply once you are satisfied.
python scripts/run_editorial.py \
  --data data.json --out build \
  --recent-days 8 --max-changes 80 --apply

# 5. Rebuild and check.
python scripts/render_site.py --data data.json --template index.html --out .
python scripts/check_site.py
```

Then go back to the defaults. Do not leave `--recent-days` or a raised
`--max-changes` in the workflow: they exist to recover from an exceptional
state, and running permanently in recovery mode means the gate stops
protecting you on the day something actually breaks.

## Step 3 is not optional

The gate's volume limit is the main defence against an upstream fault
rewriting the site in one run. Raising it for a catch-up removes that
defence, so a human has to supply it instead.

Read `build/patch.md` and check specifically:

- **Status promotions.** Anything reaching `done` should name the completion
  marker and the tier 1 source that produced it. Anything reaching
  `collapsed` should point at a report saying so.
- **Credibility falls of 20 or more.** Usually accumulated decay, which is
  correct. Occasionally a sign that evidence was misparsed.
- **Deals you know completed but that did not move.** These are the ones the
  feed lost. Add them by hand rather than widening the window further.

## Adding a deal by hand

When the news is gone from every feed, edit `data.json` directly. The record
needs at minimum an `id`, `p`, `from`, `to`, `fee`, `age`, `pos`, `status`,
`date`, `tier`, `src`, `cred` and a `note`, plus one evidence entry with a
real URL you found yourself.

Then run the audit and the gate before committing:

```bash
python scripts/backfill_completions.py --data data.json    # report only
python scripts/run_editorial.py --data data.json --out build --no-notes
```

If you hand-write a `done` status, it needs `completion_marker`,
`completion_source` and `completed_date` or the consistency gate will fail
the build. That is the TI-001 rule working as intended: a completion has to
name what proves it, whether a machine or a person wrote it.

## Preventing the next gap

The editorial workflow runs at 07:00 UTC daily and opens a pull request. It
does not need you to be awake, only to merge within a day or so. If you are
going to be away for longer than a week during the window, the lowest-effort
options in order are:

1. Merge the PRs from your phone. It is a one-tap approval.
2. Turn on auto-merge for the bot's PRs, accepting that the gate is then the
   only reviewer. It is a decent reviewer.
3. Accept the gap and budget an hour for this procedure when you return.
