import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]


def run_hook(name, cwd, payload, env=None):
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    return subprocess.run(
        ["bash", str(ROOT / name)],
        cwd=cwd,
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=True,
        env=merged_env,
    )


def context(result):
    if not result.stdout.strip():
        return ""
    return json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]


class ScryHookTests(unittest.TestCase):
    def test_manifests_and_hook_contract_are_dual_client_compatible(self):
        claude = json.loads((ROOT / ".claude-plugin/plugin.json").read_text())
        codex = json.loads((ROOT / ".codex-plugin/plugin.json").read_text())
        hooks = json.loads((ROOT / "hooks/hooks.json").read_text())
        marketplace = json.loads((ROOT / ".agents/plugins/marketplace.json").read_text())

        self.assertEqual(claude["name"], codex["name"])
        self.assertEqual(claude["version"], codex["version"])
        commands = [
            item["command"]
            for group in hooks["hooks"]["SessionStart"]
            for item in group["hooks"]
        ]
        self.assertEqual(len(commands), 4)
        self.assertTrue(all("PLUGIN_ROOT" in command for command in commands))
        self.assertTrue(all("CLAUDE_PLUGIN_ROOT" in command for command in commands))
        entry = marketplace["plugins"][0]
        self.assertEqual(entry["name"], "scry")
        self.assertEqual(entry["source"], {"source": "local", "path": "./"})
        self.assertEqual(entry["policy"]["installation"], "AVAILABLE")
        self.assertEqual(entry["policy"]["authentication"], "ON_INSTALL")

    def test_all_hooks_fail_open_and_emit_valid_session_start_json(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            repo = base / "repo"
            repo.mkdir()
            (repo / "src").mkdir()
            subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
            payload = {"cwd": str(repo), "session_id": "contract-test"}
            for hook in ("architecture.sh", "health.sh", "fleet.sh", "pressure.sh"):
                with self.subTest(hook=hook):
                    result = run_hook(
                        hook,
                        repo,
                        payload,
                        {"HOME": str(base), "CODEX_HOME": str(base / ".codex")},
                    )
                    self.assertEqual(result.returncode, 0)
                    if result.stdout.strip():
                        output = json.loads(result.stdout)
                        specific = output.get("hookSpecificOutput", {})
                        self.assertEqual(specific.get("hookEventName"), "SessionStart")
                        self.assertTrue(specific.get("additionalContext"))

    def test_fleet_finds_codex_session_and_subagent_and_excludes_self(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            repo = base / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            codex_home = base / ".codex"
            day = codex_home / "sessions/2026/08/05"
            day.mkdir(parents=True)

            def rollout(name, sid, thread_source):
                path = day / f"rollout-{name}-{sid}.jsonl"
                meta = {
                    "type": "session_meta",
                    "payload": {
                        "id": sid,
                        "cwd": str(repo),
                        "thread_source": thread_source,
                        "source": "cli" if thread_source == "user" else {"subagent": {"other": "test"}},
                    },
                }
                path.write_text(json.dumps(meta) + "\n")
                os.utime(path, (time.time(), time.time()))
                return path

            current = rollout("current", "self-session", "user")
            rollout("other", "other-session", "user")
            rollout("subagent", "subagent-session", "subagent")
            claude_dir = base / ".claude/projects" / re.sub(r"[/._]", "-", str(repo.resolve()))
            claude_dir.mkdir(parents=True)
            claude_rollout = claude_dir / "claude-other.jsonl"
            claude_rollout.write_text("{}\n")
            os.utime(claude_rollout, (time.time(), time.time()))

            result = run_hook(
                "fleet.sh",
                repo,
                {
                    "cwd": str(repo),
                    "session_id": "self-session",
                    "transcript_path": str(current),
                },
                {"CODEX_HOME": str(codex_home), "HOME": str(base)},
            )
            report = context(result)
            self.assertIn("1 other Claude session(s)", report)
            self.assertIn("1 other Codex session(s)", report)
            self.assertIn("1 Codex subagent", report)
            self.assertIn("COLLISION RISK: 3 of them are", report)

    def test_fleet_is_silent_for_only_current_codex_session(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            repo = base / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            codex_home = base / ".codex"
            day = codex_home / "sessions/2026/08/05"
            day.mkdir(parents=True)
            current = day / "rollout-self-session.jsonl"
            current.write_text(json.dumps({
                "type": "session_meta",
                "payload": {"id": "self-session", "cwd": str(repo), "thread_source": "user"},
            }) + "\n")

            result = run_hook(
                "fleet.sh",
                repo,
                {
                    "cwd": str(repo),
                    "session_id": "self-session",
                    "transcript_path": str(current),
                },
                {"CODEX_HOME": str(codex_home), "HOME": str(base)},
            )
            self.assertNotIn("Codex session(s)", context(result))


if __name__ == "__main__":
    unittest.main()
