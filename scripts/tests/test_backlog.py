"""Tests for the backlog work, TI-001 through TI-020.

Each test names the ticket it defends. The point is not coverage: it is that
the specific failures these tickets describe cannot come back without a red
build, and that someone reading the test knows which promise it protects.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from transferintel import digest as dg  # noqa: E402
from transferintel import site  # noqa: E402
from transferintel import validate  # noqa: E402
from transferintel.markers import (  # noqa: E402
    collapse_reason, completion_marker, contradicts_completion, is_official,
)
from transferintel.models import (  # noqa: E402
    CollapseReason, Deal, Evidence, Stage, Status,
)
from transferintel.scoring import (  # noqa: E402
    DEFAULT_CONFIG, DEFAULT_DECAY, DecayCurve, compute_cred, decide_status,
    score_deal,
)

TODAY = date(2026, 7, 20)


def ev(days_ago=0, tier=1, claim=Stage.talks, source="Sky Sports",
       marker=None, official=False, url=None):
    return Evidence(
        url=url or f"https://example.com/{source}/{days_ago}/{claim.value}",
        source=source, tier=tier, date=TODAY - timedelta(days=days_ago),
        claim=claim, marker=marker, official=official,
    )


def deal(**kw):
    base = dict(
        id="x-a-b", p="Player X", **{"from": "Club A"}, to="Club B",
        fee=40.0, age=24, pos="AM", status=Status.rumor,
        date="Jul 20", tier=1, src="Sky Sports", cred=50, note="", evidence=[],
    )
    base.update(kw)
    return Deal(**base)


# ============================================================ TI-001


def test_reported_completion_reaches_confirmed_not_done():
    """The defect in one test.

    A tier 1 source saying a deal is finished is worth a lot. It is not worth
    calling the transfer done, because that is the one claim a reader can
    check for themselves and be embarrassed by.
    """
    d = deal(status=Status.medical, evidence=[ev(0, 1, Stage.completed)])
    assert decide_status(d, TODAY).status is Status.confirmed


def test_completion_marker_promotes_to_done():
    d = deal(status=Status.medical,
             evidence=[ev(0, 1, Stage.completed, marker="has signed")])
    decision = decide_status(d, TODAY)
    assert decision.status is Status.done
    assert decision.marker == "has signed"
    assert decision.marker_source == "Sky Sports"


def test_silence_never_promotes():
    """The actual root cause: absence of contradiction read as confirmation."""
    d = deal(status=Status.agreed, evidence=[ev(9, 1, Stage.agreed)])
    assert decide_status(d, TODAY).status is Status.agreed


def test_tier_two_marker_cannot_complete():
    d = deal(status=Status.medical,
             evidence=[ev(0, 2, Stage.completed, source="Telegraph",
                          marker="has signed")])
    assert decide_status(d, TODAY).status is not Status.done


def test_confirmed_is_capped_at_ninety():
    d = deal(status=Status.confirmed, cred=90,
             evidence=[ev(0, 1, Stage.completed), ev(0, 1, Stage.completed,
                                                     source="BBC Sport")])
    assert compute_cred(d, TODAY).total <= 90


def test_hundred_is_reserved_for_done():
    d = deal(status=Status.confirmed, cred=100,
             evidence=[ev(0, 1, Stage.completed)])
    result = validate.check([d], [], ["Club B"], TODAY)
    assert any("reserved for completed" in m for m in result.hard)


def test_gate_rejects_done_without_a_marker():
    d = deal(status=Status.done, cred=100, evidence=[ev(0, 1, Stage.completed)])
    result = validate.check([d], [], ["Club B"], TODAY)
    assert any("no completion marker recorded" in m for m in result.hard)


def test_gate_rejects_done_on_tier_two():
    d = deal(status=Status.done, cred=100, tier=2,
             completion_marker="has signed", completed_date=TODAY,
             evidence=[ev(0, 1, Stage.completed, marker="has signed")])
    result = validate.check([d], [], ["Club B"], TODAY)
    assert any("completion requires tier 1" in m for m in result.hard)


def test_gate_accepts_a_properly_evidenced_completion():
    d = deal(status=Status.done, cred=100, tier=1,
             completion_marker="has signed", completed_date=TODAY,
             evidence=[ev(0, 1, Stage.completed, marker="has signed")])
    result = validate.check([d], [], ["Club B"], TODAY)
    assert result.passed, result.hard


def test_corrupting_a_record_fails_the_build():
    """TI-001 acceptance: deliberately corrupt one record, confirm it fails."""
    good = deal(status=Status.done, cred=100, tier=1,
                completion_marker="has signed", completed_date=TODAY,
                evidence=[ev(0, 1, Stage.completed, marker="has signed")])
    assert validate.check([good], [], ["Club B"], TODAY).passed

    corrupt = good.model_copy(update={"completion_marker": None})
    assert not validate.check([corrupt], [], ["Club B"], TODAY).passed


def test_running_twice_changes_no_status():
    """TI-001 acceptance: the regression test for the whole class of bug."""
    d = deal(status=Status.talks, cred=55, evidence=[ev(1, 1, Stage.talks)])
    first = [o for o in score_deal(d, TODAY) if o.field == "status"]
    assert not first
    for op in score_deal(d, TODAY):
        if op.op == "update":
            setattr(d, op.field, op.to)
    second = [o for o in score_deal(d, TODAY) if o.field == "status"]
    assert not second


# ------------------------------------------------------- marker detection


@pytest.mark.parametrize("text,expected", [
    ("Moreno completes Newcastle move", True),
    ("Player has signed a three-year deal", True),
    ("Smith seals move to Arsenal", True),
    ("Jones set to complete his move", False),
    ("Expected to announce the signing shortly", False),
    ("Announcement imminent", False),
    ("Personal terms being discussed", False),
])
def test_hedges_defeat_completion_phrases(text, expected):
    assert bool(completion_marker(text, "Sky Sports")) is expected


def test_here_we_go_is_scoped_to_romano():
    assert completion_marker("Here we go! Deal done", "Fabrizio Romano")
    assert not completion_marker("Here we go rumours swirl", "Football365")


def test_club_domains_are_official():
    assert is_official("https://www.manutd.com/en/news/detail/x")
    assert not is_official("https://www.football365.com/news/x")
    assert completion_marker("anything", "Club", "https://www.arsenal.com/n")


def test_contradiction_audit_catches_the_named_six():
    for note in ["announcement imminent", "all done bar the unveiling",
                 "Announcement expected this week", "then silence",
                 "personal terms being discussed",
                 "Confirmed (auto-updated 2026-07-18)"]:
        assert contradicts_completion(note), note


@pytest.mark.parametrize("text,reason", [
    ("He failed the medical on Tuesday", CollapseReason.failed_medical),
    ("Villa hijacked the agreed deal", CollapseReason.hijacked),
    ("The clubs were too far apart on the fee", CollapseReason.fee_gap),
    ("Squad Cost Ratio headroom ran out", CollapseReason.financial_rules),
    ("It just went away", CollapseReason.unknown),
])
def test_collapse_reasons_are_classified(text, reason):
    assert collapse_reason(text) is reason


# ============================================================ TI-020


def test_decay_curve_matches_the_specification():
    d = DEFAULT_DECAY
    assert d.multiplier(0) == 1.0
    assert d.multiplier(3) == 1.0
    assert d.multiplier(14) == pytest.approx(0.60)
    assert d.multiplier(15) == pytest.approx(0.40)
    assert d.multiplier(90) == pytest.approx(0.40)
    assert not d.is_stale(14)
    assert d.is_stale(15)


def test_decay_is_visible_in_the_breakdown():
    d = deal(status=Status.talks, last_verified_at=TODAY - timedelta(days=11),
             evidence=[ev(11, 1, Stage.talks)])
    b = compute_cred(d, TODAY)
    assert b.base_total > b.total
    assert "without movement" in b.explain()


def test_fresh_corroboration_resets_decay():
    quiet = deal(status=Status.talks,
                 last_verified_at=TODAY - timedelta(days=20),
                 evidence=[ev(20, 1, Stage.talks)])
    revived = quiet.model_copy(update={
        "last_verified_at": TODAY,
        "evidence": quiet.evidence + [ev(0, 1, Stage.talks, source="BBC Sport")],
    })
    assert compute_cred(revived, TODAY).total > compute_cred(quiet, TODAY).total


def test_the_curve_lives_in_one_place():
    """TI-020 acceptance: one configuration constant, not scattered."""
    steep = DecayCurve(mid_multiplier=0.2, stale_multiplier=0.1)
    assert steep.multiplier(14) == pytest.approx(0.2)


# ============================================================ TI-002


def _load_site_data():
    raw = json.loads((ROOT / "data.json").read_text(encoding="utf-8"))
    return raw, [Deal(**d) for d in raw["deals"]]


def test_chart_and_table_read_the_same_collection():
    """TI-002 acceptance: assert equal record counts at build time."""
    raw, deals = _load_site_data()
    eligible = site.value_eligible(deals)
    rendered = site.render_value_rows(deals, raw["config"].get("provenClubs", []))
    assert rendered.count('class="clickable"') == len(eligible)
    # Rows are keyed by deal id, never by position. Sorting reorders the
    # array in the browser, and an index would then open the wrong deal.
    for d in eligible:
        assert f'data-id="{d.id}"' in rendered
    assert "data-i=" not in rendered


def test_free_transfers_are_excluded_cleanly():
    raw, deals = _load_site_data()
    assert all(d.fee > 0 for d in site.value_eligible(deals))
    frees = [d for d in deals if not d.fee]
    assert frees, "dataset should contain free transfers to make this meaningful"


def test_rows_are_sorted_by_fee_descending():
    _, deals = _load_site_data()
    fees = [d.fee for d in site.value_eligible(deals)]
    assert fees == sorted(fees, reverse=True)


def test_empty_dataset_renders_an_empty_state_not_bare_headers():
    assert "No confirmed fees yet" in site.render_value_rows([], [])
    assert site.render_value_rows([], []).strip().startswith("<tr")


def test_python_and_browser_value_models_agree():
    """The two implementations of the value score must not drift apart.

    index.html scores deals for the reader and site.py scores them for the
    build. Duplication is the mechanism here, not an accident: if someone
    tunes the age curve in one copy, this test is what tells them about the
    other.
    """
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    for fragment in ["s += d.age<=24 ? (24-d.age)*2.2 : -(dist*3.2)",
                     'if(["AM","ST","LW","RW"].includes(d.pos)) s += 6',
                     'if(d.pos==="CM") s += 4',
                     "if(d.age>=30) s -= 8"]:
        assert fragment in html, (
            f"browser value model changed ({fragment!r} missing). "
            "Update site.value_score to match."
        )


# ============================================================ TI-003


def test_no_section_relies_on_a_fixed_viewport_height():
    """TI-003 acceptance: no section relies on a fixed viewport height."""
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    style = html[html.index("<style>"):html.index("</style>")]
    offenders = re.findall(r"(?:^|[;{])\s*(?:min-)?height\s*:\s*100vh", style)
    assert not offenders
    # `overflow:hidden` is only a scroll bug on the scrolling elements. It is
    # a legitimate tool elsewhere, e.g. the screen-reader-only helper.
    for rule in re.findall(r"(?:^|})\s*(html|body)\s*\{([^}]*)\}", style):
        assert "overflow" not in rule[1].replace(" ", ""), rule


def test_sections_stack_on_mobile():
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    assert "@media(max-width:860px)" in html
    assert "scrollIntoView" in html
    assert "touch-action:pan-x pan-y" in html


def test_nav_is_not_trapped_inside_the_header():
    """A sticky or fixed nav inside <header> cannot outlive the header.

    Sticky positioning is bounded by the containing block. The header is only
    as tall as its own contents, so a nav inside it scrolled away with it and
    the only way back to the first section was to scroll the whole page up.
    """
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    header = html[html.index("<header>"):html.index("</header>")]
    assert "<nav" not in header
    assert html.index("</header>") < html.index('<nav id="tabnav"')


def test_mobile_nav_stays_on_screen_and_clears_content():
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    mobile = html[html.index("@media(max-width:860px)"):]
    mobile = mobile[:mobile.index("</style>")]
    assert "position:fixed" in mobile.replace(" ", "")
    assert "bottom:0" in mobile.replace(" ", "")
    # The bar overlays the page, so the page has to make room for it or the
    # footer and the last capture form sit underneath it forever.
    assert "padding-bottom:calc(64px" in mobile.replace(" ", "")
    assert "safe-area-inset-bottom" in mobile


def test_nav_buttons_carry_both_label_lengths():
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    for full, brief in [("Window Pulse", "Pulse"), ("Rumour Credibility", "Rumours"),
                        ("Value Analytics", "Value"), ("Club Dashboards", "Clubs")]:
        assert f'<span class="full">{full}</span>' in html, full
        assert f'<span class="brief">{brief}</span>' in html, brief


def test_active_tab_is_announced_not_just_coloured():
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    assert html.count('role="tab"') == 4
    assert 'aria-selected="true"' in html
    assert "setAttribute(\"aria-selected\"" in html


# ============================================================ TI-010, TI-011


def _change(deal_obj, kind, detail, frm=None, to=None):
    return dg.Change(deal_obj, kind, detail, frm, to)


def test_quiet_day_sends_a_short_edition_not_a_broken_one():
    edition = dg.Edition("all", TODAY)
    assert edition.is_quiet
    body = dg.render_markdown(edition, "https://x.test")
    assert "Nothing moved today" in body
    assert len(body.splitlines()) < 15
    assert dg.render_subject(edition) == "TransferIntel: a quiet day"


def test_digest_reports_the_four_section_types():
    d = deal()
    changes = [
        _change(d, "tracked", "now tracked at 40/100"),
        _change(d, "status", "talks to agreed"),
        _change(d, "cred", "up 30 to 55", 30, 55),
        _change(d, "collapsed", "medical failed"),
    ]
    edition = dg.build_editions(changes, TODAY, ["Club B"])[0]
    body = dg.render_markdown(edition, "https://x.test")
    for heading in ["New deals tracked", "Status changes",
                    "Credibility moves", "Collapsed"]:
        assert heading in body


def test_credibility_moves_below_the_threshold_are_not_news():
    d = deal(cred=50)
    previous = {d.id: {"status": "rumor", "cred": 42, "fee": 40.0}}
    assert not dg.changes_since([d], previous, TODAY)


def test_segments_only_exist_for_clubs_with_news():
    d = deal(to="Newcastle")
    changes = [_change(d, "status", "talks to agreed")]
    editions = dg.build_editions(changes, TODAY, ["Newcastle", "Everton"])
    names = {e.segment for e in editions}
    assert names == {"all", "Newcastle"}


def test_a_subscriber_segment_gets_only_matching_deals():
    """TI-011 acceptance: Newcastle only means Newcastle only."""
    newcastle = deal(id="a", to="Newcastle")
    chelsea = deal(id="b", to="Chelsea", **{"from": "Club C"})
    changes = [_change(newcastle, "status", "x"), _change(chelsea, "status", "y")]
    editions = {e.segment: e for e in
                dg.build_editions(changes, TODAY, ["Newcastle", "Chelsea"])}
    assert [c.deal.id for c in editions["Newcastle"].status_moves] == ["a"]


def test_instant_alerts_respect_the_daily_cap():
    big = [
        _change(deal(id=f"d{i}", fee=90.0), "status", "to done",
                "medical", Status.done.value)
        for i in range(5)
    ]
    assert len(dg.instant_alerts(big)) == dg.MAX_ALERTS_PER_DAY


def test_small_deals_do_not_trigger_instant_alerts():
    small = [_change(deal(fee=5.0), "status", "to done", "medical",
                     Status.done.value)]
    assert not dg.instant_alerts(small)


def test_the_pipeline_never_sends_twice_for_one_day(tmp_path):
    log = tmp_path / "sent.json"
    assert not dg.already_sent(log, TODAY)
    dg.mark_sent(log, TODAY)
    assert dg.already_sent(log, TODAY)
    assert not dg.already_sent(log, TODAY + timedelta(days=1))


# ============================================================ TI-012, TI-013


def test_analytics_adds_no_cookies_and_stays_small():
    cfg = site.SiteConfig(base_url="https://x.test", analytics_token="tok")
    block = site.render_analytics(cfg)
    assert len(block.encode("utf-8")) < 5120, "TI-012: page weight under 5KB"
    assert "document.cookie" not in block


def test_all_six_analytics_events_exist():
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    for event in ["newsletter_submit", "deal_opened", "club_opened",
                  "index_filtered", "outbound_source", "newsletter_confirmed"]:
        assert event in html, event


def test_capture_form_is_absent_until_configured():
    """A form posting nowhere is worse than no form."""
    assert site.render_capture_form(
        site.SiteConfig(base_url="https://x.test"), [], "top") == ""


def test_capture_form_carries_preferences_when_asked():
    cfg = site.SiteConfig(base_url="https://x.test",
                          newsletter_action="https://provider.test/f")
    plain = site.render_capture_form(cfg, ["Arsenal"], "top")
    rich = site.render_capture_form(cfg, ["Arsenal"], "top",
                                    with_preferences=True)
    assert "fields[threshold]" not in plain
    assert "fields[threshold]" in rich and "Arsenal" in rich
    assert "/thanks/" in plain


def test_club_sort_defaults_to_activity_and_groups_the_quiet_ones():
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    assert 'id="f-clubsort"' in html
    assert "No tracked activity" in html
    assert "sessionStorage" in html and "localStorage" not in html


def test_club_cards_show_in_out_and_net():
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    for label in ["<span>Spent</span>", "<span>Sold</span>", "<span>Net</span>"]:
        assert label in html, label


# ============================================================ sorting


def test_feed_default_order_matches_the_browser():
    """The pre-rendered pulse feed and the browser feed must agree.

    They used to not: the build sorted by credibility then player name while
    index.html sorted by stage then fee, so the list visibly reshuffled the
    moment the script ran, and readers without JavaScript got twelve settled
    completions in reverse alphabetical order.
    """
    _, deals = _load_site_data()
    html = (ROOT / "index.html").read_text(encoding="utf-8")

    # The browser's rank table, read out of the page rather than restated.
    found = re.search(r"const RANK=\{([^}]*)\}", html)
    assert found, "browser feed rank table not found"
    browser_rank = dict(
        (k.strip(), int(v)) for k, v in
        (pair.split(":") for pair in found.group(1).split(","))
    )
    assert browser_rank == site.FEED_RANK

    ordered = site.feed_order(deals)
    ranks = [site.FEED_RANK[d.status.value] for d in ordered]
    assert ranks == sorted(ranks)


def test_every_feed_sort_is_implemented_on_both_sides():
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    for mode in ["active", "recent", "fee", "cred", "quiet"]:
        assert f'value="{mode}"' in html, mode
        assert f"{mode}:" in html, mode
    # "quiet" is browser only: it depends on the reader's clock, so the build
    # cannot pre-render it and does not pretend to.
    for mode in ["active", "recent", "fee", "cred"]:
        assert site.feed_order([], mode) == []


def test_display_dates_sort_chronologically():
    assert site.display_date_key("Jul 8") < site.display_date_key("Jul 21")
    assert site.display_date_key("Jun 30") < site.display_date_key("Jul 1")
    assert site.display_date_key("") == (0, 0)
    assert site.display_date_key("not a date") == (0, 0)


def test_feed_recent_puts_undated_deals_last():
    _, deals = _load_site_data()
    ordered = site.feed_order(deals, "recent")
    keys = [site.display_date_key(d.date) for d in ordered]
    assert keys == sorted(keys, reverse=True)


def test_value_table_columns_are_all_sortable():
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    for column in ["player", "move", "age", "fee", "score", "verdict"]:
        assert f'data-sort="{column}"' in html, column
        assert f"{column}:" in html, column


def test_sortable_headers_are_reachable_and_announced():
    """A sortable header that is only a click target excludes keyboard and
    screen reader users from the feature entirely."""
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    headers = re.findall(r'<th data-sort="(\w+)" aria-sort="(\w+)">'
                         r'<button type="button">', html)
    assert len(headers) == 6
    # Exactly one column carries the initial sort, and it is the one the
    # build actually rendered.
    assert [h[0] for h in headers if h[1] != "none"] == ["fee"]
    assert "aria-sort" in html and "focus-visible" in html


def test_sort_preferences_use_session_not_local_storage():
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    assert html.count("ti.valsort") >= 1
    assert html.count("ti.feedsort") >= 1
    assert "localStorage" not in html


# ============================================================ follow-ups


def test_confirmed_decay_is_floored_so_it_cannot_contradict_its_badge():
    """A record badged "Confirmed pending announcement" must not also show a
    number that reads as a coin flip.

    Confirmed deals still decay: silence after a tier 1 source calls a deal
    finished is real evidence. But the full curve took them to 50 and below,
    where the number argues with the label printed next to it.
    """
    from dataclasses import replace as _replace

    quiet = deal(
        status=Status.confirmed, cred=70,
        last_verified_at=TODAY - timedelta(days=11),
        evidence=[ev(11, 2, Stage.completed, source="Football365")],
    )
    unfloored = _replace(DEFAULT_CONFIG, confirmed_decay_floor=0.0)
    assert compute_cred(quiet, TODAY, unfloored).total < \
        compute_cred(quiet, TODAY).total
    assert compute_cred(quiet, TODAY).total >= 55


def test_confirmed_still_decays_inside_the_floor():
    fresh = deal(status=Status.confirmed, cred=90,
                 last_verified_at=TODAY,
                 evidence=[ev(0, 1, Stage.completed)])
    quiet = deal(status=Status.confirmed, cred=90,
                 last_verified_at=TODAY - timedelta(days=8),
                 evidence=[ev(8, 1, Stage.completed)])
    assert compute_cred(quiet, TODAY).total < compute_cred(fresh, TODAY).total


def test_confirmed_never_reaches_one_hundred():
    """100 stays reserved for an announced transfer, whatever the evidence."""
    strong = deal(
        status=Status.confirmed, cred=90, last_verified_at=TODAY,
        evidence=[ev(0, 1, Stage.completed),
                  ev(0, 1, Stage.completed, source="BBC Sport"),
                  ev(0, 1, Stage.completed, source="Ornstein")],
    )
    assert compute_cred(strong, TODAY).total <= 90


def test_collapsed_stays_at_zero():
    dead = deal(status=Status.collapsed, cred=0,
                evidence=[ev(30, 1, Stage.collapsed)])
    b = compute_cred(dead, TODAY)
    assert b.total == 0 and b.pinned


def test_catch_up_window_is_configurable():
    """Missed runs need a wider evidence window or the news they ingest is
    scored as history and moves nothing."""
    from dataclasses import replace as _replace

    stale_news = deal(status=Status.talks, cred=45,
                      evidence=[ev(7, 1, Stage.agreed)])
    assert decide_status(stale_news, TODAY).status is Status.talks
    wide = _replace(DEFAULT_CONFIG, recent_window_days=10)
    assert decide_status(stale_news, TODAY, wide).status is Status.agreed


def test_exactly_one_capture_form_on_the_page():
    """One ask, not three.

    Three placements meant meeting the same offer three times on a single
    scroll, which reads as nagging rather than as an offer. A reader who has
    declined once does not need asking again on the way to the footer.
    """
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    assert 'id="capture-top"' in html
    for gone in ("capture-hero", "capture-index", "capture-footer"):
        assert gone not in html, gone
    # Count the rendered markup only. The page script carries a template
    # literal for the same form, which is not a second form on the page.
    markup = html[:html.index("<script>\nconst DATA")]
    assert markup.count('<form class="capture"') == 1
    assert markup.count('id="capture-') == 1


def test_the_single_form_still_collects_preferences():
    """TI-011 segmentation had exactly one collection point, and it was on a
    placement that no longer exists. Folded into a disclosure instead, so the
    form asks for one thing and keeps the rest optional."""
    cfg = site.SiteConfig(base_url="https://x.test",
                          newsletter_action="https://provider.test/f")
    rendered = site.render_capture_form(cfg, ["Arsenal"], "top",
                                        with_preferences=True)
    assert "fields[clubs]" in rendered and "fields[threshold]" in rendered
    assert "<details" in rendered and "<summary>" in rendered


def test_capture_form_sits_above_the_nav():
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    assert html.index("</header>") < html.index('id="capture-top"')
    assert html.index('id="capture-top"') < html.index('<nav id="tabnav"')


# ============================================================ cost controls


def _art(title, url="https://bbc.co.uk/sport/football/articles/x", outlet="BBC Sport"):
    from transferintel.models import Article
    return Article(url=url, title=title, summary="", published="2026-07-22",
                   outlet=outlet, tier=1)


def test_prefilter_keeps_every_known_transfer_article():
    """Recall is the only metric that matters here.

    A false positive costs a fraction of a penny. A false negative costs a
    deal, silently, with nothing in any log to say a story was missed.
    """
    from transferintel.models import Article
    from transferintel.prefilter import looks_like_transfer_news

    for case in (ROOT / "evals" / "cases").iterdir():
        articles = case / "articles.json"
        if not articles.exists():
            continue
        for raw in json.loads(articles.read_text(encoding="utf-8")):
            article = Article(**raw)
            assert looks_like_transfer_news(article), article.title


@pytest.mark.parametrize("title", [
    "Tottenham to target Bournemouth Kroupi",
    "Man Utd set 40m valuation on Rashford",
    "Rudiger signs one-year extension at Real Madrid",
    "Arsenal complete deal for Club Brugge winger",
    "Chelsea bid rejected for Palace defender",
    "Villa hijack Newcastle move for Freiburg midfielder",
    "Player X on the verge of a switch to Everton",
    "Spurs willing to pay 75m",
])
def test_prefilter_keeps_real_transfer_headlines(title):
    from transferintel.prefilter import looks_like_transfer_news
    assert looks_like_transfer_news(_art(title))


@pytest.mark.parametrize("title", [
    # "Spain leave it late" used to be here. Adding "leave" to the vocabulary
    # so that "Rashford to leave Man Utd" survives made this a false positive,
    # which is the trade the module documents and accepts. Covered instead by
    # test_the_filter_accepts_some_false_positives_on_purpose.
    "France forward Mbappe condemns a Paraguayan senator",
    "After 10 years as Fifa president, could the controversy tip the balance",
    "BBC Sport looks at the end of Ronaldo's World Cup career",
])
def test_prefilter_drops_match_and_politics_coverage(title):
    from transferintel.prefilter import looks_like_transfer_news
    assert not looks_like_transfer_news(_art(title))


def test_live_and_video_paths_are_never_read():
    from transferintel.prefilter import looks_like_transfer_news
    assert not looks_like_transfer_news(
        _art("Transfer deadline day live: every signing",
             "https://bbc.co.uk/sport/football/live/12345"))


def test_seen_articles_are_not_paid_for_twice():
    """The window is 36 hours and the job runs every 24."""
    from transferintel.prefilter import FilterStats, prefilter

    articles = [_art("Arsenal agree deal for winger",
                     f"https://bbc.co.uk/sport/football/articles/{i}")
                for i in range(3)]
    stats = FilterStats()
    kept = prefilter(articles, {articles[0].url, articles[1].url}, stats)
    assert len(kept) == 1
    assert stats.dropped_seen_before == 2


def test_seen_cache_prunes_old_entries(tmp_path):
    from datetime import date, timedelta
    from transferintel.prefilter import load_seen, save_seen

    cache = tmp_path / "seen.json"
    old = (date.today() - timedelta(days=30)).isoformat()
    cache.write_text(json.dumps({"https://a.test/old": old}), encoding="utf-8")
    save_seen(cache, {}, ["https://a.test/new"], date.today())
    urls, _ = load_seen(cache)
    assert "https://a.test/new" in urls
    assert "https://a.test/old" not in urls


def test_notes_default_to_the_cheaper_model():
    from transferintel import notes
    assert "haiku" in notes.DEFAULT_MODEL.lower()
def _ingest_check(tmp_path, stats):
    import subprocess
    f = tmp_path / "s.json"
    f.write_text(json.dumps(stats), encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "check_ingest.py"),
         "--stats", str(f)],
        capture_output=True, text=True,
    )


def test_missing_api_key_fails_the_run_instead_of_publishing_silence(tmp_path):
    """The failure mode that let the site go four days without a new deal.

    Ingest is allowed to fail so a dead feed cannot block the decay pass. With
    no API key the extractor returns nothing, an empty evidence file is
    written, and the run reports success forever.
    """
    out = _ingest_check(tmp_path, {
        "dry_run": True, "ingest": {"feeds": 5, "feeds_failed": 0},
        "extract": {"articles": 30, "claims": 0},
    })
    assert out.returncode == 1
    assert "ANTHROPIC_API_KEY" in out.stdout


def test_all_feeds_down_fails_the_run(tmp_path):
    out = _ingest_check(tmp_path, {
        "dry_run": False, "ingest": {"feeds": 5, "feeds_failed": 5},
        "extract": {"articles": 0, "claims": 0},
    })
    assert out.returncode == 1


def test_a_genuinely_quiet_day_passes(tmp_path):
    """Articles read, no transfer claims found. That is news, not an outage."""
    out = _ingest_check(tmp_path, {
        "dry_run": False, "ingest": {"feeds": 5, "feeds_failed": 1},
        "extract": {"articles": 22, "claims": 0},
    })
    assert out.returncode == 0
    assert "quiet days" in out.stdout


def test_a_healthy_run_passes(tmp_path):
    out = _ingest_check(tmp_path, {
        "dry_run": False, "ingest": {"feeds": 5, "feeds_failed": 0},
        "extract": {"articles": 28, "claims": 9, "resolved": 6},
    })
    assert out.returncode == 0


def test_health_check_survives_a_truncated_stats_file(tmp_path):
    """An interrupted run leaves half a JSON file. The checker must report
    that, not raise about it."""
    import subprocess
    f = tmp_path / "s.json"
    f.write_text('{"ingest": {"feeds"', encoding="utf-8")
    out = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "check_ingest.py"),
         "--stats", str(f)], capture_output=True, text=True)
    assert out.returncode == 1
    assert "truncated" in out.stdout
    assert "Traceback" not in out.stderr


def test_no_orphaned_capture_references_in_the_page_script():
    """Removing the extra placements left `idx` and `foot` in use below their
    deleted declaration. The error boundary caught it and the form still
    rendered from the build, which is exactly why it was easy to miss."""
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    script = html[html.index("<script>\nconst DATA"):]
    for orphan in ('form("index"', 'form("footer"', 'form("hero"'):
        assert orphan not in script, orphan


@pytest.mark.parametrize("title", [
    "Iraola wants Mac Allister and Alisson to stay with Reds",
    "Arsenal fend off interest in Saka",
    "Guimaraes commits future to Newcastle with new deal",
    "Palace insist Lacroix is not for sale",
    "Rashford to quit Man Utd this summer",
])
def test_prefilter_keeps_transfers_that_are_being_resisted(title):
    """Resisting a transfer is transfer news.

    A manager saying he wants a player to stay only gets written because
    somebody is trying to buy him. The filter was dropping the whole genre:
    contract renewals, hands-off statements, players agitating to leave.
    """
    from transferintel.prefilter import looks_like_transfer_news
    assert looks_like_transfer_news(_art(title))


def test_the_filter_accepts_some_false_positives_on_purpose():
    """"Spain leave it late" matches on "leave" and is kept.

    Documented rather than fixed. Chasing idioms is the "how much more can
    this cut" thinking the module exists to avoid, and one extra article in a
    batch costs a fraction of a penny. The asymmetry runs the other way: a
    dropped transfer story costs a deal, silently.
    """
    from transferintel.prefilter import looks_like_transfer_news
    assert looks_like_transfer_news(
        _art("Spain leave it late as Merino scores a stoppage-time winner"))


def test_club_aliases_match_the_dataset_house_style():
    """The site matches club dashboards on exact string equality.

    The alias table canonicalised to "Wolverhampton" while every record in
    data.json said "Wolves", so an ingested deal and a migrated one described
    the same club with two different strings and only one of them could ever
    match a dashboard.
    """
    from transferintel.entities import canonical_club

    raw = json.loads((ROOT / "data.json").read_text(encoding="utf-8"))
    known = set(raw["clubs"])
    seen = {d["from"] for d in raw["deals"]} | {d["to"] for d in raw["deals"]}
    for name in seen:
        assert canonical_club(name, known) == name, (
            f"{name!r} in data.json canonicalises to "
            f"{canonical_club(name, known)!r}; the two must agree"
        )


def test_no_orphaned_form_markup_in_the_page():
    """Removing a placement must remove the whole form, not part of it.

    A non-greedy `<div id="capture-x">.*?</div>` stops at the first *inner*
    closing div, so deleting a form that contains one leaves its tail behind:
    a hidden input, a stray paragraph and an unmatched `</form>`, rendering
    "Double opt-in. One click to unsubscribe." as loose text mid-page. Three
    of those shipped before anyone noticed, because unbalanced tags still
    render and the leftovers looked like ordinary copy.
    """
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    markup = html[:html.index("<script>\nconst DATA")]
    assert markup.count("<form") == markup.count("</form>") == 1
    assert markup.count("<details") == markup.count("</details>") == 1
    assert markup.count("<div") == markup.count("</div>")
    # The give-away strings, each of which must appear exactly once.
    for phrase in ("Double opt-in", "Get the digest",
                   "Only want certain clubs", "The daily transfer digest"):
        assert markup.count(phrase) == 1, f"{phrase!r} x{markup.count(phrase)}"


# ============================================================ draft claims


def test_drafts_cannot_enter_the_pipeline_unread(tmp_path):
    """A guessed claim must not become evidence without a human reading it."""
    import subprocess

    claims = tmp_path / "c.json"
    claims.write_text(json.dumps([{
        "_draft": True, "_review": ["check the clubs"],
        "is_transfer_claim": True, "player": "X", "from_club": "Arsenal",
        "to_club": "Chelsea", "reported_stage": "talks",
        "fee_amount": None, "fee_currency": None,
        "article_url": "https://x.test/a",
    }]), encoding="utf-8")
    articles = tmp_path / "a.json"
    articles.write_text(json.dumps([{
        "url": "https://x.test/a", "title": "t", "summary": "",
        "published": "2026-07-22", "outlet": "BBC Sport", "tier": 1,
    }]), encoding="utf-8")

    out = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "run_ingest.py"),
         "--data", str(ROOT / "data.json"), "--out", str(tmp_path / "b"),
         "--articles", str(articles), "--claims", str(claims),
         "--today", "2026-07-22"],
        capture_output=True, text=True, cwd=str(ROOT))
    assert out.returncode == 2
    assert "_draft" in out.stderr


def test_drafter_reads_a_headline_it_should_get_right():
    from transferintel.models import Article
    sys.path.insert(0, str(ROOT / "scripts"))
    from draft_claims import draft

    known = ["Arsenal", "Brighton", "Chelsea", "Aston Villa"]
    art = Article(
        url="https://x.test/1",
        title="Ellis Hartley to undergo Arsenal medical on Sunday",
        summary="Brighton have accepted a bid worth £42m.",
        published="2026-07-22", outlet="BBC Sport", tier=1)
    got = draft(art, known)
    assert got["player"] == "Ellis Hartley"
    assert got["reported_stage"] == "medical"
    assert got["fee_amount"] == 42.0
    assert {got["from_club"], got["to_club"]} == {"Arsenal", "Brighton"}
    assert got["_draft"] is True and got["_review"]


def test_drafter_admits_what_it_could_not_find():
    from transferintel.models import Article
    sys.path.insert(0, str(ROOT / "scripts"))
    from draft_claims import draft

    art = Article(
        url="https://x.test/2",
        title="Rafael Moreno completes Newcastle move from Benfica",
        summary="", published="2026-07-22", outlet="BBC Sport", tier=1)
    got = draft(art, ["Newcastle"])   # Benfica is not a known club
    assert any("known club" in r for r in got["_review"])


def test_no_orphaned_form_controls_outside_a_form():
    """Two stray dropdowns shipped at the foot of the rumours section.

    They were the preference fields of a deleted capture form: the cleanup
    removed the form's tail and left its middle. Form controls render fine
    outside a form, so nothing complained.
    """
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    markup = html[:html.index("<script>\nconst DATA")]
    # Every provider field belongs to the one surviving form.
    assert markup.count('name="fields[clubs]"') == 1
    assert markup.count('name="fields[threshold]"') == 1
    assert markup.count('class="capture-prefs"') == 1
    # The filter selects, and nothing else.
    assert markup.count("<select") == 7, markup.count("<select")


def test_positions_and_source_links_render():
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    markup = html[:html.index("<script>\nconst DATA")]
    assert markup.count('class="pos"') > 20
    assert 'class="srclink"' in markup
    # Outbound links must not hand the opener window over.
    for tag in re.findall(r'<a class="srclink"[^>]*>', markup):
        assert 'rel="noopener nofollow"' in tag, tag
        assert 'target="_blank"' in tag, tag


def test_seed_records_get_no_source_link():
    """A urn: seed is not a link. Rendering one would be a dead end."""
    from transferintel.models import Deal
    raw = json.loads((ROOT / "data.json").read_text(encoding="utf-8"))
    seeded = [
        Deal(**d) for d in raw["deals"]
        if d["evidence"] and all(str(e["url"]).startswith("urn:")
                                 for e in d["evidence"])
    ]
    assert seeded, "dataset should still contain migrated records"
    for deal in seeded:
        assert site.best_source_link(deal) is None
        assert site.source_anchor(deal) == ""


def test_pitch_numbers_match_the_scoring_code():
    """The pitch quotes the model's actual constants.

    A pitch document that describes tier 1 as 55 while the code says something
    else is worse than one that stays vague: it is checkable, and someone
    evaluating this will check.
    """
    from transferintel.scoring import DEFAULT_CONFIG, DEFAULT_DECAY

    pitch = (ROOT / "docs" / "PARTNERSHIP.md").read_text(encoding="utf-8")
    for tier, base in DEFAULT_CONFIG.base_by_tier.items():
        assert f"| **{base}** |" in pitch, f"tier {tier} base {base}"
    assert f"+{DEFAULT_CONFIG.corroboration_step} for each" in pitch
    assert f"capped at +{DEFAULT_CONFIG.corroboration_cap}" in pitch
    for label, bonus in [("Fee agreed", "agreed"), ("Medical booked", "medical"),
                         ("Reported complete", "completed")]:
        from transferintel.models import Stage
        expected = DEFAULT_CONFIG.stage_bonus[Stage(bonus)]
        assert f"| {label} | +{expected} |" in pitch, label
    assert f"{DEFAULT_DECAY.mid_multiplier:.2f}" in pitch
    assert f"**{DEFAULT_DECAY.stale_multiplier:.2f}**" in pitch
    assert f"{DEFAULT_CONFIG.confirmed_cred_cap}" in pitch
    assert f"at most {DEFAULT_CONFIG.max_cred_delta_per_run} points" in pitch


# ============================================================ 23 July fixes


def test_a_claim_with_no_matching_article_is_reported(tmp_path):
    """It used to be dropped silently.

    A hand-written or drafted claim whose `article_url` is not in the article
    set has no source, date or tier to attach to. Skipping it quietly means a
    claim you took the trouble to write produces nothing and explains nothing.
    """
    from transferintel.extract import ExtractionStats, resolve
    from transferintel.models import Article, Claim

    article = Article(url="https://x.test/real", title="t", summary="",
                      published="2026-07-23", outlet="BBC Sport", tier=1)
    claim = Claim(is_transfer_claim=True, player="Someone",
                  from_club="Arsenal", to_club="Chelsea",
                  reported_stage="talks", article_url="https://x.test/typo")
    _, _, unresolved = resolve([claim], [article], [], ["Arsenal", "Chelsea"],
                               ExtractionStats())
    assert len(unresolved) == 1
    assert "no article in this run" in unresolved[0]["reason"]


def test_drafter_writes_articles_and_claims_as_a_matched_pair(tmp_path):
    """The manual routine had no step that produced manual/articles.json.

    `run_ingest --claims` needs the articles to attach a source, date and tier
    to each claim, so a claims file whose URLs are not in the article set
    resolves to nothing. Leaving that file to be assembled by hand was the
    single most confusing step in the routine.
    """
    import subprocess

    out_c, out_a = tmp_path / "c.json", tmp_path / "a.json"
    run = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "draft_claims.py"),
         "--articles", str(ROOT / "fixtures" / "articles.json"),
         "--data", str(ROOT / "data.json"),
         "--out", str(out_c), "--out-articles", str(out_a)],
        capture_output=True, text=True, cwd=str(ROOT))
    assert run.returncode == 0, run.stderr
    claims = json.loads(out_c.read_text(encoding="utf-8"))
    articles = json.loads(out_a.read_text(encoding="utf-8"))
    urls = {a["url"] for a in articles}
    assert claims and articles
    assert all(c["article_url"] in urls for c in claims)


def test_kit_segment_filter_is_an_array():
    """Kit returned 422 "Subscriber filter must be an array" on every club
    edition while the unsegmented one, which sends no filter, went out fine.
    So the digest reached everybody and the segments reached nobody."""
    sys.path.insert(0, str(ROOT / "scripts"))
    import run_digest

    captured = {}

    def fake_post(url, payload, headers):
        captured.update(payload)
        return 201, "{}"

    original, run_digest.post_json = run_digest.post_json, fake_post
    try:
        run_digest.send_edition("kit", "k", "s", "b", "Arsenal")
        assert isinstance(captured["subscriber_filter"], list)
        assert captured["subscriber_filter"][0]["all"][0]["name"] == "Arsenal"
        captured.clear()
        run_digest.send_edition("kit", "k", "s", "b", "all")
        assert "subscriber_filter" not in captured
    finally:
        run_digest.post_json = original


@pytest.mark.parametrize("title", [
    "Chelsea Women sign forward from Arsenal Women",
    "WSL champions complete signing of midfielder",
    "Lionesses star joins Manchester City Women",
    "Women's Super League transfer round-up",
])
def test_out_of_scope_coverage_is_dropped(title):
    """Women's transfer news is real news and is not what this site tracks.

    During a major tournament the general feeds fill with it, it matches every
    transfer keyword, and left in it drowns the dataset in players no tracked
    club is signing.
    """
    from transferintel.prefilter import in_scope
    assert not in_scope(_art(title))


@pytest.mark.parametrize("title", [
    "Chelsea sign Morgan Rogers from Aston Villa",
    "Newcastle chairwoman confirms Guimaraes will stay",
    "Liverpool complete signing after his agent confirmed terms",
])
def test_scope_filter_does_not_overreach(title):
    """Only explicit markers. No bare "her" or "she", which appear constantly
    in men's coverage quoting a partner, a chairwoman or a journalist."""
    from transferintel.prefilter import in_scope
    assert in_scope(_art(title))


