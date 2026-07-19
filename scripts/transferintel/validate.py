"""Phase 5: the gate.

Everything upstream of this file is allowed to be optimistic. This is the
part that assumes the pipeline is wrong and looks for the specific ways that
would matter.

Two severities, and the distinction is the whole design:

**Hard** failures abort the run. Nothing is written, the patch is saved for
inspection, and the job exits non-zero. These are reserved for things that
would put a false statement on a live site or corrupt the data file. A day
with no update is always acceptable. A day with a fabricated completed
transfer is not.

**Soft** failures ride along in the pull request as things to look at. They
never block, because a gate that cries wolf is a gate you stop reading.

The gate deliberately re-derives its own view of the world rather than
trusting the pipeline's. It applies the patch to a copy, revalidates every
deal from scratch, and checks invariants against the result.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .models import LADDER, Deal, PatchOp, Status
from .notes import BANNED_CHARS, MAX_WORDS


@dataclass(frozen=True)
class GateConfig:
    #: Total field updates in one run. A normal day is 3 to 12.
    max_updates: int = 15

    #: Completed transfers in one run. Deadline day is the exception, and on
    #: deadline day you should be watching anyway.
    max_completions: int = 6

    #: Collapses in one run. A burst usually means an upstream parsing fault,
    #: not six deals dying at once.
    max_collapses: int = 4

    #: Credibility movement outside a pinned status.
    max_cred_delta: int = 25

    #: A fee this large is a currency conversion that went wrong.
    max_fee_gbp_m: float = 300.0

    #: Evidence URL schemes the gate will accept. `urn:` covers phase 0 seeds.
    allowed_url_prefixes: tuple[str, ...] = ("http://", "https://", "urn:")


DEFAULT_GATE = GateConfig()


@dataclass
class GateResult:
    hard: list[str] = field(default_factory=list)
    soft: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.hard

    def render(self) -> str:
        lines: list[str] = []
        if self.hard:
            lines += ["### Gate failed", ""]
            lines += [f"- {m}" for m in self.hard]
            lines += ["", "Nothing was written. The patch is in "
                      "`build/aborted_patch.json`.", ""]
        if self.soft:
            lines += ["### Gate warnings", ""]
            lines += [f"- {m}" for m in self.soft]
            lines.append("")
        if not lines:
            lines = ["Gate passed with no warnings.", ""]
        return "\n".join(lines)


# ---------------------------------------------------------------- helpers


def _apply_to_copy(deals: list[Deal], ops: list[PatchOp]) -> list[Deal] | str:
    """Apply the patch to a throwaway copy. Returns the copy, or an error
    string if the result does not survive schema validation."""
    by_id = {d.id: d.model_dump(by_alias=True, mode="json") for d in deals}
    for op in ops:
        if op.op != "update":
            continue
        raw = by_id.get(op.id)
        if raw is None:
            continue
        raw[op.field] = op.to
    out: list[Deal] = []
    for did, raw in by_id.items():
        try:
            out.append(Deal(**raw))
        except ValueError as exc:
            return f"{did} would not survive the patch: {exc}"
    return out


def _rung(status: Status) -> int:
    return LADDER.index(status) if status in LADDER else -1


# ---------------------------------------------------------------- the gate


def check(
    deals: list[Deal],
    ops: list[PatchOp],
    known_clubs: list[str],
    today: date,
    cfg: GateConfig = DEFAULT_GATE,
) -> GateResult:
    """Run every check against the pre-patch state and the post-patch result."""
    r = GateResult()
    before = {d.id: d for d in deals}
    updates = [o for o in ops if o.op == "update"]

    # -- structural ------------------------------------------------------

    if len(before) != len(deals):
        r.hard.append("duplicate deal ids in data.json")

    unknown = {o.id for o in ops} - set(before)
    if unknown:
        r.hard.append(
            f"patch references {len(unknown)} deals that do not exist: "
            + ", ".join(sorted(unknown)[:5])
        )
        # Drop them and carry on rather than returning. An operator fixing one
        # failure at a time, rerunning between each, is how a five minute job
        # becomes an hour.
        ops = [o for o in ops if o.id in before]
        updates = [o for o in updates if o.id in before]

    for op in updates:
        if not str(op.reason).strip():
            r.hard.append(
                f"{op.id}: an unexplained change to {op.field}, every "
                "operation must carry a reason"
            )
        for url in op.evidence:
            if not url.startswith(cfg.allowed_url_prefixes):
                r.hard.append(f"{op.id}: evidence url is not fetchable: {url}")

    # -- volume ----------------------------------------------------------

    if len(updates) > cfg.max_updates:
        r.hard.append(
            f"{len(updates)} updates, limit is {cfg.max_updates}. A day this "
            "busy is far more likely to be an upstream fault than real news"
        )

    completions = [o for o in updates
                   if o.field == "status" and o.to == Status.done.value]
    if len(completions) > cfg.max_completions:
        r.hard.append(
            f"{len(completions)} transfers completed in one run, limit is "
            f"{cfg.max_completions}"
        )

    collapses = [o for o in updates
                 if o.field == "status" and o.to == Status.collapsed.value]
    if len(collapses) > cfg.max_collapses:
        r.hard.append(
            f"{len(collapses)} deals collapsed in one run, limit is "
            f"{cfg.max_collapses}"
        )

    # -- per operation ---------------------------------------------------

    for op in updates:
        deal = before[op.id]

        if op.field == "status":
            old, new = deal.status, Status(op.to)

            if old is Status.done:
                r.hard.append(
                    f"{deal.label}: already done, nothing may move it to "
                    f"{new.value}"
                )

            if new is Status.done:
                http = [u for u in op.evidence if u.startswith("http")]
                if not http:
                    r.hard.append(
                        f"{deal.label}: marked complete with no fetchable "
                        "source. This is the failure that matters most"
                    )
                tier1 = any(
                    e.tier == 1 and e.url in op.evidence for e in deal.evidence
                )
                if not tier1 and http:
                    r.soft.append(
                        f"{deal.label}: completed on evidence the gate could "
                        "not confirm as tier 1, check the link"
                    )

            if _rung(new) >= 0 and _rung(old) >= 0:
                jump = _rung(new) - _rung(old)
                if jump < 0:
                    r.hard.append(
                        f"{deal.label}: status went backwards, "
                        f"{old.value} to {new.value}"
                    )
                elif jump > 1:
                    r.hard.append(
                        f"{deal.label}: status skipped a rung, "
                        f"{old.value} to {new.value}"
                    )

        elif op.field == "cred":
            new_cred = int(op.to)
            if not 0 <= new_cred <= 100:
                r.hard.append(f"{deal.label}: credibility {new_cred} out of range")
            pinned = deal.status in (Status.done, Status.collapsed) or any(
                o.field == "status" and o.id == op.id
                and o.to in (Status.done.value, Status.collapsed.value)
                for o in updates
            )
            if not pinned and abs(new_cred - deal.cred) > cfg.max_cred_delta:
                r.hard.append(
                    f"{deal.label}: credibility jumped "
                    f"{deal.cred} to {new_cred}, limit is {cfg.max_cred_delta}"
                )

        elif op.field == "fee":
            fee = float(op.to)
            if fee < 0 or fee > cfg.max_fee_gbp_m:
                r.hard.append(
                    f"{deal.label}: fee of {fee}m is outside anything real, "
                    "usually a currency conversion fault"
                )
            if deal.fee > 0 and fee > deal.fee * 2.5:
                r.soft.append(
                    f"{deal.label}: fee more than doubled, {deal.fee}m to {fee}m"
                )

        elif op.field == "note":
            note = str(op.to)
            for ch in BANNED_CHARS:
                if ch in note:
                    r.hard.append(
                        f"{deal.label}: note contains a banned dash character"
                    )
            if len(note.split()) > MAX_WORDS:
                r.hard.append(
                    f"{deal.label}: note is {len(note.split())} words, "
                    f"limit is {MAX_WORDS}"
                )

    # -- post-patch invariants -------------------------------------------

    after = _apply_to_copy(deals, ops)
    if isinstance(after, str):
        r.hard.append(after)
        return r

    if len(after) != len(deals):
        r.hard.append("the patch changed the number of deals, which it may "
                      "never do: creating and deleting deals is a human job")

    known = set(known_clubs)
    for d in after:
        # Only the buying club needs a dashboard entry. Selling clubs are
        # routinely foreign and out of scope, and warning about them every
        # single run is how a warning list becomes wallpaper.
        if d.to not in known:
            r.soft.append(
                f"{d.label}: buying club {d.to!r} has no entry in clubs, its "
                "dashboard will be empty"
            )
        if not d.evidence:
            r.soft.append(f"{d.label}: no evidence at all, consider deleting")
        if d.status is Status.done and d.cred != 100:
            r.hard.append(f"{d.label}: done but credibility is {d.cred}")
        if d.status is Status.collapsed and d.cred != 0:
            r.hard.append(f"{d.label}: collapsed but credibility is {d.cred}")
        future = [e for e in d.evidence if e.date > today]
        if future:
            r.soft.append(
                f"{d.label}: {len(future)} evidence items dated in the future"
            )

    return r
