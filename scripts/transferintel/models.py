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
    done = "done"
    collapsed = "collapsed"


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
    Status.done,
]

#: Reported stage to site status. `interest` is only ever a rumor.
STAGE_TO_STATUS: dict[Stage, Status] = {
    Stage.interest: Status.rumor,
    Stage.talks: Status.talks,
    Stage.agreed: Status.agreed,
    Stage.medical: Status.medical,
    Stage.completed: Status.done,
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

    @property
    def key(self) -> str:
        """Dedupe key for corroboration counting: one outlet counts once."""
        return self.source.strip().lower()


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
