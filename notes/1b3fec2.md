# vm-ranker can now compute the value model itself (off by default)

- **`vm-ranker/` gets its own `params.rs`** with 44 `param!` defaults. These include 27 ranking weights that mirror home-mixer's names, keys and current defaults (all of home-mixer's weights except `ContActiveSecs5mResidualNormWeight`; e.g. `FavoriteWeight` = 0.5, `ReplyWeight` = 5.0), plus DPP settings (`DppEnabled` on, `DppTheta` = 0.65, `DppMaxSelectedRank` = 150) and author-diversity settings (`EnableAuthorDiversity` on, `AuthorDiversityDecay` = 0.5, `AuthorDiversityFloor` = 0.25).
- New `vm-ranker/scoring/value_model.rs` builds value-model weights from those params (`weights_from_params`). It is gated by `ComputeValueModel`, which defaults to **off**. New `ranking_config.rs` and `config_sync.rs` files are added alongside.
- **Home-mixer side:** new `vm_ranker_debias_payload.rs` and a `VMRankerSendDebiasInputs` flag (off). `PhoenixMOEMaxResults` `200` → `0` and `PhoenixMoeColdStartMaxResults` `0` → `200`. `PhoenixRetrievalMOEInferenceClusterId` `"Experiment3Memy04"` → `"Experiment2Memy04"`.
- Grox: `GROK_GEMMA_FOLLOWER_SPLIT` `200_000` → `225_000`.
