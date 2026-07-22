# DEPLOY: how to run and ship this

Everything in this archive is either a new file or a modified one. Nothing
here is unchanged from your repo, so you can unzip over the top and the diff
will be exactly the work. `CHANGED-FILES.txt` lists all 120.

Read `docs/DELIVERY.md` first if you only read one thing. It covers the two
tickets that were rediagnosed and the three decisions that need you.

---

## 0. Before you unzip

`data.json` and `data.js` in this archive have been modified: seven records
were demoted from Completed, six of them the ones named in your ticket. If you
would rather audit that yourself before it touches your repo, hold those two
files back:

```bash
unzip transfer-intel-backlog.zip
cd transfer-intel
rm data.json data.js          # keep your originals
```

then, after copying the rest in, run the audit yourself:

```bash
python scripts/backfill_completions.py --data data.json > audit.md
```

Otherwise unzip over your working copy and carry on.

---

## 1. Install and verify, offline, about two minutes

```bash
cd /path/to/transfer-intel
pip install -r scripts/requirements.txt

python -m pytest scripts/tests -q              # expect 145 passed
python scripts/run_evals.py --suite pipeline   # expect 3/3, 0 false completions
```

No API key needed. Both run entirely offline. **If either fails, stop here**
and send me the output rather than deploying.

---

## 2. Rebuild the site

```bash
python scripts/render_site.py --data data.json --template index.html --out .
python scripts/check_site.py
```

`check_site.py` should report `ok` on every line. The renderer rewrites
`index.html` in place and is idempotent: run it twice and the file is
byte-identical.

Note it regenerates all 62 Open Graph PNGs, which takes about twenty seconds.
Add `--skip-images` if you are only checking markup.

---

## 3. Ship it

```bash
git checkout -b feat/backlog-00-24
git add -A
git commit -m "TI-001,002,003,010,011,012,013,020: false completions, value table, mobile scroll, digest, analytics, club sorting, rumour decay"
git push -u origin feat/backlog-00-24
```

Open a PR, or merge to `main` directly. GitHub Pages serves what is in the
repo, so the generated pages, sitemap, feed and OG images all have to be
committed. They are already in the archive for that reason.

After the Pages deploy finishes, hard refresh and check:

- the homepage on a phone scrolls in one gesture through all four sections
- the Deal-by-deal verdicts table has 25 rows under its headers
- Trossard, Tielemans, Vuskovic, Santos, Manzambi and Rodriguez all read
  **Confirmed pending announcement** at 90, not Completed at 100
- club cards show Spent, Sold and Net, busiest first, with two quiet clubs
  folded into a collapsed group at the bottom

---

## 4. Turn on email, about fifteen minutes

Nothing renders until you configure it. That is deliberate: a capture form
posting nowhere burns the one chance you get to ask a visitor for their email.

1. Create a Kit account (10,000 subscribers free) and a form. Copy the hosted
   endpoint, the URL like `https://app.kit.com/forms/1234567/subscriptions`.
2. **Turn on double opt-in** in the provider settings before anything else.
3. Put the endpoint in `data.json`:

   ```json
   "newsletter": {
     "provider": "kit",
     "action": "https://app.kit.com/forms/1234567/subscriptions"
   }
   ```

4. Repository **Settings, Secrets and variables, Actions**, add
   `NEWSLETTER_API_KEY`.
5. Rebuild and push. Three capture forms and a `/thanks/` page appear.

Read an edition before you ever send one:

```bash
python scripts/run_digest.py --data data.json --out build/digest --segments
cat build/digest/*-all.md
```

Do that for the first week. A digest that goes out wrong cannot be recalled.
The scheduled job at 06:00 UTC starts sending once the secret exists; the
manual run in the Actions tab has a **send** tickbox that defaults to off.

Full detail: `docs/NEWSLETTER.md`.

---

## 5. Turn on analytics, about five minutes

1. Cloudflare dashboard, **Analytics and Logs**, **Web Analytics**, **Add a
   site**. You do not need Cloudflare as your DNS provider.
2. Copy the `data-cf-beacon` token into `data.json`:

   ```json
   "analytics": { "cloudflareToken": "your-token-here" }
   ```

3. Rebuild and push.

Cookieless, so no consent banner. GoatCounter stays configured as a second
opinion. Six custom events are already instrumented.

Full detail: `docs/ANALYTICS.md`.

---

## 6. The two scheduled jobs

| Workflow | When | What it does |
|---|---|---|
| `editorial.yml` | 07:00 UTC | ingest, score, gate, render, open a PR you approve |
| `digest.yml` | 06:00 UTC | build and send the day's editions |

Both need to be enabled once in the **Actions** tab if GitHub prompts you.

`editorial.yml` needs `ANTHROPIC_API_KEY` for phases 2 and 4. Without it the
run still works: ingestion fails soft to an empty evidence file and the day
becomes a pure decay pass, which is a correct thing to publish.

### One thing to expect on the first editorial run

The decay model changed, so the first run wants to resettle credibility across
many records at once and will trip the fifteen-update limit. Raise it once:

```bash
python scripts/run_editorial.py --data data.json --out build \
  --no-notes --max-changes 60 --apply
```

Commit that, and the normal limit holds from then on. This is a one-off
migration cost, not a recurring one.

---

## Troubleshooting

**Gate fails with "completed on a tier N source".** A record is marked `done`
on something below tier 1. Run `python scripts/backfill_completions.py --data
data.json` to see which and why. This is the gate working.

**Gate fails with "N updates, limit is 15".** Either genuinely busy news, or
something upstream broke. Read `build/aborted_patch.json` before raising the
limit. Nothing was written, so there is no rush.

**Value table empty after deploying.** Open the browser console. Each section
now logs which one failed and fires a `render_error` event. If the console is
clean and the table is still empty, that is a mechanism I have not seen and I
want to know about it.

**Digest sent twice.** It should not: `logs/digest-sent.json` records sent
dates and the job refuses to repeat one. Check whether someone passed
`--force`.

**Pages 404s on /sitemap.xml.** The generated files have to be committed.
Pages serves the repo, not a build artifact.
