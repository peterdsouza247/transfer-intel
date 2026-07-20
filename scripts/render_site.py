#!/usr/bin/env python3
"""Render the discoverable site from data.json.

    python scripts/render_site.py --data data.json --template index.html --out .

Writes, all derived from the same `data.json` the pipeline maintains:

    index.html          the template, with head tags and pre-rendered content
    deals/<id>/         one page per tracked transfer
    clubs/<slug>/       one page per club dashboard
    og/*.png            Open Graph cards
    sitemap.xml robots.txt feed.xml llms.txt favicon.svg 404.html

`index.html` is rewritten in place by default, which is safe because the
injection is idempotent: generated head tags are stripped and rebuilt on every
run, and container contents are replaced rather than appended. Run it twice
and you get the same file.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from transferintel import og, site  # noqa: E402
from transferintel.models import Deal  # noqa: E402

def strip_generated_head(document: str) -> str:
    """Remove exactly what the previous run added, and nothing else.

    Delimiting the block at both ends matters: an earlier version deleted from
    the start marker to `</head>`, which quietly ate any hand-written tag the
    author had added after it, and consuming the preceding whitespace instead
    ate a blank line from the template on every run. Neither file was ever
    byte-identical twice.
    """
    start = document.find(site.GEN_START)
    if start == -1:
        return document
    end = document.find(site.GEN_END, start)
    if end == -1:
        return document
    end += len(site.GEN_END)
    while end < len(document) and document[end] in "\r\n":
        end += 1
    return document[:start] + document[end:]


def _replace_block(document: str, start: str, end: str, content: str) -> str:
    """Swap the contents of a delimited block, or leave the document alone.

    Same reasoning as the head tags: delimiting at both ends means a rerun
    removes exactly what the previous run added and never eats a hand-written
    tag that happens to sit nearby.
    """
    a = document.find(start)
    b = document.find(end, a + 1) if a != -1 else -1
    if a == -1 or b == -1:
        return document
    return document[:a + len(start)] + "\n" + content + "\n" + document[b:]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, default=Path("data.json"))
    ap.add_argument("--template", type=Path, default=Path("index.html"))
    ap.add_argument("--out", type=Path, default=Path("."))
    ap.add_argument("--base-url", default=None,
                    help="overrides config.site.baseUrl for a preview build")
    ap.add_argument("--skip-images", action="store_true",
                    help="skip PNG generation, useful in tests")
    args = ap.parse_args()

    raw = json.loads(args.data.read_text(encoding="utf-8"))
    deals = [Deal(**d) for d in raw["deals"]]
    clubs = raw.get("clubs", {})
    cfg = site.SiteConfig.from_data(raw)
    if args.base_url:
        cfg.base_url = args.base_url.rstrip("/")

    if not cfg.base_url:
        print(
            "ERROR: config.site.baseUrl is not set in data.json.\n"
            "  Canonical tags, Open Graph URLs and the sitemap all need an\n"
            "  absolute origin, and a guessed one is worse than none: it tells\n"
            "  crawlers the real page lives somewhere else. Add this to the\n"
            "  config block in data.json:\n\n"
            '      "site": { "baseUrl": "https://you.github.io/transferintel" }\n',
            file=sys.stderr,
        )
        return 2

    updated = (raw.get("config") or {}).get("updated", "")
    updated_iso = date.today().isoformat()
    out = args.out
    out.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    entries: list[tuple[str, str, str]] = [("/", updated_iso, "daily")]

    # -- 1. the index, pre-rendered ---------------------------------------
    document = args.template.read_text(encoding="utf-8")
    document = strip_generated_head(document)

    # The card lists the site's own sections with a real figure against each,
    # and drops any whose figure would be zero. Early in a window there are no
    # completed deals and no fees to assess, and a share card announcing "0"
    # argues against clicking it.
    done = [d for d in deals if str(getattr(d.status, "value", d.status)) == "done"]
    assessed = sum(d.fee or 0 for d in done)
    avg_cred = round(sum(d.cred for d in deals) / len(deals)) if deals else 0

    sections: list[tuple[str, str]] = []
    if deals:
        sections.append(
            ("Rumour Credibility", f"{len(deals)} deals scored, {avg_cred} average")
        )
    if assessed:
        sections.append(("Value Analytics", f"£{assessed:g}m assessed"))
    elif deals:
        sections.append(("Value Analytics", "fees scored on completion"))
    if clubs:
        sections.append(("Club Dashboards", f"{len(clubs)} clubs"))
    if not sections:
        sections = [("Rumour Credibility", ""), ("Value Analytics", ""),
                    ("Club Dashboards", "")]

    head = site.head_tags(
        cfg,
        title=f"{cfg.title}: Premier League transfer rumours scored for credibility",
        description=cfg.description,
        path="/",
        og_image="og/default.png",
        updated_iso=updated_iso,
        extra=site.script_ld(
            site.jsonld_website(cfg, raw),
            site.jsonld_dataset(cfg, raw, deals, updated_iso),
            site.jsonld_itemlist(cfg, deals),
        ),
    )
    document = site.inject_head(document, head)

    # The site's own JavaScript overwrites these containers on load, so the
    # browser experience is unchanged and a crawler that never runs a script
    # now sees the entire window.
    document = site.inject_into(document, "rumorlist", site.render_deal_list(deals, cfg))
    document = site.inject_into(document, "clubgrid", site.render_club_grid(clubs, deals, cfg))
    document = site.inject_into(document, "funnel", site.render_funnel(deals))
    # TI-002. The verdicts table is pre-rendered from the same collection the
    # scatter chart uses, so a crawler sees the rows and, more usefully, the
    # build can count them. A table that silently renders zero rows is the
    # bug this ticket exists for; it is now impossible to ship one unnoticed.
    proven = (raw.get("config") or {}).get("provenClubs", [])
    document = site.inject_into(
        document, "valtable-body", site.render_value_rows(deals, proven)
    )
    # TI-010. Rendered server-side so the form works with JavaScript disabled
    # and so a crawler sees the offer. The page's own script re-renders these
    # on load; both produce the same markup.
    document = site.inject_into(
        document, "capture-hero", site.render_capture_form(cfg, clubs, "hero"))
    document = site.inject_into(
        document, "capture-index",
        site.render_capture_form(cfg, clubs, "index", with_preferences=True))
    document = site.inject_into(
        document, "capture-footer", site.render_capture_form(cfg, clubs, "footer"))
    document = site.inject_into(document, "feed", site.render_feed_items(deals))
    document = site.inject_into(
        document, "footer",
        f'<p>{site.e(cfg.title)} · updated {site.e(updated)} · '
        f'<a href="{site.e(cfg.href("feed.xml"))}">RSS</a></p>',
    )

    # TI-012. One generated analytics block, delimited like the head tags so a
    # rerun replaces exactly what the last one wrote.
    document = _replace_block(
        document, "<!-- ti:analytics -->", "<!-- /ti:analytics -->",
        site.render_analytics(cfg),
    )

    (out / "index.html").write_text(document, encoding="utf-8")
    written.append(out / "index.html")

    # -- 2. deal pages -----------------------------------------------------
    for deal in deals:
        path = site.deal_path(deal)
        page_dir = out / path
        page_dir.mkdir(parents=True, exist_ok=True)
        (page_dir / "index.html").write_text(
            site.render_deal_page(cfg, deal, updated_iso, updated), encoding="utf-8"
        )
        written.append(page_dir / "index.html")
        entries.append((path, updated_iso, "daily"))

    # -- 3. club pages -----------------------------------------------------
    for club, club_data in sorted(clubs.items()):
        path = site.club_path(club)
        page_dir = out / path
        page_dir.mkdir(parents=True, exist_ok=True)
        (page_dir / "index.html").write_text(
            site.render_club_page(cfg, club, club_data or {}, deals, updated_iso, updated),
            encoding="utf-8",
        )
        written.append(page_dir / "index.html")
        entries.append((path, updated_iso, "daily"))

    # -- 3b. the subscribe confirmation page (TI-010) ----------------------
    if cfg.newsletter_action:
        thanks = out / "thanks"
        thanks.mkdir(parents=True, exist_ok=True)
        (thanks / "index.html").write_text(
            site.render_thanks_page(cfg, updated_iso), encoding="utf-8")
        written.append(thanks / "index.html")
        entries.append(("thanks/", updated_iso, "monthly"))

    # -- 4. plumbing -------------------------------------------------------
    (out / "sitemap.xml").write_text(site.render_sitemap(cfg, entries), encoding="utf-8")
    (out / "robots.txt").write_text(site.render_robots(cfg), encoding="utf-8")
    (out / "feed.xml").write_text(site.render_feed(cfg, deals, updated_iso), encoding="utf-8")
    (out / "llms.txt").write_text(
        site.render_llms_txt(cfg, deals, raw, updated), encoding="utf-8"
    )
    (out / "favicon.svg").write_text(site.render_favicon(), encoding="utf-8")
    (out / "404.html").write_text(site.render_404(cfg), encoding="utf-8")
    written += [out / n for n in
                ("sitemap.xml", "robots.txt", "feed.xml", "llms.txt", "favicon.svg", "404.html")]

    # -- 5. Open Graph images ---------------------------------------------
    if not args.skip_images:
        og_dir = out / "og"
        window = (raw.get("config") or {}).get("windowName", "")
        og.site_card(og_dir / "default.png", window, updated, sections)
        for deal in deals:
            og.deal_card(
                og_dir / f"deal-{site.deal_slug(deal)}.png",
                deal.model_dump(by_alias=True), updated,
            )
        for club in clubs:
            incoming = [d for d in deals if d.to == club]
            outgoing = [d for d in deals if d.from_club == club]
            done = [d for d in incoming if str(getattr(d.status, "value", d.status)) == "done"]
            spend = sum(d.fee or 0 for d in done)

            summary: dict[str, object] = {"Tracked incoming": len(incoming)}
            if done:
                summary["Completed"] = len(done)
            if spend:
                summary["Committed spend"] = f"£{spend:g}m"
            if outgoing:
                summary["Tracked outgoing"] = len(outgoing)

            # Early in a window a club has no completed deals and no spend, so
            # the financial rows are all zero and the card comes out mostly
            # empty. Fill the space with the deals that do exist, ranked by
            # credibility, which is the thing the site is actually for.
            if len(summary) < 4:
                for deal in sorted(incoming, key=lambda x: -x.cred)[: 4 - len(summary)]:
                    summary[deal.p] = f"{deal.cred}/100"

            og.club_card(
                og_dir / f"club-{site.slugify(club)}.png", club, summary, updated,
            )

    print(
        f"{len(deals)} deal pages, {len(clubs)} club pages, "
        f"{len(entries)} sitemap URLs. Base {cfg.base_url}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
