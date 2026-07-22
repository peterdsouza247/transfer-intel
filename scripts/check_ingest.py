#!/usr/bin/env python3
"""Fail the run when ingestion produced nothing, instead of publishing silence.

    python scripts/check_ingest.py --stats build/ingest_stats.json

The failure this exists to catch is not a crash. It is a success.

Phase 1 and 2 are wrapped in `continue-on-error: true` so that a dead feed
cannot stop the day's decay pass from publishing, and a following step writes
an empty evidence file when none exists. Both of those are correct in
isolation. Together they mean a pipeline with no API key fetches nothing,
extracts nothing, attaches nothing, and reports a green tick, every day,
indefinitely. The site keeps updating, so nothing looks wrong: credibility
decays, the timestamp moves, a commit lands. It just never learns anything
new, and the newest deal on the site stays where it was the day ingestion
broke.

The three conditions below separate "nothing happened today" from "nothing
can happen any day". The first is news. The second is an outage, and an
outage should be loud.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


def summary(text: str) -> None:
    """Write to the GitHub Actions job summary when running in CI."""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(text + "\n")
    print(text)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stats", type=Path, default=Path("build/ingest_stats.json"))
    ap.add_argument("--warn-only", action="store_true",
                    help="report but never fail, for local runs without a key")
    args = ap.parse_args()

    if not args.stats.exists():
        summary("## Ingestion FAILED\n\n"
                f"No `{args.stats}` was written, so phase 1 or 2 died before "
                "it could report. Check the Ingest and extract step's log.")
        return 0 if args.warn_only else 1

    try:
        stats = json.loads(args.stats.read_text(encoding="utf-8"))
        if not isinstance(stats, dict):
            raise ValueError("stats file is not an object")
    except (ValueError, OSError) as exc:
        # A health check that raises is worse than no health check: the job
        # fails with a traceback about JSON rather than the actual problem,
        # which is that ingestion did not finish writing.
        summary("## Ingestion FAILED\n\n"
                f"`{args.stats}` could not be read ({exc}). It is usually "
                "truncated, which means the run was interrupted partway "
                "through phase 1 or 2.")
        return 0 if args.warn_only else 1
    ingest = stats.get("ingest", {})
    extract = stats.get("extract", {})

    articles = extract.get("articles", 0) or ingest.get("articles", 0)
    claims = extract.get("claims", 0)
    resolved = extract.get("resolved", 0)
    feeds = ingest.get("feeds", 0)
    failed = ingest.get("feeds_failed", 0)

    lines = [
        "## Ingestion",
        "",
        f"- Feeds: {feeds - failed} of {feeds} responded",
        f"- Articles in window: {articles}",
        f"- Claims extracted: {claims}",
        f"- Attached to existing deals: {resolved}",
        f"- New candidates: {extract.get('candidates', 0)}",
        "",
    ]

    problems: list[str] = []

    if stats.get("dry_run"):
        problems.append(
            "**No ANTHROPIC_API_KEY.** The extractor ran in dry mode, so it "
            "read nothing and returned no claims. Every run in this state is "
            "a pure decay pass: the site will keep updating and will never "
            "gain a new deal. Add the key under Settings, Secrets and "
            "variables, Actions."
        )

    if feeds and failed == feeds:
        problems.append(
            f"**Every feed failed.** All {feeds} sources were unreachable. "
            "Usually transient; persistent means a feed URL has moved and "
            "needs updating in sources.py."
        )
    elif articles == 0 and not stats.get("dry_run"):
        problems.append(
            "**Zero articles in the window.** During an open window this is "
            "an outage, not a quiet day. Check the window length and whether "
            "the feeds have changed format."
        )

    if problems:
        lines += ["### Problems", ""] + [f"- {p}" for p in problems]
        summary("\n".join(lines))
        return 0 if args.warn_only else 1

    if articles and claims == 0:
        # Genuinely possible: a day of articles about matches rather than
        # transfers. Worth saying, not worth failing over.
        lines.append(
            "Note: articles were read but no transfer claims were found. "
            "That happens on quiet days and is only a concern if it repeats."
        )

    summary("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
