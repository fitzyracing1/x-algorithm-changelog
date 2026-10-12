# Contributing

Notes are the main contribution. To add one, create `notes/<7-char-sha>.md` with a one-line `#` title, an optional paragraph, and bullets with `old` → `new` values. Only write what the diff shows.

Before you open a PR, run `python -m xchangelog --no-fetch` (after one full run), `python scripts/validate.py site`, and `python -m unittest discover -s tests`.
