"""TI-001: what counts as proof that a transfer actually happened.

The defect this module exists to prevent was not a scoring error. It was a
category error: the pipeline treated *absence of contradicting news* as
confirmation, so any deal it re-encountered in the feed, or simply failed to
hear had collapsed, drifted up to `done` with credibility 100. Six records
carried the marker "Confirmed (auto-updated)" while their own summary text
said "announcement imminent", "personal terms being discussed", and in one
case "then silence".

The fix is to make completion require a *positive* signal that is recorded on
the record, auditable after the fact, and impossible to produce by silence.
Nothing in here infers. Every function answers one question: did a source say
one of these specific things, yes or no.

Three strengths of signal, weakest to strongest:

1. **Reported completion** (`Stage.completed` with no marker). A source says
   the deal is finished. This is worth `confirmed`, never `done`.
2. **Phrase marker.** A source uses language that only applies after the fact:
   "has signed", "completes his move", "unveiled".
3. **Official.** The URL is on the club's own domain. Nobody can walk this
   back, and it is the only signal that earns credibility 100.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from .models import CollapseReason

# --------------------------------------------------------------- completion

#: Phrases that only make sense once a transfer has happened.
#:
#: Every entry here was chosen because the tense does the work. "Has signed"
#: cannot be said of a deal that has not been signed; "set to sign", "poised
#: to complete" and "expected to be announced" all can, which is why the
#: hedged forms are excluded by `_hedged` rather than merely left out.
#:
#: These are regexes rather than substrings because real headlines put a club
#: name in the middle of the phrase. "Moreno completes Newcastle move" and
#: "Moreno completes his move to Newcastle" are the same claim, and a literal
#: match on "completes his move" catches only one of them.
COMPLETION_PATTERNS: tuple[tuple[str, str], ...] = (
    ("has signed", r"\b(?:has|have) signed\b"),
    ("has joined", r"\b(?:has|have) joined\b"),
    # "Manzambi joins Aston Villa" is one of the commonest ways a completed
    # transfer is written, and the bare present tense was being missed. The
    # lookahead is the whole trick: "joins Aston Villa" is a completion,
    # "joins the race", "joins talks" and "joins up with" are not.
    ("joins", r"\bjoins\b(?!\s+(?:the\s+(?:race|hunt|chase|list)|talks|"
              r"up\b|forces|in\b|his\s|team-?mates))"),
    ("officially signed", r"\bofficially (?:signed|joined|completed)\b"),
    ("completes move", r"\bcomplet(?:es|ed) (?:\w+ ){0,3}?(?:move|transfer|switch)\b"),
    ("seals move", r"\bseal(?:s|ed) (?:\w+ ){0,3}?(?:move|transfer|switch)\b"),
    ("unveiled", r"\b(?:has been |was )?unveiled\b"),
    ("put pen to paper", r"\bput pen to paper\b"),
    ("signed a contract", r"\bsigned a (?:\w+[- ]year )?(?:contract|deal)\b"),
    ("announce the signing", r"\bannounc(?:e|es|ed) the (?:signing|arrival)\b"),
    ("medical passed", r"\b(?:passed|completed) (?:his |the )?medical\b"),
    ("welcome", r"\bwelcome (?:to|)\b.{0,30}\b(?:on a|joins|signing)\b"),
)

#: Compiled once. Order is preserved: the first match wins and its label is
#: what gets written onto the record.
_COMPLETION_RE: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (label, re.compile(pattern, re.I)) for label, pattern in COMPLETION_PATTERNS
)

#: Kept for callers that want the human-readable list, e.g. the deal page.
COMPLETION_PHRASES: tuple[str, ...] = tuple(
    label for label, _ in COMPLETION_PATTERNS
)

#: Fabrizio Romano's catchphrase, which he uses only when a deal is done.
#: Scoped to Romano specifically, as the backlog requires: the phrase has been
#: borrowed by aggregators who use it to mean "we think this will happen".
ROMANO_PHRASE = "here we go"
ROMANO_SOURCES = ("romano", "fabrizio romano")

#: Hedges that turn a completion phrase back into a rumour. Checked against
#: the window of text immediately before the marker, because "set to sign for"
#: and "sign for" differ by two words and by everything that matters.
HEDGES: tuple[str, ...] = (
    "set to",
    "poised to",
    "expected to",
    "close to",
    "on the verge of",
    "agreed to",
    "will",
    "could",
    "may",
    "might",
    "reportedly",
    "believed to",
    "understood to",
    "in line to",
    "wants to",
    "hopes to",
    "ready to",
    "about to",
)

_HEDGE_WINDOW = 28  # characters of lookbehind, roughly four words

#: Club-controlled domains. A page here is the club saying it itself.
#: Extend freely; an unknown domain simply is not treated as official, which
#: is the safe direction to be wrong in.
OFFICIAL_DOMAINS: frozenset[str] = frozenset({
    "arsenal.com",
    "avfc.co.uk",
    "afcb.co.uk",
    "brentfordfc.com",
    "brightonandhovealbion.com",
    "burnleyfootballclub.com",
    "chelseafc.com",
    "cpfc.co.uk",
    "evertonfc.com",
    "fulhamfc.com",
    "itfc.co.uk",
    "lcfc.com",
    "liverpoolfc.com",
    "mancity.com",
    "manutd.com",
    "nufc.co.uk",
    "nottinghamforest.co.uk",
    "readingfc.co.uk",
    "safc.com",
    "southamptonfc.com",
    "tottenhamhotspur.com",
    "whufc.com",
    "wolves.co.uk",
    "premierleague.com",
    "realmadrid.com",
    "fcbarcelona.com",
    "psg.fr",
    "sscnapoli.it",
    "asroma.com",
    "atalanta.it",
    "sportinggoodsclub.pt",
    "sporting.pt",
    "bayerlevkusen.com",
    "bayer04.de",
    "scfreiburg.com",
    "losc.fr",
    "staderennais.com",
})


def domain(url: str) -> str:
    host = urlsplit(url).netloc.lower()
    for prefix in ("www.", "m.", "en."):
        if host.startswith(prefix):
            host = host[len(prefix):]
    return host


def is_official(url: str) -> bool:
    """True when the URL is on a club's own domain."""
    host = domain(url)
    return any(host == d or host.endswith("." + d) for d in OFFICIAL_DOMAINS)


