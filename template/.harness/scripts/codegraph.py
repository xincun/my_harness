#!/usr/bin/env python3
"""Project code graph (CodeGraph) — the single entry point for install, init, update and query.

CodeGraph (https://github.com/colbymchenry/codegraph) parses the repo with
tree-sitter into a local SQLite graph at `.codegraph/codegraph.db`: symbols,
calls, imports and tests. The harness uses it as a plain CLI tool (no MCP):
agents query it through this script, and harness hooks keep it up to date.

Install / build:
  codegraph.py setup              # install CodeGraph into .harness/tools/ (project-local)
  codegraph.py init               # build the graph (or sync if it exists); excludes harness dirs
  codegraph.py sync               # incremental update (fast; safe to call often)
  codegraph.py install-git-hooks  # sync after git pull / checkout / rebase
  codegraph.py doctor             # check tool, index and git hooks

Query (each one syncs first, so results reflect the working tree):
  codegraph.py explore "<question or keywords>"   # source + call paths for a topic
  codegraph.py query <name> [--kind function]     # find symbols by name
  codegraph.py node <symbol|file>                 # a symbol's source and callers
  codegraph.py callers <symbol> / callees <symbol>
  codegraph.py impact <symbol> [--depth 3]        # what breaks if this changes
  codegraph.py affected [files...]                # affected tests (default: this session's changes)
  codegraph.py files [--max-depth 2]              # file structure
  codegraph.py status
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).parent))
from _common import (  # noqa: E402
    HARNESS, ROOT, STATE_DIR, git, is_code_file, list_repo_files, load_config, now,
)
from _task import StateError, changed_files, fingerprint, load

PACKAGE = "@colbymchenry/codegraph"
TOOLS_DIR = HARNESS / "tools"
LOCAL_BIN = TOOLS_DIR / "node_modules" / ".bin" / ("codegraph.cmd" if os.name == "nt" else "codegraph")
INDEX_DIR = ROOT / ".codegraph"
CONFIG_FILE = ROOT / "codegraph.json"
# The harness's own files are not project code; keep them out of the graph.
HARNESS_EXCLUDES = [".harness/", ".claude/", ".codex/", ".agents/"]
QUERY_CMDS = ("explore", "query", "node", "callers", "callees", "impact", "affected", "files")
SETUP_HINT = (
    "代码图谱工具未安装。运行：python3 .harness/scripts/codegraph.py setup\n"
    "（会把 CodeGraph 安装到项目内的 .harness/tools/，需要 npm；没有 npm 时，参考\n"
    " https://github.com/colbymchenry/codegraph 的独立安装方式，装好后 codegraph 在 PATH 上即可）"
)
GIT_HOOKS = ("post-merge", "post-checkout", "post-rewrite")
HOOK_MARK = "# >>> harness codegraph sync >>>"
HOOK_BLOCK = f"""{HOOK_MARK}
ROOT="$(git rev-parse --show-toplevel)"
if [ -d "$ROOT/.codegraph" ]; then
  (cd "$ROOT" && python3 .harness/scripts/codegraph.py sync >/dev/null 2>&1 &)
