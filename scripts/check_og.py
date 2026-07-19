#!/usr/bin/env python3
"""Check what a social scraper actually sees. A local opengraph.xyz.

    python scripts/check_og.py https://you.github.io/transferintel/
    python scripts/check_og.py https://you.github.io/transferintel/deals/x/

Fetches the page the way a scraper does, reads the Open Graph tags out of the
raw HTML without running any JavaScript, then fetches the declared image and
reports its real status code and dimensions.

Worth having alongside the hosted preview tools because those cache
aggressively: after you fix a card they will keep showing you the old one, and
you cannot tell a stale cache from a broken tag. This never caches.

Exit code 0 if a scraper would render a large image card, 1 otherwise.
"""

from __future__ import annotations

import io
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

# Scrapers do not run scripts and many do not send a browser user agent. Ask
# for the page the same way, so the result is what they would get.
UA = "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)"

WANTED = [
    ("og:title", True),
    ("og:description", True),
    ("og:image", True),
    ("og:url", True),
    ("og:type", False),
    ("og:site_name", False),
    ("twitter:card", True),
    ("twitter:title", False),
    ("twitter:image", False),
]


def fetch(url: str, timeout: int = 20) -> tuple[int, bytes, str]:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(), r.headers.get("Content-Type", "")
    except urllib.error.HTTPError as exc:
        return exc.code, b"", ""
    except Exception as exc:  # noqa: BLE001
        print(f"  could not reach {url}: {exc}")
        return 0, b"", ""


def meta(html: str, key: str) -> str | None:
    attr = "property" if key.startswith("og:") else "name"
    for a, b in ((attr, "content"), ("content", attr)):
        m = re.search(
            rf'<meta[^>]*{a}=["\']{re.escape(key)}["\'][^>]*{b}=["\']([^"\']*)["\']',
            html, re.I,
        ) or re.search(
            rf'<meta[^>]*{b}=["\']([^"\']*)["\'][^>]*{a}=["\']{re.escape(key)}["\']',
            html, re.I,
        )
        if m:
            return m.group(1)
    return None


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    url = sys.argv[1]

    print(f"Fetching {url} as a scraper would\n")
    status, body, ctype = fetch(url)
    if status == 0:
        print("[FAIL] could not reach the host at all")
        print("\n  Nothing was served. Check the domain is right, that Pages")
        print("  has finished deploying, and that you are online. This is not")
        print("  an Open Graph problem yet.")
        return 1
    if status != 200:
        print(f"[FAIL] page returned HTTP {status}")
        if status == 404:
            print("\n  A 404 here means the URL is wrong or the page is not")
            print("  committed. On a project site the path includes the repo")
            print("  name: /transferintel/, not the domain root.")
        return 1
    html = body.decode("utf-8", "replace")
    print(f"[ ok ] HTTP 200, {len(body)} bytes, {ctype}\n")

    problems: list[str] = []
    values: dict[str, str] = {}

    for key, required in WANTED:
        value = meta(html, key)
        if value:
            values[key] = value
            shown = value if len(value) < 78 else value[:75] + "..."
            print(f"[ ok ] {key:<18} {shown}")
        elif required:
            print(f"[FAIL] {key:<18} missing")
            problems.append(f"{key} is missing, so the card will not render properly")
        else:
            print(f"[warn] {key:<18} missing")

    # -- the image, which is what actually goes wrong ---------------------
    print()
    image = values.get("og:image")
    if not image:
        problems.append("No og:image, so the link unfurls as text only")
    else:
        if not image.startswith(("http://", "https://")):
            problems.append(
                f"og:image is {image!r}, which is relative. Scrapers require an "
                "absolute URL. Check config.site.baseUrl."
            )
        img_url = urllib.parse.urljoin(url, image)
        istatus, ibody, ictype = fetch(img_url)
        if istatus != 200:
            shown = "could not be loaded" if istatus == 0 else f"returned HTTP {istatus}"
            print(f"[FAIL] og:image {shown}")
            problems.append(
                f"The image at {img_url} {shown}. This is the usual "
                "cause of a link showing the wrong picture or none: the scraper "
                "cannot load it and falls back to something else on the page, "
                "or to whatever it cached earlier. Commit the og/ directory."
            )
        else:
            print(f"[ ok ] og:image reachable, HTTP 200, {len(ibody)} bytes, {ictype}")
            try:
                from PIL import Image
                with Image.open(io.BytesIO(ibody)) as im:
                    print(f"[ ok ] image is {im.size[0]}x{im.size[1]} {im.format}")
                    if im.size != (1200, 630):
                        problems.append(
                            f"Image is {im.size[0]}x{im.size[1]}. Platforms crop "
                            "from 1200x630; anything else may be cropped oddly."
                        )
            except ImportError:
                print("[warn] install pillow to check the image dimensions")
            except Exception:
                print("[FAIL] the file at og:image is not a readable image")
                problems.append("og:image does not decode as an image")

    # -- canonical and rendered text --------------------------------------
    print()
    canon = re.findall(r'<link[^>]+rel=["\']canonical["\'][^>]+href=["\']([^"\']+)', html, re.I)
    if len(canon) == 1:
        print(f"[ ok ] canonical         {canon[0]}")
    elif not canon:
        print("[warn] canonical         missing")
    else:
        problems.append(f"{len(canon)} canonical tags. There must be exactly one.")

    stripped = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.S | re.I)
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", stripped)).strip()
    print(f"[{'ok' if len(text) > 2000 else 'warn'.rjust(2)}] text without JS     "
          f"{len(text)} characters")
    if len(text) < 2000:
        problems.append(
            f"Only {len(text)} characters are visible without JavaScript. The "
            "page is serving the raw template, so render_site.py has not run "
            "against what is deployed."
        )

    print()
    if problems:
        print("Problems:\n")
        for i, p in enumerate(problems, 1):
            print(f"  {i}. {p}\n")
        return 1

    print("A scraper would render a large image card correctly.")
    print("If a preview tool still shows something else, it is serving a cached")
    print("copy. Force a re-scrape in the Facebook sharing debugger.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
