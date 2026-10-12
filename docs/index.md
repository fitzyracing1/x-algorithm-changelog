# x-algorithm changelog (unofficial)

This project publishes plain-English developer notes for every update to [xai-org/x-algorithm](https://github.com/xai-org/x-algorithm) as a static site with an Atom feed. It is an independent community project and is not affiliated with X Corp. or xAI. Every entry links to the upstream commit.

- Python ≥ 3.10 and `git`. No third-party Python dependencies.
- Package `xchangelog`. An optional X bot lives in `xchangelog/bot.py`.
- Site: https://fitzyracing1.github.io/x-algorithm-changelog/

Pages: [Getting started](getting-started.md) · [Usage](usage.md) · [Architecture](architecture.md) · [Contributing](contributing.md)

The `pages.yml` workflow builds, validates and deploys the site daily, on manual dispatch, and on push to `main`. A run is **lit** when it succeeds and **dark** otherwise.

MIT License. Copyright (c) 2026 Joshua Almeida
