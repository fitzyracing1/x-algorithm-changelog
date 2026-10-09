# Ranking weights: dwell 0 → 0.05, video open 0.05 → 0.07, VQV 0.05 → 0

- **Weight changes** in `home-mixer/params/param.rs`: `DwellWeight` `0.0` → `0.05`, `VideoOpenWeight` `0.05` → `0.07`, `VqvWeight` (video quality view) `0.05` → `0.0`.
- `PhoenixAggregationType` default goes from `"DENSE_WITH_SHORT_DWELL"` to `"DENSE_WITH_LONG_DWELL"`, and the shadow-traffic cluster list `ShadowTrafficPhoenixClusterRates` is emptied.
- `EnableAdsBrandSafetyVerdictV2` is now on by default. `ColdStartTsTopK` drops from 5 to 2.
- Removed params: `UseServedSlateContext`, `VMRankerValueModelId`, `VMRankerSendHeadWeights`. `EnableMpnScoring` is replaced by a new `MultiplierPreOffset` flag. `ranking_scorer.rs` is reworked: the per-candidate `effective_head_weights` function is removed and the author-diversity code is restructured.
- The author cold-start scorer now uses `view_count_on_home` (impressions on Home) instead of the overall `view_count`.
- New `following_blocked_by_hydrator` flags retweets and quotes whose original author blocks the viewer.
- The Brazil election list grows from 665 to 2,328 accounts, and the README adds an "Account list updated" note.
