#!/usr/bin/env python3
"""Knowledge-base & executable-architecture health check.

ERROR (exit 1):  broken local links in harness-maintained files (AGENTS.md, CLAUDE.md,
                 docs/knowledge/, docs/ARCHITECTURE.md, workflows); knowledge files missing from INDEX.md;
                 executable DEPS layer violations (via CodeGraph SQLite edges, or Python stdlib AST fallback)
                 and banned_patterns violations.
WARN:            symbol anchors (`path#Symbol`) whose symbol is gone; line-number references (`path:123`);
                 stale "最后验证" dates; AGENTS.md over budget; pitfalls hit >= 2 not yet promoted;
                 ARCHITECTURE.md still a template; code graph not initialised; module dependency cycles (A ↔ B).

Usage: python3 .harness/scripts/kb_check.py [--strict]   (--strict: warnings fail too)
"""

from __future__ import annotations

import argparse
import ast
import fnmatch
import os
import re
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).parent))
from _common import (  # noqa: E402
    CONTAINER_DIRS, KNOWLEDGE, ROOT, infer_modules, is_code_file, iter_md, list_repo_files, load_config,
)
from kb_anchors import Graph, line_references, stale_anchors  # noqa: E402

LINK_RX = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)\)")
DATE_RX = re.compile(r"最后验证\**[:：]\s*\**\s*(\d{4}-\d{2}-\d{2})")
PITFALL_RX = re.compile(r"^###\s+(P\d{3})\b.*?$(.*?)(?=^###\s|\Z)", re.M | re.S)
HITS_RX = re.compile(r"命中\**[:：]\s*\**\s*(\d+)")
FORBID_DEP_RX = re.compile(r"禁止依赖\s*[:：]\s*`?([A-Za-z0-9_.\-/]+)`?\s*(?:->|→)\s*`?([A-Za-z0-9_.\-/]+)`?")
BANNED_PAT_RX = re.compile(
    r"禁用正则\s*[:：]\s*`([^`]+)`\s*\|\s*范围\s*[:：]\s*`([^`]+)`(?:\s*\|\s*原因\s*[:：]\s*(.+))?"
)
PLACEHOLDER = ("<", ">", "path/to", "YYYY", "NNNN", "*", "file.ext", "example")
TEMPLATE_MARK = "<!-- harness:template"
LINE_SUFFIX_RX = re.compile(r":\d+(?:-\d+)?$")
EXT_STRIP_RX = re.compile(r"\.(?:py|ts|tsx|js|jsx|go|rs|java|cpp|cc|c|h|hpp|m|mm|swift|kt)$")
OWNED = ("AGENTS.md", "CLAUDE.md", "docs/ARCHITECTURE.md", "docs/knowledge/", ".harness/")


def is_owned(md: Path) -> bool:
    rel = md.relative_to(ROOT).as_posix()
    return any(rel == o or rel.startswith(o) for o in OWNED)


def strip_comments(text: str) -> str:
    return re.sub(r"<!--.*?-->", "", text, flags=re.S)


def strip_code_fences(text: str) -> str:
    return re.sub(r"```.*?```", "", text, flags=re.S)


