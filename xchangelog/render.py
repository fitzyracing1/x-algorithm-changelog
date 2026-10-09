"""Static HTML + Atom rendering. No template engine, no JS framework."""
from __future__ import annotations

import html
import os
import re
from datetime import datetime, timezone
from xml.sax.saxutils import escape as xesc

DISCLAIMER = ("Unofficial, community-maintained project. Not affiliated with, endorsed by, or operated by X Corp. "
              "or xAI. Notes are generated from the public git history of "
              '<a href="{repo}">{repo_short}</a> and may contain mistakes; the upstream diff is the source of truth.')


def esc(s: str) -> str:
    return html.escape(s, quote=True)


def inline_md(s: str) -> str:
    """Escape then apply `code`, **bold**, [text](url). Code spans are protected first."""
    codes: list[str] = []

    def stash(m: re.Match) -> str:
        codes.append(f"<code>{esc(m.group(1))}</code>")
        return f"\x00{len(codes) - 1}\x00"

    t = esc(re.sub(r"`([^`]+)`", stash, s))
    t = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)
    t = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r'<a href="\2">\1</a>', t)
    return re.sub(r"\x00(\d+)\x00", lambda m: codes[int(m.group(1))], t)


def markdown(md: str) -> str:
    html_out, para, in_list = [], [], False

    def flush_para():
        nonlocal para
        if para:
            html_out.append("<p>" + inline_md(" ".join(para)) + "</p>")
            para = []

    for line in md.splitlines():
        s = line.rstrip()
        if not s.strip():
            flush_para()
            if in_list:
                html_out.append("</ul>"); in_list = False
            continue
        m = re.match(r"^(#{2,4})\s+(.*)", s)
        if m:
            flush_para()
            if in_list:
                html_out.append("</ul>"); in_list = False
            lvl = min(len(m.group(1)) + 1, 5)
            html_out.append(f"<h{lvl}>{inline_md(m.group(2))}</h{lvl}>")
            continue
        m = re.match(r"^\s*[-*]\s+(.*)", s)
        if m:
            flush_para()
            if not in_list:
                html_out.append("<ul>"); in_list = True
            html_out.append(f"<li>{inline_md(m.group(1))}</li>")
            continue
        if in_list and line.startswith("  "):
            html_out[-1] = html_out[-1][:-5] + " " + inline_md(s.strip()) + "</li>"
            continue
        para.append(s.strip())
    flush_para()
    if in_list:
        html_out.append("</ul>")
    return "\n".join(html_out)


def fmt_date(iso: str) -> str:
    return datetime.fromisoformat(iso).astimezone(timezone.utc).strftime("%b %-d, %Y")


def iso_utc(iso: str) -> str:
    return datetime.fromisoformat(iso).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def page(title: str, body: str, cfg: dict, depth: int = 0, description: str = "") -> str:
    root = "../" * depth
    disclaimer = DISCLAIMER.format(repo=esc(cfg["repo_url"]), repo_short=esc(cfg["repo_url"].split("github.com/")[-1]))
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<meta name="description" content="{esc(description or cfg['tagline'])}">
<link rel="stylesheet" href="{root}style.css">
<link rel="alternate" type="application/atom+xml" title="{esc(cfg['site_title'])}" href="{esc(cfg['site_url'])}feed.xml">
</head>
<body>
<header class="top">
  <a class="brand" href="{root}index.html">{esc(cfg['site_title'])}</a>
  <nav><a href="{root}index.html">Changelog</a> <a href="{root}params.html">Params</a> <a href="{root}feed.xml">Atom feed</a> <a href="{esc(cfg['repo_url'])}">Upstream</a></nav>
</header>
<p class="disclaimer">{disclaimer}</p>
<main>
{body}
</main>
<footer>
  <p>{disclaimer}</p>
  <p>Generated {esc(cfg['generated_at'])} from upstream <a href="{esc(cfg['repo_url'])}/commit/{esc(cfg['head_sha'])}"><code>{esc(cfg['head_sha'][:7])}</code></a>.
  Source for this site: <a href="{esc(cfg['project_url'])}">{esc(cfg['project_url'].split('github.com/')[-1])}</a>.</p>
