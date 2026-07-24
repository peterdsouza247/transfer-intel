# The daily refresh, by hand

The routine for keeping the site current while there is no `ANTHROPIC_API_KEY`.
Budget fifteen minutes. Once the key is in, all of this becomes
`editorial.yml` running at 07:00 while you sleep, and this file is only
useful for feed outages.

The order matters. Steps 3 and 5 are where new deals enter, and they are the
two most commonly skipped.

---

## 1. Fetch, with the window set to cover the gap

```bash
python scripts/run_ingest.py --data data.json --out build --window-hours 48
```

Default is 36 hours. Widen it to cover however long since the last refresh,
plus a day of overlap. Feeds only hold twenty to fifty items, so a gap longer
than about a week loses news permanently, whatever you set here.

This fetches and filters but extracts nothing without an API key. Read the
filter line it prints. If a real transfer story appears in the dropped
samples, add the missing word to `TRANSFER_TERMS` in `prefilter.py`.

## 2. Draft the claims, then correct them

```bash
python scripts/draft_claims.py --articles build/articles.json --data data.json
```

This writes **both** `manual/claims.json` and `manual/articles.json`, as a
matched pair. The pair matters: `run_ingest --claims` needs the articles to
attach a source, date and tier to each claim, so a claim whose article is
missing resolves to nothing. If that happens the run now says so rather than
skipping it quietly.

It drafts by pattern matching: known club names in
order of appearance, a stage from keyword hints, a fee from any currency
figure, and a player from capitalised runs that are not clubs. It gets most
headlines right and says so when it does not.

Then **read every entry**. Each carries `_draft: true` and a `_review` list
naming what it could not settle: which club is buying, whether the
capitalised words are a person, whether the stage keyword means what it
looks like. Correct the claim, delete its `_draft` and `_review` keys, and
delete any claim you do not want.

`run_ingest.py` refuses a file that still contains `_draft: true`, so a
guessed claim cannot enter the dataset unread. That interlock is the point:
the draft saves the typing, not the judgment.

Record what each article **claimed**, not what you believe. If two outlets
disagree, keep both claims and let corroboration scoring decide. Picking a
winner by hand is the one thing this pipeline exists to avoid.

Record what each article **claimed**, not what you believe. If two outlets
disagree, write both claims and let corroboration scoring decide. Picking a
winner by hand is the one thing this pipeline exists to avoid.

```bash
python scripts/run_ingest.py --data data.json --out build \
  --articles manual/articles.json --claims manual/claims.json
```

## 3. Add the new deals

**This is the step that gets skipped, and skipping it is why the site can go
a week without a new transfer while appearing to update daily.**

Transfers involving players you do not track land in `build/candidates.json`
and nothing reads that file automatically.

```bash
python scripts/add_candidates.py --list
python scripts/add_candidates.py \
  --add <id> --age 24 --pos AM --note "One sentence, no dashes." --apply
```

`--age` and `--pos` are required. Neither can be read off a headline, and both
change the score.

## 4. Score everything

```bash
python scripts/run_editorial.py --data data.json \
  --evidence build/evidence.json --out build --no-notes --recent-days auto
cat build/patch.md
```

`--recent-days auto` sizes the window from the age of the evidence in hand.
The default is three days, so an announcement from last Tuesday is otherwise
scored as history and moves nothing. **A correct claims file that appears to
do nothing is almost always this**, which is why `auto` now exists and why
the scheduled workflow passes it.

Read the patch, then apply:

```bash
python scripts/run_editorial.py --data data.json \
  --evidence build/evidence.json --out build --no-notes \
  --recent-days auto --apply
```

## 5. Retire what the new deals replaced

When a player you track signs somewhere else, the old record is now wrong.
Nothing does this automatically, because deciding that Arsenal have lost a
player rather than merely gone quiet is a judgment.

In `data.json`, on the superseded record:

```json
"status": "collapsed",
"cred": 0,
"base_cred": 0,
"collapse_reason": "hijacked",
"collapse_narrative": "Chelsea completed the signing for 117m on 21 July.",
"pivot_to": "rogers-aston-villa-chelsea"
```

`pivot_to` is what makes the two records one story on the site rather than a
completion and an unexplained disappearance.

## 6. Rebuild and check

```bash
python scripts/render_site.py --data data.json --template index.html --out .
python scripts/check_site.py
python -m pytest
```

Then commit. Pages serves what is in the repository, so the generated pages,
sitemap, feed and OG images all have to be committed.

---

## The five-minute version

```bash
python scripts/run_ingest.py --data data.json --out build --window-hours 48
python scripts/draft_claims.py --articles build/articles.json --data data.json
# read and correct manual/claims.json, delete the _draft keys
python scripts/run_ingest.py --data data.json --out build \
  --articles manual/articles.json --claims manual/claims.json
python scripts/add_candidates.py --list          # then --add each one
python scripts/run_editorial.py --data data.json \
  --evidence build/evidence.json --out build --no-notes --recent-days auto --apply
# mark superseded records collapsed, with pivot_to
python scripts/render_site.py --data data.json --template index.html --out .
python scripts/check_site.py && python -m pytest
```

---

## When it looks like nothing happened

**Read what step 4 prints.** It now names anything held back and why, rather
than reporting a count. "1 flags" scrolling past is how a deal whose evidence
said completed sat at `medical` for a day with no visible reason.



**"0 status changes" after a correct claims file.** Almost always
`--recent-days`. Evidence outside the window is scored as context and moves
nothing.

**"GATE FAILED, N updates, limit is 15."** Expected after a gap: accumulated
decay alone exceeds it. Raise `--max-changes`, and read `build/patch.md`
before applying, because raising it removes the main protection against an
upstream fault rewriting the site in one run.

**New signings still missing.** Check `build/candidates.md`. They are almost
certainly sitting there waiting for step 3.

**A deal moved, but not as far as the evidence says.** The status ladder
advances one rung per run, so a deal at `rumor` cannot reach `done` however
strong the reporting. The exception is a tier 1 completion marker on a deal
already at `agreed` or beyond, which crosses the rest in one run: the clubs
have settled terms, so completion is the expected next event rather than a
leap. Anything held back is now named in the run output.

**"N claim(s) could not be resolved."** A claim's `article_url` is not in
`manual/articles.json`. Usually a hand-edited claim, or an article deleted
from the set after the claim was written. Let `draft_claims.py` write both
files and this cannot happen.

**Feeds returning mostly irrelevant news.** Run `python
scripts/check_feeds.py` to see which respond and how much each contributes
after filtering. A feed that fails every day should be removed from
`sources.py` rather than left producing a warning nobody reads.

**A club dashboard is empty.** The club name in the deal does not exactly
match a key in `clubs`. `canonical_club` should prevent this; if it does not,
the alias table and `data.json` have drifted apart and
`test_club_aliases_match_the_dataset_house_style` will say so.
