"""The whole task state: one JSON file per checkout, fingerprinted with git.

Design:
  * One active task per checkout. Parallel independent work uses `git worktree`
    (each worktree has its own .harness/.state), not custom file locks.
  * "Has the code changed since X?" is answered by a fingerprint built from git:
    HEAD + Git blob SHA-1s of dirty/untracked files (plus harness control files,
    so editing verify.py / project.json after verification invalidates it).
  * Completion = `task.py finish` passed on the current fingerprint. Nothing else.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import uuid
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

from _common import (
    ROOT, STATE_DIR, TASKS_FILE, file_hash, git, is_code_file, list_repo_files,
    load_config, now, today,
)

TASK_FILE = STATE_DIR / "task.json"
# Never writable through agent edit tools (PreToolUse guard); also bound into the fingerprint.
PROTECTED = (".harness/scripts", ".harness/.state", ".claude/settings.json", ".codex/hooks.json",
             ".agents/hooks.json")
CONTROL_FILES = (".harness/project.json",)
CONTROL_DIRS = (".harness/scripts",)
CLOSED = ("completed", "partial", "failed")
TIER_ORDER = {"S": 0, "M": 1, "L": 2}
MAX_TRANSCRIPT_SCAN_BYTES = 4 * 1024 * 1024


class StateError(ValueError):
    pass


# ------------------------------------------------------------------ storage
def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


@contextmanager
def _lock():
    """Short exclusive lock around read-modify-write of task.json (hooks may race)."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with (STATE_DIR / "task.lock").open("a+b") as stream:
        if os.name == "nt":
            import msvcrt
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                import msvcrt
                try:
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_UN)


