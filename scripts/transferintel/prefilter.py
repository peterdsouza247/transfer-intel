"""Drop articles that cannot possibly be transfer news, before paying to read them.

A general football feed during a World Cup summer is mostly match reports,
injury updates, federation politics and opinion. A sample of nine consecutive
BBC football headlines in July 2026 contained exactly one transfer story. The
extractor was reading all nine.

This module is a cheap gate in front of the expensive one. It is not trying to
decide whether a transfer is real, or what stage it has reached, or who is
involved. Those are the model's job and it is good at them. This only answers
one question: could this text plausibly be about a player moving clubs? When
the answer is clearly no, the article never reaches the API.

**The asymmetry that governs every choice here.** A false positive costs a
fraction of a penny: one extra article in a batch the model was going to read
anyway. A false negative costs a deal, silently, with nothing in any log to
say a story was missed. So the gate is deliberately generous. It keeps
anything with even a weak signal, and when tuning it the question is never
"how much can this cut" but "what would it take to drop something real".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .models import Article

#: Words that only appear when somebody is moving, or might be. Matched as
#: whole words so "signing" does not fire on "designing" and "bid" does not
#: fire on "forbidden".
TRANSFER_TERMS: tuple[str, ...] = (
    # the act itself
    "transfer", "transfers", "sign", "signs", "signed", "signing", "signings",
    "join", "joins", "joined", "joining", "move", "moves", "moved", "switch",
    "arrival", "arrivals", "departure", "departures", "exit", "exits",
    # money
    "fee", "fees", "bid", "bids", "offer", "offers", "price", "valuation",
    "worth", "wages", "salary", "clause", "add-ons", "sell-on", "buy-back",
    # process
    "medical", "talks", "negotiation", "negotiations", "agree", "agreed",
    "deal", "deals", "contract", "terms", "agent", "unveiled", "announce",
    "announced", "loan", "loaned", "permanent", "swap", "release", "released",
    "free agent", "out of contract", "window",
    # the language of rumour
    "target", "targets", "targeting", "linked", "links", "interest",
    "interested", "pursuit", "chase", "chasing", "swoop", "hijack",
    "hijacked", "poised", "verge", "close to", "wantaway", "snub", "snubbed",
    "reject", "rejected", "approach", "monitoring", "scouted", "shortlist",
    "replacement", "successor", "suitor", "suitors", "available", "for sale",
    "wants out", "future",
    # Resisting a transfer is transfer news. "Iraola wants Mac Allister to
    # stay" only gets written because somebody is trying to buy him, and the
    # filter was dropping the whole genre: contract renewals, hands-off
    # statements, players agitating to leave.
    "stay", "stays", "staying", "remain", "remains", "keep", "keeping",
    "retain", "hold on to", "hands off", "not for sale", "quit", "quits",
    "leave", "leaves", "leaving", "commit", "commits", "extension",
    "extend", "renew", "renewal", "new deal", "tie down", "fend off",
)

#: Money, in any of the three currencies the feeds use. A headline with a fee
#: in it is about a transfer even when it uses none of the words above.
_MONEY = re.compile(r"[£€$]\s?\d|\d+\s?(?:m|million|bn)\b", re.I)

#: Built once. Word boundaries on both sides, except for the multi-word
#: phrases where a plain substring is what we want.
_TERMS = re.compile(
    "|".join(
        re.escape(t) if " " in t else rf"\b{re.escape(t)}\b"
        for t in TRANSFER_TERMS
    ),
    re.I,
)

#: Sections of a feed that are never transfer news whatever words they use.
#: A match report is full of "moves" and "targets". This is the one place the
#: filter is allowed to be decisive, and it matches on URL path rather than
#: text so it cannot be fooled by prose.
_DEAD_PATHS = re.compile(
    r"/(?:live|scores|fixtures|results|tables|standings|video|videos|"
    r"podcast|podcasts|gallery|quiz|predictions?)(?:/|$)", re.I
)


#: Competitions and markers that put an article outside this site's scope.
#:
#: TransferIntel tracks the men's Premier League window. During a major
#: women's tournament the general football feeds fill with women's transfer
#: news, which is real news, correctly matches every transfer keyword, and is
#: not what this site is about. Left in, it drowns the dataset in players no
#: tracked club is signing.
#:
#: Matched on whole words against title and summary. "Women" and "womens"
#: only: no bare "her" or "she", which appear constantly in men's coverage
#: quoting a partner, a chairwoman or a journalist.
OUT_OF_SCOPE = re.compile(
    r"\b(?:women'?s?|ladies|wsl|nwsl|lionesses|matildas|"
    r"women'?s\s+(?:super\s+league|championship|world\s+cup|euros?))\b",
    re.I,
)

#: URL segments that mark a section as out of scope regardless of wording.
_OUT_OF_SCOPE_PATHS = re.compile(
    r"/(?:womens?|womens-football|wsl|nwsl)(?:/|-|$)", re.I
)


def in_scope(article: Article) -> bool:
    """False when the article is about a competition this site does not cover.

    Deliberately narrow. The cost of excluding something wrongly is a missed
    deal, so this only fires on explicit markers: a competition name, a squad
    nickname, or a section of a site given over to it.
    """
    if _OUT_OF_SCOPE_PATHS.search(article.url or ""):
        return False
    return not OUT_OF_SCOPE.search(article.text or "")


@dataclass
class FilterStats:
    """What the gate did, so the saving and the risk are both auditable."""

    seen: int = 0
    kept: int = 0
    dropped_no_signal: int = 0
    dropped_dead_path: int = 0
    dropped_seen_before: int = 0
    dropped_out_of_scope: int = 0
    samples: list[str] = field(default_factory=list)

    @property
    def cut_rate(self) -> float:
        return 0.0 if not self.seen else 1 - (self.kept / self.seen)

    def summary(self) -> str:
        return (
            f"{self.kept} of {self.seen} articles kept "
            f"({self.cut_rate:.0%} filtered): "
            f"{self.dropped_no_signal} without a transfer signal, "
            f"{self.dropped_dead_path} from non-news sections, "
            f"{self.dropped_out_of_scope} outside this site's scope, "
            f"{self.dropped_seen_before} already extracted on a previous run"
        )


def looks_like_transfer_news(article: Article) -> bool:
    """Could this plausibly be about a player changing clubs?"""
    if _DEAD_PATHS.search(article.url or ""):
        return False
    text = article.text or ""
    return bool(_TERMS.search(text) or _MONEY.search(text))


def prefilter(
    articles: list[Article],
    seen_urls: set[str] | None = None,
    stats: FilterStats | None = None,
    sample_limit: int = 12,
) -> list[Article]:
    """Everything worth spending a token on.

    `seen_urls` are articles already extracted on a previous run. The ingest
    window is 36 hours and the job runs every 24, so roughly a third of every
    day's articles are ones yesterday already paid to read. Skipping them
    costs nothing in coverage: the evidence they produced is already attached
    to the deal.
    """
    stats = stats or FilterStats()
    seen = seen_urls or set()
    keep: list[Article] = []

    for article in articles:
        stats.seen += 1
        if article.url in seen:
            stats.dropped_seen_before += 1
            continue
        if _DEAD_PATHS.search(article.url or ""):
            stats.dropped_dead_path += 1
            continue
        if not in_scope(article):
            stats.dropped_out_of_scope += 1
            if len(stats.samples) < sample_limit:
                stats.samples.append(
                    "[out of scope] " + (article.title or article.url)[:96])
            continue
        if not looks_like_transfer_news(article):
            stats.dropped_no_signal += 1
            if len(stats.samples) < sample_limit:
                # Recorded so a human can spot the day this starts eating
                # real stories. A filter you cannot audit is a filter you
                # cannot trust.
                stats.samples.append((article.title or article.url)[:110])
            continue
        keep.append(article)

    stats.kept = len(keep)
    return keep


# ------------------------------------------------------------ seen cache


def load_seen(path, keep_days: int = 7) -> tuple[set[str], dict]:
    """URLs already extracted, and the pruned cache to write back.

    The ingest window is 36 hours and the job runs every 24, so about a third
    of each day's articles were already read and paid for yesterday. Their
    evidence is attached to the deals already; reading them again buys
    nothing.

    Entries are pruned after `keep_days` so the file cannot grow without
    limit, and so an article that resurfaces after a fortnight is treated as
    new rather than silently ignored.
    """
    import json
    from datetime import date, timedelta

    if not path or not path.exists():
        return set(), {}
    try:
        cache = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return set(), {}

    cutoff = (date.today() - timedelta(days=keep_days)).isoformat()
    fresh = {url: seen for url, seen in cache.items() if seen >= cutoff}
    return set(fresh), fresh


def save_seen(path, cache: dict, urls, today) -> None:
    import json

    stamp = today.isoformat()
    for url in urls:
        cache[url] = stamp
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, indent=0, sort_keys=True),
                    encoding="utf-8")
