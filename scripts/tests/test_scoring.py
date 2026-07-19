"""Rules that must never quietly change. Run with: python -m pytest scripts/tests"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from transferintel.models import Deal, Evidence, Stage, Status  # noqa: E402
from transferintel.notes import needs_note, validate  # noqa: E402
from transferintel.scoring import (  # noqa: E402
    compute_cred, decide_fee, decide_status, score_deal,
)

TODAY = date(2026, 7, 19)


def ev(days_ago=0, tier=1, claim=Stage.talks, source="Sky Sports", fee=None):
    return Evidence(
        url=f"https://example.com/{source}/{days_ago}/{claim.value}",
        source=source, tier=tier, date=TODAY - timedelta(days=days_ago),
        claim=claim, fee_gbp_m=fee,
    )


def deal(**kw):
    base = dict(
        id="x-a-b", p="Player X", **{"from": "Club A"}, to="Club B",
        fee=40.0, age=24, pos="AM", status=Status.rumor,
        date="Jul 19", tier=1, src="Sky Sports", cred=50, note="",
        evidence=[],
    )
    base.update(kw)
    return Deal(**base)


# ------------------------------------------------------------------ cred


def test_cred_is_reproducible():
    d = deal(evidence=[ev(0, 1, Stage.agreed)])
    assert compute_cred(d, TODAY).total == compute_cred(d, TODAY).total


def test_tier_one_beats_tier_three():
    hi = deal(evidence=[ev(0, 1, Stage.talks)])
    lo = deal(evidence=[ev(0, 3, Stage.talks, source="Daily Rag")])
    assert compute_cred(hi, TODAY).total > compute_cred(lo, TODAY).total


def test_corroboration_needs_distinct_outlets():
    one = deal(evidence=[ev(0, 1, source="Sky Sports")])
    dupe = deal(evidence=[ev(0, 1, source="Sky Sports"),
                          ev(1, 1, source="sky sports")])
    two = deal(evidence=[ev(0, 1, source="Sky Sports"),
                         ev(0, 1, source="The Athletic")])
    assert compute_cred(one, TODAY).total == compute_cred(dupe, TODAY).total
    assert compute_cred(two, TODAY).total > compute_cred(one, TODAY).total


def test_silence_decays_the_score():
    fresh = deal(evidence=[ev(0, 1, Stage.talks)])
    stale = deal(evidence=[ev(10, 1, Stage.talks)])
    assert compute_cred(stale, TODAY).total < compute_cred(fresh, TODAY).total


def test_terminal_statuses_pin_the_score():
    assert compute_cred(deal(status=Status.done), TODAY).total == 100
    assert compute_cred(deal(status=Status.collapsed), TODAY).total == 0


def test_cred_stays_in_range():
    loud = deal(evidence=[ev(0, 1, Stage.completed, source=f"S{i}")
                          for i in range(8)])
    assert 0 <= compute_cred(loud, TODAY).total <= 100


# ---------------------------------------------------------------- status


def test_status_advances_one_rung_only():
    d = deal(status=Status.rumor, evidence=[ev(0, 1, Stage.completed)])
    decision = decide_status(d, TODAY)
    assert decision.status is Status.talks
    assert decision.flag is not None


def test_done_requires_tier_one():
    d = deal(status=Status.medical,
             evidence=[ev(0, 2, Stage.completed, source="Telegraph")])
    assert decide_status(d, TODAY).status is Status.medical
    d2 = deal(status=Status.medical, evidence=[ev(0, 1, Stage.completed)])
    assert decide_status(d2, TODAY).status is Status.done


def test_done_is_terminal():
    d = deal(status=Status.done, evidence=[ev(0, 1, Stage.collapsed)])
    assert decide_status(d, TODAY).status is Status.done


def test_collapse_must_be_explicit():
    silent = deal(status=Status.talks, evidence=[ev(30, 1, Stage.talks)])
    assert decide_status(silent, TODAY).status is Status.talks
    said = deal(status=Status.talks, evidence=[ev(0, 1, Stage.collapsed)])
    assert decide_status(said, TODAY).status is Status.collapsed


def test_status_never_regresses():
    d = deal(status=Status.agreed, evidence=[ev(0, 1, Stage.interest)])
    assert decide_status(d, TODAY).status is Status.agreed


def test_revived_collapse_is_flagged():
    d = deal(status=Status.collapsed, evidence=[ev(0, 1, Stage.agreed)])
    decision = decide_status(d, TODAY)
    assert decision.status is Status.talks
    assert decision.flag


# ------------------------------------------------------------------- fee


def test_fee_ignores_tier_three_and_noise():
    d = deal(fee=40.0, evidence=[ev(0, 3, fee=90.0, source="Daily Rag")])
    assert decide_fee(d, TODAY) is None
    d2 = deal(fee=40.0, evidence=[ev(0, 1, fee=40.2)])
    assert decide_fee(d2, TODAY) is None
    d3 = deal(fee=40.0, evidence=[ev(0, 1, fee=55.0)])
    assert decide_fee(d3, TODAY)[0] == 55.0


# --------------------------------------------------------------- pipeline


def test_score_deal_emits_reviewable_ops():
    d = deal(status=Status.agreed, cred=50,
             evidence=[ev(0, 1, Stage.medical), ev(0, 1, Stage.medical,
                                                   source="The Athletic")])
    ops = score_deal(d, TODAY)
    fields = {o.field for o in ops if o.op == "update"}
    assert "status" in fields and "cred" in fields
    assert all(o.reason for o in ops)


def test_stale_deals_are_flagged_not_deleted():
    d = deal(status=Status.rumor, evidence=[ev(20, 2, source="Telegraph")])
    ops = score_deal(d, TODAY)
    assert any(o.op == "flag" and o.field == "_stale" for o in ops)
    assert not any(o.op == "update" and o.field == "status" for o in ops)


# ------------------------------------------------------------------ notes


@pytest.mark.parametrize("text,ok", [
    ("Palace have no leverage left with a year to run, and Liverpool know it.", True),
    ("Wages killed it \u2014 as they always do.", False),
    ("Short.", False),
    ("Is this the signing that finally fixes the midfield problem for good?", False),
    ("Spurs finally paying market rate for a winger who beat them twice last "
     "season", False),
    (" ".join(["word"] * 40) + ".", False),
])
def test_style_validator(text, ok):
    assert validate(text)[0] is ok


def test_clean_strips_model_habits():
    from transferintel.notes import clean
    assert clean('Note: "Spurs paid over the odds here."') == (
        "Spurs paid over the odds here."
    )
    assert clean("  Context:  Palace  have   no leverage.  ") == (
        "Palace have no leverage."
    )


def test_notes_only_regenerate_on_real_movement():
    d = deal(note="Existing note.")
    from transferintel.models import PatchOp
    drift = [PatchOp(id=d.id, field="cred", **{"from": 50}, to=48)]
    moved = [PatchOp(id=d.id, field="status", **{"from": "rumor"}, to="talks")]
    assert needs_note(d, drift) is False
    assert needs_note(d, moved) is True
    assert needs_note(deal(note=""), []) is True


# ------------------------------------------------- tier gating and whiplash


def test_tabloids_cannot_move_the_ladder():
    d = deal(status=Status.rumor,
             evidence=[ev(0, 3, Stage.completed, source="Daily Rag")])
    assert decide_status(d, TODAY).status is Status.rumor


def test_tabloids_cannot_claim_a_stage_bonus():
    rag = deal(evidence=[ev(0, 3, Stage.completed, source="Daily Rag")])
    sky = deal(evidence=[ev(0, 3, Stage.interest, source="Daily Rag")])
    assert compute_cred(rag, TODAY).total == compute_cred(sky, TODAY).total


def test_quiet_deals_keep_the_stage_they_reached():
    """A deal at `agreed` that goes quiet for three days should sag, not crater."""
    d = deal(status=Status.agreed, cred=70,
             evidence=[ev(9, 1, Stage.agreed)])
    assert compute_cred(d, TODAY).total > 40


def test_credibility_moves_at_walking_pace():
    d = deal(status=Status.rumor, cred=90, evidence=[ev(40, 3)])
    ops = [o for o in score_deal(d, TODAY) if o.field == "cred"]
    assert ops and ops[0].to == 65
    assert "capped" in ops[0].reason
