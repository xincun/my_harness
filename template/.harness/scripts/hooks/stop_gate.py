#!/usr/bin/env python3
"""Stop hook: prevents claiming victory or prematurely ending without verification.

Supports:
  - Antigravity lifecycle (continuationActive & ExecutorTerminationReason)
  - Claude Code / Codex Stop hooks (stop_hook_active)
  - Manual verification check: `stop_gate.py --check`
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _boot import bootstrap  # noqa: E402

MAX_BLOCKS = 3
AGY_TERMINAL_REASONS = {
    "MAX_INVOCATIONS",
    "MAX_FORCED_INVOCATIONS",
    "MAX_TOKEN_BUDGET_EXCEEDED",
    "ERROR",
    "USER_CANCELED",
    "TERMINAL_STEP_TYPE",
    "TERMINAL_CUSTOM_HOOK",
    "INJECTED_RESPONSE",
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--task", help="task id (optional compatibility arg)")
    args = ap.parse_args()

    if args.check:
        hook, active = {}, True
    else:
        hook, active = bootstrap(allow_payload_for_agy={"decision": "allow"})
    if not active:
        return 0

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from _common import emit, hook_transcript_path, is_antigravity_hook
    from _task import StateError, assess, bind_transcript, edit, ensure, load

    agy = is_antigravity_hook(hook)
    forced_stop = bool(hook.get("stop_hook_active") or hook.get("continuationActive")) or (
        agy and hook.get("terminationReason") in AGY_TERMINAL_REASONS
    )

    try:
        t_path = hook_transcript_path(hook)
        task = load() or ensure(transcript_path=t_path)
        if t_path and not (task.get("transcript") or {}).get("path"):
            with edit() as t:
                bind_transcript(t, t_path, reset_offset=False)
                task = dict(t)

        report = assess(task)
        if args.check:
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0 if report["completed"] else 1

        if report["allow_turn_end"]:
            try:
                import codegraph
                codegraph.sync(timeout=30)
            except Exception:
                pass
            emit(hook, {"decision": "allow"}, None)
            return 0

        blocks = (task.get("blocks") or 0) + 1
        with edit() as t:
            t["blocks"] = blocks
        if forced_stop or blocks > MAX_BLOCKS:
            emit(hook, {"decision": "allow"}, None)
            return 0

        verify_active = report.get("status") == "active" and (not report.get("verified"))
        hint_verify = "需运行 verify.py 校验；" if verify_active else ""
        reason = ("[harness] 任务尚未通过门禁：\n- " + "\n- ".join(report["reasons"])
                  + f"\n{hint_verify}完成请参照 learn.md 并运行 task.py finish；等待外部输入用 task.py pause \"<原因>\"。")
        decision = "continue" if agy else "block"
        emit(hook, {"decision": decision, "reason": reason}, {"decision": decision, "reason": reason})
        return 0
    except (StateError, OSError) as exc:
        if args.check:
            print(json.dumps({"completed": False, "status": "error", "reason": str(exc)}, ensure_ascii=False))
            return 2
        reason = f"[harness] 状态损坏，阻止结束（fail-closed）：{exc}"
        emit(hook, {"decision": "continue", "reason": reason}, {"decision": "block", "reason": reason})
        return 2


if __name__ == "__main__":
    sys.exit(main())
