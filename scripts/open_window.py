#!/usr/bin/env python3
"""Freeze the current window, then start the next one with an empty deal list.

Example:
    python scripts/open_window.py --slug 2027-winter \
      --name "Premier League · Winter Window 2027" \
      --deadline 2027-02-01T23:00:00Z \
      --deadline-label "Window closes 1 Feb, 23:00 GMT"

Review the resulting git diff and publish it through the normal PR workflow.
"""
from __future__ import annotations

import argparse
import html
import json
import re
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from transferintel import site


ROOT = Path(__file__).resolve().parent.parent
RENDER = ROOT / "scripts" / "render_site.py"


def render(data: Path, destination: Path, template: Path) -> None:
    subprocess.run([
        sys.executable, str(RENDER), "--data", str(data), "--template",
        str(template), "--out", str(destination),
    ], check=True, cwd=ROOT)


def open_window(slug: str, name: str, deadline: str, deadline_label: str) -> None:
    if not re.fullmatch(r"[0-9]{4}-(winter|summer)", slug):
        raise ValueError("slug must be YYYY-winter or YYYY-summer")
    try:
        parsed = datetime.fromisoformat(deadline.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("deadline must be an ISO date-time with timezone") from exc
    if parsed.tzinfo is None or parsed.astimezone(timezone.utc) <= datetime.now(timezone.utc):
        raise ValueError("deadline needs a timezone and must be in the future")
    if not name.strip() or not deadline_label.strip():
        raise ValueError("name and deadline-label are required")

    data_path = ROOT / "data.json"
    raw = json.loads(data_path.read_text(encoding="utf-8"))
    old_deals = raw["deals"]
    config = raw["config"]
    old_slug = config.get("windowSlug")
    if not old_slug or not re.fullmatch(r"[0-9]{4}-(winter|summer)", old_slug):
        raise ValueError("set config.windowSlug for the current window first")
    if slug == old_slug or any(a["slug"] == slug for a in config.get("archives", [])):
        raise ValueError(f"window {slug} already exists")
    base = config["site"]["baseUrl"].rstrip("/")
    if "/windows/" in base or config.get("archived"):
        raise ValueError("data.json must describe the current root site")
    archive = ROOT / "windows" / old_slug
    archive.mkdir(parents=True, exist_ok=True)
    frozen = json.loads(json.dumps(raw))
    frozen["config"]["archived"] = True
    frozen["config"]["site"]["baseUrl"] = f"{base}/windows/{old_slug}"
    (archive / "data.json").write_text(
        json.dumps(frozen, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    render(archive / "data.json", archive, ROOT / "index.html")

    archives = config.setdefault("archives", [])
    if not any(a["slug"] == old_slug for a in archives):
        archives.insert(0, {"slug": old_slug, "name": config["windowName"]})
    config["windowSlug"] = slug
    config["windowName"] = name.strip()
    config["deadline"] = parsed.isoformat().replace("+00:00", "Z")
    config["deadlineLabel"] = deadline_label.strip()
    config["updated"] = date.today().strftime("%b %-d, %Y")
    config["provenClubs"] = []
    raw["deals"] = []
    # Club labels remain available, but the previous season's analysis is
    # historical and must not be presented as advice for the new window.
    raw["clubs"] = {club: {} for club in raw.get("clubs", {})}
    data_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n",
                         encoding="utf-8")
    render(data_path, ROOT, ROOT / "index.html")
    # Links shared during the old window keep working and lead to the
    # immutable record, rather than showing an out-of-season live page.
    for deal in old_deals:
        deal_path = site.slugify(deal["id"])
        old_page = ROOT / "deals" / deal_path / "index.html"
        target = f"{base}/windows/{old_slug}/deals/{deal_path}/"
        old_page.parent.mkdir(parents=True, exist_ok=True)
        escaped = html.escape(target, quote=True)
        old_page.write_text(
            '<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<link rel="canonical" href="{escaped}">'
            f'<meta http-equiv="refresh" content="0;url={escaped}">'
            f'<title>Moved to archive</title></head><body><a href="{escaped}">'
            'View this transfer in the archive</a></body></html>\n',
            encoding="utf-8",
        )
    print(f"Opened {name}. Previous window: windows/{old_slug}/")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--slug", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--deadline", required=True)
    parser.add_argument("--deadline-label", required=True)
    args = parser.parse_args()
    try:
        open_window(args.slug, args.name, args.deadline, args.deadline_label)
    except (ValueError, subprocess.CalledProcessError) as exc:
        parser.error(str(exc))
