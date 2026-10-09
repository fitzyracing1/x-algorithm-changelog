# Aug 13 release: ranking parameters, visibility filtering, labelers, Phoenix training code

The biggest drop in the repo's history (2,053 files, +363,246/−11,640). The upstream README's "August 13th, 2026" notes cover it.

- **Ranking configuration is now public.** `home-mixer/params/param.rs` appears with 189 `param!` feature-switch defaults, including the action weights that blend predicted engagement probabilities into one score (for example `ReplyWeight`, `ShareViaCopyLinkWeight` and the negative `ReportWeight`/`BlockAuthorWeight`). See the parameter tracker on this site for how they've changed since.
- **Visibility filtering:** `visibility-filtering/` decides whether a post is shown, dropped, or shown behind an interstitial.
- **The systems that produce the labels it reads:** rule engines (`botmaker/`, `botmaker-rules/`, `scarecrow/`), account-scoring models (`agatha/`, `bdsm/`, `user-cred-v2/`), media models (`media-model-proxy/`, `clip/`) and enforcement (`abuse-enforcement-service/`).
- **Phoenix:** the demo model is replaced by the training code plus synthetic data generation.
- **SimClusters** (`simclusters/`) is added as another out-of-network candidate source, alongside Thunder and Phoenix retrieval.
- New home-mixer filters include `ad_adjacent_served_filter`, `following_retweet_deduplication_filter`, `self_reply_chain_filter` and `oon_nsfw_simclusters_filter`. See the full analysis for the complete list.
- **`docs/BIDIRECTIONAL_BOOST_CHANGE.md`** is a worked example of a changelog. It walks through the July 2026 rollout of the bidirectional-follow reply boost (A/B tested at 5/10/15/20 from July 10, broadly launched at 20 on July 13, set to 15 on July 24) and says future updates will be published to the repo so readers "should be able to understand what changed by checking the diffs."
