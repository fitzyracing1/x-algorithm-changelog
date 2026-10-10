"""Unit tests for the X bot: formatting, dedup/state, gating, accuracy gates. Stdlib unittest, no network.

Run: python -m unittest discover -s tests -v
"""
from __future__ import annotations

import io
import json
import os
import unittest
import urllib.error
from datetime import datetime, timedelta, timezone

from xchangelog import bot

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NOW = datetime(2026, 10, 10, 9, 30, tzinfo=timezone.utc)
LIVE_ENV = {"BOT_POSTING_ENABLED": "true", "X_API_KEY": "k", "X_API_SECRET": "s",
            "X_ACCESS_TOKEN": "t", "X_ACCESS_TOKEN_SECRET": "ts"}


def entry(sha="a" * 40, date="2026-10-10T05:00:00Z", changed=None, added=None, removed=None, comps=None,
          consts=None, cls="code", title=None, is_root=False):
    return {
        "sha": sha, "short": sha[:7], "url": f"https://github.com/xai-org/x-algorithm/commit/{sha}", "date": date,
        "class": cls, "is_root": is_root, "stats": {"files": 3},
        "summary": {"source": "note" if title else "auto", "title": title or "auto"},
        "params": {"changed": changed or [], "added": added or [], "removed": removed or [], "moved": []},
        "components": {"added": comps or [], "removed": [], "renamed": [], "changed": []},
        "constants": {"changed": consts or [], "added": [], "removed": []},
    }


def pc(name, old, new, typ="f64", file="home-mixer/params/param.rs"):
    return {"name": name, "type": typ, "old": old, "new": new, "file": file, "key": name.lower()}


WEIGHTS = [pc("NotInterestedWeight", "-43.2", "-47.52"), pc("ClickWeight", "0.4", "0.3"),
           pc("ContClickDwellTimeWeight", "0.0", "0.4")]


class FakePoster:
    def __init__(self, fail_at=None, exc=None):
        self.calls, self.fail_at, self.exc = [], fail_at, exc

    def __call__(self, text, creds, reply_to=None):
        if self.fail_at is not None and len(self.calls) == self.fail_at:
            raise self.exc
        self.calls.append((text, reply_to))
        return str(1000 + len(self.calls))


def quiet(*_a, **_k):
    pass


class TestCharCounting(unittest.TestCase):
    def test_ascii(self):
        self.assertEqual(bot.weighted_len("hello world"), 11)

    def test_urls_count_as_23(self):
        short = "see https://x.co/a"
        long = "see https://fitzyracing1.github.io/x-algorithm-changelog/entries/a707cc2.html"
        self.assertEqual(bot.weighted_len(short), 4 + 23)
        self.assertEqual(bot.weighted_len(long), 4 + 23)

    def test_arrow_and_emoji_weigh_two(self):
        self.assertEqual(bot.weighted_len("→"), 2)
        self.assertEqual(bot.weighted_len("a\u2212b"), 1 + 2 + 1)  # unicode minus
        self.assertEqual(bot.weighted_len("ab\U0001F600"), 4)

    def test_bare_domains_counted_conservatively(self):
        self.assertEqual(bot.weighted_len("param.rs"), 23)
        self.assertEqual(bot.weighted_len("0.015"), 5)  # numbers are not domains


