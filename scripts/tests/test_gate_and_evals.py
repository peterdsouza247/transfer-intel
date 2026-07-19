"""Phase 5 and 6. Run with: python -m pytest scripts/tests -q"""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from transferintel.evals import run_pipeline_suite  # noqa: E402
from transferintel.models import Deal, PatchOp, Status  # noqa: E402
from transferintel.scoring import DEFAULT_CONFIG  # noqa: E402
from transferintel.validate import GateConfig, check  # noqa: E402

FIX = ROOT.parent / "fixtures"
EVALS = ROOT.parent / "evals"
TODAY = date(2026, 7, 19)


def load():
    raw = json.loads((FIX / "data.json").read_text())
    return [Deal(**d) for d in raw["deals"]], sorted(raw["clubs"])


def op(did, field, frm, to, reason="because", evidence=None):
    return PatchOp(id=did, field=field, **{"from": frm}, to=to,
                   reason=reason, evidence=evidence or [])


def gate(ops, deals=None, clubs=None, **cfg):
    d, c = load()
    return check(deals or d, ops, clubs or c, TODAY, GateConfig(**cfg))


# ------------------------------------------------------------- phase 5


def test_a_clean_patch_passes():
    r = gate([op("okafor-ajax-everton", "status", "talks", "agreed",
                 evidence=["https://telegraph.co.uk/x"]),
              op("okafor-ajax-everton", "cred", 55, 70)])
    assert r.passed, r.hard


def test_completion_without_a_fetchable_source_is_a_hard_failure():
    r = gate([op("moreno-benfica-newcastle", "status", "medical", "done"),
              op("moreno-benfica-newcastle", "cred", 88, 100)])
    assert not r.passed
    assert any("no fetchable source" in m for m in r.hard)


def test_a_done_deal_can_never_be_moved():
    deals, clubs = load()
    deals[0].status = Status.done
    deals[0].cred = 100
    r = gate([op(deals[0].id, "status", "done", "collapsed",
                 evidence=["https://bbc.co.uk/x"]),
              op(deals[0].id, "cred", 100, 0)],
             deals=deals, clubs=clubs)
    assert not r.passed
    assert any("already done" in m for m in r.hard)


def test_skipping_a_rung_is_a_hard_failure():
    r = gate([op("vidal-sporting-cp-chelsea", "status", "rumor", "medical",
                 evidence=["https://skysports.com/x"])])
    assert any("skipped a rung" in m for m in r.hard)


def test_unexplained_changes_are_rejected():
    r = gate([op("okafor-ajax-everton", "cred", 55, 60, reason="")])
    assert any("unexplained" in m for m in r.hard)


def test_unfetchable_evidence_is_rejected():
    r = gate([op("okafor-ajax-everton", "cred", 55, 60,
                 evidence=["ftp://somewhere/x"])])
    assert any("not fetchable" in m for m in r.hard)


def test_a_currency_mixup_is_caught():
    r = gate([op("hartley-brighton-arsenal", "fee", 42.0, 5000.0,
                 evidence=["https://skysports.com/x"])])
    assert any("outside anything real" in m for m in r.hard)


def test_a_flood_of_updates_aborts_the_run():
    deals, clubs = load()
    ops = [op(d.id, "cred", d.cred, d.cred + 1) for d in deals] * 4
    assert not gate(ops, deals=deals, clubs=clubs).passed


def test_the_gate_reports_every_failure_in_one_pass():
    """An operator fixing one thing at a time and rerunning is how a five
    minute review becomes an hour."""
    r = gate([op("ghost", "cred", 1, 2),
              op("hartley-brighton-arsenal", "fee", 42.0, 9000.0),
              op("okafor-ajax-everton", "cred", 55, 99)])
    assert len(r.hard) >= 3
    assert any("do not exist" in m for m in r.hard)
    assert any("outside anything real" in m for m in r.hard)
    assert any("credibility jumped" in m for m in r.hard)


def test_a_banned_dash_in_a_note_never_reaches_the_site():
    r = gate([op("okafor-ajax-everton", "note", "", "Everton move fast \u2014 as ever.")])
    assert any("banned dash" in m for m in r.hard)


def test_the_patch_may_not_change_how_many_deals_exist():
    deals, clubs = load()
    r = check(deals, [], clubs, TODAY)
    assert r.passed
    assert len(deals) == 5


def test_warnings_never_block():
    deals, clubs = load()
    r = check(deals, [op("okafor-ajax-everton", "cred", 55, 60)],
              ["Arsenal"], TODAY)   # most buying clubs missing from config
    assert r.passed
    assert r.soft


# ------------------------------------------------------------- phase 6


def test_the_golden_set_passes_as_committed():
    suite = run_pipeline_suite(EVALS / "cases")
    assert suite.cases, "no cases found"
    assert suite.passed, [f for c in suite.cases for f in c.failures]
    assert suite.total("false_completions") == 0


def test_the_golden_set_catches_a_broken_tier_gate():
    """The suite is worthless unless it fails when it should. Letting tier 3
    sources move the ladder must break the tabloid case."""
    broken = replace(DEFAULT_CONFIG, state_change_min_tier=3,
                     stage_bonus_min_tier=3)
    suite = run_pipeline_suite(EVALS / "cases", cfg=broken)
    assert not suite.passed
    failed = [c.name for c in suite.cases if not c.passed]
    assert any("tabloid" in n for n in failed)


def test_the_golden_set_catches_a_missing_whiplash_cap():
    broken = replace(DEFAULT_CONFIG, max_cred_delta_per_run=100)
    suite = run_pipeline_suite(EVALS / "cases", cfg=broken)
    assert not suite.passed


def test_every_case_has_the_files_the_harness_needs():
    for case in sorted(p for p in (EVALS / "cases").iterdir() if p.is_dir()):
        for name in ("case.json", "data.json", "articles.json", "expected.json"):
            assert (case / name).exists(), f"{case.name} is missing {name}"
        meta = json.loads((case / "case.json").read_text())
        assert meta.get("notes"), f"{case.name} does not say why it exists"
