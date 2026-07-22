#!/usr/bin/env python3
"""Phase 0: one-time migration from data.js to data.json.

    python scripts/migrate_data.py --js data.js --out data.json
    python scripts/migrate_data.py --js data.js --out data.json --write

Three things happen here.

1. `data.js` becomes `data.json`. The JS file does not go away, it just stops
   being the thing you edit: `run_editorial.py --apply` regenerates it from
   the JSON on every run, so `index.html` needs no change at all.

2. Every deal gets a stable `id`. Without one, diffs are ambiguous and any
   pipeline that spells a player's name differently will duplicate the row.

3. Every deal gets a seeded `evidence` entry built from the `src`, `tier`,
   `date` and `status` it already carries. This matters more than it looks:
   with an empty evidence list, the first scoring run sees every deal as
   unsourced and rewrites the whole file. The seed makes day one boring.

Defaults to a dry run. Pass --write to actually create the file.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from transferintel.entities import deal_id  # noqa: E402
from transferintel.models import Deal, Evidence, Status  # noqa: E402
from transferintel.scoring import STATUS_TO_STAGE  # noqa: E402


def parse_with_node(js: str) -> dict | None:
    """Preferred path: let a JS engine read a JS file."""
    script = (
        "let window={};"
        + js
        + ";process.stdout.write(JSON.stringify(window.TRANSFER_DATA))"
    )
    try:
        out = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, timeout=30
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0 or not out.stdout.strip():
        return None
    try:
        return json.loads(out.stdout)
    except json.JSONDecodeError:
        return None


_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f"}
_IDENT_START = re.compile(r"[A-Za-z_$]")
_IDENT = re.compile(r"[A-Za-z0-9_$]")


def _read_js_string(src: str, i: int) -> tuple[str, int]:
    """Read one JS string literal starting at the quote. Returns the decoded
    value and the index just past the closing quote."""
    quote, i = src[i], i + 1
    out: list[str] = []
    while i < len(src):
        c = src[i]
        if c == "\\":
            nxt = src[i + 1]
            if nxt == "u":
                out.append(chr(int(src[i + 2:i + 6], 16)))
                i += 6
                continue
            out.append(_ESCAPES.get(nxt, nxt))
            i += 2
            continue
        if c == quote:
            return "".join(out), i + 1
        out.append(c)
        i += 1
    raise ValueError("unterminated string literal")


def parse_with_python(js: str) -> dict:
    """Fallback for machines without node.

    A scanner rather than a pile of regexes, because the regex version is
    quietly wrong: a single quote inside a double-quoted note ("they've") ends
    up pairing with the next single-quoted string and everything after it
    shifts. This walks the source once, tracking whether it is inside a
    string, and only rewrites JS-isms it finds outside one.
    """
    body = js.split("=", 1)[1].strip().rstrip(";").strip()
    out: list[str] = []
    i, n = 0, len(body)

    while i < n:
        c = body[i]

        if c in "\"'":
            value, i = _read_js_string(body, i)
            out.append(json.dumps(value))
            continue

        if c == "/" and i + 1 < n and body[i + 1] == "/":
            i = body.find("\n", i)
            if i == -1:
                break
            continue

        if c == "/" and i + 1 < n and body[i + 1] == "*":
            end = body.find("*/", i + 2)
            i = n if end == -1 else end + 2
            continue

        if c == ",":
            # Trailing comma before a closing brace or bracket, possibly with
            # whitespace and comments in between.
            j = i + 1
            while j < n:
                if body[j].isspace():
                    j += 1
                elif body.startswith("//", j):
                    nl = body.find("\n", j)
                    j = n if nl == -1 else nl + 1
                elif body.startswith("/*", j):
                    end = body.find("*/", j + 2)
                    j = n if end == -1 else end + 2
                else:
                    break
            if j < n and body[j] in "}]":
                i = j
                continue
            out.append(c)
            i += 1
            continue

        if _IDENT_START.match(c):
            j = i
            while j < n and _IDENT.match(body[j]):
                j += 1
            word = body[i:j]
            k = j
            while k < n and body[k].isspace():
                k += 1
            if k < n and body[k] == ":" and word not in ("true", "false", "null"):
                out.append(json.dumps(word))   # bare key
            else:
                out.append(word)
            i = j
            continue

        out.append(c)
        i += 1

    return json.loads("".join(out))


def parse_display_date(raw: str, fallback: date) -> date:
    """`"Jul 15"` has no year. Assume the most recent occurrence not in the
    future, which is right for every in-window date and harmless otherwise.

    The year is supplied to `strptime` rather than patched on afterwards.
    Parsing without one silently defaults to 1900, which Python 3.15 will stop
    allowing, and which was already wrong for one date: 1900 was not a leap
    year, so "Feb 29" raised `day is out of range for month` and fell through
    to the fallback even in a year where the date exists.
    """
    raw = (raw or "").strip()
    if not raw:
        return fallback
    for year in (fallback.year, fallback.year - 1):
        for fmt in ("%b %d", "%d %b", "%B %d"):
            try:
                candidate = datetime.strptime(
                    f"{raw} {year}", f"{fmt} %Y"
                ).date()
            except ValueError:
                continue
            if candidate <= fallback:
                return candidate
    return fallback


def seed_evidence(raw: dict, did: str, today: date) -> dict:
    """One synthetic evidence item standing in for the reporting that already
    justified this row. The urn scheme makes it obvious in the data that this
    was migrated, not fetched."""
    status = Status(raw.get("status", "rumor"))
    return Evidence(
        url=f"urn:transferintel:seed:{did}",
        source=raw.get("src") or "migrated",
        tier=int(raw.get("tier") or 3),
        date=parse_display_date(raw.get("date", ""), today),
        claim=STATUS_TO_STAGE[status],
        fee_gbp_m=float(raw["fee"]) if raw.get("fee") not in (None, "") else None,
    ).model_dump(mode="json")


def migrate(data: dict, today: date) -> tuple[dict, list[str]]:
    problems: list[str] = []
    seen: dict[str, int] = {}

    for raw in data.get("deals", []):
        if not raw.get("id"):
            base = deal_id(raw.get("p", ""), raw.get("from", ""), raw.get("to", ""))
            seen[base] = seen.get(base, 0) + 1
            raw["id"] = base if seen[base] == 1 else f"{base}-{seen[base]}"
        if not raw.get("evidence"):
            raw["evidence"] = [seed_evidence(raw, raw["id"], today)]
        raw.setdefault("note", "")

        try:
            Deal(**raw)
        except ValueError as exc:
            problems.append(f"{raw.get('id', raw.get('p', '?'))}: {exc}")

    known = set(data.get("clubs", {}))
    for raw in data.get("deals", []):
        for side in ("from", "to"):
            if raw.get(side) not in known:
                problems.append(
                    f"{raw.get('id')}: club {raw.get(side)!r} has no entry in "
                    "clubs, its dashboard will be empty"
                )
    return data, problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--js", type=Path, default=Path("data.js"))
    ap.add_argument("--out", type=Path, default=Path("data.json"))
    ap.add_argument("--today", default=None)
    ap.add_argument("--write", action="store_true",
                    help="actually write the file, otherwise dry run")
    args = ap.parse_args()

    today = date.fromisoformat(args.today) if args.today else date.today()
    js = args.js.read_text(encoding="utf-8")

    data = parse_with_node(js)
    how = "node"
    if data is None:
        try:
            data = parse_with_python(js)
            how = "python fallback"
        except (json.JSONDecodeError, IndexError) as exc:
            print(f"Could not parse {args.js}: {exc}", file=sys.stderr)
            return 1

    data, problems = migrate(data, today)
    deals = data.get("deals", [])
    print(f"Parsed {len(deals)} deals with {how}.")
    print(f"Ids assigned, evidence seeded for {len(deals)} deals.")

    # The one value the renderer cannot guess. Canonical tags, Open Graph
    # URLs and the sitemap all need an absolute origin, and a wrong one is
    # worse than none: it tells crawlers the real page is somewhere else.
    config = data.setdefault("config", {})
    site_cfg = config.setdefault("site", {})
    if not site_cfg.get("baseUrl"):
        site_cfg.setdefault("baseUrl", "")
        site_cfg.setdefault("title", "TransferIntel")
        site_cfg.setdefault("twitter", "")
        problems.append(
            "config.site.baseUrl is empty. Set it to the live origin, for "
            "example https://<username>.github.io/transferintel, before "
            "running render_site.py."
        )

    if problems:
        print(f"\n{len(problems)} things to look at before you commit:")
        for p in problems:
            print(f"  - {p}")

    if not args.write:
        print("\nDry run. Re-run with --write to create the file.")
        print("Sample:")
        print(json.dumps(deals[0] if deals else {}, indent=2)[:700])
        return 0

    args.out.write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"\nWrote {args.out}. data.js is now a generated artifact: "
          "run_editorial.py --apply rebuilds it, so keep editing the JSON.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
