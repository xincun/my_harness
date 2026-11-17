"""Lightweight pre-import bootstrap for lifecycle hooks (stdlib only).

Reads stdin JSON once, resolves the active workspace's `.harness` root (supporting both
repo-local hooks and global Plugin hooks running outside the workspace directory), and
exports `CLAUDE_PROJECT_DIR` BEFORE `_common` / `_task` / `codegraph` are imported.
"""

from __future__ import annotations

import json
import os
import select
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


def _read_stdin() -> Dict[str, Any]:
    try:
        if sys.stdin is None or sys.stdin.isatty():
            return {}
        ready, _, _ = select.select([sys.stdin], [], [], 0.05)
        if not ready:
            return {}
        data = sys.stdin.read()
        return json.loads(data) if data.strip() else {}
    except (json.JSONDecodeError, OSError, ValueError):
        return {}


def _find_harness(start: Path) -> Optional[Path]:
    cur = start.resolve()
    for p in [cur, *cur.parents]:
        if (p / ".harness").is_dir():
            return p
    return None


def bootstrap(allow_payload_for_agy: Optional[Dict[str, Any]] = None) -> Tuple[Dict[str, Any], bool]:
    """Returns (hook_payload, active). When active is False, prints no-op output and caller should exit 0."""
    hook = _read_stdin()
    is_agy = any(k in hook for k in ("conversationId", "toolCall", "terminationReason", "workspacePaths"))

    candidates = []
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env:
        candidates.append(Path(env))
    if isinstance(hook.get("cwd"), str) and hook["cwd"]:
        candidates.append(Path(hook["cwd"]))
    ws_list = hook.get("workspacePaths")
    if isinstance(ws_list, list):
        candidates.extend(Path(w) for w in ws_list if isinstance(w, str) and w)
    candidates.append(Path.cwd())

    for c in candidates:
        found = _find_harness(c)
        if found is not None:
            os.environ["CLAUDE_PROJECT_DIR"] = str(found)
            return hook, True

    # Manual CLI invocation fallback (no stdin payload)
    if not hook:
        fallback = _find_harness(Path(__file__).resolve().parents[2])
        if fallback is not None:
            os.environ["CLAUDE_PROJECT_DIR"] = str(fallback)
            return hook, True

    if is_agy and allow_payload_for_agy is not None:
        print(json.dumps(allow_payload_for_agy, ensure_ascii=False))
    return hook, False
