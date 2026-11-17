#!/usr/bin/env python3
"""Install or link the AI Harness master template into a project, or export as a host plugin.

Usage:
  python3 install.py /path/to/project                  # standalone team install (non-destructive)
  python3 install.py /path/to/project --upgrade        # upgrade control plane & prune retired files
  python3 install.py /path/to/project --link           # symlink control plane to this master repo (0-copy)
  python3 install.py /path/to/project --thin           # write only project data plane (for global plugin users)
  python3 install.py --register-global                 # register global `/harness` keyword in Antigravity, Claude & Codex
  python3 install.py --export-plugin /path/to/plugin   # materialize a multi-host plugin bundle (AGY/Claude/Codex)
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List

TEMPLATE = Path(__file__).resolve().parent / "template"
SIDE_BY_SIDE = {"AGENTS.md", "CLAUDE.md"}
SKIP_PARTS = {".state", "__pycache__", ".DS_Store", "tools"}

UPGRADEABLE_PREFIXES = (
    ".harness/scripts/",
    ".harness/workflows/",
    ".harness/templates/",
    ".harness/README.md",
    "docs/guidelines/",
    ".claude/skills/",
    ".claude/agents/",
    ".agents/skills/",
)

LINKABLE_DIRS = (
    ".harness/scripts",
    ".harness/workflows",
    ".harness/templates",
    "docs/guidelines",
    ".claude/skills",
    ".claude/agents",
    ".agents/skills",
)

THIN_DATA_PREFIXES = (
    "AGENTS.md",
    "CLAUDE.md",
    ".harness/.gitignore",
    ".harness/project.json",
    ".harness/evolution/",
    "docs/ARCHITECTURE.md",
    "docs/guidelines/",
    "docs/knowledge/",
)

DEPRECATED_FILES = (
    ".harness/scripts/_coordination.py",
    ".harness/scripts/_evidence.py",
    ".harness/scripts/_state.py",
    ".harness/scripts/coordinate.py",
    ".harness/scripts/record_task.py",
    ".harness/scripts/repo_map.py",
    ".harness/scripts/hooks/coordination.py",
    ".harness/scripts/hooks/post_edit.py",
    ".harness/workflows/acceptance.md",
    ".harness/workflows/concurrent.md",
    "docs/knowledge/generated/REPO_MAP.md",
    "docs/guidelines/chrome_browser_design_principles.md",
    "docs/guidelines/chromium_layered_components.md",
)

OBSOLETE_COMMAND_KEYWORDS = (
    "coordination.py",
    "post_edit.py",
    "coordinate.py",
    "record_task.py",
    "repo_map.py",
)


def merge_settings(existing: Dict[str, Any], ours: Dict[str, Any], prune: bool = False) -> Dict[str, Any]:
    out = json.loads(json.dumps(existing))
    if prune:
        perms = out.get("permissions") or {}
        for key in list(perms.keys()):
            perms[key] = [p for p in perms[key] if not any(k in p for k in OBSOLETE_COMMAND_KEYWORDS)]
    if ours.get("permissions"):
        perms = out.setdefault("permissions", {})
        for key, values in ours["permissions"].items():
            current: List[str] = perms.setdefault(key, [])
            current.extend(v for v in values if v not in current)
    hooks = out.setdefault("hooks", {})
    if prune:
        for event in list(hooks.keys()):
            cleaned_groups = []
            for g in hooks[event]:
                filtered = [
                    h for h in g.get("hooks", [])
                    if not any(k in h.get("command", "") for k in OBSOLETE_COMMAND_KEYWORDS)
                ]
                if filtered:
                    g["hooks"] = filtered
                    cleaned_groups.append(g)
            hooks[event] = cleaned_groups
    for event, groups in ours.get("hooks", {}).items():
        current_groups = hooks.setdefault(event, [])
        known = {h.get("command") for g in current_groups for h in g.get("hooks", [])}
        for g in groups:
            if not any(h.get("command") in known for h in g.get("hooks", [])):
                current_groups.append(g)
    return out


def merge_antigravity_hooks(existing: Dict[str, Any], ours: Dict[str, Any], prune: bool = False) -> Dict[str, Any]:
    out = json.loads(json.dumps(existing))
    agy_obsolete = (*OBSOLETE_COMMAND_KEYWORDS, ".harness/scripts/hooks/')")
    if prune:
        for spec in out.values():
            if not isinstance(spec, dict):
                continue
            for event in list(spec.keys()):
                items = spec[event]
                if not isinstance(items, list):
                    continue
                cleaned = []
                for item in items:
                    if isinstance(item, dict) and "hooks" in item:
                        filtered = [
                            h for h in item.get("hooks", [])
                            if not any(k in h.get("command", "") for k in agy_obsolete)
                        ]
                        if filtered:
                            item["hooks"] = filtered
                            cleaned.append(item)
                    elif isinstance(item, dict):
                        if not any(k in item.get("command", "") for k in agy_obsolete):
                            cleaned.append(item)
                spec[event] = cleaned
                if not spec[event] and event == "PostToolUse":
                    del spec[event]
    for hook_name, spec in ours.items():
        if not isinstance(spec, dict):
            continue
        target_spec = out.setdefault(hook_name, {})
        if not isinstance(target_spec, dict):
            out[hook_name] = spec
            continue
        for event, items in spec.items():
            if not isinstance(items, list):
                target_spec.setdefault(event, items)
                continue
            current = target_spec.setdefault(event, [])
            if items and isinstance(items[0], dict) and "hooks" in items[0]:
                known = {
                    h.get("command")
                    for g in current if isinstance(g, dict)
                    for h in g.get("hooks", []) if isinstance(h, dict)
                }
                for g in items:
                    if not any(h.get("command") in known for h in g.get("hooks", [])):
                        current.append(g)
            else:
                known = {h.get("command") for h in current if isinstance(h, dict)}
                for h in items:
                    if isinstance(h, dict) and h.get("command") not in known:
                        current.append(h)
    return out


def register_global_skill(keyword: str = "harness", dry_run: bool = False) -> int:
    installer = Path(__file__).resolve()
    home = Path.home()
    skill_targets = [
        ("Antigravity", home / ".gemini" / "config" / "skills" / keyword / "SKILL.md"),
        ("Claude Code", home / ".claude" / "skills" / keyword / "SKILL.md"),
        ("OpenAI Codex", home / ".codex" / "skills" / keyword / "SKILL.md"),
    ]
    skill_body = f"""---
