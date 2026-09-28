# TransferIntel source credibility: Premier League · Summer Window 2026

Snapshots: Premier League · Summer Window 2026: Sep 24, 2026 · 69 tracked window/deal records · 20 attributed sources (1 named writer, 19 publications).

## What the data says

There are **20 resolved source calls** and **54 unresolved calls**. The latter are excluded from accuracy. A source call is a positive transfer claim linked before resolution, counted once per source and deal. Multiple sources can be credited on one deal, so these totals are not counts of distinct transfers.

This is an **exploratory sample**, not a league-wide ranking of reporters. 0 sources have at least five resolved calls in this snapshot; the named-journalist sample covers only 1 explicitly attributed writer. Do not present a one-call score as a proven reliability rating. The data reflects tracked deals and recorded evidence, not every story each outlet published.

## How to read the files

- `source-summary.csv`: one row per identified source, with hits, misses, pending calls, raw hit rate and confidence-adjusted score. Blank scores mean no resolved calls. `small_sample` flags fewer than five resolved calls.
- `source-calls.csv`: one row per source and deal, with the **earliest eligible** claim, outcome, resolution date and supporting URL. 12 calls point to internal `urn:` seed references rather than independently retrievable web articles; `evidence_type` marks them for audit. These rows need a retrievable primary link before being pitched as independently verifiable evidence.
- The site’s [sortable source index](https://peterdsouza247.github.io/transfer-intel/) presents the same calculation.

## Method

Only claims of interest, talks, agreement or medical progress count. Claims dated on or after a deal’s resolution are excluded. A completed transfer is a hit; a collapsed tracked move is a miss; all other statuses remain unresolved. Raw hit rate = hits / resolved calls. Credibility score = round(100 × (hits + 2) / (resolved + 4)). This neutral two-hit, two-miss prior tempers small samples. The score describes **this recorded sample**; it is not a probability that the next story is true.

Attribution labels are normalized by the site’s `source_stats.py`. A publication and an explicitly named journalist can each get credit for the same deal. A public report should show both the denominator and unresolved count whenever quoting a score.

## Reuse and enquiries

These exports are derived from the public transfer dataset under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/); credit TransferIntel and link to https://peterdsouza247.github.io/transfer-intel/. Third-party articles remain with their publishers. Existing public data can be reused commercially with attribution. For commissioned research, expanded coverage, tailored exports or regular delivery, contact **peterbrendanwrites@gmail.com**. No exclusive rights to this public snapshot are implied.

Available on enquiry:

- **Window report:** a dated editorial brief on the tracked calls, outcomes and patterns for one window, plus methodology, sample-size limits and linked summary and call-level CSVs.
- **Custom source or club audit:** agree sources, clubs and dates; check the recorded claims against retrievable original articles and outcomes; receive an evidence log, any corrections, and a written assessment of what the sample supports.
- **Scheduled data delivery:** documented CSV snapshots on an agreed schedule, with window IDs, source calls, outcomes, evidence links and a change log. Agree cadence and coverage in advance.

Send your desired scope, format and deadline to **peterbrendanwrites@gmail.com**. Fees depend on the work. Payment covers research, verification, tailoring or delivery, not exclusive rights to the public data.