def _hedged(text: str, at: int) -> bool:
    """Is the marker at `at` preceded by language that undoes it?"""
    window = text[max(0, at - _HEDGE_WINDOW):at]
    return any(h in window for h in HEDGES)


def completion_marker(text: str, source: str = "", url: str = "") -> str | None:
    """The phrase proving completion, or None.

    Returns the matched phrase rather than a boolean so it can be written onto
    the record and read back later. When a human asks why a transfer says
    `done`, the answer is a quotable string and a link, not "the pipeline
    decided".
    """
    if url and is_official(url):
        return f"official announcement on {domain(url)}"

    haystack = (text or "").lower()
    src = (source or "").lower()

    if ROMANO_PHRASE in haystack and any(r in src for r in ROMANO_SOURCES):
        at = haystack.find(ROMANO_PHRASE)
        if not _hedged(haystack, at):
            return ROMANO_PHRASE

    for label, pattern in _COMPLETION_RE:
        found = pattern.search(haystack)
        if found and not _hedged(haystack, found.start()):
            return label
    return None


# ----------------------------------------------------------------- collapse

#: TI-023. Ordered: the first pattern to match wins, so the specific causes
#: are listed before the general ones. `hijacked` sits above `fee_gap`
#: because a hijack usually mentions money too.
_COLLAPSE_PATTERNS: tuple[tuple[CollapseReason, re.Pattern[str]], ...] = (
    (CollapseReason.failed_medical,
     re.compile(r"fail\w* (?:the |a |his )?medical|medical (?:issue|problem|"
                r"concern)|did not pass|flunked the medical", re.I)),
    (CollapseReason.hijacked,
     re.compile(r"hijack\w*|beaten to (?:the |his )?(?:signature|punch)|"
                r"swoop\w* in|snatched", re.I)),
    (CollapseReason.player_refused,
     re.compile(r"(?:player|he) (?:has )?(?:refus\w*|reject\w*|turned down|"
                r"snubb\w*)|does not want the move|no interest in joining", re.I)),
    (CollapseReason.seller_withdrew,
     re.compile(r"(?:club|they) (?:pulled|withdrew|took him off)|"
                r"no longer for sale|withdrawn from the sale", re.I)),
    (CollapseReason.agent_demands,
     re.compile(r"agent (?:fee|demand|commission)|intermediary fee", re.I)),
    (CollapseReason.personal_terms,
     re.compile(r"personal terms (?:could not|collapsed|broke down|"
                r"were not agreed)|wage demands|salary demands", re.I)),
    (CollapseReason.financial_rules,
     re.compile(r"squad cost ratio|psr|profit and sustainability|"
                r"financial fair play|ffp", re.I)),
    (CollapseReason.fee_gap,
     re.compile(r"valuation gap|could not agree (?:a )?fee|bid rejected|"
                r"price too high|too far apart|apart on (?:the )?(?:fee|"
                r"valuation)", re.I)),
)


def collapse_reason(text: str) -> CollapseReason:
    """Classify why a deal died. `unknown` is a real answer, not a failure."""
    for reason, pattern in _COLLAPSE_PATTERNS:
        if pattern.search(text or ""):
            return reason
    return CollapseReason.unknown


# ------------------------------------------------------ contradiction audit

#: Language that contradicts a `done` status when it appears in a deal's own
#: summary. Used by the TI-001 backfill to find records whose note argues with
#: their own status, which is how the original six were spotted by eye.
CONTRADICTION_PATTERNS: tuple[str, ...] = (
    "announcement imminent",
    "announcement expected",
    "expected this week",
    "being discussed",
    "under discussion",
    "bar the unveiling",
    "all done bar",
    "then silence",
    "no collapse reported",
    "still to be",
    "yet to be",
    "personal terms being",
    "auto-updated",
    "hijacked",
    "medical booked",
    "set to",
    "expected to",
)


def contradicts_completion(note: str) -> str | None:
    """The phrase in a note that argues with a `done` status, or None."""
    low = (note or "").lower()
    for phrase in CONTRADICTION_PATTERNS:
        if phrase in low:
            return phrase
    return None
