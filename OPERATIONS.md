# TransferIntel operations

Deployment, the daily loop, and what to do when it misbehaves.

## What you are deploying

A static site that has not changed, plus a GitHub Action that proposes edits
to it every morning and never once commits them itself.

```
07:00 UTC   Action wakes
            phase 1  RSS feeds in, deduped, filtered to tracked clubs
            phase 2  Haiku reads headlines, code resolves them to deals
            phase 3  status, fee and cred computed in Python
            phase 4  Sonnet writes notes for deals that actually moved
            phase 5  the gate checks the patch, or aborts the whole run
            opens a pull request
07:05       You get a notification
07:07       You skim it on your phone and merge
            Pages redeploys
```

The state of the world lives in `data.json` on `main`. Nothing else is
authoritative. That single fact is what makes every failure recoverable:
close a bad pull request and tomorrow's run recomputes from the same place,
having lost nothing.

---

## One-time deployment

### 1. Get the code in

Copy `scripts/`, `evals/`, `fixtures/` and `.github/workflows/` into the repo
root, next to `index.html` and `data.js`.

```bash
pip install -r scripts/requirements.txt
python -m pytest scripts/tests -q          # expect 71 passed
python scripts/run_evals.py --suite pipeline
```

If either fails, stop. Nothing downstream is worth debugging until these are
green.

### 2. Migrate the data (phase 0)

```bash
python scripts/migrate_data.py --js data.js --out data.json          # dry run
```

Read the output. It lists deals whose clubs have no dashboard entry and
anything that failed validation. Fix what it complains about in `data.js`
first, because fixing it afterwards means fixing it in two files.

```bash
python scripts/migrate_data.py --js data.js --out data.json --write
```

Now open `index.html` locally and confirm the site is unchanged. It should
be: `data.js` has not been touched yet.

### 3. Prove the loop offline

```bash
python scripts/run_ingest.py --data data.json --out build
python scripts/run_editorial.py --data data.json --evidence build/evidence.json \
    --out build --today $(date +%F)
cat build/patch.md
```

With no API key set, both model phases go quiet and you get a pure decay
pass. That is the point: you are checking the plumbing, not the output.

Then run it once with `--apply`, open the site again, and confirm it still
loads. Commit `data.json`, the regenerated `data.js`, and the scripts.

### 4. Repository settings

Two settings, and the second one is the one everybody misses.

- **Settings → Secrets and variables → Actions → New repository secret.**
  Name it `ANTHROPIC_API_KEY`.
- **Settings → Actions → General → Workflow permissions.** Set *Read and
  write permissions*, and tick **Allow GitHub Actions to create and approve
  pull requests**. Without that tick the run does all its work correctly and
  then fails on the last step with a permissions error.

Set a monthly spend alert on the Anthropic console while you are at it.

### 5. First run, deliberately cheap

**Actions → Editorial refresh → Run workflow**, with `no_notes` ticked.

That exercises phases 1, 2, 3 and 5 and skips the expensive one. Read the
pull request carefully. Do not merge it yet; you are looking for whether the
*shape* is right, not the content.

### 6. First real run

Run it again with `no_notes` unticked. Read the notes. If they do not sound
like you, edit the examples in the system prompt in `notes.py`, which is a
faster lever than editing the rules.

Merge when you are happy. The schedule takes over the next morning.

---

## The daily loop

Two minutes, on your phone.

Open the pull request and read it in this order, which is risk order, not
page order.

**1. The gate section, if there is one.** Warnings ride along in the body.
They never block, but they are the pipeline telling you it noticed something
odd.

**2. Any `status` change to `done`.** Click the link. Actually click it. This
is the only genuinely irreversible thing in the run: once a deal is done it
is terminal, no later evidence can move it, and a wrong one has to be fixed
by hand. Everything else self-corrects within a day or two.

**3. Any `status` change to `collapsed`.** Same reasoning, slightly less
severe, since a tier 1 source can revive it.

**4. Fee changes flagged as more than doubling.** Usually a currency error
the model reported as GBP.

**5. The notes.** These are your public voice and the model wrote them. Read
them as prose, not as data.

**6. Credibility moves.** Skim. Each carries its own arithmetic in the Why
column, so you are checking the inputs, not the sum.

**7. The candidates section at the bottom.** Deals the pipeline saw that you
do not track. Ignore them most days.

Then merge.

### When something in the patch is wrong

| Situation | What to do |
|---|---|
| One operation is wrong, rest is fine | Edit `data.json` directly on the `auto/editorial` branch, commit, merge |
| Several things are wrong | Close the pull request, delete the branch. Tomorrow recomputes from `main` |
| A completed transfer is fabricated | Close it, then open `logs/<date>.json` and find which source claimed it. If the outlet is not in `DOMAIN_TIER`, add it at tier 3 |
| The gate aborted the run | The job exits 2 and writes no pull request. Download the `build/aborted_patch.json` artifact to see what it refused |