</footer>
</body>
</html>
"""


def summary_html(entry: dict) -> str:
    s = entry["summary"]
    if s["source"] in ("note", "llm"):
        return markdown(s["markdown"])
    items = "".join(f"<li>{inline_md(b)}</li>" for b in s["bullets"])
    return f"<ul>{items}</ul>" if items else ""


def entry_card(entry: dict, link_prefix: str = "entries/") -> str:
    s = entry["summary"]
    tags = "".join(f'<span class="tag">{esc(t)}</span>' for t in s["tags"])
    src = {"note": "hand-written notes, checked against the diff",
           "llm": "LLM-written from the analysis below",
           "auto": "automatic analysis"}[s["source"]]
    st = entry["stats"]
    body = summary_html(entry)
    if s["source"] == "note" and entry["auto"]["bullets"]:
        body += ""  # full auto analysis lives on the entry page
    return f"""<article class="entry" id="{esc(entry['short'])}">
  <div class="meta"><time datetime="{esc(entry['date'])}">{fmt_date(entry['date'])}</time>
    · <a href="{esc(entry['url'])}"><code>{esc(entry['short'])}</code></a>
    · {st['files']:,} files, +{st['lines_added']:,}/−{st['lines_deleted']:,}{' · merge' if entry['is_merge'] else ''}</div>
  <h2><a href="{link_prefix}{esc(entry['short'])}.html">{inline_md(s['title'])}</a></h2>
  <div class="tags">{tags}</div>
  <div class="summary">{body}</div>
  <p class="more"><a href="{link_prefix}{esc(entry['short'])}.html">Full analysis →</a> <span class="src">Summary: {src}</span></p>
