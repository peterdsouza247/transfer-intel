"""Schema for TransferIntel deals, evidence and patch operations.

`Deal` mirrors the field names already used by index.html, so a Deal
round-trips to data.json without a translation layer. The Python-side
attribute for the JSON key "from" is `from_club`, because `from` is a
reserved word; always dump with `by_alias=True`.
"""

from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Status(str, Enum):
    """Deal lifecycle status as rendered by the site."""

    rumor = "rumor"
    talks = "talks"
    agreed = "agreed"
    medical = "medical"
    confirmed = "confirmed"
    done = "done"
    collapsed = "collapsed"


class CollapseReason(str, Enum):
    """Why a deal died. TI-023: causes are patterned, so they are an enum."""

    failed_medical = "failed_medical"
    fee_gap = "fee_gap"
    personal_terms = "personal_terms"
    agent_demands = "agent_demands"
    hijacked = "hijacked"
    player_refused = "player_refused"
    seller_withdrew = "seller_withdrew"
    financial_rules = "financial_rules"
    unknown = "unknown"


COLLAPSE_REASON_LABEL: dict[CollapseReason, str] = {
    CollapseReason.failed_medical: "Failed medical",
    CollapseReason.fee_gap: "Fee gap",
    CollapseReason.personal_terms: "Personal terms",
    CollapseReason.agent_demands: "Agent demands",
    CollapseReason.hijacked: "Hijacked by a rival",
    CollapseReason.player_refused: "Player refused",
    CollapseReason.seller_withdrew: "Selling club withdrew",
    CollapseReason.financial_rules: "Financial rules",
    CollapseReason.unknown: "Not established",
}


class Stage(str, Enum):
    """What a single source claimed. Extracted by phase 2, never inferred."""

    none = "none"
    interest = "interest"
    talks = "talks"
    agreed = "agreed"
    medical = "medical"
    completed = "completed"
    collapsed = "collapsed"


#: Ordered ladder. Index position drives every status decision in scoring.py.
LADDER: list[Status] = [
    Status.rumor,
    Status.talks,
    Status.agreed,
    Status.medical,
    Status.confirmed,
    Status.done,
]

#: Reported stage to site status. `interest` is only ever a rumor.
STAGE_TO_STATUS: dict[Stage, Status] = {
    Stage.interest: Status.rumor,
    Stage.talks: Status.talks,
    Stage.agreed: Status.agreed,
    Stage.medical: Status.medical,
    #: A source claiming completion gets a deal as far as "confirmed pending
    #: announcement" and no further. Only a recorded completion marker on a
    #: tier 1 source promotes to `done` (TI-001), which is the single defect
    #: this ladder exists to prevent.
    Stage.completed: Status.confirmed,
    Stage.collapsed: Status.collapsed,
}


class Evidence(BaseModel):
    """One resolved claim from one article. Output of phase 2."""

    url: str
    source: str
    tier: int = Field(ge=1, le=3)
    date: date
    claim: Stage = Stage.none
    fee_gbp_m: float | None = Field(default=None, ge=0)

    #: The exact phrase that made this a completion signal, e.g. "has signed".
    #: Absence is the default and the safe state: a deal cannot reach `done`
    #: without one of these recorded (TI-001).
    marker: str | None = None

    #: True when the URL is on a club's own domain, which is the strongest
    #: completion signal available and the only one nobody can walk back.
    official: bool = False

    @property
    def key(self) -> str:
        """Dedupe key for corroboration counting: one outlet counts once."""
        return self.source.strip().lower()

    @property
    def confirms_completion(self) -> bool:
        """A completion claim that also carries an explicit marker.

        The two halves are separate on purpose. Plenty of articles report that
        a deal is finished; far fewer say the words that mean it has actually
        happened. Only the second kind moves a record to `done`.
        """
        return self.claim is Stage.completed and bool(self.marker)