def collect(cfg: Dict[str, Any]) -> Tuple[List[str], List[str], int]:
    """(errors, warnings, files scanned). Errors block `task.py finish`; warnings never do."""
    errors: List[str] = []
    warns: List[str] = []

    with Graph() as graph:
        scanned = list(iter_md([ROOT / "AGENTS.md", ROOT / "CLAUDE.md", ROOT / "docs",
                                ROOT / ".harness" / "workflows"]))
        for md in scanned:
            rel = md.relative_to(ROOT)
            text = strip_code_fences(strip_comments(md.read_text(encoding="utf-8")))
            owned = is_owned(md)
            for target in LINK_RX.findall(text):
                if re.match(r"^[a-z]+:", target) or target.startswith("#"):
                    continue
                path = LINE_SUFFIX_RX.sub("", target.split("#")[0])
                if path and not Path(os.path.normpath(md.parent / path)).exists():
                    (errors if owned else warns).append(f"{rel}: 断链 → {target}")
            if KNOWLEDGE in md.parents and "generated" not in md.parts:
                for anchor in stale_anchors(graph, text):
                    warns.append(f"{rel}: 符号锚点失效（符号已改名或删除）→ {anchor}")
                refs = line_references(text)
                if refs:
                    warns.append(f"{rel}: {len(refs)} 处行号引用（代码一改就错位），例如 {refs[0]}"
                                 " → 改成 `path#符号`，可用 kb_anchors.py --fix")
                for d in DATE_RX.findall(text):
                    try:
                        age = (time.time() - time.mktime(time.strptime(d, "%Y-%m-%d"))) / 86400
                    except ValueError:
                        continue
                    if age > cfg["stale_after_days"]:
                        warns.append(f"{rel}: 最后验证于 {d}（{int(age)} 天前），请复核")

        index = KNOWLEDGE / "INDEX.md"
        index_text = index.read_text(encoding="utf-8") if index.exists() else ""
        if not index.exists():
            errors.append("docs/knowledge/INDEX.md 不存在")
        for md in iter_md([KNOWLEDGE]):
            if md.name in ("INDEX.md", "README.md") or "generated" in md.parts:
                continue
            rel_to_index = md.relative_to(KNOWLEDGE).as_posix()
            if rel_to_index not in strip_comments(index_text):
                errors.append(f"未登记到 INDEX.md：docs/knowledge/{rel_to_index}")

        agents = ROOT / "AGENTS.md"
        if agents.exists():
            n = len(agents.read_text(encoding="utf-8").splitlines())
            if n > cfg["agents_md_max_lines"]:
                warns.append(f"AGENTS.md 有 {n} 行，超过 {cfg['agents_md_max_lines']} 行预算；请把细节下沉到 docs/")
        else:
            errors.append("AGENTS.md 不存在")

        pitfalls = KNOWLEDGE / "pitfalls.md"
        agents_text = agents.read_text(encoding="utf-8") if agents.exists() else ""
        for pid, hits in pitfall_hits(pitfalls):
            if hits >= 2 and pid not in agents_text:
                warns.append(f"pitfalls#{pid} 已命中 {hits} 次，尚未升级为 AGENTS.md 规则 → 运行 /evolve")

        arch = ROOT / "docs" / "ARCHITECTURE.md"
        if arch.exists() and TEMPLATE_MARK in arch.read_text(encoding="utf-8"):
            warns.append("docs/ARCHITECTURE.md 仍是模板 → 执行 onboard 工作流")

        if not (ROOT / ".codegraph").is_dir():
            warns.append("代码图谱未初始化 → python3 .harness/scripts/codegraph.py setup && python3 .harness/scripts/codegraph.py init")

        errors.extend(check_layer_rules(cfg, graph))
        cycles = check_module_cycles(cfg, graph)
        (errors if cfg.get("forbid_cycles") else warns).extend(cycles)
        errors.extend(check_banned_patterns(cfg))
        warns.extend(check_missing_module_cards(cfg))

    return errors, warns, len(scanned)


def check_missing_module_cards(cfg: Dict[str, Any], min_files: int = 3) -> List[str]:
    modules_dir = KNOWLEDGE / "modules"
    mod_counts: Counter = Counter()
    for rel in list_repo_files():
        if not is_code_file(rel, cfg):
            continue
        parts = Path(rel).parts
        if len(parts) <= 1:
            continue
        m = f"{parts[0]}/{parts[1]}" if parts[0] in CONTAINER_DIRS and len(parts) > 2 else parts[0]
        mod_counts[m] += 1

    missing = []
    for mod, count in mod_counts.items():
        if count < min_files:
            continue
        safe = mod.replace("/", "_")
        if not ((modules_dir / f"{safe}.md").exists() or (modules_dir / f"{Path(mod).name}.md").exists()):
            missing.append(f"主要模块缺少卡片：{mod} ({count} 个代码文件) → 请创建 docs/knowledge/modules/{safe}.md")
    return sorted(missing)


def pitfall_hits(path: Path) -> List[Tuple[str, int]]:
    if not path.exists():
        return []
    text = strip_comments(path.read_text(encoding="utf-8"))
    out = []
    for pid, body in PITFALL_RX.findall(text):
        m = HITS_RX.search(body)
        out.append((pid, int(m.group(1)) if m else 1))
    return out


