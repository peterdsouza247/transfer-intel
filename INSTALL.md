# What is in this archive

Unzip at the root of your TransferIntel repo, next to `index.html`.

```
transferintel/
  .github/workflows/     the daily job and the manual eval job
  scripts/               the pipeline, phases 0 to 7, plus tests
  evals/                 golden set cases and labelled headlines
  fixtures/              feeds, data and an index.html template for offline runs
  OPERATIONS.md          deployment and the daily routine, read this first
  DISCOVERABILITY.md     what the site renderer does and the three manual steps
  INSTALL.md             you are here
```

Nothing here overwrites your site on unzip. **`index.html` and `data.js` are
deliberately absent**, so extracting cannot clobber the live build.

Two files become generated artifacts once you are set up. `data.js` is rebuilt
by `run_editorial.py --apply`, and `index.html` is rewritten in place by
`render_site.py`, which injects a delimited block of head tags and fills the
empty containers your JavaScript later overwrites. Both are idempotent: your
own markup outside the generated block is left alone, and rerunning produces an
identical file. A copy of your current `index.html` is included as
`fixtures/template.html` so the tests have something to render against.

## Verify before you wire anything up

```bash
pip install -r scripts/requirements.txt
python -m pytest scripts/tests -q                # expect 94 passed
python scripts/run_evals.py --suite pipeline     # expect 3/3, 0 false completions
```

Both run offline with no API key. If either fails, stop there.

## Then, in order

1. `OPERATIONS.md`, from "One-time deployment". The two settings people miss
   are in step 4, and the second silently breaks the final step of every run
   until it is ticked.
2. `DISCOVERABILITY.md`. One required config value, `config.site.baseUrl`, and
   the renderer refuses to run without it rather than guessing an origin.
