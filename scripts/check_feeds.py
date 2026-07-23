#!/usr/bin/env python3
"""Test every configured feed, and the candidates, from a real connection.

    python scripts/check_feeds.py                 # what is configured now
    python scripts/check_feeds.py --candidates    # plus 15 worth trying
    python scripts/check_feeds.py --add https://example.com/rss Example

Run this from your own machine. GitHub runners and this project's sandbox are
both blocked by some publishers, so a feed that works for you can still fail
in CI and the reverse.

The column that matters is **kept**: articles surviving the prefilter. A feed
returning fifty articles a day of which two concern transfers is worth less
than one returning six of which four do.

Three outcomes, and the middle one is easy to miss:

- **dead**, no response at all
- **stale**, responds with articles that are all older than the window, which
  looks like a working feed in every log and contributes nothing
- **live**, responding with fresh articles
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from transferintel.ingest import fetch, parse_feed  # noqa: E402
from transferintel.prefilter import FilterStats, prefilter  # noqa: E402
from transferintel.sources import CANDIDATE_FEEDS, DOMAIN_TIER, FEEDS  # noqa: E402


def domain_of(url: str) -> str:
    from urllib.parse import urlsplit
    host = urlsplit(url).netloc.lower()
    for prefix in ("www.", "feeds.", "rss."):
        if host.startswith(prefix):
            host = host[len(prefix):]
    return host


def probe(url: str, name: str, today: date, days: int) -> dict:
    body = fetch(url)
    if body is None:
        return {"name": name, "url": url, "state": "dead"}

    articles = parse_feed(body, today)
    window = [a for a in articles if 0 <= (today - a.published).days <= days]
    stats = FilterStats()
    kept = prefilter(window, set(), stats)
    newest = max((a.published for a in articles), default=None)

    state = "live"
    if articles and not window:
        state = "stale"
    return {
        "name": name, "url": url, "state": state,
        "parsed": len(articles), "window": len(window), "kept": len(kept),
        "newest": newest, "out_of_scope": stats.dropped_out_of_scope,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidates", action="store_true",
                    help="also test the candidate list in sources.py")
    ap.add_argument("--add", nargs=2, action="append", default=[],
                    metavar=("URL", "NAME"))
    ap.add_argument("--window-hours", type=int, default=48)
    args = ap.parse_args()

    today = date.today()
    days = max(1, args.window_hours // 24)

    tested: list[dict] = []
    print("CONFIGURED")
    print(f"  {'feed':26} {'state':6} {'parsed':>7} {'window':>7} {'kept':>6} "
          f"{'newest':>11}")
    print("  " + "-" * 68)
    for url, name in FEEDS:
        row = probe(url, name, today, days)
        row["configured"] = True
        tested.append(row)
        show(row)

    if args.candidates:
        print("\nCANDIDATES, not currently used")
        print(f"  {'feed':26} {'state':6} {'parsed':>7} {'window':>7} "
              f"{'kept':>6} {'newest':>11}")
        print("  " + "-" * 68)
        for url, name, tier in CANDIDATE_FEEDS:
            row = probe(url, name, today, days)
            row["configured"] = False
            row["tier"] = tier
            tested.append(row)
            show(row)

    for url, name in args.add:
        row = probe(url, name, today, days)
        row["configured"] = False
        row["tier"] = 3
        tested.append(row)
        show(row)

    report(tested)
    return 0


def show(row: dict) -> None:
    if row["state"] == "dead":
        print(f"  {row['name'][:26]:26} {'DEAD':6} {'-':>7} {'-':>7} "
              f"{'-':>6} {'-':>11}")
        return
    newest = row["newest"].isoformat() if row["newest"] else "unknown"
    label = {"stale": "STALE", "live": "live"}[row["state"]]
    print(f"  {row['name'][:26]:26} {label:6} {row['parsed']:>7} "
          f"{row['window']:>7} {row['kept']:>6} {newest:>11}")


def report(rows: list[dict]) -> None:
    dead = [r for r in rows if r["state"] == "dead"]
    stale = [r for r in rows if r["state"] == "stale"]
    useful = [
        r for r in rows
        if not r.get("configured") and r["state"] == "live" and r["kept"] > 0
    ]
    barren = [
        r for r in rows
        if r.get("configured") and r["state"] == "live" and r["kept"] == 0
    ]

    if dead or stale:
        print("\nRemove from FEEDS in scripts/transferintel/sources.py:")
        for row in dead:
            if row.get("configured"):
                print(f"  {row['name']}: no response at all")
        for row in stale:
            if row.get("configured"):
                newest = row["newest"].isoformat() if row["newest"] else "?"
                print(f"  {row['name']}: responds, but its newest article is "
                      f"{newest}")
        print("  A feed that responds with nothing current is worse than one "
              "that fails:\n  it produces no warning and contributes nothing.")

    if barren:
        print("\nResponding but contributing nothing after filtering: "
              + ", ".join(r["name"] for r in barren))
        print("  Fine on a quiet day. Persistent means it is off topic for "
              "this site.")

    if useful:
        useful.sort(key=lambda r: -r["kept"])
        print("\nWorth adding. Paste into FEEDS:")
        for row in useful:
            print(f'    ("{row["url"]}", "{row["name"]}"),'
                  f'   # {row["kept"]} kept')
        print("\nAnd into DOMAIN_TIER, if not already there:")
        for row in useful:
            host = domain_of(row["url"])
            if host in DOMAIN_TIER:
                continue
            print(f'    "{host}": {row.get("tier", 3)},')
        print("\n  Tier is a standing judgment about an outlet's record, not "
              "a dial to\n  turn until a deal scores well. Only tier 1 and 2 "
              "can move a deal along\n  the status ladder, so a tier 3 "
              "addition adds breadth, never authority.")
    elif any(not r.get("configured") for r in rows):
        print("\nNo candidate is contributing anything. Either a quiet day or "
              "the feeds\nhave moved; try again tomorrow before removing them "
              "from the list.")


if __name__ == "__main__":
    raise SystemExit(main())
