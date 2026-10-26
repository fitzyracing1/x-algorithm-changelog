# x-algorithm changelog (unofficial)

Plain-English developer notes for every update to [xai-org/x-algorithm](https://github.com/xai-org/x-algorithm), X's open-source "For You" recommendation algorithm. Published as a static site with an Atom feed.

> **Unofficial.** This is an independent community project. It is not affiliated with, endorsed by, or operated by X Corp. or xAI. The upstream git history is the source of truth. Notes here can be wrong, and every entry links to the upstream commit so you can check.

## Why

The upstream repo is synced by a CI agent. Nearly every commit has the same message, *"Open-source X Recommendation Algorithm"*, and there are no tags or releases. The README's "Notable Updates" section covers only a few dates (Aug 13, Aug 14 and Sep 18, 2026), while the repo received 45 syncs between Jan 20 and Oct 9, 2026, many of which change ranking weights, filters, candidate sources and feature flags.

Elon Musk has said the repo would come with notes:

- [Jan 10, 2026](https://x.com/elonmusk/status/2010062264976736482): *"We will make the new 𝕏 algorithm … open source in 7 days. This will be repeated every 4 weeks, with comprehensive developer notes, to help you understand what changed."*
- [May 15, 2026](https://x.com/elonmusk/status/2055293386123567441): *"There will be monthly updates of the latest algorithm to GitHub with release notes."*

Upstream's own [`docs/BIDIRECTIONAL_BOOST_CHANGE.md`](https://github.com/xai-org/x-algorithm/blob/main/docs/BIDIRECTIONAL_BOOST_CHANGE.md) (added Aug 13, 2026) shows what that would look like, and says readers "should be able to understand what changed by checking the diffs." With 45 near-identical commits, that's hard to do by hand. This project fills that gap from the outside. It reads each upstream commit's diff and writes down what changed, with old → new values.

## What each entry contains

For every first-parent commit on upstream `main` (merges are diffed against their first parent):

- **Date, short SHA, link** to the upstream commit.
- **Files/modules** added, removed and changed, with line counts per top-level module.
- **Deterministic analysis** (`xchangelog/analyze.py`), with no network and no model:
  - **Pipeline components** added, removed or renamed (filters, scorers, candidate sources, hydrators, selectors, side effects, ads blenders, Botmaker rules, visibility rules), classified by path.
  - **`param!` feature-switch defaults** (e.g. `home-mixer/params/param.rs`, `vm-ranker/params.rs`): added, removed and moved params, and changed defaults with **old → new** values. Ranking weights (`*Weight`) are called out separately, and boolean flags are reported as "now ON/OFF by default".
  - **Named constants** (Rust `const`/`static`, Python typed fields / UPPER_CASE, Scala `val`, Java `static final`) with changed literal values.
  - **Other literal tweaks**: `-`/`+` line pairs that differ only in numbers or booleans (test code excluded).
  - **Static list sizes** pinned by in-file tests (e.g. the Brazil election account list: 665 → 2,795 entries over time).
  - **Proto/Thrift schema fields** added or removed, and **Git LFS artifacts** replaced (with sizes).
  - **No-op detection**: files whose only change is sync metadata (the `last sync <timestamp>` header), whitespace or comments are flagged. A commit with nothing else is collapsed as a no-op and left out of the feed.
- **Plain-English summary**, from the first available source:
  1. `notes/<short-sha>.md`: hand-written notes, written from the diff and checked against it.
  2. `data/llm/<short-sha>.md`: a cached LLM summary (only if an API key is configured, see below).
  3. The deterministic summary (`xchangelog/summarize.py`). It always works, needs no key and makes no claims beyond the analysis.

The site also has a **parameter history** page (every `param!` default change, newest first) and a **current ranking-weights** table on the index.

## Run locally

Requirements: Python ≥ 3.10 and `git`. There are **no third-party Python dependencies**.

```bash
python -m xchangelog                 # clones/fetches upstream into .cache/x-algorithm, writes ./site
python scripts/validate.py site      # Atom + HTML + internal-link checks (uses feedparser too if installed)
python -m http.server -d site 8000   # then open http://localhost:8000
```

Useful flags (`python -m xchangelog --help`):

| Flag / env | Default | Meaning |
| --- | --- | --- |
| `--no-fetch` | off | Use the existing clone in `.cache/x-algorithm` as-is |
| `--upstream-dir` | `.cache/x-algorithm` | Where to clone upstream |
| `--out` | `site` | Output directory |
| `--since YYYY-MM-DD` | all history | Only include commits on/after a date |
| `--site-url` / `SITE_URL` | `https://fitzyracing1.github.io/x-algorithm-changelog/` | Absolute base URL used in the feed |
| `--project-url` / `PROJECT_URL` | `https://github.com/fitzyracing1/x-algorithm-changelog` | "Source for this site" link |
| `--no-llm` | off | Never call an LLM even if a key is set |
| `--llm-limit` / `CHANGELOG_LLM_LIMIT` | `10` | Max *new* LLM summaries per run (cost guard) |

A full run over all 45 commits takes about 15 seconds.

### Optional LLM summaries

If **`XAI_API_KEY`** is set (or `OPENAI_API_KEY` as a fallback), commits without a hand-written note get an LLM summary. The prompt contains the structured analysis plus a truncated diff and tells the model to use only those facts. Output is cached in `data/llm/<short-sha>.md`, so each commit is summarised once. Any API error falls back to the deterministic summary.

| Env var | Default |
| --- | --- |
| `XAI_API_KEY` | (unset → no LLM) |
| `OPENAI_API_KEY` | used only if `XAI_API_KEY` is unset |
| `CHANGELOG_LLM_BASE_URL` | `https://api.x.ai/v1` (or `https://api.openai.com/v1`) |
| `CHANGELOG_LLM_MODEL` | `grok-4` (or `gpt-4o-mini`) |

Any OpenAI-compatible `/chat/completions` endpoint works. LLM-written entries are labelled as such on the site. Review them before relying on them, and promote good ones to `notes/` after checking them against the diff.

### Writing a note

Create `notes/<7-char-sha>.md`:

```markdown
# One-line title

Optional paragraph.

- Bullet with `identifiers` and `old` → `new` values.
```

Notes override the automatic summary, but the entry page always shows the full automatic analysis underneath. Only write what the diff shows.

## Deploy (GitHub Pages)

`.github/workflows/pages.yml` runs daily at 09:17 UTC, on manual dispatch, and on pushes to `main`. It:

1. Checks out the repo and sets up Python 3.12.
2. Runs `python -m xchangelog`, which clones upstream fresh. `SITE_URL` and `PROJECT_URL` are derived from the repo owner/name.
3. Validates the output with `scripts/validate.py`.
4. Commits any newly cached LLM summaries in `data/llm/` back to the repo (only when a key secret is set).
5. Uploads `site/` and deploys it with `actions/deploy-pages`.
6. After a successful deploy, the `post` job runs the bot's unit tests and then the X bot (see [Bot](#bot)). Without the bot secrets and variable it is a dry run that only prints what it would post.

One-time setup after pushing: **Settings → Pages → Source: GitHub Actions**. Optionally add an `XAI_API_KEY` repository secret.

## Bot

An optional X bot posts a short note when a **new** upstream sync contains a meaningful change. The code is in `xchangelog/bot.py` (stdlib only, including OAuth 1.0a signing for `POST /2/tweets`), and the tests are in `tests/test_bot.py`.

**What counts as meaningful.** All of these come from the deterministic analysis:

- A ranking weight default changing, being added with a non-zero value, or being removed.
- Any other `param!` default changing (old → new).
- A feature flag flipping, or a new flag that is ON by default.
- A candidate source, filter or scorer being added or removed in the feed-serving code (`home-mixer/`, `thunder/`, `vm-ranker/`, `xai-value-model/`, `candidate-pipeline/`).
- A threshold constant moving by 20% or more.

Sync noise, formatting, comment-only changes, model-file (Git LFS) churn, string-only experiment IDs and timestamp-only commits never post.

**Format.** The first line is `X algorithm update (Oct 7): <most important change, exact old → new, % or ×>`. It's followed by up to 3 more changes, a link to the entry page here, a link to the upstream commit, and an "Unofficial; repo defaults" note. Each post is at most 280 weighted characters (URLs count as 23, `→` counts as 2), and a thread is at most 4 posts. There are no hashtags and no @mentions.

**Accuracy gates.**

- Text is built only from the analysis. A hand-written note title is used as the headline only when every number in it matches the analysis.
- LLM text is never used.
- A final check rejects any post containing a number that is not an analysed value or a value computed from one (percentage, ratio, duration).
- The bot skips the entry instead of guessing on any of these: mirrored params that changed differently, non-literal numeric defaults, bulk releases (>50 new params), the initial import, or a post that doesn't fit.

**State and limits.**

- `data/posted.json` records every handled upstream SHA (`baseline`, `posted`, `not_meaningful`, `skipped`, `failed`, `partial`, `uncertain`), so nothing posts twice.
- The first live run marks every existing entry as handled and posts nothing (no backfill). Posting starts with the next meaningful sync.
- At most 1 thread per run and 3 per UTC day. Entries older than 3 days are skipped.
- A post the API definitely rejected is retried once on the next run.
- A post whose outcome is unknown (for example a timeout) is never retried. Check the account by hand.

**Kill switch.** Posting happens only when the repo variable `BOT_POSTING_ENABLED` is exactly `true` **and** all four secrets are set. Anything else is a dry run. Set the variable to `false` to stop immediately.

### Setup (in this order)

1. **Create the bot's X account.** Use a dedicated handle and an email you control. Add a profile bio that says it's an unofficial, automated tracker and links to this site.
2. **Label it as automated.** Log in as the bot and go to **Settings and privacy → Your account → Account information → Automation**. Choose **Managing account** and pick your personal account, then confirm with that account's password. The bot profile then shows "Automated by @you".
3. **Create the developer app.** Log in to [developer.x.com](https://developer.x.com) **as the bot account**, so the access token belongs to the bot. Then:
   - Create a Project and an App.
   - Under **App settings → User authentication settings → Set up**, choose:
     - App permissions: **Read and write**.
     - Type of app: **Web App, Automated App or Bot**.
     - Callback URL and Website URL: `https://fitzyracing1.github.io/x-algorithm-changelog/`.
   - Save.
   - Check that the current API access tier allows `POST /2/tweets` at about 1–3 threads a day.
4. **Generate the keys.** In **Keys and tokens**:
   - Copy the **API Key and Secret** (consumer keys).
   - **Generate** the **Access Token and Secret**. Do this after setting Read and write. The portal must say the token was created with *Read and Write* permissions; if it says *Read*, regenerate it.
5. **Add the four repository secrets.** On GitHub, open **Settings → Secrets and variables → Actions → Secrets → New repository secret** and add `X_API_KEY`, `X_API_SECRET`, `X_ACCESS_TOKEN` and `X_ACCESS_TOKEN_SECRET`.
6. **Check a dry run.** Run the workflow by hand (**Actions → Build & deploy changelog → Run workflow**). The `post` job log should say `DRY RUN (BOT_POSTING_ENABLED is not 'true')`.
7. **Enable posting.** Under **Settings → Secrets and variables → Actions → Variables → New repository variable**, add `BOT_POSTING_ENABLED` = `true`. Then run the workflow once more. That first live run only marks existing entries as handled; the first real post comes with the next meaningful upstream sync.

Local checks (no network, nothing posted):

```bash
python -m unittest discover -s tests -v
python -m xchangelog.bot --preview a707cc2 77d431a 78460ca 35650fb   # what past syncs would have looked like
python -m xchangelog.bot --dry-run                                  # what the next run would post
```

## Layout

```
xchangelog/
  gitio.py      git CLI helpers (clone/fetch, first-parent walk, numstat, cat-file batch reader)
  analyze.py    deterministic per-commit analysis
  summarize.py  deterministic plain-English summary, notes loader, optional LLM call
  render.py     HTML pages + Atom feed (no template engine, no JS)
  cli.py        orchestration; `python -m xchangelog`
  bot.py        optional X bot; `python -m xchangelog.bot` (dry run unless enabled)
tests/          unit tests for the bot
notes/          hand-written notes, one file per upstream commit
data/llm/       cached LLM summaries (empty until a key is configured)
data/posted.json  bot state: upstream SHAs already handled
static/style.css
scripts/validate.py
.github/workflows/pages.yml
```

## Limitations

- Component detection is path-based. A filter that lives in a file that isn't named or placed like one won't be listed as a filter, and a moved file can show up as removed + added.
- "Meaningful change" detection is heuristic. Named-constant and literal-tweak detection covers common Rust/Python/Scala/Java patterns, not every syntax. Refactors of non-literal expressions are ignored on purpose.
- `param!` defaults are only the defaults mirrored into the repo. Production can override them through feature switches and experiments, which this project can't see.
- Each sync bundles roughly a day of internal changes into one commit, so notes describe the net change per sync, not individual internal commits.
- Hand-written notes are written by a person reading diffs and can contain mistakes. The upstream diff wins.

## License

MIT for this project's code and notes. Upstream code is © X Corp./xAI under its own license (Apache-2.0).

## Documentation

- In-repo docs: [docs/](docs/index.md)
- Wiki: [https://github.com/fitzyracing1/x-algorithm-changelog/wiki](https://github.com/fitzyracing1/x-algorithm-changelog/wiki)

## Plans

This project is free, and its license stays as is. Support it through [GitHub Sponsors](https://github.com/sponsors/fitzyracing1):

- **Free**: use the code, community support.
- **Pro ($49/month)**: priority issues and support.
- **Team ($199/month)**: priority support for your team plus early access.
