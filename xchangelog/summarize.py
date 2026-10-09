"""Turn a structured analysis into plain-English notes.

Precedence for the human-readable summary of an entry:
  1. A hand-written note in notes/<short-sha>.md (reviewed against the diff).
  2. A cached LLM summary in data/llm/<short-sha>.md (only created when an API key is set).
  3. The deterministic summary built here (always available, no network, no key).
"""
from __future__ import annotations

import json
import os
import re
import urllib.request

MAX_LIST = 8


def humanize(name: str) -> str:
    """'ColdStartMaxPostAgeSecs' -> 'cold start max post age secs'."""
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", " ", name)
    return s.replace("_", " ").lower().strip()


def _fmt_list(items: list[str], limit: int = MAX_LIST) -> str:
    if len(items) <= limit:
        if len(items) <= 1:
            return "".join(items)
        return ", ".join(items[:-1]) + " and " + items[-1]
    return ", ".join(items[:limit]) + f" and {len(items) - limit} more"


def _plural(n: int, word: str, plural: str | None = None) -> str:
    return f"{n} {word if n == 1 else (plural or word + 's')}"


def is_weight(name: str) -> bool:
    return name.endswith("Weight") or "WeightBoost" in name or name.endswith("Weights")


def describe_param_change(p: dict) -> str:
    old, new = p["old"], p["new"]
    if p["type"] == "bool" and {old, new} == {"true", "false"}:
        verb = "now ON by default" if new == "true" else "now OFF by default"
        return f"`{p['name']}` ({humanize(p['name'])}) is {verb}" + _where(p)
    return f"`{p['name']}`: `{old}` → `{new}`" + _where(p)


def _where(p: dict) -> str:
    files = p.get("files") or ([p["file"]] if p.get("file") else [])
    if not files or files == ["home-mixer/params/param.rs"]:
        return ""
    return " (in " + " and ".join(f"`{f}`" for f in files) + ")"


