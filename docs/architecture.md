# Architecture

| File | Role |
|---|---|
| `xchangelog/gitio.py` | clone/fetch upstream and walk first-parent commits |
| `xchangelog/analyze.py` | deterministic diff analysis: pipeline components, `param!` defaults (old → new), constants, literal tweaks, list sizes, proto/thrift fields, LFS artifacts, no-op detection |
| `xchangelog/summarize.py` | deterministic or LLM summary |
| `xchangelog/render.py`, `static/style.css` | HTML site, parameter history page, Atom feed |
| `xchangelog/cli.py`, `__main__.py` | CLI |
| `xchangelog/bot.py` | optional X bot (stdlib, OAuth 1.0a) |
| `scripts/validate.py` | output validator |
| `notes/`, `data/llm/`, `data/posted.json` | hand notes, LLM cache, bot state |
| `.github/workflows/pages.yml` | daily build, deploy, then bot job |
