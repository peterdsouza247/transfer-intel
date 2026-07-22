#!/usr/bin/env python3
"""Turn a detected candidate into a tracked deal.

    python scripts/add_candidates.py --list
    python scripts/add_candidates.py --add rogers-aston-villa-chelsea \
        --age 24 --pos AM
    python scripts/add_candidates.py --add <id> --age 24 --pos AM --apply

Phase 2 finds transfers involving players nobody is tracking yet and writes
them to `build/candidates.json`. Until now nothing read that file. The only
way a new deal could enter `data.json` was for a human to hand-write a
record, which is precisely the unvalidated path the rest of this pipeline
exists to avoid, and in practice it meant new deals never entered at all: the
site kept scoring the same players while the window moved on around it.

This is the missing step. It is deliberately not automatic. A candidate is a
name the extractor did not recognise, which is exactly when a human should
look, and two fields cannot be inferred from a headline at all:

- **age**, which drives the whole value model
- **pos**, which drives the squad-gap reasoning and the club dashboards

So the tool refuses to invent them. Everything else, including status, tier,
credibility and the evidence trail, comes from the candidate's own reporting
and is scored by the same code that scores every other deal. A candidate
cannot enter as `done` without a tier 1 completion marker, because it goes
through `decide_status` like everything else.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from transferintel import validate  # noqa: E402
from transferintel.models import (  # noqa: E402
    Candidate, Deal, Stage, Status,
)
from transferintel.scoring import (  # noqa: E402
    DEFAULT_CONFIG, compute_cred, decide_status,
)

#: Positions the site understands. Listed in the error so nobody has to go
#: reading the model to find out what is allowed.
POSITIONS = ("GK", "CB", "LB", "RB", "CM", "AM", "LW", "RW", "ST")

STAGE_TO_STATUS = {
    Stage.none: Status.rumor,
    Stage.interest: Status.rumor,
    Stage.talks: Status.talks,
    Stage.agreed: Status.agreed,
    Stage.medical: Status.medical,
    Stage.completed: Status.confirmed,
    Stage.collapsed: Status.collapsed,
}


def load_candidates(path: Path) -> dict[str, Candidate]:
    if not path.exists():
        print(f"\n{path} does not exist. Run run_ingest.py first.",
              file=sys.stderr)
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    out = {}
    for item in raw:
        cand = Candidate(**item)
        out[cand.suggested_id] = cand
    return out


def show(candidates: dict[str, Candidate]) -> None:
    if not candidates:
        print("No candidates.")
        return
    print(f"{len(candidates)} candidate(s). Add one with --add <id>, and "
          "supply --age and --pos.\n")
    for cand in candidates.values():
        fee = f"£{cand.fee_gbp_m:g}m" if cand.fee_gbp_m else "no fee reported"
        print(f"  {cand.suggested_id}")
        print(f"    {cand.player}: {cand.from_club} to {cand.to_club}")
        print(f"    {cand.stage.value}, {fee}, tier {cand.best_tier}, "
              f"{cand.mentions} mention(s)")
        for ev in cand.evidence[:3]:
            print(f"      {ev.source} ({ev.date}): {ev.url[:78]}")
        print()


def build_deal(cand: Candidate, age: int, pos: str, today: date,
               note: str) -> Deal:
    """A deal record whose every scored field is derived, not asserted."""
    evidence = list(cand.evidence)
    best = min((e.tier for e in evidence), default=3)
    source = next((e.source for e in evidence if e.tier == best), "unknown")
    last = max((e.date for e in evidence), default=today)

    deal = Deal(**{
        "id": cand.suggested_id,
        "p": cand.player,
        "from": cand.from_club,
        "to": cand.to_club,
        "fee": cand.fee_gbp_m or 0.0,
        "age": age,
        "pos": pos,
        "status": Status.rumor,   # replaced below, once evidence is read
        "date": last.strftime("%b %-d") if sys.platform != "win32"
                else last.strftime("%b %d").replace(" 0", " "),
        "tier": best,
        "src": source,
        "cred": 0,
        "note": note,
        "evidence": evidence,
        "last_verified_at": last,
    })

    # Seed at the status the evidence supports, then let `decide_status`
    # apply the completion gate on top.
    #
    # Entering at `rumor` and climbing one rung was wrong, and the gate caught
    # it: a record whose sources all say "completed" carries the full stage
    # bonus, so it scored 100 while displaying "talks". The one-rung rule
    # exists to stop thin evidence carrying an *existing* deal a long way
    # between runs. A new record has no history to protect; its evidence is
    # its entire history, and it should enter where that evidence puts it.
    #
    # Note where the seeding stops. `Stage.completed` maps to `confirmed`,
    # never to `done`, so a candidate still cannot arrive as a completed
    # transfer unless `decide_status` finds a tier 1 completion marker. The
    # protection that matters is untouched.
    strongest = max(
        (e.claim for e in evidence),
        key=lambda s: list(Stage).index(s),
        default=Stage.none,
    )
    deal.status = STAGE_TO_STATUS.get(strongest, Status.rumor)

    decision = decide_status(deal, today, DEFAULT_CONFIG)
    deal.status = decision.status
    if decision.status is Status.done:
        deal.completion_marker = decision.marker
        deal.completion_source = decision.marker_source
        deal.completed_date = today
    breakdown = compute_cred(deal, today, DEFAULT_CONFIG)
    deal.cred = breakdown.total
    deal.base_cred = breakdown.base_total
    return deal


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, default=Path("data.json"))
    ap.add_argument("--candidates", type=Path,
                    default=Path("build/candidates.json"))
    ap.add_argument("--list", action="store_true", help="show and exit")
    ap.add_argument("--add", action="append", default=[],
                    help="candidate id to promote, repeatable")
    ap.add_argument("--age", type=int, action="append", default=[])
    ap.add_argument("--pos", action="append", default=[])
    ap.add_argument("--note", action="append", default=[])
    ap.add_argument("--today", default=None)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    today = date.fromisoformat(args.today) if args.today else date.today()
    candidates = load_candidates(args.candidates)

    if args.list or not args.add:
        show(candidates)
        return 0

    if len(args.age) != len(args.add) or len(args.pos) != len(args.add):
        print("\nEvery --add needs a matching --age and --pos. Neither can be "
              "read off a headline, and both change the deal's score: age "
              "drives the value model, position drives the squad reasoning.\n"
              f"Positions: {', '.join(POSITIONS)}", file=sys.stderr)
        return 2

    raw = json.loads(args.data.read_text(encoding="utf-8"))
    deals = [Deal(**d) for d in raw["deals"]]
    existing = {d.id for d in deals}
    notes = args.note + [""] * (len(args.add) - len(args.note))

    added = []
    for did, age, pos, note in zip(args.add, args.age, args.pos, notes):
        if did in existing:
            print(f"  {did} is already tracked, skipping.", file=sys.stderr)
            continue
        cand = candidates.get(did)
        if cand is None:
            print(f"  {did} is not in {args.candidates}, skipping.",
                  file=sys.stderr)
            continue
        if pos not in POSITIONS:
            print(f"  {pos!r} is not a position. One of: "
                  f"{', '.join(POSITIONS)}", file=sys.stderr)
            return 2
        deal = build_deal(cand, age, pos, today, note)
        added.append(deal)
        print(f"  {deal.p}: {deal.from_club} to {deal.to}, "
              f"{deal.status.value}, cred {deal.cred}, tier {deal.tier}")

    if not added:
        print("Nothing to add.")
        return 0

    combined = deals + added
    result = validate.check(combined, [], list(raw.get("clubs", {})), today)
    for line in result.hard:
        print(f"  GATE: {line}", file=sys.stderr)
    for line in result.soft[:6]:
        print(f"  note: {line}", file=sys.stderr)
    if not result.passed:
        print("\nGate failed, nothing written.", file=sys.stderr)
        return 1

    if not args.apply:
        print(f"\nDry run. {len(added)} deal(s) would be added. "
              "Re-run with --apply.")
        return 0

    raw["deals"] = [d.model_dump(by_alias=True, mode="json") for d in combined]
    args.data.write_text(
        json.dumps(raw, indent=2, ensure_ascii=False), encoding="utf-8")
    js = args.data.with_suffix(".js")
    js.write_text(
        "window.TRANSFER_DATA = " + json.dumps(raw, ensure_ascii=False) + ";\n",
        encoding="utf-8")
    print(f"\nAdded {len(added)} deal(s) to {args.data}. "
          "Rebuild with render_site.py.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
