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
    Status.confirmed: Stage.completed,
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

    #: TI-020. The whole decay curve, in one place, so it can be tuned against
    #: real outcomes by editing four numbers rather than hunting through the
    #: module. Read it as: nothing happens for the first `grace_days`, then
    #: the score falls in a straight line to `mid_multiplier` by
    #: `stale_days`, and drops to `stale_multiplier` the moment it crosses.
    #:
    #: The step down at `stale_days` is deliberate rather than a smoothing
    #: mistake. A deal nobody has mentioned in a fortnight is a different kind
    #: of object from one that went quiet for twelve days, and the cliff is
    #: what makes the Stale badge mean something.
    decay_curve: "DecayCurve" = field(default_factory=lambda: DecayCurve())

    #: A fee change smaller than this is noise, not news.
    fee_delta_threshold: float = 0.5

    #: TI-001. `confirmed` means a tier 1 source says it is finished but
    #: nobody has announced it. That is worth a lot and it is not worth
    #: everything: 100 is reserved for a deal carrying a completion marker.
    confirmed_cred_cap: int = 90

    #: TI-021. How far measured source accuracy may move a base score, and how
    #: many resolved claims a source needs before its rate is trusted at all.
    reliability_swing: int = 10
    reliability_min_claims: int = 10

    #: TI-023. A club that just had a deal collapse at a position moves fast
    #: to replace it, so the next link at that position is more credible than
    #: the raw evidence suggests. Small, because it is a pattern, not proof.
    pivot_bonus: int = 5
    pivot_window_days: int = 14

    #: Statuses that are pinned and never recomputed.
    terminal: tuple[Status, ...] = (Status.done, Status.collapsed)

    @property
    def stale_days(self) -> int:
        return self.decay_curve.stale_days


@dataclass(frozen=True)
class DecayCurve:
    """Silence decay. A rumour nobody repeats is a rumour that is dying."""

    grace_days: int = 3
    stale_days: int = 14
    mid_multiplier: float = 0.60
    stale_multiplier: float = 0.40

    def multiplier(self, days_quiet: int | None) -> float:
        """Fraction of the base score a deal keeps after `days_quiet` days."""
        if days_quiet is None:
            return self.stale_multiplier
        if days_quiet <= self.grace_days:
            return 1.0
        if days_quiet > self.stale_days:
            return self.stale_multiplier
        span = self.stale_days - self.grace_days
        travelled = (days_quiet - self.grace_days) / span
        return 1.0 - travelled * (1.0 - self.mid_multiplier)

    def is_stale(self, days_quiet: int | None) -> bool:
        """Past the linear ramp entirely. Day 15 in the default curve."""
        return days_quiet is None or days_quiet > self.stale_days

    def explain(self, days_quiet: int | None, base: int, current: int) -> str:
        if days_quiet is None:
            return f"base {base}, no dated evidence at all, shown as {current}"
        if days_quiet <= self.grace_days:
            return f"base {base}, reported within {days_quiet} days, no decay"
        return (
            f"base {base}, currently {current} after {days_quiet} days "
            "without movement"
        )


DEFAULT_CONFIG = ScoringConfig()
DEFAULT_DECAY = DecayCurve()


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
    """Days of silence.

    `last_verified_at` wins when it is set, because it moves only when a
    source reasserts the deal. Evidence dates are the fallback for records
    that predate the field.
    """
    last = deal.last_verified_at
    if last is None:
        last = max((e.date for e in deal.evidence), default=None)
    return None if last is None else max(0, (today - last).days)


def verification_date(deal: Deal, today: date, cfg: ScoringConfig) -> date | None:
    """The date a source last actually reasserted this deal, if one did.

    This is the other half of TI-001. `updated_at` moves whenever the pipeline
    touches a record, which is every single day; if that is the field decay
    and staleness read, then a deal that nobody has mentioned since June looks
    identical to one reported this morning.
    """
    window = recent(deal, today, cfg)
    dates = [e.date for e in window if e.claim is not Stage.none]
    return max(dates) if dates else None


def corroborating_outlets(evidence: list[Evidence]) -> int:
    """Distinct tier 1 and 2 outlets. Three tabloids are not corroboration."""
    return len({e.key for e in evidence if e.tier <= 2})


# ---------------------------------------------------------------- credibility


@dataclass
class CredBreakdown:
    """Every term that produced a score, kept apart so the site can show them.

    `base` is what the evidence is worth today. `total` is what the site
    displays, which is `base` after silence decay. Keeping both is the whole
    of TI-020: "base 45, currently 31 after 11 days without movement" is a
    sentence a reader can check, and "31" on its own is not.
    """

    base: int = 0
    corroboration: int = 0
    stage: int = 0
    reliability: int = 0
    pivot: int = 0
    base_total: int = 0
    multiplier: float = 1.0
    days_quiet: int | None = None
    stale: bool = False
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
        if self.reliability:
            parts.append(f"source record {self.reliability:+d}")
        if self.pivot:
            parts.append(f"pivot pressure {self.pivot:+d}")
        text = ", ".join(parts) + f" = {self.base_total}"
        if self.multiplier < 1.0:
            text += (
                f", decayed to {self.total} after {self.days_quiet} days "
                "without movement"
            )
        return text


