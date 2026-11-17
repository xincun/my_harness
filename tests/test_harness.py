"""End-to-end tests: install the template into a copy of the demo app and
drive every script and hook the way Claude Code / Codex / Antigravity would.

Run: python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEMO = REPO / "examples" / "demo-app"


def sh(cmd, cwd, stdin=None, check=False, env_extra=None):
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(cwd)}
    if env_extra:
        env.update(env_extra)
    p = subprocess.run(cmd, cwd=cwd, input=stdin, capture_output=True, text=True, env=env, shell=isinstance(cmd, str))
    if check and p.returncode != 0:
        raise AssertionError(f"{cmd} failed:\n{p.stdout}\n{p.stderr}")
    return p


class Project:
    def __init__(self, git: bool = True):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve() / "proj"
        shutil.copytree(DEMO, self.root)
        if git:
            sh("git init -q && git add -A && git -c user.email=t@t -c user.name=t commit -qm init", self.root, check=True)
        sh([sys.executable, str(REPO / "install.py"), str(self.root)], self.root, check=True)

    def py(self, script, *args, stdin=None):
        return sh([sys.executable, f".harness/scripts/{script}", *args], self.root, stdin=stdin)

    def hook(self, name, payload):
        return self.py(f"hooks/{name}.py", stdin=json.dumps(payload))

    def state(self, session="manual"):
        return json.loads((self.root / ".harness/.state/task.json").read_text())

    def project_state(self):
        f = self.root / ".harness/.state/codegraph.json"
        return json.loads(f.read_text()) if f.exists() else {}

    def define(self, *criteria, new=False):
        args = ["start", "--summary", "demo acceptance"]
        if new:
            args.append("--new")
        for criterion in criteria or ("pricing and order regression tests pass",):
            args += ["--accept", criterion]
        result = self.py("task.py", *args)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)
        return json.loads(result.stdout)["task_id"]

    def accept_all(self, *extra):
        res = self.py("task.py", "finish", "--type", "feature", "--outcome", "success", *extra)
        if res.returncode != 0:
            raise AssertionError(res.stdout + res.stderr)

    def write(self, rel, text, append=False):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a" if append else "w") as f:
            f.write(text)

    def close(self):
        self.tmp.cleanup()


class InstallTest(unittest.TestCase):
    def test_install_is_non_destructive(self):
        with tempfile.TemporaryDirectory() as d:
            target = Path(d)
            (target / "AGENTS.md").write_text("# my rules\n")
            (target / ".claude").mkdir()
            (target / ".claude/settings.json").write_text(json.dumps(
                {"permissions": {"allow": ["Bash(make:*)"]}, "model": "opus"}))
            sh([sys.executable, str(REPO / "install.py"), d], target, check=True)

            self.assertEqual((target / "AGENTS.md").read_text(), "# my rules\n")
            self.assertTrue((target / "AGENTS.harness.md").exists())
            settings = json.loads((target / ".claude/settings.json").read_text())
            self.assertEqual(settings["model"], "opus")
            self.assertIn("Bash(make:*)", settings["permissions"]["allow"])
            self.assertIn("Stop", settings["hooks"])
            sh([sys.executable, str(REPO / "install.py"), d], target, check=True)
            settings = json.loads((target / ".claude/settings.json").read_text())
            self.assertEqual(len(settings["hooks"]["Stop"]), 1)

    def test_codex_hooks_merge_without_claude_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            (target / ".codex").mkdir()
            path = target / ".codex/hooks.json"
            path.write_text(json.dumps({"description": "existing hooks", "hooks": {
                "Stop": [{"hooks": [{"type": "command", "command": "echo existing"}]}]}}))
            for _ in range(2):
                sh([sys.executable, str(REPO / "install.py"), directory], target, check=True)
            settings = json.loads(path.read_text())
            self.assertNotIn("permissions", settings)
            self.assertEqual(settings["description"], "existing hooks")
            self.assertEqual(len(settings["hooks"]["Stop"]), 2)

    def test_generated_codex_hook_command_works_from_subdirectory(self):
        p = Project()
        try:
            settings = json.loads((p.root / ".codex/hooks.json").read_text())
            command = settings["hooks"]["SessionStart"][0]["hooks"][0]["command"]
            result = sh(command, p.root / "shop", stdin=json.dumps({"session_id": "codex", "source": "startup"}))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Harness 简报", result.stdout)
            self.assertEqual(p.state("codex")["session_id"], "codex")
        finally:
            p.close()

    def test_antigravity_hooks_merge_idempotently(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            (target / ".agents").mkdir()
            path = target / ".agents/hooks.json"
            path.write_text(json.dumps({
                "lint-checker": {"PostToolUse": [{"matcher": "run_command", "hooks": [{"command": "echo lint"}]}]},
                "harness": {"Stop": [{"type": "command", "command": "echo custom-stop"}]},
            }))
            for _ in range(2):
                sh([sys.executable, str(REPO / "install.py"), directory], target, check=True)
            hooks = json.loads(path.read_text())
            self.assertIn("lint-checker", hooks)
            self.assertEqual(len(hooks["harness"]["Stop"]), 2)
            self.assertEqual(len(hooks["harness"]["SessionStart"]), 1)
            self.assertEqual(len(hooks["harness"]["PreToolUse"]), 1)

    def test_install_upgrade_updates_scripts_and_prunes_retired_files(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            sh([sys.executable, str(REPO / "install.py"), directory], target, check=True)
            (target / "docs/ARCHITECTURE.md").write_text("# Custom Architecture\n")
            (target / ".harness/scripts/task.py").write_text("# outdated script\n")
            # Simulate retired files from earlier master versions
            (target / ".harness/scripts/record_task.py").write_text("# retired\n")
            (target / ".harness/scripts/repo_map.py").write_text("# retired\n")
            (target / "docs/knowledge/generated").mkdir(parents=True, exist_ok=True)
            (target / "docs/knowledge/generated/REPO_MAP.md").write_text("# retired\n")
            (target / "docs/guidelines/chromium_layered_components.md").write_text("# retired\n")

            res = sh([sys.executable, str(REPO / "install.py"), directory, "--upgrade"], target, check=True)
            self.assertIn("升级 1 个控制平面文件：.harness/scripts/task.py", res.stdout)
            self.assertEqual((target / "docs/ARCHITECTURE.md").read_text(), "# Custom Architecture\n")
            self.assertNotEqual((target / ".harness/scripts/task.py").read_text(), "# outdated script\n")
            self.assertFalse((target / ".harness/scripts/record_task.py").exists())
            self.assertFalse((target / ".harness/scripts/repo_map.py").exists())
            self.assertFalse((target / "docs/knowledge/generated/REPO_MAP.md").exists())
            self.assertFalse((target / "docs/guidelines/chromium_layered_components.md").exists())
            self.assertFalse((target / "AGENTS.harness.md").exists())

    def test_install_link_mode_and_safe_materialize_on_upgrade(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            res = sh([sys.executable, str(REPO / "install.py"), directory, "--link"], target, check=True)
            self.assertIn("软链同步", res.stdout)
            self.assertTrue((target / ".harness/scripts").is_symlink())
            self.assertFalse((target / ".harness/project.json").is_symlink())

            # Switching back to standalone via --upgrade replaces symlinks safely without touching TEMPLATE
            up = sh([sys.executable, str(REPO / "install.py"), directory, "--upgrade"], target, check=True)
            self.assertEqual(up.returncode, 0, up.stderr)
            self.assertFalse((target / ".harness/scripts").is_symlink())
            self.assertTrue((target / ".harness/scripts/task.py").is_file())

    def test_export_plugin_and_thin_workspace_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            plugin_dir = base / "plugin"
            thin_proj = base / "thin-app"
            unmanaged = base / "plain-folder"
            shutil.copytree(DEMO, thin_proj)
            unmanaged.mkdir()

            sh([sys.executable, str(REPO / "install.py"), "--export-plugin", str(plugin_dir)], base, check=True)
            self.assertTrue((plugin_dir / "plugin.json").is_file())
            self.assertTrue((plugin_dir / ".claude-plugin/plugin.json").is_file())
            self.assertTrue((plugin_dir / ".codex-plugin/plugin.json").is_file())

            sh([sys.executable, str(REPO / "install.py"), str(thin_proj), "--thin"], base, check=True)
            self.assertFalse((thin_proj / ".harness/scripts").exists(), "thin mode must not copy control-plane scripts")
            self.assertTrue((thin_proj / ".harness/project.json").is_file())

            hooks = json.loads((plugin_dir / "hooks.json").read_text())["ai-harness"]
            env_no_override = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}

            # 1. Unmanaged workspace: fast no-op, never creates .harness/
            noop = subprocess.run(
                hooks["SessionStart"][0]["command"], cwd=plugin_dir, shell=True, env=env_no_override,
                input=json.dumps({"conversationId": "c0", "workspacePaths": [str(unmanaged)]}),
                capture_output=True, text=True, check=True,
            )
            self.assertEqual(json.loads(noop.stdout), {})
            self.assertFalse((unmanaged / ".harness").exists())

            # 2. Thin workspace: plugin hooks bind to thin_proj dynamically and mount gitignored runtime links
            sh("git init -q && git add -A && git -c user.email=t@t -c user.name=t commit -qm thin_init",
               thin_proj, check=True)
            start_res = subprocess.run(
                hooks["SessionStart"][0]["command"], cwd=plugin_dir, shell=True, env=env_no_override,
                input=json.dumps({"conversationId": "c1", "workspacePaths": [str(thin_proj)]}),
                capture_output=True, text=True, check=True,
            )
            self.assertIn("Harness 简报", json.loads(start_res.stdout)["injectSteps"][0]["systemMessage"]["systemMessage"])
            self.assertTrue((thin_proj / ".harness/.state/task.json").is_file())
            self.assertTrue((thin_proj / ".harness/scripts").is_symlink(), "thin workspace mounts gitignored script link")

            # Full CLI task start -> finish works inside thin_proj while git status stays clean of scripts/
            subprocess.run(
                [sys.executable, ".harness/scripts/task.py", "start", "--tier", "S",
                 "--summary", "thin e2e", "--accept", "tests pass"],
                cwd=thin_proj, env=env_no_override, check=True, capture_output=True, text=True,
            )
            (thin_proj / "shop/pricing.py").write_text((thin_proj / "shop/pricing.py").read_text() + "\n# thin edit\n")
            stop_block = subprocess.run(
                hooks["Stop"][0]["command"], cwd=plugin_dir, shell=True, env=env_no_override,
                input=json.dumps({"conversationId": "c1", "workspacePaths": [str(thin_proj)], "terminationReason": "NO_TOOL_CALL"}),
                capture_output=True, text=True, check=True,
            )
            self.assertEqual(json.loads(stop_block.stdout)["decision"], "continue")

            subprocess.run(
                [sys.executable, ".harness/scripts/task.py", "finish", "--type", "feature"],
                cwd=thin_proj, env=env_no_override, check=True, capture_output=True, text=True,
            )
            stop_ok = subprocess.run(
                hooks["Stop"][0]["command"], cwd=plugin_dir, shell=True, env=env_no_override,
                input=json.dumps({"conversationId": "c1", "workspacePaths": [str(thin_proj)], "terminationReason": "NO_TOOL_CALL"}),
                capture_output=True, text=True, check=True,
            )
            self.assertEqual(json.loads(stop_ok.stdout)["decision"], "allow")
            dirty_paths = subprocess.run(
                ["git", "status", "--porcelain"], cwd=thin_proj, check=True, capture_output=True, text=True
            ).stdout
            self.assertNotIn(".harness/scripts", dirty_paths)
            self.assertNotIn(".harness/workflows", dirty_paths)

    def test_register_global_keyword_installs_across_all_three_hosts(self):
        with tempfile.TemporaryDirectory() as fake_home:
            res = sh([sys.executable, str(REPO / "install.py"), "--register-global"],
                     Path(fake_home), check=True, env_extra={"HOME": fake_home, "USERPROFILE": fake_home})
            self.assertIn("/harness", res.stdout)
            for rel in (
                ".gemini/config/skills/harness/SKILL.md",
                ".claude/skills/harness/SKILL.md",
                ".codex/skills/harness/SKILL.md",
            ):
                skill = Path(fake_home) / rel
                self.assertTrue(skill.is_file(), rel)
                content = skill.read_text(encoding="utf-8")
                self.assertIn("name: harness", content)
                self.assertIn(str((REPO / "install.py").resolve()), content)

    def test_greenfield_empty_project_detection_and_guidance(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            res = sh([sys.executable, str(REPO / "install.py"), directory], target, check=True)
            self.assertIn("检测到目标为空白项目目录（0→1 新项目）", res.stdout)
            brief = sh([sys.executable, ".harness/scripts/hooks/session_start.py"], target,
                       stdin=json.dumps({"session_id": "greenfield"}), check=True).stdout
            self.assertIn("空白项目尚未初始化（0→1 冷启动）", brief)

    def test_generated_antigravity_hooks_work_end_to_end(self):
        p = Project()
        try:
            hooks = json.loads((p.root / ".agents/hooks.json").read_text())["harness"]
            agents_dir = p.root / ".agents"
            conv_id = "agy-conv-1"
            start_cmd = hooks["SessionStart"][0]["command"]
            start_res = sh(start_cmd, agents_dir, stdin=json.dumps({
                "conversationId": conv_id,
                "workspacePaths": [str(p.root)],
            }))
            self.assertEqual(start_res.returncode, 0, start_res.stderr)
            start_payload = json.loads(start_res.stdout)
            self.assertIn("Harness 简报", start_payload["injectSteps"][0]["systemMessage"]["systemMessage"])
            self.assertEqual(p.state(conv_id)["session_id"], conv_id)

            task_id = p.state(conv_id)["id"]
            start_task_res = p.py("task.py", "start", "--task", task_id, "--summary", "agy test", "--accept", "tests pass")
            self.assertEqual(start_task_res.returncode, 0, start_task_res.stderr)
            pre_cmd = hooks["PreToolUse"][0]["hooks"][0]["command"]
            tool_payload = {
                "conversationId": conv_id,
                "stepIdx": 7,
                "toolCall": {"name": "edit_file", "args": {"Path": str(p.root / "shop/pricing.py")}},
            }
            pre_res = sh(pre_cmd, agents_dir, stdin=json.dumps(tool_payload))
            self.assertEqual(json.loads(pre_res.stdout)["decision"], "allow")
            guard_payload = {
                "conversationId": conv_id,
                "stepIdx": 8,
                "toolCall": {"name": "edit_file", "args": {"Path": str(p.root / ".harness/scripts/task.py")}},
            }
            guard_res = sh(pre_cmd, agents_dir, stdin=json.dumps(guard_payload))
            self.assertEqual(json.loads(guard_res.stdout)["decision"], "deny")
            p.write("shop/pricing.py", "\n# agy edit\n", append=True)

            stop_cmd = hooks["Stop"][0]["command"]
            stop_res = sh(stop_cmd, agents_dir, stdin=json.dumps({
                "conversationId": conv_id,
                "terminationReason": "NO_TOOL_CALL",
            }))
            stop_payload = json.loads(stop_res.stdout)
            self.assertEqual(stop_payload["decision"], "continue")
            self.assertIn("verify.py", stop_payload["reason"])

            # Antigravity continuationActive and TERMINAL_STEP_TYPE (e.g. ask_question) must allow stop
            cont_res = sh(stop_cmd, agents_dir, stdin=json.dumps({
                "conversationId": conv_id,
                "terminationReason": "NO_TOOL_CALL",
                "continuationActive": True,
            }))
            self.assertEqual(json.loads(cont_res.stdout)["decision"], "allow")

            ask_res = sh(stop_cmd, agents_dir, stdin=json.dumps({
                "conversationId": conv_id,
                "terminationReason": "TERMINAL_STEP_TYPE",
            }))
            self.assertEqual(json.loads(ask_res.stdout)["decision"], "allow")
        finally:
            p.close()


class HostContractCanaryTest(unittest.TestCase):
    """Verifies strict wire-format compliance against Claude Code & OpenAI Codex hook schemas."""

    def setUp(self):
        self.p = Project()

    def tearDown(self):
        self.p.close()

    def test_codex_and_claude_pretooluse_allow_emits_empty_stdout_and_blocks_apply_patch(self):
        # Codex fails with HookRunStatus::Failed if PreToolUse emits top-level {"decision": "allow"}
        ok = self.p.hook("guard", {
            "session_id": "s1",
            "cwd": str(self.p.root),
            "hook_event_name": "PreToolUse",
            "tool_name": "apply_patch",
            "tool_input": {"input": "*** Begin Patch\n*** Update File: shop/pricing.py\n@@\n*** End Patch\n"},
        })
        self.assertEqual(ok.returncode, 0)
        self.assertEqual(ok.stdout.strip(), "", "Claude/Codex silent allow must emit empty stdout")

        deny_patch = self.p.hook("guard", {
            "session_id": "s1",
            "cwd": str(self.p.root),
            "hook_event_name": "PreToolUse",
            "tool_name": "apply_patch",
            "tool_input": "*** Begin Patch\n*** Update File: .harness/scripts/verify.py\n@@\n*** End Patch\n",
        })
        parsed = json.loads(deny_patch.stdout)
        self.assertEqual(parsed["hookSpecificOutput"]["permissionDecision"], "deny")

        # Relative path from subdirectory cwd targeting root .env must be blocked
        deny_rel = self.p.hook("guard", {
            "session_id": "s1",
            "cwd": str(self.p.root / "shop"),
            "hook_event_name": "PreToolUse",
            "tool_name": "Edit",
            "tool_input": {"file_path": "../.env.local"},
        })
        self.assertEqual(json.loads(deny_rel.stdout)["hookSpecificOutput"]["permissionDecision"], "deny")


class ScriptsTest(unittest.TestCase):
    def setUp(self):
        self.p = Project()
        self.p.hook("session_start", {"source": "startup"})

    def tearDown(self):
        self.p.close()

    def test_module_card_check_in_kb_check(self):
        res = self.p.py("kb_check.py")
        self.assertIn("主要模块缺少卡片：shop", res.stdout)
        self.p.write("docs/knowledge/modules/shop.md", "# 模块：shop\n")
        self.p.write("docs/knowledge/INDEX.md", "# 知识库索引\n- [shop](modules/shop.md)\n")
        res2 = self.p.py("kb_check.py")
        self.assertNotIn("主要模块缺少卡片：shop", res2.stdout)

    def test_verify_pass_fail_and_log_pruning(self):
        self.assertIn("unittest", self.p.py("verify.py", "--detect").stdout)
        ok = self.p.py("verify.py")
        self.assertEqual(ok.returncode, 0, ok.stdout)
        self.assertTrue((self.p.state()["verify"] or {})["ok"])

        self.p.write("tests/test_broken.py", "import unittest\nclass T(unittest.TestCase):\n    def test_x(self): self.fail('boom')\n")
        bad = self.p.py("verify.py")
        self.assertEqual(bad.returncode, 1)
        self.assertIn("boom", bad.stdout)
        self.assertFalse((self.p.state()["verify"] or {})["ok"])

    def test_verify_uses_configured_steps(self):
        cfg = json.loads((self.p.root / ".harness/project.json").read_text())
        cfg["verify"] = [{"name": "echo", "cmd": "echo configured"},
                         {"name": "slow", "cmd": "exit 1", "optional": True}]
        (self.p.root / ".harness/project.json").write_text(json.dumps(cfg))
        self.assertEqual(self.p.py("verify.py").returncode, 0)
        self.assertEqual(self.p.py("verify.py", "--all").returncode, 1)

    def test_kb_check_catches_rot(self):
        self.assertEqual(self.p.py("kb_check.py").returncode, 0)

        self.p.write("docs/knowledge/modules/shop.md",
                     "# shop\n- 见 [不存在](missing.md)\n- 证据 `shop/gone.py:3`\n- **最后验证**：2020-01-01\n")
        out = self.p.py("kb_check.py")
        self.assertEqual(out.returncode, 1)
        self.assertIn("断链", out.stdout)
        self.assertIn("未登记到 INDEX.md", out.stdout)
        self.assertIn("shop/gone.py", out.stdout)
        self.assertIn("2020-01-01", out.stdout)

        self.p.write("docs/knowledge/pitfalls.md",
                     "\n### P001 · 金额必须用分\n- **证据**：`shop/pricing.py:1`\n- **命中**：3 ｜ **最后验证**：2026-10-01\n",
                     append=True)
        self.assertIn("pitfalls#P001 已命中 3 次", self.p.py("kb_check.py").stdout)

    def test_kb_check_line_links_and_preexisting_docs(self):
        self.p.write("docs/knowledge/modules/shop.md", "# shop\n- [折扣](../../../shop/pricing.py:4)\n")
        self.p.write("docs/knowledge/INDEX.md", "\n- [shop](modules/shop.md)\n", append=True)
        self.p.write("docs/legacy/notes.md", "[gone](../../shop/removed.py)\n")
        out = self.p.py("kb_check.py")
        self.assertEqual(out.returncode, 0, out.stdout)
        self.assertIn("项目原有文档中有", out.stdout)
        self.assertIn("docs/legacy/notes.md：1 个", out.stdout)
        self.p.write("docs/knowledge/modules/shop.md", "[gone](../../../shop/removed.py)\n", append=True)
        self.assertEqual(self.p.py("kb_check.py").returncode, 1)

    def test_symbol_anchors_replace_line_numbers(self):
        self.p.write("docs/knowledge/modules/shop.md", "# shop\n- 折扣入口 `shop/pricing.py:4`\n")
        self.p.write("docs/knowledge/INDEX.md", "\n- [shop](modules/shop.md)\n", append=True)
        self.assertIn("1 处行号引用", self.p.py("kb_check.py").stdout)

        fixed = self.p.py("kb_anchors.py", "--fix")
        self.assertEqual(fixed.returncode, 0, fixed.stdout)
        card = (self.p.root / "docs/knowledge/modules/shop.md").read_text()
        self.assertIn("`shop/pricing.py#apply_discount`", card)
        out = self.p.py("kb_check.py").stdout
        self.assertNotIn("行号引用", out)
        self.assertNotIn("符号锚点失效", out)

        source = self.p.root / "shop/pricing.py"
        source.write_text(source.read_text().replace("def apply_discount", "def discounted_total"))
        self.assertIn("符号锚点失效（符号已改名或删除）→ shop/pricing.py#apply_discount",
                      self.p.py("kb_check.py").stdout)

    def test_kb_check_enforces_architecture_deps_and_banned_patterns_without_false_positives(self):
        self.p.write("docs/ARCHITECTURE.md", "\n- 禁止依赖: `shop/pricing.py` -> `shop/orders.py`\n"
                                             "- 禁止依赖: `other_pkg` -> `shop/orders.py`\n", append=True)
        self.p.write("docs/knowledge/patterns.md",
                     "\n- 禁用正则: `\\bprint\\s*\\(` | 范围: `shop/*.py` | 原因: 业务代码禁止裸写 print\n",
                     append=True)
        # Docstring mention and unrelated relative import must NOT false-positive
        self.p.write("shop/pricing.py", '\n"""Example:\nfrom shop import orders\n"""\n', append=True)
        self.p.write("other_pkg/__init__.py", "")
        self.p.write("other_pkg/orders.py", "X = 1\n")
        self.p.write("other_pkg/service.py", "from . import orders\n")
        self.assertEqual(self.p.py("kb_check.py").returncode, 0)

        self.p.write("shop/pricing.py", "\nfrom shop import orders\nprint('debug')\n", append=True)
        out = self.p.py("kb_check.py")
        self.assertEqual(out.returncode, 1, out.stdout)
        self.assertIn("架构分层违规（DEPS）— 禁止 `shop/pricing.py` 依赖 `shop/orders.py`", out.stdout)
        self.assertIn("代码规约违规（命中 `\\bprint\\s*\\(`）— 业务代码禁止裸写 print", out.stdout)

    def test_task_finish_telemetry_auto_modules_and_evolve_threshold(self):
        cfg = json.loads((self.p.root / ".harness/project.json").read_text())
        cfg["evolve_every_n_tasks"] = 2
        (self.p.root / ".harness/project.json").write_text(json.dumps(cfg))

        transcript = self.p.root / ".harness/.state/mock-transcript.jsonl"
        transcript.write_text(
            json.dumps({"tool_calls": [{"name": "read_file"}, {"name": "edit_file"}]}) + "\n"
            + json.dumps({"message": {"content": [{"type": "tool_use", "name": "Bash"}]}}) + "\n"
            + json.dumps({"type": "function_call", "name": "shell"}) + "\n"
        )
        self.p.hook("session_start", {"session_id": "tel", "transcriptPath": str(transcript)})
        # Append 3 new tool calls during the active task
        with transcript.open("a") as f:
            f.write(json.dumps({"tool_calls": [{"name": "a"}, {"name": "b"}]}) + "\n")
            f.write(json.dumps({"type": "function_call", "name": "c"}) + "\n")

        self.p.define()
        self.p.write("shop/pricing.py", "\n# task 1 edit\n", append=True)
        self.p.hook("stop_gate", {"session_id": "tel"})  # 1 stop block recorded
        fin1 = self.p.py("task.py", "finish", "--type", "bugfix", "--friction", "找不到折扣逻辑")
        self.assertEqual(fin1.returncode, 0, fin1.stderr)
        self.assertNotIn("进化阈值", fin1.stdout)

        entry1 = json.loads((self.p.root / ".harness/evolution/tasks.jsonl").read_text().splitlines()[-1])
        self.assertEqual(entry1["modules"], ["shop"])
        self.assertEqual(entry1["metrics"]["tool_calls"], 3)
        self.assertEqual(entry1["metrics"]["stop_blocks"], 1)
        self.assertEqual(entry1["metrics"]["verify_runs"], 1)

        self.p.define(new=True)
        self.p.write("shop/orders.py", "\n# task 2 edit\n", append=True)
        fin2 = self.p.py("task.py", "finish", "--type", "bugfix", "--friction", "找不到折扣逻辑")
        self.assertIn("进化阈值", fin2.stdout)

        stats = self.p.py("task.py", "stats").stdout
        self.assertIn("找不到折扣逻辑", stats)
        self.assertIn("shop×2", stats)
        self.assertIn("Stop 拦截累计 1 次", stats)
        self.p.py("task.py", "mark-evolved")
        self.assertIn("自上次进化以来：0 个任务", self.p.py("task.py", "stats").stdout)


class VerifyResearchTest(unittest.TestCase):
    def setUp(self):
        self.p = Project()

    def tearDown(self):
        self.p.close()

    def test_survey_collects_evidence_and_filters_noise(self):
        self.p.write(".github/workflows/ci.yml",
                     "jobs:\n  test:\n    steps:\n      - run: make lint\n      - run: python3 -m pytest -q\n")
        self.p.write("Makefile", "lint:\n\truff check .\ntest:\n\tpytest\n")
        self.p.write("CONTRIBUTING.md", "```\nmake test\nscripts/   # helper scripts\nOPT_TESTS = ON\n```\n"
                                         "Run `npm run lint` before pushing.\n")
        self.p.write("scripts/run_tests.sh", "#!/bin/sh\n")
        out = self.p.py("verify.py", "--survey").stdout
        self.assertIn(".github/workflows/ci.yml:4: - run: make lint", out)
        self.assertIn("Makefile 目标: lint, test", out)
        self.assertIn("CONTRIBUTING.md:2: make test", out)
        self.assertIn("CONTRIBUTING.md:6: npm run lint", out)
        self.assertIn("scripts/run_tests.sh", out)
        self.assertNotIn("helper scripts", out)
        self.assertNotIn("OPT_TESTS", out)

    def test_cmake_detection(self):
        self.p.write("CMakeLists.txt", "project(x)\nenable_testing()\nadd_test(NAME t COMMAND t)\n")
        sh("git add -A", self.p.root)
        steps = json.loads(self.p.py("verify.py", "--detect").stdout)
        names = [s["name"] for s in steps]
        self.assertEqual(names[-3:], ["configure", "build", "ctest"])
        self.assertIn("add_test × 1", self.p.py("verify.py", "--survey").stdout)

    def test_calibrate_times_steps_and_advises(self):
        cfg = json.loads((self.p.root / ".harness/project.json").read_text())
        cfg["slow_step_seconds"] = 0.5
        (self.p.root / ".harness/project.json").write_text(json.dumps(cfg))
        self.p.write(".harness/.state/verify-candidates.json", json.dumps([
            {"name": "fast", "cmd": "true", "source": "test"},
            {"name": "slow", "cmd": "sleep 1"},
            {"name": "broken", "cmd": "echo nope; exit 3"},
        ]))
        out = self.p.py("verify.py", "--calibrate", "--candidates", ".harness/.state/verify-candidates.json")
        self.assertEqual(out.returncode, 1)
        report = {r["name"]: r for r in json.loads((self.p.root / ".harness/.state/calibration.json").read_text())}
        self.assertEqual(report["fast"]["code"], 0)
        self.assertTrue(any("optional" in a for a in report["slow"]["advice"]))
        self.assertEqual(report["broken"]["code"], 3)
        self.assertIn("nope", report["broken"]["tail"])


class GateTest(unittest.TestCase):
    def setUp(self):
        self.p = Project()
        self.p.hook("session_start", {"source": "startup"})
        self.p.define()
        self.p.write("shop/pricing.py", "\n# change\n", append=True)

    def tearDown(self):
        self.p.close()

    def reason(self):
        out = self.p.hook("stop_gate", {}).stdout.strip()
        return json.loads(out)["reason"] if out else ""

    def test_off_and_on_verify_gate(self):
        self.assertNotEqual(self.p.py("gate.py", "off", "verify").returncode, 0, "reason is required")
        self.assertEqual(self.p.py("gate.py", "off", "verify", "--reason", "用户：本项目不需要校验").returncode, 0)
        r = self.reason()
        self.assertNotIn("verify.py", r)
        self.assertIn("learn.md", r)
        cfg = json.loads((self.p.root / ".harness/project.json").read_text())
        self.assertFalse(cfg["gates"]["verify"])
        self.assertEqual(cfg["gate_history"][-1]["reason"], "用户：本项目不需要校验")
        self.assertIn("已关闭（项目级）：用户：本项目不需要校验", self.p.py("gate.py", "status").stdout)
        self.assertIn("已按用户要求关闭关卡：verify", self.p.hook("session_start", {"source": "startup"}).stdout)

        self.p.py("gate.py", "on", "verify")
        self.p.write("shop/pricing.py", "\n# again\n", append=True)
        self.assertIn("verify.py", self.reason())

    def test_skip_is_session_scoped_and_recorded(self):
        self.p.py("gate.py", "skip", "verify", "--reason", "用户：这次只改注释")
        self.p.py("gate.py", "skip", "learn", "--reason", "用户：不用沉淀")
        self.p.py("task.py", "finish", "--type", "other", "--summary", "comment", "--outcome", "partial")
        self.assertEqual(self.reason(), "")
        task = json.loads((self.p.root / ".harness/evolution/tasks.jsonl").read_text().splitlines()[-1])
        self.assertEqual(task["gates_skipped"]["verify"], "用户：这次只改注释")
        self.assertIn("跳过关卡", self.p.py("task.py", "stats").stdout)

        self.p.define(new=True)
        self.p.write("shop/orders.py", "\n# change\n", append=True)
        self.assertIn("verify.py", self.reason())

    def test_gate_script_requires_permission_prompt(self):
        allow = json.loads((self.p.root / ".claude/settings.json").read_text())["permissions"]["allow"]
        self.assertFalse(any("gate.py" in a for a in allow))
        self.assertFalse(any("record_task.py" in a for a in allow))


class HookLifecycleTest(unittest.TestCase):
    def run_lifecycle(self, p: Project):
        brief = p.hook("session_start", {"source": "startup"}).stdout
        self.assertIn("Harness 简报", brief)
        self.assertIn("知识库尚未初始化", brief)
        self.assertEqual(p.hook("stop_gate", {}).stdout.strip(), "", "no changes -> no block")
        p.define()

        p.write("shop/pricing.py", "\n# change\n", append=True)

        block = json.loads(p.hook("stop_gate", {"stop_hook_active": False}).stdout)
        self.assertEqual(block["decision"], "block")
        self.assertIn("verify.py", block["reason"])
        self.assertIn("learn.md", block["reason"])
        res_forced = p.hook("stop_gate", {"stop_hook_active": True}).stdout.strip()
        self.assertEqual(res_forced, "")

        p.py("verify.py")
        block = json.loads(p.hook("stop_gate", {}).stdout)
        self.assertNotIn("需运行 verify.py", block["reason"])
        self.assertIn("learn.md", block["reason"])

        p.accept_all()
        self.assertEqual(p.hook("stop_gate", {}).stdout.strip(), "")
        task = json.loads((p.root / ".harness/evolution/tasks.jsonl").read_text().splitlines()[-1])
        self.assertIn("summary", task)

        p.write("shop/orders.py", "\n# change 2\n", append=True)
        out = p.hook("stop_gate", {}).stdout
        self.assertIn("verify.py", json.loads(out)["reason"])

    def test_git_project_detects_shell_edits(self):
        p = Project(git=True)
        try:
            self.run_lifecycle(p)
        finally:
            p.close()

    def test_non_git_project_detects_edits(self):
        p = Project(git=False)
        try:
            self.run_lifecycle(p)
        finally:
            p.close()

    def test_docs_only_change_does_not_block(self):
        p = Project()
        try:
            p.hook("session_start", {"source": "startup"})
            p.write("docs/knowledge/glossary.md", "| 订单 | Order | shop/orders.py | |\n", append=True)
            self.assertEqual(p.hook("stop_gate", {}).stdout.strip(), "")
        finally:
            p.close()

    def test_code_graph_files_are_not_code(self):
        p = Project()
        try:
            p.hook("session_start", {"source": "startup"})
            p.write("codegraph.json", '{"exclude": [".harness/"]}\n')
            p.write(".codegraph/.gitignore", "*\n!.gitignore\n")
            self.assertEqual(p.hook("stop_gate", {}).stdout.strip(), "")
        finally:
            p.close()

    def test_edits_outside_the_project_are_allowed_and_ignored(self):
        p = Project()
        try:
            p.hook("session_start", {"source": "startup"})
            with tempfile.TemporaryDirectory() as other:
                outside = Path(other) / ".env"
                outside.write_text("x = 1\n")
                guard_res = p.hook("guard", {"tool_name": "Edit", "tool_input": {"file_path": str(outside)}})
                self.assertEqual(guard_res.stdout.strip(), "")
            self.assertEqual(p.hook("stop_gate", {}).stdout.strip(), "")
        finally:
            p.close()

    def test_paused_task_may_end_turn_until_code_changes_again(self):
        p = Project()
        try:
            p.hook("session_start", {"source": "startup"})
            p.write("shop/pricing.py", "\n# change\n", append=True)
            p.define()
            reason = json.loads(p.hook("stop_gate", {}).stdout)["reason"]
            self.assertIn("task.py pause", reason)

            p.py("task.py", "pause", "等用户截图")
            self.assertEqual(p.state()["closed"]["reason"], "等用户截图")
            self.assertEqual(p.hook("stop_gate", {}).stdout.strip(), "")
            self.assertFalse((p.root / ".harness/evolution/tasks.jsonl").read_text().strip(),
                             "pausing is not a task record")

            p.write("shop/pricing.py", "\n# change after the pause\n", append=True)
            self.assertEqual(json.loads(p.hook("stop_gate", {}).stdout)["decision"], "block")

            p.accept_all("--type", "bugfix", "--learned", "none")
            self.assertEqual(p.state()["status"], "completed")
            self.assertEqual(p.hook("stop_gate", {}).stdout.strip(), "")
        finally:
            p.close()

    def test_manual_run_without_stdin(self):
        p = Project()
        try:
            out = subprocess.run([sys.executable, ".harness/scripts/hooks/session_start.py"], cwd=p.root,
                                 stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=20)
            self.assertIn("Harness 简报", out.stdout)
        finally:
            p.close()


TOOLS = REPO / "tests" / ".tools"
CODEGRAPH_BIN = TOOLS / "node_modules" / ".bin" / ("codegraph.cmd" if os.name == "nt" else "codegraph")


def real_codegraph() -> str | None:
    """Install the real CodeGraph once (npm, cached in tests/.tools/) and return its path."""
    if os.environ.get("HARNESS_CODEGRAPH_BIN"):
        return os.environ["HARNESS_CODEGRAPH_BIN"]
    if not CODEGRAPH_BIN.exists():
        npm = shutil.which("npm")
        if npm is None:
            return shutil.which("codegraph")
        TOOLS.mkdir(parents=True, exist_ok=True)
        subprocess.run([npm, "install", "--prefix", str(TOOLS), "--no-fund", "--no-audit",
                        "@colbymchenry/codegraph"], check=True, capture_output=True, timeout=600)
    return str(CODEGRAPH_BIN)


class CodeGraphMissingTest(unittest.TestCase):
    def test_missing_tool_degrades_gracefully(self):
        p = Project()
        os.environ["HARNESS_CODEGRAPH_BIN"] = str(p.root / "nope")
        try:
            out = p.py("codegraph.py", "explore", "discount")
            self.assertEqual(out.returncode, 2)
            self.assertIn("codegraph.py setup", out.stdout)
            self.assertEqual(p.py("codegraph.py", "doctor").returncode, 1)
            self.assertIn("代码图谱（CodeGraph）：未安装", p.hook("session_start", {"source": "startup"}).stdout)
            self.assertIn("代码图谱未初始化", p.py("kb_check.py").stdout)
        finally:
            os.environ.pop("HARNESS_CODEGRAPH_BIN", None)
            p.close()

    def test_sync_failure_does_not_return_stale_query_results(self):
        p = Project()
        previous = os.environ.get("HARNESS_CODEGRAPH_BIN")
        try:
            fake = p.root / ".harness/.state/fake-codegraph"
            fake.parent.mkdir(parents=True, exist_ok=True)
            fake.write_text(f"#!{sys.executable}\nimport sys\n"
                            "if sys.argv[1] == 'sync':\n    print('sync failed')\n    sys.exit(1)\n"
                            "print('STALE RESULT MUST NOT BE RETURNED')\n")
            fake.chmod(0o755)
            (p.root / ".codegraph").mkdir()
            os.environ["HARNESS_CODEGRAPH_BIN"] = str(fake)
            result = p.py("codegraph.py", "query", "apply_discount")
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertIn("同步失败", result.stdout)
            self.assertNotIn("STALE RESULT MUST NOT BE RETURNED", result.stdout)
        finally:
            if previous is None:
                os.environ.pop("HARNESS_CODEGRAPH_BIN", None)
            else:
                os.environ["HARNESS_CODEGRAPH_BIN"] = previous
            p.close()


class CodeGraphRealTest(unittest.TestCase):
    """Runs the real CodeGraph CLI against the demo app (no fakes)."""

    @classmethod
    def setUpClass(cls):
        cls.bin = real_codegraph()
        if not cls.bin:
            raise unittest.SkipTest("CodeGraph unavailable: npm not installed and codegraph not on PATH")

    def setUp(self):
        self.p = Project()
        self._prev = os.environ.get("HARNESS_CODEGRAPH_BIN")
        os.environ["HARNESS_CODEGRAPH_BIN"] = self.bin
        init = self.cg("init")
        self.assertEqual(init.returncode, 0, init.stdout + init.stderr)
        self.init_output = init.stdout

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("HARNESS_CODEGRAPH_BIN", None)
        self.p.close()

    def cg(self, *args):
        return self.p.py("codegraph.py", *args)

    def test_init_indexes_only_project_code(self):
        self.assertTrue((self.p.root / ".codegraph/codegraph.db").exists())
        excludes = json.loads((self.p.root / "codegraph.json").read_text())["exclude"]
        self.assertIn(".harness/", excludes)
        self.assertRegex(self.init_output, r"Files:\s+4\b")
        untracked = sh("git status --porcelain --untracked-files=all .codegraph", self.p.root).stdout.split()
        self.assertEqual(untracked, ["??", ".codegraph/.gitignore"])

    def test_structural_queries_and_graph_backed_deps_and_cycles(self):
        q = self.cg("query", "apply_discount").stdout
        self.assertIn("shop/pricing.py:4", q)
        callers = self.cg("callers", "apply_discount").stdout
        self.assertIn("total_cents", callers)
        self.assertIn("test_discount", callers)
        impact = self.cg("impact", "apply_discount").stdout
        self.assertIn("shop/orders.py", impact)
        self.assertIn("tests/test_shop.py", impact)
        self.assertIn("shop/orders.py", self.cg("explore", "order total discount").stdout)

        # Graph-backed DEPS catches real edges & detects cross-module cycles
        self.p.write("billing/invoice.py", "from shop.orders import Order\ndef bill(o): return o.total_cents()\n")
        self.p.write("shop/orders.py", "\nfrom billing.invoice import bill\ndef notify(o): return bill(o)\n", append=True)
        self.cg("sync")
        kb = self.p.py("kb_check.py").stdout
        self.assertIn("模块级环形依赖：billing ↔ shop", kb)

        self.p.write("docs/ARCHITECTURE.md", "\n- 禁止依赖: `billing` -> `shop`\n", append=True)
        kb_err = self.p.py("kb_check.py")
        self.assertEqual(kb_err.returncode, 1)
        self.assertIn("架构分层违规（DEPS）— 禁止 `billing` 依赖 `shop`", kb_err.stdout)

    def test_anchor_fix_uses_enclosing_symbol(self):
        self.p.write("docs/knowledge/pitfalls.md",
                     "\n### P001 · 折扣百分比越界会抛异常\n- **证据**：`shop/pricing.py:7`\n"
                     "- **命中**：1 ｜ **最后验证**：2026-10-26\n", append=True)
        self.assertEqual(self.p.py("kb_anchors.py", "--fix").returncode, 0)
        self.assertIn("`shop/pricing.py#apply_discount`", (self.p.root / "docs/knowledge/pitfalls.md").read_text())
        self.assertEqual(self.p.py("kb_anchors.py", "--check").stdout.strip(), "")

    def test_incremental_update_and_affected(self):
        self.p.hook("session_start", {"source": "startup"})
        self.p.write("shop/pricing.py",
                     "\n\ndef shipping_fee(total_cents: int) -> int:\n"
                     "    return 0 if cart_total([(total_cents, 1)]) >= 9900 else 800\n", append=True)
        self.assertIn("shop/pricing.py:", self.cg("query", "shipping_fee").stdout)
        self.assertIn("cart_total", self.cg("callees", "shipping_fee").stdout)
        self.assertIn("tests/test_shop.py", self.cg("affected").stdout)

    def test_session_start_and_stop_gate_keep_graph_fresh(self):
        self.assertIn("代码图谱（CodeGraph）：已同步", self.p.hook("session_start", {"source": "startup"}).stdout)
        self.p.define()
        self.p.write("shop/orders.py", "\n\ndef order_count(orders: list) -> int:\n    return len(orders)\n",
                     append=True)
        self.p.accept_all()
        before = self.p.project_state().get("codegraph_synced_at", 0)
        self.assertEqual(self.p.hook("stop_gate", {}).stdout.strip(), "")
        self.assertGreater(self.p.project_state()["codegraph_synced_at"], before)
        raw = sh([self.bin, "query", "order_count"], self.p.root).stdout
        self.assertIn("shop/orders.py", raw)

    def test_git_hooks_sync_after_checkout(self):
        hook = self.p.root / ".git/hooks/post-merge"
        hook.write_text("#!/bin/sh\necho existing\n")
        self.cg("install-git-hooks")
        self.cg("install-git-hooks")
        self.assertIn("echo existing", hook.read_text())
        self.assertEqual(hook.read_text().count("harness codegraph sync >>>"), 1)
        self.assertEqual(self.cg("doctor").returncode, 0)

        git_c = "git -c user.email=t@t -c user.name=t"
        sh(f"git add -A && {git_c} commit -qm harness && git checkout -qb feature", self.p.root, check=True)
        self.p.write("shop/refunds.py", "def refund(order):\n    order.cancel()\n")
        sh(f"git add -A && {git_c} commit -qm refunds && git checkout -q master", self.p.root, check=True)
        before = self.p.project_state().get("codegraph_synced_at", 0)
        sh("git checkout -q feature", self.p.root, check=True)
        for _ in range(40):
            if self.p.project_state().get("codegraph_synced_at", 0) > before:
                break
            time.sleep(0.25)
        self.assertGreater(self.p.project_state().get("codegraph_synced_at", 0), before, "post-checkout hook did not sync")
        self.assertIn("shop/refunds.py", sh([self.bin, "query", "refund"], self.p.root).stdout)


class TemplateIntegrityTest(unittest.TestCase):
    def test_template_kb_has_no_errors_and_agents_md_within_lean_budget(self):
        p = Project()
        try:
            self.assertEqual(p.py("kb_check.py").returncode, 0)
            self.assertLessEqual(len((p.root / "AGENTS.md").read_text().splitlines()), 80)
            for skill in ("feature", "fix", "learn", "evolve", "onboard"):
                for folder in (".claude/skills", ".agents/skills"):
                    text = (p.root / f"{folder}/{skill}/SKILL.md").read_text()
                    self.assertTrue(text.startswith("---\nname: "), f"{folder}/{skill}")
                    wf = text.split("workflows/")[1].split(".md")[0]
                    self.assertTrue((p.root / f".harness/workflows/{wf}.md").exists(), wf)
        finally:
            p.close()


if __name__ == "__main__":
    unittest.main()
