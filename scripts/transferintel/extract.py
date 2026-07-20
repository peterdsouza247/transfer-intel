"""Phase 2: turn headlines into resolved evidence.

Two halves, and the split is the whole point.

The model half is mechanical and cheap: read a headline, say whether it is a
transfer claim and what it asserted. Haiku is more than enough, it batches
twenty at a time, and it is easy to evaluate because the answers are almost
always obviously right or obviously wrong.

The code half is where all the judgment lives: matching a name to a deal we
already track, canonicalising club names, converting currency, and deciding
that an unrecognised transfer becomes a review item rather than a new row on
a live site. None of that is delegated.
"""

from __future__ import annotations

import json
import os
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

from .markers import completion_marker, is_official
from .entities import canonical_club, deal_id, fold, resolve_deal
from .models import Article, Candidate, Claim, Deal, Evidence, Stage
from .sources import to_gbp_millions

DEFAULT_MODEL = os.environ.get("TI_EXTRACT_MODEL", "claude-haiku-4-5-20251001")
BATCH_SIZE = 20

EXTRACT_SYSTEM = """\
You read football news headlines and report what each one claims about a \
player transfer. You are an extractor, not an analyst.

For each numbered item you receive, return one object with these fields:

  n                 the item number you were given
  is_transfer_claim true only if the item is about a specific named player \
moving, or being reported as moving, between two clubs
  player            the player's name exactly as written, or null
  from_club         the selling club as written, or null if not stated
  to_club           the buying club as written, or null if not stated
  reported_stage    one of: interest, talks, agreed, medical, completed, \
collapsed, none
  fee_amount        the number only, in millions, or null
  fee_currency      GBP, EUR or USD, or null

Stage definitions, applied strictly to what the item actually says:

  interest   a club likes, wants, is monitoring, is eyeing, or has been \
offered the player
  talks      contact, negotiations, bids, or an opening offer, nothing settled
  agreed     a fee agreed between clubs, or personal terms agreed
  medical    the player is travelling for or undergoing a medical
  completed  the transfer is confirmed, announced, signed, or unveiled
  collapsed  the move is off, called off, dead, or the player is staying
  none       the item is about a transfer generally but claims no stage

Hard rules:

- Report only what the item says. Never use anything you know about these \
clubs or players from elsewhere.
- If the item is a rumour round-up covering several players, set \
is_transfer_claim to false. Round-ups are noise.
- If the item is about contract renewals, loans returning, managers, injuries, \
match reports or league tables, set is_transfer_claim to false.
- Speculation counts as a claim, at whatever stage it claims. "Arsenal eye \
Hartley" is interest. Your job is to record it, not to judge it.
- Never guess a club that is not named. null is a correct answer.
- Never convert a currency. Report the number and symbol you saw.

Return a JSON array of objects, one per item, and nothing else. No prose, no \
markdown fences.\
"""


@dataclass
class ExtractionStats:
    articles: int = 0
    batches: int = 0
    parse_failures: int = 0
    claims: int = 0
    usable: int = 0
    resolved: int = 0
    candidates: int = 0
    dropped: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- the model


def build_batch_prompt(articles: list[Article]) -> str:
    lines = []
    for i, a in enumerate(articles, 1):
        body = a.text[:400]
        lines.append(f"{i}. {body}")
    return "\n".join(lines) + "\n\nReturn the JSON array."


def parse_batch_response(text: str, batch: list[Article]) -> list[Claim]:
    """Tolerate fences and preamble. Refuse to guess at anything else."""
    cleaned = re.sub(r"^```(?:json)?|```$", "", text.strip(),
                     flags=re.M).strip()
    start, end = cleaned.find("["), cleaned.rfind("]")
    if start == -1 or end == -1:
        raise ValueError("no JSON array in response")
    rows = json.loads(cleaned[start:end + 1])

    claims: list[Claim] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            n = int(row.get("n", 0))
        except (TypeError, ValueError):
            continue
        if not 1 <= n <= len(batch):
            continue
        payload = {k: v for k, v in row.items() if k != "n"}
        try:
            claim = Claim(**payload)
        except ValueError:
            continue
        claim.article_url = batch[n - 1].url
        claims.append(claim)
    return claims