Closing a pull request costs you nothing. It is the designed response to
doubt, not a failure.

### Accepting a candidate

Candidates never write themselves in. To accept one, add a deal to
`data.json` using the suggested id and a conservative starting point:

```json
{
  "id": "okafor-ajax-manchester-united",
  "p": "Tunde Okafor", "from": "Ajax", "to": "Manchester United",
  "fee": 58.8, "age": 24, "pos": "CM",
  "status": "rumor", "date": "Jul 19",
  "tier": 2, "src": "Football365", "cred": 35,
  "note": "",
  "evidence": []
}
```

Leave `evidence` empty and `note` blank. The next run attaches real evidence
and writes the note, and its credibility will be recomputed from scratch, so
the number you type here barely matters.

---

## Weekly, about ten minutes

**Read `build/needs_review.json` from a recent run.** Claims that resolved to
nothing. If the same player keeps appearing, the fix is almost always a
missing entry in `CLUB_ALIASES` in `entities.py`.

**Check for outlets you keep seeing that are not tiered.** Any domain absent
from `DOMAIN_TIER` in `sources.py` defaults to tier 3, which means it can
never move a deal. If a genuinely good outlet keeps appearing, promote it.
This is the single highest-leverage maintenance job.

**Record a case when a day was interesting.**

```bash
python scripts/run_evals.py --record evals/cases/2026-08-02-deadline \
    --data data.json --build build --today 2026-08-02
```

Then edit the generated `expected.json` down to the assertions that capture
why the day mattered, and delete the rest. A case that asserts everything
fails on every harmless change, and a suite that always fails is a suite you
stop reading.

**Prune `logs/`.** It grows by two small files a day. A yearly `git rm` is
plenty.

---

## Tuning the scoring

Every constant lives in `ScoringConfig` in `scoring.py`. The loop is:

```bash
python scripts/run_evals.py --suite pipeline    # baseline, note the numbers
# edit the constants
python scripts/run_evals.py --suite pipeline    # compare
```

`false_completions` must be zero. It is the one metric with no acceptable
trade: a transfer marked done that did not happen is the failure the entire
design exists to prevent. Everything else is negotiable against it.

If you change the extraction prompt in `extract.py`, run the other suite,
which costs a few cents and needs a key:

```bash
python scripts/run_evals.py --suite extraction
```

Note the deliberate redundancy: the gate re-derives its own limits rather
than trusting `ScoringConfig`. If you raise `max_cred_delta_per_run` in the
scorer and forget the gate, the gate aborts the run. That is not a bug, it is
two independent implementations disagreeing, which is exactly when you want
to be stopped.

---

## When something breaks

| Symptom | Cause | Fix |
|---|---|---|
| No pull request, job green | Nothing changed | Normal on a quiet day |
| No pull request, job red on the last step | Missing PR permission | Settings → Actions → General, tick "Allow GitHub Actions to create and approve pull requests" |
| Job exits 2 | The gate refused the patch | Read the log, then `build/aborted_patch.json` |
| "0 articles" in the log | Feeds down, or all filtered out | Check `ingest_stats.json`. `feeds_failed` high means network, `relevant` at zero means the club filter |
| Claims extracted but nothing resolves | Club names not matching `data.json` | Add aliases to `CLUB_ALIASES` |
| Every deal decaying, nothing advancing | Phase 2 produced nothing | Check `ANTHROPIC_API_KEY` is set in secrets and not expired |
| Notes stopped changing | Note budget hit, or nothing moved enough | Notes only regenerate on real movement. Check `--max-notes` |
| Deals flagged stale two weeks in | Only phase 0 seed evidence, never mentioned since | Working as designed. Delete them |
| Site shows old data after merge | Pages cache | Wait for the Pages deploy, then hard refresh |

### Kill switch

To stop the automation without deleting anything, comment out the `schedule`
block in `editorial.yml`. Manual runs still work. To keep it running but stop
model spend, run it with `no_notes` ticked and remove the secret; phases 1
and 3 carry on and you get a decay-only pass.

---

## Costs

| Item | Per day |
|---|---|
| Phase 2, roughly 60 to 120 headlines through Haiku, batched twenty at a time with the rules block cached | a couple of cents |
| Phase 4, up to 12 notes through Sonnet | a couple of cents |
| GitHub Actions on a public repo | free |
| Pipeline evals | free, they replay recorded output |
| Extraction evals, when you run them | a few cents |

Call it a pound a month in season. The caps that keep it there are
`--max-batches` on ingest and `--max-notes` on the editorial pass, both set
in the workflow.

---

## Starting a new window

1. Archive the old file: `git mv data.json windows/2026-summer.json`
2. Start a fresh `data.json` with the new `config` block and an empty `deals`
   array, keeping `clubs`
3. Add the season's deals by hand, or let candidates accumulate for a few
   days and accept them
4. Keep the eval cases. They are about the rules, not the window, and a case
   from last summer still catches a broken tier gate perfectly well