def _matches_spec(rel: str, spec: str) -> bool:
    norm_rel = rel.replace("\\", "/").strip("/")
    norm_spec = spec.replace("\\", "/").strip("/")
    if norm_rel == norm_spec or norm_rel.startswith(norm_spec + "/"):
        return True
    clean_rel = EXT_STRIP_RX.sub("", norm_rel)
    clean_spec = EXT_STRIP_RX.sub("", norm_spec)
    return bool(clean_spec) and (clean_rel == clean_spec or clean_rel.startswith(clean_spec + "/"))


def _py_ast_imports_target(content: str, rel: str, dst_spec: str) -> Optional[int]:
    """Syntax-accurate Python AST fallback when .codegraph/codegraph.db is absent."""
    try:
        tree = ast.parse(content, filename=rel)
    except SyntaxError:
        return None
    clean_dst = EXT_STRIP_RX.sub("", dst_spec.replace("\\", "/").strip("/")).replace("/", ".")
    if not clean_dst:
        return None
    pkg_parts = list(Path(rel).parts[:-1])
    for node in ast.walk(tree):
        candidates: List[str] = []
        if isinstance(node, ast.Import):
            candidates = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level > 0:
                if node.level > len(pkg_parts) + 1:
                    continue
                base_parts = pkg_parts[: len(pkg_parts) - (node.level - 1)]
                mod_parts = base_parts + (node.module.split(".") if node.module else [])
                mod = ".".join(p for p in mod_parts if p)
            else:
                mod = node.module or ""
            if mod:
                candidates.append(mod)
            for alias in node.names:
                candidates.append(f"{mod}.{alias.name}" if mod else alias.name)
        for cand in candidates:
            if cand == clean_dst or cand.startswith(clean_dst + "."):
                return getattr(node, "lineno", 1)
    return None


def check_layer_rules(cfg: Dict[str, Any], graph: Graph) -> List[str]:
    rules: List[Tuple[str, str]] = []
    for item in cfg.get("forbid_deps", []):
        if isinstance(item, dict) and item.get("from") and item.get("to"):
            rules.append((str(item["from"]), str(item["to"])))
    arch = ROOT / "docs" / "ARCHITECTURE.md"
    if arch.exists():
        text = strip_code_fences(strip_comments(arch.read_text(encoding="utf-8")))
        for src_mod, dst_mod in FORBID_DEP_RX.findall(text):
            if not any(tok in src_mod or tok in dst_mod for tok in PLACEHOLDER):
                rules.append((src_mod.strip(), dst_mod.strip()))
    if not rules:
        return []

    violations: List[str] = []
    seen = set()
    if graph.available:
        for src_file, dst_file, _kind, lineno in graph.file_edges():
            if not (is_code_file(src_file, cfg) and is_code_file(dst_file, cfg)):
                continue
            for src_prefix, dst_target in rules:
                if _matches_spec(src_file, src_prefix) and _matches_spec(dst_file, dst_target):
                    key = (src_file, src_prefix, dst_target)
                    if key not in seen:
                        seen.add(key)
                        violations.append(
                            f"{src_file}:{lineno}: 架构分层违规（DEPS）— 禁止 `{src_prefix}` 依赖 `{dst_target}`"
                        )

    # Python AST fallback covers .py files before `codegraph.py init` (or unindexed dirty edits)
    code_files = [f for f in list_repo_files() if f.endswith(".py") and is_code_file(f, cfg)]
    for src_prefix, dst_target in rules:
        for rel in code_files:
            if not _matches_spec(rel, src_prefix) or (rel, src_prefix, dst_target) in seen:
                continue
            try:
                content = (ROOT / rel).read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            lineno = _py_ast_imports_target(content, rel, dst_target)
            if lineno is not None:
                seen.add((rel, src_prefix, dst_target))
                violations.append(
                    f"{rel}:{lineno}: 架构分层违规（DEPS）— 禁止 `{src_prefix}` 依赖 `{dst_target}`"
                )
    return sorted(violations)