class ClaimExtractor:
    """Batched extraction with a budget, retries and an offline mode."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str = DEFAULT_MODEL,
        batch_size: int = BATCH_SIZE,
        max_batches: int = 12,
        max_tokens: int = 3000,
    ) -> None:
        self.model = model
        self.batch_size = batch_size
        self.max_batches = max_batches
        self.max_tokens = max_tokens
        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self.dry = not key
        self._client = None
        if key:
            from anthropic import Anthropic

            self._client = Anthropic(api_key=key)

    def _call(self, prompt: str) -> str:
        resp = self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=[{
                "type": "text",
                "text": EXTRACT_SYSTEM,
                # Identical on every batch of every run.
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(b.text for b in resp.content if b.type == "text")

    def run(self, articles: list[Article], stats: ExtractionStats) -> list[Claim]:
        stats.articles = len(articles)
        if self.dry or not articles:
            return []

        claims: list[Claim] = []
        batches = [
            articles[i:i + self.batch_size]
            for i in range(0, len(articles), self.batch_size)
        ][:self.max_batches]

        for batch in batches:
            stats.batches += 1
            prompt = build_batch_prompt(batch)
            for attempt in (1, 2):
                try:
                    claims.extend(parse_batch_response(self._call(prompt), batch))
                    break
                except Exception as exc:
                    if attempt == 2:
                        stats.parse_failures += 1
                        stats.dropped.append(
                            f"batch of {len(batch)} failed: {exc}"
                        )
        stats.claims = len(claims)
        return claims


# ------------------------------------------------------------ resolution


def _looks_like_a_person(name: str | None) -> bool:
    """Cheap sanity gate. Catches 'Premier League' landing in the player field."""
    if not name or len(name) < 3:
        return False
    folded = fold(name)
    if folded in {"premier league", "champions league", "the club", "unknown"}:
        return False
    return bool(re.match(r"^[a-z][a-z' \-]+$", folded))


def resolve(
    claims: list[Claim],
    articles: list[Article],
    deals: list[Deal],
    known_clubs: list[str],
    stats: ExtractionStats,
) -> tuple[dict[str, list[dict]], list[Candidate], list[dict]]:
    """Attach every usable claim to a deal, a candidate, or the reject pile."""
    by_url = {a.url: a for a in articles}
    evidence: dict[str, list[dict]] = defaultdict(list)
    candidates: dict[str, Candidate] = {}
    unresolved: list[dict] = []

    for claim in claims:
        article = by_url.get(claim.article_url)
        if article is None:
            continue
        if not claim.usable:
            continue
        stats.usable += 1

        if not _looks_like_a_person(claim.player):
            unresolved.append({
                "reason": "player field does not look like a person",
                "player": claim.player, "url": claim.article_url,
            })
            continue

        to_club = canonical_club(claim.to_club, known_clubs)
        from_club = canonical_club(claim.from_club, known_clubs)

        item = Evidence(
            url=article.url,
            source=article.outlet,
            tier=article.tier,
            date=article.published,
            claim=claim.reported_stage,
            fee_gbp_m=to_gbp_millions(claim.fee_amount, claim.fee_currency),
            # TI-001. The marker is read off the article's own words here, at
            # the point the evidence is minted, and never recomputed later.
            # A completion that cannot name the sentence proving it is not a
            # completion, and the alternative was a pipeline that promoted
            # deals to `done` because nothing had contradicted them.
            marker=completion_marker(article.text, article.outlet, article.url),
            official=is_official(article.url),
        )

        match = resolve_deal(claim.player, to_club, from_club, deals)
        if match is not None:
            stats.resolved += 1
            evidence[match.id].append(item.model_dump(mode="json"))
            continue

        # Not a deal we track. Only worth reviewing if both ends are named,
        # otherwise "Arsenal want a striker" becomes a row in the queue.
        if not (to_club and from_club):
            unresolved.append({
                "reason": "unknown deal with only one club named",
                "player": claim.player, "to": to_club, "from": from_club,
                "url": article.url,
            })
            continue

        cid = deal_id(claim.player, from_club, to_club)
        cand = candidates.get(cid)
        if cand is None:
            cand = Candidate(
                suggested_id=cid, player=claim.player,
                from_club=from_club, to_club=to_club,
                stage=claim.reported_stage, fee_gbp_m=item.fee_gbp_m,
                best_tier=article.tier, evidence=[item],
            )
            candidates[cid] = cand
        else:
            cand.mentions += 1
            cand.best_tier = min(cand.best_tier, article.tier)
            cand.evidence.append(item)
            if cand.fee_gbp_m is None:
                cand.fee_gbp_m = item.fee_gbp_m

    stats.candidates = len(candidates)
    return dict(evidence), list(candidates.values()), unresolved


def rank_candidates(
    candidates: list[Candidate], min_tier: int = 2, min_mentions: int = 1
) -> list[Candidate]:
    """Surface the ones worth a human's attention, best first.

    A single tier 3 mention of a transfer we do not track is exactly the kind
    of thing that should die quietly in the log rather than reach a review
    queue you stop reading.
    """
    worth_it = [
        c for c in candidates
        if c.best_tier <= min_tier and c.mentions >= min_mentions
    ]
    order = {s: i for i, s in enumerate(
        [Stage.none, Stage.interest, Stage.talks, Stage.agreed,
         Stage.medical, Stage.completed, Stage.collapsed]
    )}
    return sorted(
        worth_it,
        key=lambda c: (c.best_tier, -order.get(c.stage, 0), -c.mentions),
    )
