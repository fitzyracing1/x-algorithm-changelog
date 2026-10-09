# Community PR #88 merged: dedupe in-network IDs before visibility lookup

Merge of [PR #88](https://github.com/xai-org/x-algorithm/pull/88) from `joshs1017dev`. `VFCandidateHydrator` now sorts and dedups `in_network_ids` (`sort_unstable()` + `dedup()`) before calling visibility filtering, so duplicate IDs aren't looked up twice. Two lines changed. This is the only commit in the history from an outside contributor.
