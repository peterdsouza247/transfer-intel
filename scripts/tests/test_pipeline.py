"""Phase 0, 1 and 2 rules. Run with: python -m pytest scripts/tests -q"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from migrate_data import (  # noqa: E402
    parse_display_date, parse_with_node, parse_with_python,
)
from transferintel.entities import (  # noqa: E402
    canonical_club, deal_id, fold, name_similarity, resolve_deal,
)
from transferintel.extract import (  # noqa: E402
    _looks_like_a_person, parse_batch_response,
)
from transferintel.ingest import (  # noqa: E402
    canonical_url, club_terms, collect, dedupe, parse_feed, title_key,
)
from transferintel.models import Article, Deal  # noqa: E402
from transferintel.sources import (  # noqa: E402
    domain_of, outlet_for, tier_for, to_gbp_millions,
)

FIX = ROOT.parent / "fixtures"
TODAY = date(2026, 7, 19)
CLUBS = ["Arsenal", "Newcastle", "Everton", "Brighton", "Benfica", "Ajax"]


def feed_fetcher(url: str) -> bytes:
    return (FIX / "feeds" / f"{url.split('//')[1]}.xml").read_bytes()


FEEDS = [("file://sky", "Sky"), ("file://guardian", "G"), ("file://f365", "F")]


def deal(**kw) -> Deal:
    base = dict(
        id="okafor-ajax-everton", p="Tunde Okafor", **{"from": "Ajax"},
        to="Everton", fee=18.0, age=24, pos="CM", status="talks",
        date="Jul 10", tier=2, src="Telegraph", cred=55,
    )
    base.update(kw)
    return Deal(**base)


# ------------------------------------------------------------ phase 0


def test_both_data_js_parsers_agree():
    src = (FIX / "data.js").read_text()
    assert parse_with_python(src) == parse_with_node(src)


def test_fallback_survives_an_apostrophe_in_a_double_quoted_string():
    src = """window.TRANSFER_DATA = {deals:[{p:'A', note:"they've gone", to:'B'}]}"""
    out = parse_with_python(src)
    assert out["deals"][0]["note"] == "they've gone"
    assert out["deals"][0]["to"] == "B"


def test_fallback_handles_comments_and_trailing_commas():
    src = """window.TRANSFER_DATA = {
      // a comment
      deals: [ {p:'A',}, ],  /* block */
    };"""
    assert parse_with_python(src)["deals"] == [{"p": "A"}]


def test_display_dates_never_land_in_the_future():
    assert parse_display_date("Jul 16", TODAY) == date(2026, 7, 16)
    assert parse_display_date("Dec 20", TODAY) == date(2025, 12, 20)
    assert parse_display_date("garbage", TODAY) == TODAY


def test_ids_are_stable_and_slugged():
    assert deal_id("Ellis Hartley", "Brighton", "Arsenal") == \
        "hartley-brighton-arsenal"
    assert deal_id("Kylian Mbappe", "Real Madrid", "Man City") == \
        "mbappe-real-madrid-man-city"


# ------------------------------------------------------------ phase 1


def test_tracking_params_are_stripped_but_real_ones_survive():
    # The host is lowercased and the trailing slash goes, but the URL is left
    # otherwise intact: these end up as clickable citations in the PR, so
    # rewriting them beyond recognition would be a bad trade for dedupe.
    assert canonical_url("https://WWW.Sky.com/a/?utm_source=rss&id=7") == \
        "https://www.sky.com/a?id=7"


def test_tier_and_outlet_come_from_the_domain():
    assert tier_for("https://www.skysports.com/x") == 1
    assert tier_for("https://www.thesun.co.uk/x") == 3
    assert tier_for("https://unknown-blog.xyz/x") == 3
    assert outlet_for("https://www.bbc.co.uk/sport") == "BBC Sport"
    assert domain_of("https://m.telegraph.co.uk/a") == "telegraph.co.uk"


def test_rss_and_atom_both_parse():
    rss = parse_feed(feed_fetcher("file://sky"), TODAY)
    atom = parse_feed(feed_fetcher("file://f365"), TODAY)
    assert len(rss) == 3 and len(atom) == 3
    assert rss[0].tier == 1 and atom[0].tier == 2
    assert "&#163;" not in rss[0].summary and "42m" in rss[0].summary


def test_a_broken_feed_never_breaks_the_run():
    assert parse_feed(b"<html>not a feed", TODAY) == []
    articles, stats = collect(
        TODAY, 36, CLUBS, [("file://nope", "x")], lambda u: None
    )
    assert articles == [] and stats["feeds_failed"] == 1


def test_the_same_story_from_two_outlets_dedupes_to_the_better_tier():
    articles = parse_feed(feed_fetcher("file://sky"), TODAY) + \
        parse_feed(feed_fetcher("file://guardian"), TODAY)
    moreno = [a for a in dedupe(articles) if "Moreno" in a.title]
    assert len(moreno) == 1
    assert moreno[0].tier == 1


def test_title_key_ignores_transfer_boilerplate():
    assert title_key("BREAKING: Hartley to Arsenal, latest transfer news") == \
        title_key("Hartley to Arsenal")


def test_single_word_club_aliases_are_too_greedy_to_use():
    terms = club_terms(["Manchester United", "Manchester City"])
    assert "united" not in terms and "city" not in terms
    assert "man utd" in terms


def test_only_articles_mentioning_a_tracked_club_survive():
    articles, stats = collect(TODAY, 36, CLUBS, FEEDS, feed_fetcher)
    titles = " ".join(a.title for a in articles)
    assert "fixtures" not in titles.lower()      # league table noise
    assert "Vidal" not in titles                 # Chelsea is not tracked here
    assert stats["relevant"] < stats["deduped"]


# ------------------------------------------------------------ phase 2


def test_model_output_survives_fences_preamble_and_bad_rows():
    batch = [Article(url="https://skysports.com/a", title="t", published=TODAY,
                     outlet="Sky Sports", tier=1)]
    raw = """Sure, here you go:
```json
[{"n":1,"is_transfer_claim":true,"player":"A","to_club":"Arsenal",
  "reported_stage":"talks"},
 {"n":99,"is_transfer_claim":true,"player":"Out of range"},
 {"n":1,"reported_stage":"not a real stage"},
 "junk"]
```"""
    claims = parse_batch_response(raw, batch)
    assert len(claims) == 1
    assert claims[0].player == "A"
    assert claims[0].article_url == "https://skysports.com/a"


def test_unparseable_output_raises_rather_than_guessing():
    with pytest.raises(ValueError):
        parse_batch_response("I could not do that", [])


@pytest.mark.parametrize("alias,expected", [
    ("Man Utd", "Manchester United"),
    ("Spurs", "Tottenham"),
    ("Newcastle United", "Newcastle"),
    ("Sporting Lisbon", "Sporting CP"),
    ("Brighton & Hove Albion", "Brighton"),
    ("FC Barcelona", "Barcelona"),
])
def test_club_aliases_resolve(alias, expected):
    assert canonical_club(alias, CLUBS + ["Tottenham", "Barcelona"]) == expected


def test_accents_do_not_break_name_matching():
    assert name_similarity("Odegaard", "\u00d8degaard") > 0.9
    assert fold("Nu\u00f1ez") == "nunez"


def test_headlines_that_drop_the_first_name_still_match():
    assert resolve_deal("Okafor", "Everton", None, [deal()]) is not None


def test_a_rival_club_chasing_the_same_player_is_a_different_deal():
    """The bug this test exists for: 'Man Utd eye Everton target Okafor'
    must not become evidence on the Everton row."""
    assert resolve_deal("Tunde Okafor", "Manchester United", "Ajax",
                        [deal()]) is None


def test_a_claim_with_no_club_named_resolves_to_nothing():
    assert resolve_deal("Tunde Okafor", None, None, [deal()]) is None


def test_currency_is_converted_in_code_not_by_the_model():
    assert to_gbp_millions(70, "EUR") == 58.8
    assert to_gbp_millions(70, "GBP") == 70.0
    assert to_gbp_millions(None, "EUR") is None
    assert to_gbp_millions(70, "YEN") is None


def test_the_player_field_has_to_look_like_a_person():
    assert _looks_like_a_person("Ellis Hartley")
    assert _looks_like_a_person("N'Golo Kante")
    assert not _looks_like_a_person("Premier League")
    assert not _looks_like_a_person("")


def test_evidence_output_is_shaped_for_the_editorial_pass():
    """Phase 2's output must load straight into phase 3 with no translation."""
    from transferintel.models import Evidence

    payload = json.loads((FIX / "evidence.json").read_text())
    for items in payload.values():
        for item in items:
            Evidence(**item)