def deterministic(entry: dict) -> dict:
    """Return {'headline': str, 'bullets': [str], 'tags': [str]} using only analysed facts."""
    bullets: list[str] = []
    tags: list[str] = []
    params = entry["params"]
    comps = entry["components"]
    st = entry["stats"]

    if entry["class"] == "noop":
        return {"headline": "No functional change (sync metadata / formatting only).", "bullets": [], "tags": ["no-op"]}

    # The same param can be mirrored in several files (home-mixer + vm-ranker); merge identical changes.
    merged: dict[tuple, dict] = {}
    for p in params["changed"]:
        k = (p["name"], p["old"], p["new"])
        if k in merged:
            merged[k]["files"].append(p["file"])
        else:
            merged[k] = {**p, "files": [p["file"]]}
    changed = list(merged.values())
    weight_changes = [p for p in changed if is_weight(p["name"])]
    other_changes = [p for p in changed if not is_weight(p["name"])]
    if weight_changes:
        tags.append("ranking weights")
        bullets.append("Ranking weight defaults changed: " + _fmt_list([describe_param_change(p) for p in weight_changes]) + ".")
    flips = [p for p in other_changes if p["type"] == "bool"]
    values = [p for p in other_changes if p["type"] != "bool"]
    if flips:
        tags.append("feature flags")
        bullets.append("Feature switches flipped: " + _fmt_list([describe_param_change(p) for p in flips]) + ".")
    if values:
        tags.append("params")
        bullets.append("Parameter defaults changed: " + _fmt_list([describe_param_change(p) for p in values]) + ".")

    if params.get("moved"):
        bullets.append("Moved between files: " + _fmt_list([f"`{p['name']}` ({p['from']} → {p['file']})" for p in params["moved"]]) + ".")
    moved = [p for p in params["removed"] if p.get("still_defined_in")]
    if moved:
        by_file: dict[str, list[str]] = {}
        for p in moved:
            by_file.setdefault(p["still_defined_in"], []).append(f"`{p['name']}`")
        for f, names in by_file.items():
            bullets.append(f"{_plural(len(names), 'parameter')} no longer defined in the changed file(s) but still defined in `{f}` (moved, not deleted): " + _fmt_list(names) + ".")
    new_files = {f["path"] for f in entry["files"] if f["status"] == "A"}
    bulk_new = {}
    for p in params["added"]:
        if p.get("file") in new_files:
            bulk_new.setdefault(p["file"], []).append(p)
    bulk_new = {f: ps for f, ps in bulk_new.items() if len(ps) > 10}
    for f, ps in bulk_new.items():
        weights = [p for p in ps if is_weight(p["name"])]
        bullets.append(f"New parameter file `{f}` defines {len(ps)} params" +
                       (f", including {_plural(len(weights), 'ranking weight')}" if weights else "") +
                       " (see the full analysis for every default).")
        tags.append("params")
    for kind_word, key in (("Added", "added"), ("Removed", "removed")):
        ps = [p for p in params[key] if not p.get("still_defined_in") and not (key == "added" and p.get("file") in bulk_new)]
        if not ps:
            continue
        flags = [p for p in ps if p["type"] == "bool"]
        rest = [p for p in ps if p["type"] != "bool"]
        if flags:
            desc = [f"`{p['name']}`" + (f" (default {'on' if p['default'] == 'true' else 'off'})" if key == "added" else "") for p in flags]
            bullets.append(f"{kind_word} {_plural(len(flags), 'feature flag')}: " + _fmt_list(desc) + ".")
        if rest:
            desc = [f"`{p['name']}`" + (f" = `{p['default'][:40]}`" if key == "added" else "") for p in rest]
            bullets.append(f"{kind_word} {_plural(len(rest), 'config parameter')}: " + _fmt_list(desc) + ".")
        tags.append("params")

    by_kind: dict[tuple[str, str], list[str]] = {}
    hm_kind: dict[tuple[str, str], list[str]] = {}  # feed-serving (home-mixer) components lead headlines
    for key in ("added", "removed"):
        for c in sorted(comps[key], key=lambda c: (not c["path"].startswith("home-mixer/"), c["path"])):
            by_kind.setdefault((key, c["kind"]), []).append(f"`{c['name']}`")
            if c["path"].startswith("home-mixer/"):
                hm_kind.setdefault((key, c["kind"]), []).append(f"`{c['name']}`")
    for (key, kind), names in sorted(by_kind.items()):
        word = "New" if key == "added" else "Removed"
        bullets.append(f"{word} {kind.lower()}{'s' if len(names) > 1 else ''}: " + _fmt_list(names) + ".")
        tags.append(kind.lower() + "s")
    if comps["renamed"]:
        bullets.append("Renamed: " + _fmt_list([f"`{c['old']}` → `{c['new']}`" for c in comps["renamed"]]) + ".")

    cc = entry["constants"]["changed"]
    if cc:
        tags.append("constants")
        bullets.append("Constants changed: " + _fmt_list([f"`{c['name']}` `{c['old'][:40]}` → `{c['new'][:40]}` ({os.path.basename(c['file'])})" for c in cc], 6) + ".")
    tw = [t for t in entry["tweaks"] if t["changes"]]
    if tw:
        tags.append("numeric tweaks")
        desc = []
        for t in tw:
            label = f"`{t['label']}`" if t["label"] else f"`{t['new_line'][:50]}`"
            desc.append(f"{label} {', '.join(f'{o}→{n}' for o, n in t['changes'][:3])} ({os.path.basename(t['file'])})")
        bullets.append("Other literal values changed in code: " + _fmt_list(desc, 5) + ".")
    for l in entry.get("lfs", []):
        gb = lambda n: f"{n / 1e9:.2f} GB" if n and n >= 1e8 else (f"{n / 1e6:.1f} MB" if n else "?")
        bullets.append(f"Git LFS artifact `{l['file']}` " + ("added" if l["status"] == "A" else "removed" if l["status"] == "D" else "replaced")
                       + (f" ({gb(l['old_size'])} → {gb(l['new_size'])})" if l["status"] not in ("A", "D") else "") + ".")
        tags.append("artifacts")
    for ls in entry.get("list_sizes", []):
        verb = "grew" if ls["new"] > ls["old"] else "shrank"
        bullets.append(f"Static list `{ls['name']}` ({os.path.basename(ls['file'])}) {verb} from {ls['old']:,} to {ls['new']:,} entries (per its in-file test assertion).")
        tags.append("lists")
    pa, pr = entry["proto"]["added"], entry["proto"]["removed"]
    if pa or pr:
        tags.append("schema")
        uniq_a = sorted({f["field"] for f in pa})
        uniq_r = sorted({f["field"] for f in pr})
        parts = []
        if uniq_a:
            parts.append(f"{_plural(len(uniq_a), 'field')} added (e.g. {_fmt_list([f'`{x}`' for x in uniq_a], 4)})")
        if uniq_r:
            parts.append(f"{_plural(len(uniq_r), 'field')} removed (e.g. {_fmt_list([f'`{x}`' for x in uniq_r], 4)})")
        bullets.append("Proto/Thrift schema: " + "; ".join(parts) + ".")

    mods = list(entry["modules"].items())
    if mods:
        top = [f"`{m if m.startswith('(') else m + '/'}` (+{v['lines_added']:,}/−{v['lines_deleted']:,})" for m, v in mods[:5]]
        bullets.append("Most activity in " + _fmt_list(top, 5) + ".")
    quiet = st["whitespace_files"] + st["comment_only_files"] + st["noise_files"]
    if quiet:
        bullets.append(f"{_plural(quiet, 'file')} changed only in formatting, comments or sync metadata.")

    # Headline: lead with the most significant fact.
    if entry["is_root"]:
        headline = f"Initial public release: {st['files']:,} files across {_plural(len(entry['modules']), 'top-level module')}."
    elif weight_changes:
        headline = "Ranking weights changed: " + _fmt_list([f"{humanize(p['name'])} {p['old']}→{p['new']}" for p in weight_changes], 3) + "."
    elif by_kind:
        prio = ["Filter", "Scorer", "Candidate source", "Selector", "Ads blender", "Candidate hydrator", "Side effect", "Visibility rule", "Botmaker rule"]
        k = sorted((hm_kind or by_kind).items(), key=lambda kv: (prio.index(kv[0][1]) if kv[0][1] in prio else 99, kv[0][0] != "added", -len(kv[1])))[0]
        headline = f"{'New' if k[0][0] == 'added' else 'Removed'} {k[0][1].lower()}{'s' if len(k[1]) > 1 else ''} {_fmt_list(k[1], 3)}" + (" and other changes." if len(bullets) > 2 else ".")
    elif flips:
        headline = "Feature switches flipped: " + _fmt_list([f"{humanize(p['name'])} {'on' if p['new'] == 'true' else 'off'}" for p in flips], 3) + "."
    elif values:
        headline = "Parameter changes: " + _fmt_list([f"{humanize(p['name'])} {p['old']}→{p['new']}" for p in values], 3) + "."
    elif params["added"]:
        headline = f"{_plural(len(params['added']), 'new config parameter')} and code changes in " + _fmt_list([f"{m}/" for m, _ in mods[:3]]) + "."
    elif entry.get("lfs") and st["files"] == len(entry["lfs"]):
        headline = "Model artifact (Git LFS) updated: " + ", ".join(os.path.basename(l["file"]) for l in entry["lfs"]) + "."
    elif entry["class"] == "docs":
        headline = "Documentation-only update."
    else:
        headline = f"Code changes in " + _fmt_list([m if m.startswith("(") else f"{m}/" for m, _ in mods[:3]]) + f" ({_plural(st['files'], 'file')})."
    headline = headline.replace("`", "")
    return {"headline": headline, "bullets": bullets, "tags": sorted(set(tags))}


