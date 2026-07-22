# TransferIntel

A Premier League transfer window site that does one thing no fee list does:
it tells you how much to believe each rumour, and shows its working.

Every tracked deal carries a credibility score from 0 to 100 that is
**computed, never written**. It rises with the tier of the outlets reporting
it and with independent corroboration, and it decays when a deal goes quiet.
A transfer is only marked completed when a tier 1 source uses language that
only applies after the fact, or the club announces it itself.

Live: https://peterdsouza247.github.io/transfer-intel/

---

## Documentation

| Read this | When |
|---|---|
| [docs/DEPLOY.md](docs/DEPLOY.md) | first run, verify, ship |
| [docs/OPERATIONS.md](docs/OPERATIONS.md) | the daily routine and the scheduled jobs |
| [docs/INSTALL.md](docs/INSTALL.md) | what the files are and what to read in what order |
| [docs/NEWSLETTER.md](docs/NEWSLETTER.md) | email capture and the digest |
| [docs/ANALYTICS.md](docs/ANALYTICS.md) | what to measure |
| [docs/COSTS.md](docs/COSTS.md) | keeping the credit spend down |
| [docs/CATCHUP.md](docs/CATCHUP.md) | the newest deal is days old, what now |
| [docs/DAILY-REFRESH.md](docs/DAILY-REFRESH.md) | **the by-hand daily routine, start here if there is no API key** |
| [docs/MANUAL-INGEST.md](docs/MANUAL-INGEST.md) | the file formats behind that routine |
| [docs/DISCOVERABILITY.md](docs/DISCOVERABILITY.md) | SEO, pre-rendering, the manual steps |
| [docs/backlog.md](docs/backlog.md) | what is built, what is open, what was rejected |
| [CHANGELOG.md](CHANGELOG.md) | what changed and why |

---

## How it actually works

The site is static and hosted on GitHub Pages. There is no server. Everything
dynamic is generated at build time by a Python pipeline that runs on GitHub
Actions and opens a pull request you approve in one click.

```
                  RSS feeds (BBC, Guardian, Sky, F365, Telegraph)
                                    |
   phase 1  ingest.py --------------+  stdlib only, fails soft
                                    |
   phase 2  extract.py -------------+  LLM reads headlines, reports claims
                                    |  and nothing else
   phase 3  scoring.py -------------+  pure code, no model, deterministic
                                    |
   phase 4  notes.py ---------------+  LLM writes one sentence per changed deal
                                    |
   phase 5  validate.py ------------+  THE GATE. Assumes everything above
                                    |  is wrong and looks for the ways that
                                    |  would matter
   phase 6  data.json / data.js ----+  the source of truth
                                    |
   phase 7  render_site.py ---------+  index, deal pages, club pages,
                                       sitemap, feed, OG cards, llms.txt
```

The division that matters: **a model never decides anything numeric.** It
reads an article and reports what the article claimed. Every score, status
and threshold is arithmetic in `scoring.py`, which is why a credibility of 78
can always be explained by pointing at a breakdown, and why replaying
yesterday's evidence produces byte-identical output.

### The gate

`validate.py` is the reason this can run unattended. It re-derives its own
view of the world, applies the patch to a copy, and checks invariants against
the result. Two severities:

- **Hard** failures abort the run. Nothing is written, the patch is saved for
  inspection, the job exits non-zero. A day with no update is always
  acceptable. A day with a fabricated completed transfer is not.
- **Soft** failures ride along in the pull request as things to look at. They
  never block, because a gate that cries wolf is a gate you stop reading.

---

## The deal lifecycle

```
rumor -> talks -> agreed -> medical -> confirmed -> done
                                          |
                                     collapsed   (only ever explicit)
```

`confirmed` means **confirmed pending announcement**: a tier 1 source says the
deal is finished but nobody has announced it. It is capped at credibility 90.

`done` requires all three of:

1. A tier 1 source, and
2. a recorded **completion marker**, and
3. a non-null `completed_date`.

A completion marker is a phrase that only makes sense once a transfer has
happened, matched with hedge detection so that "completes his move" counts and
"set to complete his move" does not. Fabrizio Romano's "here we go" is scoped
to Romano specifically, because aggregators borrow it to mean "we think this
will happen". A URL on a club's own domain is the strongest marker available
and is flagged separately as `official`.

**Credibility 100 is only ever possible on a `done` record.** The gate fails
the build otherwise.

### Why this is spelled out at such length

Because the alternative was shipped and it was bad. An earlier version of the
pipeline treated any deal it re-encountered in a feed, or simply had not heard
had collapsed, as confirmation. Six deals sat on the live homepage marked
Completed at credibility 100 while their own summary text said "announcement
imminent" and, in one case, "then silence". Absence of contradicting news was
being read as positive proof.

A visitor who knows Trossard has not signed for Besiktas will not trust any
other number on the page. That is the whole product, so the completion rule is
the load-bearing wall.

---

## Repository layout