fi
# <<< harness codegraph sync <<<
"""


# ------------------------------------------------------------------ tool
def cli() -> Optional[str]:
    """Project-local install first (pinned with the project), then PATH."""
    override = os.environ.get("HARNESS_CODEGRAPH_BIN")
    if override:
        return override if Path(override).exists() else None
    if LOCAL_BIN.exists():
        return str(LOCAL_BIN)
    return shutil.which("codegraph")


def available() -> bool:
    return cli() is not None


def initialised() -> bool:
    return INDEX_DIR.is_dir()


def tool_env() -> dict:
    env = dict(os.environ)
    if not load_config().get("codegraph_telemetry", False):
        env.setdefault("CODEGRAPH_TELEMETRY", "0")  # privacy-preserving default
    return env


def run(args: List[str], timeout: float = 600, quiet: bool = False) -> Tuple[int, str]:
    exe = cli()
    if exe is None:
        return 127, SETUP_HINT
    try:
        p = subprocess.run([exe, *args], cwd=ROOT, capture_output=True, text=True,
                           timeout=timeout, env=tool_env())
    except subprocess.TimeoutExpired:
        return 124, f"codegraph {' '.join(args)} 超时（{timeout}s）"
    out = (p.stdout or "") + (p.stderr or "")
    if p.returncode != 0 and "lock" in out.lower():
        out += "\n提示：先确认其他 CodeGraph 进程是否仍在运行；不得抢占活跃进程的锁。"
    if not quiet:
        print(out.rstrip())
    return p.returncode, out


# ----------------------------------------------------------- lifecycle
def code_tree_fingerprint() -> str:
    cfg = load_config()
    entries = []
    for rel in sorted(list_repo_files()):
        if is_code_file(rel, cfg) or rel == "codegraph.json":
            try:
                st = (ROOT / rel).stat()
                entries.append(f"{rel}:{st.st_mtime_ns}:{st.st_size}")
            except OSError:
                continue
    return hashlib.sha256("\n".join(entries).encode("utf-8")).hexdigest()


def sync(timeout: float = 120, quiet: bool = True, force: bool = False) -> str:
    """Best-effort incremental sync. Returns a one-line status for briefings."""
    if not available():
        return "未安装（运行 `python3 .harness/scripts/codegraph.py setup`）"
    if not initialised():
        return "未初始化（运行 `python3 .harness/scripts/codegraph.py init`）"
    fp = code_tree_fingerprint()
    state_file = STATE_DIR / "codegraph.json"
    pstate = {}
    if state_file.is_file():
        try:
            pstate = json.loads(state_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    if not force and (INDEX_DIR / "codegraph.db").is_file():
        if pstate.get("codegraph_digest") == fp:
            return "已同步"
    code, out = run(["sync", str(ROOT)], timeout=timeout, quiet=quiet)
    if code == 0:
        pstate.update(codegraph_synced_at=now(), codegraph_digest=fp)
        try:
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            state_file.write_text(json.dumps(pstate), encoding="utf-8")
        except Exception:
            pass
        return "已同步"
    last = out.strip().splitlines()[-1] if out.strip() else ""
    return f"同步失败（exit {code}）：{last}"


def cmd_setup() -> int:
    if LOCAL_BIN.exists():
        print(f"[codegraph] 已安装：{LOCAL_BIN.relative_to(ROOT)}")
        return 0
    npm = shutil.which("npm")
    if npm is None:
        if shutil.which("codegraph"):
            print(f"[codegraph] 未找到 npm，将使用 PATH 上的 codegraph：{shutil.which('codegraph')}")
            return 0
        print("[codegraph] 未找到 npm，无法安装到项目内。\n" + SETUP_HINT)
        return 2
    TOOLS_DIR.mkdir(parents=True, exist_ok=True)
    pkg = TOOLS_DIR / "package.json"
    if not pkg.exists():
        pkg.write_text('{\n  "name": "harness-tools",\n  "private": true\n}\n', encoding="utf-8")
    print(f"[codegraph] 安装 {PACKAGE} 到 .harness/tools/ …")
    p = subprocess.run([npm, "install", "--prefix", str(TOOLS_DIR), "--no-fund", "--no-audit", PACKAGE])
    if p.returncode != 0 or not LOCAL_BIN.exists():
        print("[codegraph] 安装失败，请查看上方 npm 输出。")
        return 1
    print("[codegraph] 安装完成。下一步：python3 .harness/scripts/codegraph.py init")
    return 0


def ensure_excludes() -> bool:
    """Merge the harness excludes into codegraph.json. Returns True if it changed."""
    try:
        cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8")) if CONFIG_FILE.exists() else {}
    except json.JSONDecodeError:
        print("[codegraph] codegraph.json 不是合法 JSON，跳过排除规则合并。")
        return False
    exclude = cfg.setdefault("exclude", [])
    missing = [e for e in HARNESS_EXCLUDES if e not in exclude]
    if not missing:
        return False
    exclude.extend(missing)
    CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[codegraph] codegraph.json 已排除 harness 目录：{', '.join(missing)}")
    return True


def cmd_init() -> int:
    if not available():
        print(SETUP_HINT)
        return 2
    config_changed = ensure_excludes()
    if initialised() and config_changed:
        print("[codegraph] 排除规则有变化，重建索引…")
        code, _ = run(["index", "--force", str(ROOT)], timeout=3600)
    elif initialised():
        print("[codegraph] 索引已存在，执行增量同步…")
        code, _ = run(["sync", str(ROOT)])
    else:
        print("[codegraph] 首次构建代码图谱（大仓库可能需要几分钟）…")
        code, _ = run(["init", str(ROOT)], timeout=3600)
    if code == 0:
        try:
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            (STATE_DIR / "codegraph.json").write_text(json.dumps({"codegraph_synced_at": now(), "codegraph_digest": code_tree_fingerprint()}), encoding="utf-8")
        except Exception:
            pass
        run(["status", str(ROOT)])
    return code


def cmd_install_git_hooks() -> int:
    hooks_raw = git("rev-parse", "--git-path", "hooks")
    if hooks_raw is None:
        print("[codegraph] 不是 git 仓库，跳过 git hooks。")
        return 0
    hooks_dir = (ROOT / hooks_raw.strip()).resolve()
    hooks_dir.mkdir(parents=True, exist_ok=True)
    for name in GIT_HOOKS:
        path = hooks_dir / name
        text = path.read_text(encoding="utf-8") if path.exists() else "#!/bin/sh\n"
        if HOOK_MARK in text:
            continue
        path.write_text(text.rstrip("\n") + "\n\n" + HOOK_BLOCK, encoding="utf-8")
        path.chmod(0o755)
        print(f"[codegraph] 已安装 git hook：{name}")
    print("[codegraph] git pull / checkout / rebase 之后会在后台增量同步图谱。")
    return 0


def git_hooks_installed() -> Optional[bool]:
    hooks_raw = git("rev-parse", "--git-path", "hooks")
    if hooks_raw is None:
        return None
    hooks_dir = ROOT / hooks_raw.strip()
    return all(
        (hooks_dir / h).exists() and HOOK_MARK in (hooks_dir / h).read_text(encoding="utf-8")
        for h in GIT_HOOKS
    )


def cmd_doctor() -> int:
    checks = [
        (available(), f"CodeGraph 工具：{cli() or '未安装'}"),
        (initialised(), f"索引 .codegraph/：{'存在' if initialised() else '不存在'}"),
    ]
    hooks = git_hooks_installed()
    if hooks is not None:
        checks.append((hooks, "git hooks（pull / checkout / rebase 后自动同步）"))
    for good, msg in checks:
        print(f"{'✅' if good else '❌'} {msg}")
    if not available():
        print("\n" + SETUP_HINT)
    return 0 if all(g for g, _ in checks) else 1


# --------------------------------------------------------------- query
def cmd_query(cmd: str, args: List[str]) -> int:
    if not available():
        print(SETUP_HINT)
        return 2
    if not initialised():
        print("代码图谱未初始化：先运行 python3 .harness/scripts/codegraph.py init（检索可暂时退回 grep）")
        return 2
    if cmd == "affected" and not [a for a in args if not a.startswith("-")]:
        cfg = load_config()
        task = load()
        files = [f for f in changed_files(task.get("base", {}), fingerprint()) if is_code_file(f, cfg)] if task else []
        if not files:
            print("[codegraph] 本次会话没有代码改动。")
            return 0
        args = [*files, *args]
    freshness = sync()
    if freshness != "已同步":
        print(f"[codegraph] {freshness}；本次不返回可能过期的图谱，请暂用源码检索。")
        return 2
    return run([cmd, *args])[0]


def main(argv: List[str]) -> int:
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    cmd, rest = argv[0], argv[1:]
    if "--task" in rest:
        index = rest.index("--task")
        rest = rest[:index] + rest[index + 2:]
    if cmd in QUERY_CMDS:
        return cmd_query(cmd, rest)
    if cmd == "status":
        return run(["status", str(ROOT)])[0] if available() else (print(SETUP_HINT) or 2)
    if cmd == "sync":
        status = sync(quiet=False)
        print(f"[codegraph] {status}")
        return 0 if status == "已同步" else 1
    handlers = {
        "setup": cmd_setup, "init": cmd_init,
        "install-git-hooks": cmd_install_git_hooks, "doctor": cmd_doctor,
    }
    if cmd not in handlers:
        print(f"未知命令：{cmd}\n{__doc__}")
        return 2
    return handlers[cmd]()


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except StateError as exc:
        print(f"[codegraph] {exc}", file=sys.stderr)
        sys.exit(2)
