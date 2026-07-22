# TransferIntel pipeline

Four phases. The model appears twice, both times doing something narrow and
checkable; every decision that could put a false transfer on a live site is
made in Python.

| Phase | What it does | Model |
|---|---|---|
| 0 | `data.js` becomes `data.json`, with stable ids and seeded evidence | none |
| 1 | RSS in, deduped and filtered articles out | none |
| 2 | Headlines become claims, claims resolve to deals | Haiku, batched |
| 3 | `status`, `fee` and `cred` computed | none |
| 4 | The one-sentence `note` | Sonnet, one call per changed deal |
| 5 | The gate: abort, or annotate the PR | none |
| 6 | Evals against a golden set | Haiku, extraction suite only |
| 7 | Render the discoverable site | none |

Deployment and the daily routine are in [OPERATIONS.md](../docs/OPERATIONS.md).
SEO and pre-rendering are in [DISCOVERABILITY.md](../docs/DISCOVERABILITY.md).

## Layout

```
scripts/
  transferintel/
    models.py     Deal, Evidence, Article, Claim, Candidate, PatchOp
    sources.py    feeds, domain to tier map, FX rates
    entities.py   club aliases, player matching, deal ids
    ingest.py     phase 1, stdlib only
    extract.py    phase 2, batched extraction plus resolution
    scoring.py    phase 3, deterministic
    notes.py      phase 4, model plus validator
    validate.py   phase 5, the gate
    evals.py      phase 6, golden set harness
    site.py       phase 7, static rendering and structured data
    og.py         phase 7, Open Graph card images
  migrate_data.py    phase 0, one time
  run_ingest.py      phases 1 and 2
  run_editorial.py   phases 3, 4 and 5
  run_evals.py       phase 6
  render_site.py     phase 7
  check_site.py      phase 7 diagnostic, writes nothing
  check_og.py        fetches a live URL and validates its card
  tests/             the rules, locked down
  requirements.txt
evals/
  cases/             recorded days, replayed for free
  extraction/        headlines with known correct readings
fixtures/            replayable feeds and data for offline runs
.github/workflows/editorial.yml
.github/workflows/extraction-evals.yml
```

## Phase 0, once

```bash
pip install -r scripts/requirements.txt
python -m pytest scripts/tests -q

python scripts/migrate_data.py --js data.js --out data.json    # dry run
python scripts/migrate_data.py --js data.js --out data.json --write
```

Reads `data.js` with node when it is available and a quote-aware scanner when
it is not, then adds an `id` and a seeded `evidence` entry to every deal.

Two things worth knowing:

- **`index.html` does not change.** `data.js` stays exactly where it is and
  keeps the same shape; it is now generated from `data.json` on every applied
  run. Editing moves to the JSON, and the site is none the wiser.
- **The seeded evidence matters.** Each deal gets one synthetic item built
  from the `src`, `tier`, `date` and `status` it already carries, at
  `urn:transferintel:seed:<id>`. Without it the first scoring run sees every
  deal as unsourced and rewrites the entire file on day one.

## Every day

```bash
# phases 1 and 2
python scripts/run_ingest.py --data data.json --out build

# phases 3 and 4
python scripts/run_editorial.py --data data.json --evidence build/evidence.json \
    --out build --apply
```

With no `ANTHROPIC_API_KEY` set, both model phases go quiet and everything
else still runs, so the pipeline is fully exercisable offline. To replay a
saved day instead of fetching, pass `--articles build/articles.json`.

Outputs, all in `build/`:

| File | What it is |
|---|---|
| `articles.json` | the fetched article set, replayable for evals |
| `evidence.json` | what phase 3 consumes, keyed by deal id |
| `candidates.json`, `candidates.md` | transfers we do not track, for review |
| `needs_review.json` | claims that resolved to nothing, and why |
| `ingest_stats.json` | counts for the run log |
| `patch.json`, `patch.md` | the operations, and the PR body |

## Phase 1 rules

Stdlib only, so there is nothing to break in three years of unattended runs.

- Feeds and the domain to tier map live in `sources.py`. An unknown domain is
  tier 3, which is the safe direction to be wrong in
- 36 hour window by default
- Dedupe on canonical URL and on a title key that strips transfer boilerplate,
  keeping the best-tier copy. The same wire story from Sky and the Guardian
  collapses to the Sky one
- Articles that mention no tracked club are dropped before any model sees
  them, which is most of the day's football news and most of the cost
- Generic one-word aliases ("United", "City", "Forest") are excluded from that
  filter, because they appear in ordinary prose constantly
- Any feed that fails is skipped silently. A quiet day is always acceptable,
  a broken site never is

## Phase 2 rules

The model half is deliberately mechanical: read a headline, say whether it is
a transfer claim and what stage it asserts. Batched twenty at a time through
Haiku, with the rules block cached.

Everything downstream of that is code:

- **Club names** resolve through an alias table to strings that exist in
  `data.json`. The site matches dashboards on exact equality, so "Man Utd"
  and "Manchester United" being different strings is a real bug
- **Player names** fold through transliteration and accent stripping, then
  match on surname. Odegaard has to equal Ødegaard or every headline about
  him resolves to nothing
- **The destination club is authoritative.** If a claim names a buying club
  that disagrees with the deal, it is not that deal. "Man Utd eye Everton
  target Okafor" must never become evidence on the Everton row
- **A claim naming no club at all resolves to nothing** and goes to review
- **Currency conversion happens in code**, from the number and symbol the
  model reported. One constant to update, visible in git history
