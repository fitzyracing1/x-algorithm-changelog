"""Deterministic analysis of one upstream commit.

Everything here is derived mechanically from the git diff -- no guessing.
The output is a plain dict (JSON-serialisable) consumed by the summariser and renderer.
"""
from __future__ import annotations

import os
import re
from collections import Counter, defaultdict

from .gitio import EMPTY_TREE, BlobReader, CommitMeta, changed_files, run, unified_diff

# ---------------------------------------------------------------------------
# Noise: lines that change on every sync and carry no algorithmic meaning.
# ---------------------------------------------------------------------------
NOISE_LINE_RES = [
    re.compile(r"last sync \d{4}-\d{2}-\d{2}T[\d:]+Z"),
]

CODE_EXTS = {".rs", ".py", ".scala", ".java", ".cc", ".hpp", ".h", ".cpp", ".proto",
             ".thrift", ".go", ".ts", ".js", ".strato", ".bot", ".df", ".toml", ".yml",
             ".yaml", ".json", ".bazel", ".typed"}
DOC_EXTS = {".md", ".txt", ".rst"}

# ---------------------------------------------------------------------------
# Component classification by path.
# ---------------------------------------------------------------------------
COMPONENT_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("Filter", re.compile(r"(^|/)filters?/(?!mod\.rs$|__init__\.py$)[^/]+$|[A-Za-z0-9]*_filter\.(rs|py)$|[A-Za-z0-9]*Filter\.(scala|java)$")),
    ("Scorer", re.compile(r"(^|/)scorers?/(?!mod\.rs$|__init__\.py$)[^/]+$|_scorer\.(rs|py)$|Scorer\.(scala|java)$")),
    ("Candidate source", re.compile(r"(^|/)sources/(?!mod\.rs$|__init__\.py$)[^/]+\.(rs|py|scala|java)$|CandidateSource\.(scala|java)$")),
    ("Candidate hydrator", re.compile(r"(^|/)(candidate|query)_hydrators/(?!mod\.rs$)[^/]+$|_hydrator\.rs$")),
    ("Selector", re.compile(r"(^|/)selectors/(?!mod\.rs$)[^/]+$")),
    ("Side effect", re.compile(r"(^|/)side_effects/(?!mod\.rs$)[^/]+$")),
    ("Ads blender", re.compile(r"home-mixer/ads/[^/]*blender[^/]*\.rs$")),
    ("Botmaker rule", re.compile(r"botmaker-rules/.*\.bot$")),
    ("Visibility rule", re.compile(r"visibility-filtering/.*/rules?/[^/]+\.(rs|scala|java|py)$")),
]
EXCLUDE_COMPONENT = re.compile(r"(^|/)(tests?|test_[^/]*|[^/]*_test\.\w+|[^/]*Test\.\w+|mod\.rs|__init__\.py)$|/tests?/")


def classify_component(path: str) -> str | None:
    if EXCLUDE_COMPONENT.search(path):
        return None
    for kind, rx in COMPONENT_RULES:
        if rx.search(path):
            return kind
    return None


GENERIC_NAMES = {"filter", "task_filter", "filters", "scorer", "scorers", "source", "sources", "rules", "rule"}


def component_name(path: str) -> str:
    base = os.path.splitext(os.path.basename(path))[0]
    if base.lower() in GENERIC_NAMES:
        parent = os.path.basename(os.path.dirname(path))
        return f"{parent}/{base}"
    return base


# ---------------------------------------------------------------------------
# param!(Name, Type, "key", default) macros (home-mixer feature-switch params).
# ---------------------------------------------------------------------------

def _split_top_level(s: str) -> list[str]:
    parts, depth, cur, in_str = [], 0, [], False
    i = 0
    while i < len(s):
        ch = s[i]
        if in_str:
            cur.append(ch)
            if ch == "\\" and i + 1 < len(s):
                cur.append(s[i + 1]); i += 2; continue
            if ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True; cur.append(ch)
        elif ch in "([{<":
            depth += 1; cur.append(ch)
        elif ch in ")]}>":
            depth -= 1; cur.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(cur).strip()); cur = []
        else:
            cur.append(ch)
        i += 1
    if "".join(cur).strip():
        parts.append("".join(cur).strip())
    return parts


