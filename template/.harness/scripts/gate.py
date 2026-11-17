#!/usr/bin/env python3
"""Turn completion gates on or off (verify, learn).

Usage:
  gate.py status
  gate.py off verify --reason "..."
  gate.py on verify
  gate.py skip verify --reason "..."
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict

sys.path.insert(0, str(Path(__file__).parent))
from _common import HARNESS, load_config, today
from _task import StateError, edit, ensure, load

GATES = {
    "verify": "校验关卡（改动代码必须通过 verify.py）",
    "learn": "沉淀关卡（改动代码必须执行 finish 记录任务）",
}
CONFIG_PATH = HARNESS / "project.json"


def status() -> int:
    cfg = load_config()
    task = load() or {}
    skips = task.get("skips", {})
    for name, desc in GATES.items():
        enabled = bool((cfg.get("gates") or {}).get(name, True))
        if not enabled:
            reason = next((h.get("reason", "") for h in reversed(cfg.get("gate_history", []))
                           if h.get("gate") == name and h.get("action") == "off"), "")
            mark = f"⛔ 已关闭（项目级）{'：' + reason if reason else ''}"
        elif name in skips:
            mark = f"⏭️  本次跳过：{skips[name]}"
        else:
            mark = "✅ 开启"
        print(f"{name:7} {mark}  — {desc}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("action", choices=["status", "on", "off", "skip"])
    ap.add_argument("gate", nargs="?", choices=list(GATES))
    ap.add_argument("--reason", default="")
    args = ap.parse_args()

    if args.action == "status":
        return status()
    if not args.gate:
        ap.error("需要指定关卡：verify 或 learn")
    if args.action in ("off", "skip") and not args.reason.strip():
        ap.error("关闭或跳过关卡必须用 --reason 说明原因")

    if args.action == "skip":
        ensure()
        with edit() as task:
            task.setdefault("skips", {})[args.gate] = args.reason
        print(f"[gate] 本次任务跳过 {args.gate}：{args.reason}")
        return 0

    enabled = args.action == "on"
    raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8")) if CONFIG_PATH.exists() else {}
    raw.setdefault("gates", {})[args.gate] = enabled
    raw.setdefault("gate_history", []).append(
        {"date": today(), "gate": args.gate, "action": args.action, "reason": args.reason}
    )
    CONFIG_PATH.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if enabled:
        try:
            with edit() as task:
                task.get("skips", {}).pop(args.gate, None)
        except Exception:
            pass
    print(f"[gate] {args.gate} 已{'开启' if enabled else '关闭（项目级）'}。")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (StateError, OSError) as exc:
        print(f"[gate] {exc}", file=sys.stderr)
        sys.exit(2)
