#!/usr/bin/env python3
"""SessionStart hook: prints compact briefing and initializes task state."""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _boot import bootstrap  # noqa: E402


def main() -> int:
    hook, active = bootstrap(allow_payload_for_agy={})
    if not active:
        return 0

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from _common import (
        HARNESS, KNOWLEDGE, ROOT, emit, git, git_dirty_files, hook_transcript_path,
        is_antigravity_hook, is_git_repo, is_harness_file, list_repo_files, load_config,
    )
    from _task import StateError, ensure, gate_active, load_tasks, since_last_evolve
    import codegraph
    from kb_check import TEMPLATE_MARK, pitfall_hits

    # If invoked from an external Plugin on a --thin workspace, auto-mount local symlinks
    # (gitignored inside .harness/.gitignore) so `python3 .harness/scripts/...` works seamlessly.
    bundled_harness = Path(__file__).resolve().parents[2]
    if bundled_harness != HARNESS and (bundled_harness / "scripts").is_dir():
        gi = HARNESS / ".gitignore"
        gi_text = gi.read_text(encoding="utf-8") if gi.exists() else ""
        needed = [e for e in ("scripts", "workflows", "templates") if e not in gi_text.splitlines()]
        if needed:
            gi.write_text(gi_text.rstrip("\n") + ("\n" if gi_text.strip() else "") + "\n".join(needed) + "\n", encoding="utf-8")
        for sub in ("scripts", "workflows", "templates"):
            dst = HARNESS / sub
            src = bundled_harness / sub
            if src.is_dir() and not dst.exists() and not dst.is_symlink():
                dst.symlink_to(src)

    def pitfall_titles(limit: int = 8) -> list:
        path = KNOWLEDGE / "pitfalls.md"
        if not path.exists():
            return []
        text = re.sub(r"<!--.*?-->", "", path.read_text(encoding="utf-8"), flags=re.S)
        titles = dict(re.findall(r"^###\s+(P\d{3})\s*[·:\-]?\s*(.+)$", text, re.M))
        ranked = sorted(pitfall_hits(path), key=lambda x: -x[1])
        return [f"{pid}（命中 {hits}）{titles.get(pid, '')}" for pid, hits in ranked[:limit]]

    sid = hook.get("session_id") or hook.get("conversationId") or "manual"
    try:
        task = ensure(session_id=sid, transcript_path=hook_transcript_path(hook))
    except StateError as exc:
        print(f"[harness] 无法初始化任务：{exc}", file=sys.stderr)
        return 2

    cfg = load_config()
    out = ["# Harness 简报（自动注入）", ""]
    if task.get("summary"):
        out.append(f"当前任务：`{task['id']}` [Tier {task.get('tier', 'M')}] — {task['summary']}"
                   f"（状态：{task['status']}；完工用 `task.py finish`）")
    else:
        out.append(f"当前任务：`{task['id']}`（空闲；按改动规模用 `task.py start --tier S|M|L --summary ... --accept ...` 启动）")

    arch = ROOT / "docs" / "ARCHITECTURE.md"
    project_files = [f for f in list_repo_files() if not is_harness_file(f)]
    if not arch.exists() or TEMPLATE_MARK in arch.read_text(encoding="utf-8"):
        if not project_files:
            out.append("⚠️ **空白项目尚未初始化（0→1 冷启动）**：当前目录尚无业务源码。"
                       "请执行 `.harness/workflows/onboard.md`（含第 1 步搭建最小可运行骨架与冒烟测试）。")
        else:
            out.append("⚠️ **知识库尚未初始化**：开始用户任务前，先执行 `.harness/workflows/onboard.md`（至少完成第 1–3 步）。")
    off = [g for g in ("verify", "learn") if not gate_active(task, g, cfg)]
    if off:
        out.append(f"ℹ️ 已按用户要求关闭关卡：{', '.join(off)}（`gate.py status` 查看原因）。")
    elif not cfg.get("verify"):
        out.append("⚠️ `.harness/project.json` 未配置 verify 命令；verify.py 会使用自动检测结果。")

    if is_git_repo():
        branch = (git("branch", "--show-current") or "?").strip()
        dirty = git_dirty_files()
        log = (git("log", "--oneline", "-3") or "").strip()
        out += ["", f"## Git：分支 `{branch}`，{len(dirty)} 个未提交文件"]
        if log:
            out += [f"- {line}" for line in log.splitlines()]

    graph = codegraph.sync(timeout=30)
    out += ["", f"## 代码图谱（CodeGraph）：{graph}"]

    modules = sorted(p.stem for p in (KNOWLEDGE / "modules").glob("*.md") if p.name != "README.md")
    out += ["", f"## 知识库（模块卡片：{', '.join(modules) if modules else '无'}）"]
    titles = pitfall_titles()
    if titles:
        out += [f"- {t}" for t in titles]

    pending = len(since_last_evolve(load_tasks()))
    if pending >= cfg.get("evolve_every_n_tasks", 10):
        out.append(f"- ⚡ 距上次进化已完成 {pending} 个任务：本次用户任务完成后，建议执行 `/evolve`。")

    text = "\n".join(out)
    if is_antigravity_hook(hook):
        emit(hook, {"injectSteps": [{"systemMessage": {"systemMessage": text}}]})
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
