#!/usr/bin/env python3
"""Task lifecycle and single completion gate: define, check milestones, pause, or finish work.

Usage:
  task.py start [--tier S|M|L] --summary "..." --accept "behavior" [--accept ...]
  task.py add [--tier M|L] [--accept "behavior" ...]
  task.py check --covers "C1=<evidence>" [--covers ...]   # incremental milestone check-off (Tier L)
  task.py pause "reason"                                  # pause waiting for user/external input
  task.py finish --type <feature|bugfix|refactor|other> [--outcome <success|partial|failed>] \
                 [--friction "..."] [--learned "..."] [--modules "..."] [--covers "C1=<evidence>" ...]
  task.py status [--pretty]
  task.py stats
  task.py mark-evolved
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List

sys.path.insert(0, str(Path(__file__).parent))
from _common import infer_modules, load_config, now, today
from _task import (
    CLOSED, TIER_ORDER, StateError, append_task_record, assess, changed_files,
    count_transcript_tool_calls, edit, ensure, fingerprint, gate_active, load,
    load_tasks, new_task, print_stats, ratchet_tier, since_last_evolve, verified,
)
import kb_check
import verify


def parse_covers(covers_list: List[str]) -> Dict[str, str]:
    out = {}
    for item in covers_list or []:
        if "=" in item:
            cid, _, ev = item.partition("=")
            out[cid.strip()] = ev.strip() or "verified"
        elif item.strip():
            out[item.strip()] = "verified"
    return out


def cmd_start(args: argparse.Namespace) -> int:
    if not args.summary.strip() or any(not c.strip() for c in args.accept):
        raise StateError("任务和验收标准不能为空")
    fp = fingerprint()
    existing = load()
    if existing and existing.get("summary") and (existing.get("closed") or {}).get("status") not in CLOSED:
        if not args.new:
            raise StateError("当前已有未完成任务；新任务请传 --new，或用 add 补充验收项/升档")
    ensured = ensure()
    prev_transcript = (ensured.get("transcript") or {}).get("path")
    had_prior_summary = bool(ensured.get("summary"))
    with edit() as t:
        fresh = new_task(fp, session_id=t.get("session_id", "manual"), tier=args.tier, transcript_path=prev_transcript)
        if prev_transcript and not had_prior_summary and isinstance(t.get("transcript"), dict):
            fresh["transcript"] = dict(t["transcript"])  # include turns since SessionStart
        t.clear()
        t.update(fresh)
        t["summary"] = args.summary.strip()
        t["criteria"] = [{"id": f"C{i + 1}", "text": c.strip(), "status": "unverified", "evidence": None}
                         for i, c in enumerate(args.accept)]
        t["status"] = "active"
    out = {"task_id": t["id"], "tier": t["tier"], "summary": t["summary"], "status": t["status"]}
    print(json.dumps(t if args.pretty else out, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0


def cmd_add(args: argparse.Namespace) -> int:
    accepts = args.accept or []
    if not accepts and not args.tier:
        raise StateError("需提供 --accept 补充验收标准，或提供 --tier 升级任务档位")
    if any(not c.strip() for c in accepts):
        raise StateError("验收标准不能为空")
    with edit() as t:
        if not t.get("summary"):
            raise StateError("尚未启动任务；先运行 task.py start")
        old_tier = t.get("tier", "M")
        new_tier = ratchet_tier(old_tier, args.tier)
        t["tier"] = new_tier
        count = len(t["criteria"])
        for i, c in enumerate(accepts):
            t["criteria"].append({"id": f"C{count + i + 1}", "text": c.strip(), "status": "unverified", "evidence": None})
        if accepts:
            t["closed"] = None
            t["status"] = "active"
    if args.tier and new_tier != old_tier:
        print(f"[task] 档位已单向升级：{old_tier} → {new_tier}")
    if accepts:
        print(f"[task] 已补充 {len(accepts)} 条验收项：")
        for i, c in enumerate(accepts):
            print(f"  - [C{count + i + 1}] {c.strip()}")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    covers = parse_covers(args.covers)
    if not covers:
        raise StateError("请用 --covers \"C1=<证据>\" 指定要打卡的里程碑条目")
    with edit() as t:
        if not t.get("summary"):
            raise StateError("尚未启动任务；先运行 task.py start")
        known = {c["id"]: c for c in t["criteria"]}
        unknown = sorted(set(covers) - set(known))
        if unknown:
            raise StateError(f"未知验收条目：{', '.join(unknown)}")
        for cid, ev in covers.items():
            known[cid]["status"] = "passed"
            known[cid]["evidence"] = ev
        done = sum(1 for c in t["criteria"] if c.get("status") == "passed")
        total = len(t["criteria"])
    print(f"[task] 已打卡 {len(covers)} 项里程碑（当前进度 {done}/{total}；全部完成后仍需运行 task.py finish 最终闭环）。")
    return 0


def cmd_pause(args: argparse.Namespace) -> int:
    reason = args.pending_reason or args.reason
    if not reason or not reason.strip():
        raise StateError("暂停任务必须说明原因")
    fp = fingerprint()
    with edit() as t:
        if not t.get("summary"):
            raise StateError("尚未启动任务")
        t["closed"] = {"status": "pending", "reason": reason.strip(), "fp": fp["id"], "at": now()}
        t["status"] = "pending"
    print(f"[task] 任务已暂停（Stop hook 本轮将放行）：{reason.strip()}")
    return 0


def cmd_finish(args: argparse.Namespace) -> int:
    cfg = load_config()
    task = load()
    if not task or not task.get("summary"):
        raise StateError("没有活动任务可收尾；先运行 task.py start")

    fp = fingerprint()
    is_success = args.outcome == "success"

    if is_success:
        if gate_active(task, "verify", cfg) and not verified(task, fp):
            print("[task finish] 正在执行全量校验...")
            code = verify.verify_steps("full")
            if code != 0:
                print(f"[task finish] 校验失败（exit {code}），任务未能完成。", file=sys.stderr)
                return 1
            task = load() or task
            fp = fingerprint()

        kb_errors, _, _ = kb_check.collect(cfg)
        if kb_errors:
            print("[task finish] 知识库体检未通过，发现致命错误：", file=sys.stderr)
            for err in kb_errors:
                print(f"  ERROR {err}", file=sys.stderr)
            return 1
    else:
        if (not args.friction or args.friction.strip().lower() == "none") and not task.get("skips"):
            raise StateError(f"标记为 {args.outcome} 时必须用 --friction 说明未完成或失败的具体原因")

    covers = parse_covers(args.covers)
    with edit() as t:
        if args.summary:
            t["summary"] = args.summary.strip()
        verify_satisfied = (not gate_active(t, "verify", cfg)) or verified(t, fp)
        all_passed = True
        for c in t["criteria"]:
            if c["id"] in covers:
                c["status"] = "passed"
                c["evidence"] = covers[c["id"]]
            elif not covers and is_success and verify_satisfied:
                c["status"] = "passed"
                c["evidence"] = c.get("evidence") or ("verified" if verified(t, fp) else "gate_skipped")
            elif c.get("status") != "passed":
                all_passed = False

        if is_success and not all_passed:
            raise StateError("存在未满足的验收标准，不能标记为 success；未满足请指定 --outcome partial --friction ... 或补齐 --covers")

        changed = changed_files(t.get("base") or {}, fp)
        auto_mods = infer_modules(changed, cfg)
        manual_mods = [s.strip() for s in (args.modules or "").split(",") if s.strip() and s.strip().lower() != "none"]
        modules = sorted(set(auto_mods) | set(manual_mods))

        entry = {
            "task_id": t["id"],
            "tier": t.get("tier", "M"),
            "summary": t["summary"],
            "type": args.type,
            "outcome": args.outcome,
            "date": today(),
            "ts": now(),
            "friction": args.friction,
            "learned": [s.strip() for s in (args.learned or "").split(",") if s.strip() and s.strip().lower() != "none"],
            "modules": modules,
            "criteria": t["criteria"],
            "verification": t.get("verify"),
            "gates_skipped": dict(t.get("skips") or {}),
            "metrics": {
                "tool_calls": count_transcript_tool_calls(t.get("transcript")),
                "stop_blocks": int(t.get("blocks") or 0),
                "verify_runs": int(t.get("verify_runs") or 0),
                "input_tokens": None,
                "human_interventions": None,
                "elapsed_seconds": max(0, int(now() - t.get("started_at", now()))),
            },
        }
        append_task_record(entry)

        status = "completed" if is_success else args.outcome
        t["closed"] = {"status": status, "outcome": args.outcome, "fp": fp["id"], "at": now()}
        t["status"] = status
        t["blocks"] = 0

    pending = len(since_last_evolve(load_tasks()))
    threshold = cfg.get("evolve_every_n_tasks", 10)
    print(f"[task finish] 任务已标记为 {status}，并写入 evolution/tasks.jsonl（自上次进化累计 {pending} 任务）。")
    if pending >= threshold:
        print(f"[task finish] ⚡ 达到进化阈值（{threshold}），建议执行 /evolve。")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    task = ensure()
    report = assess(task)
    print(json.dumps(report, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    subs = ap.add_subparsers(dest="action", required=True)

    p_start = subs.add_parser("start")
    p_start.add_argument("--tier", choices=tuple(TIER_ORDER), default="M")
    p_start.add_argument("--summary", required=True)
    p_start.add_argument("--accept", action="append", required=True)
    p_start.add_argument("--task", help="task id (optional compatibility arg)")
    p_start.add_argument("--new", action="store_true")
    p_start.add_argument("--pretty", action="store_true")

    p_add = subs.add_parser("add")
    p_add.add_argument("--tier", choices=tuple(TIER_ORDER))
    p_add.add_argument("--accept", action="append")

    p_check = subs.add_parser("check")
    p_check.add_argument("--covers", action="append", required=True)

    p_pause = subs.add_parser("pause")
    p_pause.add_argument("reason", nargs="?", default="等待外部输入")
    p_pause.add_argument("--pending", dest="pending_reason")

    p_finish = subs.add_parser("finish")
    p_finish.add_argument("--summary", help="override task summary")
    p_finish.add_argument("--type", choices=("feature", "bugfix", "refactor", "other"), default="feature")
    p_finish.add_argument("--outcome", choices=("success", "partial", "failed"), default="success")
    p_finish.add_argument("--friction", default="none")
    p_finish.add_argument("--learned", default="none")
    p_finish.add_argument("--modules", default="")
    p_finish.add_argument("--covers", action="append", help="C1=evidence, C2=evidence...")

    p_status = subs.add_parser("status")
    p_status.add_argument("--pretty", action="store_true")

    subs.add_parser("stats")
    subs.add_parser("mark-evolved")

    args = ap.parse_args()
    try:
        if args.action == "start":
            return cmd_start(args)
        if args.action == "add":
            return cmd_add(args)
        if args.action == "check":
            return cmd_check(args)
        if args.action == "pause":
            return cmd_pause(args)
        if args.action == "finish":
            return cmd_finish(args)
        if args.action == "status":
            return cmd_status(args)
        if args.action == "stats":
            return print_stats()
        if args.action == "mark-evolved":
            append_task_record({"type": "evolve", "date": today(), "ts": now()})
            print("[task] 已标记进化检查点。")
            return 0
        return 0
    except StateError as exc:
        print(f"[task] {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
