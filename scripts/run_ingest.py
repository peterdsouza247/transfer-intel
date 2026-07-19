#!/usr/bin/env python3
"""Run phases 1 and 2: news in, resolved evidence out.

    python scripts/run_ingest.py --data data.json --out build

    # offline, against a saved article set, for evals and debugging
    python scripts/run_ingest.py --data data.json --out build \\
        --articles fixtures/articles.json

Outputs, all in --out:
    evidence.json      what run_editorial.py consumes, keyed by deal id
    candidates.json    transfers we do not track yet, for a human to accept
    needs_review.json  claims that could not be resolved, and why
    articles.json      the fetched article set, replayable
    ingest_stats.json  counts for the run log
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from transferintel.extract import (  # noqa: E402
    ClaimExtractor, ExtractionStats, rank_candidates, resolve,
)
from transferintel.ingest import collect  # noqa: E402
from transferintel.models import Article, Deal  # noqa: E402


def render_candidates_md(candidates, stats) -> str:
    lines = [
        "## Deals we are not tracking",
        "",
        f"{len(candidates)} candidates from {stats.articles} articles. "
        "Nothing here has touched data.json.",
        "",
    ]
    if not candidates:
        lines.append("Nothing worth adding today.")
        return "\n".join(lines)
    lines += [
        "| Player | Move | Stage | Fee | Mentions | Best source | Links |",
        "|---|---|---|---|---|---|---|",
    ]
    for c in candidates:
        fee = f"£{c.fee_gbp_m}m" if c.fee_gbp_m is not None else "n/a"
        links = " ".join(
            f"[{i + 1}]({e.url})" for i, e in enumerate(c.evidence[:3])
        )
        lines.append(
            f"| {c.player} | {c.from_club} to {c.to_club} | {c.stage.value} | "
            f"{fee} | {c.mentions} | tier {c.best_tier} | {links} |"
        )
    lines += ["", "To accept one, add a deal to data.json using the "
              "suggested id, then rerun the editorial pass."]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, default=Path("data.json"))
    ap.add_argument("--out", type=Path, default=Path("build"))
    ap.add_argument("--today", default=None)
    ap.add_argument("--window-hours", type=int, default=36)
    ap.add_argument("--articles", type=Path, default=None,
                    help="replay a saved article set instead of fetching")
    ap.add_argument("--max-batches", type=int, default=12,
                    help="cost ceiling, 20 articles per batch")
    ap.add_argument("--candidate-min-tier", type=int, default=2)
    ap.add_argument("--save-claims", action="store_true", default=True,
                    help="write the raw claim cassette for eval replay")
    args = ap.parse_args()

    today = date.fromisoformat(args.today) if args.today else date.today()
    args.out.mkdir(parents=True, exist_ok=True)

    raw = json.loads(args.data.read_text(encoding="utf-8"))
    deals = [Deal(**d) for d in raw["deals"]]
    known_clubs = sorted(raw.get("clubs", {}))

    # -- phase 1 ------------------------------------------------------------
    if args.articles and args.articles.exists():
        articles = [Article(**a) for a in
                    json.loads(args.articles.read_text(encoding="utf-8"))]
        ingest_stats = {"replayed": len(articles)}
    else:
        articles, ingest_stats = collect(
            today, window_hours=args.window_hours, clubs=known_clubs
        )
        if ingest_stats["feeds_failed"]:
            print(f"warning: {ingest_stats['feeds_failed']} of "
                  f"{ingest_stats['feeds']} feeds did not respond",
                  file=sys.stderr)

    (args.out / "articles.json").write_text(
        json.dumps([a.model_dump(mode="json") for a in articles], indent=2),
        encoding="utf-8",
    )

    # -- phase 2 ------------------------------------------------------------
    stats = ExtractionStats()
    extractor = ClaimExtractor(max_batches=args.max_batches)
    claims = extractor.run(articles, stats)

    if args.save_claims:
        # The cassette. Recording it is what makes phase 6 free to run.
        (args.out / "claims.json").write_text(
            json.dumps([c.model_dump(mode="json") for c in claims], indent=2),
            encoding="utf-8",
        )

    evidence, candidates, unresolved = resolve(
        claims, articles, deals, known_clubs, stats
    )
    ranked = rank_candidates(candidates, min_tier=args.candidate_min_tier)

    (args.out / "evidence.json").write_text(
        json.dumps(evidence, indent=2), encoding="utf-8"
    )
    (args.out / "candidates.json").write_text(
        json.dumps([c.model_dump(mode="json") for c in ranked], indent=2),
        encoding="utf-8",
    )
    (args.out / "candidates.md").write_text(
        render_candidates_md(ranked, stats), encoding="utf-8"
    )
    (args.out / "needs_review.json").write_text(
        json.dumps(unresolved, indent=2), encoding="utf-8"
    )
    (args.out / "ingest_stats.json").write_text(
        json.dumps({
            "date": today.isoformat(),
            "dry_run": extractor.dry,
            "ingest": ingest_stats,
            "extract": {
                "articles": stats.articles, "batches": stats.batches,
                "parse_failures": stats.parse_failures, "claims": stats.claims,
                "usable": stats.usable, "resolved": stats.resolved,
                "candidates": stats.candidates,
                "unresolved": len(unresolved), "dropped": stats.dropped,
            },
        }, indent=2),
        encoding="utf-8",
    )

    mode = " (dry run, no API key)" if extractor.dry else ""
    print(
        f"{len(articles)} articles, {stats.claims} claims, "
        f"{stats.resolved} attached to {len(evidence)} deals, "
        f"{len(ranked)} candidates, {len(unresolved)} unresolved{mode}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