def compute_cred(
    deal: Deal,
    today: date,
    cfg: ScoringConfig = DEFAULT_CONFIG,
    reliability: dict[str, float] | None = None,
    pivot_positions: set[tuple[str, str]] | None = None,
) -> CredBreakdown:
    """Credibility as an arithmetic consequence of the evidence, not an opinion.

    `reliability` maps a lowercased source name to a measured hit rate in the
    range 0 to 1 (TI-021). Sources with too few resolved claims are absent
    from the mapping and simply contribute nothing, which is the honest
    behaviour: an unmeasured source is not a bad source.

    `pivot_positions` holds (club, position) pairs where that club has had a
    deal collapse recently (TI-023).
    """
    if deal.status is Status.done:
        return CredBreakdown(total=100, base_total=100,
                             pinned="pinned to 100, deal is done and announced")
    if deal.status is Status.collapsed:
        return CredBreakdown(total=0, base_total=0,
                             pinned="pinned to 0, deal collapsed")

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
    b.reliability = reliability_term(window or deal.evidence, cfg, reliability)
    b.pivot = pivot_term(deal, cfg, pivot_positions)

    b.base_total = max(
        0, min(100, b.base + b.corroboration + b.stage + b.reliability + b.pivot)
    )

    # TI-020. Decay multiplies the base rather than subtracting from it, so a
    # weak rumour and a strong one lose proportionally and the ratio between
    # them survives a quiet fortnight.
    b.days_quiet = days_since_mention(deal, today)
    b.multiplier = cfg.decay_curve.multiplier(b.days_quiet)
    b.stale = cfg.decay_curve.is_stale(b.days_quiet)
    b.total = max(0, min(100, round(b.base_total * b.multiplier)))

    if deal.status is Status.confirmed:
        b.total = min(b.total, cfg.confirmed_cred_cap)
        b.base_total = min(b.base_total, cfg.confirmed_cred_cap)
    return b


def reliability_term(
    evidence: list[Evidence],
    cfg: ScoringConfig,
    reliability: dict[str, float] | None,
) -> int:
    """TI-021: measured accuracy of the outlets carrying this claim.

    Centred on 0.5, so a source that gets half its calls right is neutral and
    the term only moves a score when the record says something. Uses the best
    measured source rather than the average, on the reasoning that one outlet
    with a real hit rate is not made less reliable by three that also ran it.
    """
    if not reliability:
        return 0
    rates = [
        reliability[e.key] for e in evidence if e.key in reliability
    ]
    if not rates:
        return 0
    return round((max(rates) - 0.5) * 2 * cfg.reliability_swing)


def pivot_term(
    deal: Deal,
    cfg: ScoringConfig,
    pivot_positions: set[tuple[str, str]] | None,
) -> int:
    """TI-023: a club that just lost a target at this position moves fast."""
    if not pivot_positions or deal.is_terminal:
        return 0
    return cfg.pivot_bonus if (deal.to, deal.pos) in pivot_positions else 0


# ---------------------------------------------------------------- status


