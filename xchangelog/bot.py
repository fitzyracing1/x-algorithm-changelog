"""Auto-poster: turn a NEW meaningful upstream sync into a short X post (or a thread of at most 4).

Pipeline (all deterministic, stdlib only):
  site/entries.json (written by `python -m xchangelog`)  ->  facts()  ->  compose()  ->  gates  ->  post or dry run

Safety model
  * Live posting only when BOT_POSTING_ENABLED == "true" AND all four X_* secrets are non-empty. Otherwise dry run.
  * Every number in a post must come from the deterministic analysis (or be derived from it by code here,
    e.g. a percentage); hand-written note titles are only used when all their numbers check out. LLM text is never used.
  * Anything ambiguous (conflicting mirrored values, non-literal numeric defaults, bulk releases, stale entries,
    a post that does not fit) is skipped, never guessed.
  * data/posted.json records every SHA that was posted/skipped so nothing posts twice. The first live run
    marks every existing entry as already handled and posts nothing (no backfill).
  * At most 1 thread per run and 3 per UTC day.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import math
import os
import re
import secrets as _secrets
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_SITE_URL = "https://fitzyracing1.github.io/x-algorithm-changelog/"
TWEET_ENDPOINT = "https://api.x.com/2/tweets"
SECRET_NAMES = ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET")

MAX_CHARS = 280
URL_WEIGHT = 23
MAX_THREAD = 4
MAX_FACTS = 4            # lead + up to 3 more
MAX_THREADS_PER_RUN = 1
MAX_THREADS_PER_DAY = 3
STALE_AFTER = timedelta(days=3)
MAX_ATTEMPTS = 2
DISCLAIMER = "Unofficial tracker; values are repo defaults, not confirmed live settings."

# Paths whose filters/scorers/sources shape the served feed. Components elsewhere (grox, simclusters, botmaker,
# visibility-filtering internals, training code) are on the site but never trigger a post on their own.
FEED_PATHS = ("home-mixer/", "thunder/", "vm-ranker/", "xai-value-model/", "candidate-pipeline/")
POST_KINDS = ("Candidate source", "Filter", "Scorer")
TEST_PATH = re.compile(r"(^|/)(tests?|testing|testdata)/|_test\.\w+$|(^|/)test_[^/]+$|Test\.\w+$")
# Outside the feed-serving code, only threshold-like constants (e.g. Grox reply-spam follower thresholds) can trigger.
OUT_OF_FEED_TRIGGER_CONST = re.compile(r"THRESHOLD|FOLLOWER")
BIG_CONST_RATIO = 1.2     # constants must move by >= 20% to count as a "big threshold change"
BULK_PARAMS = 50


# ---------------------------------------------------------------------------
# X weighted character counting (twitter-text v3 rules, conservative)
# ---------------------------------------------------------------------------
_LIGHT_RANGES = ((0, 4351), (8192, 8205), (8208, 8223), (8242, 8247))
# Real URLs plus anything X might auto-link as a bare domain (e.g. "param.rs"): both count as 23.
_URL_RE = re.compile(r"https?://\S+|\b[\w-]+(?:\.[\w-]+)*\.[a-z]{2,}\b(?:/\S*)?", re.I)


def _char_weight(ch: str) -> int:
    cp = ord(ch)
    return 1 if any(lo <= cp <= hi for lo, hi in _LIGHT_RANGES) else 2


def weighted_len(text: str) -> int:
    text = unicodedata.normalize("NFC", text)
    total, pos = 0, 0
    for m in _URL_RE.finditer(text):
        if not re.search(r"[a-z]", m.group(0), re.I):
            continue  # pure numbers like 0.015 are not URLs
        total += sum(_char_weight(c) for c in text[pos:m.start()]) + URL_WEIGHT
        pos = m.end()
    return total + sum(_char_weight(c) for c in text[pos:])


def _bare_domains(text: str) -> list[str]:
    out = []
    for m in _URL_RE.finditer(text):
        tok = m.group(0)
        if not tok.lower().startswith("http") and re.search(r"[a-z]", tok, re.I):
            out.append(tok)
    return out


# ---------------------------------------------------------------------------
# Number helpers
# ---------------------------------------------------------------------------
_NUM_LIT = re.compile(r"[-+]?(?:\d[\d_]*\.?\d*(?:[eE][-+]?\d+)?|\.\d+)(?:_?(?:f32|f64|u8|u16|u32|u64|usize|i32|i64))?")


def parse_num(v) -> float | None:
    """Parse a plain numeric literal ('0.4', '-43.2', '125_000', '1.0f64'); anything else -> None."""
    if v is None:
        return None
    s = str(v).strip().replace("\u2212", "-")
    if not _NUM_LIT.fullmatch(s):
        return None
    s = re.sub(r"_?(f32|f64|u8|u16|u32|u64|usize|i32|i64)$", "", s).replace("_", "")
    try:
        x = float(s)
    except ValueError:
        return None
    return x if math.isfinite(x) else None


def fmt_num(v) -> str:
    """Exact value, readable: '0.0' -> '0', '125_000' -> '125,000', '-47.52' stays."""
    x = parse_num(v)
    if x is None:
        return str(v)
    if x == int(x) and abs(x) < 1e15:
        n = int(x)
        return f"{n:,}" if abs(n) >= 1000 else str(n)
    s = str(v).strip().replace("_", "").replace("\u2212", "-")
    s = re.sub(r"(f32|f64)$", "", s)
    return s.lstrip("+")


def _round_pct(p: float) -> str:
    return f"{p:.1f}".rstrip("0").rstrip(".") if p < 10 else str(int(round(p)))


def _round_ratio(r: float) -> str:
    return f"{r:.1f}".rstrip("0").rstrip(".") if r < 10 else str(int(round(r)))


class Derived:
    """Collects numbers the composer computed from analysis values (percentages, ratios, durations, counts)."""

    def __init__(self):
        self.values: set[float] = set()

    def add(self, s: str) -> str:
        x = parse_num(s.replace(",", ""))
        if x is not None:
            self.values.add(abs(x))
        return s


# ---------------------------------------------------------------------------
# Names
# ---------------------------------------------------------------------------
_ACRONYMS = {"oon": "out-of-network", "vqv": "VQV", "vm": "VM", "moe": "MoE", "sid": "SID", "id": "ID", "ids": "IDs",
             "ts": "TS", "vf": "VF", "thompson": "Thompson", "phoenix": "Phoenix", "grok": "Grok", "grox": "Grox", "nsfw": "NSFW", "dm": "DM", "url": "URL", "secs": "secs", "ms": "ms"}


def humanize(name: str) -> str:
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", " ", name).replace("_", " ").lower().strip()
    return " ".join(_ACRONYMS.get(w, w) for w in s.split())


def is_weight(name: str) -> bool:
    return name.endswith("Weight") or "WeightBoost" in name


def weight_label(name: str) -> str:
    base = re.sub(r"Weight$", "", name)
    base = re.sub(r"^Cont(?=[A-Z])", "", base)  # ContClickDwellTimeWeight -> click dwell time
    return humanize(base)


def flag_label(name: str) -> str:
    h = humanize(name)
    return h[len("enable "):] if h.startswith("enable ") else h


_DURATION_UNITS = (("Secs", 1), ("Seconds", 1), ("Minutes", 60), ("Hours", 3600), ("Ms", 0.001), ("Millis", 0.001))


def _human_duration(seconds: float) -> str | None:
    for unit, size in (("d", 86400), ("h", 3600), ("min", 60), ("s", 1)):
        if seconds >= size and seconds % size == 0:
            return f"{int(seconds // size)}{unit}"
    return None


# ---------------------------------------------------------------------------
# Facts: the only things a post may say
# ---------------------------------------------------------------------------
class Ambiguous(Exception):
    pass


def _change_suffix(old: float, new: float, d: Derived, penalty: bool = False, weight: bool = False) -> str:
    if old == new:
        return ""
    if old == 0:
        return " (previously unused)" if weight else ""
    if new == 0:
        return " (now unused)" if weight else ""
    if (old < 0) != (new < 0):
        return " (sign flipped)"
    ratio = abs(new) / abs(old)
    if penalty:
        pct = abs(new - old) / abs(old) * 100
        word = "harsher" if abs(new) > abs(old) else "softer"
        return f" ({d.add(_round_pct(pct))}% {word})"
    if ratio >= 2:
        return f" ({d.add(_round_ratio(ratio))}x higher)"
    if ratio <= 0.5:
        return f" ({d.add(_round_ratio(1 / ratio))}x lower)"
    pct = abs(new - old) / abs(old) * 100
    return f" ({d.add(_round_pct(pct))}% {'higher' if new > old else 'lower'})"


def _magnitude(old: float, new: float) -> float:
    if old == new:
        return 0.0
    if old == 0 or new == 0 or (old < 0) != (new < 0):
        return 10.0
    return abs(math.log(abs(new) / abs(old)))


def _merge_mirrored(changed: list[dict]) -> list[dict]:
    """The same param can be mirrored in home-mixer and vm-ranker. Identical changes merge; conflicting ones are ambiguous."""
    by_name: dict[str, dict] = {}
    for p in changed:
        prev = by_name.get(p["name"])
        if prev is None:
            by_name[p["name"]] = dict(p)
        elif (prev["old"], prev["new"]) != (p["old"], p["new"]):
            raise Ambiguous(f"{p['name']} changed differently in {prev['file']} and {p['file']}")
    return list(by_name.values())


def _where(path: str) -> str:
    """Context for constants outside the feed-serving code, e.g. grox/flows/reply_spam/x.py -> ' (grox reply spam)'."""
    parts = path.split("/")
    if path.startswith(FEED_PATHS) or len(parts) < 2:
        return ""
    if parts[0] == "grox" and len(parts) > 3 and parts[1] == "flows":
        return f" (grox {parts[2].replace('_', ' ')})"
    return f" ({parts[0]})"


def known_param_names(entries: list[dict], before: dict) -> set[str]:
    """Param names defined by entries older than `before` (to tell a genuinely new weight from a mirror copy)."""
    known: set[str] = set()
    for e in sorted(entries, key=lambda x: _parse_dt(x["date"])):
        if e["sha"] == before["sha"] or _parse_dt(e["date"]) > _parse_dt(before["date"]):
            break
        p = e.get("params", {})
        known |= {x["name"] for x in p.get("added", []) + p.get("moved", [])}
        known -= {x["name"] for x in p.get("removed", []) if not x.get("still_defined_in")}
    return known


def facts(entry: dict, known: set[str] | None = None) -> tuple[list[dict], Derived]:
    """Return (facts sorted by importance, derived numbers). Raises Ambiguous when the entry must not be auto-posted.

    Each fact: {"text", "kind", "score", "trigger": bool}. Only trigger facts make an entry post-worthy;
    supporting facts can ride along but never cause a post on their own.
    """
    d = Derived()
    if entry.get("class") not in ("code", "docs"):
        return [], d
    if entry.get("is_root"):
        raise Ambiguous("initial import; needs a hand-written post")
    params = entry.get("params", {})
    if len(params.get("added", [])) > BULK_PARAMS:
        raise Ambiguous(f"bulk release ({len(params['added'])} params added); needs a hand-written post")

    out: list[dict] = []
    for p in _merge_mirrored(params.get("changed", [])):
        name, typ = p["name"], p.get("type", "")
        if typ == "bool" or {p["old"], p["new"]} <= {"true", "false"}:
            if {p["old"], p["new"]} != {"true", "false"}:
                raise Ambiguous(f"{name}: unclear bool change {p['old']} -> {p['new']}")
            on = p["new"] == "true"
            out.append({"kind": "flag", "trigger": True, "score": 80 + (1 if on else 0),
                        "text": f"{flag_label(name)}: switched {'ON' if on else 'OFF'} by default"})
            continue
        old, new = parse_num(p["old"]), parse_num(p["new"])
        if old is None or new is None:
            if re.fullmatch(r"[fiu](8|16|32|64)|usize|isize", typ or ""):
                raise Ambiguous(f"{name}: non-literal numeric default {p['old']} -> {p['new']}")
            if str(p["old"]).startswith('"') and str(p["new"]).startswith('"') and len(p["old"]) + len(p["new"]) < 60:
                out.append({"kind": "string", "trigger": False, "score": 5,
                            "text": f"{humanize(name)}: {p['old']} → {p['new']}"})
            continue
        if is_weight(name):
            penalty = old < 0 or new < 0
            label = f"\"{weight_label(name)}\" {'penalty' if penalty else 'weight'}"
            out.append({"kind": "weight", "trigger": True, "score": 100 + abs(new - old),
                        "text": f"{label} {fmt_num(p['old'])} → {fmt_num(p['new'])}{_change_suffix(old, new, d, penalty, weight=True)}"})
            continue
        text = f"{humanize(name)} {fmt_num(p['old'])} → {fmt_num(p['new'])}"
        for unit, size in _DURATION_UNITS:
            if name.endswith(unit):
                a, b = _human_duration(old * size), _human_duration(new * size)
                if a and b:
                    text = f"{humanize(re.sub(unit + '$', '', name))} {a} → {b}"
                    for tok in re.findall(r"\d+", a + " " + b):
                        d.add(tok)
                break
        out.append({"kind": "param", "trigger": True, "score": 60 + _magnitude(old, new),
                    "text": text + _change_suffix(old, new, d)})

    known = known or set()
    for p in params.get("added", []):
        if p["name"] in known:
            continue  # already defined elsewhere (e.g. mirrored into vm-ranker/params.rs): not a new weight or flag
        if is_weight(p["name"]):
            v = parse_num(p.get("default"))
            if v is None:
                raise Ambiguous(f"new weight {p['name']} has a non-literal default")
            if v != 0:
                penalty = v < 0
                out.append({"kind": "weight", "trigger": True, "score": 95,
                            "text": f"new \"{weight_label(p['name'])}\" {'penalty' if penalty else 'weight'} = {fmt_num(p['default'])}"})
            else:
                out.append({"kind": "new_param", "trigger": False, "score": 8,
                            "text": f"new \"{weight_label(p['name'])}\" weight added at 0 (no effect yet)"})
        elif p.get("type") == "bool" and p.get("default") in ("true", "false"):
            on = p["default"] == "true"
            out.append({"kind": "new_param", "trigger": on, "score": 50 if on else 10,
                        "text": f"new flag \"{flag_label(p['name'])}\" ({'ON' if on else 'off'} by default)"})
    for p in params.get("removed", []):
        if p.get("still_defined_in") or not is_weight(p["name"]):
            continue
        v = parse_num(p.get("default"))
        if v not in (None, 0):
            out.append({"kind": "weight", "trigger": True, "score": 90,
                        "text": f"\"{weight_label(p['name'])}\" weight removed (was {fmt_num(p['default'])})"})

    comps = entry.get("components", {})
    for key, word in (("added", "new"), ("removed", "removed")):
        groups: dict[str, list[str]] = {}
        for c in comps.get(key, []):
            if c.get("kind") in POST_KINDS and c.get("path", "").startswith(FEED_PATHS) and not TEST_PATH.search(c.get("path", "")):
                groups.setdefault(c["kind"], []).append(c["name"])
        for kind, names in groups.items():
            plural = (kind.lower() + "s") if len(names) > 1 else kind.lower()
            shown = names[:3]
            more = f" and {d.add(str(len(names) - 3))} more" if len(names) > 3 else ""
            if len(names) > 3:
                d.add(str(len(names)))
            prio = {"Candidate source": 3, "Filter": 2, "Scorer": 1}[kind]
            out.append({"kind": "component", "trigger": True, "score": 70 + prio + (0.5 if key == "added" else 0),
                        "text": f"{word} {plural}: {', '.join(shown)}{more}"})

    for c in entry.get("constants", {}).get("changed", []):
        if TEST_PATH.search(c.get("file", "")):
            continue
        old, new = parse_num(c["old"]), parse_num(c["new"])
        if {c["old"], c["new"]} == {"true", "false"} and c.get("file", "").startswith(FEED_PATHS):
            out.append({"kind": "constant", "trigger": True, "score": 75,
                        "text": f"{humanize(c['name'])}: switched {'ON' if c['new'] == 'true' else 'OFF'}"})
            continue
        if old is None or new is None or old == 0 or new == 0 or (old < 0) != (new < 0):
            continue
        r = max(abs(new) / abs(old), abs(old) / abs(new))
        if r < BIG_CONST_RATIO:
            continue
        trig = c.get("file", "").startswith(FEED_PATHS) or bool(OUT_OF_FEED_TRIGGER_CONST.search(c["name"]))
        out.append({"kind": "constant", "trigger": trig, "score": (40 if trig else 4) + _magnitude(old, new),
                    "text": f"{humanize(c['name'])}{_where(c['file'])} {fmt_num(c['old'])} → {fmt_num(c['new'])}{_change_suffix(old, new, d)}"})

    out.sort(key=lambda f: -f["score"])
    return out, d


def allowed_numbers(entry: dict) -> set[float]:
    vals: set[float] = set()

    def add(v):
        if isinstance(v, (int, float)):
            vals.add(abs(float(v)))
            return
        for tok in re.findall(r"\d[\d_,]*\.?\d*", str(v)):
            x = parse_num(tok.replace(",", "").rstrip("."))
            if x is not None:
                vals.add(abs(x))

    p = entry.get("params", {})
    for x in p.get("changed", []):
        add(x["old"]); add(x["new"])
    for key in ("added", "removed", "moved"):
        for x in p.get(key, []):
            add(x.get("default"))
    for key in ("changed",):
        for x in entry.get("constants", {}).get(key, []):
            add(x["old"]); add(x["new"])
    for x in entry.get("constants", {}).get("added", []) + entry.get("constants", {}).get("removed", []):
        add(x.get("value"))
    for k, v in entry.get("stats", {}).items():
        add(v)
    return vals


def verified_note_title(entry: dict) -> str | None:
    """A hand-written note title, only if every number in it matches the deterministic analysis."""
    s = entry.get("summary") or {}
    if s.get("source") != "note" or not s.get("title"):
        return None
    title = s["title"].replace("`", "").replace("\u2212", "-").strip()
    if "#" in title or "@" in title or _bare_domains(title):
        return None
    allowed = allowed_numbers(entry)
    for tok in re.findall(r"\d[\d,_]*\.?\d*", title):
        x = parse_num(tok.replace(",", "").rstrip("."))
        if x is None or abs(x) not in allowed:
            return None
    return title


# ---------------------------------------------------------------------------
# Composition
# ---------------------------------------------------------------------------
def short_date(iso: str) -> str:
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(timezone.utc)
    return f"{dt:%b} {dt.day}"


def entry_url(entry: dict, site_url: str) -> str:
    return site_url.rstrip("/") + f"/entries/{entry['short']}.html"


def compose(entry: dict, site_url: str = DEFAULT_SITE_URL, known: set[str] | None = None) -> dict:
    """Return {"posts": [...], "facts": [...], "headline_source": ...} or raise Ambiguous / return posts=[] if nothing meaningful."""
    fs, derived = facts(entry, known)
    triggers = [f for f in fs if f["trigger"]]
    if not triggers:
        return {"posts": [], "facts": fs, "reason": "no meaningful change"}
    lead = triggers[0]
    shown = [lead] + [f for f in fs if f is not lead][:MAX_FACTS - 1]
    title = verified_note_title(entry) if lead["kind"] in ("component", "constant") else None
    header = f"X algorithm update ({short_date(entry['date'])}): "
    if title:
        head_text, bullets = header + title, shown
    else:
        head_text, bullets = header + lead["text"], shown[1:]
    links = f"Details: {entry_url(entry, site_url)}\nCommit: {entry['url']}"

    def build(bul: list[dict]) -> list[str] | None:
        shown_ids = {id(x) for x in bul} | ({id(lead)} if not title else set())
        more = sum(1 for f in fs if f["trigger"] and id(f) not in shown_ids)
        tail_lines = []
        if more > 0:
            tail_lines.append(f"+{derived.add(str(more))} more on the details page.")
        tail = "\n".join(tail_lines + [links, DISCLAIMER])
        lines = [f"- {b['text']}" for b in bul]
        single = head_text + ("\n" + "\n".join(lines) if lines else "") + "\n\n" + tail
        if weighted_len(single) <= MAX_CHARS:
            return [single]
        posts, cur = [], head_text
        for ln in lines:
            cand = cur + "\n" + ln
            if weighted_len(cand) <= MAX_CHARS:
                cur = cand
            else:
                posts.append(cur)
                cur = ln
        last = cur + "\n\n" + tail
        if weighted_len(last) <= MAX_CHARS:
            posts.append(last)
        else:
            posts.append(cur)
            posts.append(tail)
        if len(posts) > MAX_THREAD or any(weighted_len(p) > MAX_CHARS for p in posts):
            return None
        return posts

    def best(bul: list[dict]) -> list[str] | None:
        for k in range(len(bul), -1, -1):
            got = build(bul[:k])
            if got:
                return got
        return None

    # Supporting facts (e.g. a new flag that is off) only ride along if they don't make the thread longer.
    trig_only = [b for b in bullets if b["trigger"]]
    a, b = best(trig_only), best(bullets)
    posts = b if (b and (not a or len(b) <= len(a))) else a
    if not posts:
        raise Ambiguous("could not fit the lead change into a 4-post thread")
    check_posts(posts, entry, derived)
    return {"posts": posts, "facts": fs, "headline_source": "note" if title else "analysis"}


def check_posts(posts: list[str], entry: dict, derived: Derived) -> None:
    """Final accuracy + policy gate. Raises Ambiguous on any violation."""
    allowed = allowed_numbers(entry) | derived.values
    date_tok = short_date(entry["date"])
    for p in posts:
        n = weighted_len(p)
        if n > MAX_CHARS:
            raise Ambiguous(f"post is {n} weighted chars")
        if re.search(r"(^|\s)[@#＠＃]\w", p):
            raise Ambiguous("post contains a mention or hashtag")
        body = re.sub(r"https?://\S+", " ", p).replace(date_tok, " ")
        if _bare_domains(body):
            raise Ambiguous(f"post contains a bare domain-like token: {_bare_domains(body)}")
        for tok in re.findall(r"(?<![\w.])\d[\d,]*\.?\d*", body):
            x = parse_num(tok.replace(",", "").rstrip("."))
            if x is None or abs(x) not in allowed:
                raise Ambiguous(f"unverified number {tok!r} in post")
    if len(posts) > MAX_THREAD:
        raise Ambiguous("thread too long")
    if not any("Unofficial" in p for p in posts):
        raise Ambiguous("disclaimer missing")


# ---------------------------------------------------------------------------
# State (dedup)
# ---------------------------------------------------------------------------
def load_state(path: str) -> dict | None:
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_state(path: str, state: dict) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=1, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)


def baseline_state(entries: list[dict], head: str | None, now: datetime, live: bool) -> dict:
    st = {"version": 1, "live_since": now.strftime("%Y-%m-%dT%H:%M:%SZ") if live else None,
          "baseline": {"created_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "upstream_head": head}, "entries": {}}
    mark_baseline(st, entries, now)
    return st


def mark_baseline(state: dict, entries: list[dict], now: datetime) -> int:
    n = 0
    for e in entries:
        if e["sha"] not in state["entries"]:
            state["entries"][e["sha"]] = {"short": e["short"], "date": e["date"], "status": "baseline",
                                          "at": now.strftime("%Y-%m-%dT%H:%M:%SZ")}
            n += 1
    return n


def handled(state: dict, sha: str) -> bool:
    rec = state["entries"].get(sha)
    if rec is None:
        return False
    return not (rec["status"] == "failed" and rec.get("attempts", 0) < MAX_ATTEMPTS)


def posted_today(state: dict, now: datetime) -> int:
    day = now.strftime("%Y-%m-%d")
    return sum(1 for r in state["entries"].values() if r["status"] in ("posted", "partial", "uncertain") and r.get("at", "").startswith(day))


# ---------------------------------------------------------------------------
# Gating
# ---------------------------------------------------------------------------
def posting_mode(env: dict) -> tuple[bool, str]:
    """(live, reason). Live only when the repo variable says so AND every secret is present."""
    if (env.get("BOT_POSTING_ENABLED") or "").strip() != "true":
        return False, "BOT_POSTING_ENABLED is not 'true'"
    missing = [k for k in SECRET_NAMES if not (env.get(k) or "").strip()]
    if missing:
        return False, "missing secrets: " + ", ".join(missing)
    return True, "enabled"


# ---------------------------------------------------------------------------
# X API v2 (OAuth 1.0a user context, HMAC-SHA1, stdlib only)
# ---------------------------------------------------------------------------
def _pct(s: str) -> str:
    return urllib.parse.quote(str(s), safe="-._~")


def oauth1_header(method: str, url: str, consumer_key: str, consumer_secret: str, token: str, token_secret: str,
                  params: dict | None = None, nonce: str | None = None, timestamp: str | None = None) -> str:
    """Authorization header per RFC 5849. `params` are query/form params (a JSON body is NOT signed)."""
    oauth = {
        "oauth_consumer_key": consumer_key,
        "oauth_nonce": nonce or _secrets.token_hex(16),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": timestamp or str(int(time.time())),
        "oauth_token": token,
        "oauth_version": "1.0",
    }
    parts = urllib.parse.urlsplit(url)
    base_url = f"{parts.scheme.lower()}://{parts.netloc.lower()}{parts.path}"
    all_params = list(oauth.items()) + list((params or {}).items()) + urllib.parse.parse_qsl(parts.query)
    norm = "&".join(f"{k}={v}" for k, v in sorted((_pct(k), _pct(v)) for k, v in all_params))
    base = "&".join([method.upper(), _pct(base_url), _pct(norm)])
    key = f"{_pct(consumer_secret)}&{_pct(token_secret)}"
    sig = base64.b64encode(hmac.new(key.encode(), base.encode(), hashlib.sha1).digest()).decode()
    oauth["oauth_signature"] = sig
    return "OAuth " + ", ".join(f'{_pct(k)}="{_pct(v)}"' for k, v in sorted(oauth.items()))


class PostError(Exception):
    def __init__(self, msg: str, definite: bool, ids: list[str]):
        super().__init__(msg)
        self.definite, self.ids = definite, ids


def create_post(text: str, creds: dict, reply_to: str | None = None, timeout: int = 30) -> str:
    body = {"text": text}
    if reply_to:
        body["reply"] = {"in_reply_to_tweet_id": reply_to}
    req = urllib.request.Request(TWEET_ENDPOINT, data=json.dumps(body).encode(), method="POST", headers={
        "Authorization": oauth1_header("POST", TWEET_ENDPOINT, creds["X_API_KEY"], creds["X_API_SECRET"],
                                       creds["X_ACCESS_TOKEN"], creds["X_ACCESS_TOKEN_SECRET"]),
        "Content-Type": "application/json", "User-Agent": "x-algorithm-changelog-bot/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.load(resp)
    return str(data["data"]["id"])


def post_thread(posts: list[str], creds: dict, post_fn=create_post) -> list[str]:
    ids: list[str] = []
    for text in posts:
        try:
            ids.append(post_fn(text, creds, ids[-1] if ids else None))
        except urllib.error.HTTPError as e:  # the server answered: nothing was created for this post
            detail = e.read().decode("utf-8", "replace")[:300] if hasattr(e, "read") else ""
            raise PostError(f"HTTP {e.code}: {detail}", definite=True, ids=ids)
        except Exception as e:  # timeout / connection reset: the post may or may not exist
            raise PostError(f"{type(e).__name__}: {e}", definite=False, ids=ids)
    return ids


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
def _parse_dt(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


def run(entries: list[dict], state: dict | None, env: dict, now: datetime, site_url: str = DEFAULT_SITE_URL,
        head: str | None = None, post_fn=create_post, log=print) -> tuple[dict, list[dict], bool]:
    """Returns (state, actions, state_changed). `entries` may be in any order."""
    live, why = posting_mode(env)
    log(f"[bot] mode: {'LIVE' if live else 'DRY RUN'} ({why})")
    entries = sorted(entries, key=lambda e: _parse_dt(e["date"]))
    actions: list[dict] = []
    changed = False
    ts = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    if state is None:
        state = baseline_state(entries, head, now, live)
        log(f"[bot] no state file: marked {len(entries)} existing entries as already handled (no backfill)")
        return state, [{"action": "baseline", "count": len(entries)}], True
    if live and not state.get("live_since"):
        n = mark_baseline(state, entries, now)
        state["live_since"] = ts
        log(f"[bot] first live run: marked {n} new entries as already handled (no backfill); posting starts next sync")
        return state, [{"action": "baseline", "count": n}], True

    threads_this_run = 0
    for e in entries:
        if handled(state, e["sha"]):
            continue
        rec = state["entries"].get(e["sha"], {})
        base = {"short": e["short"], "date": e["date"], "at": ts}
        if now - _parse_dt(e["date"]) > STALE_AFTER:
            actions.append({"action": "skip", "sha": e["sha"], "reason": "stale (older than 3 days)"})
            if live:
                state["entries"][e["sha"]] = {**base, "status": "skipped", "reason": "stale"}; changed = True
            continue
        try:
            result = compose(e, site_url, known_param_names(entries, e))
        except Ambiguous as exc:
            log(f"[bot] {e['short']}: skipped (ambiguous: {exc})")
            actions.append({"action": "skip", "sha": e["sha"], "reason": f"ambiguous: {exc}"})
            if live:
                state["entries"][e["sha"]] = {**base, "status": "skipped", "reason": f"ambiguous: {exc}"}; changed = True
            continue
        if not result["posts"]:
            log(f"[bot] {e['short']}: nothing meaningful to post")
            actions.append({"action": "skip", "sha": e["sha"], "reason": result["reason"]})
            if live:
                state["entries"][e["sha"]] = {**base, "status": "not_meaningful"}; changed = True
            continue
        if threads_this_run >= MAX_THREADS_PER_RUN or posted_today(state, now) >= MAX_THREADS_PER_DAY:
            log(f"[bot] {e['short']}: deferred (cap: {MAX_THREADS_PER_RUN}/run, {MAX_THREADS_PER_DAY}/day)")
            actions.append({"action": "defer", "sha": e["sha"]})
            continue
        threads_this_run += 1
        posts = result["posts"]
        if not live:
            log(f"[bot] {e['short']}: DRY RUN, would post {len(posts)} post(s):")
            for i, p in enumerate(posts, 1):
                log(f"--- post {i}/{len(posts)} ({weighted_len(p)}/280) ---\n{p}")
            actions.append({"action": "would_post", "sha": e["sha"], "posts": posts})
            continue
        creds = {k: env[k] for k in SECRET_NAMES}
        try:
            ids = post_thread(posts, creds, post_fn)
            state["entries"][e["sha"]] = {**base, "status": "posted", "tweet_ids": ids}
            log(f"[bot] {e['short']}: posted {len(ids)} post(s): {ids}")
            actions.append({"action": "posted", "sha": e["sha"], "ids": ids})
        except PostError as exc:
            if exc.ids:
                status = "partial"
            elif exc.definite:
                status = "failed"
            else:
                status = "uncertain"
            state["entries"][e["sha"]] = {**base, "status": status, "error": str(exc)[:300], "tweet_ids": exc.ids,
                                          "attempts": rec.get("attempts", 0) + 1}
            log(f"[bot] {e['short']}: {status}: {exc}")
            actions.append({"action": status, "sha": e["sha"], "error": str(exc)})
        changed = True
    return state, actions, changed


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Post new meaningful x-algorithm syncs to X (dry run unless enabled).")
    ap.add_argument("--entries", default=os.path.join(ROOT, "site", "entries.json"))
    ap.add_argument("--state", default=os.path.join(ROOT, "data", "posted.json"))
    ap.add_argument("--site-url", default=os.environ.get("SITE_URL", DEFAULT_SITE_URL))
    ap.add_argument("--dry-run", action="store_true", help="Never post, even if enabled")
    ap.add_argument("--preview", nargs="+", metavar="SHA", help="Print the post(s) for these entries, ignoring state; never posts")
    args = ap.parse_args(argv)
    with open(args.entries, encoding="utf-8") as f:
        data = json.load(f)
    entries = data["entries"]
    site_url = args.site_url if args.site_url.endswith("/") else args.site_url + "/"

    if args.preview:
        rc = 0
        for want in args.preview:
            match = [e for e in entries if e["sha"].startswith(want) or e["short"] == want[:7]]
            if not match:
                print(f"== {want}: not found"); rc = 1; continue
            e = match[0]
            print(f"== {e['short']} ({e['date']})")
            try:
                r = compose(e, site_url, known_param_names(entries, e))
            except Ambiguous as exc:
                print(f"   SKIP (ambiguous): {exc}"); continue
            if not r["posts"]:
                print(f"   SKIP: {r['reason']}"); continue
            for i, p in enumerate(r["posts"], 1):
                print(f"--- post {i}/{len(r['posts'])} ({weighted_len(p)}/280) ---\n{p}")
        return rc

    env = dict(os.environ)
    if args.dry_run:
        env["BOT_POSTING_ENABLED"] = "false"
    state = load_state(args.state)
    state, actions, changed = run(entries, state, env, datetime.now(timezone.utc), site_url, data.get("upstream_head"))
    if not actions:
        print("[bot] nothing new since the last run")
    if changed:
        save_state(args.state, state)
        print(f"[bot] state written to {args.state}")
    failed = [a for a in actions if a["action"] in ("failed", "partial", "uncertain")]
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
