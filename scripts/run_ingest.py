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
from transferintel.models import Article, Claim, Deal  # noqa: E402
from transferintel.prefilter import (  # noqa: E402
    FilterStats, load_seen, prefilter, save_seen,
)


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


def _require(path, what: str, hint: str) -> bool:
    if path is None or path.exists():
        return True
    print(f"\n{path} does not exist.\n  {what}\n  {hint}", file=sys.stderr)
    return False


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
    ap.add_argument("--claims", type=Path, default=None,
                    help="load pre-extracted claims and skip phase 2 entirely, "
                         "for running without an API key. "
                         "See docs/MANUAL-INGEST.md")
    ap.add_argument("--seen-cache", type=Path,
                    default=Path("logs/seen-articles.json"),
                    help="URLs already extracted, so the overlap between the "
                         "36 hour window and the 24 hour schedule is not paid "
                         "for twice")
    ap.add_argument("--no-cache", action="store_true",
                    help="re-extract everything, ignoring the seen cache")
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
            names = ", ".join(ingest_stats.get("failed_names") or ["unknown"])
            print(f"warning: {ingest_stats['feeds_failed']} of "
                  f"{ingest_stats['feeds']} feeds did not respond: {names}",
                  file=sys.stderr)
        per_feed = ingest_stats.get("per_feed") or {}
        if per_feed:
            print("  " + ", ".join(f"{n}: {c}" for n, c in per_feed.items()))

    (args.out / "articles.json").write_text(
        json.dumps([a.model_dump(mode="json") for a in articles], indent=2),
        encoding="utf-8",
    )

    # -- gate before the meter starts ---------------------------------------
    # Everything above is free: fetching a feed costs nothing. Everything
    # below is billed per token. This is the only place in the pipeline where
    # dropping work saves real money, so it is where the filtering happens.
    seen_urls, seen_cache = (set(), {})
    if not args.no_cache:
        seen_urls, seen_cache = load_seen(args.seen_cache)
    fstats = FilterStats()
    articles = prefilter(articles, seen_urls, fstats)
    print(fstats.summary())
    if fstats.samples:
        print("  dropped, e.g.: " + "; ".join(fstats.samples[:3]))

    # -- phase 2 ------------------------------------------------------------
    stats = ExtractionStats()
    if args.claims and not args.claims.exists():
        # A raw FileNotFoundError traceback here is unhelpful: by this point
        # the run has already fetched and filtered, so it reads like the
        # pipeline broke rather than like a path being wrong.
        print(f"\n{args.claims} does not exist.\n"
              "  --claims expects a JSON array of pre-extracted claims.\n"
              "  See docs/MANUAL-INGEST.md, and manual/claims.json for a\n"
              "  worked example you can copy.", file=sys.stderr)
        return 2

    if args.claims:
        # Claims supplied from outside. Everything downstream is unchanged:
        # resolution, marker detection, deduping, scoring and the gate all run
        # exactly as they would on model output, so a curated claim cannot
        # promote a deal to `done` without the same completion marker and tier
        # 1 source the pipeline demands of anything else.
        supplied = json.loads(args.claims.read_text(encoding="utf-8"))
        drafts = [c for c in supplied if c.pop("_draft", False)]
        if drafts:
            # draft_claims.py guesses. Guesses are fine as a starting point
            # and must never enter the dataset unread, so the marker it
            # leaves behind is a hard stop rather than a warning.
            print(f"\n{len(drafts)} of {len(supplied)} claims in "
                  f"{args.claims} are still marked `_draft: true`.\n"
                  "  These were generated by pattern matching, not read by "
                  "anyone.\n"
                  "  Check each one, then delete its `_draft` and `_review` "
                  "keys.\n"
                  "  Delete the claims you do not want entirely.",
                  file=sys.stderr)
            return 2
        for c in supplied:
            c.pop("_review", None)
            c.pop("_title", None)
        claims = [Claim(**c) for c in supplied]
        stats.articles = len(articles)
        stats.claims = len(claims)
        extractor = None
        print(f"Loaded {len(claims)} claims from {args.claims}, "
              "phase 2 skipped.")
    else:
        extractor = ClaimExtractor(max_batches=args.max_batches)
        claims = extractor.run(articles, stats)

    if args.save_claims and not args.claims:
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
            "dry_run": bool(extractor) and extractor.dry,
            "claims_supplied": bool(args.claims),
            "filter": {
                "seen": fstats.seen, "kept": fstats.kept,
                "cut_rate": round(fstats.cut_rate, 3),
                "no_signal": fstats.dropped_no_signal,
                "dead_path": fstats.dropped_dead_path,
                "seen_before": fstats.dropped_seen_before,
            },
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

    if not args.no_cache and extractor and not extractor.dry:
        # Only after a real extraction. Marking articles as seen during a dry
        # run would make them invisible to the first run that has a key.
        save_seen(args.seen_cache, seen_cache,
                  [a.url for a in articles], today)

    if unresolved:
        # Loud, because the commonest cause is a claims file referencing an
        # article the run does not have, and the symptom otherwise is a
        # correct-looking run that changes nothing and explains nothing.
        print(f"\n{len(unresolved)} claim(s) could not be resolved:",
              file=sys.stderr)
        for item in unresolved[:8]:
            print(f"  {item.get('player') or '(no player)'}: {item['reason']}",
                  file=sys.stderr)
            if item.get("url"):
                print(f"    {str(item['url'])[:96]}", file=sys.stderr)
        print(f"  Full list in {args.out / 'needs_review.json'}\n",
              file=sys.stderr)

    mode = (" (claims supplied)" if args.claims
            else " (dry run, no API key)" if extractor and extractor.dry else "")
    print(
        f"{len(articles)} articles, {stats.claims} claims, "
        f"{stats.resolved} attached to {len(evidence)} deals, "
        f"{len(ranked)} candidates, {len(unresolved)} unresolved{mode}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
