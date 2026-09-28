#!/usr/bin/env python3
"""Build an auditable source index package from one transfer-window snapshot."""

from __future__ import annotations

import argparse
import csv
import html
import json
from pathlib import Path

from transferintel.models import Deal
from transferintel.source_stats import (
    POSITIVE_CLAIMS, canonical_attributions, resolution_date, source_records,
)


def window_order(slug: str) -> tuple[int, int]:
    year, season = slug.split("-", 1)
    return int(year), {"winter": 0, "summer": 1}[season]


def label(value: object) -> str:
    return str(getattr(value, "value", value))


def safe_cell(value: object) -> object:
    """Avoid spreadsheet formula execution when a CSV is opened by a reader."""
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def write_csv(path: Path, fields: list[str], rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows({key: safe_cell(row.get(key, "")) for key in fields}
                         for row in rows)


def build(data_paths: list[Path], out: Path) -> None:
    windows = []
    names = set()
    for path in data_paths:
        raw = json.loads(path.read_text(encoding="utf-8"))
        cfg = raw.get("config", {})
        name = cfg.get("windowName") or path.parent.name
        if name in names:
            raise ValueError(f"Duplicate window {name!r}; do not include both its current and archived snapshot")
        names.add(name)
        windows.append((name, cfg, [Deal(**item) for item in raw["deals"]]))
    if not windows:
        raise ValueError("At least one window is required")
    # Source statistics key calls by deal.id; namespace IDs across windows so
    # the same transfer route in two windows remains two independent calls.
    scoped = [deal.model_copy(update={"id": f"{name}::{deal.id}"})
              for name, _, deals in windows for deal in deals]
    records = source_records(scoped)
    cfg = windows[0][1]
    site = (cfg.get("site") or {}).get("baseUrl", "").rstrip("/").split("/windows/", 1)[0]
    title = windows[0][0] if len(windows) == 1 else f"{windows[0][0]} to {windows[-1][0]} ({len(windows)} windows)"
    total_deals = sum(len(deals) for _, _, deals in windows)
    snapshots = "; ".join(f"{name}: {config.get('updated', 'unknown')}" for name, config, _ in windows)
    out.mkdir(parents=True, exist_ok=True)

    calls: list[dict] = []
    for window_name, window_config, window_deals in windows:
        window_site = (window_config.get("site") or {}).get("baseUrl", "").rstrip("/")
        for deal in window_deals:
            cutoff = resolution_date(deal)
            earliest: dict[tuple[str, str], object] = {}
            for evidence in deal.evidence:
                if label(evidence.claim) not in POSITIVE_CLAIMS:
                    continue
                if cutoff is not None and evidence.date >= cutoff:
                    continue
                for key in canonical_attributions(evidence.source):
                    previous = earliest.get(key)
                    if previous is None or (evidence.date, evidence.url) < (previous.date, previous.url):
                        earliest[key] = evidence
            for (kind, name), evidence in earliest.items():
                status = label(deal.status)
                outcome = "hit" if status == "done" else (
                    "miss" if status == "collapsed" else "unresolved"
                )
                calls.append({
                "window": window_name, "source_type": kind,
                "source": name, "deal_id": deal.id, "player": deal.p,
                "from_club": deal.from_club, "to_club": deal.to,
                "first_call_date": evidence.date.isoformat(),
                "first_claim": label(evidence.claim),
                "evidence_url": evidence.url,
                "evidence_type": "seed" if evidence.url.startswith("urn:") else "web",
                "resolution_date": cutoff.isoformat() if cutoff else "",
                "deal_status": status, "outcome": outcome,
                "deal_url": f"{window_site}/deals/{deal.id}/" if window_site else "",
                })
    calls.sort(key=lambda x: (x["source_type"], x["source"].casefold(), x["deal_id"]))

    summary = [{
        "window": title, "source_type": kind,
        "source": record.name, "credibility_score": record.credibility,
        "raw_hit_rate_pct": record.hit_rate, "hits": record.hits,
        "misses": record.misses, "resolved": record.resolved,
        "unresolved": record.unresolved, "total_calls": record.calls,
        "sample_flag": "small_sample" if record.resolved < 5 else "5_plus_resolved",
    } for kind in ("journalist", "publication") for record in records[kind]]
    lookup = {(r["source_type"], r["source"]): r for r in summary}
    for key, record in lookup.items():
        evidence = [call for call in calls if (call["source_type"], call["source"]) == key]
        assert len(evidence) == record["total_calls"], key
        assert sum(call["outcome"] == "hit" for call in evidence) == record["hits"], key
        assert sum(call["outcome"] == "miss" for call in evidence) == record["misses"], key

    write_csv(out / "source-summary.csv", list(summary[0]) if summary else [
        "window", "source_type", "source", "credibility_score", "raw_hit_rate_pct",
        "hits", "misses", "resolved", "unresolved", "total_calls", "sample_flag",
    ], summary)
    write_csv(out / "source-calls.csv", list(calls[0]) if calls else [
        "window", "source_type", "source", "deal_id", "player", "from_club",
        "to_club", "first_call_date", "first_claim", "evidence_url",
        "evidence_type", "resolution_date", "deal_status", "outcome", "deal_url",
    ], calls)

    resolved = sum(r["resolved"] for r in summary)
    unresolved = sum(r["unresolved"] for r in summary)
    seed_count = sum(call["evidence_type"] == "seed" for call in calls)
    named = len(records["journalist"])
    named_label = "writer" if named == 1 else "writers"
    report = f"""# TransferIntel source credibility: {title}

Snapshots: {snapshots} · {total_deals} tracked window/deal records · {len(summary)} attributed sources ({named} named {named_label}, {len(records['publication'])} publications).

## What the data says

There are **{resolved} resolved source calls** and **{unresolved} unresolved calls**. The latter are excluded from accuracy. A source call is a positive transfer claim linked before resolution, counted once per source and deal. Multiple sources can be credited on one deal, so these totals are not counts of distinct transfers.

This is an **exploratory sample**, not a league-wide ranking of reporters. {sum(r['resolved'] >= 5 for r in summary)} sources have at least five resolved calls in this snapshot; the named-journalist sample covers only {named} explicitly attributed {named_label}. Do not present a one-call score as a proven reliability rating. The data reflects tracked deals and recorded evidence, not every story each outlet published.

## How to read the files

- `source-summary.csv`: one row per identified source, with hits, misses, pending calls, raw hit rate and confidence-adjusted score. Blank scores mean no resolved calls. `small_sample` flags fewer than five resolved calls.
- `source-calls.csv`: one row per source and deal, with the **earliest eligible** claim, outcome, resolution date and supporting URL. {seed_count} calls point to internal `urn:` seed references rather than independently retrievable web articles; `evidence_type` marks them for audit. These rows need a retrievable primary link before being pitched as independently verifiable evidence.
- The site’s [sortable source index]({site}/) presents the same calculation.

## Method

Only claims of interest, talks, agreement or medical progress count. Claims dated on or after a deal’s resolution are excluded. A completed transfer is a hit; a collapsed tracked move is a miss; all other statuses remain unresolved. Raw hit rate = hits / resolved calls. Credibility score = round(100 × (hits + 2) / (resolved + 4)). This neutral two-hit, two-miss prior tempers small samples. The score describes **this recorded sample**; it is not a probability that the next story is true.

Attribution labels are normalized by the site’s `source_stats.py`. A publication and an explicitly named journalist can each get credit for the same deal. A public report should show both the denominator and unresolved count whenever quoting a score.

## Reuse and enquiries

These exports are derived from the public transfer dataset under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/); credit TransferIntel and link to {site}/. Third-party articles remain with their publishers. Existing public data can be reused commercially with attribution. For commissioned research, expanded coverage, tailored exports or regular delivery, contact **{cfg.get('contactEmail', 'peterbrendanwrites@gmail.com')}**. No exclusive rights to this public snapshot are implied.

Available on enquiry: a window report with methodology and source receipts; a custom audit of selected sources or clubs; or scheduled CSV delivery across new windows. Send the desired scope, format and deadline to **peterbrendanwrites@gmail.com**. Coverage and fees are agreed for each project.
"""
    (out / "README.md").write_text(report, encoding="utf-8")
    table_rows = "\n".join(
        "<tr>" + "".join(f"<td>{html.escape(str(value if value is not None else '—'))}</td>"
                           for value in (r["source_type"], r["source"],
                                         r["credibility_score"], r["raw_hit_rate_pct"],
                                         r["hits"], r["misses"], r["resolved"],
                                         r["unresolved"], r["sample_flag"])) + "</tr>"
        for r in summary
    )
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)} source research | TransferIntel</title>
<style>body{{font:16px/1.55 system-ui,sans-serif;max-width:1100px;margin:auto;padding:24px;color:#e9edf5;background:#111827}}
a{{color:#80cfff}}h1,h2{{line-height:1.2}}.notice{{border-left:4px solid #ffb454;background:#202a38;padding:12px 18px}}
.scroll{{overflow-x:auto}}table{{border-collapse:collapse;width:100%;font-size:14px}}th,td{{border-bottom:1px solid #394354;padding:9px;text-align:left;white-space:nowrap}}
th button{{background:none;border:0;color:#80cfff;cursor:pointer;font:inherit;font-weight:bold;padding:0}}tr:hover{{background:#202a38}}
small{{color:#b8c3d3}}code{{color:#c7f7d4}}</style></head><body>
<p><a href="{html.escape(site)}/">← TransferIntel</a></p>
<h1>{html.escape(title)} source research</h1><p>Snapshots: {html.escape(snapshots)} · {total_deals} tracked window/deal records · {len(summary)} attributed sources · {resolved} resolved and {unresolved} unresolved source calls.</p>
<div class="notice"><strong>Exploratory sample.</strong> No source has five resolved calls. The one named journalist has a small sample. These scores describe the tracked claims only, and are not a general rating of a writer or outlet. {seed_count} call receipts are internal seed references rather than independently retrievable article links.</div>
<p><a href="source-summary.csv">Download source summary CSV</a> · <a href="source-calls.csv">Download call-level evidence CSV</a> · <a href="https://github.com/peterdsouza247/transfer-intel/blob/main/reports/2026-summer/README.md">Full methodology and reuse terms</a></p>
<h2>Source summary</h2><p>Use the column buttons to sort. Blank scores mean no resolved calls; every source is flagged as a small sample.</p>
<div class="scroll"><table id="sources"><thead><tr>{''.join(f'<th><button type="button" data-col="{i}">{heading}</button></th>' for i,heading in enumerate(['Type','Source','Score','Raw hit rate %','Hits','Misses','Resolved','Unresolved','Sample']))}</tr></thead><tbody>{table_rows}</tbody></table></div>
<h2>Method and reuse</h2><p>One source gets one call per transfer for an interest, talks, agreed or medical claim dated before resolution. Completed = hit; collapsed = miss; pending = excluded from the rate. Score = <code>round(100 × (hits + 2) / (resolved + 4))</code>. Multiple sources may call the same deal. The call CSV shows the earliest qualifying evidence for each source/deal pair.</p>
<p>Dataset: <a href="https://creativecommons.org/licenses/by/4.0/">CC BY 4.0</a>. Credit TransferIntel and link to <a href="{html.escape(site)}/">the original data</a>. Third-party article rights remain with their publishers. For expanded coverage, tailored research or ongoing delivery: <a href="mailto:peterbrendanwrites@gmail.com">peterbrendanwrites@gmail.com</a>.</p>
<h2>Commissioned research</h2><p>Ask for a window report with methodology and receipts, an audit of selected sources or clubs, or scheduled CSV delivery for future windows. Scope, coverage and fees are agreed per project; the public CC BY dataset remains reusable. <a href="mailto:peterbrendanwrites@gmail.com?subject=TransferIntel%20research%20enquiry">Email Peter with your windows, sources, format and deadline</a>.</p>
<script>document.querySelectorAll('th button').forEach(b=>b.addEventListener('click',()=>{{const t=document.querySelector('tbody'),i=Number(b.dataset.col),numeric=[2,3,4,5,6,7].includes(i),descending=b.dataset.desc!=='true';b.dataset.desc=descending?'true':'false';[...t.rows].sort((a,c)=>{{const x=a.cells[i].textContent,y=c.cells[i].textContent;return (numeric?(Number(x)||-1)-(Number(y)||-1):x.localeCompare(y))*(descending?-1:1)}}).forEach(row=>t.append(row))}}))</script>
</body></html>"""
    (out / "index.html").write_text(page, encoding="utf-8")
    print(f"{out}: {len(summary)} sources, {len(calls)} calls, {resolved} resolved")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, nargs="+",
                        help="one or more distinct window data.json files")
    parser.add_argument("--windows-root", type=Path, default=Path("windows"))
    parser.add_argument("--from-window", help="first archived slug, e.g. 2026-summer")
    parser.add_argument("--to-window", help="last archived slug, e.g. 2027-summer")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.data and (args.from_window or args.to_window):
        parser.error("Use either --data or --from-window/--to-window")
    if args.data:
        paths = args.data
    elif args.from_window and args.to_window:
        lo, hi = window_order(args.from_window), window_order(args.to_window)
        if lo > hi:
            parser.error("--from-window must precede --to-window")
        candidates = [p for p in args.windows_root.iterdir()
                      if p.is_dir() and len(p.name.split("-")) == 2
                      and p.name.split("-")[0].isdigit()
                      and p.name.split("-")[1] in {"winter", "summer"}]
        paths = [p / "data.json" for p in sorted(candidates, key=lambda p: window_order(p.name))
                 if lo <= window_order(p.name) <= hi and (p / "data.json").exists()]
        if not paths or paths[0].parent.name != args.from_window or paths[-1].parent.name != args.to_window:
            parser.error("Both range endpoints need archived data.json files")
    else:
        parser.error("Provide --data or both --from-window and --to-window")
    build(paths, args.out)
