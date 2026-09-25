from datetime import date
from types import SimpleNamespace

from transferintel.source_stats import (
    canonical_attributions,
    source_records,
)


def evidence(source, claim, when):
    return SimpleNamespace(source=source, claim=claim, date=date.fromisoformat(when))


def deal(identifier, status, rows, completed=None, verified=None):
    return SimpleNamespace(
        id=identifier,
        status=status,
        evidence=rows,
        completed_date=date.fromisoformat(completed) if completed else None,
        last_verified_at=date.fromisoformat(verified) if verified else None,
    )


def test_composite_attributions_split_writers_from_publications():
    assert canonical_attributions("Football365 / Romano") == [
        ("publication", "Football365"),
        ("journalist", "Fabrizio Romano"),
    ]
    assert canonical_attributions("Romano + Ornstein") == [
        ("journalist", "Fabrizio Romano"),
        ("journalist", "David Ornstein"),
    ]


def test_repeat_articles_count_as_one_call_and_pending_is_not_a_miss():
    deals = [
        deal("hit", "done", [
            evidence("Sky Sports", "talks", "2026-07-01"),
            evidence("Sky Sports", "agreed", "2026-07-02"),
        ], completed="2026-07-03"),
        deal("pending", "confirmed", [
            evidence("Sky Sports", "talks", "2026-07-04"),
        ]),
    ]
    sky = source_records(deals)["publication"][0]
    assert (sky.hits, sky.misses, sky.unresolved, sky.calls) == (1, 0, 1, 2)
    assert sky.hit_rate == 100
    assert sky.credibility == 60


def test_post_resolution_followups_and_collapse_reports_are_not_tips():
    deals = [
        deal("done", "done", [
            evidence("Sky Sports", "talks", "2026-07-01"),
            evidence("Daily Mail", "completed", "2026-07-04"),
        ], completed="2026-07-03"),
        deal("off", "collapsed", [
            evidence("Daily Mail", "interest", "2026-07-01"),
            evidence("Sky Sports", "collapsed", "2026-07-05"),
        ], verified="2026-07-05"),
    ]
    rows = {row.name: row for row in source_records(deals)["publication"]}
    assert (rows["Sky Sports"].hits, rows["Sky Sports"].misses) == (1, 0)
    assert (rows["Daily Mail"].hits, rows["Daily Mail"].misses) == (0, 1)


def test_completion_day_reports_are_not_retroactive_predictions():
    deals = [
        deal("done", "done", [
            evidence("The Guardian", "interest", "2026-08-12"),
            evidence("Sky Sports", "completed", "2026-09-01"),
        ], completed="2026-09-01"),
    ]
    rows = {row.name: row for row in source_records(deals)["publication"]}
    assert rows["The Guardian"].hits == 1
    assert "Sky Sports" not in rows


def test_smoothed_score_rewards_evidence_not_one_lucky_call():
    many = [
        deal(f"hit-{i}", "done", [evidence("Sky Sports", "talks", "2026-07-01")],
             completed="2026-07-02")
        for i in range(9)
    ]
    many.append(deal("miss", "collapsed", [
        evidence("Sky Sports", "talks", "2026-07-01"),
        evidence("BBC Sport", "talks", "2026-07-01"),
    ], verified="2026-07-03"))
    rows = {row.name: row for row in source_records(many)["publication"]}
    assert rows["Sky Sports"].credibility > rows["BBC Sport"].credibility
