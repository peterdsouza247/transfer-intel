#!/usr/bin/env python3
"""What the last run cost, from the token counts it recorded.

    python scripts/report_cost.py --stats build/ingest_stats.json

Spend on this project is small enough that the danger is not a large bill,
it is an unnoticed one: a feed change or a filter regression quietly triples
the article count and nothing says so until the month ends. This prints the
number after every run and writes it into the GitHub job summary, so the
trend is visible without anyone going looking.

Rates are a local guess and go stale. They are only used to turn token counts
into something a person can react to, and the token counts themselves are
measured. Check current pricing at anthropic.com/pricing.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

#: US dollars per million tokens. Haiku 4.5 at the time of writing.
RATE_IN = 1.00
RATE_OUT = 5.00
#: Cached input is billed at a fraction of the base rate on a read.
RATE_CACHE_READ = 0.10


def summary(text: str) -> None:
    print(text)
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(text + "\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stats", type=Path,
                    default=Path("build/ingest_stats.json"))
    args = ap.parse_args()

    if not args.stats.exists():
        print(f"No {args.stats}, nothing to report.")
        return 0
    try:
        stats = json.loads(args.stats.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        print(f"{args.stats} is unreadable, skipping the cost report.")
        return 0

    extraction = stats.get("extraction") or {}
    tokens_in = extraction.get("input_tokens", 0)
    tokens_out = extraction.get("output_tokens", 0)
    cached = extraction.get("cache_read_tokens", 0)
    calls = extraction.get("calls", 0)

    if not (tokens_in or tokens_out or cached):
        summary("## Extraction cost\n\nNo token counts recorded. Either the "
                "run was a dry run, or extraction did not reach the API.")
        return 0

    cost = (
        (tokens_in / 1e6) * RATE_IN
        + (tokens_out / 1e6) * RATE_OUT
        + (cached / 1e6) * RATE_CACHE_READ
    )
    filt = stats.get("filter") or {}

    summary(
        "## Extraction cost\n\n"
        f"- **${cost:.4f}** this run, about **${cost * 30:.2f}** a month at "
        "this rate\n"
        f"- {calls} API call(s), {tokens_in:,} input, {tokens_out:,} output, "
        f"{cached:,} read from cache\n"
        f"- {filt.get('kept', 0)} of {filt.get('seen', 0)} articles reached "
        f"the model ({filt.get('cut_rate', 0):.0%} filtered out first)\n\n"
        "Rates are a local estimate and go stale; token counts are measured. "
        "A sudden jump usually means the prefilter stopped catching something, "
        "not that transfer news got busier."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