```
transfer-intel/
  index.html              the app. Design and browser logic
  data.json               source of truth, maintained by the pipeline
  data.js                 generated from data.json, what the browser loads
  deals/ clubs/ thanks/   generated pages, one per URL
  og/                     generated Open Graph cards
  conftest.py             pins test imports to this checkout
  scripts/
    transferintel/        the pipeline package
      ingest.py           phase 1, RSS
      prefilter.py        drops non-transfer articles before they cost anything
      extract.py          phase 2, claims
      markers.py          what counts as proof a transfer happened
      scoring.py          phase 3, all the arithmetic
      notes.py            phase 4, one sentence per deal
      validate.py         phase 5, the gate
      digest.py           the daily email
      site.py             phase 7, rendering
      entities.py         name resolution
      sources.py          outlet tiers and FX
      og.py               share cards
    run_ingest.py         phases 1 and 2
    run_editorial.py      phases 3 to 6
    run_digest.py         the daily email
    render_site.py        phase 7
    check_ingest.py       fails the run when ingestion produced nothing
    draft_claims.py       drafts manual claims for a human to correct
    add_candidates.py     promotes a detected candidate into a tracked deal
    check_site.py         post-build sanity check
    backfill_completions.py   completion audit
    migrate_data.py       phase 0, one time
    run_evals.py          golden set
    tests/                185 tests, all offline
  evals/                  recorded days, replayed on every run
  fixtures/               offline feeds and data
  docs/                   see the index above
  .github/workflows/      editorial refresh, digest, extraction evals
```

---

## Running it locally

```bash
pip install -r scripts/requirements.txt
python -m pytest                               # expect 185 passed
python scripts/run_evals.py --suite pipeline   # expect 3/3, 0 false completions
```

Both run offline with no API key. If either fails, stop there.

To rebuild the site from the current data:

```bash
python scripts/render_site.py --data data.json --template index.html --out .
```

This rewrites `index.html` in place. It is idempotent: generated head tags are
delimited at both ends and rebuilt, container contents are replaced rather
than appended, and running it twice produces a byte-identical file.

To see what the pipeline would change without changing anything:

```bash
python scripts/run_editorial.py --data data.json --out build --no-notes
cat build/patch.md
```

To read tomorrow's email today:

```bash
python scripts/run_digest.py --data data.json --out build/digest --segments
```

---

## Configuration

Everything window-specific lives in `data.json` under `config`, so a new
window is a data change rather than a code change.

| Key | Meaning |
|---|---|
| `windowName`, `tagline`, `deadline`, `deadlineLabel` | header and countdown |
| `site.baseUrl` | **required.** Canonical tags, OG URLs and the sitemap all need an absolute origin. The renderer refuses to run without it rather than guessing |
| `provenClubs` | selling clubs the value model treats as league-proven |
| `newsletter.provider` | `kit` or `buttondown` |
| `newsletter.action` | the provider's hosted form endpoint. **Empty means no capture form renders at all**, which is deliberate: a form posting nowhere collects addresses into a void |
| `analytics.cloudflareToken` | Cloudflare Web Analytics. Cookieless, no consent banner needed |
| `analytics.goatcounter` | optional second opinion on the numbers |

See `docs/NEWSLETTER.md` for provider setup and `docs/ANALYTICS.md` for what
to measure.

### Deal fields

| Field | Meaning |
|---|---|
| `id` | stable slug, also the deal page URL |
| `p`, `from`, `to` | player and clubs. Club names must match `clubs` keys exactly |
| `fee` | GBP millions, `0` for a free transfer |
| `age`, `pos` | both feed the value model. `pos` is one of `GK CB LB RB CM AM LW RW ST` |
| `status` | `rumor` `talks` `agreed` `medical` `confirmed` `done` `collapsed` |
| `tier` | 1 to 3, the best source carrying it |
| `cred` | 0 to 100, computed |
| `base_cred` | credibility before silence decay, so the site can show both |
| `last_verified_at` | moves **only** when a source reasserts the deal |
| `completion_marker`, `completion_source`, `completed_date` | the receipt for a `done` status |
| `collapse_reason`, `collapse_narrative` | why a deal died |
| `evidence[]` | every article that carried it, with tier, date, claim and marker |

`last_verified_at` is separate from anything the pipeline touches on a normal
run. That separation is what makes silence measurable, and it is what the
decay curve reads.

---

## House style

- **No em dashes or en dashes anywhere**, in code comments, copy, docs or
  generated content. Use commas, colons, semicolons, or the middot separator.
  The gate fails the build on a banned dash in a note.
- Notes are one sentence, 6 to 30 words.
- British spellings in reader-facing copy: rumour, not rumor. The status enum
  uses `rumor` for historical reasons and is not reader-facing.

---

## Licence

Two sets of terms, in `LICENSE`.

The **code** is proprietary, all rights reserved. Read it, run it locally,
quote it for review. Do not ship it.

The **dataset**, meaning the deal records and their generated pages, is CC BY
4.0. Share and adapt it, including commercially, with credit and a link back.
Every generated page declares this in its Schema.org markup, and `robots.txt`
admits the answer engines on purpose: this site is more useful to its author
the more widely it is cited.

CC BY covers the compilation, not the underlying reporting. The articles cited
in each record belong to the outlets that wrote them.

Scores are an assessment of how well supported a published claim is. They are
not predictions, not statements of fact, and not betting advice.