</article>"""


def _table(headers: list[str], rows: list[list[str]], cls: str = "") -> str:
    if not rows:
        return ""
    th = "".join(f"<th>{h}</th>" for h in headers)
    trs = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f'<div class="tw"><table class="{cls}"><thead><tr>{th}</tr></thead><tbody>{trs}</tbody></table></div>'


def entry_detail(entry: dict, cfg: dict, prev: dict | None, nxt: dict | None) -> str:
    c = lambda s: f"<code>{esc(str(s))}</code>"
    blob = lambda path: f'<a href="{esc(cfg["repo_url"])}/blob/{esc(entry["sha"])}/{esc(path)}">{esc(path)}</a>'
    parts = [entry_card(entry, link_prefix="")]
    if entry["summary"]["source"] != "auto":
        parts.append("<h3>Automatic analysis</h3><ul>" + "".join(f"<li>{inline_md(b)}</li>" for b in entry["auto"]["bullets"]) + "</ul>")
    p = entry["params"]
    parts.append("<h3>Config parameters (<code>param!</code> defaults)</h3>" + (
        _table(["Parameter", "Feature-switch key", "Old", "New"],
               [[c(x["name"]) + f"<br><span class='muted'>{esc(x['file'])}</span>", c(x["key"]), c(x["old"]), c(x["new"])] for x in p["changed"]]) +
        _table(["Added parameter", "Type", "Default", "File"], [[c(x["name"]), c(x["type"]), c(x["default"][:80]), esc(x["file"])] for x in p["added"][:300]]) +
        (f"<p class='muted'>…and {len(p['added']) - 300} more added.</p>" if len(p["added"]) > 300 else "") +
        _table(["Removed parameter", "Type", "Last default", "Note"], [[c(x["name"]), c(x["type"]), c(x["default"][:80]),
               esc(f"still defined in {x['still_defined_in']}" if x.get("still_defined_in") else f"removed from {x['file']}")] for x in p["removed"]]) +
        _table(["Moved parameter", "From", "To"], [[c(x["name"]), esc(x["from"]), esc(x["file"])] for x in p.get("moved", [])])
        or "<p class='muted'>None.</p>"))
    comps = entry["components"]
    rows = [[esc(k), "added", blob(x["path"])] for k_, lst in (("added", comps["added"]),) for x in lst for k in [x["kind"]]]
    rows += [[esc(x["kind"]), "removed", esc(x["path"])] for x in comps["removed"]]
    rows += [[esc(x["kind"]), f"renamed from {c(x['old'])}", blob(x["path"])] for x in comps["renamed"]]
    rows += [[esc(x["kind"]), "modified", blob(x["path"])] for x in comps["changed"]]
    parts.append("<h3>Pipeline components</h3>" + (_table(["Kind", "Change", "File"], rows[:400]) or "<p class='muted'>None detected.</p>"))
    k = entry["constants"]
    tw = [t for t in entry["tweaks"] if t["changes"]]
    if k["changed"] or tw:
        parts.append("<h3>Changed constants &amp; literal values</h3>" +
                     _table(["Name", "Old", "New", "File"], [[c(x["name"]), c(x["old"]), c(x["new"]), blob(x["file"])] for x in k["changed"]]) +
                     _table(["Old line", "New line", "File"], [[c(t["old_line"]), c(t["new_line"]), blob(t["file"])] for t in tw]))
    pr = entry["proto"]
    if pr["added"] or pr["removed"]:
        fields = sorted({("added", f["field"]) for f in pr["added"]} | {("removed", f["field"]) for f in pr["removed"]})
        parts.append(f"<h3>Schema fields ({len(fields)})</h3><details><summary>Show proto/thrift field changes</summary>" +
                     _table(["Change", "Field"], [[a, c(b)] for a, b in fields[:500]]) + "</details>")
    mods = [[f"<code>{esc(m)}/</code>", str(v["added"]), str(v["modified"]), str(v["removed"]), f"+{v['lines_added']:,}", f"−{v['lines_deleted']:,}"]
            for m, v in entry["modules"].items()]
    parts.append("<h3>Modules touched</h3>" + _table(["Module", "Files added", "Modified", "Removed", "Lines +", "Lines −"], mods))
    files = entry["files"]
    frows = [[esc(f["status"]), blob(f["path"]) if f["status"] != "D" else esc(f["path"]), f"+{f['added']}/−{f['deleted']}",
              esc({"code": "", "noise": "sync metadata", "whitespace": "formatting only", "comments": "comments only"}[f["kind"]])]
             for f in files[:1500]]
    parts.append(f"<h3>Files ({len(files):,})</h3><details><summary>Show file list</summary>" + _table(["", "Path", "Lines", "Note"], frows) +
                 (f"<p class='muted'>…and {len(files) - 1500:,} more.</p>" if len(files) > 1500 else "") + "</details>")
    if entry["body"]:
        parts.append(f"<h3>Upstream commit message</h3><pre>{esc(entry['subject'])}\n\n{esc(entry['body'])}</pre>")
    nav = '<nav class="pn">'
    if prev:
        nav += f'<a href="{esc(prev["short"])}.html">← older ({fmt_date(prev["date"])})</a>'
    if nxt:
        nav += f'<a href="{esc(nxt["short"])}.html">newer ({fmt_date(nxt["date"])}) →</a>'
    parts.append(nav + "</nav>")
    return "\n".join(parts)


def index_body(entries: list[dict], noops: list[dict], weights: list[dict], cfg: dict) -> str:
    intro = f"""<section class="intro">
<h1>{esc(cfg['site_title'])}</h1>
<p>Plain-English developer notes for every sync of <a href="{esc(cfg['repo_url'])}">xai-org/x-algorithm</a>, the open-source
X “For You” recommendation algorithm. Upstream commits are published by a CI agent and all carry the same message
(<em>“Open-source X Recommendation Algorithm”</em>), so this site reads each diff and writes down what actually changed:
new or removed filters, scorers and candidate sources, ranking-weight and parameter changes with old → new values, and new feature flags.</p>
<p class="muted">{len(entries)} entries · newest first · <a href="feed.xml">subscribe via Atom</a>{f' · {len(noops)} no-op syncs collapsed' if noops else ''}</p>
</section>"""
    by_name: dict[str, dict] = {}
    for w in weights:
        r = by_name.setdefault(w["name"], {"vals": {}, "last": None})
        r["vals"][w["file"]] = w["value"]
        if w["prev"] is not None and (r["last"] is None or w["last_date"] > r["last"]["last_date"]):
            r["last"] = w
    files = sorted({w["file"] for w in weights})
    cell = lambda v: f"<code>{esc(v)}</code>" if v is not None else "—"
    wrows = [[f"<code>{esc(n)}</code>"] + [cell(r["vals"].get(f)) for f in files] +
             [(f"<a href='entries/{esc(r['last']['last_short'])}.html'>{fmt_date(r['last']['last_date'])}</a> (was <code>{esc(r['last']['prev'])}</code>)"
               if r["last"] else "—")]
             for n, r in sorted(by_name.items())]
    wheaders = ["Weight"] + [esc(f.split("/")[0]) for f in files] + ["Last changed"]
    wsec = f"""<section class="weights"><details><summary>Current ranking-weight defaults ({len(by_name)})</summary>
