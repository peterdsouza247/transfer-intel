"""Where the news comes from and how much it is worth.

Source tier is a property of the outlet, decided here, once, in code. It is
never inferred at runtime and never asked of a model. If an outlet is not in
`DOMAIN_TIER` it is tier 3 by default, which is the safe direction to be
wrong in: an unknown outlet can nudge a score but can never move a deal along
the ladder (see scoring.state_change_min_tier).
"""

from __future__ import annotations

from urllib.parse import urlsplit

#: Feeds polled by phase 1. Add to this rather than scraping anything.
#: Transfermarkt is deliberately absent: it blocks datacenter IPs, so it
#: cannot work from a GitHub runner.
FEEDS: list[tuple[str, str]] = [
    ("https://feeds.bbci.co.uk/sport/football/rss.xml", "BBC Sport"),
    ("https://www.theguardian.com/football/rss", "The Guardian"),
    ("https://www.skysports.com/rss/12040", "Sky Sports"),
    ("https://www.football365.com/feed", "Football365"),
    ("https://www.telegraph.co.uk/football/rss.xml", "Telegraph"),
]

#: Tier 1 is a journalist whose word moves a market. Tier 2 is an established
#: outlet doing real reporting. Tier 3 is everything else, including foreign
#: press aggregating each other.
DOMAIN_TIER: dict[str, int] = {
    "skysports.com": 1,
    "theathletic.com": 1,
    "nytimes.com": 1,          # The Athletic lives here now
    "bbc.co.uk": 1,
    "bbc.com": 1,
    "theguardian.com": 2,
    "telegraph.co.uk": 2,
    "football365.com": 2,
    "footballtransfers.com": 2,
    "independent.co.uk": 2,
    "standard.co.uk": 2,
    "espn.com": 2,
    "goal.com": 3,
    "mirror.co.uk": 3,
    "thesun.co.uk": 3,
    "dailymail.co.uk": 3,
    "express.co.uk": 3,
    "caughtoffside.com": 3,
    "givemesport.com": 3,
    "abola.pt": 3,
    "marca.com": 3,
    "sport.es": 3,
}

DEFAULT_TIER = 3

#: Display names for the `src` field and the PR body. Falls back to the domain.
OUTLET_NAMES: dict[str, str] = {
    "skysports.com": "Sky Sports",
    "theathletic.com": "The Athletic",
    "nytimes.com": "The Athletic",
    "bbc.co.uk": "BBC Sport",
    "bbc.com": "BBC Sport",
    "theguardian.com": "The Guardian",
    "telegraph.co.uk": "Telegraph",
    "football365.com": "Football365",
    "footballtransfers.com": "FootballTransfers",
    "independent.co.uk": "The Independent",
    "standard.co.uk": "Evening Standard",
    "espn.com": "ESPN",
    "goal.com": "Goal",
    "mirror.co.uk": "The Mirror",
    "thesun.co.uk": "The Sun",
    "dailymail.co.uk": "Daily Mail",
    "express.co.uk": "Daily Express",
    "caughtoffside.com": "CaughtOffside",
    "givemesport.com": "GiveMeSport",
    "abola.pt": "A Bola",
    "marca.com": "Marca",
    "sport.es": "Sport",
}

#: Fees get quoted in whatever currency the outlet fancies. The model reports
#: the number and the currency it saw; the conversion happens here, in code,
#: so it is one line to update and it shows up in git history.
FX_TO_GBP: dict[str, float] = {
    "GBP": 1.0,
    "EUR": 0.84,
    "USD": 0.78,
}


def domain_of(url: str) -> str:
    host = urlsplit(url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    if host.startswith("m."):
        host = host[2:]
    # Collapse co.uk and com.au style suffixes to the registrable domain.
    parts = host.split(".")
    if len(parts) > 2 and parts[-2] in {"co", "com", "org", "net"}:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:]) if len(parts) > 1 else host


def tier_for(url: str) -> int:
    return DOMAIN_TIER.get(domain_of(url), DEFAULT_TIER)


def outlet_for(url: str) -> str:
    d = domain_of(url)
    return OUTLET_NAMES.get(d, d)


def to_gbp_millions(amount: float | None, currency: str | None) -> float | None:
    if amount is None:
        return None
    rate = FX_TO_GBP.get((currency or "GBP").upper())
    if rate is None:
        return None
    return round(amount * rate, 1)
