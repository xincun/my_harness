#!/usr/bin/env python3
"""Deterministic harness protocol baseline, with an independent behavior oracle.

No model calls, private repositories, or inferred usage statistics. Fixed replay
actions test the harness decision; they are not a measure of agent coding skill.
"""

import argparse
import hashlib
import json
import shlex
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from test_harness import Project, sh  # noqa: E402

FEATURE = "\n\ndef shipping_fee(total_cents):\n    return 0 if total_cents >= 9900 else 800\n"
TEST = """import unittest
from shop.pricing import shipping_fee
class ShippingTest(unittest.TestCase):
    def test_threshold(self):
        self.assertEqual(shipping_fee(9899), 800)
        self.assertEqual(shipping_fee(9900), 0)
        self.assertEqual(shipping_fee(10000), 0)
"""
# This oracle is held outside the installed candidate's verification config.
ORACLE = ("from shop.pricing import shipping_fee, apply_discount; "
          "assert shipping_fee(9899) == 800; assert shipping_fee(9900) == 0; "
          "assert shipping_fee(10000) == 0; assert apply_discount(1000, 10) == 900")


def run_case(case):
    started = time.monotonic()
    project = Project()
    try:
        project.define("free shipping threshold is exactly 9900 cents", "existing orders still work")
        identifier = case["id"]
        config_path = project.root / ".harness/project.json"
        config = json.loads(config_path.read_text())
        config["verify"] = [{"name": "tests", "cmd": shlex.quote(sys.executable) + " -m unittest discover -s tests"}]
        config_path.write_text(json.dumps(config))
        checks = {}

        if identifier == "worktree_parallel_handoff":
            git_c = "git -c user.email=t@t -c user.name=t"
            with tempfile.TemporaryDirectory() as wt_parent:
                wt_dir = Path(wt_parent) / "wt-shipping"
                sh(f"git worktree add -q -b feat-shipping {shlex.quote(str(wt_dir))}", project.root, check=True)
                (wt_dir / "shop/pricing.py").write_text((wt_dir / "shop/pricing.py").read_text() + FEATURE)
                (wt_dir / "tests/test_shipping.py").write_text(TEST)
                sh(f"git add shop/pricing.py tests/test_shipping.py && {git_c} commit -qm feat", wt_dir, check=True)
                # Child worktree task state is physically isolated from main checkout
                checks["main_unverified_before_merge"] = not json.loads(
                    project.py("hooks/stop_gate.py", "--check").stdout
                )["completed"]
                sh(f"{git_c} merge -q feat-shipping && git worktree remove --force {shlex.quote(str(wt_dir))}",
                   project.root, check=True)
        else:
            feature = FEATURE.replace(">= 9900", "> 9900") if identifier == "regression" else FEATURE
            project.write("shop/pricing.py", feature, append=True)
            project.write("tests/test_shipping.py", TEST)

        if identifier == "zero_checks":
            config["verify"][0]["optional"] = True
            config_path.write_text(json.dumps(config))

        if identifier == "conflicting_writer":
            conflict = project.py("task.py", "start", "--summary", "conflicting task", "--accept", "other")
            checks["conflicting_writer_rejected"] = conflict.returncode != 0

        if identifier == "unverified_wait":
            project.py("task.py", "pause", "waiting for example")
        elif identifier == "conflicting_writer":
            pass
        else:
            finish_args = ["task.py", "finish", "--type", "feature", "--summary", case["prompt"], "--outcome", "success"]
            if identifier == "missing_acceptance":
                finish_args += ["--covers", "C1=ShippingTest threshold checks"]
            elif identifier != "zero_checks":
                finish_args += ["--covers", "C1=ShippingTest threshold checks", "--covers", "C2=PricingTest existing orders"]
            res = project.py(*finish_args)
            checks["finish_exit"] = res.returncode

        if identifier == "deleted_after_verify":
            (project.root / "shop/pricing.py").unlink()

        if identifier == "missing_log":
            state = project.state()
            logs_dir = (state.get("verify") or {}).get("logs")
            if logs_dir and (project.root / logs_dir).exists():
                shutil.rmtree(project.root / logs_dir)

        result = project.py("hooks/stop_gate.py", "--check")
        report = json.loads(result.stdout)
        oracle = sh([sys.executable, "-B", "-c", ORACLE], project.root)
        completed = report["completed"]
        false_completion = completed and (not case["expected_completed"] or oracle.returncode != 0)
        passed = completed == case["expected_completed"] and not false_completion
        passed = passed and checks.get("conflicting_writer_rejected", True)
        passed = passed and checks.get("main_unverified_before_merge", True)
        return {"id": identifier, "passed": passed, "expected_completed": case["expected_completed"],
                "completed": completed, "status": report["status"], "false_completion": false_completion,
                "oracle_passed": oracle.returncode == 0, "checks": checks,
                "seconds": round(time.monotonic() - started, 3), "input_tokens": None,
                "output_tokens": None, "cost_usd": None, "human_interventions": None}
    except Exception as exc:
        return {"id": case["id"], "passed": False, "error": str(exc), "false_completion": None,
                "seconds": round(time.monotonic() - started, 3)}
    finally:
        project.close()


def source_digest():
    paths = list((ROOT / "template").rglob("*")) + list((ROOT / "evals").glob("*.py"))
    paths += [ROOT / "install.py", ROOT / "tests/test_harness.py", ROOT / "evals/cases.json"]
    hashes = {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in paths if p.is_file() and not set(p.parts) & {"__pycache__", ".state", "tools"}}
    return hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--output", required=True)
    ap.add_argument("--case", action="append", help="optional fixed case IDs")
    args = ap.parse_args()
    cases = json.loads((ROOT / "evals/cases.json").read_text())
    if args.case:
        unknown = set(args.case) - {c["id"] for c in cases}
        if unknown:
            ap.error(f"unknown cases: {sorted(unknown)}")
        cases = [c for c in cases if c["id"] in args.case]
    results = []
    for case in cases:
        result = run_case(case)
        results.append(result)
        print(f"{case['id']}: {'PASS' if result['passed'] else 'FAIL'} ({result['seconds']}s)", flush=True)
    report = {"schema": 1, "kind": "deterministic_protocol_replay", "model_evaluation": False,
              "source_sha256": source_digest(), "python": sys.version, "cases": results,
              "summary": {"total": len(results), "passed": sum(r["passed"] for r in results),
                          "false_completions": sum(r.get("false_completion") is True for r in results),
                          "unclassified_errors": sum("error" in r for r in results),
                          "seconds": round(sum(r["seconds"] for r in results), 3)}}
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return 0 if all(r["passed"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