name: {keyword}
description: >-
  在其他项目一键接入、冷启动或升级 AI Harness 工程母版。当用户输入 /{keyword}，或提到“接入 harness”、“安装 harness”、“升级/同步 harness 母版”、“用 harness 初始化新项目”时立即触发。
---

# /{keyword} — AI Harness 母版全局接入与同步入口

母版安装器路径：`{installer}`

在当前工作区根目录按现状自动分流执行（不要向用户抛连环选择题）：

1. **检查当前项目状态**：
   - 查看当前目录是否已有 `.harness/`、是否是 Git 仓库（空白新目录尚未 `git init` 时先执行 `git init`），以及用户是否明确指定了 `link`（软链零拷贝模式）或 `thin`（极薄数据层模式）。
2. **首次接入（尚无 `.harness/`）**：
   - 默认执行：`python3 "{installer}" .`（若用户要求 `link`/软链模式则加 `--link`；要求 `thin` 则加 `--thin`）。
   - 安装完成后，**立即读取并严格执行当前项目内的 `.harness/workflows/onboard.md`**：
     - 存量项目：合并已有入口文件 → 初始化 CodeGraph → 调研并实跑校准 `verify` 与 `smoke` 轨道 → 编写 `docs/ARCHITECTURE.md` 与核心模块卡片 → `task.py finish` 闭环。
     - 0→1 空白目录：若用户已在请求中说明技术栈与目标，直接搭建最小可运行骨架与冒烟测试并完成 onboard；若完全未说明，仅问 1 句话确认技术栈与一期目标。
3. **已安装但未完成初始化（已有 `.harness/`，但 `docs/ARCHITECTURE.md` 仍含 `harness:template` 标记）**：
   - 先执行 `python3 "{installer}" . --upgrade` 确保控制平面为最新母版，随后继续执行 `.harness/workflows/onboard.md` 完成接入。
