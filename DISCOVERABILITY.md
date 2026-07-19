# Discoverability

What the build generates, why, and the three things only you can do.

## The actual problem

The site had no sitemap, but that was not what was stopping it being found.
Every deal, score, verdict and club dashboard was written into an empty `<div>`
by JavaScript at runtime. Strip the scripts and the page contained four
headings, four tab labels and some empty containers: roughly 1,200 characters
of boilerplate, none of which named a player, a fee or a score.

Google will usually render JavaScript eventually. Bing is patchier. The social
and chat crawlers that build link previews do not run it at all, which is why
the site unfurled as a bare URL everywhere it was shared. A sitemap on its own
would have told more crawlers where the empty page was.

So the work went in this order.

**1. Pre-render the content.** `render_site.py` writes the real deal list,
funnel, feed and club grid into the existing containers. The site's own
JavaScript assigns `.innerHTML` on all of them at load, so it overwrites the
pre-rendered markup and the browser experience is byte-for-byte what it was.
Nothing in your JavaScript changed. Indexable text went from about 1,200
characters of boilerplate to about 3,700 characters naming every player, club,
fee, status and score.

**2. Give the content URLs.** One page with four tabs is one indexable
document, so "Arsenal transfer spend" had nowhere to land. Every deal and every
club now has its own page with its own canonical URL, title, description and
Open Graph card. Five deals and five clubs turned one URL into eleven. Those
pages are also where the long tail lives: nobody searches "transfer
credibility index", they search a player's name and a club.

**3. Then the plumbing.** Sitemap, robots, feed, structured data.

## What gets generated

Every daily run, from the same `data.json` the gate just approved, so the
pages can never disagree with the data.

| Output | What it is |
|---|---|
| `index.html` | your template, with generated head tags and pre-rendered content |
| `deals/<id>/` | one page per transfer: score, facts, sources, method |
| `clubs/<slug>/` | one page per club: spend, incoming, outgoing, needs |
| `og/*.png` | Open Graph cards, 1200x630, one per page worth sharing |
| `sitemap.xml` | every URL with a `lastmod` |
| `robots.txt` | permissive, sitemap declared |
| `feed.xml` | RSS, so readers can come back without remembering to |
| `llms.txt` | plain-text site map and methodology for assistants |
| `favicon.svg` | so the tab is not a blank page icon |
| `404.html` | GitHub Pages serves this automatically |

Rendering is idempotent. The generated head block is delimited at both ends,
so a rerun removes exactly what the previous run added and leaves your own tags
and whitespace alone. Run it a hundred times and the file is identical.

## Structured data

`WebSite`, `Dataset` and `ItemList` on the index; `ClaimReview` and
`BreadcrumbList` on each deal page; `SportsTeam` on each club page.

`Dataset` is the honest description of what this site is, and it is the type
most likely to be understood by something that is not a search engine.

`ClaimReview` deserves a caveat. A credibility score on a reported transfer is
precisely a review of a claim, so the markup is correct and valid. But Google's
fact-check rich results additionally require acceptance into their fact-check
programme, which is a separate application with editorial criteria. Treat any
rich result as upside, not as the reason it is there.

## About robots.txt

The AI crawlers are listed explicitly and **allowed**: GPTBot, ClaudeBot,
PerplexityBot, Google-Extended. That is a judgment call rather than a default.
For a site whose entire pitch is being a citable credibility source, being
quotable by an assistant is distribution. If you decide otherwise, flip those
four blocks to `Disallow` and nothing else changes.

## The three things only you can do

**1. Set `config.site.baseUrl` in `data.json`.** This is required and the
renderer exits 2 without it:

```json
"site": {
  "baseUrl": "https://<username>.github.io/<repo-name>",
  "title": "TransferIntel",
  "twitter": "@peterdwrites"
}
```

Canonical tags, Open Graph URLs and the sitemap all need an absolute origin. A
guessed one is worse than none, because it tells crawlers the real page lives
somewhere else, so the build refuses rather than guessing.

