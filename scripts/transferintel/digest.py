"""TI-010 and TI-011: the daily digest, and who gets which edition.

The digest exists because transfer window traffic is spiky and almost
entirely non-returning. A visitor who reads one deal page and leaves is worth
a fraction of one who gets an email every morning, and every day the window is
open without a capture form is subscribers that cannot be recovered later.

Two rules shape everything here:

**A quiet day is a real edition.** The temptation is to skip sending when
nothing moved. Do not. A two-line email saying nothing moved is the clearest
possible demonstration that the site distinguishes signal from noise, and it
is the one competitors will not send because it looks like failure. It is not.
It is the product.

**Segments are generated, not looped.** With 20 clubs and 5 thresholds the
naive product is 100 editions. The generator instead produces one edition per
club at the lowest threshold in use, plus an unsegmented edition, and leaves
the final threshold cut to the provider's send-time filter. That keeps the job
inside a GitHub Actions run and inside a free tier's send limits.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from .models import Deal, Status

# --------------------------------------------------------------- thresholds

#: TI-011. The credibility floors a subscriber can choose. `confirmed` is not
#: a number, it is a status filter, and it is the option serious readers pick.
THRESHOLDS: tuple[str, ...] = ("0", "40", "60", "80", "confirmed")

#: TI-011, step 4. A deal crossing this, or a completion above this fee, is
#: worth interrupting someone's day for. Nothing else is.
ALERT_CRED = 80
ALERT_FEE_GBP_M = 40.0
MAX_ALERTS_PER_DAY = 2

#: A credibility move smaller than this is not news, it is decay arithmetic.
CRED_MOVE_THRESHOLD = 15


@dataclass
class Change:
    """One thing that happened to one deal since the last edition."""

    deal: Deal
    kind: str          # tracked | status | cred | collapsed
    detail: str
    from_value: object = None
    to_value: object = None

    @property
    def headline(self) -> str:
        return f"{self.deal.p}, {self.deal.from_club} to {self.deal.to}"


@dataclass
class Edition:
    """One email, for one segment."""

    segment: str                     # "all" or a club name
    for_date: date
    tracked: list[Change] = field(default_factory=list)
    status_moves: list[Change] = field(default_factory=list)
    cred_moves: list[Change] = field(default_factory=list)
    collapses: list[Change] = field(default_factory=list)

    @property
    def is_quiet(self) -> bool:
        return not (self.tracked or self.status_moves
                    or self.cred_moves or self.collapses)

    @property
    def count(self) -> int:
        return (len(self.tracked) + len(self.status_moves)
                + len(self.cred_moves) + len(self.collapses))


# ------------------------------------------------------------- change detection


def changes_since(
    deals: list[Deal],
    previous: dict[str, dict],
    today: date,
    window_hours: int = 24,
) -> list[Change]:
    """What moved, by comparing today's records against yesterday's snapshot.

    `previous` is the snapshot the last run wrote: deal id to a small dict of
    the fields that matter. Comparing snapshots rather than reading the day's
    patch means the digest still works if a run was skipped, and that a manual
    edit shows up in the email like any other change.
    """
    cutoff = today - timedelta(days=max(1, round(window_hours / 24)))
    out: list[Change] = []

    for deal in deals:
        before = previous.get(deal.id)
        status = deal.status.value

        if before is None:
            # New to the dataset. Only interesting if a source actually
            # asserted it recently, otherwise a backfill floods the email.
            if deal.last_verified_at and deal.last_verified_at >= cutoff:
                out.append(Change(deal, "tracked",
                                  f"now tracked at {deal.cred}/100"))
            continue

        if before.get("status") != status:
            if status == Status.collapsed.value:
                reason = deal.collapse_narrative or "reported off"
                out.append(Change(deal, "collapsed", reason,
                                  before.get("status"), status))
            else:
                out.append(Change(
                    deal, "status",
                    f"{before.get('status')} to {status}",
                    before.get("status"), status,
                ))
            continue

        old_cred = before.get("cred")
        if isinstance(old_cred, int) and abs(deal.cred - old_cred) >= CRED_MOVE_THRESHOLD:
            direction = "up" if deal.cred > old_cred else "down"
            out.append(Change(
                deal, "cred", f"{direction} {old_cred} to {deal.cred}",
                old_cred, deal.cred,
            ))

    return out


def snapshot(deals: list[Deal]) -> dict[str, dict]:
    """The state the next run compares against."""
    return {
        d.id: {"status": d.status.value, "cred": d.cred, "fee": d.fee}
        for d in deals
    }


# ----------------------------------------------------------------- segments


def build_editions(
    changes: list[Change], today: date, clubs: list[str],
) -> list[Edition]:
    """One unsegmented edition, plus one per club that actually has news.

    A club with nothing to report gets no edition rather than a quiet one.
    The unsegmented edition is the one that always sends, quiet or not,
    because "nothing moved today" is only reassuring from a source that
    reports on the whole window.
    """
    editions: dict[str, Edition] = {"all": Edition("all", today)}

    def place(edition: Edition, change: Change) -> None:
        bucket = {
            "tracked": edition.tracked,
            "status": edition.status_moves,
            "cred": edition.cred_moves,
            "collapsed": edition.collapses,
        }[change.kind]
        bucket.append(change)

    known = set(clubs)
    for change in changes:
        place(editions["all"], change)
        for club in {change.deal.to, change.deal.from_club} & known:
            edition = editions.setdefault(club, Edition(club, today))
            place(edition, change)

    ordered = [editions["all"]]
    ordered += [e for name, e in sorted(editions.items()) if name != "all"]
    return ordered


def instant_alerts(changes: list[Change], cap: int = MAX_ALERTS_PER_DAY) -> list[Change]:
    """TI-011, step 4. The two things worth a separate email, at most.

    Ranked by fee so that when more than `cap` qualify, the ones that survive
    are the ones a reader would have wanted. The cap is not negotiable: an
    alert tier that fires six times on deadline day trains people to mute it,
    and then it is not an alert tier.
    """
    qualifying = [
        c for c in changes
        if (c.kind == "status" and c.to_value in
            (Status.done.value, Status.confirmed.value)
            and (c.deal.fee or 0) >= ALERT_FEE_GBP_M)
        or (c.kind == "cred" and isinstance(c.to_value, int)
            and c.to_value >= ALERT_CRED
            and isinstance(c.from_value, int) and c.from_value < ALERT_CRED)
    ]
    qualifying.sort(key=lambda c: -(c.deal.fee or 0))
    return qualifying[:cap]


# ------------------------------------------------------------------ rendering


def _line(change: Change, base_url: str) -> str:
    url = f"{base_url.rstrip('/')}/deals/{change.deal.id}/"
    return f"- [{change.headline}]({url}): {change.detail}"


def render_markdown(edition: Edition, base_url: str, window_name: str = "") -> str:
    """The email body. Markdown because both recommended providers take it."""
    who = "" if edition.segment == "all" else f" · {edition.segment}"
    lines = [
        f"# TransferIntel{who}",
        "",
        f"{edition.for_date.strftime('%A %d %B %Y')}"
        + (f" · {window_name}" if window_name else ""),
        "",
    ]

    if edition.is_quiet:
        # The quiet edition. Short on purpose. Padding it would undo the point.
        lines += [
            "Nothing moved today that clears our reporting bar.",
            "",
            "No new deals, no status changes, no credibility swings worth "
            "reporting. That is a real result during a window: most days "
            "produce noise rather than news.",
            "",
            f"[The full index is where it always is]({base_url.rstrip('/')}/).",
        ]
        return "\n".join(lines) + "\n"

    sections = (
        ("New deals tracked", edition.tracked),
        ("Status changes", edition.status_moves),
        (f"Credibility moves of {CRED_MOVE_THRESHOLD}+", edition.cred_moves),
        ("Collapsed", edition.collapses),
    )
    for title, items in sections:
        if not items:
            continue
        lines += [f"## {title}", ""]
        lines += [_line(c, base_url) for c in items]
        lines.append("")

    lines += [
        "---",
        "",
        "Every score above is computed from published reporting, not "
        "predicted. A completed transfer requires a tier 1 source using "
        "language that only applies after the fact, or the club's own "
        "announcement.",
        "",
        f"[Open the full index]({base_url.rstrip('/')}/)",
    ]
    return "\n".join(lines) + "\n"


def render_subject(edition: Edition) -> str:
    if edition.is_quiet:
        return "TransferIntel: a quiet day"
    who = "" if edition.segment == "all" else f"{edition.segment}: "
    if edition.collapses:
        return f"{who}{edition.collapses[0].headline} is off"
    if edition.status_moves:
        return f"{who}{edition.status_moves[0].headline}, {edition.status_moves[0].detail}"
    return f"{who}{edition.count} updates this morning"


# ------------------------------------------------------------------ send log


def already_sent(log_path: Path, for_date: date) -> bool:
    """TI-010. The pipeline never sends twice for the same day.

    A duplicate send is the fastest way to lose a subscriber you spent the
    whole window earning, and a rerun after a failed step is exactly the
    situation where it happens.
    """
    if not log_path.exists():
        return False
    try:
        log = json.loads(log_path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return False
    return for_date.isoformat() in log.get("sent", [])


def mark_sent(log_path: Path, for_date: date) -> None:
    log = {"sent": []}
    if log_path.exists():
        try:
            log = json.loads(log_path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            pass
    sent = set(log.get("sent", []))
    sent.add(for_date.isoformat())
    log["sent"] = sorted(sent)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(json.dumps(log, indent=2), encoding="utf-8")