@dataclass
class StatusDecision:
    status: Status
    reason: str = ""
    evidence: list[str] = field(default_factory=list)
    flag: str | None = None
    #: The phrase and outlet that justified a promotion to `done`, written
    #: onto the record so the decision stays auditable (TI-001).
    marker: str | None = None
    marker_source: str | None = None

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
    5. A tier 1 source claiming the deal is finished reaches `confirmed`,
       meaning confirmed pending announcement, and stops there.
    6. Reaching `done` additionally requires a recorded completion marker on a
       tier 1 source: language that only applies after the fact, or the club's
       own domain. This is TI-001. Silence, repetition, and the mere absence
       of a collapse report are not evidence of anything, and every one of
       them used to be enough.
    7. Status never regresses.
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

    # TI-001. A recorded completion marker on a tier 1 source is the strongest
    # evidence this system can hold, so it is allowed to cross the last rung
    # in a single run. The one-rung rule exists to stop thin evidence carrying
    # a deal a long way; it should not make a club's own announcement wait a
    # day. Deals further back than `medical` still advance one rung and raise
    # a flag: something that was a rumour yesterday and "signed" today is
    # either a scoop or a parsing fault, and a human should decide which.
    proofs = [e for e in window if e.tier == 1 and e.confirms_completion]
    if proofs and deal.status in (Status.medical, Status.confirmed):
        proofs.sort(key=lambda e: (not e.official, -e.date.toordinal()))
        best = proofs[0]
        return StatusDecision(
            Status.done,
            reason=f"{best.source}: {best.marker}",
            evidence=[e.url for e in proofs],
            marker=best.marker,
            marker_source=best.source,
        )

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

    if LADDER[next_i] is Status.confirmed:
        tier1 = [e for e in window if e.tier == 1 and e.claim is Stage.completed]
        if not tier1:
            return StatusDecision(
                deal.status,
                flag="completion claimed without a tier 1 source, held at "
                     f"{deal.status.value}",
            )
        return StatusDecision(
            Status.confirmed,
            reason=f"{tier1[0].source} reports the deal is done, no "
                   "announcement yet",
            evidence=[e.url for e in tier1],
        )

    if LADDER[next_i] is Status.done:
        # TI-001. The one promotion on the ladder that a reader can check for
        # themselves, so it is the one that must never be inferred.
        proofs = [
            e for e in window
            if e.tier == 1 and e.confirms_completion
        ]
        proofs.sort(key=lambda e: (not e.official, -e.date.toordinal()))
        if not proofs:
            return StatusDecision(
                deal.status,
                flag="no completion marker on a tier 1 source, held at "
                     "confirmed pending announcement",
            )
        best = proofs[0]
        return StatusDecision(
            Status.done,
            reason=f"{best.source}: {best.marker}",
            evidence=[e.url for e in proofs],
            marker=best.marker,
            marker_source=best.source,
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
    deal: Deal,
    today: date,
    cfg: ScoringConfig = DEFAULT_CONFIG,
    reliability: dict[str, float] | None = None,
    pivot_positions: set[tuple[str, str]] | None = None,
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
        # TI-001. A `done` status without the phrase that produced it is a
        # claim with no receipt, so the receipt is written in the same patch.
        if decision.status is Status.done:
            ops.append(PatchOp(
                id=deal.id, field="completion_marker",
                from_value=deal.completion_marker, to=decision.marker,
                reason=decision.reason, evidence=decision.evidence[:1],
            ))
            ops.append(PatchOp(
                id=deal.id, field="completion_source",
                from_value=deal.completion_source, to=decision.marker_source,
                reason=decision.reason, evidence=decision.evidence[:1],
            ))
            ops.append(PatchOp(
                id=deal.id, field="completed_date",
                from_value=None, to=today.isoformat(),
                reason=decision.reason, evidence=decision.evidence[:1],
            ))
    if decision.flag:
        ops.append(PatchOp(
            op="flag", id=deal.id, field="status", reason=decision.flag
        ))

    # `last_verified_at` moves only when a source actually reasserted the
    # deal. This is the field that makes silence legible.
    verified = verification_date(deal, today, cfg)
    if verified is not None and verified != deal.last_verified_at:
        ops.append(PatchOp(
            id=deal.id, field="last_verified_at",
            from_value=deal.last_verified_at.isoformat()
            if deal.last_verified_at else None,
            to=verified.isoformat(),
            reason="a source reasserted the deal on this date",
            evidence=[e.url for e in recent(deal, today, cfg)][:2],
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
    breakdown = compute_cred(projected, today, cfg, reliability, pivot_positions)
    if breakdown.base_total != (deal.base_cred if deal.base_cred is not None else -1):
        ops.append(PatchOp(
            id=deal.id, field="base_cred",
            from_value=deal.base_cred, to=breakdown.base_total,
            reason="credibility before silence decay",
            evidence=[e.url for e in recent(deal, today, cfg)][:1],
        ))
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


def pivot_pressure(
    deals: list[Deal], today: date, cfg: ScoringConfig = DEFAULT_CONFIG
) -> set[tuple[str, str]]:
    """(club, position) pairs where a deal collapsed inside the pivot window.

    TI-023, step 5. United losing Éderson and signing Tielemans inside 72
    hours is the pattern; this is the set that lets the scorer see it coming
    rather than describe it afterwards.
    """
    out: set[tuple[str, str]] = set()
    for d in deals:
        if d.status is not Status.collapsed:
            continue
        when = d.last_verified_at or max(
            (e.date for e in d.evidence), default=None
        )
        if when is None or (today - when).days > cfg.pivot_window_days:
            continue
        out.add((d.to, d.pos))
    return out


def score_all(
    deals: list[Deal],
    today: date,
    cfg: ScoringConfig = DEFAULT_CONFIG,
    reliability: dict[str, float] | None = None,
) -> list[PatchOp]:
    pivots = pivot_pressure(deals, today, cfg)
    return [
        op for d in deals
        for op in score_deal(d, today, cfg, reliability, pivots)
    ]
