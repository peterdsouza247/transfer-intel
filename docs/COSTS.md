# Keeping the credit spend down

Two phases cost money: extraction (phase 2) and notes (phase 4). Everything
else is free. Fetching feeds, scoring, the gate, rendering and the digest are
all ordinary code.

## What was changed, and why

**A prefilter in front of the extractor.** `prefilter.py` drops anything with
no transfer signal before a token is spent.

**How much it saves depends entirely on your feeds, and the first estimate
here was wrong.** It was based on a sample of general BBC football headlines,
where one story in nine was about a transfer, and predicted a cut of roughly
85 percent. On the feeds actually configured in `sources.py` the real figure
is closer to 16 percent, because the feeds carry more transfer news
than a general football feed would. Note that Sky's 12040 is their general
football news id, not the transfer centre, despite what an earlier version of
this file claimed. The
filter is still worth having, and it is worth less than first claimed.

Recall is unaffected and is the number that matters: it keeps 100 percent of
known transfer articles in the eval fixtures, including the ones about
transfers being resisted rather than completed.

**A seen-article cache.** The ingest window is 36 hours and the job runs
every 24, so roughly a third of each day's articles were already read and
paid for yesterday. Their evidence is attached to the deals already. Reading
them again buys nothing. `logs/seen-articles.json` holds URLs for seven days,
then forgets them.

**Notes moved from Sonnet to Haiku.** A note is one sentence of 6 to 30
words, written against an explicit rules block and validated on the way out
with a retry that quotes the specific violation back. That is a task shaped
for a small model. Haiku costs roughly a third of Sonnet per token.

Two things were already right and were left alone: the system prompts on both
phases are marked for prompt caching, so the fixed instruction block is
charged at a fraction of the base rate after the first call, and `needs_note`
already refuses to rewrite prose for a deal whose credibility drifted two
points from silence.

## Rough shape of the saving

Extraction input falls by something like an order of magnitude: the prefilter
removes most articles, and the cache removes a third of what survives. The
notes phase falls to about a third of its previous cost. The remaining spend
during a live window is small enough that a spending cap of a few pounds in
the Anthropic console is the practical control rather than anything in this
repository.

Check current per-token rates on Anthropic's pricing page rather than
trusting a number written down here, which will go stale.

## Tuning

The filter is deliberately generous, and the asymmetry behind that is worth
stating plainly. A false positive costs a fraction of a penny: one extra
article in a batch the model was going to read anyway. A false negative costs
a deal, silently, with nothing in any log to say a story was missed.

So when tuning `TRANSFER_TERMS`, the question is never "how much more can
this cut" but "what would it take to drop something real". Add terms freely.
Remove them only with a specific reason.

Every run prints what the filter did and a sample of what it dropped:

```
14 of 96 articles kept (85% filtered): 68 without a transfer signal,
9 from non-news sections, 5 already extracted on a previous run
  dropped, e.g.: Spain leave it late as super-sub Merino scores...
```

Read that sample occasionally. If a real transfer story appears in it, add
the word that would have saved it.

The same numbers land in `build/ingest_stats.json` under `filter`, so the
cut rate can be tracked over time.

## Levers not pulled

**Shrinking the article text sent per item.** Currently 400 characters. It
could go lower, but headline plus first sentence is where the stage and the
fee usually live, and losing a fee to save a few tokens is a bad trade.

**Dropping tier 3 outlets before extraction.** Tempting, and wrong. The
tabloid trap eval case exists because low-tier sources reporting something
first, then being corroborated, is a real signal the scoring model uses.
Filtering them out at ingest would remove information the pipeline is
designed to weigh.

**Running less often than daily.** This costs more, not less. A longer gap
means a wider window, more articles per run, and the catch-up procedure in
`docs/CATCHUP.md` with its raised limits.