def test_failing_feeds_are_named():
    """"1 of 5 feeds did not respond" says something is wrong and not which
    thing, so the dead feed stays dead: nobody can act on a number."""
    from transferintel.ingest import collect

    feeds = [("https://a.test/rss", "Alpha"), ("https://b.test/rss", "Beta")]
    _, stats = collect(date(2026, 7, 23), feeds=feeds, fetcher=lambda u: None)
    assert stats["feeds_failed"] == 2
    assert set(stats["failed_names"]) == {"Alpha", "Beta"}


def test_dead_feeds_are_not_polled_but_their_tiers_survive():
    """Removing a feed must not forget what the outlet is worth.

    Football365 and the Telegraph still appear as sources inside articles
    other outlets link to, so their tier still has to be known even though
    neither feed is polled.
    """
    from transferintel.sources import DOMAIN_TIER, FEEDS

    polled = {url for url, _ in FEEDS}
    assert not any("football365" in u or "telegraph" in u for u in polled)
    assert DOMAIN_TIER["football365.com"] == 2
    assert DOMAIN_TIER["telegraph.co.uk"] == 2


def test_candidate_feeds_are_well_formed():
    from transferintel.sources import CANDIDATE_FEEDS, FEEDS

    configured = {url for url, _ in FEEDS}
    for url, name, tier in CANDIDATE_FEEDS:
        assert url.startswith("https://"), url
        assert name and tier in (1, 2, 3), name
        assert url not in configured, f"{name} is already configured"


def test_a_stale_feed_is_distinguished_from_a_dead_one():
    """The Telegraph responded with 120 articles and nothing in the window.

    That is a third state, and the one that hides: it parses cleanly, raises
    no warning, and contributes nothing to any run.
    """
    sys.path.insert(0, str(ROOT / "scripts"))
    from check_feeds import probe

    old = (
        b'<?xml version="1.0"?><rss><channel>'
        b"<item><title>Old transfer story</title>"
        b"<link>https://x.test/1</link>"
        b"<pubDate>Mon, 02 Mar 2026 10:00:00 GMT</pubDate></item>"
        b"</channel></rss>"
    )
    import transferintel.ingest as ing
    original, ing.fetch = ing.fetch, lambda u: old
    import check_feeds
    original_cf, check_feeds.fetch = check_feeds.fetch, lambda u: old
    try:
        row = probe("https://x.test/rss", "Stale", date(2026, 7, 23), 2)
        assert row["state"] == "stale"
        assert row["parsed"] == 1 and row["window"] == 0
    finally:
        ing.fetch = original
        check_feeds.fetch = original_cf
