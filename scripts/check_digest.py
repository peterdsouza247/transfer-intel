#!/usr/bin/env python3
"""What is actually in the Kit account: subscribers, and whether mail went out.

    export NEWSLETTER_API_KEY=...
    python scripts/check_digest.py

The digest reported "sent (201)" for days while nobody received anything,
because `POST /v4/broadcasts` returns 201 Created for a draft exactly as it
does for a send. The status code cannot tell those apart. This asks Kit
directly.

Reports, per recent broadcast, whether it was actually sent, and how many
confirmed subscribers exist to send to. Both are things the digest itself
cannot see: it only knows what the API returned at the moment it posted.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

TIMEOUT = 20


def get(path: str, api_key: str) -> dict | None:
    req = urllib.request.Request(
        f"https://api.kit.com/v4/{path}",
        headers={"Accept": "application/json", "X-Kit-Api-Key": api_key},
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:200]
        print(f"  {path}: HTTP {exc.code} {body}", file=sys.stderr)
    except (urllib.error.URLError, ValueError, TimeoutError, OSError) as exc:
        print(f"  {path}: {type(exc).__name__}: {exc}", file=sys.stderr)
    return None


def main() -> int:
    api_key = os.environ.get("NEWSLETTER_API_KEY", "")
    if not api_key:
        print("NEWSLETTER_API_KEY is not set in this shell.\n"
              "  export NEWSLETTER_API_KEY=...\n"
              "The repository secret and your shell are separate.",
              file=sys.stderr)
        return 1

    print(f"Key found, {len(api_key)} characters.\n")

    # --- subscribers -------------------------------------------------------
    subs = get("subscribers?per_page=1&status=active", api_key)
    if subs is None:
        print("Could not read subscribers. A 401 means the key is wrong or is "
              "a V3 key;\nthis needs a V4 key from Settings, Developer.",
              file=sys.stderr)
        return 1

    total = (subs.get("pagination") or {}).get("total_count")
    if total is None:
        total = len(subs.get("subscribers") or [])
    print(f"Active subscribers: {total}")
    if not total:
        print("  Nobody to send to. A subscriber who has not clicked the\n"
              "  confirmation email counts as unconfirmed, not active, so\n"
              "  check for that before assuming the form is broken.\n")
    else:
        print()

    # --- broadcasts --------------------------------------------------------
    data = get("broadcasts?per_page=10", api_key)
    if data is None:
        return 1

    broadcasts = data.get("broadcasts") or []
    if not broadcasts:
        print("No broadcasts in the account at all. The digest has never "
              "reached Kit.")
        return 0

    print(f"{len(broadcasts)} most recent broadcast(s):\n")
    drafts = 0
    for item in broadcasts:
        subject = (item.get("subject") or "(no subject)")[:52]
        sent_at = item.get("send_at") or item.get("published_at")
        public = item.get("public")
        if sent_at:
            state = f"sent {str(sent_at)[:16]}"
        else:
            state = "DRAFT, never sent"
            drafts += 1
        print(f"  {state:26} public={public!s:5} {subject}")

    if drafts:
        print(f"\n{drafts} broadcast(s) were created and never sent.\n"
              "  That is the old bug: the digest posted without `public` and\n"
              "  `send_at`, so Kit stored a draft and returned 201 Created,\n"
              "  which the digest read as success.\n"
              "  They can be sent by hand from the Kit dashboard, or ignored;\n"
              "  the content is stale either way. Runs from now on will send.")
    else:
        print("\nEvery recent broadcast was sent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
