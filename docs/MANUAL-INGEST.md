# Running without an API key

`run_ingest.py --claims` loads pre-extracted claims and skips phase 2. Phase 1
still fetches and phases 3 onward are untouched, so resolution, marker
detection, deduping, scoring and the gate all run exactly as they would on
model output.

That last point is the reason this path exists rather than hand-editing
`data.json`. A curated claim gets no privileges: it still cannot promote a
deal to `done` without a tier 1 source carrying a completion marker, still
cannot skip a rung, still counts against the daily update limit. The gate does
not know or care where the claim came from.

## Usage

```bash
python scripts/run_ingest.py \
  --data data.json --out build \
  --articles manual/articles.json \
  --claims manual/claims.json

python scripts/run_editorial.py \
  --data data.json --evidence build/evidence.json --out build

cat build/patch.md          # read before applying
cat build/candidates.md     # deals not yet tracked
```

## The two input files

`articles.json` is the provenance. One entry per source article:

```json
[{"url": "https://...", "title": "...", "summary": "...",
  "published": "2026-07-21", "outlet": "ESPN", "tier": 1}]
```

`claims.json` is what each article asserted. One entry per claim, not per
article, since one article can carry several:

```json
[{"is_transfer_claim": true, "player": "Christos Tzolis",
  "from_club": "Club Brugge", "to_club": "Arsenal",
  "reported_stage": "agreed", "fee_amount": null, "fee_currency": null,
  "article_url": "https://..."}]
```

`reported_stage` is one of `none`, `interest`, `talks`, `agreed`, `medical`,
`completed`, `collapsed`. Report what the article said, not what you believe.
That distinction is the whole discipline: the pipeline's job is to score
claims, and it can only do that if the claims are recorded honestly.

Outlet tiers come from `sources.py`. Do not promote an outlet to tier 1 to
make a deal score better; the tiering is a standing judgment about track
record, not a per-deal dial.

## What to watch for

**Marker false positives.** Completion markers are matched against the whole
article text, so a roundup headed "every completed transfer today" can stamp a
marker on a claim about a deal that is merely agreed. It is harmless in
practice, since promotion to `done` also needs tier 1 and a `completed`
stage, but keep summaries specific to the claim rather than pasting a page
title.

**Sources disagree, and they disagree more than you would expect.** ESPN,
FootballTransfers and the club sites carry different fees for the same
transfer, and roundup pages go stale without saying so. When two sources
conflict, record both claims and let corroboration scoring do its job.
Picking a winner by hand is the one thing this pipeline exists to avoid.

**Candidates are not deals.** Anything for an untracked player lands in
`build/candidates.md` rather than the dataset. Adding it is a separate,
deliberate act.

## This is a stopgap

Doing extraction by hand does not scale and it is not meant to. The pipeline
was built around a model reading roughly thirty articles a day on Haiku,
which at current prices is on the order of a few pence per day, and single
figures of pounds for the rest of a window. Set `ANTHROPIC_API_KEY` and this
whole document becomes unnecessary.
