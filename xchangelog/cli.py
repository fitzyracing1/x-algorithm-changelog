"""Command-line entry point: python -m xchangelog [options]"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime, timezone

from . import render
from .analyze import analyze_commit
from .gitio import EMPTY_TREE, ensure_clone, list_commits, run, unified_diff
from .summarize import deterministic, is_weight, llm_config, llm_summarize, load_note

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Generate the unofficial x-algorithm changelog site + Atom feed.")
    p.add_argument("--repo-url", default=os.environ.get("UPSTREAM_REPO", "https://github.com/xai-org/x-algorithm"))
    p.add_argument("--upstream-dir", default=os.path.join(ROOT, ".cache", "x-algorithm"),
                   help="Where to clone/fetch the upstream repo (default: .cache/x-algorithm)")
    p.add_argument("--no-fetch", action="store_true", help="Use the existing clone as-is")
    p.add_argument("--out", default=os.path.join(ROOT, "site"))
    p.add_argument("--notes-dir", default=os.path.join(ROOT, "notes"))
    p.add_argument("--llm-cache-dir", default=os.path.join(ROOT, "data", "llm"))
    p.add_argument("--no-llm", action="store_true", help="Never call an LLM even if an API key is set")
    p.add_argument("--llm-limit", type=int, default=int(os.environ.get("CHANGELOG_LLM_LIMIT", "10")),
                   help="Max new LLM summaries per run (cost guard; cached ones are reused for free)")
    p.add_argument("--site-url", default=os.environ.get("SITE_URL", "https://fitzyracing1.github.io/x-algorithm-changelog/"))
    p.add_argument("--project-url", default=os.environ.get("PROJECT_URL", "https://github.com/fitzyracing1/x-algorithm-changelog"))
    p.add_argument("--since", default=None, help="Only include commits on/after this date (YYYY-MM-DD)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    site_url = args.site_url if args.site_url.endswith("/") else args.site_url + "/"
    up = args.upstream_dir
    if not args.no_fetch:
        print(f"[fetch] {args.repo_url} -> {up}", file=sys.stderr)
        ensure_clone(args.repo_url, up)
    commits = list_commits(up)
    if args.since:
        commits = [c for c in commits if c.date[:10] >= args.since]
    print(f"[walk] {len(commits)} first-parent commits", file=sys.stderr)

    llm = None if args.no_llm else llm_config()
    if llm:
        print(f"[llm] enabled via {'XAI_API_KEY' if os.environ.get('XAI_API_KEY') else 'OPENAI_API_KEY'} model={llm['model']}", file=sys.stderr)

    entries = []
    llm_calls = 0
    for meta in commits:
        e = analyze_commit(up, meta, args.repo_url)
        auto = deterministic(e)
        e["auto"] = auto
        note = load_note(args.notes_dir, e["short"])
        cached = load_note(args.llm_cache_dir, e["short"])
        if note:
            e["summary"] = {"source": "note", "title": note["title"] or auto["headline"], "markdown": note["markdown"],
                            "bullets": auto["bullets"], "tags": auto["tags"]}
        elif cached:
            e["summary"] = {"source": "llm", "title": cached["title"] or auto["headline"], "markdown": cached["markdown"],
                            "bullets": auto["bullets"], "tags": auto["tags"]}
        elif llm and e["class"] != "noop" and llm_calls < args.llm_limit:
            llm_calls += 1
            try:
                parent = meta.parents[0] if meta.parents else EMPTY_TREE
                text = llm_summarize(llm, e, unified_diff(up, parent, meta.sha, context=2))
                os.makedirs(args.llm_cache_dir, exist_ok=True)
                with open(os.path.join(args.llm_cache_dir, f"{e['short']}.md"), "w", encoding="utf-8") as f:
                    f.write(text + "\n")
                n = load_note(args.llm_cache_dir, e["short"])
                e["summary"] = {"source": "llm", "title": n["title"] or auto["headline"], "markdown": n["markdown"],
                                "bullets": auto["bullets"], "tags": auto["tags"]}
                print(f"[llm] summarised {e['short']}", file=sys.stderr)
            except Exception as exc:  # fall back to deterministic output
                print(f"[llm] {e['short']} failed ({exc}); using deterministic summary", file=sys.stderr)
        if "summary" not in e:
            e["summary"] = {"source": "auto", "title": auto["headline"], "markdown": "", "bullets": auto["bullets"], "tags": auto["tags"]}
        entries.append(e)
        print(f"[entry] {e['date'][:10]} {e['short']} {e['class']:5} {e['summary']['source']:4} {e['summary']['title'][:80]}", file=sys.stderr)

    # Parameter history + current weights (chronological walk).
    state: dict[str, dict] = {}
    history = []
    for e in entries:
        bulk = len(e["params"]["added"]) > 50
        rec = lambda change, p, old, new: history.append({"short": e["short"], "date": e["date"], "change": change, "name": p["name"],
                                                          "file": p["file"], "old": old, "new": new})
        for p in e["params"].get("moved", []):
            prev = state.pop((p["from"], p["name"]), None)
            state[(p["file"], p["name"])] = prev or {"value": p["default"], "prev": None, "last_short": e["short"], "last_date": e["date"]}
            rec(f"moved from {p['from']}", p, None, p["default"])
        for p in e["params"]["added"]:
            state[(p["file"], p["name"])] = {"value": p["default"], "prev": None, "last_short": e["short"], "last_date": e["date"]}
            if not bulk:
                rec("added", p, None, p["default"])
        for p in e["params"]["changed"]:
            state[(p["file"], p["name"])] = {"value": p["new"], "prev": p["old"], "last_short": e["short"], "last_date": e["date"]}
            rec("changed", p, p["old"], p["new"])
        for p in e["params"]["removed"]:
            state.pop((p["file"], p["name"]), None)
            rec("removed" + (f" (still defined in {p['still_defined_in']})" if p.get("still_defined_in") else ""), p, p["default"], None)
    weights = sorted(({"name": k[1], "file": k[0], **v} for k, v in state.items() if is_weight(k[1])), key=lambda w: (w["file"], w["name"]))

    head = run(["rev-parse", "HEAD"], cwd=up).strip()
    now = datetime.now(timezone.utc)
    cfg = {
        "repo_url": args.repo_url.rstrip("/"),
        "site_url": site_url,
        "project_url": args.project_url,
        "site_title": "x-algorithm changelog (unofficial)",
        "tagline": "Plain-English developer notes for every update to X's open-source recommendation algorithm.",
        "author": "x-algorithm-changelog contributors",
        "tag_authority": site_url.split("//", 1)[-1].split("/", 1)[0],
        "generated_at": now.strftime("%Y-%m-%d %H:%M UTC"),
        "generated_iso": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "head_sha": head,
    }

    out = args.out
    if os.path.isdir(out):
        shutil.rmtree(out)
    os.makedirs(os.path.join(out, "entries"))
    shutil.copy(os.path.join(ROOT, "static", "style.css"), os.path.join(out, "style.css"))
    real = [e for e in entries if e["class"] != "noop"]
    noops = [e for e in entries if e["class"] == "noop"]
    newest_first = list(reversed(real))
    render.write(os.path.join(out, "index.html"), render.page(cfg["site_title"], render.index_body(newest_first, list(reversed(noops)), weights, cfg), cfg))
    render.write(os.path.join(out, "params.html"), render.page("Parameter history · " + cfg["site_title"], render.params_body(history), cfg))
    for i, e in enumerate(real):
        prev = real[i - 1] if i > 0 else None
        nxt = real[i + 1] if i + 1 < len(real) else None
        title = f"{render.fmt_date(e['date'])}: {e['summary']['title'].replace('`', '')}"
        render.write(os.path.join(out, "entries", f"{e['short']}.html"),
                     render.page(title + " · x-algorithm changelog", render.entry_detail(e, cfg, prev, nxt), cfg, depth=1, description=title))
    render.write(os.path.join(out, "feed.xml"), render.atom_feed(newest_first, cfg))
    slim = [{k: e[k] for k in ("sha", "short", "url", "date", "class", "stats", "summary", "params", "components", "constants")} for e in newest_first]
    render.write(os.path.join(out, "entries.json"), json.dumps({"generated": cfg["generated_iso"], "upstream_head": head, "entries": slim}, indent=1))
    render.write(os.path.join(out, ".nojekyll"), "")
    print(f"[done] {len(real)} entries ({len(noops)} no-op collapsed), {len(history)} param changes -> {out}", file=sys.stderr)
    return 0
