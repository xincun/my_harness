"""Completion must describe verified work, not merely permission to end a turn."""

import json
import os
import subprocess
import sys
import time
import unittest

from test_harness import Project, sh


class CompletionRegressionTest(unittest.TestCase):
    def setUp(self):
        self.p = Project()
        self.p.hook("session_start", {"session_id": "first", "source": "startup"})

    def tearDown(self):
        self.p.close()

    def config(self, steps):
        path = self.p.root / ".harness/project.json"
        cfg = json.loads(path.read_text())
        cfg["verify"] = steps
        path.write_text(json.dumps(cfg))

    def test_deletion_without_verification_blocks(self):
        (self.p.root / "shop/pricing.py").unlink()
        out = self.p.hook("stop_gate", {"session_id": "first"})
        self.assertIn('"decision": "block"', out.stdout, out.stdout + out.stderr)

    def test_zero_selected_checks_cannot_pass(self):
        self.config([{"name": "optional", "cmd": "true", "optional": True}])
        out = self.p.py("verify.py")
        self.assertNotEqual(out.returncode, 0, out.stdout)

    def test_second_session_does_not_replace_first_baseline(self):
        self.p.write("shop/pricing.py", "\n# first session edit\n", append=True)
        self.p.hook("session_start", {"session_id": "second", "source": "startup"})
        out = self.p.hook("stop_gate", {"session_id": "first"})
        self.assertIn('"decision": "block"', out.stdout, out.stdout + out.stderr)

    def test_mutation_during_verification_invalidates_result(self):
        self.config([{"name": "read", "cmd": sys.executable + " .harness/.state/read_then_wait.py"}])
        self.p.write(".harness/.state/read_then_wait.py", """from pathlib import Path
import time
compile(Path('shop/pricing.py').read_text(), 'pricing.py', 'exec')
Path('.harness/.state/ready').touch()
while not Path('.harness/.state/release').exists():
    time.sleep(0.01)
""")
        proc = subprocess.Popen(
            [sys.executable, ".harness/scripts/verify.py"], cwd=self.p.root,
            env={**os.environ, "CLAUDE_PROJECT_DIR": str(self.p.root)},
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        try:
            deadline = time.monotonic() + 10
            while not (self.p.root / ".harness/.state/ready").exists():
                if proc.poll() is not None or time.monotonic() >= deadline:
                    self.fail("verification command never reached its source read")
                time.sleep(0.01)
            self.p.write("shop/pricing.py", "invalid python !\n")
            self.p.write(".harness/.state/release", "")
            stdout, stderr = proc.communicate(timeout=10)
            self.assertNotEqual(proc.returncode, 0, stdout + stderr)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.communicate()


class AcceptanceTest(unittest.TestCase):
    def setUp(self):
        self.p = Project()
        self.task_id = self.p.define("pricing and order behavior pass")

    def tearDown(self):
        self.p.close()

    def check(self):
        result = self.p.py("hooks/stop_gate.py", "--check")
        return result, json.loads(result.stdout)

    def finish(self, *extra):
        result = self.p.py("task.py", "finish", "--type", "feature", "--summary", "demo", *extra)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(self.check()[1]["completed"])

    def test_unmet_acceptance_cannot_be_recorded_as_success(self):
        cfg = self.p.root / ".harness/project.json"
        value = json.loads(cfg.read_text())
        value["verify"] = [{"name": "broken", "cmd": "false"}]
        cfg.write_text(json.dumps(value))

        result = self.p.py("task.py", "finish", "--type", "feature", "--summary", "demo")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.check()[1]["completed"])

    def test_same_mtime_edit_and_deletion_invalidate_completion(self):
        self.finish()
        source = self.p.root / "shop/pricing.py"
        old = source.stat()
        source.write_text(source.read_text() + "\n# same timestamp\n")
        os.utime(source, ns=(old.st_atime_ns, old.st_mtime_ns))
        self.assertFalse(self.check()[1]["completed"])
        self.finish()
        source.unlink()
        self.assertFalse(self.check()[1]["completed"])

    def test_config_contract_and_verifier_changes_invalidate_evidence(self):
        self.finish()
        cfg = self.p.root / ".harness/project.json"
        value = json.loads(cfg.read_text())
        value["verify"] = [{"name": "replacement", "cmd": "false"}]
        cfg.write_text(json.dumps(value))
        self.assertFalse(self.check()[1]["verified"])
        value["verify"] = []
        cfg.write_text(json.dumps(value))
        self.finish()
        self.p.py("task.py", "add", "--accept", "a newly requested requirement")
        self.assertFalse(self.check()[1]["completed"])
        self.finish()
        self.p.write(".harness/scripts/verify.py", "\n# new verifier version\n", append=True)
        self.assertFalse(self.check()[1]["verified"])

    def test_only_smoke_and_unknown_steps_never_count_as_full_verification(self):
        cfg = self.p.root / ".harness/project.json"
        value = json.loads(cfg.read_text())
        value["verify"] = [
            {"name": "test_step", "cmd": "true"},
            {"name": "e2e_smoke", "cmd": "true", "kind": "smoke"},
        ]
        cfg.write_text(json.dumps(value))
        self.finish()
        self.assertEqual(self.p.py("verify.py", "--only", "test_step").returncode, 0)
        self.p.write("shop/pricing.py", "\n# touched\n", append=True)
        self.p.py("verify.py", "--only", "test_step")
        self.assertFalse(self.check()[1]["completed"])
        smoke = self.p.py("verify.py", "--smoke")
        self.assertEqual(smoke.returncode, 0, smoke.stdout)
        self.assertIn("passed · smoke — e2e_smoke=ok", smoke.stdout)
        self.assertFalse(self.check()[1]["completed"])

    def test_affected_mode_runs_scoped_tests_without_granting_full_completion(self):
        self.p.write("shop/pricing.py", "\n# edit pricing\n", append=True)
        self.p.write("tests/test_shop.py", "\n# touched test\n", append=True)
        res = self.p.py("verify.py", "--affected")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("passed · affected", res.stdout)
        self.assertFalse(self.check()[1]["verified"], "affected mode is inner-loop only; full verify still required")
        self.finish()

    def test_wait_failure_and_retry_limit_never_mean_completed(self):
        self.p.py("task.py", "pause", "waiting for input")
        self.assertEqual(self.check()[1]["status"], "pending")
        self.assertTrue(self.check()[1]["allow_turn_end"])
        self.assertFalse(self.check()[1]["completed"])

        self.p.write("shop/orders.py", "\n# new work\n", append=True)
        self.assertEqual(self.check()[1]["status"], "active")
        # Honest partial/failed requires a friction explanation
        no_reason = self.p.py("task.py", "finish", "--type", "feature", "--outcome", "partial")
        self.assertNotEqual(no_reason.returncode, 0)
        for outcome in ("partial", "failed"):
            res = self.p.py("task.py", "finish", "--type", "feature", "--summary", "incomplete",
                            "--outcome", outcome, "--friction", "external service unavailable")
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertEqual(self.check()[1]["status"], outcome)
            self.assertTrue(self.check()[1]["allow_turn_end"])
            self.assertFalse(self.check()[1]["completed"])
        self.finish()

    def test_startup_and_clear_keep_baseline_and_corruption_fails_closed(self):
        baseline = self.p.state()["base"]
        self.p.write("shop/orders.py", "\n# changed\n", append=True)
        for source in ("startup", "clear", "resume", "compact"):
            self.p.hook("session_start", {"source": source})
            self.assertEqual(self.p.state()["base"], baseline)
        (self.p.root / ".harness/.state/task.json").write_text("broken json")
        result, report = self.check()
        self.assertEqual(result.returncode, 2)
        self.assertFalse(report["completed"])
        self.assertIn('"decision": "block"', self.p.hook("stop_gate", {}).stdout)

    def test_non_git_shell_edits_are_visible_without_hooks(self):
        other = Project(git=False)
        try:
            other.define()
            other.write("shop/orders.py", "\n# shell edit\n", append=True)
            report = json.loads(other.py("hooks/stop_gate.py", "--check").stdout)
            self.assertIn("shop/orders.py", report["changed_files"])
            (other.root / "shop/pricing.py").unlink()
            report = json.loads(other.py("hooks/stop_gate.py", "--check").stdout)
            self.assertIn("shop/pricing.py", report["changed_files"])
        finally:
            other.close()

    def test_completed_record_is_idempotent_yet_updates_learned_metadata(self):
        self.finish()
        path = self.p.root / ".harness/evolution/tasks.jsonl"
        before = path.read_text()
        self.finish()  # exact same args -> byte-identical idempotence
        self.assertEqual(path.read_text(), before)
        entry = json.loads(before.splitlines()[-1])
        self.assertEqual(entry["task_id"], self.task_id)
        self.assertIsNone(entry["metrics"]["input_tokens"])
        self.assertIsNone(entry["metrics"]["human_interventions"])
        self.assertGreaterEqual(entry["metrics"]["elapsed_seconds"], 0)

        # Updating learned/friction on the same task updates in place instead of dropping or duplicating
        self.finish("--learned", "pitfalls#P001", "--friction", "ambiguous spec")
        lines = path.read_text().splitlines()
        self.assertEqual(len(lines), 1)
        updated = json.loads(lines[-1])
        self.assertEqual(updated["learned"], ["pitfalls#P001"])
        self.assertEqual(updated["friction"], "ambiguous spec")

    def test_gitignored_control_inputs_and_symlink_content_are_bound(self):
        self.p.write(".gitignore", ".harness/\n.claude/\n.codex/\ngenerated.py\n")
        self.p.write("generated.py", "value = 1\n")
        (self.p.root / "alias.py").symlink_to("generated.py")
        self.finish()
        self.p.write("generated.py", "value = 2\n")
        self.assertFalse(self.check()[1]["verified"])
        self.finish()
        self.p.write(".harness/scripts/task.py", "\n# changed control\n", append=True)
        self.assertFalse(self.check()[1]["verified"])

    def test_batch_accept_and_parallel_verify(self):
        cfg = self.p.root / ".harness/project.json"
        value = json.loads(cfg.read_text())
        value["verify"] = [
            {"name": "s1", "cmd": "python3 -c 'import time; time.sleep(0.15); print(1)'", "parallel": True},
            {"name": "s2", "cmd": "python3 -c 'import time; time.sleep(0.15); print(2)'", "parallel": True},
        ]
        cfg.write_text(json.dumps(value))
        ver = self.p.py("verify.py")
        self.assertEqual(ver.returncode, 0, ver.stdout + ver.stderr)
        self.assertIn("并行执行 (2 步)", ver.stdout)
        self.finish()

    def test_tiered_ratchet_and_incremental_milestone_checkoff(self):
        start = self.p.py("task.py", "start", "--new", "--tier", "S", "--summary", "epic task",
                          "--accept", "M1", "--accept", "M2")
        self.assertEqual(start.returncode, 0, start.stderr)
        self.assertEqual(self.check()[1]["tier"], "S")

        up = self.p.py("task.py", "add", "--tier", "L")
        self.assertEqual(up.returncode, 0, up.stderr)
        self.assertEqual(self.check()[1]["tier"], "L")

        down = self.p.py("task.py", "add", "--tier", "M")
        self.assertNotEqual(down.returncode, 0, "one-way ratchet must reject tier downgrade")

        chk = self.p.py("task.py", "check", "--covers", "C1=milestone 1 verified")
        self.assertEqual(chk.returncode, 0, chk.stderr)
        self.assertFalse(self.check()[1]["completed"])
        self.assertEqual(self.check()[1]["criteria"][0]["status"], "passed")

        # Missing C2 still blocks success when --covers only names C1
        bad_fin = self.p.py("task.py", "finish", "--covers", "C1=milestone 1 verified")
        self.assertNotEqual(bad_fin.returncode, 0)

        ok_fin = self.p.py("task.py", "finish", "--covers", "C2=milestone 2 verified")
        self.assertEqual(ok_fin.returncode, 0, ok_fin.stderr)
        self.assertTrue(self.check()[1]["completed"])

    def test_committing_preexisting_dirty_file_does_not_falsely_report_change(self):
        self.p.write("shop/pricing.py", "\n# dirty before task start\n", append=True)
        self.p.py("task.py", "start", "--new", "--summary", "clean check", "--accept", "ok")
        sh("git add shop/pricing.py && git -c user.email=t@t -c user.name=t commit -qm commit_as_is",
           self.p.root, check=True)
        report = self.check()[1]
        self.assertEqual(report["changed_files"], [])


if __name__ == "__main__":
    unittest.main()
