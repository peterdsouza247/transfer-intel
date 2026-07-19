#!/usr/bin/env python3
"""Run phases 3 and 4 and emit a reviewable patch.

    python scripts/run_editorial.py \
        --data data.json \
        --evidence build/evidence.json \
        --out build \
        --apply

Inputs
    data.json      the site's source of truth, with `id` and `evidence` per deal
    evidence.json  phase 2 output: {"<deal id>": [ {url, source, tier, date,
                   claim, fee_gbp_m}, ... ]}

Outputs (in --out)
    patch.json     the machine-readable operation list
    patch.md       the pull request body
    data.json      only with --apply
    data.js        only with --apply, the window shim index.html loads
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from transferintel.models import Deal, Evidence, Patch, PatchOp  # noqa: E402
from transferintel.notes import NoteWriter, note_ops  # noqa: E402
from transferintel.scoring import DEFAULT_CONFIG, score_all  # noqa: E402
from transferintel import validate  # noqa: E402


def load_data(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def attach_evidence(deals: list[Deal], evidence: dict[str, list[dict]]) -> None:
    """Phase 2 hands over evidence keyed by deal id. Merge and dedupe on url."""
    for deal in deals:
        incoming = [Evidence(**e) for e in evidence.get(deal.id, [])]
        seen = {e.url for e in deal.evidence}
        deal.evidence.extend(e for e in incoming if e.url not in seen)


def apply_ops(deals: list[Deal], ops: list[PatchOp]) -> None:
    by_id = {d.id: d for d in deals}
    for op in ops:
        if op.op != "update":
            continue
        deal = by_id.get(op.id)
        if deal is None:
            continue
        setattr(deal, op.field, op.to)


def render_pr_body(
    patch: Patch, notes_written: int, dry: bool,
    gate: validate.GateResult | None = None,
) -> str:
    lines = [
        f"## Editorial refresh, {patch.generated_for.isoformat()}",
        "",
        f"{len(patch.updates)} updates, {len(patch.flags)} flags, "
        f"{notes_written} notes rewritten"
        + (" (dry run, no model calls)" if dry else ""),
        "",
    ]
    if gate is not None and gate.soft:
        lines += [gate.render(), ""]
    if patch.updates:
        lines += [
            "### Changes",
            "",
            "| Deal | Field | From | To | Why | Sources |",
            "|---|---|---|---|---|---|",
        ]
        for o in patch.updates:
            # Seeded evidence from the phase 0 migration is a urn, not a link.
            src = " ".join(
                f"[{i + 1}]({u})" if u.startswith("http") else "migrated"
                for i, u in enumerate(o.evidence)
            )
            frm = str(o.from_value)[:60]
            to = str(o.to)[:60]
            lines.append(
                f"| `{o.id}` | {o.field} | {frm} | {to} | {o.reason} | {src or 'n/a'} |"
            )
        lines.append("")
    if patch.flags:
        lines += ["### Needs a human", ""]
        lines += [f"- `{o.id}` {o.field}: {o.reason}" for o in patch.flags]
        lines.append("")
    lines += [
        "<details><summary>Raw patch</summary>",
        "",
        "```json",
        json.dumps([o.model_dump(by_alias=True) for o in patch.ops],
                   indent=2, default=str),
        "```",
        "",
        "</details>",
    ]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, default=Path("data.json"))
    ap.add_argument("--evidence", type=Path, default=None)
    ap.add_argument("--out", type=Path, default=Path("build"))
    ap.add_argument("--today", default=None, help="ISO date, defaults to now")
    ap.add_argument("--apply", action="store_true",
                    help="write the updated data.json and data.js")
    ap.add_argument("--no-notes", action="store_true", help="skip phase 4")
    ap.add_argument("--max-notes", type=int, default=40)	# default 12
    ap.add_argument("--max-changes", type=int, default=40,
                    help="abort if phase 3 wants more updates than this")	# default 15
    args = ap.parse_args()

    today = date.fromisoformat(args.today) if args.today else date.today()
    args.out.mkdir(parents=True, exist_ok=True)

    raw = load_data(args.data)
    deals = [Deal(**d) for d in raw["deals"]]
    known_clubs = sorted(raw.get("clubs", {}))
    if args.evidence and args.evidence.exists():
        attach_evidence(deals, json.loads(args.evidence.read_text()))

    ops = score_all(deals, today, DEFAULT_CONFIG)

    writer = NoteWriter(max_notes=args.max_notes)
    notes_written = 0
    if not args.no_notes:
        n_ops, results = note_ops(deals, ops, today, writer)
        ops.extend(n_ops)
        notes_written = sum(1 for r in results if r.text)

    patch = Patch(generated_for=today, ops=ops)

    # -- phase 5: the gate --------------------------------------------------
    gate_cfg = validate.GateConfig(max_updates=args.max_changes)
    gate = validate.check(deals, patch.ops, known_clubs, today, gate_cfg)
    (args.out / "gate.md").write_text(gate.render(), encoding="utf-8")

    if not gate.passed:
        (args.out / "aborted_patch.json").write_text(
            json.dumps([o.model_dump(by_alias=True) for o in patch.ops],
                       indent=2, default=str),
            encoding="utf-8",
        )
        print("GATE FAILED, nothing written:", file=sys.stderr)
        for message in gate.hard:
            print(f"  - {message}", file=sys.stderr)
        return 2

    (args.out / "patch.json").write_text(
        json.dumps([o.model_dump(by_alias=True) for o in patch.ops],
                   indent=2, default=str),
        encoding="utf-8",
    )
    (args.out / "patch.md").write_text(
        render_pr_body(patch, notes_written, writer.dry, gate), encoding="utf-8"
    )

    if args.apply:
        apply_ops(deals, patch.ops)
        raw["deals"] = [d.model_dump(by_alias=True, mode="json") for d in deals]
        raw.setdefault("config", {})["updated"] = today.strftime("%b %d, %Y")
        args.data.write_text(
            json.dumps(raw, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        js = args.data.with_suffix(".js")
        js.write_text(
            "window.TRANSFER_DATA = "
            + json.dumps(raw, ensure_ascii=False)
            + ";\n",
            encoding="utf-8",
        )

    print(f"{len(patch.updates)} updates, {len(patch.flags)} flags, "
          f"{notes_written} notes, {len(gate.soft)} gate warnings. "
          f"Patch in {args.out}/patch.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
