# Manual claim input

A worked example, not live data. These two files are the 22 July 2026 refresh:
five sources, twelve claims, two of which promoted deals to `done` on
Manchester United's own announcement.

```bash
python scripts/run_ingest.py \
  --data data.json --out build \
  --articles manual/articles.json \
  --claims manual/claims.json

python scripts/run_editorial.py \
  --data data.json --evidence build/evidence.json --out build

cat build/patch.md         # read before applying
cat build/candidates.md    # deals not yet tracked
```

Announcements more than three days old need `--recent-days N` on the
editorial pass, or they are scored as history and move nothing. That is the
one thing most likely to make a correct claims file look like it did nothing.

Full documentation: `docs/MANUAL-INGEST.md`.
