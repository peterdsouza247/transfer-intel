#!/usr/bin/env python3
"""Draft `manual/claims.json` from fetched articles, for a human to correct.

    python scripts/draft_claims.py --articles build/articles.json \
        --data data.json --out manual/claims.json

Phase 2 normally asks a model to read each article and report what it claimed.
Without an API key that job falls to a person, and writing thirty claims by
hand from scratch is enough work that it does not get done, which is how a
site goes a week without a new transfer.

Most of that work is not judgment. Finding "Aston Villa" and "Chelsea" in a
headline is string matching against a known club list. Spotting "£117m" is a
regex. Deciding whether "completes his move" means completed is the same
marker table the scorer already uses. All of that can be drafted.

What cannot be drafted is the part that matters: whether the article is
actually about a transfer, which of the two clubs is buying, whether the
capitalised words are a player or a stadium, and whether the reported stage
is what the sentence really says. So every drafted claim carries `_draft:
true` and a `_review` note, and **`run_ingest.py --claims` refuses a file that
still contains any of them**. The draft cannot be used without being read.

This turns writing claims into correcting them. In testing that is the
difference between fifteen minutes and three.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from transferintel.entities import fold  # noqa: E402
from transferintel.markers import collapse_reason, completion_marker  # noqa: E402
from transferintel.models import Article, CollapseReason, Stage  # noqa: E402

#: Stage keywords, strongest first. The first hit wins, which is why
#: `completed` sits above `agreed`: "the deal was agreed and has now been
#: completed" is a completion.
STAGE_HINTS: tuple[tuple[Stage, str], ...] = (
    (Stage.collapsed, r"\b(?:collaps\w+|off\b|dead\b|fail\w+ (?:a |the |his )?"
                      r"medical|called off|broke down|pulled out)"),
    (Stage.medical, r"\bmedical\b"),
    (Stage.agreed, r"\b(?:fee agreed|deal agreed|agreed a (?:fee|deal)|"
                   r"agreement reached|terms agreed|release clause triggered)"),
    (Stage.talks, r"\b(?:talks|negotiat\w+|in discussions|bid|bids|offer|"
                  r"offers|approach|submitted)"),
    (Stage.interest, r"\b(?:interest\w*|linked|target\w*|monitor\w*|eye\w*|"
                     r"want\w*|pursu\w*|chase|chasing|shortlist\w*|keen)"),
)

#: Fees, in the three currencies the feeds use.
_FEE = re.compile(
    r"([£€$])\s?(\d+(?:\.\d+)?)\s?(m|million|bn)?"
    r"|(\d+(?:\.\d+)?)\s?(?:m|million)\s?(?:pounds|euros|dollars)?",
    re.I,
)
_CURRENCY = {"£": "GBP", "€": "EUR", "$": "USD"}

#: Words that look like names but are not people.
_NOT_PEOPLE = {
    "premier", "league", "cup", "world", "united", "city", "town", "rovers",
    "wanderers", "albion", "athletic", "county", "forest", "villa", "palace",
    "hotspur", "wednesday", "monday", "tuesday", "thursday", "friday",
    "saturday", "sunday", "january", "june", "july", "august", "september",
    "transfer", "deal", "done", "here", "live", "sources", "exclusive",
    "report", "reports", "official", "confirmed", "breaking", "update",
}


def guess_stage(text: str) -> Stage:
    low = (text or "").lower()
    if completion_marker(text or ""):
        return Stage.completed
    for stage, pattern in STAGE_HINTS:
        if re.search(pattern, low):
            return stage
    return Stage.none


def guess_fee(text: str) -> tuple[float | None, str | None]:
    """The largest figure mentioned, which is usually the headline fee."""
    best, currency = None, None
    for match in _FEE.finditer(text or ""):
        symbol, value, unit, bare = (
            match.group(1), match.group(2), match.group(3), match.group(4)
        )
        raw = value or bare
        if raw is None:
            continue
        try:
            amount = float(raw)
        except ValueError:
            continue
        if unit and unit.lower() == "bn":
            amount *= 1000
        # A two-figure number with no unit is more often an age or a minute
        # than a fee, so require a currency symbol for the bare form.
        if bare and not symbol:
            continue
        if best is None or amount > best:
            best, currency = amount, _CURRENCY.get(symbol or "", "GBP")
    return best, currency


def find_clubs(text: str, known: list[str]) -> list[str]:
    """Known clubs named in the text, in the order they appear.

    Order is the whole reason this is worth doing: "Rogers joins Chelsea from
    Aston Villa" and "Aston Villa sell Rogers to Chelsea" name the same two
    clubs, and the draft has to guess which is buying. It guesses, marks the
    guess, and leaves it to be checked.
    """
    hits: list[tuple[int, str]] = []
    folded = fold(text or "").lower()
    for club in known:
        needle = fold(club).lower()
        at = folded.find(needle)
        if at != -1:
            hits.append((at, club))
    hits.sort()
    seen, ordered = set(), []
    for _, club in hits:
        if club not in seen:
            seen.add(club)
            ordered.append(club)
    return ordered


def guess_player(text: str, clubs: list[str]) -> str | None:
    """Capitalised runs that are not clubs and not sentence openers."""
    club_words = {w.lower() for c in clubs for w in c.split()}
    candidates = re.findall(
        r"\b([A-Z][\w'\u00C0-\u024F-]+(?:\s+[A-Z][\w'\u00C0-\u024F-]+)+)", text or ""
    )
    for phrase in candidates:
        words = phrase.split()
        low = {w.lower() for w in words}
        if low & club_words or low & _NOT_PEOPLE:
            continue
        if len(words) > 3:
            continue
        return phrase
    return None


def draft(article: Article, known: list[str]) -> dict:
    text = article.text or ""
    clubs = find_clubs(text, known)
    stage = guess_stage(text)
    fee, currency = guess_fee(text)
    player = guess_player(text, clubs)

    review: list[str] = []
    if not player:
        review.append("no player found, fill in `player`")
    if len(clubs) < 2:
        review.append(
            f"found {len(clubs)} known club(s), both from_club and to_club "
            "are needed"
        )
    else:
        review.append(
            f"clubs appear in this order: {clubs[0]} then {clubs[1]}. "
            "Confirm which is buying and swap if wrong"
        )
    if stage is Stage.none:
        review.append("no stage keyword found, set `reported_stage` by hand")
    if stage is Stage.completed:
        review.append(
            "reads as completed. It needs a tier 1 source and a completion "
            "marker to reach `done`, and will otherwise stop at `confirmed`"
        )
    if stage is Stage.collapsed:
        reason = collapse_reason(text)
        if reason is not CollapseReason.unknown:
            review.append(f"collapse looks like: {reason.value}")
    if fee is None:
        review.append("no fee found, leave null if none was reported")

    return {
        "_draft": True,
        "_review": review,
        "_title": (article.title or "")[:120],
        "is_transfer_claim": True,
        "player": player,
        "from_club": clubs[1] if len(clubs) > 1 else None,
        "to_club": clubs[0] if clubs else None,
        "reported_stage": stage.value,
        "fee_amount": fee,
        "fee_currency": currency,
        "article_url": article.url,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--articles", type=Path, default=Path("build/articles.json"))
    ap.add_argument("--data", type=Path, default=Path("data.json"))
    ap.add_argument("--out", type=Path, default=Path("manual/claims.json"))
    ap.add_argument("--out-articles", type=Path,
                    default=Path("manual/articles.json"),
                    help="the articles these claims refer to, written as a "
                         "matched pair so a claim cannot reference an article "
                         "the next run does not have")
    ap.add_argument("--min-stage", default="interest",
                    help="drop drafts below this stage, default interest")
    args = ap.parse_args()

    if not args.articles.exists():
        print(f"\n{args.articles} does not exist. Run run_ingest.py first.",
              file=sys.stderr)
        return 2

    raw = json.loads(args.data.read_text(encoding="utf-8"))
    known = sorted(
        {*raw.get("clubs", {}),
         *(d["from"] for d in raw["deals"]),
         *(d["to"] for d in raw["deals"])},
        key=len, reverse=True,   # longest first: "Man Utd" before "Man"
    )
    articles = [Article(**a) for a in
                json.loads(args.articles.read_text(encoding="utf-8"))]

    order = list(Stage)
    floor = order.index(Stage(args.min_stage))
    drafts = []
    skipped = 0
    for article in articles:
        item = draft(article, known)
        if order.index(Stage(item["reported_stage"])) < floor:
            skipped += 1
            continue
        drafts.append(item)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(drafts, indent=1, ensure_ascii=False),
                        encoding="utf-8")

    # The articles these claims point at, written alongside them.
    #
    # This used to be left to the operator, and it was the single most
    # confusing step in the manual routine: `run_ingest --claims` needs the
    # articles to attach a source, date and tier to each claim, so a claims
    # file whose URLs are not in the article set produces nothing at all. The
    # two files are one unit and are now written as one.
    kept = {d["article_url"] for d in drafts}
    pair = [a.model_dump(mode="json") for a in articles if a.url in kept]
    args.out_articles.write_text(
        json.dumps(pair, indent=1, ensure_ascii=False), encoding="utf-8")

    complete = sum(
        1 for d in drafts
        if d["player"] and d["from_club"] and d["to_club"]
    )
    print(f"{len(drafts)} draft claim(s) written to {args.out}, "
          f"and the {len(pair)} article(s) they cite to {args.out_articles} "
          f"({skipped} article(s) below --min-stage).")
    print("Keep the two in step: if you delete a claim, its article can stay, "
          "but a claim\nwhose article is missing resolves to nothing and is "
          "reported as unresolved.")
    print(f"{complete} look complete; all of them still need reading.")
    print("\nEvery entry has `_draft: true` and a `_review` list. run_ingest "
          "will refuse\nthe file until you have deleted those two keys from "
          "each claim you keep,\nand deleted the claims you do not want.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
