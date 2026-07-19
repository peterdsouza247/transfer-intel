"""Phase 7. Run with: python -m pytest scripts/tests -q"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import xml.dom.minidom
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from transferintel import site  # noqa: E402
from transferintel.models import Deal  # noqa: E402

FIX = ROOT.parent / "fixtures"
TEMPLATE = FIX / "template.html"
BASE = "https://example.github.io/transferintel"


def load_deals() -> list[Deal]:
    raw = json.loads((FIX / "data.json").read_text())
    return [Deal(**d) for d in raw["deals"]]


@pytest.fixture(scope="module")
def rendered(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("site")
    result = subprocess.run(
        [sys.executable, str(ROOT / "render_site.py"),
         "--data", str(FIX / "data.json"),
         "--template", str(TEMPLATE),
         "--out", str(out), "--base-url", BASE],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    return out


def text_of(path: Path) -> str:
    """Everything a crawler that runs no JavaScript would read."""
    s = path.read_text(encoding="utf-8")
    s = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", s, flags=re.S | re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", s)


# ---------------------------------------------------------- HTML injection


def test_injection_replaces_inner_html():
    doc = '<div id="a">old</div>'
    assert site.inject_into(doc, "a", "new") == '<div id="a">new</div>'


def test_injection_survives_nested_tags_of_the_same_name():
    """A regex would stop at the first closing div and corrupt the file."""
    doc = '<div id="a"><div>one</div><div>two</div></div><div>after</div>'
    out = site.inject_into(doc, "a", "X")
    assert out == '<div id="a">X</div><div>after</div>'


def test_injection_survives_attribute_order_and_extra_attributes():
    doc = '<div class="panel feed" data-x="1" id="feed" role="list"></div>'
    assert "FILLED" in site.inject_into(doc, "feed", "FILLED")


def test_injection_leaves_the_document_alone_when_the_id_is_missing():
    doc = "<div id='b'>keep</div>"
    assert site.inject_into(doc, "nope", "X") == doc


def test_injection_does_not_touch_anything_outside_the_target():
    doc = '<p>before</p><div id="a">old</div><p>after</p>'
    out = site.inject_into(doc, "a", "new")
    assert out.startswith("<p>before</p>") and out.endswith("<p>after</p>")


# --------------------------------------------------------------- rendering


def test_the_index_gains_real_text_for_a_crawler_without_javascript(rendered):
    """The template's own text is boilerplate: headings, tab labels, blurb.
    None of it names a player, a fee or a score, so a crawler that does not
    run scripts learns nothing about the actual content. This asserts the
    substance arrives, not just that the byte count went up."""
    before = text_of(TEMPLATE)
    after = text_of(rendered / "index.html")
    raw = json.loads((FIX / "data.json").read_text())

    assert len(after) > len(before) * 2
    for deal in load_deals():
        assert deal.p in after, f"{deal.p} is not in the pre-rendered HTML"
        assert deal.from_club in after and deal.to in after
        assert str(deal.cred) in after
    for club in raw["clubs"]:
        assert club in after
    # And none of it was in the template to begin with.
    assert not any(d.p in before for d in load_deals())


def test_every_deal_and_club_has_its_own_url(rendered):
    raw = json.loads((FIX / "data.json").read_text())
    for deal in load_deals():
        assert (rendered / site.deal_path(deal) / "index.html").exists()
    for club in raw["clubs"]:
        assert (rendered / site.club_path(club) / "index.html").exists()


def test_each_page_declares_one_canonical_url(rendered):
    for page in rendered.rglob("index.html"):
        html = page.read_text(encoding="utf-8")
        canonicals = re.findall(r'<link rel="canonical" href="([^"]+)"', html)
        assert len(canonicals) == 1, f"{page} has {len(canonicals)} canonicals"
        assert canonicals[0].startswith(BASE)


def test_open_graph_and_twitter_tags_are_present_and_absolute(rendered):
    for page in rendered.rglob("index.html"):
        html = page.read_text(encoding="utf-8")
        for prop in ("og:title", "og:description", "og:image", "og:url"):
            assert f'property="{prop}"' in html, f"{page} missing {prop}"
        assert 'name="twitter:card" content="summary_large_image"' in html
        image = re.search(r'property="og:image" content="([^"]+)"', html).group(1)
        assert image.startswith("https://"), "og:image must be an absolute URL"


def test_open_graph_images_exist_at_the_size_platforms_crop_from(rendered):
    from PIL import Image
    images = list((rendered / "og").glob("*.png"))
    assert len(images) >= 1 + len(load_deals())
    for img_path in images:
        with Image.open(img_path) as im:
            assert im.size == (1200, 630), f"{img_path.name} is {im.size}"


def test_deal_pages_carry_the_score_and_the_sources(rendered):
    for deal in load_deals():
        page = text_of(rendered / site.deal_path(deal) / "index.html")
        assert deal.p in page
        assert str(deal.cred) in page
        assert deal.from_club in page and deal.to in page


def test_seed_evidence_is_never_shown_as_a_source(rendered):
    """Phase 0 seeds a `urn:` placeholder on migrated deals. Rendering it as a
    citation would put a dead link on a page whose entire pitch is sourcing."""
    for page in rendered.rglob("index.html"):
        assert "urn:transferintel" not in page.read_text(encoding="utf-8")


def test_structured_data_parses_and_says_what_the_page_is(rendered):
    index = (rendered / "index.html").read_text(encoding="utf-8")
    blocks = [json.loads(b) for b in
              re.findall(r'<script type="application/ld\+json">(.*?)</script>',
                         index, re.S)]
    types = {b["@type"] for b in blocks}
    assert {"WebSite", "Dataset", "ItemList"} <= types

    deal = load_deals()[0]
    page = (rendered / site.deal_path(deal) / "index.html").read_text(encoding="utf-8")
    blocks = [json.loads(b) for b in
              re.findall(r'<script type="application/ld\+json">(.*?)</script>',
                         page, re.S)]
    review = next(b for b in blocks if b["@type"] == "ClaimReview")
    assert review["reviewRating"]["ratingValue"] == deal.cred
    assert review["reviewRating"]["bestRating"] == 100


# --------------------------------------------------------------- plumbing


def test_sitemap_is_well_formed_and_lists_every_page(rendered):
    xml.dom.minidom.parse(str(rendered / "sitemap.xml"))
    sitemap = (rendered / "sitemap.xml").read_text(encoding="utf-8")
    raw = json.loads((FIX / "data.json").read_text())
    for deal in load_deals():
        assert f"{BASE}/{site.deal_path(deal)}" in sitemap
    for club in raw["clubs"]:
        assert f"{BASE}/{site.club_path(club)}" in sitemap
    assert "<lastmod>" in sitemap


def test_robots_points_at_the_sitemap_and_allows_crawling(rendered):
    robots = (rendered / "robots.txt").read_text(encoding="utf-8")
    assert f"Sitemap: {BASE}/sitemap.xml" in robots
    assert "User-agent: *" in robots
    assert re.search(r"^Allow: /$", robots, re.M)


def test_the_feed_is_valid_rss_with_absolute_links(rendered):
    xml.dom.minidom.parse(str(rendered / "feed.xml"))
    feed = (rendered / "feed.xml").read_text(encoding="utf-8")
    assert "<rss version=\"2.0\">" in feed
    assert f"<link>{BASE}/</link>" in feed
    for deal in load_deals()[:3]:
        assert deal.p in feed


def test_the_index_advertises_the_feed(rendered):
    html = (rendered / "index.html").read_text(encoding="utf-8")
    assert 'type="application/rss+xml"' in html


def test_llms_txt_states_the_method_not_just_the_links(rendered):
    txt = (rendered / "llms.txt").read_text(encoding="utf-8")
    assert "## Method" in txt
    assert "not predictions" in txt
    for deal in load_deals():
        assert deal.p in txt


def test_a_404_page_exists_for_pages_to_serve(rendered):
    assert (rendered / "404.html").exists()


# ------------------------------------------------------------- guardrails


def test_rendering_refuses_to_run_without_an_absolute_base_url(tmp_path):
    data = json.loads((FIX / "data.json").read_text())
    data["config"].pop("site", None)
    target = tmp_path / "data.json"
    target.write_text(json.dumps(data))
    result = subprocess.run(
        [sys.executable, str(ROOT / "render_site.py"),
         "--data", str(target), "--template", str(TEMPLATE),
         "--out", str(tmp_path), "--skip-images"],
        capture_output=True, text=True,
    )
    assert result.returncode == 2
    assert "baseUrl" in result.stderr


def test_rendering_is_idempotent(tmp_path):
    """The generated block is delimited at both ends so a rerun removes
    exactly what the last one added. Without that, running twice either
    stacks duplicate meta tags or eats a line of the author's template."""
    def render(template: Path) -> str:
        subprocess.run(
            [sys.executable, str(ROOT / "render_site.py"),
             "--data", str(FIX / "data.json"), "--template", str(template),
             "--out", str(tmp_path), "--base-url", BASE, "--skip-images"],
            capture_output=True, text=True, check=True,
        )
        return (tmp_path / "index.html").read_text(encoding="utf-8")

    first = render(TEMPLATE)
    second = render(tmp_path / "index.html")
    third = render(tmp_path / "index.html")
    assert first == second == third


def test_generated_head_does_not_swallow_hand_written_tags(tmp_path):
    doc = (
        "<!DOCTYPE html><html><head><title>T</title>"
        + site.GEN_START + "<meta name='old'>" + site.GEN_END
        + "<link rel='me' href='https://example.com'></head><body></body></html>"
    )
    sys.path.insert(0, str(ROOT))
    from render_site import strip_generated_head
    out = strip_generated_head(doc)
    assert "rel='me'" in out
    assert "name='old'" not in out


def test_slugs_are_url_safe(tmp_path):
    assert site.slugify("West Ham") == "west-ham"
    assert site.slugify("Sporting CP") == "sporting-cp"
    assert site.slugify("Ødegaard, Martin") == "odegaard-martin"
    assert site.slugify("!!!") == "item"
