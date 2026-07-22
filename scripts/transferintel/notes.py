"""Phase 4: the one sentence per deal that is the personality of the site.

This is the only place a strong model is worth paying for, and it is
deliberately the narrowest possible job: given a deal, its evidence, and the
numbers phase 3 already computed, write one sentence. The model is never
asked for a score, a status, a fee or a club name. If it hallucinates, the
worst case is a bad sentence next to correct data.

Style is enforced in code afterwards. The prompt asks nicely; the validator
decides.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import date

from .models import Deal, PatchOp
from .scoring import recent, ScoringConfig, DEFAULT_CONFIG

#: Notes are one sentence of 6 to 30 words, written against an explicit rules
#: block, and validated on the way out with a retry that quotes the specific
#: violation back. That is a task shaped for a small model, and Haiku costs
#: roughly a third of Sonnet per token. Set TI_NOTE_MODEL to override if the
#: prose quality drops below what you want on the page.
DEFAULT_MODEL = os.environ.get("TI_NOTE_MODEL", "claude-haiku-4-5-20251001")

#: House style: no em dash, en dash, horizontal bar, or figure dash. Ever.
BANNED_CHARS = "\u2014\u2013\u2015\u2012"
MAX_WORDS = 30
MIN_WORDS = 6

#: Openers that signal the model is narrating instead of writing.
BANNED_OPENERS = (
    "note:", "this deal", "here is", "in summary", "the deal ",
    "reports suggest that", "it appears",
)

SYSTEM_PROMPT = """\
You write one-sentence context notes for TransferIntel, a Premier League \
transfer window site.

Your entire job is a single sentence of sharp editorial context for one deal. \
You are given the deal, the sources that mentioned it recently, and the \
credibility score and status that have already been computed. Those numbers \
are final and are not yours to argue with. Your sentence explains why the \
deal matters, what is actually holding it up, or what it tells you about the \
club's window.

Rules, all of them hard:

- Exactly one sentence, between 6 and 30 words.
- Never use an em dash or an en dash. Use commas, colons, semicolons, \
periods, or the separator character.
- Never invent a fee, a club, a player, a date or a quote. Everything factual \
in your sentence must appear in the input.
- Never restate the status or the credibility number. The card already shows \
them. Add what the card cannot.
- No hedging filler: avoid "reportedly", "it seems", "sources suggest".
- Plain declarative voice. No exclamation marks, no rhetorical questions.
- Output the sentence and nothing else. No preamble, no quotation marks, no \
markdown.

Examples of the voice:

Deal: Kudus, West Ham to Tottenham, agreed, 55m
Note: Spurs finally paying market rate for a winger who beat them twice last \
season.

Deal: Guehi, Crystal Palace to Liverpool, talks, 45m
Note: Palace have no leverage left with twelve months on the contract, and \
Liverpool know it.

Deal: Sancho, Manchester United to Roma, collapsed, 20m
Note: Wages killed it, as they have killed every Sancho exit since 2023.

Deal: Ekitike, Eintracht Frankfurt to Newcastle, medical, 69m
Note: Newcastle's answer to the Isak question, bought before the question was \
even asked out loud.