def parse_params(text: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for m in re.finditer(r"\bparam!\s*\(", text):
        start = m.end()
        depth, i, in_str = 1, start, False
        while i < len(text) and depth:
            ch = text[i]
            if in_str:
                if ch == "\\":
                    i += 1
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            i += 1
        body = text[start:i - 1]
        body = re.sub(r"//[^\n]*", "", body)
        parts = _split_top_level(body)
        if len(parts) >= 4:
            name, typ, key = parts[0], parts[1], parts[2].strip('"')
            default = re.sub(r"\s+", " ", ",".join(parts[3:])).strip()
            out[name] = {"type": typ, "key": key, "default": default}
    return out


# ---------------------------------------------------------------------------
# Named constants (Rust const/static, Python UPPER_CASE / typed fields, Scala val, Java static final).
# ---------------------------------------------------------------------------
NUM = r"[-+]?(?:\d[\d_]*\.?\d*(?:[eE][-+]?\d+)?|\.\d+)"
LIT = rf"(?:{NUM}(?:\s*[-+*/]\s*{NUM})*|true|false|True|False|\"[^\"]*\"|'[^']*')"
CONST_RES = {
    ".rs": re.compile(rf"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:const|static)\s+([A-Z][A-Z0-9_]*)\s*:\s*[^=]+=\s*([^;]+);"),
    ".py": re.compile(rf"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*(?::\s*[A-Za-z_][\w\[\], .|]*)?\s*=\s*({LIT})\s*(?:#.*)?$"),
    ".scala": re.compile(rf"^\s*(?:(?:private|protected|override|final|lazy)\s+)*(?:val|var)\s+([A-Za-z_]\w*)\s*(?::\s*[\w\[\]]+)?\s*=\s*({LIT})\s*$"),
    ".java": re.compile(rf"^\s*(?:(?:public|private|protected|static|final)\s+)+[\w<>\[\]]+\s+([A-Z][A-Z0-9_]*)\s*=\s*({LIT})\s*;"),
}


LITERALISH = re.compile(r"""[-+*/()\s\d_.eE]+|true|false|True|False|None|"[^"]*"|'[^']*'|vec!\[.*\]|\[.*\]|[\d_.]+\s*as\s*\w+|Duration::\w+\([\d_.]+\)""")


def is_literalish(v: str) -> bool:
    return bool(LITERALISH.fullmatch(v.strip()))


def parse_constants(path: str, text: str) -> dict[str, str]:
    ext = os.path.splitext(path)[1]
    rx = CONST_RES.get(ext)
    if not rx:
        return {}
    seen: Counter[str] = Counter()
    out: dict[str, str] = {}
    for line in text.splitlines():
        m = rx.match(line)
        if not m:
            continue
        name, val = m.group(1), re.sub(r"\s+", " ", m.group(2).strip())
        if ext == ".py" and (line.startswith((" ", "\t")) and not re.search(r":\s*\w", line.split("=")[0])) and not name.isupper():
            # skip local variable assignments inside functions (keep typed fields + UPPER constants)
            continue
        seen[name] += 1
        key = name if seen[name] == 1 else f"{name}#{seen[name]}"
        out[key] = val
    return out


# ---------------------------------------------------------------------------
# Generic numeric / boolean tweaks found by pairing -/+ lines in the same hunk.
# ---------------------------------------------------------------------------
NUM_TOKEN = re.compile(rf"(?<![\w.]){NUM}(?![\w])")
BOOL_TOKEN = re.compile(r"\b(true|false|True|False)\b")
LOCK_FILE = re.compile(r"(^|/)(Cargo\.lock|package-lock\.json|yarn\.lock|poetry\.lock|uv\.lock|[^/]*\.lock)$")
TEST_PATH = re.compile(r"(^|/)(tests?|testing|testdata)/|_test\.\w+$|(^|/)test_[^/]+$|Test\.\w+$")
TEST_LINE = re.compile(r"\bassert|\bexpect\(|\.apply\(&|should|#\[test\]")
COMMENT_LINE = re.compile(r"^\s*(//|#|\*|/\*|--)")


def _skeleton(line: str) -> str:
    s = NUM_TOKEN.sub("#", line)
    s = BOOL_TOKEN.sub("?", s)
    return re.sub(r"\s+", "", s)


def _label_for(line: str) -> str:
    m = re.match(r"\s*(?:pub\s+)?(?:const\s+|static\s+|val\s+|var\s+|let\s+)?([A-Za-z_][\w.]*)\s*(?::[^=]*)?=", line)
    if m:
        return m.group(1)
    m = re.match(r"\s*([A-Za-z_][\w.]*)\s*:", line)
    if m:
        return m.group(1)
    m = re.match(r"\s*\"([^\"]+)\"\s*[:=]", line)
    if m:
        return m.group(1)
    return ""


def numeric_tweaks(diff_text: str) -> list[dict]:
    tweaks = []
    cur_file = None
    minus: list[str] = []
    plus: list[str] = []

    def flush():
        if not cur_file or not minus or not plus:
            return
        used = set()
        if TEST_PATH.search(cur_file) or os.path.splitext(cur_file)[1] in DOC_EXTS or LOCK_FILE.search(cur_file):
            return
        for m in minus:
            if TEST_LINE.search(m):
                continue
            if COMMENT_LINE.match(m) or any(rx.search(m) for rx in NOISE_LINE_RES):
                continue
            sk = _skeleton(m)
            if len(re.sub(r"[^A-Za-z]", "", sk)) < 2:
                continue  # bare literal lines (e.g. macro defaults) are handled elsewhere
            for idx, p in enumerate(plus):
                if idx in used or _skeleton(p) != sk or p.strip() == m.strip():
                    continue
                old_vals = NUM_TOKEN.findall(m) + BOOL_TOKEN.findall(m)
                new_vals = NUM_TOKEN.findall(p) + BOOL_TOKEN.findall(p)
                used.add(idx)
                tweaks.append({
                    "file": cur_file,
                    "label": _label_for(p) or _label_for(m),
                    "old_line": m.strip()[:200],
                    "new_line": p.strip()[:200],
                    "changes": [[o, n] for o, n in zip(old_vals, new_vals) if o != n] if len(old_vals) == len(new_vals) else [],
                })
                break

    for line in diff_text.splitlines():
        if line.startswith("diff --git"):
            flush(); minus, plus = [], []
            cur_file = None
        elif line.startswith("+++ "):
            cur_file = line[6:] if line.startswith("+++ b/") else None
        elif line.startswith("--- "):
            continue
        elif line.startswith("@@"):
            flush(); minus, plus = [], []
        elif line.startswith("-"):
            minus.append(line[1:])
        elif line.startswith("+"):
            plus.append(line[1:])
    flush()
    return tweaks


LIST_SIZE = re.compile(r"assert_eq!\(\s*([A-Z][A-Z0-9_]+)\.len\(\)\s*,\s*(\d+)\s*\)")


def list_size_changes(diff_text: str) -> list[dict]:
    """Static lists whose size is pinned by an in-file test, e.g. assert_eq!(IDS.len(), 2795)."""
    cur, old, new, out = None, {}, {}, []
    def flush():
        for k in old:
            if k in new and old[k] != new[k]:
                out.append({"file": cur, "name": k, "old": int(old[k]), "new": int(new[k])})
    for line in diff_text.splitlines():
        if line.startswith("diff --git"):
            flush(); old, new = {}, {}
            cur = line.split(" b/", 1)[-1]
        elif line.startswith(("-", "+")) and not line.startswith(("---", "+++")):
            m = LIST_SIZE.search(line)
            if m:
                (old if line[0] == "-" else new)[m.group(1)] = m.group(2)
    flush()
    return out


# ---------------------------------------------------------------------------
# Proto / thrift field additions & removals.
# ---------------------------------------------------------------------------
PROTO_FIELD = re.compile(r"^\s*(?:optional|repeated|required)?\s*(?:\d+:\s*)?(?:optional|required)?\s*[\w.<>, ]+\s+(\w+)\s*=\s*(\d+)\s*[;\[]")
THRIFT_FIELD = re.compile(r"^\s*(\d+)\s*:\s*(?:optional|required)?\s*[\w.<>, ]+\s+(\w+)")


def proto_fields(path: str, text: str) -> set[str]:
    """Fields of messages/structs, keyed by their dotted message path (enum values skipped)."""
    out = set()
    stack: list[str] = []
    is_proto = path.endswith(".proto")
    for raw in text.splitlines():
        line = re.sub(r"//.*$", "", raw)
        m = re.match(r"\s*(message|struct|union|enum|service|exception|oneof)\s+(\w+)", line)
        opened = line.count("{")
        closed = line.count("}")
        if m and opened:
            stack.append(("~" if m.group(1) in ("enum", "service") else
                          ("" if m.group(1) == "oneof" else m.group(2))))
            opened -= 1
        elif not (stack and stack[-1] == "~"):
            f = PROTO_FIELD.match(line) if is_proto else THRIFT_FIELD.match(line)
            if f and not line.strip().startswith(("option", "reserved")):
                name = f.group(1) if is_proto else f.group(2)
                scope = ".".join(x for x in stack if x and x != "~")
                out.add(f"{scope}.{name}" if scope else name)
        for _ in range(opened):
            stack.append("")
        for _ in range(closed):
            if stack:
                stack.pop()
    return out


# ---------------------------------------------------------------------------
# Whitespace / comment normalisation for no-op detection.
# ---------------------------------------------------------------------------

def _strip_noise(text: str) -> str:
    lines = [l for l in text.splitlines() if not any(rx.search(l) for rx in NOISE_LINE_RES)]
    return "\n".join(lines)


def _no_ws(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _strip_comments(path: str, text: str) -> str:
    ext = os.path.splitext(path)[1]
    if ext in {".py", ".toml", ".yml", ".yaml", ".bazel", ".bzl"} or path.endswith("BUILD"):
        text = re.sub(r"(?m)^\s*#.*$", "", text)
        text = re.sub(r"(?m)\s+#[^\n'\"]*$", "", text)
        if ext == ".py":
            text = re.sub(r'(?s)"""(.*?)"""', '""""""', text)
    else:
        text = re.sub(r"(?s)/\*.*?\*/", "", text)
        text = re.sub(r"(?m)(^|\s)//.*$", r"\1", text)
    return text


def classify_file_change(path: str, old: str | None, new: str | None) -> str:
    """Return 'noise', 'whitespace', 'comments', or 'code'."""
    if old is None or new is None:
        return "code"
    o, n = _strip_noise(old), _strip_noise(new)
    if o == n:
        return "noise"
    if _no_ws(o) == _no_ws(n):
        return "whitespace"
    if _no_ws(_strip_comments(path, o)) == _no_ws(_strip_comments(path, n)):
        return "comments"
    return "code"


def top_module(path: str) -> str:
    return path.split("/", 1)[0] if "/" in path else "(repo root)"


# ---------------------------------------------------------------------------
# Main entry point.
# ---------------------------------------------------------------------------

def analyze_commit(repo: str, meta: CommitMeta, repo_url: str) -> dict:
    parent = meta.parents[0] if meta.parents else EMPTY_TREE
    files = changed_files(repo, parent, meta.sha)
    blobs = BlobReader(repo)
    try:
        file_kinds: dict[str, str] = {}
        old_params: dict[tuple[str, str], dict] = {}  # (file, name) -> info
        new_params: dict[tuple[str, str], dict] = {}
        const_changes, const_added, const_removed = [], [], []
        proto_added, proto_removed = [], []
        param_files = set()
        lfs_changes: list[dict] = []
        for fc in files:
            old_path = fc.old_path or fc.path
            old = blobs.text(parent, old_path) if fc.status != "A" and parent != EMPTY_TREE else None
            new = blobs.text(meta.sha, fc.path) if fc.status != "D" else None
            if fc.status in ("M", "R"):
                file_kinds[fc.path] = classify_file_change(fc.path, old, new)
            else:
                file_kinds[fc.path] = "code"
            for txt, bucket in ((old, old_params), (new, new_params)):
                if txt and fc.path.endswith(".rs") and "param!(" in txt:
                    param_files.add(fc.path)
                    for name, info in parse_params(txt).items():
                        bucket[(fc.path, name)] = info
            if fc.status in ("M", "R") and old is not None and new is not None and file_kinds[fc.path] == "code":
                oc, nc = parse_constants(old_path, old), parse_constants(fc.path, new)
                dup = {k.split("#")[0] for k in list(oc) + list(nc) if "#" in k}
                for k in sorted(set(oc) | set(nc)):
                    name = k.split("#")[0]
                    if name in dup:
                        continue  # same name defined several times in a file: pairing would be a guess
                    if k in oc and k in nc and oc[k] != nc[k] and is_literalish(oc[k]) and is_literalish(nc[k]):
                        const_changes.append({"file": fc.path, "name": name, "old": oc[k], "new": nc[k]})
                    elif k in nc and k not in oc and name.isupper():
                        const_added.append({"file": fc.path, "name": name, "value": nc[k]})
                    elif k in oc and k not in nc and name.isupper():
                        const_removed.append({"file": fc.path, "name": name, "value": oc[k]})
            lfs = [t for t in (old, new) if t and t.startswith("version https://git-lfs")]
            if lfs:
                size = lambda t: (re.search(r"^size (\d+)", t or "", re.M) or [None, None])[1]
                lfs_changes.append({"file": fc.path, "status": fc.status,
                                    "old_size": int(size(old)) if size(old) else None,
                                    "new_size": int(size(new)) if size(new) else None})
            if fc.path.endswith((".proto", ".thrift")):
                of = proto_fields(old_path, old) if old else set()
                nf = proto_fields(fc.path, new) if new else set()
                if old is not None and new is not None:
                    proto_added += [{"file": fc.path, "field": f} for f in sorted(nf - of)]
                    proto_removed += [{"file": fc.path, "field": f} for f in sorted(of - nf)]
    finally:
        blobs.close()

    # Params diff, keyed by (file, name). A name that leaves one changed file and appears in another
    # in the same commit is reported as moved, not as remove + add.
    params_added, params_removed, params_changed, params_moved = [], [], [], []
    gone = {k for k in old_params if k not in new_params}
    new_keys = {k for k in new_params if k not in old_params}
    gone_by_name = {name: f for f, name in gone}
    for key in sorted(set(old_params) | set(new_params)):
        f, name = key
        o, n = old_params.get(key), new_params.get(key)
        if o and n:
            if o["default"] != n["default"] or o["type"] != n["type"]:
                params_changed.append({"name": name, "file": f, "key": n["key"], "type": n["type"],
                                       "old": o["default"], "new": n["default"]})
        elif n:
            src = gone_by_name.get(name)
            if src is not None:
                ov = old_params[(src, name)]
                params_moved.append({"name": name, "file": f, "from": src, **n})
                if ov["default"] != n["default"]:
                    params_changed.append({"name": name, "file": f, "key": n["key"], "type": n["type"],
                                           "old": ov["default"], "new": n["default"]})
            else:
                params_added.append({"name": name, "file": f, **n})
        elif o:
            if not any(k[1] == name for k in new_keys):
                params_removed.append({"name": name, "file": f, **o})

    # A param that disappears from the changed files may still be defined elsewhere (e.g. it moved
    # from home-mixer/params/param.rs into vm-ranker/params.rs). Mark those instead of calling them removed.
    if params_removed:
        names = [p["name"] for p in params_removed]
        pattern = r"^\s*(param!\(\s*)?(" + "|".join(re.escape(n) for n in names) + r")\s*,"
        out = run(["grep", "-n", "-E", pattern, meta.sha, "--", "*.rs"], cwd=repo, check=False)
        found: dict[str, str] = {}
        for line in out.splitlines():
            parts = line.split(":", 3)
            if len(parts) < 4:
                continue
            fpath, text = parts[1], parts[3]
            m = re.match(r"\s*(?:param!\(\s*)?(\w+)\s*,", text)
            if m and m.group(1) in names and "params" in fpath:
                found.setdefault(m.group(1), fpath)
        for p in params_removed:
            if p["name"] in found:
                p["still_defined_in"] = found[p["name"]]

    # Components.
    comps_added, comps_removed, comps_changed, comps_renamed = [], [], [], []
    for fc in files:
        kind = classify_component(fc.path)
        old_kind = classify_component(fc.old_path) if fc.old_path else kind
        if fc.status == "A" and kind:
            comps_added.append({"kind": kind, "name": component_name(fc.path), "path": fc.path})
        elif fc.status == "D" and kind:
            comps_removed.append({"kind": kind, "name": component_name(fc.path), "path": fc.path})
        elif fc.status == "R" and (kind or old_kind):
            if component_name(fc.old_path) != component_name(fc.path):
                comps_renamed.append({"kind": kind or old_kind, "old": component_name(fc.old_path),
                                      "new": component_name(fc.path), "path": fc.path})
            elif file_kinds.get(fc.path) == "code" and kind:
                comps_changed.append({"kind": kind, "name": component_name(fc.path), "path": fc.path})
        elif fc.status == "M" and kind and file_kinds.get(fc.path) == "code":
            comps_changed.append({"kind": kind, "name": component_name(fc.path), "path": fc.path})

    # Numeric/boolean tweaks from the diff (skip files covered by param! parsing and named consts).
    diff_text = unified_diff(repo, parent, meta.sha, context=0) if parent != EMPTY_TREE else ""
    known_consts = {(c["file"], c["name"]) for c in const_changes}
    tweaks = [t for t in numeric_tweaks(diff_text)
              if t["file"] not in param_files and t["file"] not in {l["file"] for l in lfs_changes} and (t["file"], t["label"]) not in known_consts
              and file_kinds.get(t["file"]) == "code"]

    # Modules summary.
    modules: dict[str, dict] = defaultdict(lambda: {"added": 0, "removed": 0, "modified": 0, "lines_added": 0, "lines_deleted": 0})
    for fc in files:
        m = modules[top_module(fc.path)]
        m[{"A": "added", "D": "removed"}.get(fc.status, "modified")] += 1
        m["lines_added"] += fc.added
        m["lines_deleted"] += fc.deleted

    kinds = Counter(file_kinds.values())
    meaningful = [fc for fc in files if file_kinds.get(fc.path) == "code"]
    doc_only = bool(meaningful) and all(os.path.splitext(fc.path)[1] in DOC_EXTS for fc in meaningful)
    if not meaningful:
        change_class = "noop"
    elif doc_only:
        change_class = "docs"
    else:
        change_class = "code"

    short = meta.sha[:7]
    return {
        "sha": meta.sha,
        "short": short,
        "url": f"{repo_url}/commit/{meta.sha}",
        "date": meta.date,
        "author": meta.author,
        "subject": meta.subject,
        "body": meta.body,
        "is_merge": len(meta.parents) > 1,
        "is_root": not meta.parents,
        "parent": meta.parents[0] if meta.parents else None,
        "class": change_class,
        "stats": {
            "files": len(files),
            "added_files": sum(1 for f in files if f.status == "A"),
            "removed_files": sum(1 for f in files if f.status == "D"),
            "lines_added": sum(f.added for f in files),
            "lines_deleted": sum(f.deleted for f in files),
            "noise_files": kinds.get("noise", 0),
            "whitespace_files": kinds.get("whitespace", 0),
            "comment_only_files": kinds.get("comments", 0),
        },
        "modules": dict(sorted(modules.items(), key=lambda kv: -(kv[1]["lines_added"] + kv[1]["lines_deleted"]))),
        "files": [{"status": f.status, "path": f.path, "old_path": f.old_path, "added": f.added,
                   "deleted": f.deleted, "kind": file_kinds.get(f.path, "code")} for f in files],
        "components": {"added": comps_added, "removed": comps_removed, "renamed": comps_renamed, "changed": comps_changed},
        "params": {"added": params_added, "removed": params_removed, "changed": params_changed, "moved": params_moved},
        "constants": {"changed": const_changes, "added": const_added, "removed": const_removed},
        "tweaks": tweaks,
        "list_sizes": list_size_changes(diff_text),
        "lfs": lfs_changes,
        "proto": {"added": proto_added, "removed": proto_removed},
    }