class TestNumbers(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(bot.parse_num("125_000"), 125000)
        self.assertEqual(bot.parse_num("-47.52"), -47.52)
        self.assertEqual(bot.parse_num("1.0f64"), 1.0)
        self.assertIsNone(bot.parse_num("Duration::from_secs(1)"))
        self.assertIsNone(bot.parse_num("0.5 * X"))

    def test_format(self):
        self.assertEqual(bot.fmt_num("0.0"), "0")
        self.assertEqual(bot.fmt_num("125_000"), "125,000")
        self.assertEqual(bot.fmt_num("-47.52"), "-47.52")
        self.assertEqual(bot.fmt_num("0.015"), "0.015")


class TestFormatting(unittest.TestCase):
    def test_weight_lead_and_percent(self):
        r = bot.compose(entry(changed=WEIGHTS))
        first = r["posts"][0].splitlines()[0]
        self.assertEqual(first, 'X algorithm update (Oct 10): "not interested" penalty -43.2 → -47.52 (10% harsher)')
        joined = "\n".join(r["posts"])
        self.assertIn('"click" weight 0.4 → 0.3 (25% lower)', joined)
        self.assertIn('"click dwell time" weight 0 → 0.4 (previously unused)', joined)

    def test_every_post_fits_and_has_links_and_disclaimer(self):
        r = bot.compose(entry(changed=WEIGHTS))
        for p in r["posts"]:
            self.assertLessEqual(bot.weighted_len(p), 280)
            self.assertNotRegex(p, r"[@#]\w")
        joined = "\n".join(r["posts"])
        self.assertIn("https://fitzyracing1.github.io/x-algorithm-changelog/entries/aaaaaaa.html", joined)
        self.assertIn("https://github.com/xai-org/x-algorithm/commit/" + "a" * 40, joined)
        self.assertIn("Unofficial", joined)
        self.assertIn("repo defaults", joined)

    def test_single_post_when_it_fits(self):
        r = bot.compose(entry(changed=[pc("PostUnexploredWeight", "0.02", "0.015")]))
        self.assertEqual(len(r["posts"]), 1)
        self.assertLessEqual(bot.weighted_len(r["posts"][0]), 280)

    def test_thread_split_and_max_four(self):
        long = [pc(f"VeryLongDescriptiveParameterNameNumber{c}ForTestingThreadSplitting", "1", "2", "u32")
                for c in "ABCDEFGHIJ"]
        r = bot.compose(entry(changed=long))
        self.assertGreater(len(r["posts"]), 1)
        self.assertLessEqual(len(r["posts"]), 4)
        for p in r["posts"]:
            self.assertLessEqual(bot.weighted_len(p), 280)
        joined = "\n".join(r["posts"])
        shown = sum(joined.count(f"number {c.lower()} for") for c in "abcdefghij")
        self.assertLessEqual(shown, 4)  # lead + up to 3 more
        self.assertIn("more on the details page", joined)

    def test_component_lead_uses_verified_note_title(self):
        comps = [{"kind": "Filter", "name": "video_carousel_filter", "path": "home-mixer/filters/video_carousel_filter.rs"}]
        r = bot.compose(entry(comps=comps, title="Vertical video carousel added to For You (off by default)"))
        self.assertTrue(r["posts"][0].startswith("X algorithm update (Oct 10): Vertical video carousel added"))
        self.assertIn("new filter: video_carousel_filter", r["posts"][0])

    def test_note_title_with_unverified_number_is_not_used(self):
        comps = [{"kind": "Filter", "name": "x_filter", "path": "home-mixer/filters/x_filter.rs"}]
        r = bot.compose(entry(comps=comps, title="New filter drops 37% of posts"))
        self.assertNotIn("37", "\n".join(r["posts"]))
        self.assertEqual(r["headline_source"], "analysis")

    def test_durations(self):
        r = bot.compose(entry(changed=[pc("ColdStartMaxPostAgeSecs", "172800", "7200", "u64")]))
        self.assertIn("cold start max post age 2d → 2h (24x lower)", r["posts"][0])


class TestMeaningfulness(unittest.TestCase):
    def test_noop_and_docs_never_post(self):
        self.assertEqual(bot.compose(entry(cls="noop", changed=WEIGHTS))["posts"], [])

    def test_string_param_only_does_not_post(self):
        r = bot.compose(entry(changed=[pc("VMRankerClusterId", '"Experiment3"', '"Experiment6"', "String")]))
        self.assertEqual(r["posts"], [])

    def test_new_flag_off_alone_does_not_post(self):
        r = bot.compose(entry(added=[{"name": "EnableFoo", "type": "bool", "default": "false", "file": "home-mixer/params/param.rs"}]))
        self.assertEqual(r["posts"], [])

    def test_small_constant_change_does_not_post(self):
        consts = [{"file": "grox/flows/reply_spam/constants.py", "name": "RISKY_MIN_SCORE", "old": "62", "new": "66"}]
        self.assertEqual(bot.compose(entry(consts=consts))["posts"], [])

    def test_test_file_constants_ignored(self):
        consts = [{"file": "home-mixer/ads/tests/blender_comparison_test.rs", "name": "NUM_SCENARIOS", "old": "200", "new": "1000"}]
        self.assertEqual(bot.compose(entry(consts=consts))["posts"], [])

    def test_non_feed_component_does_not_post(self):
        comps = [{"kind": "Filter", "name": "task_filter", "path": "grox/flows/upa/task_filter.py"}]
        self.assertEqual(bot.compose(entry(comps=comps))["posts"], [])

    def test_flag_flip_posts(self):
        r = bot.compose(entry(changed=[pc("EnablePopularPostsSource", "false", "true", "bool")]))
        self.assertIn("popular posts source: switched ON by default", r["posts"][0])

    def test_mirrored_weight_added_is_not_new(self):
        added = [{"name": "ClickWeight", "type": "f64", "default": "0.4", "file": "vm-ranker/params.rs"}]
        self.assertEqual(bot.compose(entry(added=added), known={"ClickWeight"})["posts"], [])
        self.assertTrue(bot.compose(entry(added=added), known=set())["posts"])


class TestAmbiguity(unittest.TestCase):
    def test_conflicting_mirrors(self):
        e = entry(changed=[pc("ClickWeight", "0.4", "0.3"), pc("ClickWeight", "0.4", "0.35", file="vm-ranker/params.rs")])
        with self.assertRaises(bot.Ambiguous):
            bot.compose(e)

    def test_identical_mirrors_merge(self):
        e = entry(changed=[pc("ClickWeight", "0.4", "0.3"), pc("ClickWeight", "0.4", "0.3", file="vm-ranker/params.rs")])
        self.assertEqual("\n".join(bot.compose(e)["posts"]).count('"click" weight'), 1)

    def test_non_literal_numeric(self):
        with self.assertRaises(bot.Ambiguous):
            bot.compose(entry(changed=[pc("ClickWeight", "0.4", "BASE * 2.0")]))

    def test_bulk_and_root(self):
        bulk = [{"name": f"P{i}", "type": "u32", "default": "1", "file": "f.rs"} for i in range(51)]
        with self.assertRaises(bot.Ambiguous):
            bot.compose(entry(added=bulk))
        with self.assertRaises(bot.Ambiguous):
            bot.compose(entry(changed=WEIGHTS, is_root=True))

    def test_number_gate_catches_numbers_in_names(self):
        consts = [{"file": "home-mixer/x.rs", "name": "GROK_4_DIAL", "old": "0.1", "new": "0.3"}]
        with self.assertRaises(bot.Ambiguous):
            bot.compose(entry(consts=consts))


class TestGating(unittest.TestCase):
    def test_modes(self):
        self.assertFalse(bot.posting_mode({})[0])
        self.assertFalse(bot.posting_mode({**LIVE_ENV, "BOT_POSTING_ENABLED": "TRUE "})[0])
        self.assertFalse(bot.posting_mode({**LIVE_ENV, "BOT_POSTING_ENABLED": "1"})[0])
        self.assertFalse(bot.posting_mode({**LIVE_ENV, "X_ACCESS_TOKEN_SECRET": ""})[0])
        self.assertIn("X_API_KEY", bot.posting_mode({**LIVE_ENV, "X_API_KEY": "  "})[1])
        self.assertTrue(bot.posting_mode(LIVE_ENV)[0])

    def _state(self, entries, live_since="2026-10-01T00:00:00Z"):
        st = bot.baseline_state(entries, "h", NOW - timedelta(days=1), live=False)
        st["live_since"] = live_since
        return st

    def test_dry_run_never_posts_or_changes_state(self):
        old = entry(sha="b" * 40, date="2026-10-01T00:00:00Z")
        st = self._state([old])
        before = json.dumps(st, sort_keys=True)
        poster = FakePoster(fail_at=0, exc=AssertionError("must not be called"))
        new = entry(changed=WEIGHTS)
        for env in ({}, {**LIVE_ENV, "BOT_POSTING_ENABLED": "false"}, {**LIVE_ENV, "X_API_SECRET": ""}):
            st2, actions, changed = bot.run([old, new], st, env, NOW, post_fn=poster, log=quiet)
            self.assertFalse(changed)
            self.assertEqual(json.dumps(st2, sort_keys=True), before)
            self.assertEqual([a["action"] for a in actions], ["would_post"])
        self.assertEqual(poster.calls, [])

    def test_missing_state_backfills_nothing(self):
        es = [entry(sha=c * 40, changed=WEIGHTS) for c in "abc"]
        poster = FakePoster()
        st, actions, changed = bot.run(es, None, LIVE_ENV, NOW, post_fn=poster, log=quiet)
        self.assertTrue(changed)
        self.assertEqual(poster.calls, [])
        self.assertEqual({r["status"] for r in st["entries"].values()}, {"baseline"})

    def test_first_enable_backfills_nothing(self):
        old = entry(sha="b" * 40, date="2026-10-01T00:00:00Z")
        st = self._state([old], live_since=None)
        new = entry(changed=WEIGHTS)  # arrived while still in dry-run mode
        poster = FakePoster()
        st, actions, changed = bot.run([old, new], st, LIVE_ENV, NOW, post_fn=poster, log=quiet)
        self.assertEqual(poster.calls, [])
        self.assertEqual(st["entries"][new["sha"]]["status"], "baseline")
        self.assertIsNotNone(st["live_since"])
        # next run: still nothing (already handled)
        st, actions, _ = bot.run([old, new], st, LIVE_ENV, NOW, post_fn=poster, log=quiet)
        self.assertEqual(poster.calls, [])


class TestDedupAndCaps(unittest.TestCase):
    def setUp(self):
        self.old = entry(sha="0" * 40, date="2026-10-01T00:00:00Z")
        self.state = bot.baseline_state([self.old], "h", NOW - timedelta(days=2), live=True)

    def test_posts_once(self):
        new = entry(changed=WEIGHTS)
        poster = FakePoster()
        st, actions, _ = bot.run([self.old, new], self.state, LIVE_ENV, NOW, post_fn=poster, log=quiet)
        self.assertEqual(st["entries"][new["sha"]]["status"], "posted")
        n = len(poster.calls)
        self.assertGreaterEqual(n, 1)
        # replies chain to the previous post
        self.assertIsNone(poster.calls[0][1])
        for i in range(1, n):
            self.assertEqual(poster.calls[i][1], str(1000 + i))
        st, actions, changed = bot.run([self.old, new], st, LIVE_ENV, NOW, post_fn=poster, log=quiet)
        self.assertEqual(len(poster.calls), n)
        self.assertFalse(changed)

    def test_state_roundtrip(self):
        import tempfile
        new = entry(changed=WEIGHTS)
        st, _, _ = bot.run([self.old, new], self.state, LIVE_ENV, NOW, post_fn=FakePoster(), log=quiet)
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "data", "posted.json")
            bot.save_state(p, st)
            self.assertEqual(bot.load_state(p), st)

    def test_one_thread_per_run(self):
        a = entry(sha="a" * 40, date="2026-10-10T01:00:00Z", changed=WEIGHTS)
        b = entry(sha="b" * 40, date="2026-10-10T02:00:00Z", changed=[pc("PostUnexploredWeight", "0.02", "0.015")])
        poster = FakePoster()
        st, actions, _ = bot.run([b, a, self.old], self.state, LIVE_ENV, NOW, post_fn=poster, log=quiet)
        self.assertEqual(st["entries"][a["sha"]]["status"], "posted")  # oldest first
        self.assertNotIn(b["sha"], st["entries"])
        self.assertIn("defer", [x["action"] for x in actions])
        st, _, _ = bot.run([b, a, self.old], st, LIVE_ENV, NOW, post_fn=poster, log=quiet)
        self.assertEqual(st["entries"][b["sha"]]["status"], "posted")

    def test_three_per_day(self):
        poster = FakePoster()
        es = [entry(sha=f"{i}" * 40, date=f"2026-10-10T0{i}:00:00Z", changed=[pc("ClickWeight", "0.4", f"0.{i}")])
              for i in range(1, 6)]
        st = self.state
        for _ in range(5):
            st, _, _ = bot.run(es + [self.old], st, LIVE_ENV, NOW, post_fn=poster, log=quiet)
        posted = [s for s, r in st["entries"].items() if r["status"] == "posted"]
        self.assertEqual(len(posted), 3)
        # next UTC day the backlog continues
        st, _, _ = bot.run(es + [self.old], st, LIVE_ENV, NOW + timedelta(hours=16), post_fn=poster, log=quiet)
        self.assertEqual(sum(r["status"] == "posted" for r in st["entries"].values()), 4)

    def test_stale_entries_skipped(self):
        stale = entry(sha="5" * 40, date="2026-10-05T00:00:00Z", changed=WEIGHTS)
        poster = FakePoster()
        st, _, _ = bot.run([self.old, stale], self.state, LIVE_ENV, NOW, post_fn=poster, log=quiet)
        self.assertEqual(st["entries"][stale["sha"]]["status"], "skipped")
        self.assertEqual(poster.calls, [])

    def test_not_meaningful_and_ambiguous_recorded(self):
        noise = entry(sha="6" * 40, changed=[pc("VMRankerClusterId", '"A"', '"B"', "String")])
        amb = entry(sha="7" * 40, changed=[pc("ClickWeight", "0.4", "X * 2.0")])
        st, _, _ = bot.run([self.old, noise, amb], self.state, LIVE_ENV, NOW, post_fn=FakePoster(), log=quiet)
        self.assertEqual(st["entries"][noise["sha"]]["status"], "not_meaningful")
        self.assertEqual(st["entries"][amb["sha"]]["status"], "skipped")

    def test_definite_failure_retried_once_then_stops(self):
        new = entry(changed=WEIGHTS)
        err = urllib.error.HTTPError(bot.TWEET_ENDPOINT, 503, "busy", {}, io.BytesIO(b"{}"))
        poster = FakePoster(fail_at=0, exc=err)
        st, _, _ = bot.run([self.old, new], self.state, LIVE_ENV, NOW, post_fn=poster, log=quiet)
        self.assertEqual(st["entries"][new["sha"]]["status"], "failed")
        st, _, _ = bot.run([self.old, new], st, LIVE_ENV, NOW, post_fn=poster, log=quiet)
        self.assertEqual(st["entries"][new["sha"]]["attempts"], 2)
        st, actions, changed = bot.run([self.old, new], st, LIVE_ENV, NOW, post_fn=poster, log=quiet)
        self.assertFalse(changed)  # gives up after MAX_ATTEMPTS

    def test_uncertain_failure_never_retried(self):
        new = entry(changed=WEIGHTS)
        poster = FakePoster(fail_at=0, exc=TimeoutError("timed out"))
        st, _, _ = bot.run([self.old, new], self.state, LIVE_ENV, NOW, post_fn=poster, log=quiet)
        self.assertEqual(st["entries"][new["sha"]]["status"], "uncertain")
        ok = FakePoster()
        st, _, changed = bot.run([self.old, new], st, LIVE_ENV, NOW, post_fn=ok, log=quiet)
        self.assertEqual(ok.calls, [])

    def test_partial_thread_recorded(self):
        long = [pc(f"VeryLongDescriptiveParameterNameNumber{c}ForTestingThreadSplitting", "1", "2", "u32") for c in "ABCD"]
        new = entry(changed=long)
        poster = FakePoster(fail_at=1, exc=TimeoutError("x"))
        st, _, _ = bot.run([self.old, new], self.state, LIVE_ENV, NOW, post_fn=poster, log=quiet)
        rec = st["entries"][new["sha"]]
        self.assertEqual(rec["status"], "partial")
        self.assertEqual(rec["tweet_ids"], ["1001"])


