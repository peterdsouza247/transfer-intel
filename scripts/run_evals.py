#!/usr/bin/env python3
"""Run the golden set.

    python scripts/run_evals.py                        # pipeline suite, free
    python scripts/run_evals.py --suite extraction     # needs an API key
    python scripts/run_evals.py --suite all

Record a new case from a day you have just reviewed:

    python scripts/run_evals.py --record evals/cases/2026-08-02-deadline \\
        --data data.json --build build

That copies the state, the articles and the claims into a case folder and
writes a skeleton `expected.json` from what actually happened. Edit the
skeleton down to the assertions you care about, delete the rest, and commit.

Exit codes: 0 all passed, 1 a case failed, 2 the suite could not run.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from transferintel.evals import (  # noqa: E402
    run_extraction_suite, run_pipeline_suite,
)
from transferintel.extract import ClaimExtractor  # noqa: E402


def record(case_dir: Path, data: Path, build: Path, today: date) -> int:
    """Freeze a reviewed day into a replayable case."""
    if not (build / "articles.json").exists():
        print(f"No articles.json in {build}. Run run_ingest.py first.",
              file=sys.stderr)
        return 2

    case_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(data, case_dir / "data.json")
    shutil.copy(build / "articles.json", case_dir / "articles.json")

    claims_src = build / "claims.json"
    if claims_src.exists():
        shutil.copy(claims_src, case_dir / "claims.json")
    else:
        (case_dir / "claims.json").write_text("[]")
        print("warning: no claims.json, recording an empty cassette. Rerun "
              "run_ingest.py with --save-claims to capture the model output.",
              file=sys.stderr)

    (case_dir / "case.json").write_text(json.dumps({
        "name": case_dir.name,
        "today": today.isoformat(),
        "notes": "Describe what makes this day worth keeping.",
    }, indent=2))

    # Skeleton assertions from what the patch actually did, for editing down.
    patch_file = build / "patch.json"
    must = []
    cred = {}
    if patch_file.exists():
        for op in json.loads(patch_file.read_text()):
            if op.get("op") != "update":
                continue
            if op["field"] == "status":
                must.append({"id": op["id"], "field": "status", "to": op["to"]})
            elif op["field"] == "cred":
                cred[op["id"]] = op["to"]

    (case_dir / "expected.json").write_text(json.dumps({
        "must": must,
        "must_not": [],
        "cred": cred,
        "cred_tolerance": 5,
        "resolved_evidence": {},
        "candidates": [],
        "gate_must_pass": True,
    }, indent=2))

    print(f"Recorded {case_dir}.")
    print("Now edit expected.json: keep the assertions that capture why this "
          "day matters, delete the rest. A case asserting everything is a "
          "case that fails on every harmless change.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--suite", choices=["pipeline", "extraction", "all"],
                    default="pipeline")
    ap.add_argument("--cases", type=Path, default=Path("evals/cases"))
    ap.add_argument("--headlines", type=Path,
                    default=Path("evals/extraction/headlines.jsonl"))
    ap.add_argument("--report", type=Path, default=Path("evals/report.md"))
    ap.add_argument("--record", type=Path, default=None)
    ap.add_argument("--data", type=Path, default=Path("data.json"))
    ap.add_argument("--build", type=Path, default=Path("build"))
    ap.add_argument("--today", default=None)
    args = ap.parse_args()

    today = date.fromisoformat(args.today) if args.today else date.today()

    if args.record:
        return record(args.record, args.data, args.build, today)

    reports: list[str] = []
    failed = False

    if args.suite in ("pipeline", "all"):
        if not args.cases.exists():
            print(f"No cases at {args.cases}.", file=sys.stderr)
            return 2
        suite = run_pipeline_suite(args.cases)
        reports.append("# Pipeline suite\n\n" + suite.render())
        failed = failed or not suite.passed
        print(f"pipeline: {sum(1 for c in suite.cases if c.passed)}/"
              f"{len(suite.cases)} passed, "
              f"{suite.total('false_completions'):.0f} false completions")

    if args.suite in ("extraction", "all"):
        extractor = ClaimExtractor()
        if extractor.dry:
            print("extraction: skipped, no ANTHROPIC_API_KEY")
        elif not args.headlines.exists():
            print(f"extraction: skipped, no {args.headlines}")
        else:
            suite = run_extraction_suite(args.headlines, extractor)
            reports.append("# Extraction suite\n\n" + suite.render())
            failed = failed or not suite.passed
            print(f"extraction: {sum(1 for c in suite.cases if c.passed)}/"
                  f"{len(suite.cases)} passed")

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text("\n\n---\n\n".join(reports), encoding="utf-8")
    print(f"Report written to {args.report}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
