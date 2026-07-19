"""Phase 6: evals.

Without this the answer to "did that prompt edit help?" is a shrug, and every
constant in `ScoringConfig` is a superstition.

Two suites, because there are two different questions.

**pipeline** replays a saved day: the state as it was, the articles that were
published, and the claims the model produced, recorded once as a cassette. No
API key, no network, no cost, byte-identical every time. This is what tells
you whether a change to the scoring constants or the resolution rules broke
something, and it is the one you run on every commit.

**extraction** is the only suite that needs a model. It runs headlines whose
correct reading you already know and scores the extractor's answers. This is
what tells you whether an edit to the extraction prompt helped, and it costs
a few cents, so it runs when you touch the prompt.

Assertions are deliberately loose about things that should be free to move
and strict about things that must not. Expected files say "this deal must
reach done" and "this deal must not", not "the patch must be exactly these
fourteen operations". Pinning the exact operation list would mean every
tweak to a constant fails every case, and a suite that always fails is a
suite nobody runs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .extract import ClaimExtractor, ExtractionStats, rank_candidates, resolve
from .models import Article, Claim, Deal, PatchOp, Stage
from .scoring import DEFAULT_CONFIG, ScoringConfig, score_all
from .validate import DEFAULT_GATE, GateConfig, check


# ---------------------------------------------------------------- results


@dataclass
class CaseResult:
    name: str
    failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metrics: dict[str, float] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return not self.failures


@dataclass
class SuiteResult:
    cases: list[CaseResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.cases)

    def metric(self, key: str) -> float:
        vals = [c.metrics[key] for c in self.cases if key in c.metrics]
        return sum(vals) / len(vals) if vals else 0.0

    def total(self, key: str) -> float:
        return sum(c.metrics.get(key, 0) for c in self.cases)

    def render(self) -> str:
        ok = sum(1 for c in self.cases if c.passed)
        lines = [
            "# Eval report",
            "",
            f"**{ok} of {len(self.cases)} cases passed.**",
            "",
        ]

        false_completions = self.total("false_completions")
        lines += [
            "| Metric | Value | Why it matters |",
            "|---|---|---|",
            f"| False completions | {false_completions:.0f} | "
            "The only metric that must be zero. A transfer marked done that "
            "did not happen is the failure the whole design exists to prevent |",
            f"| Missed completions | {self.total('missed_completions'):.0f} | "
            "Late is survivable, wrong is not |",
            f"| Wrong status | {self.total('wrong_status'):.0f} | "
            "Any status that landed somewhere unexpected |",
            f"| Credibility error | {self.metric('cred_mae'):.1f} | "
            "Mean absolute error against the recorded judgment |",
            f"| Resolution misses | {self.total('resolution_misses'):.0f} | "
            "Claims that should have attached to a deal and did not |",
            f"| Gate aborts | {self.total('gate_aborted'):.0f} | "
            "Cases where the gate refused a patch it should have passed |",
            "",
        ]

        if false_completions:
            lines += ["> False completions are non-zero. Stop and fix this "
                      "before shipping anything else.", ""]

        lines += ["## Cases", ""]
        for c in self.cases:
            mark = "pass" if c.passed else "FAIL"
            lines.append(f"### {c.name}: {mark}")
            lines.append("")
            for f in c.failures:
                lines.append(f"- FAIL {f}")
            for w in c.warnings:
                lines.append(f"- warn {w}")
            if c.metrics:
                lines.append(
                    "- " + ", ".join(f"{k} {v:g}" for k, v in c.metrics.items())
                )
            lines.append("")
        return "\n".join(lines)


# ---------------------------------------------------------------- pipeline


def _op_matches(op: PatchOp, spec: dict) -> bool:
    if op.op != "update":
        return False
    if op.id != spec["id"] or op.field != spec["field"]:
        return False
    if "to" in spec and str(op.to) != str(spec["to"]):
        return False
    return True


def run_pipeline_case(
    case_dir: Path,
    cfg: ScoringConfig = DEFAULT_CONFIG,
    gate_cfg: GateConfig = DEFAULT_GATE,
) -> CaseResult:
    """Replay one recorded day through resolution, scoring and the gate."""
    meta = json.loads((case_dir / "case.json").read_text())
    result = CaseResult(name=meta.get("name", case_dir.name))
    today = date.fromisoformat(meta["today"])

    data = json.loads((case_dir / "data.json").read_text())
    deals = [Deal(**d) for d in data["deals"]]
    known_clubs = sorted(data.get("clubs", {}))
    articles = [Article(**a) for a in
                json.loads((case_dir / "articles.json").read_text())]

    cassette = case_dir / "claims.json"
    claims = [Claim(**c) for c in json.loads(cassette.read_text())] \
        if cassette.exists() else []

    expected = json.loads((case_dir / "expected.json").read_text())

    stats = ExtractionStats()
    evidence, candidates, unresolved = resolve(
        claims, articles, deals, known_clubs, stats
    )

    # Evidence from the replayed claims joins whatever the state already had.
    from .models import Evidence
    for deal in deals:
        seen = {e.url for e in deal.evidence}
        deal.evidence.extend(
            Evidence(**e) for e in evidence.get(deal.id, []) if e["url"] not in seen
        )

    ops = score_all(deals, today, cfg)
    gate = check(deals, ops, known_clubs, today, gate_cfg)

    # -- assertions ------------------------------------------------------

    for spec in expected.get("must", []):
        if not any(_op_matches(op, spec) for op in ops):
            result.failures.append(
                f"expected {spec['id']} {spec['field']} to become "
                f"{spec.get('to')}, it did not"
            )

    for spec in expected.get("must_not", []):
        if any(_op_matches(op, spec) for op in ops):
            result.failures.append(
                f"{spec['id']} {spec['field']} became {spec.get('to')} and "
                "must not have"
            )

    tolerance = expected.get("cred_tolerance", 5)
    errors: list[float] = []
    cred_ops = {op.id: op.to for op in ops if op.field == "cred"}
    current = {d.id: d.cred for d in deals}
    for did, want in expected.get("cred", {}).items():
        got = cred_ops.get(did, current.get(did))
        if got is None:
            result.failures.append(f"{did} is not in the case data")
            continue
        errors.append(abs(got - want))
        if abs(got - want) > tolerance:
            result.failures.append(
                f"{did} credibility {got}, expected {want} within {tolerance}"
            )

    for did, want in expected.get("resolved_evidence", {}).items():
        got = len(evidence.get(did, []))
        if got < want:
            result.failures.append(
                f"{did} attached {got} pieces of evidence, expected {want}"
            )
            result.metrics["resolution_misses"] = \
                result.metrics.get("resolution_misses", 0) + (want - got)

    want_candidates = set(expected.get("candidates", []))
    got_candidates = {c.suggested_id for c in rank_candidates(candidates)}
    for missing in want_candidates - got_candidates:
        result.failures.append(f"expected a candidate for {missing}, got none")
    for extra in got_candidates - want_candidates:
        result.warnings.append(f"unexpected candidate {extra}")

    if expected.get("gate_must_pass", True) and not gate.passed:
        result.failures.append("the gate rejected a patch it should have passed")
        result.failures += [f"  gate: {m}" for m in gate.hard]
        result.metrics["gate_aborted"] = 1
    if not expected.get("gate_must_pass", True) and gate.passed:
        result.failures.append("the gate passed a patch it should have rejected")

    # -- metrics ---------------------------------------------------------

    want_done = {
        s["id"] for s in expected.get("must", [])
        if s["field"] == "status" and s.get("to") == "done"
    }
    got_done = {
        op.id for op in ops if op.field == "status" and op.to == "done"
    }
    result.metrics["false_completions"] = len(got_done - want_done)
    result.metrics["missed_completions"] = len(want_done - got_done)

    want_status = {
        s["id"]: s.get("to") for s in expected.get("must", [])
        if s["field"] == "status"
    }
    got_status = {
        op.id: op.to for op in ops if op.field == "status"
    }
    result.metrics["wrong_status"] = sum(
        1 for k, v in got_status.items() if want_status.get(k, v) != v
    )
    if errors:
        result.metrics["cred_mae"] = sum(errors) / len(errors)
    result.metrics.setdefault("resolution_misses", 0)
    result.metrics.setdefault("gate_aborted", 0)
    result.metrics["unresolved"] = len(unresolved)

    return result


def run_pipeline_suite(cases_dir: Path, **kwargs) -> SuiteResult:
    suite = SuiteResult()
    for case_dir in sorted(p for p in cases_dir.iterdir() if p.is_dir()):
        if not (case_dir / "case.json").exists():
            continue
        suite.cases.append(run_pipeline_case(case_dir, **kwargs))
    return suite


# ---------------------------------------------------------------- extraction


def run_extraction_suite(
    headlines_file: Path, extractor: ClaimExtractor
) -> SuiteResult:
    """Score the extractor against headlines whose correct reading is known."""
    suite = SuiteResult()
    rows = [
        json.loads(line) for line in
        headlines_file.read_text().splitlines() if line.strip()
    ]

    articles = [
        Article(
            url=f"https://example.com/eval/{i}",
            title=row["text"], published=date(2026, 1, 1),
            outlet="Eval", tier=row.get("tier", 1),
        )
        for i, row in enumerate(rows)
    ]

    stats = ExtractionStats()
    claims = {c.article_url: c for c in extractor.run(articles, stats)}

    for article, row in zip(articles, rows):
        want = row["expected"]
        case = CaseResult(name=row["text"][:70])
        got = claims.get(article.url)

        if got is None:
            case.failures.append("no claim returned for this headline")
            suite.cases.append(case)
            continue

        if got.is_transfer_claim != want.get("is_transfer_claim", True):
            case.failures.append(
                f"is_transfer_claim {got.is_transfer_claim}, expected "
                f"{want.get('is_transfer_claim')}"
            )
        if not want.get("is_transfer_claim", True):
            suite.cases.append(case)
            continue

        if want.get("player") and got.player:
            from .entities import name_similarity
            if name_similarity(got.player, want["player"]) < 0.85:
                case.failures.append(
                    f"player {got.player!r}, expected {want['player']!r}"
                )
        want_stage = want.get("reported_stage")
        if want_stage and got.reported_stage is not Stage(want_stage):
            case.failures.append(
                f"stage {got.reported_stage.value}, expected {want_stage}"
            )
        for key in ("from_club", "to_club"):
            if key in want and (getattr(got, key) or None) != want[key]:
                case.failures.append(
                    f"{key} {getattr(got, key)!r}, expected {want[key]!r}"
                )
        if "fee_amount" in want and got.fee_amount != want["fee_amount"]:
            case.warnings.append(
                f"fee {got.fee_amount}, expected {want['fee_amount']}"
            )
        suite.cases.append(case)

    return suite
