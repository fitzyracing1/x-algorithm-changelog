# Usage

## `python -m xchangelog`

| Flag / env | Default | Meaning |
|---|---|---|
| `--no-fetch` | off | use the existing clone as-is |
| `--upstream-dir` | `.cache/x-algorithm` | clone location |
| `--out` | `site` | output dir |
| `--since YYYY-MM-DD` | all | only commits on or after this date |
| `--site-url` / `SITE_URL` | project Pages URL | absolute base for the feed |
| `--project-url` / `PROJECT_URL` | repo URL | "Source for this site" link |
| `--no-llm` | off | never call an LLM |
| `--llm-limit` / `CHANGELOG_LLM_LIMIT` | `10` | max new LLM summaries per run |

## Summary sources (in priority order)
1. `notes/<short-sha>.md`: hand-written notes, checked against the diff
2. `data/llm/<short-sha>.md`: cached LLM summary. Written only if `XAI_API_KEY` (or `OPENAI_API_KEY`) is set. Configure with `CHANGELOG_LLM_BASE_URL` and `CHANGELOG_LLM_MODEL`.
3. Deterministic summary (`xchangelog/summarize.py`)

## Bot
`python -m xchangelog.bot --entries entries.json --state data/posted.json`. In CI it posts only when the repo variable `BOT_POSTING_ENABLED` is `true` and the `X_API_KEY`, `X_API_SECRET`, `X_ACCESS_TOKEN` and `X_ACCESS_TOKEN_SECRET` secrets exist. Otherwise it is a dry run. Already-posted entries are tracked in `data/posted.json`.
