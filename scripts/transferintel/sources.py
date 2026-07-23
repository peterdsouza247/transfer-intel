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
#: Sky's 12040 is the general football news id, not the transfer centre, so
#: it carries match reports and women's football alongside transfers. That is
#: what the prefilter is for. Feeds are cheap to poll and the filter is cheap
#: to run, so breadth here is the right trade.
#:
#: Run `python scripts/check_feeds.py` to see which of these actually respond
#: and how much each contributes before adding or removing any.
FEEDS: list[tuple[str, str]] = [
    ("https://feeds.bbci.co.uk/sport/football/rss.xml", "BBC Sport"),
    ("https://www.theguardian.com/football/rss", "The Guardian"),
    ("https://www.skysports.com/rss/12040", "Sky Sports"),
    ("https://www.dailymail.co.uk/sport/football/index.rss", "Daily Mail"),   # 55 kept
    ("https://talksport.com/football/feed/", "talkSPORT"),   # 32 kept
    ("https://www.manchestereveningnews.co.uk/sport/football/?service=rss", "Manchester Evening News"),   # 21 kept
    ("https://www.birminghammail.co.uk/sport/football/?service=rss", "Birmingham Mail"),   # 21 kept
    ("https://www.standard.co.uk/sport/football/rss", "Standard"),   # 20 kept
    ("https://www.football.london/?service=rss", "football.london"),   # 20 kept
    ("https://www.mirror.co.uk/sport/football/?service=rss", "Mirror"),   # 17 kept
    ("https://www.liverpoolecho.co.uk/sport/football/?service=rss", "Liverpool Echo"),   # 17 kept
    ("https://www.chroniclelive.co.uk/sport/football/?service=rss", "Chronicle Live"),   # 17 kept
    ("https://metro.co.uk/sport/football/feed/", "Metro"),   # 15 kept
    ("https://www.independent.co.uk/sport/football/rss", "Independent"),   # 10 kept
    ("https://www.caughtoffside.com/feed/", "CaughtOffside"),   # 10 kept
    ("https://www.espn.com/espn/rss/soccer/news", "ESPN"),   # 9 kept
]

#: Removed 23 July 2026, both confirmed by check_feeds.py:
#:   Football365, https://www.football365.com/feed
#:     no response at all
#:   Telegraph, https://www.telegraph.co.uk/football/rss.xml
#:     responded with 120 articles, none newer than the window. A feed
#:     serving stale content is worse than one that fails: it raises no
#:     warning, parses cleanly, and contributes nothing.
#: Their DOMAIN_TIER entries stay, because both still appear as sources in
#: articles other outlets link to.

#: Feeds worth trying, none of them verified from here. Publishers move and
#: retire RSS constantly, so this is a list of addresses to test rather than a
#: list of feeds that work.
#:
#:     python scripts/check_feeds.py --candidates
#:
#: That prints, for each one, whether it responds and how many transfer
#: articles it actually contributes after filtering, then gives you the exact
#: lines to paste into FEEDS and DOMAIN_TIER above.
CANDIDATE_FEEDS: list[tuple[str, str, int]] = [
    # (url, name, tier it would be given)
    ("https://www.teamtalk.com/feed", "TEAMtalk", 2),
#    ("https://talksport.com/football/feed/", "talkSPORT", 2),
#    ("https://www.independent.co.uk/sport/football/rss", "Independent", 2),
#    ("https://www.standard.co.uk/sport/football/rss", "Standard", 2),
#    ("https://metro.co.uk/sport/football/feed/", "Metro", 3),
#    ("https://www.mirror.co.uk/sport/football/?service=rss", "Mirror", 3),
#    ("https://www.dailymail.co.uk/sport/football/index.rss", "Daily Mail", 3),
    ("https://www.90min.com/posts.rss", "90min", 3),
#    ("https://www.caughtoffside.com/feed/", "CaughtOffside", 3),
#    ("https://www.football.london/?service=rss", "football.london", 3),
#    ("https://www.manchestereveningnews.co.uk/sport/football/?service=rss", "Manchester Evening News", 2),
#    ("https://www.liverpoolecho.co.uk/sport/football/?service=rss", "Liverpool Echo", 2),
#    ("https://www.chroniclelive.co.uk/sport/football/?service=rss", "Chronicle Live", 2),
#    ("https://www.birminghammail.co.uk/sport/football/?service=rss", "Birmingham Mail", 2),
#    ("https://www.espn.com/espn/rss/soccer/news", "ESPN", 2),
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
    "talksport.com": 2,
    "liverpoolecho.co.uk": 2,
    "chroniclelive.co.uk": 2,
    "manchestereveningnews.co.uk": 2,
    "birminghammail.co.uk": 2,
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
    "football.london": 3,
    "metro.co.uk": 3,
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
