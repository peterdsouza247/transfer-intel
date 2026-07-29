# Turning the automation on

You have the API key in repository secrets. This is the rest of it: what to
check, how to test without spending much, and what stays manual.

---

## 1. Test the key first, for a hundredth of a penny

```bash
export ANTHROPIC_API_KEY=sk-ant-...
python scripts/check_api_key.py
```

One thirty-token call per model. It answers the only question worth asking
before enabling a schedule: does the key work, and is the model this project
names still reachable under it.

Note that your shell and the repository secret are separate. Passing locally
does not prove the secret is set correctly, and the reverse.

## 2. Then a real run, on demand

**Actions, Editorial refresh, Run workflow**, with **Skip phase 4** ticked.

That runs everything except note writing: fetch, filter, extract, resolve,
score, gate, render, and open a pull request. Notes are the smaller half of
the cost but the only part that writes prose, so skipping them on the first
run means the output is entirely arithmetic and easy to check.

Read the PR. It contains the full patch, the candidates, and now the cost of
the run. If it looks right, merge it and run again without the tick.

## 3. What to check in that first PR

**The diff includes rendered pages.** `deals/`, `clubs/`, `index.html`,
`sitemap.xml`, `feed.xml`, `og/`. If it only touches `data.json` and
`data.js`, the `add-paths` list has regressed and everything except the
browser app is frozen.

**Tiers look right.** In `build/ingest_stats.json`, BBC and Sky articles
should be tier 1. A tier 3 BBC article means a feed is emitting an unmapped
hostname; `test_every_configured_feed_host_resolves_to_a_known_tier` should
catch it first.

**The filter is still cutting.** The cost report prints the rate. A sudden
drop to near zero usually means a feed changed shape, not that the news got
more relevant.

**Nothing reached `done` without a marker.** The gate enforces this and fails
the build otherwise, so a green run is already evidence, but the first time is
worth reading properly.

---

## What is still manual, and why

**Merging the pull request.** Deliberate. The gate is a good reviewer and it
is not an editor. If you would rather it merged itself, enable auto-merge on
the `auto/editorial` label; the digest at 06:30 UTC reads `main`, so an
unmerged PR means the morning email describes yesterday.

**New deals.** Transfers involving players nobody tracks land in
`build/candidates.json` and ride along in the PR body. Promoting one needs
`--age` and `--pos`, neither of which can be read off a headline, so
`add_candidates.py` asks for them.

**Retiring a deal a player left.** Marking the old record collapsed with a
`pivot_to` is a judgment about whether a club lost a target or merely went
quiet. Nothing infers it.

---

## Cost

Roughly **$0.03 a day, under $1 a month** at the current article volume, on
Haiku 4.5 with the system prompts cached.

Four things keep it there, and three were already in place:

- **The prefilter** drops articles that cannot be about a transfer before a
  token is spent.
- **The seen cache** means the overlap between a 36 hour window and a 24 hour
  schedule is not paid for twice.
- **Prompt caching** on both phases, so the fixed instruction block is charged
  at a fraction of the base rate after the first call.
- **`--max-batches 12`** caps a single run at 240 articles, which is the
  backstop if a feed starts returning a thousand items.

Set a **$5 monthly limit** in the Anthropic console anyway. It costs nothing
and turns the worst case into a number you chose.

Every run prints what it spent into the job summary. The reason to look is not
the total, it is the trend: a filter regression or a feed change can triple
the article count, and the bill is the only place that shows up.

### What was deliberately not done

**Sending less article text.** 400 characters per article. Lower would save a
little, and the fee and the stage usually live in the first sentence or two,
so it would start costing accuracy quickly.

**Deduplicating the same story across outlets.** Sixteen feeds covering one
transfer is not waste: independent corroboration is a scoring input, and
collapsing those into one report would remove the signal the model is built
on.

**Moving notes to a smaller model.** They are already on Haiku.

---

## Things that will bite eventually

**GitHub disables scheduled workflows after 60 days without a repository
commit.** The daily PR keeps the clock reset while it is running, so this only
matters after a long quiet spell. If the schedule stops firing, that is the
first thing to check.

**Deadline day, 1 September.** The window closes 23:00 BST, which is 03:30 IST
on the 2nd. A daily 04:00 UTC run will miss the entire closing evening. Plan
manual runs through that night.

**The volume gate.** Fifteen editorial changes a day. Decay no longer counts
toward it, so an ordinary day is nowhere near, but a genuinely busy deadline
day will hit it and stop. That is the gate working; raise `--max-changes` for
that run and read the patch properly before merging.