class Deal(BaseModel):
    model_config = ConfigDict(populate_by_name=True, validate_assignment=True)

    id: str
    p: str
    from_club: str = Field(alias="from")
    to: str
    fee: float = Field(ge=0)
    age: int = Field(ge=14, le=45)
    pos: str
    status: Status
    date: str
    tier: int = Field(ge=1, le=3)
    src: str
    cred: int = Field(ge=0, le=100)
    note: str = ""
    evidence: list[Evidence] = Field(default_factory=list)

    # -- TI-001: provenance for the completion decision --------------------
    #: Moves only when a source actually reasserts the deal. `date` and the
    #: pipeline's own touch time both move for reasons that are not evidence,
    #: which is exactly how absence of news became proof of a signing.
    last_verified_at: date | None = None

    #: The phrase, and the outlet that said it, behind a `done` status.
    completion_marker: str | None = None
    completion_source: str | None = None
    completed_date: date | None = None

    # -- TI-020: decay is shown, not hidden --------------------------------
    #: Credibility before silence decay is applied. `cred` is what the site
    #: displays; this is what it would be if the deal had moved today.
    base_cred: int | None = None

    # -- TI-023: collapse post-mortems -------------------------------------
    collapse_reason: CollapseReason | None = None
    collapse_narrative: str = ""
    #: Deal id this record pivoted from, and the one it pivoted to. Éderson
    #: collapsing and Tielemans arriving is one story, not two.
    pivot_from: str | None = None
    pivot_to: str | None = None

    @property
    def is_terminal(self) -> bool:
        return self.status in (Status.done, Status.collapsed)

    @property
    def days_quiet(self) -> int | None:
        """Days since a source last reasserted this deal."""
        anchor = self.last_verified_at or max(
            (ev.date for ev in self.evidence), default=None
        )
        if anchor is None:
            return None
        return max(0, (date.today() - anchor).days)

    @field_validator("pos")
    @classmethod
    def _known_position(cls, v: str) -> str:
        allowed = {"GK", "CB", "LB", "RB", "CM", "AM", "LW", "RW", "ST"}
        if v not in allowed:
            raise ValueError(f"unknown position {v!r}")
        return v

    @property
    def label(self) -> str:
        return f"{self.p} ({self.from_club} to {self.to})"


class PatchOp(BaseModel):
    """A single reviewable change. The pipeline's real output artifact."""

    model_config = ConfigDict(populate_by_name=True)

    op: Literal["update", "flag"] = "update"
    id: str
    field: str
    from_value: Any = Field(default=None, alias="from")
    to: Any = None
    reason: str = ""
    evidence: list[str] = Field(default_factory=list)

    @property
    def is_flag(self) -> bool:
        return self.op == "flag"


class Patch(BaseModel):
    generated_for: date
    ops: list[PatchOp] = Field(default_factory=list)

    @property
    def updates(self) -> list[PatchOp]:
        return [o for o in self.ops if o.op == "update"]

    @property
    def flags(self) -> list[PatchOp]:
        return [o for o in self.ops if o.op == "flag"]


class Article(BaseModel):
    """One item from one feed, after phase 1 normalisation."""

    url: str
    title: str
    summary: str = ""
    published: date
    outlet: str
    tier: int = Field(ge=1, le=3)

    @property
    def text(self) -> str:
        return f"{self.title}. {self.summary}".strip()


class Claim(BaseModel):
    """What one article asserted, as extracted by phase 2.

    Deliberately dumb. The model fills this in and does nothing else: no
    scoring, no canonical names, no deciding whether the deal already exists.
    """

    is_transfer_claim: bool = False
    player: str | None = None
    from_club: str | None = None
    to_club: str | None = None
    reported_stage: Stage = Stage.none
    fee_amount: float | None = Field(default=None, ge=0)
    fee_currency: str | None = None

    #: Set by the pipeline, not the model.
    article_url: str = ""

    @field_validator("player", "from_club", "to_club")
    @classmethod
    def _blank_to_none(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        return v or None

    @property
    def usable(self) -> bool:
        return bool(
            self.is_transfer_claim
            and self.player
            and self.reported_stage is not Stage.none
        )


class Candidate(BaseModel):
    """A transfer the pipeline saw but does not already track.

    Candidates are never written into data.json automatically. They go to
    needs_review.json for a human to accept, because creating a deal is the
    one action with no safe way to undo it once the site has been seen.
    """

    suggested_id: str
    player: str
    from_club: str | None = None
    to_club: str | None = None
    stage: Stage = Stage.none
    fee_gbp_m: float | None = None
    mentions: int = 1
    best_tier: int = 3
    evidence: list[Evidence] = Field(default_factory=list)
