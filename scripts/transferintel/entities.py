"""Turning the names in a headline into the exact strings `data.json` uses.

The site matches club dashboards on exact name equality, so "Man Utd" and
"Manchester United" being different strings is a real bug, not a cosmetic
one. Everything here is deterministic string work. A model is never asked to
pick a canonical name, only to report the name it saw.
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

#: Alias to canonical. The canonical side must match the keys of `clubs` in
#: data.json exactly. Extend freely, it costs nothing.
CLUB_ALIASES: dict[str, str] = {
    "man utd": "Manchester United",
    "man united": "Manchester United",
    "manchester utd": "Manchester United",
    "united": "Manchester United",
    "man city": "Manchester City",
    "city": "Manchester City",
    "spurs": "Tottenham",
    "tottenham hotspur": "Tottenham",
    "wolves": "Wolves",
    "wolverhampton wanderers": "Wolves",
    "wolverhampton": "Wolves",
    "brighton and hove albion": "Brighton",
    "brighton & hove albion": "Brighton",
    "west ham united": "West Ham",
    "newcastle united": "Newcastle",
    "leeds united": "Leeds",
    "nottingham forest": "Nott'm Forest",
    "notts forest": "Nott'm Forest",
    "forest": "Nott'm Forest",
    "afc bournemouth": "Bournemouth",
    "leicester city": "Leicester",
    "norwich city": "Norwich",
    "sporting lisbon": "Sporting CP",
    "sporting": "Sporting CP",
    "inter milan": "Inter",
    "internazionale": "Inter",
    # Canonical forms follow data.json rather than the other way round. The
    # site matches dashboards on exact string equality, so the alias table
    # producing "Atletico" while every record said "Atletico Madrid" meant an
    # ingested deal and a migrated one described the same club two ways.
    "atletico madrid": "Atlético Madrid",
    "athletico madrid": "Atlético Madrid",
    "atletico": "Atlético Madrid",
    "psg": "PSG",
    "paris sg": "PSG",
    "paris saint-germain": "PSG",
    "bayern munich": "Bayern",
    "borussia dortmund": "Dortmund",
    "eintracht": "Eintracht Frankfurt",
    "rb leipzig": "Leipzig",
}

#: Words that carry no signal when comparing player names.
_NAME_NOISE = {"jr", "junior", "de", "da", "dos", "van", "der", "den", "el", "al"}


#: Letters NFKD will not take apart, because they are distinct letters rather
#: than a base plus a combining mark. Odegaard is the one that matters here.
_TRANSLIT = str.maketrans({
    "\u00d8": "O", "\u00f8": "o", "\u00c6": "AE", "\u00e6": "ae",
    "\u0152": "OE", "\u0153": "oe", "\u0110": "D", "\u0111": "d",
    "\u0141": "L", "\u0142": "l", "\u00df": "ss", "\u0130": "I",
    "\u0131": "i", "\u00d0": "D", "\u00f0": "d", "\u00de": "Th",
    "\u00fe": "th",
})


def fold(text: str) -> str:
    """Lowercase, transliterate, strip accents and punctuation.

    Odegaard and Nunez have to fold to the same string as their accented
    spellings, or every headline about them resolves to nothing.
    """
    text = text.translate(_TRANSLIT)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.replace("&", " and ")
    text = re.sub(r"[^\w\s]", " ", text.lower())
    return re.sub(r"\s+", " ", text).strip()


def canonical_club(name: str | None, known: list[str]) -> str | None:
    """Resolve a club name to a string that exists in data.json, or None.

    None is a real answer and it means "do not touch the data". A club the
    site does not know about is either out of scope or a typo, and inventing
    it would break the dashboards.
    """
    if not name:
        return None
    folded = fold(name)
    by_fold = {fold(k): k for k in known}

    if folded in by_fold:
        return by_fold[folded]
    alias = CLUB_ALIASES.get(folded)
    if alias and fold(alias) in by_fold:
        return by_fold[fold(alias)]
    if alias:
        return alias

    # "Arsenal FC" and "FC Barcelona" style suffixes.
    stripped = re.sub(r"^(fc|afc|ac|as|ss|rc)\s+|\s+(fc|afc|cf|sc|ac)$", "",
                      folded).strip()
    if stripped in by_fold:
        return by_fold[stripped]
    if stripped in CLUB_ALIASES:
        return CLUB_ALIASES[stripped]

    # A club we simply do not carry. Selling clubs abroad are common and
    # legitimate, so pass the name through in title case rather than dropping
    # the claim entirely; the caller decides whether that is acceptable.
    return name.strip()


def surname(name: str) -> str:
    parts = [p for p in fold(name).split() if p not in _NAME_NOISE]
    return parts[-1] if parts else ""


def name_similarity(a: str, b: str) -> float:
    """0 to 1. Surname agreement dominates, because headlines drop first names."""
    fa, fb = fold(a), fold(b)
    if fa == fb:
        return 1.0
    sa, sb = surname(a), surname(b)
    if sa and sa == sb:
        return 0.95
    return SequenceMatcher(None, fa, fb).ratio()


def resolve_deal(
    player: str,
    to_club: str | None,
    from_club: str | None,
    deals,
    threshold: float = 0.88,
):
    """Match a claim to an existing deal, or return None.

    Player similarity is necessary but nowhere near sufficient. The
    destination club is what identifies a deal: two clubs chasing the same
    player are two different deals, and "Man Utd eye Everton target Okafor"
    must not become evidence on the Everton row. So when the buying club is
    named it has to agree, full stop. Only when it is absent do we fall back
    to matching on the selling club, which is the "Brighton sell Hartley"
    shape of headline.

    A claim naming no club at all resolves to nothing and goes to review.
    """
    best, best_score = None, 0.0
    for deal in deals:
        score = name_similarity(player, deal.p)
        if score < threshold:
            continue

        if to_club:
            if fold(to_club) != fold(deal.to):
                continue
            if from_club and fold(from_club) == fold(deal.from_club):
                score += 0.05  # both ends agree, strongest possible match
        elif from_club:
            if fold(from_club) != fold(deal.from_club):
                continue
        else:
            continue  # no club named, not enough to identify a deal

        if score > best_score:
            best, best_score = deal, score
    return best


def deal_id(player: str, from_club: str, to_club: str) -> str:
    """Stable id for a new deal. Same shape as the ids phase 0 backfills."""
    return "-".join(
        fold(x).replace(" ", "-") for x in (surname(player) or player,
                                            from_club, to_club)
    )


def fold_for_slug(value: str) -> str:
    """Lowercase, accent-stripped, transliterated text for building slugs.

    Shares `_TRANSLIT` with `fold` deliberately: the resolution layer and the
    URL layer must agree on what a name looks like, or a deal resolves under
    one spelling and publishes under another.
    """
    # _TRANSLIT is a str.maketrans table keyed by codepoint, so it must be
    # applied with .translate(); .get(char) silently does nothing.
    out = value.translate(_TRANSLIT)
    out = unicodedata.normalize("NFKD", out)
    return "".join(c for c in out if not unicodedata.combining(c)).lower()