Deal: Rodrygo, Real Madrid to Arsenal, rumor, 70m
Note: Recycled every window since Arteta signed his extension, and no closer \
than the first time.\
"""


@dataclass
class NoteResult:
    deal_id: str
    text: str | None
    attempts: int = 0
    rejected: list[str] = None
    skipped: str | None = None

    def __post_init__(self) -> None:
        if self.rejected is None:
            self.rejected = []


# ---------------------------------------------------------------- triggers


def needs_note(deal: Deal, ops: list[PatchOp], cred_delta_threshold: int = 8) -> bool:
    """Rewrite a note only when something actually happened.

    Regenerating every note every day burns money, thrashes the diff, and
    makes the PR unreviewable. A deal whose credibility drifted two points
    from silence does not need new prose.
    """
    if not deal.note.strip():
        return True
    mine = [o for o in ops if o.id == deal.id and o.op == "update"]
    for o in mine:
        if o.field == "status":
            return True
        if o.field == "fee":
            return True
        if o.field == "cred":
            try:
                if abs(int(o.to) - int(o.from_value)) >= cred_delta_threshold:
                    return True
            except (TypeError, ValueError):
                return True
    return False


# ---------------------------------------------------------------- validation


def clean(text: str) -> str:
    """Strip the wrappers models add out of habit, before judging the prose."""
    t = " ".join(text.strip().split())
    t = re.sub(r"^(note|context)\s*:\s*", "", t, flags=re.I)
    if len(t) > 1 and t[0] in "\"'\u201c\u2018" and t[-1] in "\"'\u201d\u2019":
        t = t[1:-1].strip()
    return t


def validate(text: str) -> tuple[bool, str]:
    """Deterministic gate. The prompt is a request; this is the rule."""
    t = clean(text)
    if not t:
        return False, "empty output"
    for ch in BANNED_CHARS:
        if ch in t:
            return False, f"contains a banned dash character (U+{ord(ch):04X})"
    words = t.split()
    if len(words) > MAX_WORDS:
        return False, f"{len(words)} words, limit is {MAX_WORDS}"
    if len(words) < MIN_WORDS:
        return False, f"{len(words)} words, minimum is {MIN_WORDS}"
    if "\n" in text.strip():
        return False, "more than one line"
    if t.count(".") > 1 and not re.search(r"\b\w\.\w\.", t):
        return False, "more than one sentence"
    if t.endswith("?") or "!" in t:
        return False, "rhetorical question or exclamation"
    if not t.endswith("."):
        return False, "does not end in a period"
    low = t.lower()
    for opener in BANNED_OPENERS:
        if low.startswith(opener):
            return False, f"opens with banned phrase {opener!r}"
    return True, ""


# ---------------------------------------------------------------- prompt


def build_user_message(
    deal: Deal, today: date, cfg: ScoringConfig = DEFAULT_CONFIG,
    club_context: str | None = None,
) -> str:
    lines = [
        f"Player: {deal.p}",
        f"Move: {deal.from_club} to {deal.to}",
        f"Position and age: {deal.pos}, {deal.age}",
        f"Fee: {'free transfer' if deal.fee == 0 else f'{deal.fee}m GBP'}",
        f"Status: {deal.status.value}",
        f"Credibility: {deal.cred}",
    ]
    if club_context:
        lines.append(f"Buying club's window so far: {club_context}")
    if deal.note.strip():
        lines.append(f"Previous note (now out of date): {deal.note}")

    window = recent(deal, today, cfg)
    if window:
        lines.append("Recent sources:")
        for e in sorted(window, key=lambda x: (x.tier, x.date)):
            fee = f", fee {e.fee_gbp_m}m" if e.fee_gbp_m is not None else ""
            lines.append(
                f"  - {e.source} (tier {e.tier}, {e.date.isoformat()}): "
                f"claims {e.claim.value}{fee}"
            )
    else:
        lines.append("Recent sources: none, this deal has gone quiet.")

    lines.append("")
    lines.append("Write the note.")
    return "\n".join(lines)


# ---------------------------------------------------------------- writer


class NoteWriter:
    """Wraps the Anthropic client with retries, style enforcement and a budget.

    With no API key present the writer runs in dry mode: it builds and logs
    every prompt but returns no text, so the rest of the pipeline stays
    testable offline and in CI.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str = DEFAULT_MODEL,
        max_notes: int = 12,
        max_tokens: int = 120,
        cfg: ScoringConfig = DEFAULT_CONFIG,
    ) -> None:
        self.model = model
        self.max_notes = max_notes
        self.max_tokens = max_tokens
        self.cfg = cfg
        self.spent = 0
        self.prompts: list[tuple[str, str]] = []
        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self.dry = not key
        self._client = None
        if key:
            from anthropic import Anthropic  # imported lazily so dry runs need no SDK

            self._client = Anthropic(api_key=key)

    def _call(self, user_msg: str, correction: str | None = None) -> str:
        messages = [{"role": "user", "content": user_msg}]
        if correction:
            messages.append({"role": "user", "content": correction})
        resp = self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    # The rules block is identical on every call, every day.
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=messages,
        )
        return "".join(b.text for b in resp.content if b.type == "text")

    def write(
        self, deal: Deal, today: date, club_context: str | None = None
    ) -> NoteResult:
        if self.spent >= self.max_notes:
            return NoteResult(deal.id, None, skipped="note budget exhausted")

        user_msg = build_user_message(deal, today, self.cfg, club_context)
        self.prompts.append((deal.id, user_msg))
        if self.dry:
            return NoteResult(deal.id, None, skipped="dry run, no API key")

        result = NoteResult(deal.id, None)
        correction: str | None = None
        for attempt in (1, 2):
            result.attempts = attempt
            self.spent += 1
            try:
                raw = self._call(user_msg, correction)
            except Exception as exc:  # fail soft, never break the site
                result.rejected.append(f"API error: {exc}")
                return result
            ok, why = validate(raw)
            if ok:
                result.text = clean(raw)
                return result
            result.rejected.append(f"{why}: {raw.strip()[:120]}")
            correction = (
                f"That was rejected: {why}. Rewrite the sentence so it passes. "
                "Output only the sentence."
            )
        return result


def note_ops(
    deals: list[Deal],
    ops: list[PatchOp],
    today: date,
    writer: NoteWriter,
    club_context: dict[str, str] | None = None,
) -> tuple[list[PatchOp], list[NoteResult]]:
    """Run phase 4 over the deals phase 3 actually touched."""
    club_context = club_context or {}
    new_ops: list[PatchOp] = []
    results: list[NoteResult] = []

    targets = [d for d in deals if needs_note(d, ops)]
    # Spend the budget on the deals that moved furthest.
    moved = {o.id for o in ops if o.op == "update" and o.field == "status"}
    targets.sort(key=lambda d: (d.id not in moved, -d.cred))

    for deal in targets:
        # Write against the state the deal is about to be in, not the stale
        # one. A note describing "agreed" on a deal this run moves to "medical"
        # is wrong the moment the patch lands.
        pending = {
            o.field: o.to for o in ops
            if o.id == deal.id and o.op == "update" and o.field != "note"
        }
        # Revalidate rather than model_copy: patch values arrive as raw JSON
        # scalars, and the prompt builder needs real enums.
        projected = (
            Deal.model_validate({**deal.model_dump(by_alias=True), **pending})
            if pending else deal
        )
        res = writer.write(projected, today, club_context.get(deal.to))
        results.append(res)
        if res.text and res.text != deal.note:
            new_ops.append(PatchOp(
                id=deal.id, field="note",
                from_value=deal.note, to=res.text,
                reason="regenerated after the deal moved",
            ))
        elif res.rejected:
            new_ops.append(PatchOp(
                op="flag", id=deal.id, field="note",
                reason="note rewrite failed style checks, previous note kept: "
                       + "; ".join(res.rejected[:2]),
            ))
    return new_ops, results
