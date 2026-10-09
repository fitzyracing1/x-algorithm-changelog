# First public commit: Thunder, Phoenix and the Rust home-mixer

The first commit in the repository's history: 79 files across `candidate-pipeline/`, `home-mixer/`, `phoenix/` and `thunder/`.

- `home-mixer/` is the service that assembles the For You feed. It ships with 12 filters (e.g. `age_filter`, `author_socialgraph_filter`, `muted_keyword_filter`, `previously_seen_posts_filter`, `vf_filter`), two candidate sources (`thunder_source` for in-network posts, `phoenix_source` for out-of-network retrieval) and four scorers: `phoenix_scorer`, `weighted_scorer`, `author_diversity_scorer` and `oon_scorer`.
- `thunder/` holds recent posts from followed accounts in memory; `phoenix/` contains the retrieval/ranking model code.
- `candidate-pipeline/` is the generic source → hydrate → filter → score → select framework the feed is built on.
