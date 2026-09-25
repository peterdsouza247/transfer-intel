"""Post-window accountability for publications and named journalists.

A source gets one call per transfer, however many follow-up articles it ran.
Only positive links published before the deal resolved are calls. A completed
move is a hit, a collapsed move a miss, and an unfinished record is unresolved
and excluded from accuracy. Reporting that a deal collapsed is valuable news,
but it is not retrospectively treated as having tipped the move.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable


PRIOR_HITS = 2
PRIOR_MISSES = 2

WRITER_ALIASES = {
    "Romano": "Fabrizio Romano",
    "Ornstein": "David Ornstein",
}

PUBLICATION_ALIASES = {
    "dailymail.com": "Daily Mail",
    "mirror.co.uk": "The Mirror",
    "The Mirror": "The Mirror",
    "liverpoolecho.co.uk": "Liverpool Echo",
    "Evening Standard": "Evening Standard",
    "chroniclelive.co.uk": "Chronicle Live",
    "talksport.com": "talkSPORT",
    "metro.co.uk": "Metro",
    "birminghammail.co.uk": "Birmingham Mail",
    "manchestereveningnews.co.uk": "Manchester Evening News",
    "football.london": "football.london",
    "CaughtOffside": "CaughtOffside",
    "The Independent": "The Independent",
    "Football365": "Football365",
    "F365": "Football365",
    "Telegraph": "The Telegraph",
    "BBC Sport": "BBC Sport",
    "Reuters": "Reuters",
    "Sky Sports": "Sky Sports",
    "The Guardian": "The Guardian",
    "FootballTransfers": "FootballTransfers",
    "ESPN": "ESPN",
    "Transfermarkt": "Transfermarkt",
    "RTE": "RTÉ",
    "FOX Sports": "FOX Sports",
    "TEAMtalk": "TEAMtalk",
}

# These labels cannot be held accountable as one identifiable media source.
IGNORED_ATTRIBUTIONS = {
    "Spanish press", "French press", "Turkish press", "Paper talk",
    "Man Utd",  # official club confirmation, not a transfer-news source
}

# Completion-day coverage proves that a transfer happened; it does not prove
# that an outlet called it before the market knew. Credibility therefore uses
# only genuinely predictive stages and requires them to pre-date resolution.
POSITIVE_CLAIMS = {"interest", "talks", "agreed", "medical"}


def _value(item: object) -> str:
    return str(getattr(item, "value", item))


def split_attribution(label: str) -> list[str]:
    """Expand the small set of composite seed labels without splitting names."""
    parts = [label]
    for separator in (" + ", " / "):
        parts = [piece for part in parts for piece in part.split(separator)]
    return [part.strip() for part in parts if part.strip()]


def canonical_attributions(label: str) -> list[tuple[str, str]]:
    """Return ``(kind, display name)`` pairs for one evidence label."""
    result: list[tuple[str, str]] = []
    for raw in split_attribution(label):
        if raw in WRITER_ALIASES:
            result.append(("journalist", WRITER_ALIASES[raw]))
        elif raw not in IGNORED_ATTRIBUTIONS:
            result.append(("publication", PUBLICATION_ALIASES.get(raw, raw)))
    return result


def resolution_date(deal: object) -> date | None:
    status = _value(getattr(deal, "status", ""))
    if status == "done":
        return getattr(deal, "completed_date", None) or getattr(
            deal, "last_verified_at", None
        )
    if status == "collapsed":
        collapse_dates = [
            evidence.date for evidence in getattr(deal, "evidence", [])
            if _value(getattr(evidence, "claim", "")) == "collapsed"
        ]
        return min(collapse_dates, default=None) or getattr(
            deal, "last_verified_at", None
        )
    return None


@dataclass(frozen=True)
class SourceRecord:
    name: str
    kind: str
    hits: int
    misses: int
    unresolved: int

    @property
    def resolved(self) -> int:
        return self.hits + self.misses

    @property
    def calls(self) -> int:
        return self.resolved + self.unresolved

    @property
    def hit_rate(self) -> int | None:
        if not self.resolved:
            return None
        return round(self.hits / self.resolved * 100)

    @property
    def credibility(self) -> int | None:
        """A smoothed hit rate that does not reward tiny samples.

        The neutral Beta(2, 2) prior is deliberately visible in the UI. It
        means a 1/1 source scores 60 rather than 100, while a substantial
        record quickly overwhelms the prior.
        """
        if not self.resolved:
            return None
        return round(
            (self.hits + PRIOR_HITS)
            / (self.resolved + PRIOR_HITS + PRIOR_MISSES)
            * 100
        )


def source_records(deals: Iterable[object]) -> dict[str, list[SourceRecord]]:
    """Measure unique pre-resolution calls for each identifiable source."""
    buckets: dict[tuple[str, str], dict[str, str]] = {}

    for deal in deals:
        cutoff = resolution_date(deal)
        credited: set[tuple[str, str]] = set()
        for evidence in getattr(deal, "evidence", []):
            if _value(getattr(evidence, "claim", "")) not in POSITIVE_CLAIMS:
                continue
            if cutoff is not None and evidence.date >= cutoff:
                continue
            credited.update(canonical_attributions(evidence.source))

        status = _value(getattr(deal, "status", ""))
        deal_id = str(getattr(deal, "id", ""))
        for key in credited:
            buckets.setdefault(key, {})[deal_id] = status

    output: dict[str, list[SourceRecord]] = {
        "publication": [], "journalist": [],
    }
    for (kind, name), calls in buckets.items():
        statuses = calls.values()
        output[kind].append(SourceRecord(
            name=name,
            kind=kind,
            hits=sum(status == "done" for status in statuses),
            misses=sum(status == "collapsed" for status in calls.values()),
            unresolved=sum(
                status not in {"done", "collapsed"}
                for status in calls.values()
            ),
        ))

    def order(record: SourceRecord) -> tuple[int, int, int, str]:
        return (
            -(record.credibility if record.credibility is not None else -1),
            -record.resolved,
            -record.calls,
            record.name.casefold(),
        )

    for records in output.values():
        records.sort(key=order)
    return output
