#!/usr/bin/env python3
"""Build and optionally send the daily digest.

    python scripts/run_digest.py --data data.json --out build/digest   # build
    python scripts/run_digest.py --data data.json --out build/digest --send

Without `--send` this writes every edition to disk and touches no network, so
you can read tomorrow's email today. That is the mode to use for the first
week: a digest that goes out wrong cannot be recalled, and the failure mode of
reading it first is that you spent two minutes.

Sending is deliberately a thin shim over the provider's HTTP API rather than
an SDK. Both recommended providers (Kit and Buttondown) accept a POST with a
subject and a markdown body, the whole integration is thirty lines, and an
SDK would be one more dependency to break on a runner in eighteen months.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from transferintel import digest as dg  # noqa: E402
from transferintel.models import Deal  # noqa: E402

TIMEOUT = 30


def post_json(url: str, payload: dict, headers: dict) -> tuple[int, str]:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": "application/json", **headers,
    })
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")[:400]
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")[:400]
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return 0, str(exc)


def send_edition(provider: str, api_key: str, subject: str, body: str,
                 segment: str) -> tuple[int, str]:
    """One edition to one provider. Returns (status, message).

    Segment targeting differs between providers and both change their APIs
    from time to time, so the tag is passed as a plain string and the failure
    is reported rather than raised: a digest that fails to send for Newcastle
    must not stop the one for everybody else.
    """
    if provider == "buttondown":
        return post_json(
            "https://api.buttondown.email/v1/emails",
            {"subject": subject, "body": body,
             "tags": [] if segment == "all" else [segment]},
            {"Authorization": f"Token {api_key}"},
        )
    if provider == "kit":
        return post_json(
            "https://api.kit.com/v4/broadcasts",
            {"subject": subject, "content": body,
             "email_template_id": None,
             "subscriber_filter": None if segment == "all"
             else {"all": [{"type": "tag", "name": segment}]}},
            {"X-Kit-Api-Key": api_key},
        )
    return 0, f"unknown provider {provider!r}, nothing sent"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, default=Path("data.json"))
    ap.add_argument("--out", type=Path, default=Path("build/digest"))
    ap.add_argument("--state", type=Path, default=Path("logs/digest-state.json"),
                    help="yesterday's snapshot, written back after every run")
    ap.add_argument("--sent-log", type=Path, default=Path("logs/digest-sent.json"))
    ap.add_argument("--today", default=None)
    ap.add_argument("--send", action="store_true",
                    help="actually call the provider API")
    ap.add_argument("--segments", action="store_true",
                    help="also build per-club editions (TI-011)")
    ap.add_argument("--force", action="store_true",
                    help="send even if today is already in the sent log")
    args = ap.parse_args()

    today = date.fromisoformat(args.today) if args.today else date.today()
    raw = json.loads(args.data.read_text(encoding="utf-8"))
    deals = [Deal(**d) for d in raw["deals"]]
    config = raw.get("config") or {}
    base_url = ((config.get("site") or {}).get("baseUrl") or "").rstrip("/")
    window_name = config.get("windowName", "")
    news = config.get("newsletter") or {}
    provider = news.get("provider", "")

    previous: dict[str, dict] = {}
    if args.state.exists():
        try:
            previous = json.loads(args.state.read_text(encoding="utf-8"))
        except ValueError:
            previous = {}

    changes = dg.changes_since(deals, previous, today)
    clubs = sorted(raw.get("clubs", {}))
    editions = dg.build_editions(changes, today, clubs)
    if not args.segments:
        editions = editions[:1]

    args.out.mkdir(parents=True, exist_ok=True)
    written = []
    for edition in editions:
        body = dg.render_markdown(edition, base_url, window_name)
        subject = dg.render_subject(edition)
        slug = "all" if edition.segment == "all" else edition.segment.lower().replace(" ", "-")
        path = args.out / f"{today.isoformat()}-{slug}.md"
        path.write_text(f"<!-- subject: {subject} -->\n\n{body}", encoding="utf-8")
        written.append((edition, subject, body, path))

    alerts = dg.instant_alerts(changes)
    (args.out / "alerts.json").write_text(
        json.dumps([{"deal": c.deal.id, "kind": c.kind, "detail": c.detail}
                    for c in alerts], indent=2),
        encoding="utf-8",
    )

    print(f"{len(changes)} changes, {len(written)} editions, "
          f"{len(alerts)} instant alerts. Written to {args.out}")
    for edition, subject, _, path in written:
        flag = " (quiet)" if edition.is_quiet else ""
        print(f"  {path.name}: {subject}{flag}")

    if not args.send:
        print("Dry run. Nothing sent. Re-run with --send to deliver.")
        # The snapshot is still written: the next dry run should show the day's
        # changes once, not the same ones forever.
        args.state.parent.mkdir(parents=True, exist_ok=True)
        args.state.write_text(json.dumps(dg.snapshot(deals), indent=2),
                              encoding="utf-8")
        return 0

    if dg.already_sent(args.sent_log, today) and not args.force:
        print(f"{today} is already in {args.sent_log}. Nothing sent.",
              file=sys.stderr)
        return 0

    api_key = os.environ.get("NEWSLETTER_API_KEY", "")
    if not api_key or not provider:
        print("NEWSLETTER_API_KEY or config.newsletter.provider is missing. "
              "Editions were written but nothing was sent.", file=sys.stderr)
        return 1

    failures = 0
    for edition, subject, body, _ in written:
        status, message = send_edition(
            provider, api_key, subject, body, edition.segment
        )
        ok = 200 <= status < 300
        print(f"  {edition.segment}: {'sent' if ok else 'FAILED'} ({status}) {message[:120]}")
        failures += 0 if ok else 1

    if failures == 0:
        dg.mark_sent(args.sent_log, today)
    args.state.parent.mkdir(parents=True, exist_ok=True)
    args.state.write_text(json.dumps(dg.snapshot(deals), indent=2),
                          encoding="utf-8")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
