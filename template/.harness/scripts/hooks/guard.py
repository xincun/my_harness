#!/usr/bin/env python3
"""PreToolUse guard: protects critical harness control plane & environment secrets from modification."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import List

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _boot import bootstrap  # noqa: E402


def main() -> int:
    hook, active = bootstrap(allow_payload_for_agy={"decision": "allow"})
    if not active:
        return 0

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from _common import emit, rel_path
    from _task import PROTECTED

    patch_rx = re.compile(r"^\*\*\*\s+(?:Update|Add|Delete)\s+File:\s+(.+?)\s*$|^\*\*\*\s+Move to:\s+(.+?)\s*$", re.M)

    def extract_patch_paths(text: str) -> List[str]:
        return [(m.group(1) or m.group(2)).strip() for m in patch_rx.finditer(text) if (m.group(1) or m.group(2))]

    tool_call = hook.get("toolCall") if isinstance(hook.get("toolCall"), dict) else {}
    name = hook.get("tool_name") or tool_call.get("name") or ""
    args = hook.get("tool_input") if hook.get("tool_input") is not None else tool_call.get("args", {})
    base = hook.get("cwd") if isinstance(hook.get("cwd"), str) and hook.get("cwd") else None

    raw_paths: List[str] = []
    if isinstance(args, str):
        raw_paths.extend(extract_patch_paths(args))
    elif isinstance(args, dict):
        p = args.get("file_path") or args.get("Path") or args.get("TargetFile") or args.get("notebook_path")
        if isinstance(p, str) and p:
            raw_paths.append(p)
        if name == "apply_patch" or "patch" in args or "input" in args:
            for key in ("input", "patch", "cmd", "command"):
                val = args.get(key)
                if isinstance(val, str) and "*** " in val:
                    raw_paths.extend(extract_patch_paths(val))

    for raw in raw_paths:
        p = rel_path(raw, base=base)
        if Path(p).is_absolute():
            continue
        if any(p == cp or p.startswith(cp + "/") for cp in PROTECTED):
            msg = f"[harness guard] 禁止修改控制平面核心文件：{p}"
            emit(
                hook,
                {"decision": "deny", "reason": msg},
                {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": msg}},
            )
            return 0
        if any(part.startswith(".env") for part in Path(p).parts) or p.endswith((".pem", ".key")):
            msg = f"[harness guard] 禁止直接读写机密配置文件：{p}"
            emit(
                hook,
                {"decision": "deny", "reason": msg},
                {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": msg}},
            )
            return 0

    emit(hook, {"decision": "allow"}, None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
