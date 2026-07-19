#!/usr/bin/env python3
"""
TransferIntel mechanical data refresher.

Checks in-progress deals (rumor/talks/agreed/medical) in data.js against:

  1. PRIMARY: Wikipedia's "List of English football transfers summer 2026"
     page via the Wikimedia REST API. Reliable from GitHub Actions runners,
     updated fast by editors, and fees are listed in GBP.
  2. FALLBACK: the unofficial Transfermarkt API (transfermarkt-api.fly.dev).
     Often blocked from datacenter IPs, so treat it as best-effort only.

If a player's move to the expected club is confirmed, the deal is promoted
to "done" and the fee updated.

This script only automates the MECHANICAL layer (statuses and fees). It
never touches the editorial layer (credibility scores, notes, club needs).

Fail-soft by design: any problem is logged and skipped, and the script
always exits 0 so the GitHub Action never breaks the site.

When a new window starts, update WIKI_PAGE below (one line).
"""

import datetime
import html
import json
import re
import sys
import time
import unicodedata
import urllib.parse
import urllib.request

DATA_FILE = "data.js"
WIKI_PAGE = "List_of_English_football_transfers_summer_2026"
WIKI_API = "https://en.wikipedia.org/api/rest_v1/page/html/"
TM_API = "https://transfermarkt-api.fly.dev"
EUR_TO_GBP = 0.86
IN_PROGRESS = {"rumor", "talks", "agreed", "medical"}

# data.js short names -> strings to look for in Wikipedia/Transfermarkt text
CLUB_ALIASES = {
    "man utd": ["manchester united"],
    "man city": ["manchester city"],
    "nott'm forest": ["nottingham forest"],
    "tottenham": ["tottenham hotspur", "tottenham"],
    "newcastle": ["newcastle united", "newcastle"],
    "brighton": ["brighton & hove albion", "brighton and hove albion", "brighton"],
    "west ham": ["west ham united", "west ham"],
    "wolves": ["wolverhampton wanderers", "wolves"],
    "leeds": ["leeds united", "leeds"],
}


def norm(s):
    """Lowercase and strip accents so 'Vušković' matches 'Vuskovic'."""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s.lower()).strip()


def club_needles(club):
    n = norm(club)
    return [norm(a) for a in CLUB_ALIASES.get(n, [])] or [n]


def fetch(url, as_json=True):
    req = urllib.request.Request(url, headers={
        "User-Agent": "TransferIntelBot/1.0 (github pages hobby site)"})
    with urllib.request.urlopen(req, timeout=30) as r:
        body = r.read().decode("utf-8", "replace")
    return json.loads(body) if as_json else body


def load_data():
    raw = open(DATA_FILE, encoding="utf-8").read()
    m = re.search(r"window\.TRANSFER_DATA\s*=\s*(\{.*\});?\s*$", raw, re.S)
    if not m:
        print("Could not parse data.js")
        sys.exit(0)
    return json.loads(m.group(1))


def save_data(data):
    body = json.dumps(data, ensure_ascii=False, indent=2)
    open(DATA_FILE, "w", encoding="utf-8").write(
        f"window.TRANSFER_DATA = {body};\n")


def display_date(d):
    return d.strftime("%b %d").replace(" 0", " ")


# ---------------------------------------------------------------- Wikipedia

def wiki_rows():
    """Return the transfer list page as a list of normalized table-row texts."""
    try:
        page = fetch(WIKI_API + urllib.parse.quote(WIKI_PAGE), as_json=False)
    except Exception as e:  # noqa: BLE001
        print(f"wikipedia unavailable: {e}")
        return []
    rows = []
    for row_html in re.split(r"<tr[ >]", page)[1:]:
        row_html = row_html.split("</tr>")[0]
        text = re.sub(r"<[^>]+>", " ", row_html)
        rows.append(html.unescape(text))
    print(f"wikipedia: {len(rows)} table rows loaded")
    return rows


def check_wikipedia(deal, rows):
    """Look for a row naming both the player and the destination club."""
    p = norm(deal["p"])
    needles = club_needles(deal["to"])
    for text in rows:
        t = norm(text)
        if p in t and any(n in t for n in needles):
            fee = None
            m = re.search(r"£\s*([\d.]+)\s*(m|million)", t)
            if m:
                fee = float(m.group(1))
            elif re.search(r"free transfer|free\b", t):
                fee = 0.0
            m = re.search(r"(\d{1,2})\s+(january|february|march|april|may|june|"
                          r"july|august|september|october|november|december)", t)
            date = None
            if m:
                try:
                    date = datetime.datetime.strptime(
                        f"{m.group(1)} {m.group(2)} {datetime.date.today().year}",
                        "%d %B %Y").date()
                except ValueError:
                    pass
            return {"fee": fee, "date": date}
    return None


# ------------------------------------------------------------- Transfermarkt

def check_transfermarkt(deal):
    q = urllib.parse.quote(deal["p"])
    hits = fetch(f"{TM_API}/players/search/{q}").get("results", [])
    if not hits:
        return None
    pid = hits[0]["id"]
    time.sleep(1.5)  # be polite to the shared instance
    transfers = fetch(f"{TM_API}/players/{pid}/transfers").get("transfers", [])
    today = datetime.date.today()
    needles = club_needles(deal["to"])
    for t in transfers:
        to_club = norm((t.get("clubTo") or {}).get("name", ""))
        try:
            d = datetime.date.fromisoformat(t.get("date", ""))
        except ValueError:
            continue
        if abs((today - d).days) > 60 or t.get("upcoming"):
            continue
        if any(n in to_club for n in needles):
            fee = t.get("fee")  # integer, euros
            fee_gbp = round(fee / 1e6 * EUR_TO_GBP, 1) if fee else None
            return {"fee": fee_gbp, "date": d}
    return None


# ------------------------------------------------------------------- main

def main():
    data = load_data()
    changed = False
    today = datetime.date.today()
    rows = wiki_rows()

    for deal in data["deals"]:
        if deal["status"] not in IN_PROGRESS:
            continue
        hit = None
        if rows:
            hit = check_wikipedia(deal, rows)
        if hit is None:
            try:
                hit = check_transfermarkt(deal)
                time.sleep(1.5)
            except Exception as e:  # noqa: BLE001
                print(f"transfermarkt skip {deal['p']}: {e}")
        if hit:
            deal["status"] = "done"
            deal["cred"] = 100
            if hit["fee"] is not None:
                deal["fee"] = hit["fee"]
            deal["date"] = display_date(hit["date"] or today)
            deal["note"] = (f"Confirmed (auto-updated {today.isoformat()}). "
                            + deal["note"])
            print(f"PROMOTED: {deal['p']} -> {deal['to']} (£{deal['fee']}m)")
            changed = True

    if changed:
        data["config"]["updated"] = today.strftime("%d %B %Y").lstrip("0")
        save_data(data)
        print("data.js updated")
    else:
        print("no changes")


if __name__ == "__main__":
    main()
