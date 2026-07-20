#!/usr/bin/env python3
"""TI-001, step 7: audit every record currently marked `done`.

    python scripts/backfill_completions.py --data data.json          # report
    python scripts/backfill_completions.py --data data.json --apply  # write

The scoring fix stops *new* false completions. It does nothing about the ones
already on the site, and those are the ones a visitor can see. Thirty-one
records claim `done` with credibility 100; several of them say, in their own
summary text, that the deal has not been announced yet.

This script decides nothing subjective. For each completed record it asks two
questions with checkable answers:

1. Does any piece of evidence carry a completion marker on a tier 1 source?
2. Does the record's own note contradict its status?

A record that fails the first test cannot prove it happened. A record that
fails the second argues with itself. Either is enough to demote.

Demotion target is `confirmed`, meaning confirmed pending announcement, with
credibility capped at 90. That is deliberately not `collapsed` and not
`medical`: the pipeline does not know these deals fell through, only that it
cannot show they completed. Overstating a retraction is the same class of
error as overstating the original claim.

Records seeded by the phase 0 migration carry `urn:` evidence rather than a
link. Those cannot be verified by definition, so they are judged on their note
alone: a seeded record with a clean note keeps its status and is listed under
"unverifiable" for the operator to spot-check, because demoting Anderson to
Man City, a deal that plainly happened, would be its own kind of false
statement.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from transferintel.markers import contradicts_completion  # noqa: E402
from transferintel.models import Deal, Status  # noqa: E402


class Finding:
    """One record's audit result, in a form that prints as a table row."""

    def __init__(self, deal: Deal, verdict: str, why: str) -> None:
        self.deal = deal
        self.verdict = verdict          # keep | demote | unverifiable
        self.why = why

    @property
    def row(self) -> str:
        return f"| {self.deal.label} | {self.verdict} | {self.why} |"


def has_tier1_proof(deal: Deal) -> bool:
    return any(e.tier == 1 and e.confirms_completion for e in deal.evidence)


def is_seeded(deal: Deal) -> bool:
    """Every piece of evidence came from the migration, not from a fetch."""
    return bool(deal.evidence) and all(
        str(e.url).startswith("urn:") for e in deal.evidence
    )


def audit(deal: Deal) -> Finding:
    contradiction = contradicts_completion(deal.note)

    if contradiction:
        return Finding(
            deal, "demote",
            f"note says {contradiction!r}, which is not a completed transfer",
        )
    # The consistency gate requires tier 1 for a completed transfer, so the
    # audit has to apply the same rule. Touré is the case that matters here:
    # a genuine-looking record sourced entirely to a tier 2 outlet, which the
    # old pipeline was happy to call done and the new gate is not.
    if deal.tier != 1:
        return Finding(
            deal, "demote",
            f"completed on a tier {deal.tier} source, completion needs tier 1",
        )
    if has_tier1_proof(deal):
        return Finding(deal, "keep", "tier 1 evidence carries a completion marker")
    if is_seeded(deal):
        return Finding(
            deal, "unverifiable",
            "migrated record, no fetchable source, note does not contradict",
        )
    return Finding(deal, "demote", "no tier 1 completion marker on any evidence")


def demote(deal: Deal, cap: int = 90) -> None:
    """Move a record back to confirmed pending announcement.

    The note is left alone. It is the operator's sentence, it is accurate
    about the state of the deal, and it is the reason the demotion happened.
    """
    deal.status = Status.confirmed
    deal.cred = min(deal.cred, cap)
    deal.completion_marker = None
    deal.completion_source = None
    deal.completed_date = None


#: Written onto migrated records that survive the audit. The consistency gate
#: requires every `done` record to name the thing that proves it, and these
#: cannot name a URL. Saying so in the field itself is better than either
#: exempting them from the gate, which would leave a hole exactly where the
#: original defect lived, or inventing a source they do not have.
MIGRATED_MARKER = "hand-verified at migration, no fetchable source"


def seal_migrated(deal: Deal, fallback: date) -> None:
    """Give an unverifiable completion honest provenance rather than none."""
    deal.completion_marker = MIGRATED_MARKER
    deal.completion_source = deal.src or "migration"
    if deal.completed_date is None:
        deal.completed_date = _display_date(deal.date, fallback)


def _display_date(label: str, fallback: date) -> date:
    """Parse the site's display date, e.g. "Jul 8", into a real date.

    The window runs June to September of one year, so the year is not
    ambiguous in practice. Anything unparseable falls back to today rather
    than raising: a slightly wrong completion date on a migrated record is
    worth less than a build that will not run.
    """
    import re

    months = {m: i for i, m in enumerate(
        ["jan", "feb", "mar", "apr", "may", "jun",
         "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}
    found = re.match(r"\s*([A-Za-z]{3})\w*\s+(\d{1,2})", label or "")
    if not found:
        return fallback
    month = months.get(found.group(1).lower())
    if month is None:
        return fallback
    try:
        return date(fallback.year, month, int(found.group(2)))
    except ValueError:
        return fallback


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, default=Path("data.json"))
    ap.add_argument("--apply", action="store_true",
                    help="write the demotions to data.json and data.js")
    ap.add_argument("--demote-unverifiable", action="store_true",
                    help="also demote migrated records with no fetchable source")
    ap.add_argument("--out", type=Path, default=Path("build/backfill.md"))
    args = ap.parse_args()

    raw = json.loads(args.data.read_text(encoding="utf-8"))
    deals = [Deal(**d) for d in raw["deals"]]
    completed = [d for d in deals if d.status is Status.done]

    findings = [audit(d) for d in completed]
    demotions = [f for f in findings if f.verdict == "demote"]
    unverifiable = [f for f in findings if f.verdict == "unverifiable"]
    keeps = [f for f in findings if f.verdict == "keep"]

    if args.demote_unverifiable:
        demotions += unverifiable
        unverifiable = []

    lines = [
        "# TI-001 completion audit",
        "",
        f"Run {date.today().isoformat()} against {args.data}.",
        "",
        f"- {len(completed)} records marked completed",
        f"- {len(demotions)} demoted to confirmed pending announcement",
        f"- {len(unverifiable)} unverifiable, left alone, spot-check these",
        f"- {len(keeps)} verified by a tier 1 completion marker",
        "",
    ]
    for title, group in (
        ("Demoted", demotions),
        ("Unverifiable, kept", unverifiable),
        ("Verified", keeps),
    ):
        if not group:
            continue
        lines += [f"## {title}", "", "| Deal | Verdict | Why |",
                  "|---|---|---|"]
        lines += [f.row for f in group]
        lines.append("")

    report = "\n".join(lines)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(report, encoding="utf-8")
    print(report)

    if not args.apply:
        print("Dry run. Nothing written. Re-run with --apply to commit.",
              file=sys.stderr)
        return 0

    for finding in demotions:
        demote(finding.deal)
    for finding in unverifiable + keeps:
        if finding.deal.status is Status.done:
            seal_migrated(finding.deal, date.today())

    raw["deals"] = [d.model_dump(by_alias=True, mode="json") for d in deals]
    args.data.write_text(
        json.dumps(raw, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    js = args.data.with_suffix(".js")
    js.write_text(
        "window.TRANSFER_DATA = " + json.dumps(raw, ensure_ascii=False) + ";\n",
        encoding="utf-8",
    )
    print(f"\nApplied {len(demotions)} demotions to {args.data} and {js}.",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
