"""Shared helpers for harness scripts. Stdlib only, Python 3.8+."""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

IGNORED_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv", "env",
    "dist", "build", "target", ".next", ".nuxt", "out", "coverage", ".idea", ".vscode",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox", ".gradle", "vendor", ".harness",
    ".claude", ".codex", ".agents", ".codegraph", ".DS_Store",
}

# Harness-owned files, including the code graph index and its config: never
# "code changes" for the Stop hook or task records.
HARNESS_PATHS = ("AGENTS.md", "CLAUDE.md", ".harness/", ".claude/", ".codex/", ".agents/", "docs/",
                 ".codegraph/", "codegraph.json")

CONTAINER_DIRS = {"src", "lib", "app", "apps", "packages", "services", "modules", "internal", "pkg", "cmd"}

DEFAULT_CONFIG: Dict[str, Any] = {
    "name": "",
    "verify": [],
    "gates": {"verify": True, "learn": True},
    "evolve_every_n_tasks": 10,
    "slow_step_seconds": 300,
    "agents_md_max_lines": 200,
    "stale_after_days": 120,
    "code_globs_ignore": ["docs/**", "*.md", ".harness/**", ".claude/**", ".codex/**", ".agents/**"],
}


def find_harness_root(start: Optional[Path] = None) -> Optional[Path]:
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env and (Path(env) / ".harness").is_dir():
        return Path(env).resolve()
    here = (start or Path.cwd()).resolve()
    for p in [here, *here.parents]:
        if (p / ".harness").is_dir():
            return p
    return None


def project_root(start: Optional[Path] = None) -> Path:
    found = find_harness_root(start)
    if found is not None:
        return found
    return Path(__file__).resolve().parents[2]


ROOT = project_root()
HARNESS = ROOT / ".harness"
STATE_DIR = HARNESS / ".state"
TASKS_FILE = HARNESS / "evolution" / "tasks.jsonl"
KNOWLEDGE = ROOT / "docs" / "knowledge"


def load_config() -> Dict[str, Any]:
    cfg = dict(DEFAULT_CONFIG)
    path = HARNESS / "project.json"
    if path.exists():
        try:
            cfg.update(json.loads(path.read_text(encoding="utf-8")))
        except json.JSONDecodeError as e:
            raise SystemExit(f"[harness] .harness/project.json is not valid JSON: {e}")
    return cfg


# -------------------------------------------------------------------- git
def git(*args: str) -> Optional[str]:
    try:
        out = subprocess.run(
            ["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout if out.returncode == 0 else None


def is_git_repo() -> bool:
    return git("rev-parse", "--is-inside-work-tree") is not None


def git_dirty_files() -> List[str]:
    """Paths changed in the working tree or index (including untracked)."""
    out = git("status", "--porcelain", "-z", "--untracked-files=all")
    if not out:
        return []
    files: List[str] = []
    entries = out.split("\0")
    i = 0
    while i < len(entries):
        entry = entries[i]
        if len(entry) > 3:
            status, path = entry[:2], entry[3:]
            files.append(path)
            if "R" in status or "C" in status:
                i += 1  # skip rename source
        i += 1
    return files


def list_repo_files() -> List[str]:
    out = git("ls-files", "-co", "--exclude-standard")
    if out is not None:
        files = [f for f in out.splitlines() if f]
    else:
        files = []
        for dirpath, dirnames, filenames in os.walk(ROOT):
            dirnames[:] = [d for d in dirnames if d not in IGNORED_DIRS and not d.startswith(".")]
            for name in filenames:
                files.append(os.path.relpath(os.path.join(dirpath, name), ROOT))
    return [f for f in files if not _in_ignored_dir(f) and Path(ROOT, f).is_file()]


def _in_ignored_dir(rel: str) -> bool:
    return any(part in IGNORED_DIRS for part in Path(rel).parts[:-1])


# ------------------------------------------------------- classification
def is_harness_file(rel: str) -> bool:
    rel = rel.replace(os.sep, "/")
    return any(rel == p or rel.startswith(p) for p in HARNESS_PATHS)


def is_code_file(rel: str, cfg: Optional[Dict[str, Any]] = None) -> bool:
    rel = rel.replace(os.sep, "/")
    if is_harness_file(rel):
        return False
    globs = (cfg or load_config()).get("code_globs_ignore", [])
    return not any(fnmatch.fnmatch(rel, g) for g in globs)


def infer_modules(files: Iterable[str], cfg: Optional[Dict[str, Any]] = None) -> List[str]:
    cfg = cfg or load_config()
    mods = set()
    for rel in files:
        if not is_code_file(rel, cfg):
            continue
        parts = Path(rel).parts
        if len(parts) <= 1:
            mods.add(Path(rel).stem)
        elif parts[0] in CONTAINER_DIRS and len(parts) > 2:
            mods.add(f"{parts[0]}/{parts[1]}")
        else:
            mods.add(parts[0])
    return sorted(m for m in mods if m)


def file_hash(rel: str) -> Optional[str]:
    """Git-compatible blob SHA-1 so clean files match `git rev-parse HEAD:<path>`."""
    try:
        data = Path(ROOT, rel).read_bytes()
    except OSError:
        return None
    header = f"blob {len(data)}\0".encode("utf-8")
    return hashlib.sha1(header + data).hexdigest()


def rel_path(path: str, base: Optional[str] = None) -> str:
    anchor = Path(base).resolve() if base else ROOT
    p = (anchor / path).resolve() if not Path(path).is_absolute() else Path(path).resolve()
    try:
        return p.relative_to(ROOT).as_posix()
    except ValueError:
        return str(p)


def hook_transcript_path(hook: Dict[str, Any]) -> Optional[str]:
    raw = hook.get("transcript_path") or hook.get("transcriptPath")
    return raw if isinstance(raw, str) and raw else None


def is_antigravity_hook(hook: Dict[str, Any]) -> bool:
    return any(k in hook for k in ("conversationId", "toolCall", "terminationReason", "workspacePaths"))


def emit(hook: Dict[str, Any], antigravity: Dict[str, Any], claude: Optional[Dict[str, Any]] = None) -> None:
    """Print a hook response in the host's format (Antigravity JSON vs Claude/Codex JSON)."""
    if is_antigravity_hook(hook):
        print(json.dumps(antigravity, ensure_ascii=False))
    elif claude is not None:
        print(json.dumps(claude, ensure_ascii=False))


def now() -> float:
    return time.time()


def today() -> str:
    return time.strftime("%Y-%m-%d")


def iter_md(paths: Iterable[Path]) -> Iterable[Path]:
    for base in paths:
        if base.is_file() and base.suffix == ".md":
            yield base
        elif base.is_dir():
            yield from sorted(base.rglob("*.md"))