- **New deals are never created automatically.** An unrecognised transfer
  with both clubs named becomes a candidate in `candidates.md`; a human adds
  the row. Creating a deal is the one action with no safe undo once the site
  has been seen

## Phase 3 rules

Credibility:

```
cred = base + corroboration + stage - decay,  clamped to 0..100
```

| Term | Rule |
|---|---|
| base | 55 / 35 / 15 by the best source tier published in the last 3 days. With nothing recent, the best tier that ever carried the deal, minus 10 |
| corroboration | 5 per additional distinct tier 1 or 2 outlet, capped at 15. Three tabloids are not corroboration, and the same outlet twice is not either |
| stage | 0 interest, 10 talks, 25 agreed, 35 medical, 45 completed. Tier 3 sources cannot claim a stage. When the window is silent the deal keeps the stage it already reached |
| decay | 2 per day since the last mention, capped at 30 |

Pins and guards:

- `done` pins cred to 100, `collapsed` pins it to 0
- outside those, cred moves at most 25 points in one run

Status, in precedence order:

1. `done` is terminal
2. collapse must be explicitly claimed, and silence is never collapse
3. a collapsed deal only revives on a tier 1 claim, and only back to `talks`, flagged
4. status advances at most one rung per run, and skipped rungs raise a flag
5. reaching `done` also needs a tier 1 source explicitly claiming completion
6. status never regresses
7. only tier 1 and 2 sources move the ladder at all

Fees move only on a tier 1 or 2 quote and only by 0.5m or more.

Everything tunable lives in `ScoringConfig`. Change a constant, rerun the
golden set, compare.

## Phase 4 rules

The model gets the deal, its recent sources, and the status and credibility
phase 3 just computed, and returns one sentence. It never sees a request for
a number, a status or a club name.

- Notes regenerate only when the status changed, the fee changed, cred moved
  8 points or more, or the note is empty. Regenerating everything daily burns
  money and makes the diff unreviewable
- The prompt describes the deal in its post-patch state, not the stale one
- The rules block is identical on every call and is sent with
  `cache_control: ephemeral`
- Style validation is code, not prompt: one sentence, 6 to 30 words, no em or
  en dash, no exclamation, no rhetorical question, no hedging openers, must
  end in a period
- One retry with the rejection reason fed back, then the previous note is
  kept and a flag is raised. A stale note is always better than a bad one
- Budget capped at `--max-notes`, default 12. API errors fail soft

## Phase 5 rules

The gate assumes the pipeline is wrong and looks for the specific ways that
would matter. It applies the patch to a copy, revalidates every deal from
scratch, and checks invariants against the result, deriving its own limits
rather than trusting `ScoringConfig`.

**Hard** failures abort the run. Nothing is written, the patch is saved to
`build/aborted_patch.json`, the job exits 2. Reserved for anything that would
put a false statement on a live site:

- a completion with no fetchable source, which is the failure that matters most
- moving a deal that is already `done`
- status going backwards or skipping a rung
- credibility moving more than 25 points outside a pinned status, or landing
  out of range
- a fee outside 0 to 300m, which is almost always a currency fault
- a note containing a banned dash, or over the word limit
- an unexplained operation, an unfetchable evidence URL, an unknown deal id
- more than 15 updates, 6 completions or 4 collapses in one run
- a patch that changes how many deals exist

**Soft** failures ride along in the PR body and never block, because a gate
that cries wolf is a gate you stop reading: a buying club with no dashboard
entry, a fee that more than doubled, a deal with no evidence, evidence dated
in the future.

Every failure is reported in one pass. Fixing one thing at a time and
rerunning between each is how a five minute review becomes an hour.

## Phase 6 rules

```bash
python scripts/run_evals.py                     # pipeline, free, no key
python scripts/run_evals.py --suite extraction  # needs a key, a few cents
```

The **pipeline** suite replays a recorded day: the state as it was, the
articles published, and the claims the model produced, captured once as a
cassette in `claims.json`. No key, no network, byte-identical every time,
which is why it runs on every commit.

The **extraction** suite is the only one that needs a model. It runs
headlines whose correct reading is known and scores the extractor. Run it
when you touch the extraction prompt.

Assertions are loose about what should be free to move and strict about what
must not. `expected.json` says "this deal must reach done" and "this one must
not", never "the patch must be exactly these fourteen operations". Pinning
the exact op list means every constant tweak fails every case, and a suite
that always fails is a suite nobody runs.

`false_completions` must be zero. It is the one metric with no acceptable
trade-off.

Record a new case with `--record`, then edit the generated `expected.json`
down to the assertions that capture why the day mattered.

## Failure modes and what happens

| Situation | Behaviour |
|---|---|
| No `ANTHROPIC_API_KEY` | Both model phases run dry, everything else unaffected |
| A feed is down | Skipped, counted in `ingest_stats.json` |
| Every feed is down | Empty evidence, pure decay pass |
| Phase 2 produced nothing | Pure decay pass, still a useful run |
| Model returns unparseable JSON | Batch retried once, then dropped and logged |
| A transfer we do not track | Candidate in the PR body, `data.json` untouched |
| The gate finds a hard failure | Exit 2, no PR, `aborted_patch.json` written |
| Scorer and gate limits disagree | The gate wins and aborts. Two implementations disagreeing is exactly when you want stopping |
| Phase 3 wants more than `--max-changes` updates | Abort with exit 2, patch saved as `aborted_patch.json`, nothing written |
| Model returns garbage twice | Old note kept, flag in the PR |
| Tabloid claims a completed transfer | No status change, no stage bonus, cred typically falls |