# ---------------------------------------------------------------------------
# Hand-written notes and cached LLM output.
# ---------------------------------------------------------------------------

def load_note(directory: str, short: str) -> dict | None:
    path = os.path.join(directory, f"{short}.md")
    if not os.path.exists(path):
        return None
    text = open(path, encoding="utf-8").read().strip()
    title = None
    if text.startswith("# "):
        first, _, text = text.partition("\n")
        title = first[2:].strip()
    return {"title": title, "markdown": text.strip()}


LLM_PROMPT = """You write release notes for an UNOFFICIAL changelog of the open-source X (Twitter) recommendation
algorithm repository (github.com/xai-org/x-algorithm). Upstream commits all say "Open-source X Recommendation
Algorithm", so readers rely on you to explain what changed.

Rules:
- Use ONLY facts present in the structured analysis and diff excerpt below. Never speculate about motives,
  never invent features, and say "unclear from the diff" rather than guess.
- Start with a one-line title prefixed by "# " (max 90 chars).
- Then 1 short paragraph in plain English for developers, followed by 3-8 bullet points ("- ").
- Always quote parameter names and old→new values exactly. Use `backticks` for identifiers.
- No marketing tone. No emojis.

STRUCTURED ANALYSIS (JSON):
{analysis}

DIFF EXCERPT (truncated):
{diff}
"""


def llm_config() -> dict | None:
    """XAI_API_KEY (preferred) or OPENAI_API_KEY enables the optional LLM step."""
    if os.environ.get("XAI_API_KEY"):
        return {"key": os.environ["XAI_API_KEY"],
                "base": os.environ.get("CHANGELOG_LLM_BASE_URL", "https://api.x.ai/v1"),
                "model": os.environ.get("CHANGELOG_LLM_MODEL", "grok-4")}
    if os.environ.get("OPENAI_API_KEY"):
        return {"key": os.environ["OPENAI_API_KEY"],
                "base": os.environ.get("CHANGELOG_LLM_BASE_URL", "https://api.openai.com/v1"),
                "model": os.environ.get("CHANGELOG_LLM_MODEL", "gpt-4o-mini")}
    return None


def llm_summarize(cfg: dict, entry: dict, diff_excerpt: str) -> str:
    compact = {k: entry[k] for k in ("short", "date", "class", "stats", "modules", "components", "params", "constants", "tweaks")}
    compact["proto"] = {k: v[:40] for k, v in entry["proto"].items()}
    prompt = LLM_PROMPT.format(analysis=json.dumps(compact, indent=1)[:30000], diff=diff_excerpt[:40000])
    req = urllib.request.Request(
        cfg["base"].rstrip("/") + "/chat/completions",
        data=json.dumps({"model": cfg["model"], "temperature": 0,
                         "messages": [{"role": "user", "content": prompt}]}).encode(),
        headers={"Authorization": f"Bearer {cfg['key']}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        data = json.load(resp)
    return data["choices"][0]["message"]["content"].strip()
