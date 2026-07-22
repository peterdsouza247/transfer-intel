#!/usr/bin/env python3
"""Why are my SEO files not there?

    python scripts/check_site.py

Run it from the repo root. It reports what exists, what does not, and what to
do about each thing, rather than making you infer it from a missing file.

Nothing here writes anything. It is safe to run at any time.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlsplit

GENERATED = [
    ("sitemap.xml", "every URL with a lastmod"),
    ("robots.txt", "crawl rules and the sitemap pointer"),
    ("feed.xml", "RSS of tracked deals"),
    ("llms.txt", "plain-text site map for assistants"),
    ("favicon.svg", "tab icon"),
    ("404.html", "served automatically by GitHub Pages"),
]

OK = "  ok  "
BAD = " MISS "
WARN = " warn "


def main() -> int:
    root = Path.cwd()
    print(f"Checking {root}\n")
    problems: list[str] = []

    # -- inputs ----------------------------------------------------------
    data = root / "data.json"
    index = root / "index.html"

    if not index.exists():
        print(f"[{BAD}] index.html")
        problems.append(
            "No index.html here. You are probably not in the repo root. The "
            "renderer writes next to your site, so cd to the directory that "
            "contains index.html and run it again."
        )
        print()
        for message in problems:
            print(f"  - {message}")
        return 1
    print(f"[{OK}] index.html")

    if not data.exists():
        print(f"[{BAD}] data.json")
        problems.append(
            "No data.json. Run phase 0 first:\n"
            "      python scripts/migrate_data.py --js data.js --out data.json --write"
        )
    else:
        print(f"[{OK}] data.json")
        raw = json.loads(data.read_text(encoding="utf-8"))
        base = ((raw.get("config") or {}).get("site") or {}).get("baseUrl", "")
        if not base:
            print(f"[{BAD}] config.site.baseUrl")
            problems.append(
                "config.site.baseUrl is empty, so render_site.py exits 2 and "
                "writes nothing at all. That is almost certainly why the files "
                "are missing. Set it in data.json:\n"
                '      "site": { "baseUrl": "https://you.github.io/transferintel" }'
            )
        elif base.endswith("/"):
            print(f"[{WARN}] config.site.baseUrl has a trailing slash")
            problems.append(
                f"baseUrl is {base!r}. Drop the trailing slash or every "
                "generated URL gets a double slash."
            )
        else:
            print(f"[{OK}] config.site.baseUrl = {base}")

            # The single easiest thing to get wrong, and it fails silently:
            # every canonical, og:image and sitemap entry points at a path
            # that does not exist, so scrapers 404 and fall back to some
            # other image. The repo name is the truth, so compare against it.
            import subprocess
            try:
                remote = subprocess.run(
                    ["git", "remote", "get-url", "origin"],
                    capture_output=True, text=True, timeout=5,
                ).stdout.strip()
            except Exception:
                remote = ""
            if remote:
                repo = re.sub(r"\.git$", "", remote.rstrip("/").rsplit("/", 1)[-1])
                owner = ""
                m = re.search(r"[:/]([^/:]+)/[^/]+?(?:\.git)?$", remote)
                if m:
                    owner = m.group(1)
                path = urlsplit(base).path.strip("/")
                host = urlsplit(base).netloc

                if repo.lower().endswith(".github.io"):
                    pass  # user site, served at the domain root
                elif path.lower() != repo.lower():
                    print(f"[{BAD}] baseUrl path {path!r} != repo name {repo!r}")
                    problems.append(
                        f"baseUrl ends in /{path} but the repository is named "
                        f"{repo!r}. GitHub Pages serves a project site at "
                        f"/{repo}/, so every canonical, og:image and sitemap "
                        f"URL currently points at a path that does not exist. "
                        f"Scrapers get a 404 and fall back to another image, "
                        f"which is why a link preview can show a card that is "
                        f"not yours. Set it to:\n"
                        f"      https://{owner or 'YOURNAME'}.github.io/{repo}"
                    )
                else:
                    print(f"[{OK}] baseUrl path matches the repo name {repo!r}")
                if owner and host and not host.lower().startswith(owner.lower()):
                    print(f"[{WARN}] baseUrl host {host!r} does not match owner {owner!r}")

    # -- generated output -------------------------------------------------
    print()
    missing = []
    for name, what in GENERATED:
        if (root / name).exists():
            print(f"[{OK}] {name:<12} {what}")
        else:
            print(f"[{BAD}] {name:<12} {what}")
            missing.append(name)

    deals = list((root / "deals").glob("*/index.html")) if (root / "deals").exists() else []
    clubs = list((root / "clubs").glob("*/index.html")) if (root / "clubs").exists() else []
    og = list((root / "og").glob("*.png")) if (root / "og").exists() else []
    print(f"[{OK if deals else BAD}] deals/       {len(deals)} pages")
    print(f"[{OK if clubs else BAD}] clubs/       {len(clubs)} pages")
    print(f"[{OK if og else BAD}] og/          {len(og)} images")

    if missing and not problems:
        problems.append(
            "The inputs are fine but nothing has been generated here yet. "
            "Run it, and note the `--out .` which puts the files in this "
            "directory rather than a preview folder:\n"
            "      python scripts/render_site.py --data data.json "
            "--template index.html --out ."
        )

    # -- is the index actually pre-rendered? ------------------------------
    print()
    html = index.read_text(encoding="utf-8")
    if "<!-- generated by scripts/render_site.py" in html:
        body = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.S | re.I)
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body))
        print(f"[{OK}] index.html carries the generated head block")
        print(f"[{OK}] {len(text)} characters visible without JavaScript")
    else:
        print(f"[{BAD}] index.html has no generated block, it is still the raw template")

    # -- committed? --------------------------------------------------------
    gitignore = root / ".gitignore"
    if gitignore.exists():
        ignored = gitignore.read_text(encoding="utf-8")
        for name in ("sitemap.xml", "robots.txt", "og/", "deals/", "clubs/"):
            if re.search(rf"^{re.escape(name)}", ignored, re.M):
                problems.append(
                    f".gitignore has a rule for {name}. Generated pages have to "
                    "be committed, because GitHub Pages serves what is in the "
                    "repo. Remove that rule."
                )

    print()
    if problems:
        print("What to do:\n")
        for i, message in enumerate(problems, 1):
            print(f"  {i}. {message}\n")
        return 1

    print("All generated. If the live site still 404s on /sitemap.xml:\n")
    print("  1. Commit and push them. Pages serves what is in the repo, and")
    print("     these files are build output that has to be checked in.")
    print("  2. Wait for the Pages deploy to finish, then hard refresh.")
    print("  3. Check the URL matches your baseUrl. A project site serves them")
    print("     at /<repo>/sitemap.xml, not at the domain root.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
