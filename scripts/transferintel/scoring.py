"""Phase 3: scoring. Pure code, no model calls, fully deterministic.

The whole point of this module is that nothing in it is a judgment call made
at runtime. Every number comes out of `ScoringConfig` and every status move
comes out of an explicit state machine, so a `cred` of 78 can always be
explained by pointing at the breakdown, and rerunning yesterday's evidence
produces byte-identical output.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from .models import (
    LADDER, STAGE_TO_STATUS, Deal, Evidence, PatchOp, Stage, Status,
)

#: Inverse of STAGE_TO_STATUS, used to keep the stage bonus a deal has already
#: earned when it goes quiet for a few days.
STATUS_TO_STAGE: dict[Status, Stage] = {
    Status.rumor: Stage.interest,
    Status.talks: Stage.talks,
    Status.agreed: Stage.agreed,
    Status.medical: Stage.medical,
    Status.done: Stage.completed,
    Status.collapsed: Stage.collapsed,
}


@dataclass(frozen=True)
class ScoringConfig:
    """Every tunable constant. Change these, rerun the golden set, compare."""

    #: Evidence older than this does not count toward base or corroboration.
    recent_window_days: int = 3

    #: Starting points for the best source tier seen in the recent window.
    base_by_tier: dict[int, int] = field(
        default_factory=lambda: {1: 55, 2: 35, 3: 15}
    )
    base_no_recent: int = 20

    #: Points per additional distinct tier 1 or 2 outlet, and the cap.
    corroboration_step: int = 5
    corroboration_cap: int = 15

    #: Points for the strongest stage claimed in the recent window.
    stage_bonus: dict[Stage, int] = field(
        default_factory=lambda: {
            Stage.none: 0,
            Stage.interest: 0,
            Stage.talks: 10,
            Stage.agreed: 25,
            Stage.medical: 35,
            Stage.completed: 45,
            Stage.collapsed: 0,
        }
    )

    #: A tabloid can raise credibility a little. It can never move the deal
    #: along the ladder, and it cannot claim a stage on its own.
    state_change_min_tier: int = 2
    stage_bonus_min_tier: int = 2

    #: Penalty applied to the base when nothing was published in the window.
    no_recent_tier_penalty: int = 10

    #: Whiplash guard. Credibility is a running judgment, not a coin flip, so
    #: it moves at walking pace unless a terminal status pins it.
    max_cred_delta_per_run: int = 25

    #: Silence decay. A rumor nobody repeats is a rumor that is dying.
    decay_per_day: int = 2
    decay_cap: int = 30

    #: No mention for this long and the deal gets flagged for deletion.
    stale_days: int = 14

    #: A fee change smaller than this is noise, not news.
    fee_delta_threshold: float = 0.5

    #: Statuses that are pinned and never recomputed.
    terminal: tuple[Status, ...] = (Status.done, Status.collapsed)


DEFAULT_CONFIG = ScoringConfig()


# ---------------------------------------------------------------- helpers


def recent(deal: Deal, today: date, cfg: ScoringConfig) -> list[Evidence]:
    cutoff = today - timedelta(days=cfg.recent_window_days)
    return [e for e in deal.evidence if e.date >= cutoff and e.date <= today]


def best_tier(evidence: list[Evidence]) -> int | None:
    """Lowest tier number is the best source. None when there is no evidence."""
    return min((e.tier for e in evidence), default=None)


STAGE_ORDER: dict[Stage, int] = {
    s: i for i, s in enumerate(
        [Stage.none, Stage.interest, Stage.talks, Stage.agreed,
         Stage.medical, Stage.completed]
    )
}


def strongest_stage(evidence: list[Evidence], max_tier: int = 3) -> Stage:
    """The furthest-along claim in the set, ignoring collapse claims.

    `max_tier` exists so a tabloid cannot single-handedly declare a medical.
    """
    stages = [
        e.claim for e in evidence
        if e.claim in STAGE_ORDER and e.tier <= max_tier
    ]
    return max(stages, key=lambda s: STAGE_ORDER[s], default=Stage.none)


def collapse_claims(evidence: list[Evidence]) -> list[Evidence]:
    """Collapse is only ever explicit. Silence is decay, not death."""
    return [e for e in evidence if e.claim is Stage.collapsed]


def days_since_mention(deal: Deal, today: date) -> int | None:
    last = max((e.date for e in deal.evidence), default=None)
    return None if last is None else max(0, (today - last).days)


def corroborating_outlets(evidence: list[Evidence]) -> int:
    """Distinct tier 1 and 2 outlets. Three tabloids are not corroboration."""
    return len({e.key for e in evidence if e.tier <= 2})


# ---------------------------------------------------------------- credibility


@dataclass
class CredBreakdown:
    base: int = 0
    corroboration: int = 0
    stage: int = 0
    decay: int = 0
    total: int = 0
    pinned: str | None = None

    def explain(self) -> str:
        if self.pinned:
            return self.pinned
        parts = [f"base {self.base:+d}"]
        if self.corroboration:
            parts.append(f"corroboration {self.corroboration:+d}")
        if self.stage:
            parts.append(f"stage {self.stage:+d}")
        if self.decay:
            parts.append(f"decay {-self.decay:+d}")
        return ", ".join(parts) + f" = {self.total}"


def compute_cred(
    deal: Deal, today: date, cfg: ScoringConfig = DEFAULT_CONFIG
) -> CredBreakdown:
    """Credibility as an arithmetic consequence of the evidence, not an opinion."""
    if deal.status is Status.done:
        return CredBreakdown(total=100, pinned="pinned to 100, deal is done")
    if deal.status is Status.collapsed:
        return CredBreakdown(total=0, pinned="pinned to 0, deal collapsed")

    window = recent(deal, today, cfg)
    tier = best_tier(window)

    b = CredBreakdown()
    if tier is not None:
        b.base = cfg.base_by_tier[tier]
        b.stage = cfg.stage_bonus[
            strongest_stage(window, cfg.stage_bonus_min_tier)
        ]
    else:
        # Nothing published in the window. The deal does not forget what it
        # already established: keep the stage it reached, lean on the best
        # source that ever carried it, and let decay do the work.
        historic = best_tier(deal.evidence)
        b.base = (
            cfg.base_by_tier[historic] - cfg.no_recent_tier_penalty
            if historic is not None else cfg.base_no_recent
        )
        b.stage = cfg.stage_bonus[STATUS_TO_STAGE[deal.status]]

    b.corroboration = min(
        cfg.corroboration_cap,
        cfg.corroboration_step * max(0, corroborating_outlets(window) - 1),
    )

    silence = days_since_mention(deal, today)
    if silence is None:
        b.decay = cfg.decay_cap
    else:
        b.decay = min(cfg.decay_cap, cfg.decay_per_day * silence)

    b.total = max(0, min(100, b.base + b.corroboration + b.stage - b.decay))
    return b


# ---------------------------------------------------------------- status


@dataclass
class StatusDecision:
    status: Status
    reason: str = ""
    evidence: list[str] = field(default_factory=list)
    flag: str | None = None

    @property
    def changed_from(self) -> Status | None:
        return None


def decide_status(
    deal: Deal, today: date, cfg: ScoringConfig = DEFAULT_CONFIG
) -> StatusDecision:
    """The state machine.

    Rules, in order of precedence:

    1. `done` is terminal. Nothing resurrects a completed transfer.
    2. `collapsed` requires an explicit collapse claim, and can be reached
       from anything except `done`.
    3. `collapsed` deals only revive on a tier 1 claim, and only to `talks`.
    4. Status advances at most one rung per run, no matter how far ahead the
       evidence is. A deal that goes rumor to completed in one morning still
       spends a day at `talks`, and the skipped rungs raise a flag.
    5. Reaching `done` additionally requires a tier 1 source explicitly
       claiming completion. Everything else stops at `medical`.
    6. Status never regresses.
    """
    window = recent(deal, today, cfg)

    if deal.status is Status.done:
        return StatusDecision(Status.done)

    collapses = collapse_claims(window)
    if collapses and deal.status is not Status.collapsed:
        best = min(collapses, key=lambda e: e.tier)
        return StatusDecision(
            Status.collapsed,
            reason=f"{best.source} reports the move is off",
            evidence=[e.url for e in collapses],
        )

    if deal.status is Status.collapsed:
        revivals = [e for e in window if e.tier == 1 and e.claim not in
                    (Stage.none, Stage.interest, Stage.collapsed)]
        if revivals:
            return StatusDecision(
                Status.talks,
                reason=f"{revivals[0].source} reports the deal is live again",
                evidence=[e.url for e in revivals],
                flag="collapsed deal revived, confirm before merging",
            )
        return StatusDecision(Status.collapsed)

    # Only sources good enough to be believed can move the ladder.
    movers = [e for e in window if e.tier <= cfg.state_change_min_tier]
    target = STAGE_TO_STATUS.get(strongest_stage(movers), Status.rumor)
    cur_i, tgt_i = LADDER.index(deal.status), LADDER.index(target)

    if tgt_i <= cur_i:
        return StatusDecision(deal.status)

    next_i = cur_i + 1
    flag = None
    if tgt_i > next_i:
        flag = (
            f"evidence claims {target.value} but status only advanced to "
            f"{LADDER[next_i].value}, one rung per run"
        )

    if LADDER[next_i] is Status.done:
        tier1 = [e for e in window if e.tier == 1 and e.claim is Stage.completed]
        if not tier1:
            return StatusDecision(
                deal.status,
                flag="completion claimed without a tier 1 source, held at "
                     f"{deal.status.value}",
            )
        return StatusDecision(
            Status.done,
            reason=f"{tier1[0].source} confirms the transfer is complete",
            evidence=[e.url for e in tier1],
        )

    supporting = [e for e in movers if STAGE_TO_STATUS.get(e.claim) == target]
    supporting.sort(key=lambda e: e.tier)
    src = supporting[0].source if supporting else "reports"
    return StatusDecision(
        LADDER[next_i],
        reason=f"{src} reports the deal has reached {LADDER[next_i].value}",
        evidence=[e.url for e in supporting[:3]],
        flag=flag,
    )


# ---------------------------------------------------------------- fee


def decide_fee(
    deal: Deal, today: date, cfg: ScoringConfig = DEFAULT_CONFIG
) -> tuple[float, str, list[str]] | None:
    """Fees only move on a tier 1 or 2 source, and only by a real amount."""
    quotes = [
        e for e in recent(deal, today, cfg)
        if e.fee_gbp_m is not None and e.tier <= 2
    ]
    if not quotes:
        return None
    best = min(quotes, key=lambda e: (e.tier, -e.date.toordinal()))
    if abs(best.fee_gbp_m - deal.fee) < cfg.fee_delta_threshold:
        return None
    return best.fee_gbp_m, f"{best.source} reports the fee", [best.url]


# ---------------------------------------------------------------- entry point


def score_deal(
    deal: Deal, today: date, cfg: ScoringConfig = DEFAULT_CONFIG
) -> list[PatchOp]:
    """All phase 3 operations for one deal. Order matters: status first,
    because credibility is pinned by the terminal statuses."""
    ops: list[PatchOp] = []

    decision = decide_status(deal, today, cfg)
    if decision.status is not deal.status:
        ops.append(PatchOp(
            id=deal.id, field="status",
            from_value=deal.status.value, to=decision.status.value,
            reason=decision.reason, evidence=decision.evidence,
        ))
    if decision.flag:
        ops.append(PatchOp(
            op="flag", id=deal.id, field="status", reason=decision.flag
        ))

    fee = decide_fee(deal, today, cfg)
    if fee is not None:
        new_fee, reason, urls = fee
        ops.append(PatchOp(
            id=deal.id, field="fee", from_value=deal.fee, to=new_fee,
            reason=reason, evidence=urls,
        ))

    # Score against the status we are about to land on, not the stale one.
    projected = deal.model_copy(update={"status": decision.status})
    breakdown = compute_cred(projected, today, cfg)
    if breakdown.pinned is None:
        delta = breakdown.total - deal.cred
        if abs(delta) > cfg.max_cred_delta_per_run:
            capped = deal.cred + cfg.max_cred_delta_per_run * (1 if delta > 0 else -1)
            breakdown.total = max(0, min(100, capped))
            breakdown.pinned = (
                f"{breakdown.explain()}, capped to a "
                f"{cfg.max_cred_delta_per_run} point move"
            )
    if breakdown.total != deal.cred:
        ops.append(PatchOp(
            id=deal.id, field="cred",
            from_value=deal.cred, to=breakdown.total,
            reason=breakdown.explain(),
            evidence=[e.url for e in recent(deal, today, cfg)][:3],
        ))

    silence = days_since_mention(deal, today)
    if (
        decision.status not in cfg.terminal
        and silence is not None
        and silence >= cfg.stale_days
    ):
        ops.append(PatchOp(
            op="flag", id=deal.id, field="_stale",
            reason=f"no mention in {silence} days, consider deleting",
        ))

    return ops


def score_all(
    deals: list[Deal], today: date, cfg: ScoringConfig = DEFAULT_CONFIG
) -> list[PatchOp]:
    return [op for d in deals for op in score_deal(d, today, cfg)]