def _is_test_path(rel: str) -> bool:
    p = Path(rel)
    return any(part in ("test", "tests", "__tests__") for part in p.parts) or p.name.startswith("test_") or p.stem.endswith("_test")


def check_module_cycles(cfg: Dict[str, Any], graph: Graph) -> List[str]:
    if not graph.available:
        return []
    mod_edges: Dict[Tuple[str, str], Tuple[str, int]] = {}
    for src_file, dst_file, _kind, lineno in graph.file_edges():
        if _is_test_path(src_file) or _is_test_path(dst_file):
            continue
        if not (is_code_file(src_file, cfg) and is_code_file(dst_file, cfg)):
            continue
        s_mods = infer_modules([src_file], cfg)
        d_mods = infer_modules([dst_file], cfg)
        if s_mods and d_mods and s_mods[0] != d_mods[0]:
            mod_edges.setdefault((s_mods[0], d_mods[0]), (src_file, lineno))
    warnings = []
    reported = set()
    for (a, b), (f1, l1) in sorted(mod_edges.items()):
        pair = (min(a, b), max(a, b))
        if (b, a) in mod_edges and pair not in reported:
            reported.add(pair)
            f2, l2 = mod_edges[(b, a)]
            warnings.append(f"模块级环形依赖：{a} ↔ {b}（{f1}:{l1} 与 {f2}:{l2}）")
    return warnings


def check_banned_patterns(cfg: Dict[str, Any]) -> List[str]:
    rules: List[Dict[str, str]] = []
    for item in cfg.get("banned_patterns", []):
        if isinstance(item, dict) and item.get("pattern"):
            rules.append({
                "pattern": str(item["pattern"]),
                "glob": str(item.get("glob", "*")),
                "reason": str(item.get("reason", "违反项目代码规约")),
            })
    patterns_md = KNOWLEDGE / "patterns.md"
    if patterns_md.exists():
        text = strip_code_fences(strip_comments(patterns_md.read_text(encoding="utf-8")))
        for pat, glob_pat, reason in BANNED_PAT_RX.findall(text):
            rules.append({
                "pattern": pat.strip(),
                "glob": glob_pat.strip() or "*",
                "reason": (reason or "违反 patterns.md 代码规约").strip(),
            })
    if not rules:
        return []
    code_files = [f for f in list_repo_files() if is_code_file(f, cfg)]
    violations: List[str] = []
    for rule in rules:
        try:
            rx = re.compile(rule["pattern"])
        except re.error as exc:
            violations.append(f"banned_patterns 正则无效 `{rule['pattern']}`: {exc}")
            continue
        glob_pat = rule["glob"]
        for rel in code_files:
            if not (fnmatch.fnmatch(rel, glob_pat) or fnmatch.fnmatch(Path(rel).name, glob_pat)):
                continue
            try:
                lines = (ROOT / rel).read_text(encoding="utf-8", errors="ignore").splitlines()
            except OSError:
                continue
            for lineno, line in enumerate(lines, 1):
                if rx.search(line) and "harness:ignore" not in line:
                    violations.append(
                        f"{rel}:{lineno}: 代码规约违规（命中 `{rule['pattern']}`）— {rule['reason']}"
                    )
    return violations


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict", action="store_true")
    args = ap.parse_args()
    errors, warns, scanned = collect(load_config())
    for e in errors:
        print(f"ERROR  {e}")
    other_links = [w for w in warns if "断链" in w]
    for w in warns:
        if "断链" not in w:
            print(f"WARN   {w}")
    if other_links:
        by_file: dict = {}
        for w in other_links:
            by_file.setdefault(w.split(": 断链")[0], []).append(w)
        print(f"WARN   项目原有文档中有 {len(other_links)} 个断链（非 harness 维护，不阻塞）：")
        for f, items in sorted(by_file.items(), key=lambda kv: -len(kv[1])):
            print(f"         {f}：{len(items)} 个，例如 {items[0].split('→ ')[-1]}")
    print(f"\n[kb_check] 扫描 {scanned} 个文件：{len(errors)} 个错误，{len(warns)} 个警告")
    return 1 if errors or (args.strict and warns) else 0


if __name__ == "__main__":
    sys.exit(main())