4. **已接入项目的母版同步升级（已完成 onboard，用户触发 `/{keyword}` 或要求升级）**：
   - 执行 `python3 "{installer}" . --upgrade`（若项目原为软链模式或用户指定 `link`，则用 `--link`），无损更新脚本/工作流/通用架构准则并清理退役文件（绝不覆盖项目专属 `docs/` 与 `.harness/project.json`）。
   - 随后运行 `python3 .harness/scripts/kb_check.py` 与 `python3 .harness/scripts/task.py stats`，简要汇报升级变更与当前知识库/遥测健康度。
"""
    for host_name, path in skill_targets:
        if not dry_run:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(skill_body, encoding="utf-8")
        print(f"  [{host_name}] 已注册全局关键字 `/{keyword}` → {path}" + ("（演练）" if dry_run else ""))
    print(f"\n完成！现在打开任意其他项目，直接输入 `/{keyword}`（或 `/{keyword} link`）即可一键接入或升级母版。")
    return 0


def export_plugin(dest: Path, dry_run: bool = False) -> int:
    dest = dest.expanduser().resolve()
    if not dry_run:
        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copytree(TEMPLATE / ".harness", dest / "runtime" / ".harness",
                        ignore=shutil.ignore_patterns(".state", "__pycache__", ".DS_Store", "tools"))
        shutil.copytree(TEMPLATE / ".agents" / "skills", dest / "skills")
        shutil.copytree(TEMPLATE / ".claude" / "agents", dest / "agents")
        (dest / "rules").mkdir(parents=True, exist_ok=True)
        shutil.copy2(TEMPLATE / "docs" / "guidelines" / "architecture_principles.md", dest / "rules" / "AGENTS.md")

        # Rewrite SKILL.md workflow links to point to bundled runtime workflows with workspace fallback
        for skill_md in (dest / "skills").rglob("SKILL.md"):
            text = skill_md.read_text(encoding="utf-8")
            text = text.replace("(../../../.harness/workflows/", "(../../runtime/.harness/workflows/")
            skill_md.write_text(text, encoding="utf-8")

        agy_manifest = {
            "name": "ai-harness",
            "displayName": "AI Harness Engineering Master",
            "version": "2.0.0",
            "description": "Git-native verification gates, S/M/L tiered workflows, CodeGraph context, and self-evolving project memory.",
            "suggestedPrompts": [
                "执行 onboard，接入 harness 并校准校验与冒烟轨道",
                "按 feature 工作流实现新需求并通过门禁收尾",
                "按 fix 工作流先写外部复现探针见红再根因修复",
            ],
        }
        (dest / "plugin.json").write_text(json.dumps(agy_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        # Workspace-gated Antigravity hooks (no-op in <5ms for workspaces without .harness/)
        plugin_hooks = {
            "ai-harness": {
                "SessionStart": [{"type": "command", "command": "python3 ./runtime/.harness/scripts/hooks/session_start.py", "timeout": 45}],
                "PreToolUse": [{
                    "matcher": "edit_file|write_file|multi_edit_file",
                    "hooks": [{"type": "command", "command": "python3 ./runtime/.harness/scripts/hooks/guard.py", "timeout": 10}],
                }],
                "Stop": [{"type": "command", "command": "python3 ./runtime/.harness/scripts/hooks/stop_gate.py", "timeout": 20}],
            }
        }
        (dest / "hooks.json").write_text(json.dumps(plugin_hooks, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        claude_dir = dest / ".claude-plugin"
        claude_dir.mkdir(exist_ok=True)
        (claude_dir / "plugin.json").write_text(json.dumps({
            "name": "ai-harness",
            "version": "2.0.0",
            "description": agy_manifest["description"],
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        claude_hooks_dir = dest / "hooks"
        claude_hooks_dir.mkdir(exist_ok=True)
        (claude_hooks_dir / "hooks.json").write_text(json.dumps({
            "hooks": {
                "SessionStart": [{"hooks": [{"type": "command", "command": "python3 \"${CLAUDE_PLUGIN_ROOT}/runtime/.harness/scripts/hooks/session_start.py\"", "timeout": 45}]}],
                "PreToolUse": [{"matcher": "Edit|Write|MultiEdit|NotebookEdit|apply_patch", "hooks": [{"type": "command", "command": "python3 \"${CLAUDE_PLUGIN_ROOT}/runtime/.harness/scripts/hooks/guard.py\"", "timeout": 10}]}],
                "Stop": [{"hooks": [{"type": "command", "command": "python3 \"${CLAUDE_PLUGIN_ROOT}/runtime/.harness/scripts/hooks/stop_gate.py\"", "timeout": 20}]}],
            }
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        codex_dir = dest / ".codex-plugin"
        codex_dir.mkdir(exist_ok=True)
        (codex_dir / "plugin.json").write_text(json.dumps({
            "name": "ai-harness",
            "version": "2.0.0",
            "description": agy_manifest["description"],
            "skills": "./skills/",
            "hooks": "./hooks/hooks.json",
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"已导出多宿主 Plugin 包到：{dest}" + ("（演练）" if dry_run else ""))
    print("  包含：plugin.json (Antigravity) / .claude-plugin / .codex-plugin / skills / agents / rules / workspace-gated hooks")
    print("  配合 `python3 install.py <项目路径> --thin` 即可零控制平面污染接入任意业务仓库。")
    return 0


def _under_linked_dir(rel_posix: str) -> bool:
    return any(rel_posix == d or rel_posix.startswith(d + "/") for d in LINKABLE_DIRS)


def _unlink_symlink_parents(target: Path, rel: Path) -> None:
    cur = target
    for part in rel.parts[:-1]:
        cur = cur / part
        if cur.is_symlink():
            cur.unlink()
            break


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("target", nargs="?")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--upgrade", action="store_true",
                    help="update control-plane files & prune deprecated files while preserving project docs/config")
    ap.add_argument("--link", action="store_true",
                    help="symlink control-plane directories directly to this master repo for zero-copy instant sync")
    ap.add_argument("--thin", action="store_true",
                    help="install only the thin project data plane (AGENTS.md, project.json, docs/knowledge)")
    ap.add_argument("--register-global", action="store_true",
                    help="register global /harness skill in Antigravity, Claude Code, and Codex")
    ap.add_argument("--keyword", default="harness",
                    help="custom slash/skill keyword for --register-global (default: harness)")
    ap.add_argument("--export-plugin", metavar="DIR",
                    help="export a self-contained multi-host Plugin bundle (Antigravity / Claude Code / Codex)")
    args = ap.parse_args()

    if args.register_global:
        return register_global_skill(keyword=args.keyword.strip().lstrip("/"), dry_run=args.dry_run)
    if args.export_plugin:
        return export_plugin(Path(args.export_plugin), dry_run=args.dry_run)
    if not args.target:
        ap.error("请指定目标项目路径，或使用 --register-global / --export-plugin <DIR>")

    target = Path(args.target).expanduser().resolve()
    if not target.is_dir():
        print(f"目标目录不存在：{target}")
        return 1
    was_empty = not any(p.name not in (".git", ".DS_Store") for p in target.iterdir())

    copied, upgraded, linked, side, merged, skipped = [], [], [], [], [], []

    if args.link and not args.thin:
        for d in LINKABLE_DIRS:
            src_dir = TEMPLATE / d
            dst_dir = target / d
            if not src_dir.exists():
                continue
            if dst_dir.is_symlink() and dst_dir.resolve() == src_dir.resolve():
                skipped.append(Path(d))
                continue
            linked.append(Path(d))
            if not args.dry_run:
                dst_dir.parent.mkdir(parents=True, exist_ok=True)
                if dst_dir.is_symlink() or dst_dir.is_file():
                    dst_dir.unlink()
                elif dst_dir.is_dir():
                    shutil.rmtree(dst_dir)
                os.symlink(src_dir, dst_dir)

    for src in sorted(TEMPLATE.rglob("*")):
        rel = src.relative_to(TEMPLATE)
        rel_posix = rel.as_posix()
        if src.is_dir() or SKIP_PARTS & set(rel.parts):
            continue
        if args.thin and not any(rel_posix == p or rel_posix.startswith(p) for p in THIN_DATA_PREFIXES):
            continue
        if args.link and not args.thin and _under_linked_dir(rel_posix):
            continue

        if not args.link and not args.dry_run:
            _unlink_symlink_parents(target, rel)

        dst = target / rel
        if not dst.exists() and not dst.is_symlink():
            copied.append(rel)
            if not args.dry_run:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
        elif args.upgrade and any(rel_posix == p or rel_posix.startswith(p) for p in UPGRADEABLE_PREFIXES):
            if dst.is_symlink() or dst.read_bytes() != src.read_bytes():
                upgraded.append(rel)
                if not args.dry_run:
                    if dst.is_symlink():
                        dst.unlink()
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)
            else:
                skipped.append(rel)
        elif rel_posix in SIDE_BY_SIDE:
            if not (args.upgrade or args.link):
                alt = dst.with_name(dst.stem + ".harness.md")
                side.append(alt.relative_to(target))
                if not args.dry_run:
                    shutil.copy2(src, alt)
            else:
                skipped.append(rel)
        elif rel_posix in (".claude/settings.json", ".codex/hooks.json"):
            merged.append(rel)
            if not args.dry_run:
                result = merge_settings(
                    json.loads(dst.read_text(encoding="utf-8")),
                    json.loads(src.read_text(encoding="utf-8")),
                    prune=(args.upgrade or args.link),
                )
                dst.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        elif rel_posix == ".agents/hooks.json":
            merged.append(rel)
            if not args.dry_run:
                result = merge_antigravity_hooks(
                    json.loads(dst.read_text(encoding="utf-8")),
                    json.loads(src.read_text(encoding="utf-8")),
                    prune=(args.upgrade or args.link),
                )
                dst.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        else:
            skipped.append(rel)

    pruned = []
    if args.thin and not args.dry_run:
        gi = target / ".harness" / ".gitignore"
        gi_text = gi.read_text(encoding="utf-8") if gi.exists() else ""
        extra = [e for e in ("scripts", "workflows", "templates") if e not in gi_text.splitlines()]
        if extra:
            gi.parent.mkdir(parents=True, exist_ok=True)
            gi.write_text(gi_text.rstrip("\n") + ("\n" if gi_text.strip() else "") + "\n".join(extra) + "\n", encoding="utf-8")

    if args.upgrade or args.link:
        for dep in DEPRECATED_FILES:
            dep_path = target / dep
            if dep_path.exists() or dep_path.is_symlink():
                # Avoid deleting inside a newly created symlinked directory
                if args.link and _under_linked_dir(dep):
                    continue
                pruned.append(dep)
                if not args.dry_run:
                    dep_path.unlink()
                    parent = dep_path.parent
                    if parent.is_dir() and not any(parent.iterdir()):
                        parent.rmdir()

    tag = "（演练，未写入）" if args.dry_run else ""
    print(f"安装到 {target} {tag}")
    print(f"  新增 {len(copied)} 个文件")
    if linked:
        print(f"  软链同步 {len(linked)} 个控制平面目录：{', '.join(p.as_posix() for p in linked)}")
    if upgraded:
        print(f"  升级 {len(upgraded)} 个控制平面文件：{', '.join(p.as_posix() for p in upgraded)}")
    if pruned:
        print(f"  清理 {len(pruned)} 个废弃文件：{', '.join(pruned)}")
    for p in merged:
        print(f"  合并 {p}（权限与 hooks 取并集）")
    for p in side:
        print(f"  已有同名入口文件，harness 版本写到 {p}；onboard 时会合并")
    for p in skipped:
        print(f"  跳过已存在：{p}")
    if was_empty:
        print(
            "\n检测到目标为空白项目目录（0→1 新项目）。\n"
            "下一步：在 Claude Code / Codex / Antigravity 中打开该项目，直接说：\n"
            "  “用 <语言/框架> 初始化 <项目目标>，并执行 onboard 接入 harness”\n"
            "  （或直接输入 /onboard，由 agent 引导搭建最小可运行骨架与冒烟测试）"
        )
    else:
        print(
            "\n下一步：在 Claude Code / Codex / Antigravity 中打开该项目，说一句：\n"
            "  “执行 onboard，接入 harness”   （也可以直接输入 /onboard）\n"
            "完成后，就可以用一句话提需求或报 bug 了。"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
