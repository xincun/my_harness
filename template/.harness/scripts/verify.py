#!/usr/bin/env python3
"""One command to verify the project: the agent's definition of "it works".

Steps come from `.harness/project.json` -> "verify", normally produced by the
onboard workflow from evidence (CI config, build scripts, docs) and calibrated
by actually running them:
  [{"name": "build", "cmd": "cmake --build build", "source": "CI: build job"},
   {"name": "test", "cmd": "ctest --test-dir build", "timeout": 1800, "source": "..."},
   {"name": "asan", "cmd": "...", "optional": true}]
If none are configured, common stacks are auto-detected as a fallback.

Usage:
  verify.py               # run required steps (records verification for the current tree)
  verify.py --all         # include optional steps
  verify.py --smoke       # run runtime black-box smoke steps ("kind": "smoke")
  verify.py --only test   # run a single step (inner loop; never counts as verification)
  verify.py --affected    # run only tests affected by this task's changes (inner loop)
  verify.py --parallel    # run steps concurrently
  verify.py --detect      # print auto-detected steps as JSON
  verify.py --survey      # list evidence for how this project builds/tests (for onboard)
  verify.py --calibrate   # run candidate steps, time them, suggest optional/timeouts
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).parent))
from _common import ROOT, STATE_DIR, git_dirty_files, is_code_file, list_repo_files, load_config, now  # noqa: E402
from _task import StateError, TASK_FILE, atomic_json, changed_files, edit, fingerprint, load  # noqa: E402

TAIL_LINES = 60
CMD_WORDS = re.compile(
    r"\b(test|tests|lint|check|build|typecheck|ctest|cmake|pytest|unittest|tox|nox|npm|pnpm|yarn|bun|"
    r"make|ninja|xcodebuild|swift|cargo|go|gradle|gradlew|mvn|ruff|mypy|eslint|tsc|jest|vitest|clippy)\b"
)
CI_GLOBS = [".github/workflows/*.yml", ".github/workflows/*.yaml", ".gitlab-ci.yml", "Jenkinsfile",
            ".circleci/config.yml", "azure-pipelines.yml", "bitbucket-pipelines.yml", ".buildkite/*.yml",
            ".travis.yml", ".woodpecker.yml", ".drone.yml"]
BUILD_FILES = ["Makefile", "CMakeLists.txt", "CMakePresets.json", "Package.swift", "package.json",
               "pyproject.toml", "tox.ini", "noxfile.py", "setup.cfg", "go.mod", "Cargo.toml", "pom.xml",
               "build.gradle", "build.gradle.kts", "justfile", "Taskfile.yml", ".pre-commit-config.yaml"]
DOC_FILES = ["README.md", "README", "CONTRIBUTING.md", "AGENTS.md", "CLAUDE.md", "AGENTS.harness.md",
             "docs/DEVELOPMENT.md", "docs/development.md", "docs/BUILDING.md", "docs/TESTING.md"]


def detect() -> List[Dict[str, Any]]:
    steps: List[Dict[str, Any]] = []
    has = lambda name: (ROOT / name).exists()  # noqa: E731

    if has("package.json"):
        try:
            scripts = json.loads((ROOT / "package.json").read_text()).get("scripts", {})
        except (json.JSONDecodeError, OSError):
            scripts = {}
        pm = "pnpm" if has("pnpm-lock.yaml") else "yarn" if has("yarn.lock") else "bun" if has("bun.lockb") else "npm"
        for key in ("lint", "typecheck", "type-check", "check", "test"):
            if key in scripts:
                cmd = f"{pm} run {key}"
                if key == "test" and "watch" in scripts[key]:
                    cmd += " -- --watch=false"
                steps.append({"name": key, "cmd": cmd})

    repo_files = list_repo_files()
    py_tests = [f for f in repo_files if f.endswith(".py") and Path(f).name.startswith("test")]
    if has("pyproject.toml") or has("setup.py") or has("setup.cfg") or has("requirements.txt") or py_tests:
        py = "python3"
        text = (ROOT / "pyproject.toml").read_text() if has("pyproject.toml") else ""
        if shutil.which("ruff") and ("[tool.ruff" in text or has("ruff.toml")):
            steps.append({"name": "ruff", "cmd": "ruff check ."})
        if shutil.which("mypy") and ("[tool.mypy" in text or has("mypy.ini")):
            steps.append({"name": "mypy", "cmd": "mypy ."})
        if importlib.util.find_spec("pytest") is not None:
            steps.append({"name": "pytest", "cmd": f"{py} -m pytest -q"})
        elif py_tests:
            if any(f.startswith("tests/") for f in py_tests):
                top = " -t ." if has("tests/__init__.py") else ""
                cmd = f"{py} -m unittest discover -s tests{top}"
            else:
                cmd = f"{py} -m unittest discover"
            steps.append({"name": "unittest", "cmd": cmd})

    if has("go.mod"):
        steps += [{"name": "go vet", "cmd": "go vet ./..."}, {"name": "go test", "cmd": "go test ./..."}]
    if has("Cargo.toml"):
        steps += [{"name": "cargo check", "cmd": "cargo check --all-targets"},
                  {"name": "cargo test", "cmd": "cargo test"}]
    if has("pom.xml"):
        steps.append({"name": "maven test", "cmd": "mvn -q test"})
    elif has("gradlew"):
        steps.append({"name": "gradle test", "cmd": "./gradlew test"})
    if has("Package.swift"):
        steps += [{"name": "swift build", "cmd": "swift build"}, {"name": "swift test", "cmd": "swift test"}]
    if has("CMakeLists.txt"):
        gen = " -G Ninja" if shutil.which("ninja") else ""
        steps += [{"name": "configure", "cmd": f"cmake -S . -B build{gen}"},
                  {"name": "build", "cmd": "cmake --build build"}]
        cmake_text = "".join(
            (ROOT / f).read_text(errors="ignore") for f in repo_files if f.endswith("CMakeLists.txt")
        )
        if re.search(r"\b(enable_testing|add_test|ctest)\b", cmake_text, re.I):
            steps.append({"name": "ctest", "cmd": "ctest --test-dir build --output-on-failure", "timeout": 1800})
    if not steps and has("Makefile"):
        mk = (ROOT / "Makefile").read_text(errors="ignore")
        for target in ("lint", "test"):
            if f"\n{target}:" in f"\n{mk}":
                steps.append({"name": f"make {target}", "cmd": f"make {target}"})
    return steps


def run_step(step: Dict[str, Any]) -> Dict[str, Any]:
    started = time.time()
    try:
        proc = subprocess.Popen(step["cmd"], shell=True, cwd=ROOT, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, errors="replace",
                                start_new_session=(os.name != "nt"))
        try:
            output, _ = proc.communicate(timeout=step.get("timeout", 600))
            code = proc.returncode
        except subprocess.TimeoutExpired:
            if os.name != "nt":
                os.killpg(proc.pid, signal.SIGKILL)
            else:
                proc.kill()
            output, _ = proc.communicate()
            code, output = 124, (output or "") + f"\n[timeout after {step.get('timeout', 600)}s]"
    except OSError as exc:
        code, output = 127, str(exc)
    return {"name": step["name"], "cmd": step["cmd"], "code": code,
            "seconds": round(time.time() - started, 1), "output": output}


# ------------------------------------------------------------- survey
def survey() -> Dict[str, List[str]]:
    """Collect evidence of how this project is built and tested, with file:line."""
    files = set(list_repo_files())
    out: Dict[str, List[str]] = {"CI": [], "构建与清单文件": [], "脚本": [], "文档中的命令": []}

    for pattern in CI_GLOBS:
        for path in sorted(ROOT.glob(pattern)):
            rel = path.relative_to(ROOT).as_posix()
            for n, line in enumerate(path.read_text(errors="ignore").splitlines(), 1):
                stripped = line.strip()
                if CMD_WORDS.search(stripped) and not stripped.startswith("#") and (
                    "run:" in stripped or "script" in stripped or stripped.startswith("-") or "sh " in stripped
                ):
                    out["CI"].append(f"{rel}:{n}: {stripped[:200]}")

    for name in BUILD_FILES:
        path = ROOT / name
        if not path.exists():
            continue
        if name == "package.json":
            try:
                scripts = json.loads(path.read_text()).get("scripts", {})
            except (json.JSONDecodeError, OSError):
                scripts = {}
            out["构建与清单文件"] += [f"package.json scripts.{k}: {v}" for k, v in scripts.items()]
        elif name == "Makefile":
            targets = re.findall(r"^([A-Za-z0-9_.\-]+):", path.read_text(errors="ignore"), re.M)
            out["构建与清单文件"].append(f"Makefile 目标: {', '.join(targets[:30])}")
        elif name == "CMakePresets.json":
            try:
                presets = json.loads(path.read_text())
                for kind in ("configurePresets", "buildPresets", "testPresets"):
                    names = [p.get("name") for p in presets.get(kind, [])]
                    if names:
                        out["构建与清单文件"].append(f"CMakePresets {kind}: {', '.join(names)}")
            except (json.JSONDecodeError, OSError):
                out["构建与清单文件"].append("CMakePresets.json（解析失败）")
        elif name == "CMakeLists.txt":
            cm = [f for f in files if f.endswith("CMakeLists.txt")]
            text = "".join((ROOT / f).read_text(errors="ignore") for f in cm)
            tests = len(re.findall(r"\badd_test\s*\(", text, re.I))
            out["构建与清单文件"].append(f"CMakeLists.txt × {len(cm)}，add_test × {tests}"
                                       f"{'，enable_testing' if 'enable_testing' in text.lower() else ''}")
        else:
            out["构建与清单文件"].append(name)
    for f in sorted(files):
        if f.endswith((".xcodeproj/project.pbxproj",)) or f.endswith(".xcworkspace/contents.xcworkspacedata"):
            out["构建与清单文件"].append(f"Xcode: {f.split('/project.pbxproj')[0].split('/contents.')[0]}")
    for d in sorted(p.name for p in ROOT.iterdir() if p.is_dir() and re.match(r"^(build|out|cmake-build)", p.name)):
        out["构建与清单文件"].append(f"已有构建目录: {d}/")

    for f in sorted(files):
        p = Path(f)
        if p.parts and p.parts[0] in ("scripts", "script", "tools", "bin", "ci", ".ci") and len(p.parts) <= 3:
            if re.search(r"(test|lint|check|build|ci|verify|format)", p.name, re.I):
                out["脚本"].append(f)

    for name in DOC_FILES:
        path = ROOT / name
        if not path.exists():
            continue
        in_code = False
        for n, line in enumerate(path.read_text(errors="ignore").splitlines(), 1):
            if line.strip().startswith("```"):
                in_code = not in_code
                continue
            cmd = line.strip().lstrip("$ ").strip()
            inline = re.findall(r"`([^`]{4,160})`", line)
            candidates = [cmd] if in_code else inline
            for c in candidates:
                if looks_like_command(c):
                    out["文档中的命令"].append(f"{name}:{n}: {c[:200]}")
    return out


def looks_like_command(c: str) -> bool:
    first = c.split()[0] if c.split() else ""
    return bool(
        CMD_WORDS.search(c) and re.match(r"^[\w./-]", c) and " " in c
        and not first.endswith("/")                 # directory-tree lines
        and not re.match(r"^\w+\s+=", c)            # option tables: NAME = VALUE
        and not re.match(r"^[\w./-]+\s+#", c)       # "path  # comment"
    )


def calibrate(steps: List[Dict[str, Any]], slow_s: float) -> List[Dict[str, Any]]:
    report = []
    for step in steps:
        print(f"[calibrate] ▶ {step['name']}: {step['cmd']}", flush=True)
        r = run_step({**step, "timeout": step.get("timeout", 3600)})
        advice = []
        if r["code"] != 0:
            advice.append("失败：先确认是命令不对、缺前置步骤，还是项目原本就失败（基线失败记入 pitfalls）")
        if r["seconds"] > slow_s and not step.get("optional"):
            advice.append(f"耗时 {r['seconds']}s > {slow_s}s：建议标记 optional")
        suggested_timeout = max(120, int(r["seconds"] * 3))
        report.append({"name": step["name"], "cmd": step["cmd"], "code": r["code"], "seconds": r["seconds"],
                       "suggested_timeout": suggested_timeout, "advice": advice,
                       "tail": r["output"].strip().splitlines()[-15:] if r["code"] else []})
        mark = "✅" if r["code"] == 0 else f"❌ exit {r['code']}"
        print(f"[calibrate]   {mark} · {r['seconds']}s · 建议 timeout={suggested_timeout}"
              + "".join(f"\n    → {a}" for a in advice))
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    history = STATE_DIR / "calibrations" / (uuid.uuid4().hex + ".json")
    atomic_json(history, report)
    atomic_json(STATE_DIR / "calibration.json", report)  # compatibility: most recent report
    _prune_entries(STATE_DIR / "calibrations", keep=10)
    print(f"[calibrate] 结果已写入 {history.relative_to(ROOT)}（最新副本：.harness/.state/calibration.json）")
    return report


def _prune_entries(parent: Path, keep: int = 15, preserve: str = "") -> None:
    if not parent.is_dir():
        return
    items = sorted(parent.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
    for stale in items[keep:]:
        if preserve and stale.relative_to(ROOT).as_posix() == preserve:
            continue
        try:
            shutil.rmtree(stale) if stale.is_dir() else stale.unlink()
        except OSError:
            pass


def validate_steps(steps):
    if not isinstance(steps, list):
        raise StateError("verify 必须是步骤数组")
    names = set()
    for step in steps:
        if (not isinstance(step, dict) or not isinstance(step.get("name"), str)
                or not step["name"].strip() or not isinstance(step.get("cmd"), str) or not step["cmd"].strip()):
            raise StateError("每个验证步骤必须有非空 name 和 cmd")
        if step["name"] in names:
            raise StateError(f"验证步骤名称重复：{step['name']}")
        names.add(step["name"])
        timeout = step.get("timeout", 600)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
            raise StateError("验证 timeout 必须是正数")


def affected_test_files(cfg) -> List[str]:
    task = load()
    if task and task.get("base"):
        changed = changed_files(task["base"], fingerprint())
    else:
        changed = [f for f in git_dirty_files() if is_code_file(f, cfg)]
    tests = {f for f in changed if "test" in Path(f).name.lower()}
    if changed:
        import codegraph
        if codegraph.available() and codegraph.initialised() and codegraph.sync() == "已同步":
            code, out = codegraph.run(["affected", *changed], quiet=True)
            if code == 0:
                for token in re.findall(r"[A-Za-z0-9_./-]+\.(?:py|ts|tsx|js|jsx|go|rs|java|cpp|cc|c|swift)", out):
                    rel = token.split(":")[0]
                    if (ROOT / rel).is_file() and "test" in Path(rel).name.lower():
                        tests.add(rel)
    return sorted(tests)


def scope_affected_steps(steps: List[Dict[str, Any]], test_files: List[str]) -> List[Dict[str, Any]]:
    if not test_files:
        return [s for s in steps if not s.get("optional")]
    scoped = []
    q_files = " ".join(shlex_quote(f) for f in test_files)
    for step in steps:
        if step.get("optional"):
            continue
        cmd = step["cmd"]
        if "unittest discover" in cmd and all(f.endswith(".py") for f in test_files):
            py = cmd.split("-m unittest")[0].strip() or "python3"
            scoped.append({**step, "cmd": f"{py} -m unittest {q_files}"})
        elif re.search(r"\bpytest\b", cmd) and all(f.endswith(".py") for f in test_files):
            scoped.append({**step, "cmd": f"{cmd} {q_files}"})
        else:
            scoped.append(step)
    return scoped


def shlex_quote(s: str) -> str:
    import shlex
    return shlex.quote(s)


def execute_steps_with_parallelism(steps: List[Dict[str, Any]], force_parallel: bool = False) -> List[Dict[str, Any]]:
    from concurrent.futures import ThreadPoolExecutor
    batches: List[List[tuple[int, Dict[str, Any]]]] = []
    for idx, step in enumerate(steps):
        is_par = bool(force_parallel or step.get("parallel"))
        if is_par and batches and (force_parallel or batches[-1][0][1].get("parallel")):
            batches[-1].append((idx, step))
        else:
            batches.append([(idx, step)])

    ordered_results: List[Dict[str, Any]] = [{}] * len(steps)
    for batch in batches:
        if len(batch) == 1:
            idx, step = batch[0]
            print(f"[verify] ▶ {step['name']}: {step['cmd']}", flush=True)
            ordered_results[idx] = run_step(step)
        else:
            names = ", ".join(s["name"] for _, s in batch)
            print(f"[verify] ⚡ 并行执行 ({len(batch)} 步): {names}", flush=True)
            for _, step in batch:
                print(f"[verify] ▶ {step['name']}: {step['cmd']}", flush=True)
            with ThreadPoolExecutor(max_workers=min(len(batch), 8)) as pool:
                futures = [(idx, pool.submit(run_step, step)) for idx, step in batch]
                for idx, fut in futures:
                    ordered_results[idx] = fut.result()
    return ordered_results


def verify_steps(mode, only=None, include_optional=False, force_parallel=False):
    """Run the configured steps. Only a full run on an unchanged tree counts as verification."""
    cfg = load_config()
    steps = cfg.get("verify") or detect()
    validate_steps(steps)
    if only:
        steps = [s for s in steps if s["name"] == only]
    elif mode == "smoke":
        steps = [s for s in steps if s.get("kind") == "smoke"]
    elif mode == "affected":
        steps = scope_affected_steps(steps, affected_test_files(cfg))
    elif not include_optional:
        steps = [s for s in steps if not s.get("optional")]
    before = fingerprint()
    run_id = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    directory = STATE_DIR / "logs" / run_id
    directory.mkdir(parents=True, exist_ok=True)
    results = execute_steps_with_parallelism(steps, force_parallel=force_parallel)
    for index, result in enumerate(results):
        log = directory / f"{index + 1}.log"
        log.write_text(result.pop("output"), encoding="utf-8")
        result["log"] = log.relative_to(ROOT).as_posix()
        mark = "通过" if result["code"] == 0 else f"失败（exit {result['code']}）"
        print(f"[verify] {result['name']}: {mark} · {result['seconds']}s")
        if result["code"] != 0:
            print("\n".join(log.read_text().splitlines()[-TAIL_LINES:]))
    after = fingerprint()
    stable = before["id"] == after["id"]
    ok = bool(results) and all(r["code"] == 0 for r in results) and stable
    summary = ", ".join(f"{r['name']}={'ok' if r['code'] == 0 else 'FAIL'}" for r in results)
    status = "passed" if ok else "unverified" if not results else "stale" if not stable else "failed"
    atomic_json(directory / "result.json", {"mode": mode, "status": status, "fingerprint": after["id"],
                                            "results": results, "at": now()})
    preserved_log = ""
    if TASK_FILE.exists():
        with edit() as task:
            task["verify_runs"] = int(task.get("verify_runs") or 0) + 1
            if mode == "full":
                task["verify"] = {"fp": after["id"], "ok": ok, "summary": summary,
                                  "logs": directory.relative_to(ROOT).as_posix(), "at": now()}
            preserved_log = (task.get("verify") or {}).get("logs") or ""
    _prune_entries(STATE_DIR / "logs", keep=15, preserve=preserved_log)
    print(f"[verify] {status} · {mode} — {summary or '未运行任何检查'}（日志：{directory.relative_to(ROOT)}）")
    if not stable:
        print("[verify] 执行期间代码或配置变化，结果不能用于完成判定，请重跑。")
    if mode != "full" and ok:
        print("[verify] 局部校验不计入完成判定；收尾时 task.py finish 会跑完整校验。")
    return 0 if ok else 2 if not results else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--all", action="store_true", help="include optional steps")
    ap.add_argument("--smoke", action="store_true", help="run runtime black-box smoke steps (kind=smoke)")
    ap.add_argument("--only", help="run only the step with this name")
    ap.add_argument("--affected", action="store_true", help="run affected tests first (fast inner-loop check)")
    ap.add_argument("--parallel", action="store_true", help="run verification steps in parallel")
    ap.add_argument("--detect", action="store_true", help="print detected steps and exit")
    ap.add_argument("--survey", action="store_true", help="list build/test evidence and exit")
    ap.add_argument("--calibrate", action="store_true", help="run candidate steps and report timings")
    ap.add_argument("--candidates", help="JSON file of candidate steps for --calibrate")
    ap.add_argument("--task", help="task id (optional, scoped to checkout)")
    args = ap.parse_args()
    cfg = load_config()

    if args.detect:
        print(json.dumps(detect(), ensure_ascii=False, indent=2))
        return 0
    if args.survey:
        for section, items in survey().items():
            print(f"## {section}")
            print("\n".join(f"- {i}" for i in items[:80]) if items else "- （无）")
            if len(items) > 80:
                print(f"- … 另有 {len(items) - 80} 条")
            print()
        print("## 自动检测（兜底，仅供参考）")
        print(json.dumps(detect(), ensure_ascii=False, indent=2))
        return 0
    if args.calibrate:
        if args.candidates:
            candidates = json.loads(Path(args.candidates).read_text(encoding="utf-8"))
        else:
            candidates = cfg.get("verify") or detect()
        if not candidates:
            print("[calibrate] 没有候选步骤：用 --candidates 传入 JSON 文件，或先写入 project.json。")
            return 2
        report = calibrate(candidates, float(cfg.get("slow_step_seconds", 300)))
        return 0 if all(r["code"] == 0 for r in report) else 1

    try:
        mode = "only" if args.only else "smoke" if args.smoke else "affected" if args.affected else "full"
        return verify_steps(mode, args.only, args.all, force_parallel=args.parallel)
    except (StateError, OSError) as exc:
        print(f"[verify] {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