def load() -> Optional[Dict[str, Any]]:
    try:
        value = json.loads(TASK_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise StateError(f"任务状态无法读取，不能据此判定完成：{TASK_FILE}: {exc}")
    if not isinstance(value, dict) or not value.get("id"):
        raise StateError(f"任务状态损坏：{TASK_FILE}")
    return value


@contextmanager
def edit():
    with _lock():
        task = load()
        if task is None:
            raise StateError("还没有任务；先运行 task.py start --summary ... --accept ...")
        yield task
        task["updated_at"] = now()
        atomic_json(TASK_FILE, task)


def _transcript_size(path: Optional[str]) -> int:
    if not path:
        return 0
    try:
        return Path(path).stat().st_size
    except OSError:
        return 0


def bind_transcript(task: Dict[str, Any], path: Optional[str], reset_offset: bool = False) -> None:
    if not path:
        return
    existing = task.get("transcript")
    if reset_offset or not isinstance(existing, dict) or existing.get("path") != path:
        offset = _transcript_size(path) if reset_offset else 0
        task["transcript"] = {"path": path, "offset": offset}


def new_task(
    fp: Optional[Dict[str, Any]] = None,
    session_id: str = "manual",
    tier: str = "M",
    transcript_path: Optional[str] = None,
) -> Dict[str, Any]:
    tid = uuid.uuid4().hex[:12]
    base = fp or fingerprint()
    task: Dict[str, Any] = {
        "id": tid,
        "session_id": session_id,
        "tier": tier if tier in TIER_ORDER else "M",
        "status": "idle",
        "summary": "",
        "criteria": [],
        "started_at": now(),
        "base": base,
        "verify": None,
        "verify_runs": 0,
        "closed": None,
        "skips": {},
        "blocks": 0,
        "transcript": None,
    }
    bind_transcript(task, transcript_path, reset_offset=True)
    return task


def ensure(session_id: str = "manual", transcript_path: Optional[str] = None) -> Dict[str, Any]:
    """SessionStart: resume an open task, otherwise start a fresh idle one at the current tree."""
    with _lock():
        task = load()
        if task and (task.get("closed") or {}).get("status") not in CLOSED:
            task["session_id"] = session_id
            bind_transcript(task, transcript_path, reset_offset=not bool(task.get("summary")))
            atomic_json(TASK_FILE, task)
            return task  # unfinished work (active / pending) carries over
        task = new_task(session_id=session_id, transcript_path=transcript_path)
        atomic_json(TASK_FILE, task)
        return task


def ratchet_tier(current: str, requested: Optional[str]) -> str:
    cur = current if current in TIER_ORDER else "M"
    if not requested:
        return cur
    if requested not in TIER_ORDER:
        raise StateError(f"无效的任务档位：{requested}（可选 S / M / L）")
    if TIER_ORDER[requested] < TIER_ORDER[cur]:
        raise StateError(f"单向棘轮：执行中途只能升档（{cur} → M/L），禁止降档为 {requested}")
    return requested


# -------------------------------------------------------------- fingerprint
def _tracked(rel: str, cfg: Dict[str, Any]) -> bool:
    return rel in CONTROL_FILES or rel.startswith(tuple(d + "/" for d in CONTROL_DIRS)) or is_code_file(rel, cfg)


def _secret(rel: str) -> bool:
    parts = Path(rel).parts
    return any(p.startswith(".env") or p in ("__pycache__", ".pytest_cache") for p in parts) or rel.endswith((".pem", ".key", ".pyc"))


def fingerprint() -> Dict[str, Any]:
    """{"id", "head", "files"}: files = Git blob SHA-1s of tracked paths that differ
    from HEAD (or every tracked file outside git), plus harness control files always."""
    cfg = load_config()
    head = (git("rev-parse", "HEAD") or "").strip()
    status = git("status", "--porcelain", "-z", "--untracked-files=all")
    if status is None:
        paths = list_repo_files()
    else:
        paths, entries, i = [], status.split("\0"), 0
        while i < len(entries):
            entry = entries[i]
            if len(entry) > 3:
                paths.append(entry[3:])
                if "R" in entry[:2] or "C" in entry[:2]:
                    i += 1
            i += 1
    for directory in CONTROL_DIRS:
        root_dir = ROOT / directory
        if root_dir.exists():
            paths += [p.relative_to(ROOT).as_posix() for p in root_dir.rglob("*")
                      if p.is_file() and "__pycache__" not in p.parts]
    paths += list(CONTROL_FILES)
    files = {}
    for rel in sorted(set(paths)):
        if rel and not _secret(rel) and _tracked(rel, cfg):
            files[rel] = file_hash(rel) or "deleted"
    digest = hashlib.sha256(json.dumps({"head": head, "files": files}, sort_keys=True).encode()).hexdigest()
    return {"id": digest[:16], "head": head, "files": files}


def _blob_at(snap: Dict[str, Any], rel: str, use_disk_if_clean: bool = False) -> str:
    recorded = (snap.get("files") or {}).get(rel)
    if recorded is not None:
        return recorded
    if use_disk_if_clean:
        return file_hash(rel) or "deleted"
    head = snap.get("head") or ""
    if head:
        oid = (git("rev-parse", "--verify", "-q", f"{head}:{rel}") or "").strip()
        return oid or "deleted"
    return "deleted"


def changed_files(base: Dict[str, Any], current: Dict[str, Any]) -> List[str]:
    """Project files whose content differs between two fingerprints (control files excluded)."""
    names = set(base.get("files", {})) | set(current.get("files", {}))
    if base.get("head") and current.get("head") and base["head"] != current["head"]:
        names |= set((git("diff", "--name-only", base["head"], current["head"]) or "").split())
    cfg = load_config()
    out = []
    for rel in names:
        if rel in CONTROL_FILES or rel.startswith(tuple(d + "/" for d in CONTROL_DIRS)) or not is_code_file(rel, cfg):
            continue
        old = _blob_at(base, rel, use_disk_if_clean=False)
        new = _blob_at(current, rel, use_disk_if_clean=True)
        if old != new:
            out.append(rel)
    return sorted(out)


# ------------------------------------------------------------------ verdict
def gate_active(task: Dict[str, Any], name: str, cfg: Optional[Dict[str, Any]] = None) -> bool:
    cfg = cfg or load_config()
    return bool((cfg.get("gates") or {}).get(name, True)) and name not in (task.get("skips") or {})


def verified(task: Dict[str, Any], fp: Dict[str, Any]) -> bool:
    v = task.get("verify") or {}
    if not (v.get("ok") and v.get("fp") == fp["id"]):
        return False
    logs = v.get("logs")
    if logs and not (ROOT / logs).exists():
        return False
    return True


def assess(task: Dict[str, Any], fp: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    fp = fp or fingerprint()
    cfg = load_config()
    changes = changed_files(task.get("base") or {}, fp)
    closed = task.get("closed") or {}
    is_verified = verified(task, fp)
    reasons: List[str] = []
    closed_matches = bool(closed and closed.get("fp") == fp["id"])
    if closed_matches:
        if closed.get("status") == "completed" and gate_active(task, "verify", cfg) and not is_verified:
            status = "active"
            reasons.append("校验凭据失效或日志丢失，需重新运行 verify.py")
        else:
            status = closed["status"]
    elif not task.get("summary"):
        status = "active" if changes else "idle"
        if changes:
            reasons.append("代码有改动但还没定义任务：task.py start --tier S|M|L --summary ... --accept ...，完成后 task.py finish")
    else:
        status = "active"
        if not gate_active(task, "verify", cfg):
            is_verified = True
        if not gate_active(task, "learn", cfg) and is_verified:
            status = "completed"  # learn gate off: a fresh verification is the whole bar
        else:
            if closed.get("status") == "pending":
                reasons.append("等待期间代码发生变动，需重新校验并 finish")
            elif closed:
                reasons.append(f"上次 finish（{closed['status']}）之后代码又变了")
            reasons.append("运行 task.py finish --covers \"C1=<证据>\" ... 完成校验与记录"
                           "（等待外部输入用 task.py pause \"<原因>\"；无法完成用 --outcome partial/failed --friction \"<原因>\"）")
    completed = status == "completed"
    allow = status in ("idle", "completed", "pending", "partial", "failed")
    return {
        "task_id": task["id"],
        "tier": task.get("tier", "M"),
        "summary": task.get("summary"),
        "status": status,
        "completed": completed,
        "allow_turn_end": allow,
        "verified": is_verified,
        "changed_files": changes,
        "criteria": task.get("criteria", []),
        "reasons": reasons,
        "fingerprint": fp["id"],
    }


# ------------------------------------------------ evolution & telemetry log
def count_transcript_tool_calls(transcript: Optional[Dict[str, Any]]) -> Optional[int]:
    if not isinstance(transcript, dict) or not transcript.get("path"):
        return None
    path = Path(transcript["path"])
    if not path.is_file():
        return None
    try:
        size = path.stat().st_size
        offset = max(0, min(int(transcript.get("offset", 0)), size))
        skip_partial = False
        if size - offset > MAX_TRANSCRIPT_SCAN_BYTES:
            offset = size - MAX_TRANSCRIPT_SCAN_BYTES
            skip_partial = True
        with path.open("rb") as stream:
            stream.seek(offset)
            raw = stream.read()
    except (OSError, ValueError):
        return None
    lines = raw.splitlines()
    if skip_partial and lines:
        lines = lines[1:]
    calls = 0
    for b_line in lines:
        line = b_line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line.decode("utf-8", errors="ignore"))
        except ValueError:
            continue
        if not isinstance(obj, dict):
            continue
        # Antigravity transcript.jsonl
        if isinstance(obj.get("tool_calls"), list):
            calls += len(obj["tool_calls"])
            continue
        # Claude Code transcript.jsonl
        msg = obj.get("message")
        if isinstance(msg, dict) and isinstance(msg.get("content"), list):
            calls += sum(1 for item in msg["content"] if isinstance(item, dict) and item.get("type") == "tool_use")
            continue
        # OpenAI Codex rollout JSONL
        ev_type = obj.get("type") or (obj.get("payload") or {}).get("type") if isinstance(obj.get("payload"), dict) else obj.get("type")
        if ev_type in ("function_call", "custom_tool_call", "local_shell_call"):
            calls += 1
    return calls


def load_tasks() -> List[Dict[str, Any]]:
    if not TASKS_FILE.exists():
        return []
    out = []
    for line in TASKS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def since_last_evolve(tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    idx = max((i for i, t in enumerate(tasks) if t.get("type") == "evolve"), default=-1)
    return tasks[idx + 1:]


def append_task_record(entry: Dict[str, Any]) -> None:
    TASKS_FILE.parent.mkdir(parents=True, exist_ok=True)
    previous = load_tasks()
    if previous and entry.get("task_id") and previous[-1].get("task_id") == entry["task_id"] and previous[-1].get("outcome") == entry.get("outcome"):
        last = previous[-1]
        same = all(last.get(k) == entry.get(k) for k in ("summary", "type", "friction", "learned", "modules", "criteria"))
        if same:
            return  # byte-identical idempotence
        raw_lines = [ln for ln in TASKS_FILE.read_text(encoding="utf-8").splitlines() if ln.strip()]
        raw_lines[-1] = json.dumps(entry, ensure_ascii=False)
        fd, name = tempfile.mkstemp(prefix="tasks.jsonl.", suffix=".tmp", dir=str(TASKS_FILE.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write("\n".join(raw_lines) + "\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(name, TASKS_FILE)
        finally:
            if os.path.exists(name):
                os.unlink(name)
        return
    with TASKS_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())


def print_stats() -> int:
    tasks = load_tasks()
    recent = since_last_evolve(tasks)
    evolves = [t for t in tasks if t.get("type") == "evolve"]
    print(f"任务总数：{len([t for t in tasks if t.get('type') != 'evolve'])} ｜ 进化次数：{len(evolves)}"
          f" ｜ 上次进化：{evolves[-1]['date'] if evolves else '从未'}")
    print(f"自上次进化以来：{len(recent)} 个任务")
    if not recent:
        return 0
    print("  类型：", dict(Counter(t.get("type") for t in recent)))
    print("  档位：", dict(Counter(t.get("tier", "M") for t in recent)))
    print("  结果：", dict(Counter(t.get("outcome") for t in recent)))
    mods = Counter(m for t in recent for m in t.get("modules", []))
    if mods:
        print("  高频模块：", ", ".join(f"{m}×{n}" for m, n in mods.most_common(8)))
    blocks = sum((t.get("metrics") or {}).get("stop_blocks") or 0 for t in recent)
    v_runs = [int((t.get("metrics") or {}).get("verify_runs") or 0) for t in recent]
    t_calls = [(t.get("metrics") or {}).get("tool_calls") for t in recent]
    valid_calls = [int(c) for c in t_calls if isinstance(c, int)]
    telemetry_parts = [f"Stop 拦截累计 {blocks} 次", f"平均校验 {round(sum(v_runs) / len(v_runs), 1)} 次/任务"]
    if valid_calls:
        telemetry_parts.append(f"平均工具步数 {round(sum(valid_calls) / len(valid_calls), 1)} 步/任务")
    print("  轨迹遥测：" + " ｜ ".join(telemetry_parts))
    frictions = [(t["date"], t["friction"]) for t in recent if t.get("friction") and t["friction"] != "none"]
    if frictions:
        print("  摩擦点：")
        for d, f in frictions:
            print(f"    - [{d}] {f}")
    skipped = [(t.get("date", today()), g, r) for t in recent for g, r in (t.get("gates_skipped") or {}).items()]
    if skipped:
        print("  跳过关卡：")
        for d, g, r in skipped:
            print(f"    - [{d}] {g}：{r}")
    return 0