**The path must match the repository name exactly, hyphens and all.** A repo
called `transfer-intel` is served at `/transfer-intel/`, and a baseUrl of
`/transferintel` produces canonical tags, Open Graph images and sitemap
entries that all point at a path which does not exist. Nothing errors: the
pages build, the tags look right in the source, and every scraper 404s on the
image and falls back to whatever else it can find or has cached. That is a
common way to end up with a link preview showing somebody else's card.

`check_site.py` compares the baseUrl path against your git remote and fails
if they differ, so this cannot pass silently.

**2. Verify the site and submit the sitemap.** Ten minutes, once.

- [Google Search Console](https://search.google.com/search-console): add the
  property, verify by uploading the HTML file to the repo root, then submit
  `sitemap.xml`. Search Console is also where you find out whether Google is
  rendering the JavaScript, under URL Inspection.
- [Bing Webmaster Tools](https://www.bing.com/webmasters): same, and it can
  import directly from Search Console. Worth doing because Bing's JavaScript
  rendering is weaker, so pre-rendering helps you most there.

**3. Get one real inbound link.** This is the uncomfortable one. Everything
above makes the site legible once something points at it, but a GitHub Pages
subdomain with no inbound links has close to no crawl priority, and no amount
of correct markup substitutes for that. One link from a football subreddit, a
Hacker News post, or your own site with real traffic does more than every tag
in this document. The pages are now good enough to survive the visit.

## What I would not bother with

- **Keyword-stuffing the descriptions.** The generated ones describe the page
  accurately, which is what actually gets clicks from a results list.
- **A blog.** Only if you would write it anyway. An abandoned one is worse
  than none.
- **AMP.** Dead.
- **Manual meta tags.** They are generated now. Hand-edits inside the
  generated block are removed on the next run, which is why the block is
  delimited: anything you add outside it survives.

## Nothing was generated, or you cannot find it

The SEO files are build output, not source. They do not exist until the
renderer runs, and they are not in the archive for the same reason `data.js`
is not: shipping them would mean shipping a sitemap for somebody else's site.

```bash
python scripts/check_site.py
```

Run from the repo root. It reports which of the three states you are in:
`baseUrl` unset so the renderer exits 2 and writes nothing, inputs fine but
never run, or generated and waiting to be committed. It writes nothing itself.

The commonest cause is the preview command. `--out /tmp/preview` deliberately
writes to a throwaway directory; `--out .` writes next to your site.

One thing that catches people afterwards: **generated pages have to be
committed.** GitHub Pages serves what is in the repository, so `sitemap.xml`,
`deals/`, `clubs/` and `og/` all need to be checked in. Do not add them to
`.gitignore`.

## Checking your work

```bash
python scripts/render_site.py --data data.json --template index.html --out .
python -m pytest scripts/tests/test_site.py -q
```

The tests assert the things that break silently: every page has exactly one
canonical, Open Graph images are the size platforms crop from, the sitemap
lists every generated page, seed evidence never renders as a dead citation,
and rendering twice produces an identical file.

### Checking the live card

```bash
python scripts/check_og.py https://you.github.io/transferintel/
```

This is a local opengraph.xyz. It fetches the page with a scraper's user
agent, reads the tags out of the raw HTML without running any JavaScript,
follows the declared image, and reports its real status code and dimensions.

Worth having alongside the hosted preview tools specifically because those
cache hard. After you fix a card they will keep serving the old one, and you
cannot tell a stale cache from a broken tag by looking at it. This never
caches, so if it passes and the hosted tool disagrees, the hosted tool is
wrong and you need to force a re-scrape.

It distinguishes the three states that look identical from the outside: tags
missing entirely because the renderer never ran against what is deployed;
tags present but the image 404s because `og/` was not committed; and
everything correct.

After deploying, three external checks worth running once:

- Paste a deal URL into the [Facebook sharing
  debugger](https://developers.facebook.com/tools/debug/) to confirm the card
  unfurls.
- Run a deal page through the [Rich Results
  Test](https://search.google.com/test/rich-results) for the structured data.
- View source on the live index and search for a player name. If it is there,
  a crawler that runs no JavaScript can see it.