<p class="muted">Defaults of <code>*Weight</code> <code>param!</code>s at upstream HEAD (<code>home-mixer/params/param.rs</code> and <code>vm-ranker/params.rs</code>). These multiply the model's
<em>predicted probabilities</em> of each action for a given viewer, not raw engagement counts (see the upstream README). Production values can be overridden by feature switches.</p>
{_table(wheaders, wrows)}
<p><a href="params.html">Full parameter history →</a></p></details></section>"""
    cards = "\n".join(entry_card(e) for e in entries)
    nsec = ""
    if noops:
        nsec = "<section><h2>No-op syncs</h2><ul>" + "".join(
            f"<li>{fmt_date(e['date'])} · <a href='{esc(e['url'])}'><code>{esc(e['short'])}</code></a> — only sync metadata/formatting changed</li>" for e in noops) + "</ul></section>"
    return intro + wsec + cards + nsec


def params_body(history: list[dict]) -> str:
    rows = [[f"<a href='entries/{esc(h['short'])}.html'>{fmt_date(h['date'])}</a>", esc(h["change"]), f"<code>{esc(h['name'])}</code><br><span class='muted'>{esc(h['file'])}</span>",
             f"<code>{esc(h['old'])}</code>" if h["old"] is not None else "—",
             f"<code>{esc(h['new'])}</code>" if h["new"] is not None else "—"] for h in reversed(history)]
    return ("<h1>Parameter history</h1><p class='muted'>Every change to a <code>param!</code> default in upstream history, newest first "
            "(the initial bulk addition of parameters on Aug 13, 2026 is listed on its entry page instead).</p>" +
            _table(["Date", "Change", "Parameter", "Old", "New"], rows, "params"))


def atom_feed(entries: list[dict], cfg: dict, limit: int = 50) -> str:
    updated = iso_utc(entries[0]["date"]) if entries else cfg["generated_iso"]
    items = []
    for e in entries[:limit]:
        link = f"{cfg['site_url']}entries/{e['short']}.html"
        content = summary_html(e) + f'<p><a href="{e["url"]}">Upstream commit {e["short"]}</a> · <a href="{link}">Full analysis</a></p>'
        items.append(f"""  <entry>
    <title>{xesc(fmt_date(e['date']) + ' — ' + re.sub(r'`', '', e['summary']['title']))}</title>
    <link rel="alternate" type="text/html" href="{xesc(link)}"/>
    <link rel="related" href="{xesc(e['url'])}"/>
    <id>tag:{xesc(cfg['tag_authority'])},2026:{e['sha']}</id>
    <published>{iso_utc(e['date'])}</published>
    <updated>{iso_utc(e['date'])}</updated>
    <summary>{xesc(re.sub(r'`', '', e['summary']['title']))}</summary>
    <content type="html">{xesc(content)}</content>
  </entry>""")
    return f"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>{xesc(cfg['site_title'])}</title>
  <subtitle>{xesc(cfg['tagline'])} Unofficial; not affiliated with X Corp. or xAI.</subtitle>
  <link rel="self" type="application/atom+xml" href="{xesc(cfg['site_url'])}feed.xml"/>
  <link rel="alternate" type="text/html" href="{xesc(cfg['site_url'])}"/>
  <link rel="related" href="{xesc(cfg['repo_url'])}"/>
  <id>{xesc(cfg['site_url'])}</id>
  <updated>{updated}</updated>
  <author><name>{xesc(cfg['author'])}</name></author>
  <generator>x-algorithm-changelog</generator>
{chr(10).join(items)}
</feed>
"""


def write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
