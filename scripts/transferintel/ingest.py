"""Phase 1: get the day's football news into a clean list of Articles.

Stdlib only, on purpose. This runs on a GitHub runner every morning and the
cheapest way to keep it running for years is to depend on nothing that can
break. RSS is boring, stable and free.

Everything here fails soft. One dead feed must never take down the run: the
worst acceptable outcome is a quiet day, never a broken site.
"""

from __future__ import annotations

import gzip
import re
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen

from .entities import fold
from .models import Article
from .sources import FEEDS, outlet_for, tier_for

USER_AGENT = "TransferIntel/1.0 (+https://github.com/) editorial-bot"
TIMEOUT = 20

#: Query parameters that identify a share, not a page.
_TRACKING = re.compile(r"^(utm_|fbclid|gclid|ito|CMP|ns_|at_)", re.I)

#: Common English stopwords plus the words every transfer headline contains.
#: Removing them makes near-duplicate titles collide on the same hash.
_TITLE_NOISE = {
    "the", "a", "an", "of", "to", "for", "in", "on", "at", "and", "as", "is",
    "are", "be", "with", "from", "by", "his", "her", "it", "that", "this",
    "transfer", "news", "latest", "update", "updates", "live", "report",
    "reports", "reportedly", "exclusive", "breaking", "deal", "move",
}


# ---------------------------------------------------------------- fetching


def canonical_url(url: str) -> str:
    """Strip tracking noise so the same article from two feeds dedupes."""
    parts = urlsplit(url.strip())
    query = "&".join(
        q for q in parts.query.split("&")
        if q and not _TRACKING.match(q.split("=")[0])
    )
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme, parts.netloc.lower(), path, query, ""))


def fetch(url: str, timeout: int = TIMEOUT) -> bytes | None:
    """One feed. Returns None on any failure, and says nothing about it."""
    req = Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/rss+xml, application/xml, text/xml, */*",
        "Accept-Encoding": "gzip",
    })
    try:
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            if resp.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            return raw
    except (HTTPError, URLError, TimeoutError, OSError, EOFError):
        return None


# ---------------------------------------------------------------- parsing


def _text(node, *paths: str) -> str:
    for p in paths:
        found = node.find(p)
        if found is not None and (found.text or "").strip():
            return found.text.strip()
        if found is not None and found.get("href"):
            return found.get("href").strip()
    return ""


def _strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    text = (text.replace("&amp;", "&").replace("&#39;", "'")
                .replace("&quot;", '"').replace("&nbsp;", " ")
                .replace("&lt;", "<").replace("&gt;", ">"))
    return re.sub(r"\s+", " ", text).strip()


def _parse_date(raw: str) -> date | None:
    raw = raw.strip()
    if not raw:
        return None
    try:  # RFC 822, what RSS uses
        return parsedate_to_datetime(raw).astimezone(timezone.utc).date()
    except (TypeError, ValueError, IndexError):
        pass
    try:  # ISO 8601, what Atom uses
        return datetime.fromisoformat(
            raw.replace("Z", "+00:00")
        ).astimezone(timezone.utc).date()
    except ValueError:
        return None


ATOM = "{http://www.w3.org/2005/Atom}"


def parse_feed(raw: bytes, today: date) -> list[Article]:
    """Handles RSS 2.0 and Atom. Anything else is silently skipped."""
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return []

    items = root.findall(".//item") or root.findall(f".//{ATOM}entry")
    out: list[Article] = []
    for item in items:
        link = _text(item, "link", f"{ATOM}link")
        title = _strip_html(_text(item, "title", f"{ATOM}title"))
        if not link or not title:
            continue
        summary = _strip_html(_text(
            item, "description", "summary", f"{ATOM}summary", f"{ATOM}content"
        ))[:600]
        published = _parse_date(_text(
            item, "pubDate", "published", f"{ATOM}published", f"{ATOM}updated",
            "{http://purl.org/dc/elements/1.1/}date",
        )) or today

        url = canonical_url(link)
        try:
            out.append(Article(
                url=url, title=title, summary=summary,
                published=min(published, today),
                outlet=outlet_for(url), tier=tier_for(url),
            ))
        except ValueError:
            continue
    return out


# ---------------------------------------------------------------- filtering


def title_key(title: str) -> str:
    """Near-duplicate key. Two outlets rewriting the same wire copy collide."""
    words = sorted(set(fold(title).split()) - _TITLE_NOISE)
    return " ".join(words)


def dedupe(articles: list[Article]) -> list[Article]:
    """Keep the best-tier version of each story, then the earliest."""
    best: dict[str, Article] = {}
    for a in sorted(articles, key=lambda x: (x.tier, x.published)):
        for key in (a.url, title_key(a.title)):
            if key in best:
                break
        else:
            best[a.url] = a
            best[title_key(a.title)] = a
    seen, out = set(), []
    for a in best.values():
        if a.url not in seen:
            seen.add(a.url)
            out.append(a)
    return out


def mentions_tracked_club(article: Article, club_terms: set[str]) -> bool:
    text = fold(article.text)
    return any(re.search(rf"\b{re.escape(t)}\b", text) for t in club_terms)


def club_terms(clubs: list[str]) -> set[str]:
    """Canonical names plus every alias that points at one, all folded."""
    from .entities import CLUB_ALIASES

    terms = {fold(c) for c in clubs}
    terms |= {fold(a) for a, canon in CLUB_ALIASES.items() if canon in clubs}
    # "United", "City" and "Forest" appear in ordinary prose constantly, and a
    # canonical club name never needs them: Manchester United is already in
    # the set. Dropping them costs nothing and saves a lot of model spend.
    generic = {"united", "city", "forest", "sporting", "inter", "atletico",
               "wolves", "spurs"}
    return {t for t in terms if " " in t or (len(t) > 3 and t not in generic)}


def collect(
    today: date,
    window_hours: int = 36,
    clubs: list[str] | None = None,
    feeds: list[tuple[str, str]] | None = None,
    fetcher=fetch,
) -> tuple[list[Article], dict[str, int]]:
    """Phase 1 end to end. Returns the articles and a stats dict for logging."""
    feeds = feeds if feeds is not None else FEEDS
    cutoff = today - timedelta(days=max(1, round(window_hours / 24)))

    raw_articles: list[Article] = []
    stats = {"feeds": len(feeds), "feeds_failed": 0, "raw": 0}
    for url, _name in feeds:
        body = fetcher(url)
        if body is None:
            stats["feeds_failed"] += 1
            continue
        raw_articles.extend(parse_feed(body, today))

    stats["raw"] = len(raw_articles)
    fresh = [a for a in raw_articles if cutoff <= a.published <= today]
    stats["in_window"] = len(fresh)

    unique = dedupe(fresh)
    stats["deduped"] = len(unique)

    if clubs:
        terms = club_terms(clubs)
        unique = [a for a in unique if mentions_tracked_club(a, terms)]
    stats["relevant"] = len(unique)

    return unique, stats
