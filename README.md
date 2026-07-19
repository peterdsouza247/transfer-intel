# TransferIntel

A Premier League transfer window site that goes beyond fee lists: rumor credibility scoring, value-for-money analytics, club dashboards, and a deal-lifecycle pulse (rumor → talks → agreed → medical → done).

## Repository layout

```
transferintel/
├── index.html                    # the site (design + logic, rarely changes)
├── data.js                       # ALL content lives here (edit this)
├── scripts/
│   └── update_data.py            # mechanical auto-refresher for GitHub Actions
├── .github/
│   └── workflows/
│       └── refresh.yml           # daily schedule for the script
└── README.md
```

Note: the files were delivered flat. When you create the repo, place `update_data.py` in `scripts/` and `refresh.yml` in `.github/workflows/` exactly as shown above.

## Setup (one time, ~10 minutes)

1. Create a free account at github.com if you don't have one.
2. Click **New repository**, name it (e.g. `transferintel`), set it to **Public** (required for free Pages and Actions), and create it.
3. Upload the files: on the repo page choose **Add file → Upload files**. Drag in `index.html`, `data.js`, `README.md`. Then use **Add file → Create new file**, type `scripts/update_data.py` as the name (the slash creates the folder), and paste in the script. Do the same for `.github/workflows/refresh.yml`.
4. Enable the website: **Settings → Pages → Source: Deploy from a branch → Branch: main / (root) → Save**. After a minute your site is live at `https://<your-username>.github.io/transferintel/`.
5. Enable the daily refresh: go to the **Actions** tab, click **"I understand my workflows, enable them"** if prompted. The workflow runs daily at 06:00 UTC; you can also trigger it any time with the **Run workflow** button.

That's it. Every push to `main` redeploys the site automatically.

## How updates work

There are two layers, and it pays to keep them separate in your head.

### The mechanical layer (automated)

`scripts/update_data.py` runs daily via GitHub Actions. For every deal not yet done, it checks Wikipedia's "List of English football transfers summer 2026" page (via the Wikimedia REST API) and, if the player's move to the expected club appears there, promotes the deal to "done" and updates the fee (Wikipedia lists fees in GBP directly). It commits the change, and Pages redeploys. Zero human input.

Why Wikipedia as the primary source: it is updated within hours by editors, every entry carries a citation, the API is built for automated access, and it works from GitHub's runners. The unofficial Transfermarkt API (`transfermarkt-api.fly.dev`) is kept as a fallback, but Transfermarkt blocks datacenter IPs, so expect it to fail from Actions more often than not; that is fine, the script fails soft (logs and skips), so the worst case is "no update today", never a broken site.

Caveats: Wikipedia can lag a day or two behind Sky's ticker, and fees listed as "Undisclosed" won't update the number. Hand-correct in `data.js` when better figures land. When a new window starts, change the `WIKI_PAGE` constant at the top of the script (one line, e.g. `List_of_English_football_transfers_winter_2026-27`).

### The editorial layer (the actual value of the site)

Everything that makes this site better than a fee list is editorial, and no API provides it:

- **`cred` (0-100)** rumor credibility: your judgment of how likely a move is, anchored by source tier. Suggested calibration: 85+ = fee agreed per a Tier 1 source; 60-84 = active talks confirmed by Tier 1/2; 40-59 = credible interest, no agreement; under 40 = paper talk. Done = 100, collapsed = 0.
- **`tier` (1-3)** source quality: 1 = Romano, Ornstein, Sky Sports, The Athletic; 2 = established outlets (Football365, FootballTransfers, Telegraph); 3 = tabloids and unsourced foreign press.
- **`note`** one sharp sentence of context per deal. This is the personality of the site.
- **`clubs`** the `needs` and `ctx` text per club: what they still need, and the story of their window.

Maintaining it is a 5-10 minute daily edit of `data.js`, doable straight in the GitHub web editor (open the file, click the pencil, commit). Skim Sky Sports' transfer centre and Football365's daily rumour ranking, then adjust scores, add new rumors, and delete stale ones (a rumor with no movement for two weeks is dead; remove it or drop its score).

House style: never use em dashes or en dashes anywhere in the content. Use commas, colons, semicolons, periods, or the "·" separator.

### Automating the editorial layer later

If the daily edit gets old, realistic paths in increasing order of effort:

1. **LLM-assisted drafting (recommended first step).** Extend the GitHub Action with a step that calls an LLM API (e.g. Anthropic's) with your own API key stored as a repo secret (**Settings → Secrets and variables → Actions**). Feed it the current `data.js` plus the day's headlines (fetched via an RSS feed or news API) and a strict prompt: return the updated deals array only, follow the credibility calibration above, never invent deals, no em dashes. Have the Action write the result to a **pull request instead of committing directly**, so you approve each day's edit in one click. Cost is pennies per day; the review step protects you from hallucinated transfers.
2. **Rules on top of signals.** Cheaper but cruder: auto-decay `cred` by a few points for every day a rumor goes unmentioned, auto-bump when a Tier 1 journalist's RSS feed mentions the player. This automates score drift but still can't write notes.
3. **Full autopilot.** Same as option 1 but committing directly without review. Only do this after weeks of PR-reviewed runs have proven the prompt reliable; an invented "done deal" on a live site is worse than a stale one.

## Future transfer windows

All window-specific content lives in `data.js`, so a new window is a data change, not a code change:

1. Archive the old window: rename `data.js` to `data-2026-summer.js` and keep it in the repo.
2. Create a fresh `data.js`: update `config` (`windowName`, `deadline` as an ISO UTC timestamp, `deadlineLabel`, `updated`), empty the `deals` array, rewrite the `clubs` entries for the new season's 20 clubs (relegations/promotions), and refresh `provenClubs` (the list of leagues-proven selling clubs used by the value model).
3. Seed it with the first confirmed deals and rumors, commit, done.

The countdown clock, KPIs, funnel, charts and dashboards all rebuild themselves from whatever is in `data.js`. If you later want a window-picker dropdown to browse archives, that's a small `index.html` change; any LLM coding tool can add it, since the data files are already versioned by name.

## Editing data.js: field reference

Each deal object:

| Field | Meaning |
|---|---|
| `p` | Player name |
| `from`, `to` | Clubs (use consistent short names; club dashboards match on exact name) |
| `fee` | £ millions, `0` for free transfers |
| `age`, `pos` | Age and position (`GK CB LB RB CM AM LW RW ST`); both feed the value model |
| `status` | `rumor`, `talks`, `agreed`, `medical`, `done`, `collapsed` |
| `date` | Display date, e.g. `"Jul 15"` |
| `tier` | Source tier 1-3 (see editorial layer) |
| `src` | Primary source name shown on the card |
| `cred` | 0-100 credibility (see calibration above) |
| `note` | One sentence of context |

Deals appear automatically in every tab: the pulse feed, the rumor index (any non-done status), value analytics (paid deals at agreed/medical/done), and both clubs' dashboards.