class TestOAuth(unittest.TestCase):
    def test_reference_signature(self):
        # Worked example from X's "Creating a signature" documentation.
        h = bot.oauth1_header(
            "POST", "https://api.twitter.com/1.1/statuses/update.json",
            "xvz1evFS4wEEPTGEFPHBog", "kAcSOqF21Fu85e7zjz7ZN2U4ZRhfV3WpwPAoE3Z7kBw",
            "370773112-GmHxMAgYyLbNEtIKZeRNFsMKPR9EyMZeS9weJAEb", "LswwdoUaIvS8ltyTt5jkRh4J50vUPVVHtR2YPi5kE",
            params={"status": "Hello Ladies + Gentlemen, a signed OAuth request!", "include_entities": "true"},
            nonce="kYjzVBB8Y0ZFabxSWbWovY3uYSQ2pTgmZeNu2VS4cg", timestamp="1318622958")
        self.assertIn('oauth_signature="hCtSmYh%2BiHYCEqBWrE7C7hYmtUk%3D"', h)
        self.assertTrue(h.startswith("OAuth "))


@unittest.skipUnless(os.path.exists(os.path.join(ROOT, "site", "entries.json")), "site/entries.json not built")
class TestRealHistory(unittest.TestCase):
    """The dry-run examples in the README, checked against the real analysis output."""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(ROOT, "site", "entries.json"), encoding="utf-8") as f:
            cls.entries = json.load(f)["entries"]

    def post(self, short):
        e = next(x for x in self.entries if x["short"] == short)
        return "\n".join(bot.compose(e, known=bot.known_param_names(self.entries, e))["posts"])

    def test_sep29_weights(self):
        p = self.post("a707cc2")
        self.assertIn('"not interested" penalty -43.2 → -47.52 (10% harsher)', p)
        self.assertIn('"click" weight 0.4 → 0.3 (25% lower)', p)

    def test_sep30_cold_start(self):
        p = self.post("77d431a")
        self.assertIn("cold start follower cap 1,000 → 50,000 (50x higher)", p)

    def test_oct7_popular_posts(self):
        p = self.post("78460ca")
        self.assertIn('"post unexplored" weight 0.02 → 0.015 (25% lower)', p)
        self.assertIn("popular posts source: switched ON by default", p)

    def test_oct8_video_carousel(self):
        self.assertIn("new filter: video_carousel_filter", self.post("35650fb"))

    def test_noise_never_posts(self):
        for short in ("cb45b55", "0bfc279", "a389166"):  # sync noise / LFS artifact / .gitattributes
            e = next(x for x in self.entries if x["short"] == short)
            self.assertEqual(bot.compose(e, known=bot.known_param_names(self.entries, e))["posts"], [], short)

    def test_all_history_respects_limits(self):
        for e in self.entries:
            try:
                r = bot.compose(e, known=bot.known_param_names(self.entries, e))
            except bot.Ambiguous:
                continue
            self.assertLessEqual(len(r["posts"]), 4)
            for p in r["posts"]:
                self.assertLessEqual(bot.weighted_len(p), 280, e["short"])


if __name__ == "__main__":
    unittest.main()
