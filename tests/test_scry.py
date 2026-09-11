import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
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
    def test_health_reports_stale_main_without_changing_checkout(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            origin = base / "origin"
            repo = base / "repo"

            def git(cwd, *args):
                return subprocess.run(
                    ["git", "-C", str(cwd), *args], check=True,
                    capture_output=True, text=True,
                ).stdout.strip()

            origin.mkdir()
            git(origin, "init", "-q", "-b", "main")
            git(origin, "config", "user.name", "Scry Test")
            git(origin, "config", "user.email", "scry@example.test")
            (origin / "tracked.txt").write_text("original\n")
            git(origin, "add", ".")
            git(origin, "commit", "-qm", "Initial")
            git(base, "clone", "-q", str(origin), str(repo))
            original_main = git(repo, "rev-parse", "main")
            (origin / "tracked.txt").write_text("remote update\n")
            git(origin, "commit", "-qam", "Advance remote")

            report = context(run_hook("health.sh", repo, {"cwd": str(repo)}))

            self.assertIn("Local main is 1 commit(s) / 0 day(s) behind origin/main",
                          report)
            # The count alone fired during the 2026-09-09 incident and changed
            # nothing. The line has to carry the consequence and the command,
            # and has to say that a subagent will not see any of it.
            self.assertIn("ABSENT from disk here", report)
            self.assertIn("show origin/main:<path>", report)
            self.assertIn("Subagents do NOT inherit this warning", report)
            self.assertEqual(git(repo, "rev-parse", "origin/main"),
                             git(origin, "rev-parse", "main"))
            self.assertEqual(git(repo, "rev-parse", "main"), original_main)
            self.assertEqual(git(repo, "rev-parse", "HEAD"), original_main)
            self.assertEqual((repo / "tracked.txt").read_text(), "original\n")
            self.assertEqual(git(repo, "status", "--porcelain"), "")

    def test_provenance_treats_markdown_decisions_as_untrusted_and_explains_consequences(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
            (repo / "DESIGN.md").write_text("The ingress owns provider verification.\n")

            report = context(run_hook("provenance.sh", repo, {}))
            lower_report = report.lower()

            self.assertIn("UNTRUSTED HISTORICAL MATERIAL", report)
            self.assertIn("architectural decisions", report)
            self.assertIn("stale architecture", lower_report)
            self.assertIn("aspirational plan", lower_report)
            self.assertIn("conflicting artifacts", lower_report)
            self.assertIn("code quality", report)
            self.assertIn("tokens", report)
            self.assertIn("user trust", report)
            self.assertIn("customer", report)
            self.assertNotIn("decisions and principles in them age fine", report)

    def test_markdown_read_advisory_rejects_architectural_authority_and_hedging(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)

            report = context(
                run_hook(
                    "md_advisory.sh",
                    repo,
                    {"tool_name": "Read", "tool_input": {"file_path": str(repo / "DESIGN.md")}},
                )
            )

            self.assertIn("UNTRUSTED HISTORICAL MATERIAL", report)
            self.assertIn("architecture, intent, or decisions", report)
            self.assertIn("cannot establish truth or authority", report)
            self.assertIn("Calling a claim unverified while relying on it", report)
            self.assertIn("surface the conflict", report)

    def test_markdown_read_advisory_is_shorter_than_session_start_insight(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
            (repo / "DESIGN.md").write_text("Historical architecture.\n")

            session_report = context(run_hook("provenance.sh", repo, {}))
            read_report = context(
                run_hook(
                    "md_advisory.sh",
                    repo,
                    {"tool_name": "Read", "tool_input": {"file_path": str(repo / "DESIGN.md")}},
                )
            )

            self.assertLess(len(read_report), len(session_report))

    def test_provenance_flags_snapshot_language_in_markdown_paths_with_spaces(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
            (repo / "Design Notes.md").write_text("The old ingress is currently deployed.\n")

            report = context(run_hook("provenance.sh", repo, {}))

            self.assertIn("Design Notes.md", report)

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
        self.assertEqual(len(commands), 6)
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

    def test_fleet_after_clear_states_four_facts_from_the_end_record(self):
        # /clear ends the session and starts a new one with source "clear".
        # clear_record.sh wrote the ending session down; fleet.sh reads that
        # record for this directory, deletes it, and states four facts: the
        # session left, its transcript, what resuming it costs, and whether a
        # handoff exists (path only). Insight, not action (council + Matt,
        # 2026-09-10). Never the transcript's prompts, never the handoff body.
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            repo = base / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            claude_dir = base / ".claude/projects" / re.sub(r"[/._]", "-", str(repo.resolve()))
            claude_dir.mkdir(parents=True)
            prev = claude_dir / "0123abcd-prev-session.jsonl"
            prev.write_text(json.dumps({"type": "user", "message": "SECRET PROMPT"}) + "\n"
                            + json.dumps({"type": "ai-title", "aiTitle": "Scry cache TTL handoff design"}) + "\n")
            # A newer transcript from ANOTHER session in the same directory —
            # the case the mtime guess gets wrong.
            other = claude_dir / "other-live-session.jsonl"
            other.write_text(json.dumps({"type": "ai-title", "aiTitle": "Unrelated work"}) + "\n")
            os.utime(prev, (time.time() - 30, time.time() - 30))
            handoffs = base / "handoffs" / "repo"
            handoffs.mkdir(parents=True)
            (handoffs / "20260910-1200-0123abcd.md").write_text("# Handoff\nHANDOFF BODY\n")
            env = {"HOME": str(base), "CODEX_HOME": str(base / ".codex"),
                   "SCRY_CACHE_HANDOFF_DIR": str(base / "handoffs"),
                   "SCRY_CLEAR_STATE_DIR": str(base / "cleared"),
                   "SCRY_CACHE_STATE_DIR": str(base / "deadline")}
            # The old session ends with /clear: clear_record.sh writes it down.
            run_hook("clear_record.sh", repo, {"session_id": "0123abcd-prev-session",
                                              "transcript_path": str(prev), "cwd": str(repo),
                                              "reason": "clear"}, env)
            rec_path = base / "cleared" / "0123abcd-prev-session.json"
            self.assertTrue(rec_path.exists())
            self.assertNotIn("SECRET", rec_path.read_text())
            # Its cache deadline, as the status-line wrapper records it.
            (base / "deadline").mkdir()
            (base / "deadline" / "0123abcd-prev-session.deadline").write_text(json.dumps(
                {"session_id": "0123abcd-prev-session", "observed_at": int(time.time()),
                 "warm": True, "ttl": "1h", "expires_at": int(time.time()) + 1800, "requests": 9}))
            payload = {"cwd": str(repo), "session_id": "new-session",
                       "transcript_path": str(claude_dir / "new-session.jsonl"), "source": "clear"}

            report = context(run_hook("fleet.sh", repo, payload, env))
            self.assertIn("began with /clear", report)
            self.assertIn("recorded as it ended", report)
            self.assertIn('"Scry cache TTL handoff design"', report)
            self.assertIn("id 0123abcd-prev-session", report)
            self.assertIn(f"Its transcript: {prev}", report)
            self.assertRegex(report, r"warm until \d\d:\d\d")
            self.assertIn("claude --resume 0123abcd-prev-session", report)
            self.assertIn("full rate", report)
            self.assertIn(f"A handoff was written for it: {handoffs / '20260910-1200-0123abcd.md'}", report)
            self.assertNotIn("HANDOFF BODY", report)
            self.assertNotIn("SECRET PROMPT", report)
            self.assertNotIn("Unrelated work", report)      # the guess would have said this
            self.assertNotIn("GUESS", report)
            self.assertNotIn("subagent", report.lower())     # facts, not a recipe
            self.assertFalse(rec_path.exists())              # read once, then gone

            # With no record (the SessionEnd hook did not run) the guess stands
            # in, says so, and the cold-cache cost is stated.
            (base / "deadline" / "0123abcd-prev-session.deadline").unlink()
            (handoffs / "20260910-1200-0123abcd.md").unlink()
            report = context(run_hook("fleet.sh", repo, payload, env))
            self.assertIn("a GUESS", report)
            self.assertIn("Unrelated work", report)
            self.assertIn("cold or unrecorded", report)
            self.assertIn("No handoff was written", report)

            # A plain start keeps the old rule: the just-written transcript
            # counts as live and is not reported as the last session.
            report = context(run_hook("fleet.sh", repo, {**payload, "source": "startup"}, env))
            self.assertNotIn("began with /clear", report)
            self.assertNotIn("0123abcd", report)

    def test_fleet_after_clear_names_the_work_the_cleared_session_left_running(self):
        # 2026-09-11: a session was cleared mid-build. The builder it had
        # launched kept running, as subagents do — but its results reported to
        # a session id that takes no more turns, and the replacement session
        # was never handed it. Worse, this hook SAW that builder and filed it
        # under "from other sessions", which reads as somebody else's work. So
        # the replacement launched a second builder on the same branch and the
        # two collided on the same PR. The subagent was always visible; only
        # its owner was wrong. Attribution is the fix.
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            repo = base / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            enc = re.sub(r"[/._]", "-", str(repo.resolve()))
            claude_dir = base / ".claude/projects" / enc
            claude_dir.mkdir(parents=True)
            prev_id = "0123abcd-prev-session"
            prev = claude_dir / f"{prev_id}.jsonl"
            prev.write_text(json.dumps({"type": "ai-title", "aiTitle": "Overnight build"}) + "\n")

            # The builder the cleared session started, still writing here.
            mine = claude_dir / prev_id / "subagents"
            mine.mkdir(parents=True)
            (mine / "builder.jsonl").write_text(
                json.dumps({"cwd": str(repo), "type": "user",
                            "message": "SECRET BUILDER PROMPT"}) + "\n")
            # A genuine stranger: another session's subagent in the same tree.
            stranger = claude_dir / "someone-elses-session" / "subagents"
            stranger.mkdir(parents=True)
            (stranger / "reviewer.jsonl").write_text(
                json.dumps({"cwd": str(repo)}) + "\n")

            # Tasks it registered. A shell task is finished when its output
            # carries the exit marker and unfinished when it does not —
            # emptiness says only that it has printed nothing yet. An agent
            # task's file is a symlink to that agent's transcript, so its
            # liveness is the transcript's mtime.
            tasks = base / "taskroot" / enc / prev_id / "tasks"
            tasks.mkdir(parents=True)
            (tasks / "running.output").write_text("partial output so far\n")
            (tasks / "finished.output").write_text("done\n\n[exited with code 0]\n")
            (tasks / "agent.output").symlink_to(mine / "builder.jsonl")
            stale = claude_dir / prev_id / "subagents" / "old.jsonl"
            stale.write_text(json.dumps({"cwd": str(repo)}) + "\n")
            os.utime(stale, (time.time() - 7200, time.time() - 7200))
            (tasks / "stale-agent.output").symlink_to(stale)

            env = {"HOME": str(base), "CODEX_HOME": str(base / ".codex"),
                   "SCRY_CACHE_HANDOFF_DIR": str(base / "handoffs"),
                   "SCRY_CLEAR_STATE_DIR": str(base / "cleared"),
                   "SCRY_CACHE_STATE_DIR": str(base / "deadline"),
                   "SCRY_TASK_STATE_DIR": str(base / "taskroot")}
            run_hook("clear_record.sh", repo, {"session_id": prev_id,
                                              "transcript_path": str(prev), "cwd": str(repo),
                                              "reason": "clear"}, env)
            payload = {"cwd": str(repo), "session_id": "new-session",
                       "transcript_path": str(claude_dir / "new-session.jsonl"),
                       "source": "clear"}
            report = context(run_hook("fleet.sh", repo, payload, env))

            # Fact five: named as this seat's own, with the consequence.
            self.assertIn("IT LEFT WORK RUNNING", report)
            self.assertIn("1 subagent still writing (in this directory", report)
            # Reported as ids — the handle the runtime itself uses — with the
            # finished shell task and the long-idle agent both left out.
            self.assertIn("2 task(s) it registered that have not recorded an exit: "
                          "agent (agent), running (shell)", report)
            self.assertNotIn("finished", report)
            self.assertNotIn("stale-agent", report)
            self.assertIn("runs twice", report)
            # The stranger is still a stranger, and is counted once, not twice.
            self.assertIn("1 subagent from other sessions is working", report)
            # Both are concurrent writers in this tree, so both are exposure.
            self.assertIn("COLLISION RISK: 2 of them are", report)
            # ...and the session that just ended is not also counted as one of
            # the live neighbours competing for this tree.
            self.assertNotIn("other Claude session(s) active", report)
            # Still metadata only: no prompt from either transcript.
            self.assertNotIn("SECRET BUILDER PROMPT", report)

            # The guess is not good enough to call work your own. With the
            # record consumed, the fifth fact goes silent rather than pointing
            # a reader at agents that may belong to anyone.
            report = context(run_hook("fleet.sh", repo, payload, env))
            self.assertIn("a GUESS", report)
            self.assertNotIn("IT LEFT WORK RUNNING", report)
            self.assertIn("2 subagents from other sessions are working", report)

    def test_fleet_leaves_a_finished_background_job_unreported(self):
        # A job that wrote its result is finished work, not in-flight work,
        # and a line that fired for every cleared session with a tasks
        # directory would stop carrying information (README, output budget).
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            repo = base / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            enc = re.sub(r"[/._]", "-", str(repo.resolve()))
            (base / ".claude/projects" / enc).mkdir(parents=True)
            prev = base / ".claude/projects" / enc / "s-prev.jsonl"
            prev.write_text(json.dumps({"type": "ai-title", "aiTitle": "Quiet session"}) + "\n")
            tasks = base / "taskroot" / enc / "s-prev" / "tasks"
            tasks.mkdir(parents=True)
            (tasks / "finished.output").write_text("done\n\n[exited with code 0]\n")
            env = {"HOME": str(base), "CODEX_HOME": str(base / ".codex"),
                   "SCRY_CACHE_HANDOFF_DIR": str(base / "handoffs"),
                   "SCRY_CLEAR_STATE_DIR": str(base / "cleared"),
                   "SCRY_CACHE_STATE_DIR": str(base / "deadline"),
                   "SCRY_TASK_STATE_DIR": str(base / "taskroot")}
            run_hook("clear_record.sh", repo, {"session_id": "s-prev", "cwd": str(repo),
                                              "reason": "clear", "transcript_path": str(prev)}, env)
            report = context(run_hook("fleet.sh", repo,
                                      {"cwd": str(repo), "session_id": "new",
                                       "transcript_path": str(base / "new.jsonl"),
                                       "source": "clear"}, env))
            self.assertIn("began with /clear", report)
            self.assertNotIn("IT LEFT WORK RUNNING", report)

            # And a client that does not use this layout at all is silence,
            # never a guess (AGENTS.md: never silently wrong).
            env["SCRY_TASK_STATE_DIR"] = str(base / "nothing-here")
            run_hook("clear_record.sh", repo, {"session_id": "s-prev", "cwd": str(repo),
                                              "reason": "clear", "transcript_path": str(prev)}, env)
            report = context(run_hook("fleet.sh", repo,
                                      {"cwd": str(repo), "session_id": "new",
                                       "transcript_path": str(base / "new.jsonl"),
                                       "source": "clear"}, env))
            self.assertNotIn("IT LEFT WORK RUNNING", report)

    def test_clear_record_writes_only_on_clear_and_sweeps_old_records(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            env = {"SCRY_CLEAR_STATE_DIR": str(base / "cleared")}
            stale = base / "cleared" / "stale.json"
            stale.parent.mkdir()
            stale.write_text("{}")
            os.utime(stale, (time.time() - 90000, time.time() - 90000))
            run_hook("clear_record.sh", td, {"session_id": "s1", "cwd": td, "reason": "logout"}, env)
            self.assertFalse((base / "cleared" / "s1.json").exists())
            self.assertFalse(stale.exists())
            run_hook("clear_record.sh", td, {"session_id": "s1", "cwd": td, "reason": "clear",
                                            "transcript_path": "/x/s1.jsonl"}, env)
            rec = json.loads((base / "cleared" / "s1.json").read_text())
            self.assertEqual(set(rec), {"session_id", "transcript_path", "cwd", "ended_at"})

    def test_session_worktree_reports_lock_escape_hatch_and_orphan_exposure(self):
        # 2026-08-10 incident: a session inside a linked worktree spent an
        # hour failing to leave it, and an untracked file in that same
        # worktree (not the primary) had no commit anywhere and would have
        # been unrecoverable on cleanup. Both facts must reach the session
        # unprompted.
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            primary = base / "primary"
            primary.mkdir()
            subprocess.run(["git", "init", "-q", "-b", "main", str(primary)], check=True)
            subprocess.run(["git", "-C", str(primary), "commit", "-q", "--allow-empty", "-m", "init"], check=True)
            linked = base / "linked"
            subprocess.run(
                ["git", "-C", str(primary), "worktree", "add", "-q", "-b", "feature", str(linked)],
                check=True,
            )
            (linked / "orphan.md").write_text("unbacked-up brief\n")

            result = run_hook(
                "health.sh",
                linked,
                {"cwd": str(linked)},
                {"HOME": str(base)},
            )
            report = context(result)
            self.assertIn("THIS SESSION IS LOCKED TO THIS WORKTREE", report)
            self.assertIn("ExitWorktree", report)
            self.assertIn("EXPOSURE: 1 file(s) here exist in NO commit on ANY branch", report)

    def test_session_worktree_warns_against_orchestrating_from_linked_worktree(self):
        # 2026-08-14 incident (trellys-app signup-portal build): a session
        # that starts in a linked worktree arms worktree shell isolation for
        # itself and every subagent it spawns — cross-worktree git refused,
        # compound commands refused, sibling-worktree builders unable to
        # commit. An evening was lost before this was diagnosed. Sibling
        # case to the primary-worktree warning above.
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            primary = base / "primary"
            primary.mkdir()
            subprocess.run(["git", "init", "-q", "-b", "main", str(primary)], check=True)
            subprocess.run(["git", "-C", str(primary), "commit", "-q", "--allow-empty", "-m", "init"], check=True)
            linked = base / "linked"
            subprocess.run(
                ["git", "-C", str(primary), "worktree", "add", "-q", "-b", "feature", str(linked)],
                check=True,
            )

            result = run_hook(
                "health.sh",
                linked,
                {"cwd": str(linked)},
                {"HOME": str(base)},
            )
            report = context(result)
            self.assertIn("THIS SESSION IS INSIDE A LINKED WORKTREE", report)
            self.assertIn("do NOT orchestrate from here", report)
            self.assertIn("isolation: worktree", report)
            self.assertIn("ExitWorktree first", report)

    def test_session_worktree_stays_silent_when_clean(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            primary = base / "primary"
            primary.mkdir()
            subprocess.run(["git", "init", "-q", "-b", "main", str(primary)], check=True)
            subprocess.run(["git", "-C", str(primary), "commit", "-q", "--allow-empty", "-m", "init"], check=True)
            linked = base / "linked"
            subprocess.run(
                ["git", "-C", str(primary), "worktree", "add", "-q", "-b", "feature", str(linked)],
                check=True,
            )

            result = run_hook(
                "health.sh",
                linked,
                {"cwd": str(linked)},
                {"HOME": str(base)},
            )
            self.assertNotIn("EXPOSURE", context(result))

    def test_primary_worktree_orphan_exposure_still_reported_off_main(self):
        # Regression guard for the orphan_file_exposure refactor: the
        # original primary-only check must still fire.
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            repo = base / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
            subprocess.run(["git", "-C", str(repo), "commit", "-q", "--allow-empty", "-m", "init"], check=True)
            subprocess.run(["git", "-C", str(repo), "checkout", "-q", "-b", "sidework"], check=True)
            (repo / "orphan.md").write_text("unbacked-up\n")

            result = run_hook("health.sh", repo, {"cwd": str(repo)}, {"HOME": str(base)})
            report = context(result)
            self.assertIn("EXPOSURE: 1 file(s) here exist in NO commit on ANY branch", report)

    def test_elixir_scanner_flags_missing_deps_and_clears_once_fetched(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            repo = base / "repo"
            (repo / "lib/my_app").mkdir(parents=True)
            (repo / "mix.exs").write_text("defmodule MyApp.MixProject do\nend\n")
            (repo / "lib/my_app/accounts.ex").write_text(
                'defmodule MyApp.Accounts do\n'
                '  @moduledoc """\n'
                "  Users and sessions.\n"
                '  """\n'
                "  use Ash.Domain\n"
                "end\n"
            )

            missing = run_hook("architecture.sh", repo, {}, {"HOME": str(base)})
            report = context(missing)
            self.assertIn("Elixir deps not fetched", report)
            self.assertIn("mix deps.get", report)
            self.assertIn("Ash domains (1, 0 resources)", report)

            (repo / "deps").mkdir()
            fetched = run_hook("architecture.sh", repo, {}, {"HOME": str(base)})
            fetched_report = context(fetched)
            self.assertNotIn("Elixir deps not fetched", fetched_report)
            self.assertIn("Ash domains (1, 0 resources)", fetched_report)

    def test_architecture_finds_mix_projects_below_cwd_not_only_above(self):
        """The monorepo gap: mix.exs in apps/* was invisible to an upward walk.

        architecture.sh used to walk UP from cwd for a project marker, so a
        repo whose mix projects live in apps/* reported a bare folder list
        and the domain map went silently missing. Also pins the pruning:
        a vendored copy under deps/ must not be reported as first-party.
        """
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            for app, domain in (("engine", "Engine.Cases"), ("web", "Web.Accounts")):
                lib = repo / "apps" / app / "lib" / app
                lib.mkdir(parents=True)
                (repo / "apps" / app / "mix.exs").write_text("defmodule M do\nend\n")
                (repo / "apps" / app / "deps").mkdir()
                (lib / "domain.ex").write_text(
                    f'defmodule {domain} do\n'
                    '  @moduledoc """\n  Real first-party domain.\n  """\n'
                    "  use Ash.Domain\n  resource Foo\n  resource Bar\nend\n"
                )
            # A vendored dependency that must be pruned, not reported.
            vend = repo / "apps/engine/deps/ash/lib"
            vend.mkdir(parents=True)
            (repo / "apps/engine/deps/ash/mix.exs").write_text("defmodule A do\nend\n")
            (vend / "vendored.ex").write_text(
                'defmodule Ash.Vendored do\n  use Ash.Domain\nend\n'
            )
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)

            report = context(run_hook("architecture.sh", repo, {}, {"HOME": td}))
            self.assertIn("Engine.Cases", report)
            self.assertIn("Web.Accounts", report)
            self.assertIn("apps/engine", report)
            self.assertIn("Real first-party domain.", report)  # descriptions kept
            self.assertIn("2 resources", report)
            self.assertNotIn("Ash.Vendored", report)  # deps/ pruned
            self.assertNotIn("Quick layout", report)  # no longer falls back

    def test_architecture_falls_back_to_layout_with_no_mix_project(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            (repo / "src").mkdir(parents=True)
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            report = context(run_hook("architecture.sh", repo, {}, {"HOME": td}))
            self.assertIn("Quick layout", report)
            self.assertIn("src", report)

    def test_health_does_not_nag_when_no_deploy_surface_is_configured(self):
        """A repo with nothing deployed must not be told so every session.

        This warning used to fire unconditionally. scry itself is a plugin --
        there is no endpoint that could ever satisfy it, so the line repeated
        forever and trained skimming of the lines beside it that do matter
        (Matt, 2026-08-22).
        """
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            repo.mkdir(parents=True)
            subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
            (repo / "f.txt").write_text("x")
            subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
            subprocess.run(
                ["git", "-c", "user.email=t@t", "-c", "user.name=t",
                 "commit", "-qm", "init"], cwd=repo, check=True)
            report = context(run_hook("health.sh", repo, {}, {"HOME": td}))
            self.assertNotIn("Deploy state UNKNOWN", report)
            self.assertNotIn("SCRY_HEALTH_URL", report)

    # ---- build guard ---------------------------------------------------

    def _bash_payload(self, cwd, command):
        return {"tool_name": "Bash", "cwd": str(cwd),
                "tool_input": {"command": command}}

    def _mix_project(self, base, with_deps=True, with_build=True):
        base.mkdir(parents=True, exist_ok=True)
        repo = base / "proj"
        (repo / "lib").mkdir(parents=True)
        (repo / "mix.exs").write_text("defmodule M do\nend\n")
        if with_deps:
            (repo / "deps" / "ash").mkdir(parents=True)
        if with_build:
            (repo / "_build" / "dev").mkdir(parents=True)
        return repo

    def test_build_guard_denies_first_force_then_allows_the_repeat(self):
        """Two-strike, not a block: the reflex is stopped, the decision isn't."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            repo = self._mix_project(base)
            env = {"TMPDIR": str(base / "state")}
            (base / "state").mkdir()
            cmd = "mix compile --force"

            first = run_hook("elixir_build_guard.sh", repo,
                             self._bash_payload(repo, cmd), env)
            payload = json.loads(first.stdout)
            self.assertEqual(
                payload["hookSpecificOutput"]["permissionDecision"], "deny")
            reason = payload["hookSpecificOutput"]["permissionDecisionReason"]
            self.assertIn("run it again within", reason)
            self.assertIn("incremental", reason)

            second = run_hook("elixir_build_guard.sh", repo,
                              self._bash_payload(repo, cmd), env)
            self.assertEqual(second.stdout.strip(), "")  # allowed

    def test_build_guard_never_fires_on_ordinary_commands(self):
        """A plain `mix compile` is incremental and is what we WANT people running."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            repo = self._mix_project(base)
            env = {"TMPDIR": str(base / "state")}
            (base / "state").mkdir()
            for cmd in ("mix compile", "mix test", "mix deps.get",
                        "mix format", "git commit -m 'force'",
                        "echo rm -rf _build_notes"):
                result = run_hook("elixir_build_guard.sh", repo,
                                  self._bash_payload(repo, cmd), env)
                self.assertEqual(result.stdout.strip(), "", f"fired on: {cmd}")

    def test_build_guard_catches_every_artifact_discarding_form(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            repo = self._mix_project(base)
            for i, cmd in enumerate(("mix compile --force",
                                     "mix deps.compile --force",
                                     "mix clean --deps",
                                     "rm -rf _build",
                                     "rm -rf deps")):
                # Fresh state dir each time so every command is a first strike.
                state = base / f"s{i}"
                state.mkdir()
                result = run_hook("elixir_build_guard.sh", repo,
                                  self._bash_payload(repo, cmd),
                                  {"TMPDIR": str(state)})
                self.assertTrue(result.stdout.strip(), f"missed: {cmd}")

    def test_build_guard_ignores_non_elixir_projects(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            plain = base / "plain"
            plain.mkdir()
            result = run_hook("elixir_build_guard.sh", plain,
                              self._bash_payload(plain, "rm -rf _build"),
                              {"TMPDIR": str(base)})
            self.assertEqual(result.stdout.strip(), "")

    def test_cold_build_is_reported_before_it_costs_twenty_minutes(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            repo = self._mix_project(base, with_deps=True, with_build=False)
            (repo / "lib" / "d.ex").write_text(
                'defmodule D do\n  use Ash.Domain\nend\n')
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            report = context(run_hook("architecture.sh", repo, {}, {"HOME": td}))
            self.assertIn("COLD", report)
            self.assertIn("FULL build", report)
            self.assertNotIn("deps not fetched", report)

            # Warm build: silent.
            (repo / "_build" / "dev").mkdir(parents=True)
            warm = context(run_hook("architecture.sh", repo, {}, {"HOME": td}))
            self.assertNotIn("COLD", warm)

    def test_build_guard_second_strike_survives_command_variants(self):
        """The retry is almost never byte-identical, so the key must be intent.

        Keying the window on the raw command string looked right and was
        wrong: `mix compile --force 2>&1 | tail`, doubled whitespace, and a
        `cd ... &&` prefix are all one intention to an agent but four distinct
        strings, so the retry got denied a SECOND time -- the exact thrash the
        two-strike design exists to avoid. Found by the Grimoire advisory
        council, 2026-08-22.
        """
        variants = [
            "mix compile --force 2>&1 | tail -20",
            "mix  compile  --force",
            "cd . && mix compile --force",
            "mix do compile --force",
        ]
        for variant in variants:
            with tempfile.TemporaryDirectory() as td:
                base = Path(td)
                repo = self._mix_project(base)
                env = {"TMPDIR": str(base / "state")}
                (base / "state").mkdir()

                first = run_hook("elixir_build_guard.sh", repo,
                                 self._bash_payload(repo, "mix compile --force"), env)
                self.assertTrue(first.stdout.strip(), "first strike should deny")

                second = run_hook("elixir_build_guard.sh", repo,
                                  self._bash_payload(repo, variant), env)
                self.assertEqual(second.stdout.strip(), "",
                                 f"variant denied twice: {variant}")

    def test_build_guard_window_is_scoped_per_project(self):
        """Clearing one project must not pre-authorise clearing another."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            env = {"TMPDIR": str(base / "state")}
            (base / "state").mkdir()
            a = self._mix_project(base / "a")
            b = self._mix_project(base / "b")
            (base / "a").mkdir(exist_ok=True)
            cmd = "mix compile --force"

            run_hook("elixir_build_guard.sh", a, self._bash_payload(a, cmd), env)
            other = run_hook("elixir_build_guard.sh", b,
                             self._bash_payload(b, cmd), env)
            self.assertTrue(other.stdout.strip(),
                            "a strike in project a must not clear project b")

    def test_stack_provider_fingerprints_are_ordered_most_specific_first(self):
        """A confidently WRONG provider label is the bug this scanner kills.

        `internal` sat above `host.docker.internal` as a substring match, so
        the docker entry was dead code and any host merely containing the
        word -- `db.internal.mycorp.com` -- was labelled Fly. Found by Fable
        review, 2026-08-22.
        """
        sys.path.insert(0, str(ROOT / "scanners"))
        import stack as stack_mod

        self.assertEqual(stack_mod.provider_for("host.docker.internal"),
                         "local (docker)")
        self.assertEqual(stack_mod.provider_for("myapp.flycast"),
                         "Fly.io (internal)")
        self.assertEqual(stack_mod.provider_for("ep-x.aws.neon.tech"), "Neon")
        # An unrelated host containing the word must NOT be claimed as Fly.
        self.assertEqual(stack_mod.provider_for("db.internal.mycorp.com"), "")
        self.assertEqual(stack_mod.provider_for("internal-db.neon.tech"), "Neon")

    def test_build_guard_sees_through_a_cd_prefix_in_a_monorepo(self):
        """The guard must guard the layout that motivated it.

        nearest_mix_root walked UP from cwd, so `cd apps/engine && mix compile
        --force` issued from a repo root with no mix.exs of its own resolved to
        nothing and was silently ALLOWED -- the same up-from-cwd assumption
        commit ebc4aed removed from architecture.sh. Found by Fable review.
        """
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            repo = base / "repo"
            (repo / "apps/engine/lib").mkdir(parents=True)
            (repo / "apps/engine/deps").mkdir(parents=True)
            (repo / "apps/engine/mix.exs").write_text("defmodule M do\nend\n")
            (repo / ".git").mkdir()
            env = {"TMPDIR": str(base / "state")}
            (base / "state").mkdir()

            for command in ("cd apps/engine && mix compile --force",
                            "cd 'apps/engine' && mix compile --force",
                            "mix compile --force"):
                shutil.rmtree(base / "state", ignore_errors=True)
                (base / "state").mkdir()
                result = run_hook("elixir_build_guard.sh", repo,
                                  self._bash_payload(repo, command), env)
                self.assertTrue(result.stdout.strip(),
                                f"guard missed from repo root: {command}")

    def test_build_guard_states_the_retry_window(self):
        """"Run it again" without a deadline invites a denial after the window."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            repo = self._mix_project(base)
            env = {"TMPDIR": str(base / "state")}
            (base / "state").mkdir()
            result = run_hook("elixir_build_guard.sh", repo,
                              self._bash_payload(repo, "mix compile --force"), env)
            reason = json.loads(result.stdout)["hookSpecificOutput"][
                "permissionDecisionReason"]
            self.assertIn("within 5 minutes", reason)

    def test_manifests_disclose_the_deny_hook(self):
        """A plugin that denies tool calls may not describe itself as advisory."""
        codex = json.loads((ROOT / ".codex-plugin/plugin.json").read_text())
        claude = json.loads((ROOT / ".claude-plugin/plugin.json").read_text())
        self.assertIn("DENIES", codex["interface"]["longDescription"])
        self.assertIn("SCRY_BUILD_GUARD=0", codex["interface"]["longDescription"])
        self.assertIn("DenyToolCall", codex["interface"]["capabilities"])
        self.assertIn("denies", claude["description"])
        self.assertNotIn("quiet, advisory session-start context",
                         codex["interface"]["longDescription"])
        # The Bash surface spends real money, so both its existence and its
        # off switch have to be visible before anyone installs this.
        self.assertIn("SCRY_METERED_CLI_GUARD=0",
                      codex["interface"]["longDescription"])
        for text in (codex["interface"]["longDescription"],
                     codex["description"], claude["description"]):
            self.assertIn("CLI", text)

    def test_the_model_guard_is_actually_wired_to_the_bash_surface(self):
        """A guard nothing invokes is a guard that does not exist.

        Every shape below was verified by hand; none of it runs in a session
        unless hooks.json points PreToolUse/Bash at the script.
        """
        hooks = json.loads((ROOT / "hooks/hooks.json").read_text())
        bash = [b for b in hooks["hooks"]["PreToolUse"]
                if b.get("matcher") == "Bash"]
        self.assertEqual(len(bash), 1)
        commands = [h["command"] for h in bash[0]["hooks"]]
        self.assertTrue(any("agent_model_guard.sh" in c for c in commands),
                        commands)
        self.assertTrue(all("PLUGIN_ROOT" in c for c in commands))

    # ── Stale-tree advisory (SubagentStart) ────────────────────────────────

    @staticmethod
    def _git(cwd, *args):
        return subprocess.run(["git", "-C", str(cwd), *args], check=True,
                              capture_output=True, text=True).stdout.strip()

    def _stale_repo(self, base, commits=61, day_gap=20):
        """A clone whose main is `commits` behind and `day_gap` days older.

        One file exists only upstream, which is the shape that made the
        2026-09-09 incident silent: grep finds nothing and nothing reads as
        non-existence.
        """
        origin = base / "origin"
        repo = base / "repo"
        g = self._git
        origin.mkdir(parents=True)
        g(origin, "init", "-q", "-b", "main")
        g(origin, "config", "user.name", "Scry Test")
        g(origin, "config", "user.email", "scry@example.test")
        (origin / "stage_one.txt").write_text("one\n")
        g(origin, "add", ".")
        old = {"GIT_AUTHOR_DATE": "2026-08-10T09:00:00",
               "GIT_COMMITTER_DATE": "2026-08-10T09:00:00"}
        subprocess.run(["git", "-C", str(origin), "commit", "-qm", "Initial"],
                       check=True, capture_output=True,
                       env={**os.environ, **old})
        g(base, "clone", "-q", str(origin), str(repo))
        new_date = f"2026-08-{10 + day_gap:02d}T09:00:00"
        new = {"GIT_AUTHOR_DATE": new_date, "GIT_COMMITTER_DATE": new_date}
        for i in range(commits - 1):
            (origin / "churn.txt").write_text(f"{i}\n")
            g(origin, "add", "churn.txt")
            subprocess.run(["git", "-C", str(origin), "commit", "-qm", f"c{i}"],
                           check=True, capture_output=True,
                           env={**os.environ, **new})
        # The stage that exists upstream and nowhere on disk.
        (origin / "stage_six.txt").write_text("six\n")
        g(origin, "add", "stage_six.txt")
        subprocess.run(["git", "-C", str(origin), "commit", "-qm", "stage six"],
                       check=True, capture_output=True,
                       env={**os.environ, **new})
        g(repo, "fetch", "-q", "origin")
        return repo

    @staticmethod
    def _subagent_payload(cwd, agent_type="Explore"):
        return {
            "session_id": "sa-1",
            "transcript_path": "/dev/null",
            "cwd": str(cwd),
            "prompt_id": "p-1",
            "agent_id": "a-1",
            "agent_type": agent_type,
            "hook_event_name": "SubagentStart",
        }

    def test_stale_tree_advisory_gives_a_subagent_the_consequence_and_the_commands(self):
        """A commit count changed nothing during the incident; the read-around did.

        SessionStart output does not reach a subagent and Agent-tool
        additionalContext lands in the parent, so SubagentStart is the only
        place this can be said to the agent that will do the grepping.
        """
        with tempfile.TemporaryDirectory() as td:
            repo = self._stale_repo(Path(td))
            self.assertFalse((repo / "stage_six.txt").exists())

            report = context(run_hook("main_drift_advisory.sh", repo,
                                      self._subagent_payload(repo)))

            self.assertIn("61 commit(s) / 20 day(s) behind origin/main", report)
            self.assertIn("ABSENT from disk", report)
            self.assertIn("show origin/main:<path>", report)
            self.assertIn("grep <pattern> origin/main", report)
            self.assertIn("ls-tree -r --name-only origin/main", report)
            # Past the escalation thresholds it must say the answer is unusable,
            # not merely that the tree is dated.
            self.assertIn("UNRELIABLE", report)
            emitted = json.loads(
                run_hook("main_drift_advisory.sh", repo,
                         self._subagent_payload(repo)).stdout)
            self.assertEqual(
                emitted["hookSpecificOutput"]["hookEventName"], "SubagentStart")

    def test_stale_tree_advisory_never_moves_a_ref_or_a_file(self):
        """Projects consequences, never actions — the ff-only merge removed in #11
        does not come back through the subagent door."""
        with tempfile.TemporaryDirectory() as td:
            repo = self._stale_repo(Path(td))
            before_main = self._git(repo, "rev-parse", "main")
            before_head = self._git(repo, "rev-parse", "HEAD")

            run_hook("main_drift_advisory.sh", repo,
                     self._subagent_payload(repo))

            self.assertEqual(self._git(repo, "rev-parse", "main"), before_main)
            self.assertEqual(self._git(repo, "rev-parse", "HEAD"), before_head)
            self.assertEqual(self._git(repo, "status", "--porcelain"), "")
            self.assertFalse((repo / "stage_six.txt").exists())

    def test_stale_tree_advisory_does_not_fetch_in_the_dispatch_path(self):
        """health.sh already fetched. A fetch here is latency on every dispatch,
        so the hook must report only what the existing refs already know."""
        with tempfile.TemporaryDirectory() as td:
            repo = self._stale_repo(Path(td), commits=3, day_gap=0)
            origin = Path(td) / "origin"
            before_remote = self._git(repo, "rev-parse", "origin/main")
            # Origin moves again, unfetched. A hook that fetched would say 8.
            for i in range(5):
                (origin / "later.txt").write_text(f"{i}\n")
                self._git(origin, "add", "later.txt")
                self._git(origin, "commit", "-qm", f"later{i}")

            report = context(run_hook("main_drift_advisory.sh", repo,
                                      self._subagent_payload(repo)))

            self.assertIn("3 commit(s) / 0 day(s) behind", report)
            self.assertNotIn("UNRELIABLE", report)
            self.assertEqual(self._git(repo, "rev-parse", "origin/main"),
                             before_remote)

    def test_stale_tree_advisory_is_silent_off_the_default_branch(self):
        """A feature branch is expected to diverge; its author chose the base."""
        with tempfile.TemporaryDirectory() as td:
            repo = self._stale_repo(Path(td))
            self._git(repo, "checkout", "-q", "-b", "feat/thing")

            result = run_hook("main_drift_advisory.sh", repo,
                              self._subagent_payload(repo))

            self.assertEqual(result.stdout.strip(), "")

    def test_stale_tree_advisory_is_silent_when_current_or_outside_git(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            repo = self._stale_repo(base)
            fresh = base / "fresh"
            self._git(base, "clone", "-q", str(base / "origin"), str(fresh))
            self.assertEqual(
                run_hook("main_drift_advisory.sh", fresh,
                         self._subagent_payload(fresh)).stdout.strip(), "")

            plain = base / "plain"
            plain.mkdir()
            self.assertEqual(
                run_hook("main_drift_advisory.sh", plain,
                         self._subagent_payload(plain)).stdout.strip(), "")

            # An unparseable payload must not produce a confident answer.
            result = subprocess.run(
                ["bash", str(ROOT / "main_drift_advisory.sh")],
                cwd=str(repo), input="not json", text=True,
                capture_output=True, check=True)
            self.assertEqual(result.returncode, 0)

    def test_stale_tree_advisory_exempts_no_agent_type(self):
        """Explore is the heaviest searcher, so it is the most exposed, not the
        least. The gate is the state of the tree, which is checkable."""
        with tempfile.TemporaryDirectory() as td:
            repo = self._stale_repo(Path(td))
            for agent_type in ("Explore", "general-purpose", "Plan",
                               "code-reviewer", ""):
                with self.subTest(agent_type=agent_type):
                    report = context(run_hook(
                        "main_drift_advisory.sh", repo,
                        self._subagent_payload(repo, agent_type)))
                    self.assertIn("behind origin/main", report)

    # ── Session disposal advisory (Stop) ───────────────────────────────────

    def _worktree_repo(self, base):
        """A clone with one merged worktree and one live one."""
        g = self._git
        origin = base / "origin"
        repo = base / "repo"
        origin.mkdir(parents=True)
        g(origin, "init", "-q", "-b", "main")
        g(origin, "config", "user.name", "Scry Test")
        g(origin, "config", "user.email", "scry@example.test")
        (origin / "base.txt").write_text("base\n")
        g(origin, "add", ".")
        g(origin, "commit", "-qm", "init")
        g(base, "clone", "-q", str(origin), str(repo))
        g(repo, "config", "user.name", "Scry Test")
        g(repo, "config", "user.email", "scry@example.test")
        # Merged: branch sits exactly at origin/main, nothing pending.
        g(repo, "worktree", "add", "-q", "-b", "feat/done",
          str(repo / ".claude/worktrees/agent-done"), "origin/main")
        # Live: one commit that is not upstream.
        wip = repo / ".claude/worktrees/agent-wip"
        g(repo, "worktree", "add", "-q", "-b", "feat/wip", str(wip))
        (wip / "wip.txt").write_text("wip\n")
        g(wip, "add", "wip.txt")
        g(wip, "commit", "-qm", "wip")
        return repo

    @staticmethod
    def _stop_payload(cwd, session_id="stop-1", active=False):
        return {
            "session_id": session_id,
            "transcript_path": "/dev/null",
            "cwd": str(cwd),
            "hook_event_name": "Stop",
            "stop_hook_active": active,
        }

    def test_disposal_advisory_counts_what_is_reclaimable_and_deletes_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            repo = self._worktree_repo(base)
            state = base / "state"
            state.mkdir()
            env = {"TMPDIR": str(state),
                   "SCRY_WORKTREE_REMINDER_MINUTES": "0"}

            report = context(run_hook("session_disposal_advisory.sh", repo,
                                      self._stop_payload(repo), env))

            self.assertIn("2 linked worktree(s)", report)
            self.assertIn("already in origin/main", report)
            self.assertIn("prune-worktrees", report)
            self.assertIn("deleted by this note", report)
            # Advisory means advisory: both worktrees still on disk and listed.
            listing = self._git(repo, "worktree", "list")
            self.assertIn("agent-done", listing)
            self.assertIn("agent-wip", listing)
            self.assertTrue((repo / ".claude/worktrees/agent-done").is_dir())
            self.assertTrue((repo / ".claude/worktrees/agent-wip").is_dir())

    def test_disposal_advisory_recognises_a_squash_merged_worktree(self):
        """Squash merges rewrite SHAs and hide from --is-ancestor. Deciding on
        ancestry alone files finished workspaces under 'might be precious',
        which is exactly the bucket nobody ever empties."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            repo = self._worktree_repo(base)
            origin = base / "origin"
            g = self._git
            # feat/wip's content lands upstream as one squashed commit.
            (origin / "wip.txt").write_text("wip\n")
            g(origin, "add", "wip.txt")
            g(origin, "commit", "-qm", "squash: wip")
            g(repo, "fetch", "-q", "origin")
            state = base / "state"
            state.mkdir()

            report = context(run_hook(
                "session_disposal_advisory.sh", repo,
                self._stop_payload(repo),
                {"TMPDIR": str(state), "SCRY_WORKTREE_REMINDER_MINUTES": "0"}))

            self.assertIn("2 of them hold branches whose content is already in "
                          "origin/main", report)

    def test_disposal_advisory_speaks_once_per_session(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            repo = self._worktree_repo(base)
            state = base / "state"
            state.mkdir()
            env = {"TMPDIR": str(state),
                   "SCRY_WORKTREE_REMINDER_MINUTES": "0"}

            first = run_hook("session_disposal_advisory.sh", repo,
                             self._stop_payload(repo), env)
            second = run_hook("session_disposal_advisory.sh", repo,
                              self._stop_payload(repo), env)
            other = run_hook("session_disposal_advisory.sh", repo,
                             self._stop_payload(repo, "stop-2"), env)

            self.assertTrue(first.stdout.strip())
            self.assertEqual(second.stdout.strip(), "")
            self.assertTrue(other.stdout.strip())

    def test_disposal_advisory_honours_the_stop_loop_guard(self):
        """Its own additionalContext makes the model continue, so the Stop that
        follows arrives with stop_hook_active true. Answering it again loops."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            repo = self._worktree_repo(base)
            state = base / "state"
            state.mkdir()
            env = {"TMPDIR": str(state),
                   "SCRY_WORKTREE_REMINDER_MINUTES": "0"}

            result = run_hook("session_disposal_advisory.sh", repo,
                              self._stop_payload(repo, active=True), env)

            self.assertEqual(result.stdout.strip(), "")

    def test_disposal_advisory_waits_until_the_work_has_happened(self):
        """A reminder about finished workspaces has nothing to say on turn one."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            repo = self._worktree_repo(base)
            state = base / "state"
            state.mkdir()
            transcript = base / "transcript.jsonl"
            transcript.write_text("")
            payload = self._stop_payload(repo)
            payload["transcript_path"] = str(transcript)

            young = run_hook("session_disposal_advisory.sh", repo, payload,
                             {"TMPDIR": str(state),
                              "SCRY_WORKTREE_REMINDER_MINUTES": "45"})
            self.assertEqual(young.stdout.strip(), "")

            aged = run_hook("session_disposal_advisory.sh", repo, payload,
                            {"TMPDIR": str(state),
                             "SCRY_WORKTREE_REMINDER_MINUTES": "0"})
            self.assertTrue(aged.stdout.strip())

    def test_disposal_advisory_is_silent_with_nothing_reclaimable(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            repo = self._worktree_repo(base)
            self._git(repo, "worktree", "remove", "--force",
                      str(repo / ".claude/worktrees/agent-done"))
            state = base / "state"
            state.mkdir()

            quiet = run_hook("session_disposal_advisory.sh", repo,
                             self._stop_payload(repo),
                             {"TMPDIR": str(state),
                              "SCRY_WORKTREE_REMINDER_MINUTES": "0",
                              "SCRY_WORKTREE_DISK_MB_WARN": "99999"})
            self.assertEqual(quiet.stdout.strip(), "")

            # A repo with no linked worktrees at all says nothing either.
            plain = base / "plain"
            self._git(base, "clone", "-q", str(base / "origin"), str(plain))
            self.assertEqual(
                run_hook("session_disposal_advisory.sh", plain,
                         self._stop_payload(plain, "stop-plain"),
                         {"TMPDIR": str(state),
                          "SCRY_WORKTREE_REMINDER_MINUTES": "0"}).stdout.strip(),
                "")

    def test_disposal_advisory_stays_silent_when_it_cannot_identify_the_session(self):
        """No session id means no throttle key, and no throttle key on a Stop
        hook means an unbounded re-fire. Silence is the only safe answer."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            repo = self._worktree_repo(base)
            state = base / "state"
            state.mkdir()
            env = {**os.environ, "TMPDIR": str(state),
                   "SCRY_WORKTREE_REMINDER_MINUTES": "0"}
            for raw in ("", "not json", json.dumps({"cwd": str(repo)})):
                with self.subTest(payload=raw):
                    result = subprocess.run(
                        ["bash", str(ROOT / "session_disposal_advisory.sh")],
                        cwd=str(repo), input=raw, text=True,
                        capture_output=True, check=True, env=env)
                    self.assertEqual(result.stdout.strip(), "")

    # ── Session disposal: scratch Markdown (Stop) ──────────────────────────

    def _scratch_repo(self, base):
        """A clone with no linked worktrees, so only the Markdown half speaks.

        `.resolve()` because a macOS temp dir is a symlink and git reports the
        physical path; without it the hook's repo-relative paths are absolute
        and every exemption misses for a reason that has nothing to do with
        what is being tested.
        """
        g = self._git
        origin = base / "origin"
        repo = base / "repo"
        origin.mkdir(parents=True)
        g(origin, "init", "-q", "-b", "main")
        g(origin, "config", "user.name", "Scry Test")
        g(origin, "config", "user.email", "scry@example.test")
        (origin / "README.md").write_text("readme\n")
        g(origin, "add", ".")
        g(origin, "commit", "-qm", "init")
        g(base, "clone", "-q", str(origin), str(repo))
        return repo

    def _dated_session(self, base, repo, session_id="stop-md"):
        """A Stop payload whose transcript is a real file, so its birth time is
        a readable session start. Everything written after this call is, by
        construction, 'created during this session'."""
        transcript = base / f"{session_id}.jsonl"
        transcript.write_text("")
        payload = self._stop_payload(repo, session_id)
        payload["transcript_path"] = str(transcript)
        return payload

    @staticmethod
    def _md_env(state):
        return {"TMPDIR": str(state), "SCRY_WORKTREE_REMINDER_MINUTES": "0"}

    def test_disposal_advisory_lists_scratch_markdown_and_deletes_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td).resolve()
            repo = self._scratch_repo(base)
            state = base / "state"
            state.mkdir()
            payload = self._dated_session(base, repo)
            (repo / "notes").mkdir()
            (repo / "notes/plan.md").write_text("draft\n")
            before = sorted(str(p.relative_to(repo)) for p in repo.rglob("*.md"))

            report = context(run_hook("session_disposal_advisory.sh", repo,
                                      payload, self._md_env(state)))

            self.assertIn("1 untracked .md file(s)", report)
            self.assertIn("notes/plan.md", report)
            self.assertIn("where long-lived work is tracked", report)
            self.assertIn("Nothing here has been or will be deleted", report)
            # Advisory means advisory: the tree is byte-for-byte as it was.
            after = sorted(str(p.relative_to(repo)) for p in repo.rglob("*.md"))
            self.assertEqual(after, before)
            self.assertEqual((repo / "notes/plan.md").read_text(), "draft\n")

    def test_disposal_advisory_ignores_markdown_older_than_the_session(self):
        """A file that predates the session is not this session's leftover, and
        claiming it is turns the note into noise on the second day of a repo."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td).resolve()
            repo = self._scratch_repo(base)
            state = base / "state"
            state.mkdir()
            (repo / "old.md").write_text("from last week\n")
            os.utime(repo / "old.md", (1, 1))
            payload = self._dated_session(base, repo)

            result = run_hook("session_disposal_advisory.sh", repo, payload,
                              self._md_env(state))

            self.assertEqual(result.stdout.strip(), "")
            self.assertTrue((repo / "old.md").is_file())

    def test_disposal_advisory_is_silent_with_no_scratch_markdown(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td).resolve()
            repo = self._scratch_repo(base)
            state = base / "state"
            state.mkdir()
            payload = self._dated_session(base, repo)

            self.assertEqual(
                run_hook("session_disposal_advisory.sh", repo, payload,
                         self._md_env(state)).stdout.strip(), "")

    def test_disposal_advisory_applies_the_shared_markdown_exemptions(self):
        """Same list as md_creation_advisory.sh, from md_exemptions.sh. A file
        exempt when it was written is exempt when the session ends."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td).resolve()
            repo = self._scratch_repo(base)
            state = base / "state"
            state.mkdir()
            payload = self._dated_session(base, repo)
            for rel in ("CLAUDE.md", "AGENTS.md", "CHANGELOG.md", "LICENSE.md",
                        "CONTRIBUTING.md", "CODE_OF_CONDUCT.md", "SECURITY.md",
                        "SUPPORT.md", ".github/PULL_REQUEST_TEMPLATE.md",
                        ".github/copilot-instructions.md",
                        ".github/ISSUE_TEMPLATE/bug.md", ".claude/scratch.md"):
                target = repo / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("x\n")

            self.assertEqual(
                run_hook("session_disposal_advisory.sh", repo, payload,
                         self._md_env(state)).stdout.strip(), "")

    def test_disposal_advisory_is_silent_in_a_markdown_product_repo(self):
        for marker in ("astro.config.mjs", "docusaurus.config.js"):
            with self.subTest(marker=marker):
                with tempfile.TemporaryDirectory() as td:
                    base = Path(td).resolve()
                    repo = self._scratch_repo(base)
                    state = base / "state"
                    state.mkdir()
                    payload = self._dated_session(base, repo)
                    (repo / marker).write_text("export default {}\n")
                    (repo / "src").mkdir()
                    (repo / "src/post.md").write_text("a post\n")

                    self.assertEqual(
                        run_hook("session_disposal_advisory.sh", repo, payload,
                                 self._md_env(state)).stdout.strip(), "")

    def test_disposal_advisory_will_not_guess_when_the_session_start_is_unknown(self):
        """Birth time is the only honest cutoff. Where it cannot be read there
        is no fallback: the transcript's mtime is its LAST write, and using it
        would misdate every file in the tree. Report nothing instead."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td).resolve()
            repo = self._scratch_repo(base)
            state = base / "state"
            state.mkdir()
            (repo / "plan.md").write_text("draft\n")

            for transcript in ("/dev/null", str(base / "missing.jsonl"), ""):
                with self.subTest(transcript=transcript):
                    payload = self._stop_payload(repo, f"stop-{len(transcript)}")
                    payload["transcript_path"] = transcript
                    self.assertEqual(
                        run_hook("session_disposal_advisory.sh", repo, payload,
                                 self._md_env(state)).stdout.strip(), "")

    def test_disposal_advisory_says_both_leftovers_in_one_note(self):
        """It fires in a live session at the end of a turn. Two findings are
        one note, not two walls of text."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td).resolve()
            repo = self._worktree_repo(base)
            state = base / "state"
            state.mkdir()
            payload = self._dated_session(base, repo, "stop-both")
            (repo / "plan.md").write_text("draft\n")

            report = context(run_hook("session_disposal_advisory.sh", repo,
                                      payload, self._md_env(state)))

            self.assertIn("Worktrees:", report)
            self.assertIn("Scratch Markdown:", report)
            self.assertIn("plan.md", report)
            self.assertEqual(report.count("Nothing here has been or will be "
                                          "deleted"), 1)

    def test_disposal_advisory_markdown_half_honours_the_session_gates(self):
        """The loop guard and the once-per-session marker bound the Markdown
        half exactly as they bound the worktree half — it is one note."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td).resolve()
            repo = self._scratch_repo(base)
            state = base / "state"
            state.mkdir()
            env = self._md_env(state)
            payload = self._dated_session(base, repo, "stop-gates")
            (repo / "plan.md").write_text("draft\n")

            looping = dict(payload, stop_hook_active=True)
            self.assertEqual(
                run_hook("session_disposal_advisory.sh", repo, looping,
                         env).stdout.strip(), "")

            first = run_hook("session_disposal_advisory.sh", repo, payload, env)
            second = run_hook("session_disposal_advisory.sh", repo, payload, env)
            self.assertIn("plan.md", context(first))
            self.assertEqual(second.stdout.strip(), "")

            # A session that has not run long enough has nothing to say yet.
            young = self._dated_session(base, repo, "stop-young")
            (repo / "second.md").write_text("draft\n")
            self.assertEqual(
                run_hook("session_disposal_advisory.sh", repo, young,
                         {"TMPDIR": str(state),
                          "SCRY_WORKTREE_REMINDER_MINUTES": "45"}
                         ).stdout.strip(), "")

    # ── Markdown creation advisory (PostToolUse: Write) ────────────────────

    def _creation_repo(self, base):
        repo = base / "repo"
        repo.mkdir(parents=True)
        g = self._git
        g(repo, "init", "-q", "-b", "main")
        g(repo, "config", "user.name", "Scry Test")
        g(repo, "config", "user.email", "scry@example.test")
        (repo / "README.md").write_text("readme\n")
        (repo / "docs").mkdir()
        (repo / "docs/guide.md").write_text("tracked\n")
        g(repo, "add", ".")
        g(repo, "commit", "-qm", "init")
        return repo

    @staticmethod
    def _write_payload(file_path, session_id="md-1"):
        return {
            "session_id": session_id,
            "hook_event_name": "PostToolUse",
            "tool_name": "Write",
            "tool_input": {"file_path": str(file_path)},
        }

    def test_md_creation_advisory_states_the_facts_without_the_sermon(self):
        """The old wording ranked two homes a file could belong in — which of
        them is right is not something a hook can check. Cut 2026-09-09."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td).resolve()
            repo = self._creation_repo(base)
            state = base / "state"
            state.mkdir()
            scratch = repo / "notes"
            scratch.mkdir()
            (scratch / "plan.md").write_text("draft\n")

            report = context(run_hook(
                "md_creation_advisory.sh", repo,
                self._write_payload(scratch / "plan.md"),
                {"TMPDIR": str(state)}))

            self.assertIn("NEW MARKDOWN", report)
            self.assertIn("notes/plan.md", report)
            self.assertIn("cleared out at session end", report)
            self.assertIn("where long-lived work is tracked", report)
            for gone in ("Artifact", "bind future sessions",
                         "worth checking in", "delete it before the session"):
                self.assertNotIn(gone, report)

    def test_md_creation_advisory_keeps_every_exemption(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td).resolve()
            repo = self._creation_repo(base)
            state = base / "state"
            state.mkdir()
            exempt = ("README.md", "CLAUDE.md", "AGENTS.md", "LICENSE.md",
                      "CONTRIBUTING.md", "CODE_OF_CONDUCT.md", "SECURITY.md",
                      "SUPPORT.md", "CHANGELOG.md",
                      ".github/PULL_REQUEST_TEMPLATE.md",
                      ".github/copilot-instructions.md",
                      ".github/ISSUE_TEMPLATE/bug.md",
                      ".claude/scratch.md", ".claude/commands/deep/note.md",
                      # Already tracked: a Write is an overwrite, not creation.
                      "docs/guide.md",
                      # Not Markdown at all.
                      "notes.txt")
            for rel in exempt:
                with self.subTest(rel=rel):
                    target = repo / rel
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text("x\n")
                    self.assertEqual(
                        run_hook("md_creation_advisory.sh", repo,
                                 self._write_payload(target, f"md-{rel}"),
                                 {"TMPDIR": str(state)}).stdout.strip(), "")

    def test_md_creation_advisory_is_silent_in_a_markdown_product_repo(self):
        """index.md is dangerous in a code repo and normal in a docs site, so
        the exemption is structural — a generator config, not a filename."""
        for marker in ("astro.config.mjs", "astro.config.ts", "astro.config.js",
                       "docusaurus.config.js", "docusaurus.config.ts"):
            with self.subTest(marker=marker):
                with tempfile.TemporaryDirectory() as td:
                    base = Path(td).resolve()
                    repo = self._creation_repo(base)
                    state = base / "state"
                    state.mkdir()
                    (repo / marker).write_text("export default {}\n")
                    (repo / "src").mkdir()
                    (repo / "src/index.md").write_text("a post\n")
                    self.assertEqual(
                        run_hook("md_creation_advisory.sh", repo,
                                 self._write_payload(repo / "src/index.md"),
                                 {"TMPDIR": str(state)}).stdout.strip(), "")

        with tempfile.TemporaryDirectory() as td:
            base = Path(td).resolve()
            repo = self._creation_repo(base)
            state = base / "state"
            state.mkdir()
            (repo / ".vitepress").mkdir()
            (repo / "post.md").write_text("a post\n")
            self.assertEqual(
                run_hook("md_creation_advisory.sh", repo,
                         self._write_payload(repo / "post.md"),
                         {"TMPDIR": str(state)}).stdout.strip(), "")

    def test_md_creation_advisory_speaks_once_per_file_per_session(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td).resolve()
            repo = self._creation_repo(base)
            state = base / "state"
            state.mkdir()
            env = {"TMPDIR": str(state)}
            (repo / "plan.md").write_text("draft\n")
            (repo / "other.md").write_text("draft\n")

            first = run_hook("md_creation_advisory.sh", repo,
                             self._write_payload(repo / "plan.md"), env)
            again = run_hook("md_creation_advisory.sh", repo,
                             self._write_payload(repo / "plan.md"), env)
            other_file = run_hook("md_creation_advisory.sh", repo,
                                  self._write_payload(repo / "other.md"), env)
            other_session = run_hook(
                "md_creation_advisory.sh", repo,
                self._write_payload(repo / "plan.md", "md-2"), env)

            self.assertTrue(first.stdout.strip())
            self.assertEqual(again.stdout.strip(), "")
            self.assertTrue(other_file.stdout.strip())
            self.assertTrue(other_session.stdout.strip())

    def test_md_creation_advisory_has_no_client_detection_left(self):
        """The Codex/Claude split existed only to gate the Artifact line. With
        that line gone it was dead code, and dead code that reads like policy
        is the kind a later session re-implements around."""
        source = (ROOT / "md_creation_advisory.sh").read_text()
        for gone in ("/.codex/", "client=", "EPHEMERAL_BULLET"):
            self.assertNotIn(gone, source)

    def test_the_two_markdown_hooks_share_one_exemption_list(self):
        """Two copies of one whitelist is two answers to one question, and the
        copy that drifts is the one that starts nagging about README.md."""
        lib = ROOT / "md_exemptions.sh"
        self.assertTrue(lib.is_file())
        for hook in ("md_creation_advisory.sh", "session_disposal_advisory.sh"):
            source = (ROOT / hook).read_text()
            self.assertIn("md_exemptions.sh", source, hook)
            self.assertIn("scry_md_product_repo", source, hook)
            self.assertIn("scry_md_standard_file", source, hook)
            # The list itself appears once, in the library.
            self.assertNotIn("CODE_OF_CONDUCT.md", source, hook)
        self.assertIn("CODE_OF_CONDUCT.md", lib.read_text())

    def test_the_advisory_hooks_are_wired_to_the_events_that_actually_deliver(self):
        """Verified against Claude Code 2.1.266 on 2026-09-09.

        SubagentStart is the only event whose additionalContext reaches the
        spawned agent. Stop is the end-shaped event whose output reaches the
        model at all — a probe SessionEnd hook demonstrably ran and its token
        appeared zero times in both the CLI output and the transcript, while
        the same probe on Stop appeared three times. An ADVISORY on SessionEnd
        would be inert, so none is wired there. The one SessionEnd hook that is
        wired, clear_record.sh, says nothing: its whole effect is a file, which
        is exactly what an end-of-session event can still do (the same probe
        showed the hook ran).
        """
        hooks = json.loads((ROOT / "hooks/hooks.json").read_text())
        sub = [h["command"] for group in hooks["hooks"]["SubagentStart"]
               for h in group["hooks"]]
        stop = [h["command"] for group in hooks["hooks"]["Stop"]
                for h in group["hooks"]]
        self.assertTrue(any("main_drift_advisory.sh" in c for c in sub), sub)
        self.assertTrue(
            any("session_disposal_advisory.sh" in c for c in stop), stop)
        end = [h["command"] for group in hooks["hooks"].get("SessionEnd", [])
               for h in group["hooks"]]
        self.assertEqual([c.rsplit("/", 1)[-1] for c in end], ["clear_record.sh"])
        for command in sub + stop + end:
            self.assertIn("PLUGIN_ROOT", command)
            self.assertIn("CLAUDE_PLUGIN_ROOT", command)

    def test_manifests_disclose_the_two_advisory_hooks(self):
        """A hook that speaks into a subagent, and one that fires on every turn,
        are both visible behavior — disclosed in BOTH manifests or not shipped."""
        claude = json.loads((ROOT / ".claude-plugin/plugin.json").read_text())
        codex = json.loads((ROOT / ".codex-plugin/plugin.json").read_text())
        self.assertEqual(claude["version"], codex["version"])
        long_desc = codex["interface"]["longDescription"]
        self.assertIn("SubagentStart", claude["description"])
        self.assertIn("SubagentStart", long_desc)
        self.assertIn("Stop hook", claude["description"])
        self.assertIn("Stop", long_desc)
        self.assertIn("SCRY_WORKTREE_REMINDER_MINUTES", long_desc)
        self.assertIn("SCRY_WORKTREE_DISK_MB_WARN", long_desc)
        self.assertIn("SCRY_SCRATCH_MD_LIST_MAX", long_desc)
        # It must not read as another deny hook: these two never block.
        self.assertIn("ADVISORY", long_desc)
        self.assertIn("deletes nothing", long_desc)
        # The Stop hook now reports scratch Markdown too, and a manifest that
        # does not say so is a manifest that under-discloses what fires on
        # every turn. Both manifests or not shipped.
        for text in (claude["description"], long_desc, codex["description"]):
            self.assertIn("Markdown", text)
        self.assertIn("untracked Markdown files", claude["description"])
        self.assertIn("untracked Markdown files", long_desc)

    def test_the_tuning_table_documents_the_new_thresholds(self):
        """AGENTS.md: keep thresholds documented in the README tuning table."""
        readme = (ROOT / "README.md").read_text()
        for var in ("SCRY_SUBAGENT_STALE_DAYS", "SCRY_SUBAGENT_STALE_COMMITS",
                    "SCRY_WORKTREE_REMINDER_MINUTES",
                    "SCRY_WORKTREE_DISK_MB_WARN", "SCRY_WORKTREE_DU_BUDGET",
                    "SCRY_SCRATCH_MD_LIST_MAX"):
            self.assertIn(f"`{var}`", readme)

    def _stack_repo_full(self, base, env_lines, fly_apps, lock=""):
        base.mkdir(parents=True, exist_ok=True)
        (base / ".env").write_text(env_lines)
        for app, extra in fly_apps:
            d = base / "apps" / app
            d.mkdir(parents=True)
            (d / "fly.toml").write_text(f'app = "{app}"\n{extra}\n')
            if lock:
                (d / "mix.lock").write_text(lock)
                (d / "mix.exs").write_text("defmodule M do\nend\n")
        subprocess.run(["git", "init", "-q"], cwd=base, check=True)
        return base

    def test_stack_warns_when_scale_to_zero_can_never_fire(self):
        """Neon + a polling worker + a pinned machine = always-on Postgres.

        A compute suspends only after N seconds with ZERO connections. Oban
        polls continuously and a pinned machine keeps it up, so the database's
        own suspend setting is inert -- reading that setting would produce a
        confidently wrong "this one is fine."
        """
        with tempfile.TemporaryDirectory() as td:
            base = self._stack_repo_full(
                Path(td) / "r",
                "DATABASE_URL=postgres://u:p@ep-x.us-east-2.aws.neon.tech/db\n",
                [("engine", "min_machines_running = 1")],
                lock='"oban": {:hex, :oban, "2.0.0"},\n')
            out = context(run_hook("stack.sh", base, {}))
            self.assertIn("COST:", out)
            self.assertIn("Oban", out)
            self.assertIn("engine", out)
            self.assertIn("machine lifecycle", out)

    def test_stack_stays_quiet_when_the_machine_can_actually_stop(self):
        with tempfile.TemporaryDirectory() as td:
            base = self._stack_repo_full(
                Path(td) / "r",
                "DATABASE_URL=postgres://u:p@ep-x.us-east-2.aws.neon.tech/db\n",
                [("engine", "min_machines_running = 0")],
                lock='"oban": {:hex, :oban, "2.0.0"},\n')
            out = context(run_hook("stack.sh", base, {}))
            self.assertNotIn("COST:", out)

    def test_stack_warns_on_migrations_over_a_transaction_pooler(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td) / "r"
            base.mkdir(parents=True)
            (base / ".env").write_text(
                "DATABASE_URL=postgres://u:p@ep-x-pooler.us-east-2.aws.neon.tech/db\n")
            subprocess.run(["git", "init", "-q"], cwd=base, check=True)
            out = context(run_hook("stack.sh", base, {}))
            self.assertIn("MIGRATIONS:", out)
            self.assertIn("DIRECT", out)

    def test_stack_pooler_warning_clears_once_a_direct_url_exists(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td) / "r"
            base.mkdir(parents=True)
            (base / ".env").write_text(
                "DATABASE_URL=postgres://u:p@ep-x-pooler.us-east-2.aws.neon.tech/db\n"
                "DIRECT_DATABASE_URL=postgres://u:p@ep-x.us-east-2.aws.neon.tech/db\n")
            subprocess.run(["git", "init", "-q"], cwd=base, check=True)
            out = context(run_hook("stack.sh", base, {}))
            self.assertNotIn("MIGRATIONS:", out)

    def test_stack_announces_dev_tooling_the_agent_may_not_know_it_has(self):
        """A tool nothing names is a tool nobody uses.

        trellys-app depended on Tidewave for months while it appeared in no
        instruction file, so every session recompiled instead of evaluating
        against the running app. The build guard names it too, but only once
        someone reaches for --force -- too late for the agent that simply
        recompiles slowly forever.
        """
        with tempfile.TemporaryDirectory() as td:
            base = self._stack_repo_full(
                Path(td) / "r",
                "DATABASE_URL=postgres://u:p@ep-x.us-east-2.aws.neon.tech/db\n",
                [("engine", "min_machines_running = 0")],
                lock='"tidewave": {:hex, :tidewave, "0.4.0"},\n')
            out = context(run_hook("stack.sh", base, {}))
            self.assertIn("Dev tooling available", out)
            self.assertIn("Tidewave", out)

    def test_health_reports_a_stash_on_an_otherwise_clean_tree(self):
        """A stash is invisible exactly when nobody is looking for it.

        `git status` is clean with a stash sitting there, so every other
        signal reports "nothing to see" while real work waits in a stack on
        no branch and in no commit. Unlike an orphan file it is not destroyed
        by cleanup -- it is forgotten, which is the failure mode.
        """
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            repo.mkdir(parents=True)
            git = ["git", "-C", str(repo)]
            subprocess.run(git + ["init", "-q", "-b", "main"], check=True)
            subprocess.run(git + ["config", "user.email", "t@t"], check=True)
            subprocess.run(git + ["config", "user.name", "t"], check=True)
            (repo / "f.txt").write_text("one")
            subprocess.run(git + ["add", "-A"], check=True)
            subprocess.run(git + ["commit", "-qm", "init"], check=True)

            clean = context(run_hook("health.sh", repo, {}, {"HOME": td}))
            self.assertNotIn("stash", clean.lower())

            (repo / "f.txt").write_text("two")
            subprocess.run(git + ["stash", "-q"], check=True)
            stashed = context(run_hook("health.sh", repo, {}, {"HOME": td}))
            self.assertIn("1 stash entry", stashed)
            self.assertIn("no branch", stashed)
            # Single worktree: the shared-stack mechanics are inapplicable
            # noise, so the message stays quiet about them.
            self.assertNotIn("repository-wide", stashed)

    def test_health_stash_states_shared_stack_mechanics_with_linked_worktrees(self):
        """The stash stack is repository-wide, and the message must say so.

        `git stash pop` applies the newest entry regardless of which worktree,
        branch, or session pushed it. The earlier "N stash entries here"
        phrasing implied locality the stack does not have; that false mental
        model applied a concurrent session's WIP into an unrelated worktree
        with merge conflicts (2026-08-25, trellys-app). With linked worktrees
        present, the report states the sharing mechanics.
        """
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            repo.mkdir(parents=True)
            git = ["git", "-C", str(repo)]
            subprocess.run(git + ["init", "-q", "-b", "main"], check=True)
            subprocess.run(git + ["config", "user.email", "t@t"], check=True)
            subprocess.run(git + ["config", "user.name", "t"], check=True)
            (repo / "f.txt").write_text("one")
            subprocess.run(git + ["add", "-A"], check=True)
            subprocess.run(git + ["commit", "-qm", "init"], check=True)
            subprocess.run(
                git + ["worktree", "add", "-q", str(Path(td) / "wt2"), "-b", "feat/x"],
                check=True,
            )

            (repo / "f.txt").write_text("two")
            subprocess.run(git + ["stash", "-q"], check=True)
            stashed = context(run_hook("health.sh", repo, {}, {"HOME": td}))
            self.assertIn("1 stash entry in this repository", stashed)
            self.assertIn("repository-wide", stashed)
            self.assertIn("2 worktrees", stashed)
            self.assertIn("regardless of which worktree", stashed)
            self.assertIn("before any pop", stashed)

    def _health_report_with_pr_checks(self, base, checks):
        repo = base / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)

        fake_bin = base / "bin"
        fake_bin.mkdir()
        fixture = base / "prs.json"
        fixture.write_text(json.dumps([{
            "number": 1141,
            "headRefName": "fix/document-errors",
            "statusCheckRollup": checks,
            "reviewDecision": "",
            "title": "Normalize document errors",
            "isDraft": False,
        }]))
        gh = fake_bin / "gh"
        gh.write_text("#!/bin/sh\ncat \"$GH_FIXTURE\"\n")
        gh.chmod(0o755)

        return context(run_hook("health.sh", repo, {}, {
            "GH_FIXTURE": str(fixture),
            "HOME": str(base),
            "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        }))

    def test_health_treats_path_gated_skips_as_green(self):
        """A skipped lane is not a mixed CI verdict when every run is healthy."""
        with tempfile.TemporaryDirectory() as td:
            report = self._health_report_with_pr_checks(Path(td), [
                {"status": "COMPLETED", "conclusion": "SUCCESS"},
                {"status": "COMPLETED", "conclusion": "SKIPPED"},
                {"status": "COMPLETED", "conclusion": "NEUTRAL"},
            ])

            self.assertIn("#1141 fix/document-errors -- CI green", report)
            self.assertNotIn("CI mixed", report)

    def test_health_reports_a_known_failure_before_pending_checks(self):
        """A running lane must not hide a failure that already needs attention."""
        with tempfile.TemporaryDirectory() as td:
            report = self._health_report_with_pr_checks(Path(td), [
                {"status": "COMPLETED", "conclusion": "SUCCESS"},
                {"status": "COMPLETED", "conclusion": "TIMED_OUT"},
                {"status": "IN_PROGRESS", "conclusion": ""},
            ])

            self.assertIn("#1141 fix/document-errors -- CI failing (1/3)", report)
            self.assertNotIn("CI pending", report)

    def test_health_distinguishes_interrupted_checks_from_failures(self):
        """Cancellation is terminal without claiming that tested code failed."""
        with tempfile.TemporaryDirectory() as td:
            report = self._health_report_with_pr_checks(Path(td), [
                {"status": "COMPLETED", "conclusion": "SUCCESS"},
                {"status": "COMPLETED", "conclusion": "CANCELLED"},
                {"status": "COMPLETED", "conclusion": "STALE"},
            ])

            self.assertIn("#1141 fix/document-errors -- CI interrupted (2/3)", report)
            self.assertNotIn("CI failing", report)

    def test_health_reports_unknown_completed_conclusions_honestly(self):
        """A new GitHub conclusion must not be implied healthy or understood."""
        with tempfile.TemporaryDirectory() as td:
            report = self._health_report_with_pr_checks(Path(td), [
                {"status": "COMPLETED", "conclusion": "SUCCESS"},
                {"status": "COMPLETED", "conclusion": "FUTURE_STATE"},
            ])

            self.assertIn("#1141 fix/document-errors -- CI unknown", report)
            self.assertNotIn("CI mixed", report)

    def test_health_classifies_legacy_status_contexts(self):
        """GitHub's older state-shaped rollups must share the same PR verdicts."""
        cases = (
            ("SUCCESS", "CI green"),
            ("FAILURE", "CI failing (1/1)"),
            ("ERROR", "CI failing (1/1)"),
            ("PENDING", "CI pending (1/1)"),
            ("EXPECTED", "CI pending (1/1)"),
        )
        for state, expected in cases:
            with self.subTest(state=state), tempfile.TemporaryDirectory() as td:
                report = self._health_report_with_pr_checks(
                    Path(td), [{"__typename": "StatusContext", "state": state}]
                )

                self.assertIn(f"#1141 fix/document-errors -- {expected}", report)

    def test_health_uses_the_latest_attempt_of_each_logical_check(self):
        """A superseded cancelled or failed run must not poison a successful rerun."""
        for old_conclusion in ("CANCELLED", "FAILURE"):
            with self.subTest(old_conclusion=old_conclusion), tempfile.TemporaryDirectory() as td:
                report = self._health_report_with_pr_checks(Path(td), [
                    {
                        "__typename": "CheckRun",
                        "workflowName": "Validate",
                        "name": "Test engine",
                        "startedAt": "2026-08-24T04:00:00Z",
                        "status": "COMPLETED",
                        "conclusion": old_conclusion,
                    },
                    {
                        "__typename": "CheckRun",
                        "workflowName": "Validate",
                        "name": "Test engine",
                        "startedAt": "2026-08-24T05:00:00Z",
                        "status": "COMPLETED",
                        "conclusion": "SUCCESS",
                    },
                ])

                self.assertIn("#1141 fix/document-errors -- CI green", report)

    def _ash_migrate_repo(self, base, resource_newer):
        repo = base / "repo"
        (repo / "lib/app").mkdir(parents=True)
        (repo / "priv/repo/migrations").mkdir(parents=True)
        (repo / ".git").mkdir()
        (repo / "mix.exs").write_text("defmodule M do\nend\n")
        (repo / "mix.lock").write_text('"ash_postgres": {:hex, :ash_postgres, "2.0"},\n')
        # thing.ex must really be a resource: the guard scopes the lib/ side to
        # files containing `use Ash.Resource`, because migrations are generated
        # from resources and nothing else. A plain module being newer means
        # nothing, and comparing against all of lib/ was the noise bug Fable
        # caught.
        resource = "lib/app/thing.ex"
        migration = "priv/repo/migrations/001.exs"
        (repo / resource).write_text(
            "defmodule App.Thing do\n  use Ash.Resource\nend\n")
        (repo / migration).write_text("defmodule M do\nend\n")
        older, newer = (migration, resource) if resource_newer else (resource, migration)
        os.utime(repo / older, (1_700_000_000, 1_700_000_000))
        os.utime(repo / newer, (1_700_000_100, 1_700_000_100))
        return repo

    def test_migrate_guard_warns_when_a_resource_is_newer_than_the_migrations(self):
        """In Ash the migrations are GENERATED, so a newer resource means stale DDL.

        Migrating without regenerating applies yesterday's schema; the resource
        then compiles against a column that does not exist and the break lands
        in CI or production rather than in the dev loop.
        """
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            repo = self._ash_migrate_repo(base, resource_newer=True)
            env = {"TMPDIR": str(base / "state")}
            (base / "state").mkdir()

            first = run_hook("elixir_build_guard.sh", repo,
                             self._bash_payload(repo, "mix ecto.migrate"), env)
            payload = json.loads(first.stdout)
            reason = payload["hookSpecificOutput"]["permissionDecisionReason"]
            self.assertEqual(
                payload["hookSpecificOutput"]["permissionDecision"], "deny")
            self.assertIn("ash.codegen", reason)
            self.assertIn("thing.ex", reason)

            # Second strike passes, same as the rebuild guard.
            second = run_hook("elixir_build_guard.sh", repo,
                              self._bash_payload(repo, "mix ecto.migrate"), env)
            self.assertEqual(second.stdout.strip(), "")

    def test_migrate_guard_is_silent_when_nothing_is_pending(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            repo = self._ash_migrate_repo(base, resource_newer=False)
            env = {"TMPDIR": str(base / "state")}
            (base / "state").mkdir()
            result = run_hook("elixir_build_guard.sh", repo,
                              self._bash_payload(repo, "mix ecto.migrate"), env)
            self.assertEqual(result.stdout.strip(), "")

    def test_migrate_guard_never_fires_outside_ash_postgres(self):
        """Plain Ecto migrations are hand-written, so 'newer resource' means nothing."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            repo = self._ash_migrate_repo(base, resource_newer=True)
            (repo / "mix.lock").write_text('"ecto": {:hex, :ecto, "3.0"},\n')
            env = {"TMPDIR": str(base / "state")}
            (base / "state").mkdir()
            result = run_hook("elixir_build_guard.sh", repo,
                              self._bash_payload(repo, "mix ecto.migrate"), env)
            self.assertEqual(result.stdout.strip(), "")

    def _elixir_edit(self, path):
        return {"tool_name": "Edit", "tool_input": {"file_path": str(path)}}

    def _ash_tenant_repo(self, base, multitenant=True):
        repo = base / "repo"
        (repo / "lib/app").mkdir(parents=True)
        (repo / "mix.exs").write_text("defmodule M do\nend\n")
        (repo / "mix.lock").write_text('"ash": {:hex, :ash, "3.0"},\n')
        body = "defmodule App.Thing do\n  use Ash.Resource\n"
        if multitenant:
            body += "  multitenancy do\n    strategy :attribute\n  end\n"
        (repo / "lib/app/thing.ex").write_text(body + "end\n")
        return repo

    def test_advisory_warns_that_a_spark_extension_edit_is_expensive(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            (repo / "lib").mkdir(parents=True)
            (repo / "mix.exs").write_text("defmodule M do\nend\n")
            (repo / "mix.lock").write_text('"spark": {:hex, :spark, "2.0"},\n')
            ext = repo / "lib/ext.ex"
            ext.write_text("defmodule App.Ext do\n  use Spark.Dsl.Transformer\nend\n")
            out = context(run_hook("elixir_advisory.sh", repo, self._elixir_edit(ext)))
            self.assertIn("spark-extension-edit", out)
            self.assertIn("COMPILE time", out)

            plain = repo / "lib/plain.ex"
            plain.write_text("defmodule App.P do\n  def f, do: 1\nend\n")
            self.assertEqual(
                context(run_hook("elixir_advisory.sh", repo, self._elixir_edit(plain))), "")

    def _read_payload(self, path):
        return {"tool_name": "Read", "tool_input": {"file_path": str(path)}}

    def test_code_prose_advisory_strikes_wider_claims_on_source_reads(self):
        for name in ("mod.ex", "script.exs", "tool.py"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as td:
                repo = Path(td)
                subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
                f = repo / name
                f.write_text("# some code\n")
                report = context(run_hook("code_prose_advisory.sh", repo, self._read_payload(f)))
                self.assertIn("STRICKEN", report)
                self.assertIn("only for this module", report)
                self.assertIn("investigate", report)

    def test_code_prose_advisory_is_silent_for_markdown_and_other_files(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
            for name in ("notes.md", "data.txt", "conf.json"):
                f = repo / name
                f.write_text("content\n")
                self.assertEqual(
                    context(run_hook("code_prose_advisory.sh", repo, self._read_payload(f))), "")

    def _prose_drift_edit(self, path, old, new):
        return {
            "tool_name": "Edit",
            "tool_input": {"file_path": str(path), "old_string": old, "new_string": new},
        }

    def test_prose_drift_fires_on_shape_change_that_skips_the_prose_block(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            (repo / "lib").mkdir(parents=True)
            f = repo / "lib/mod.ex"
            f.write_text(
                'defmodule App.M do\n  @moduledoc "Does one thing."\n  def f, do: 2\nend\n')
            out = context(run_hook(
                "elixir_advisory.sh", repo,
                self._prose_drift_edit(f, "def f, do: 1", "def f, do: 2")))
            self.assertIn("prose-drift", out)
            self.assertIn("ATOMIC", out)
            self.assertIn("not a gate", out)

    def test_prose_drift_is_silent_when_the_edit_touches_prose_or_lacks_shape(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            (repo / "lib").mkdir(parents=True)
            f = repo / "lib/mod.ex"
            f.write_text(
                'defmodule App.M do\n  @moduledoc "Does one thing."\n  def f, do: 2\nend\n')
            # Edit that updates the prose alongside the code: silent.
            self.assertEqual(
                context(run_hook(
                    "elixir_advisory.sh", repo,
                    self._prose_drift_edit(
                        f, '@moduledoc "Does one thing."', '@moduledoc "Does two things."'))), "")
            # Body tweak with no def line in the edited region: silent.
            self.assertEqual(
                context(run_hook(
                    "elixir_advisory.sh", repo,
                    self._prose_drift_edit(f, "do: 1", "do: 2"))), "")
            # Payload without old/new strings (synthetic or Write): silent.
            self.assertEqual(
                context(run_hook("elixir_advisory.sh", repo, self._elixir_edit(f))), "")

    def test_prose_drift_is_silent_when_the_file_has_no_prose_block(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            (repo / "lib").mkdir(parents=True)
            f = repo / "lib/bare.ex"
            f.write_text("defmodule App.B do\n  def f, do: 2\nend\n")
            self.assertEqual(
                context(run_hook(
                    "elixir_advisory.sh", repo,
                    self._prose_drift_edit(f, "def f, do: 1", "def f, do: 2"))), "")

    def test_architecture_flags_env_reads_in_build_time_config(self):
        """config.exs is evaluated at BUILD time; env read there bakes in CI's value."""
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            (repo / "lib").mkdir(parents=True)
            (repo / "config").mkdir(parents=True)
            (repo / "deps").mkdir()
            (repo / "_build").mkdir()
            (repo / "mix.exs").write_text("defmodule M do\nend\n")
            (repo / "config/config.exs").write_text(
                'import Config\nconfig :app, k: System.get_env("SECRET")\n')
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)

            out = context(run_hook("architecture.sh", repo, {}, {"HOME": td}))
            self.assertIn("BUILD-TIME CONFIG READS ENV", out)
            self.assertIn("runtime.exs", out)

            # Correct placement is silent.
            (repo / "config/config.exs").write_text("import Config\n")
            (repo / "config/runtime.exs").write_text(
                'import Config\nconfig :app, k: System.get_env("SECRET")\n')
            clean = context(run_hook("architecture.sh", repo, {}, {"HOME": td}))
            self.assertNotIn("BUILD-TIME CONFIG", clean)

    def test_migrate_guard_ignores_non_resource_files_under_lib(self):
        """Only resource edits imply stale migrations.

        Comparing against ALL of lib/ meant any LiveView, worker or plain
        module edit armed the gate. Verified live in the target repo: its
        newest lib file is an events bridge, so every `mix ecto.migrate` would
        have eaten a denial that meant nothing (Fable review, 2026-08-22).
        """
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            repo = self._ash_migrate_repo(base, resource_newer=False)
            plain = repo / "lib/app/live.ex"
            plain.write_text("defmodule AppWeb.Live do\n  def m, do: 1\nend\n")
            os.utime(plain, (1_700_000_200, 1_700_000_200))   # newest of all
            env = {"TMPDIR": str(base / "state")}
            (base / "state").mkdir()
            result = run_hook("elixir_build_guard.sh", repo,
                              self._bash_payload(repo, "mix ecto.migrate"), env)
            self.assertEqual(result.stdout.strip(), "")

    def test_migrate_guard_ignores_seeds_when_dating_the_migrations(self):
        """priv/repo/seeds.exs is edited routinely and must not mask staleness."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            repo = self._ash_migrate_repo(base, resource_newer=True)
            seeds = repo / "priv/repo/seeds.exs"
            seeds.write_text("# seeds\n")
            os.utime(seeds, (1_700_000_300, 1_700_000_300))   # newer than the resource
            env = {"TMPDIR": str(base / "state")}
            (base / "state").mkdir()
            result = run_hook("elixir_build_guard.sh", repo,
                              self._bash_payload(repo, "mix ecto.migrate"), env)
            self.assertTrue(result.stdout.strip(), "seeds.exs masked a stale migration")

    def _policy_repo(self, base):
        repo = base / "repo"
        (repo / "lib/app").mkdir(parents=True)
        (repo / "mix.exs").write_text("defmodule M do\nend\n")
        (repo / "mix.lock").write_text('"ash": {:hex, :ash, "3.0"},\n')
        (repo / "lib/app/secret.ex").write_text(
            "defmodule App.Secret do\n  use Ash.Resource\n  policies do\n"
            "    policy always() do\n      authorize_if actor_present()\n"
            "    end\n  end\nend\n")
        (repo / "lib/app/plain.ex").write_text(
            "defmodule App.Plain do\n  use Ash.Resource\nend\n")
        return repo

    def _caller(self, repo, body):
        path = repo / "lib/app/c.ex"
        path.write_text("defmodule AppWeb.C do\n" + body + "\nend\n")
        return path

    def test_actor_check_fires_only_when_the_resource_has_policies(self):
        """The gate that turned 93-of-93 noise into a real signal.

        Measured on a live Ash app: ungated, this flagged every for_read call
        site in the project -- 93 of 93, across 67 files -- because only 5 of
        ~189 resources declare policies, so for the rest the actor would be
        discarded unread. Passing an actor only matters where a rule exists to
        consult it (2026-08-22).
        """
        with tempfile.TemporaryDirectory() as td:
            repo = self._policy_repo(Path(td))

            # The real mistake: policy-bearing resource, no actor, no bypass.
            bad = self._caller(repo, "  def f do\n    App.Secret\n"
                                     "    |> Ash.Query.for_read(:read)\n"
                                     "    |> Ash.read()\n  end")
            out = context(run_hook("elixir_advisory.sh", repo, self._elixir_edit(bad)))
            self.assertIn("ash-for-read-without-actor", out)
            self.assertIn("declares policies", out)

            # Same shape against a resource with no policies: nothing to check.
            ok = self._caller(repo, "  def f do\n    App.Plain\n"
                                    "    |> Ash.Query.for_read(:read)\n"
                                    "    |> Ash.read()\n  end")
            self.assertEqual(
                context(run_hook("elixir_advisory.sh", repo, self._elixir_edit(ok))), "")

    def test_actor_check_respects_an_explicit_authorization_bypass(self):
        """`authorize?: false` is a reviewed decision, not an oversight."""
        with tempfile.TemporaryDirectory() as td:
            repo = self._policy_repo(Path(td))
            bypass = self._caller(repo, "  def f do\n    App.Secret\n"
                                        "    |> Ash.Query.for_read(:read)\n"
                                        "    |> Ash.read(authorize?: false)\n  end")
            self.assertEqual(
                context(run_hook("elixir_advisory.sh", repo, self._elixir_edit(bypass))), "")

    def test_actor_check_fails_quiet_on_an_unresolvable_target(self):
        """A guess here would be the confident-wrong-answer this tool exists to stop."""
        with tempfile.TemporaryDirectory() as td:
            repo = self._policy_repo(Path(td))
            odd = self._caller(repo, "  def f(q) do\n"
                                     "    q |> Ash.Query.for_read(:read) |> Ash.read()\n  end")
            self.assertEqual(
                context(run_hook("elixir_advisory.sh", repo, self._elixir_edit(odd))), "")

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

    # ---- stack scanner -------------------------------------------------
    #
    # The bug this scanner exists to prevent (2026-08-21): an instruction
    # file named Fly.io managed Postgres months after the database became
    # Neon. The obvious implementation -- count provider names across the
    # tree -- reproduces the bug rather than catching it, because in the
    # real repo Fly strings outnumbered Neon strings 25 files to 11 while
    # the database was unambiguously Neon. These tests pin the behaviour
    # that difference depends on.

    def _stack_repo(self, base, db_url, extra_files=None):
        (base / ".env").write_text(f"DATABASE_URL={db_url}\n")
        for rel, body in (extra_files or {}).items():
            path = base / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body)
        subprocess.run(["git", "init", "-q"], cwd=base, check=True)
        return base

    def test_stack_binds_db_provider_to_role_not_to_name_frequency(self):
        """Fly strings can outnumber Neon and the answer must still be Neon."""
        with tempfile.TemporaryDirectory() as td:
            base = self._stack_repo(
                Path(td),
                "postgres://u:pw@ep-x1.us-east-2.aws.neon.tech/db?sslmode=require",
                {
                    # Fly appears far more often -- but never as the DB role.
                    "apps/ocr/fly.toml": 'app = "svc-ocr"\n# reached via svc-ocr.flycast\n',
                    "apps/api/fly.toml": 'app = "svc-api"\n# talks to svc-ocr.flycast\n',
                    "config/runtime.exs": 'base_url: "http://svc-ocr.flycast"\n# flycast flycast\n',
                },
            )
            out = context(run_hook("stack.sh", base, {}))
            self.assertIn("Neon", out)
            self.assertRegex(out, r"- Data:[^\n]*Neon")
            # Fly is reported as HOSTING, never as the database provider.
            self.assertNotRegex(out, r"- Data:[^\n]*Fly")
            self.assertIn("svc-ocr", out)

    def test_stack_ignores_stale_prose_in_docs(self):
        """docs/ is excluded outright -- prose is what was wrong to begin with."""
        with tempfile.TemporaryDirectory() as td:
            base = self._stack_repo(
                Path(td),
                "postgres://u:pw@ep-x1.us-east-2.aws.neon.tech/db",
                {
                    "docs/architecture.md": "The database is Fly.io managed Postgres.\n" * 40,
                    "CLAUDE.md": "We run Supabase for everything.\n",
                },
            )
            out = context(run_hook("stack.sh", base, {}))
            self.assertIn("Neon", out)
            self.assertNotIn("Supabase", out)
            self.assertRegex(out, r"- Data:[^\n]*Neon")

    def test_stack_never_emits_credentials(self):
        with tempfile.TemporaryDirectory() as td:
            secret = "sup3rs3cr3tpassw0rd"
            base = self._stack_repo(
                Path(td),
                f"postgres://admin:{secret}@ep-x1.us-east-2.aws.neon.tech/db",
            )
            (base / ".env").write_text(
                f"DATABASE_URL=postgres://admin:{secret}@ep-x1.us-east-2.aws.neon.tech/db\n"
                f"STRIPE_API_KEY=sk_live_{secret}\n"
            )
            result = run_hook("stack.sh", base, {})
            self.assertNotIn(secret, result.stdout + result.stderr)
            out = context(result)
            self.assertIn("Neon", out)
            self.assertIn("Stripe", out)  # name only, never the value

    def test_stack_refuses_to_call_a_tunnel_local(self):
        """localhost on a non-default port is a proxy; saying 'local' misleads."""
        with tempfile.TemporaryDirectory() as td:
            base = self._stack_repo(Path(td), "postgres://u:pw@localhost:5433/app")
            out = context(run_hook("stack.sh", base, {}))
            self.assertIn("NOT determinable", out)
            self.assertIn("5433", out)

    def test_stack_reads_plain_local_postgres_as_local(self):
        with tempfile.TemporaryDirectory() as td:
            base = self._stack_repo(Path(td), "postgres://u:pw@localhost:5432/app")
            out = context(run_hook("stack.sh", base, {}))
            self.assertIn("local", out)
            self.assertNotIn("NOT determinable", out)

    def test_stack_ignores_env_example_placeholders(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            (base / ".env.example").write_text(
                "DATABASE_URL=postgres://user:pass@db.supabase.co/postgres\n"
            )
            subprocess.run(["git", "init", "-q"], cwd=base, check=True)
            out = context(run_hook("stack.sh", base, {}))
            self.assertNotIn("Supabase", out)

    def test_stack_is_silent_with_no_config(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            subprocess.run(["git", "init", "-q"], cwd=base, check=True)
            (base / "notes.txt").write_text("nothing to see")
            self.assertEqual(context(run_hook("stack.sh", base, {})), "")


    # ── scale_advisory.sh ──────────────────────────────────────────────────
    def _scale_repo(self, td):
        """A repo with enough small files to calibrate against, plus one big one."""
        repo = Path(td)
        subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
        env = os.environ.copy()
        env.update({
            "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
        })
        lib = repo / "lib"
        lib.mkdir()
        for i in range(25):
            (lib / f"small{i}.ex").write_text("defmodule S do\n  def a, do: 1\nend\n")
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-qm", "base"], cwd=repo, check=True, env=env)
        return repo, env

    def _big_file(self, repo, env, name="big.ex", lines=900, commits=4, defs=60):
        body = ["defmodule Big do"]
        for i in range(defs):
            body.append(f"  def f{i}, do: :ok")
        while len(body) < lines:
            body.append("  # padding line")
        body.append("end")
        target = repo / "lib" / name
        for c in range(commits):
            target.write_text("\n".join(body) + f"\n# rev {c}\n")
            subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", f"c{c}"], cwd=repo, check=True, env=env)
        return target

    def _scale(self, repo, path, tool="Edit", session="s1"):
        state = Path(os.environ.get("TMPDIR", "/tmp")) / "scry-scale-advisory"
        payload = {
            "tool_name": tool,
            "session_id": session,
            "tool_input": {"file_path": str(path)},
        }
        return context(run_hook("scale_advisory.sh", repo, payload))

    def test_scale_advisory_fires_on_a_large_actively_worked_file_and_names_the_failure_modes(self):
        with tempfile.TemporaryDirectory() as td:
            repo, env = self._scale_repo(td)
            big = self._big_file(repo, env)
            report = self._scale(repo, big, session="fires1")

            self.assertIn("SCALE", report)
            self.assertIn("lib/big.ex", report)
            # It must state the concrete, silent failure modes -- not just "this file is big".
            self.assertIn("absence from the part you have seen is not absence from the file", report)
            self.assertIn("duplicate", report)
            self.assertIn("production", report)
            # And it must give the remedy, plus the guard against turning it into a refactor.
            self.assertIn("grep THIS FILE", report)
            self.assertIn("This is a look, not a gate", report)
            self.assertIn("split it by job, not by size", report)

    def test_scale_advisory_stays_silent_on_a_large_file_nobody_touches(self):
        with tempfile.TemporaryDirectory() as td:
            repo, env = self._scale_repo(td)
            cold = self._big_file(repo, env, name="cold.ex", commits=1)
            self.assertEqual("", self._scale(repo, cold, session="cold1"))

    def test_scale_advisory_stays_silent_on_a_small_file(self):
        with tempfile.TemporaryDirectory() as td:
            repo, env = self._scale_repo(td)
            small = self._big_file(repo, env, name="tiny.ex", lines=40, commits=5, defs=10)
            self.assertEqual("", self._scale(repo, small, session="small1"))

    def test_scale_advisory_fires_once_per_file_per_session_across_read_and_edit(self):
        with tempfile.TemporaryDirectory() as td:
            repo, env = self._scale_repo(td)
            big = self._big_file(repo, env, name="once.ex")

            first = self._scale(repo, big, tool="Read", session="dedupe")
            second = self._scale(repo, big, tool="Edit", session="dedupe")
            other_session = self._scale(repo, big, tool="Edit", session="dedupe2")

            self.assertIn("SCALE", first)
            self.assertEqual("", second)          # same session, same file -> quiet
            self.assertIn("SCALE", other_session)  # a fresh session hears it once

    def test_scale_advisory_reports_the_lines_a_read_never_returned(self):
        with tempfile.TemporaryDirectory() as td:
            repo, env = self._scale_repo(td)
            huge = self._big_file(repo, env, name="huge.ex", lines=2400)
            report = self._scale(repo, huge, tool="Read", session="trunc")

            self.assertIn("2000 lines", report)
            self.assertIn("nothing marked their absence", report)

    def test_scale_advisory_treats_a_declaration_only_file_as_legitimately_long(self):
        with tempfile.TemporaryDirectory() as td:
            repo, env = self._scale_repo(td)
            registry = self._big_file(repo, env, name="registry.ex", defs=0)
            report = self._scale(repo, registry, session="registry")

            self.assertIn("declarative", report)
            self.assertIn("splitting it would probably make it worse", report)

    def test_scale_advisory_ignores_generated_and_vendored_trees(self):
        with tempfile.TemporaryDirectory() as td:
            repo, env = self._scale_repo(td)
            vendored = repo / "deps" / "dep"
            vendored.mkdir(parents=True)
            big = self._big_file(repo, env, name="v.ex")
            moved = vendored / "v.ex"
            moved.write_text(big.read_text())
            self.assertEqual("", self._scale(repo, moved, session="vendor"))


    # ── agent_model_guard.sh ───────────────────────────────────────────────
    def _guard(self, payload):
        import tempfile as _tf
        with _tf.TemporaryDirectory() as td:
            result = run_hook("agent_model_guard.sh", td, payload)
        out = result.stdout.strip()
        if not out:
            return None
        h = json.loads(out)["hookSpecificOutput"]
        return h.get("permissionDecisionReason") or h.get("additionalContext")

    def _pre(self, tool, tool_input):
        return self._guard({"tool_name": tool, "hook_event_name": "PreToolUse",
                            "tool_input": tool_input})

    def _post(self, tool, tool_input):
        return self._guard({"tool_name": tool, "hook_event_name": "PostToolUse",
                            "tool_input": tool_input})

    def test_agent_guard_blocks_only_the_absent_choice(self):
        reason = self._pre("Agent", {"description": "verify the fix"})
        self.assertIsNotNone(reason)
        self.assertIn("inherits", reason)

    def test_agent_guard_does_not_prescribe_which_model_to_use(self):
        """Model names age out; the rule does not. A roster would rot in months."""
        reason = self._pre("Agent", {"description": "d"})
        for rostered in ("opus", "sonnet", "haiku"):
            self.assertNotIn(rostered, reason.lower())
        self.assertIn("cheapest model that can actually do this job", reason)

    def test_agent_guard_never_objects_to_a_model_that_was_actually_chosen(self):
        """The guard is against accident, not against any model."""
        for model in ("fable", "opus", "sonnet", "haiku", "some-future-model"):
            self.assertIsNone(self._pre("Agent", {"description": "d", "model": model}),
                              model)

    def test_agent_guard_allows_a_premium_model_inside_a_workflow_when_chosen(self):
        """A cheaper session asking the strongest model questions is legitimate."""
        self.assertIsNone(self._pre("Workflow", {
            "script": "agent(a,{model:'fable'}); agent(b,{model:'fable'})"}))

    def test_agent_guard_denies_a_workflow_whose_agents_do_not_all_choose(self):
        reason = self._pre("Workflow", {
            "script": "agent(a,{model:'opus'}); agent(b,{}); agent(c,{})"})
        self.assertIsNotNone(reason)
        self.assertIn("3 agent() call(s)", reason)

    def test_agent_guard_makes_a_fork_a_conscious_choice_but_lets_the_retry_through(self):
        payload = {"subagent_type": "fork", "description": "carry on here"}
        first = self._pre("Agent", payload)
        self.assertIsNotNone(first)
        self.assertIn("inherits", first.lower())
        self.assertIsNone(self._pre("Agent", payload))

    def test_agent_guard_ignores_model_names_that_appear_only_in_comments(self):
        self.assertIsNone(self._pre("Workflow", {
            "script": "// consider model: 'fable' here\nagent(a,{model:'opus'})"}))

    def test_agent_guard_reports_the_cost_after_a_premium_model_is_used(self):
        """Guidance after a legitimate choice -- never a block."""
        for payload in ({"description": "d", "model": "fable"},):
            note = self._post("Agent", payload)
            self.assertIsNotNone(note)
            self.assertIn("COST", note)
            self.assertIn("smaller usage allowance", note)
            # It must not scold: the choice was deliberate and that is the point.
            self.assertIn("Nothing is wrong here", note)

        note = self._post("Workflow", {"script": "agent(a,{model:'fable'})"})
        self.assertIsNotNone(note)
        self.assertIn("COST", note)

    def test_agent_guard_stays_silent_after_an_everyday_model(self):
        self.assertIsNone(self._post("Agent", {"description": "d", "model": "sonnet"}))

    def test_agent_guard_premium_list_is_configurable_not_hardcoded(self):
        import tempfile as _tf
        payload = {"tool_name": "Agent", "hook_event_name": "PostToolUse",
                   "tool_input": {"description": "d", "model": "opus"}}
        with _tf.TemporaryDirectory() as td:
            out = run_hook("agent_model_guard.sh", td, payload,
                           env={"SCRY_PREMIUM_MODELS": "opus"}).stdout.strip()
        self.assertIn("COST", out)

    # ── agent_model_guard.sh — metered CLI on Bash ─────────────────────────
    def _bash(self, command, env=None):
        import tempfile as _tf
        payload = {"tool_name": "Bash", "hook_event_name": "PreToolUse",
                   "tool_input": {"command": command}}
        with _tf.TemporaryDirectory() as td:
            out = run_hook("agent_model_guard.sh", td, payload, env=env).stdout.strip()
        if not out:
            return None
        return json.loads(out)["hookSpecificOutput"]["permissionDecisionReason"]

    def test_metered_cli_guard_blocks_a_call_that_names_no_model(self):
        for command in ('codex exec "fix the thing"',
                        'codex "fix the thing"',
                        '/Users/m/.local/bin/codex exec "fix"',
                        'cat notes | codex exec'):
            reason = self._bash(command)
            self.assertIsNotNone(reason, command)
            self.assertIn("config default", reason)

    def test_metered_cli_guard_accepts_every_way_of_naming_the_model(self):
        for command in ('codex exec -m gpt-5.6-terra "fix"',
                        'codex exec --model=gpt-5.6-terra "fix"',
                        'codex exec --model gpt-5.6-terra "fix"',
                        'codex exec -c model=gpt-5.6-terra "fix"',
                        'ls && codex exec -m gpt-5.6-terra "fix"'):
            self.assertIsNone(self._bash(command), command)

    def test_metered_cli_guard_does_not_prescribe_which_model_to_use(self):
        """Model names age out; the rule does not."""
        reason = self._bash('codex exec "fix"')
        for rostered in ("gpt-", "astra", "terra", "opus", "sonnet"):
            self.assertNotIn(rostered, reason.lower(), rostered)
        self.assertIn("cheapest model that can actually do this job", reason)

    def test_metered_cli_guard_reads_the_segment_not_the_whole_command_line(self):
        """A `-m` belonging to another command must not vouch for this one.

        The whole-string grep this replaced passed `mkdir -m 755 x && codex
        exec` — the exact shape that makes a chained call look pinned.
        """
        reason = self._bash('mkdir -m 755 x && codex exec "fix"')
        self.assertIsNotNone(reason)
        self.assertIn("config default", reason)

    def test_metered_cli_guard_ignores_the_cli_name_where_it_is_not_a_command(self):
        for command in ('echo "run codex now"',
                        'cd ~/dev/codex && ls',
                        'grep -rn codex .',
                        'ls -la'):
            self.assertIsNone(self._bash(command), command)

    def test_metered_cli_guard_does_not_split_a_separator_inside_a_prompt(self):
        self.assertIsNone(self._bash('codex exec -m some-model "run a && b"'))

    def test_metered_cli_guard_lets_management_verbs_through(self):
        """login, --help and friends run no inference, so they bill nothing."""
        for command in ('codex login', 'codex logout', 'codex --help',
                        'codex --version', 'codex mcp list', 'codex doctor'):
            self.assertIsNone(self._bash(command), command)

    def test_metered_cli_guard_covers_a_call_behind_env_assignments(self):
        for command in ('FOO=1 codex exec "fix"', 'env FOO=1 codex exec "fix"'):
            self.assertIsNotNone(self._bash(command), command)

    def test_metered_cli_list_is_configurable_not_hardcoded(self):
        """A tool that bills today may not tomorrow, and vice versa."""
        self.assertIsNone(self._bash('gemini "hi"'))
        self.assertIsNotNone(self._bash('gemini "hi"',
                                        env={"SCRY_METERED_CLIS": "gemini"}))
        self.assertIsNone(self._bash('codex exec "fix"',
                                     env={"SCRY_METERED_CLIS": "gemini"}))

    def test_metered_cli_guard_can_be_switched_off(self):
        self.assertIsNone(self._bash('codex exec "fix"',
                                     env={"SCRY_METERED_CLI_GUARD": "0"}))

    def test_metered_cli_free_verbs_are_extensible(self):
        self.assertIsNotNone(self._bash('codex cloud list'))
        self.assertIsNone(self._bash('codex cloud list',
                                     env={"SCRY_METERED_CLI_FREE": "cloud"}))

    # ── agent_model_guard.sh — headless Claude on the API account ──────────
    def _billing(self, command, env=None, tmp=None):
        import tempfile as _tf
        payload = {"tool_name": "Bash", "hook_event_name": "PreToolUse",
                   "tool_input": {"command": command}}
        merged = dict(env or {})
        if tmp:
            merged["TMPDIR"] = tmp
        with _tf.TemporaryDirectory() as td:
            out = run_hook("agent_model_guard.sh", td, payload,
                           env=merged).stdout.strip()
        if not out:
            return None
        return json.loads(out)["hookSpecificOutput"]["permissionDecisionReason"]

    def test_headless_claude_with_an_api_key_says_what_it_will_bill(self):
        """The charge lands on a different account and is not refundable."""
        with tempfile.TemporaryDirectory() as tmp:
            reason = self._billing('claude -p "fix"',
                                   {"ANTHROPIC_API_KEY": "sk-x"}, tmp)
        self.assertIsNotNone(reason)
        self.assertIn("API BILLING", reason)
        self.assertIn("not refundable", reason)
        self.assertIn("subscription is not touched", reason)

    def test_headless_claude_is_a_speed_bump_not_a_wall(self):
        """Billing the API on purpose is legitimate; it just has to be seen."""
        with tempfile.TemporaryDirectory() as tmp:
            env = {"ANTHROPIC_API_KEY": "sk-x"}
            self.assertIsNotNone(self._billing('claude -p "fix"', env, tmp))
            self.assertIsNone(self._billing('claude -p "fix"', env, tmp))

    def test_headless_claude_is_silent_when_no_key_can_bill(self):
        """With no key the run goes to the subscription, which is the point."""
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(self._billing('claude -p "fix"', {}, tmp))

    def test_an_interactive_claude_session_is_never_flagged(self):
        """Only -p/--print bypasses the subscription login."""
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(self._billing('claude "fix"',
                                            {"ANTHROPIC_API_KEY": "sk-x"}, tmp))

    def test_a_key_exported_on_the_command_itself_still_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            reason = self._billing('ANTHROPIC_API_KEY=sk-y claude -p "fix"',
                                   {}, tmp)
        self.assertIsNotNone(reason)
        self.assertIn("API BILLING", reason)

    def test_the_billing_advisory_can_be_switched_off_on_its_own(self):
        """Its switch is separate from the metered-CLI switch, both ways."""
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(self._billing(
                'claude -p "fix"',
                {"ANTHROPIC_API_KEY": "sk-x", "SCRY_API_BILLING_GUARD": "0"}, tmp))
            self.assertIsNotNone(self._billing(
                'claude -p "fix"',
                {"ANTHROPIC_API_KEY": "sk-x", "SCRY_METERED_CLI_GUARD": "0"}, tmp))

    def test_metered_cli_guard_says_nothing_after_the_call(self):
        """PreToolUse is the only useful moment; a PostToolUse note on every
        Bash call would be noise."""
        import tempfile as _tf
        payload = {"tool_name": "Bash", "hook_event_name": "PostToolUse",
                   "tool_input": {"command": 'codex exec "fix"'}}
        with _tf.TemporaryDirectory() as td:
            self.assertEqual("", run_hook("agent_model_guard.sh", td,
                                          payload).stdout.strip())

    def test_metered_cli_guard_fails_open_on_a_command_it_cannot_lex(self):
        """Refusing what cannot be parsed wedges a session over a lexing bug."""
        self.assertIsNone(self._bash('codex exec "unbalanced'))
        self.assertIsNone(self._bash(""))

    def test_agent_guard_fails_open_rather_than_wedging_a_session(self):
        """A cost guard that blocks what it cannot parse costs more than it saves."""
        self.assertIsNone(self._guard({"tool_name": "Bash", "tool_input": {"command": "ls"}}))
        self.assertIsNone(self._guard({}))
        self.assertIsNone(self._pre("Agent", None))
        self.assertIsNone(self._pre("Workflow", {"name": "saved-workflow"}))


if __name__ == "__main__":
    unittest.main()


class BranchPointAdvisoryTests(unittest.TestCase):
    """The branch is created from HEAD when the command names no start point.

    Observed 2026-09-09 on a freshly built remote box: an agent asked to make
    one change ran `git checkout -b <name>` with no start point and no
    preceding fetch. The reflog recorded "Created from HEAD" and the checkout
    had no FETCH_HEAD at all. It was current only because the clone was
    minutes old.
    """

    @staticmethod
    def _git(cwd, *args):
        return subprocess.run(["git", "-C", str(cwd), *args], check=True,
                              capture_output=True, text=True).stdout.strip()

    def _clone_behind(self, base, commits=3):
        """A clone whose HEAD is `commits` behind origin/main, never fetched."""
        origin = base / "origin"
        repo = base / "repo"
        g = self._git
        origin.mkdir(parents=True)
        g(origin, "init", "-q", "-b", "main")
        g(origin, "config", "user.name", "Scry Test")
        g(origin, "config", "user.email", "scry@example.test")
        (origin / "first.txt").write_text("one\n")
        g(origin, "add", "first.txt")
        g(origin, "commit", "-qm", "first")
        subprocess.run(["git", "clone", "-q", str(origin), str(repo)],
                       check=True, capture_output=True)
        g(repo, "config", "user.name", "Scry Test")
        g(repo, "config", "user.email", "scry@example.test")
        for i in range(commits):
            (origin / f"later{i}.txt").write_text(f"{i}\n")
            g(origin, "add", f"later{i}.txt")
            g(origin, "commit", "-qm", f"later{i}")
        if commits:
            g(repo, "fetch", "-q", "origin")
        return repo

    @staticmethod
    def _payload(cwd, command):
        return {
            "session_id": "bp-1",
            "transcript_path": "/dev/null",
            "cwd": str(cwd),
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": command},
        }

    def _run(self, repo, command, env=None):
        return run_hook("branch_point_advisory.sh", repo,
                        self._payload(repo, command), env=env)

    def test_it_names_the_distance_the_new_branch_will_carry(self):
        with tempfile.TemporaryDirectory() as td:
            repo = self._clone_behind(Path(td), commits=3)
            report = context(self._run(repo, "git checkout -b feat/thing"))
            self.assertIn("3 commit(s) behind origin/main", report)
            self.assertIn("no start point named", report)

    def test_it_says_the_count_is_unknowable_when_origin_was_never_fetched(self):
        """A zero measured against a never-updated remote-tracking ref is not a
        zero. That is the exact shape of the 2026-09-09 observation."""
        with tempfile.TemporaryDirectory() as td:
            repo = self._clone_behind(Path(td), commits=0)
            self.assertFalse((repo / ".git" / "FETCH_HEAD").exists())
            report = context(self._run(repo, "git checkout -b feat/thing"))
            self.assertIn("never been fetched", report)
            self.assertIn("can only be larger", report)

    def test_it_is_silent_when_the_command_chooses_its_own_start_point(self):
        with tempfile.TemporaryDirectory() as td:
            repo = self._clone_behind(Path(td), commits=3)
            for cmd in ("git checkout -b feat/x origin/main",
                        "git switch -c feat/x origin/main",
                        "git worktree add ../wt -b feat/x origin/main"):
                self.assertEqual(context(self._run(repo, cmd)), "", cmd)

    def test_it_is_silent_on_a_current_tree_fetched_recently(self):
        with tempfile.TemporaryDirectory() as td:
            repo = self._clone_behind(Path(td), commits=0)
            self._git(repo, "fetch", "-q", "origin")
            self.assertEqual(context(self._run(repo, "git checkout -b feat/x")), "")

    def test_it_speaks_again_once_the_fetch_itself_is_old(self):
        """A count is only as fresh as the ref it was measured against."""
        with tempfile.TemporaryDirectory() as td:
            repo = self._clone_behind(Path(td), commits=0)
            self._git(repo, "fetch", "-q", "origin")
            fetch_head = repo / ".git" / "FETCH_HEAD"
            old = time.time() - 9 * 3600
            os.utime(fetch_head, (old, old))
            report = context(self._run(repo, "git checkout -b feat/x"))
            self.assertIn("last fetched 9h ago", report)

    def test_it_ignores_commands_that_create_no_branch(self):
        with tempfile.TemporaryDirectory() as td:
            repo = self._clone_behind(Path(td), commits=3)
            for cmd in ("git checkout main", "git branch -d old",
                        "git branch --merged", "git status", "git switch main",
                        "ls -la"):
                self.assertEqual(context(self._run(repo, cmd)), "", cmd)

    def test_it_advises_and_never_denies(self):
        """AGENTS.md: advisory, never blocking. Every hook exits zero."""
        with tempfile.TemporaryDirectory() as td:
            repo = self._clone_behind(Path(td), commits=3)
            result = self._run(repo, "git checkout -b feat/x")
            self.assertEqual(result.returncode, 0)
            emitted = json.loads(result.stdout)
            self.assertEqual(
                emitted["hookSpecificOutput"]["hookEventName"], "PreToolUse")
            self.assertNotIn("permissionDecision", emitted["hookSpecificOutput"])

    def test_it_moves_no_ref_and_no_file(self):
        with tempfile.TemporaryDirectory() as td:
            repo = self._clone_behind(Path(td), commits=3)
            before_head = self._git(repo, "rev-parse", "HEAD")
            before_remote = self._git(repo, "rev-parse", "origin/main")
            self._run(repo, "git checkout -b feat/x")
            self.assertEqual(self._git(repo, "rev-parse", "HEAD"), before_head)
            self.assertEqual(self._git(repo, "rev-parse", "origin/main"),
                             before_remote)
            self.assertEqual(self._git(repo, "status", "--porcelain"), "")

    def test_the_staleness_threshold_is_configurable_not_hardcoded(self):
        with tempfile.TemporaryDirectory() as td:
            repo = self._clone_behind(Path(td), commits=0)
            self._git(repo, "fetch", "-q", "origin")
            fetch_head = repo / ".git" / "FETCH_HEAD"
            old = time.time() - 4 * 3600
            os.utime(fetch_head, (old, old))
            self.assertEqual(
                context(self._run(repo, "git checkout -b feat/x",
                                  env={"SCRY_BRANCH_POINT_FETCH_HOURS": "8"})), "")
            self.assertIn(
                "last fetched 4h ago",
                context(self._run(repo, "git checkout -b feat/x",
                                  env={"SCRY_BRANCH_POINT_FETCH_HOURS": "2"})))

    def test_it_fails_open_on_a_payload_it_cannot_read(self):
        with tempfile.TemporaryDirectory() as td:
            repo = self._clone_behind(Path(td), commits=3)
            result = run_hook("branch_point_advisory.sh", repo, {"cwd": str(repo)})
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout.strip(), "")


class CacheHandoffTests(unittest.TestCase):
    """One handoff per user-work cycle, shortly before the prompt cache goes
    cold, and only a user message re-arms it (designed with Codex,
    2026-09-10). Three scripts share two small files of numbers per session.
    """

    SID = "sess-cache-0001"

    def _env(self, td, **extra):
        env = {
            "SCRY_CACHE_STATE_DIR": str(Path(td) / "state"),
            "SCRY_CACHE_HANDOFF_DIR": str(Path(td) / "handoffs"),
            "CLAUDE_CODE_SESSION_ID": self.SID,
            "SCRY_CACHE_HANDOFF_ONCE": "1",
        }
        env.update(extra)
        return env

    def _statusline(self, td, expires_in, warm=True, inner=None, sid=None):
        env = self._env(td)
        if inner is not None:
            env["SCRY_STATUSLINE_INNER"] = inner
        payload = {
            "session_id": sid or self.SID,
            "cwd": td,
            "model": {"id": "claude-fable-5-1"},
            "prompt_cache": {
                "warm": warm,
                "ttl": "1h",
                "expires_at": int(time.time()) + expires_in,
                "requests": 7,
                "misses": 1,
            },
        }
        return run_hook("cache_deadline_statusline.sh", td, payload, env=env)

    def _arm(self, td):
        return run_hook("cache_handoff_arm.sh", td,
                        {"session_id": self.SID, "prompt": "SECRET PROMPT TEXT"},
                        env=self._env(td))

    def _monitor(self, td, **extra):
        return subprocess.run(
            ["bash", str(ROOT / "cache_handoff_monitor.sh")], cwd=td, text=True,
            capture_output=True, check=True,
            env={**os.environ, **self._env(td, **extra)},
        )

    def _state(self, td):
        p = Path(td) / "state" / f"{self.SID}.state"
        return p.read_text().split() if p.exists() else []

    def test_the_status_line_records_the_deadline_and_nothing_else(self):
        with tempfile.TemporaryDirectory() as td:
            self._statusline(td, 3000)
            rec = json.loads((Path(td) / "state" / f"{self.SID}.deadline").read_text())
            self.assertEqual(set(rec), {"session_id", "observed_at", "warm",
                                        "ttl", "expires_at", "requests"})
            self.assertTrue(rec["warm"])
            self.assertNotIn("model", json.dumps(rec))

    def test_the_status_line_passes_the_payload_to_the_inner_command_and_appends_the_cache(self):
        with tempfile.TemporaryDirectory() as td:
            r = self._statusline(td, 3000, inner="python3 -c 'import json,sys; print(\"inner:\"+json.load(sys.stdin)[\"model\"][\"id\"])'")
            # The bar the user already had, then the cache at the end of it
            # (Matt, 2026-09-10: the TTL "at the bottom" is the point).
            self.assertRegex(r.stdout.strip(), r"^inner:claude-fable-5-1  \U0001f525 (49|50)m$")
            # An inner that prints nothing leaves the cache segment alone.
            r = self._statusline(td, 3000, inner="true")
            self.assertRegex(r.stdout.strip(), r"^\U0001f525 (49|50)m$")

    def test_the_status_line_is_a_cache_status_when_no_inner_command_is_set(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertRegex(self._statusline(td, 3000).stdout, r"\U0001f525 (49|50)m")
            self.assertRegex(self._statusline(td, 3000, warm=False).stdout.strip(), r"^\u2744\ufe0f \x1b\[34mcold\x1b\[0m$")

    def test_the_status_line_says_what_a_cold_cache_will_cost(self):
        # recache_tokens_if_cold is what the next request re-reads at the
        # full rate once the cache has gone cold — the number that decides
        # whether to /clear, /compact, or resume (prompt-caching reference,
        # read 2026-09-10). Inside the last ten minutes it is shown in
        # yellow with the count; cold, in red with the count.
        with tempfile.TemporaryDirectory() as td:
            env = self._env(td)
            def run(expires_in, warm):
                payload = {
                    "session_id": self.SID, "cwd": td,
                    "prompt_cache": {"warm": warm, "ttl": "1h",
                                     "expires_at": int(time.time()) + expires_in,
                                     "requests": 3, "recache_tokens_if_cold": 153_400},
                }
                return run_hook("cache_deadline_statusline.sh", td, payload, env=env).stdout.strip()
            self.assertRegex(run(3000, True), r"^\U0001f525 (49|50)m$")
            self.assertRegex(run(250, True), r"^\U0001f525 \x1b\[33m4m ~153k\x1b\[0m$")
            self.assertEqual(run(-5, False), "\u2744\ufe0f \x1b[34m~153k\x1b[0m")
            # The record the monitor reads is unchanged by any of this.
            rec = json.loads((Path(td) / "state" / f"{self.SID}.deadline").read_text())
            self.assertEqual(set(rec), {"session_id", "observed_at", "warm",
                                        "ttl", "expires_at", "requests"})

    def test_the_status_line_records_nothing_without_cache_data(self):
        with tempfile.TemporaryDirectory() as td:
            run_hook("cache_deadline_statusline.sh", td,
                     {"session_id": self.SID, "cwd": td}, env=self._env(td))
            self.assertFalse((Path(td) / "state").exists())

    def test_a_user_message_arms_and_reads_no_prompt(self):
        with tempfile.TemporaryDirectory() as td:
            r = self._arm(td)
            self.assertEqual(r.stdout, "")
            self.assertEqual(self._state(td)[0], "armed")
            for p in (Path(td) / "state").iterdir():
                self.assertNotIn("SECRET", p.read_text())

    def test_it_asks_once_inside_the_lead_window_and_never_again(self):
        with tempfile.TemporaryDirectory() as td:
            self._arm(td)
            time.sleep(1.1)  # the deadline must be observed after arming
            self._statusline(td, 90)
            first = self._monitor(td)
            self.assertIn("goes cold", first.stdout)
            self.assertEqual(first.stdout.count("\n"), 1)
            path = self._state(td)[2]
            self.assertIn(os.path.basename(td.rstrip("/")), path)
            self.assertIn(self.SID[:8], path)
            self.assertTrue(Path(path).parent.is_dir())
            self.assertEqual(self._state(td)[0], "requested")
            # The handoff itself refreshes the cache; that must not re-arm.
            self._statusline(td, 3500)
            self.assertEqual(self._monitor(td).stdout, "")
            self._statusline(td, 90)
            self.assertEqual(self._monitor(td).stdout, "")

    def test_a_new_user_message_starts_a_new_cycle(self):
        with tempfile.TemporaryDirectory() as td:
            self._arm(td)
            time.sleep(1.1)
            self._statusline(td, 90)
            self.assertIn("goes cold", self._monitor(td).stdout)
            self._arm(td)
            time.sleep(1.1)
            self._statusline(td, 90)
            self.assertIn("goes cold", self._monitor(td).stdout)

    def test_it_is_silent_when_not_armed_or_outside_the_window(self):
        with tempfile.TemporaryDirectory() as td:
            self._statusline(td, 90)
            self.assertEqual(self._monitor(td).stdout, "")  # never armed
            self._arm(td)
            time.sleep(1.1)
            self._statusline(td, 3000)
            self.assertEqual(self._monitor(td).stdout, "")  # far from expiry
            self._statusline(td, 90, warm=False)
            self.assertEqual(self._monitor(td).stdout, "")  # already cold
            self.assertEqual(self._state(td)[0], "armed")

    def test_a_deadline_observed_before_arming_is_not_this_cycles(self):
        with tempfile.TemporaryDirectory() as td:
            self._statusline(td, 90)
            time.sleep(1.1)
            self._arm(td)
            self.assertEqual(self._monitor(td).stdout, "")

    def test_a_missed_deadline_is_silent_here_and_said_on_the_next_turn(self):
        with tempfile.TemporaryDirectory() as td:
            self._arm(td)
            time.sleep(1.1)
            self._statusline(td, -5)
            self.assertEqual(self._monitor(td).stdout, "")
            self.assertEqual(self._state(td)[0], "missed")
            note = context(self._arm(td))
            self.assertIn("expired at", note)
            self.assertEqual(self._state(td)[0], "armed")
            self.assertEqual(self._arm(td).stdout, "")  # said once

    def test_it_notes_the_saved_handoff_silently(self):
        with tempfile.TemporaryDirectory() as td:
            self._arm(td)
            time.sleep(1.1)
            self._statusline(td, 90)
            self._monitor(td)
            path = self._state(td)[2]
            Path(path).write_text("# handoff\n")
            self.assertEqual(self._monitor(td).stdout, "")
            self.assertEqual(self._state(td)[0], "saved")

    def test_the_lead_time_is_configurable_not_hardcoded(self):
        with tempfile.TemporaryDirectory() as td:
            self._arm(td)
            time.sleep(1.1)
            self._statusline(td, 500)
            self.assertEqual(self._monitor(td).stdout, "")
            self.assertIn("goes cold", self._monitor(td, SCRY_CACHE_HANDOFF_LEAD_SECONDS="600").stdout)

    def test_opt_out_exits_silently_and_a_missing_session_id_says_so(self):
        with tempfile.TemporaryDirectory() as td:
            self._arm(td)
            time.sleep(1.1)
            self._statusline(td, 90)
            self.assertEqual(self._monitor(td, SCRY_CACHE_HANDOFF="0").stdout, "")
            self.assertEqual(self._state(td)[0], "armed")
            self.assertIn("unavailable", self._monitor(td, CLAUDE_CODE_SESSION_ID="").stdout)


class StatuslineTests(unittest.TestCase):
    """statusline.sh owns the whole bar (Matt, 2026-09-10): folder, branch
    only when off the default, N uncommitted / N unpushed only when non-zero,
    model, session cost, other live sessions here, the 7-day window with its
    reset day, context as tokens over the window, then the cache segment."""

    def _payload(self, cwd, **over):
        now = int(time.time())
        p = {
            "session_id": "sess-bar-0001",
            "cwd": cwd,
            "workspace": {"current_dir": cwd},
            "model": {"id": "claude-fable-5-1", "display_name": "Fable 5.1"},
            "cost": {"total_cost_usd": 4.2},
            "rate_limits": {
                "five_hour": {"used_percentage": 31, "resets_at": now + 7200},
                "seven_day": {"used_percentage": 64, "resets_at": now + 3 * 86400},
            },
            "context_window": {"total_input_tokens": 42000, "context_window_size": 200000, "used_percentage": 21},
            "prompt_cache": {"warm": True, "ttl": "1h", "expires_at": now + 2400, "recache_tokens_if_cold": 42000},
        }
        p.update(over)
        return p

    def _run(self, td, cwd, **over):
        env = {
            "SCRY_CACHE_STATE_DIR": str(Path(td) / "state"),
            "SCRY_CLAUDE_PROJECTS": str(Path(td) / "projects"),
        }
        return run_hook("statusline.sh", cwd, self._payload(cwd, **over), env=env).stdout.rstrip("\n")

    def _repo(self, td):
        origin = Path(td) / "origin"
        subprocess.run(["git", "init", "-q", "-b", "main", str(origin)], check=True)
        subprocess.run(["git", "-C", str(origin), "commit", "-q", "--allow-empty", "-m", "init"], check=True,
                       env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})
        work = Path(td) / "scry"
        subprocess.run(["git", "clone", "-q", str(origin), str(work)], check=True, capture_output=True)
        return work

    def test_a_clean_primary_on_main_is_quiet_and_ordered_slow_to_fast(self):
        with tempfile.TemporaryDirectory() as td:
            work = self._repo(td)
            day = time.strftime("%a", time.localtime(time.time() + 3 * 86400))
            self.assertRegex(self._run(td, str(work)),
                r"^ \x1b\[1;36mscry\x1b\[0m  Fable 5\.1  \$4\.20  🗓️ 64% " + day + r"  🌕 42k/200k  🔥 (39|40)m$")

    def test_a_feature_branch_with_work_shows_branch_uncommitted_and_unpushed(self):
        with tempfile.TemporaryDirectory() as td:
            work = self._repo(td)
            env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
            subprocess.run(["git", "-C", str(work), "checkout", "-q", "-b", "feature-x"], check=True)
            for m in ("one", "two"):
                subprocess.run(["git", "-C", str(work), "commit", "-q", "--allow-empty", "-m", m], check=True, env=env)
            for f in ("a", "b", "c"):
                (work / f).write_text("x")
            out = self._run(td, str(work))
            self.assertIn(" \x1b[1;36mscry\x1b[0m  \x1b[33mfeature-x\x1b[0m  \x1b[33m3 uncommitted\x1b[0m  \x1b[33m2 unpushed\x1b[0m  Fable 5.1", out)
            # In a worktree named after the branch, the branch is not repeated.
            wt = Path(td) / "feature-x"
            subprocess.run(["git", "-C", str(work), "worktree", "add", "-q", str(wt), "-b", "feature-x-wt", "feature-x"],
                           check=True, capture_output=True)
            subprocess.run(["git", "-C", str(wt), "checkout", "-q", "--detach"], check=True)
            subprocess.run(["git", "-C", str(work), "checkout", "-q", "main"], check=True)
            subprocess.run(["git", "-C", str(wt), "checkout", "-q", "feature-x"], check=True)
            out = self._run(td, str(wt))
            self.assertTrue(out.startswith(" \x1b[1;36mfeature-x\x1b[0m  \x1b[33m2 unpushed\x1b[0m  Fable 5.1"), out)

    def test_week_turns_yellow_then_red_and_cold_cache_is_blue(self):
        with tempfile.TemporaryDirectory() as td:
            work = self._repo(td)
            now = int(time.time())
            y = self._run(td, str(work), rate_limits={"seven_day": {"used_percentage": 72, "resets_at": now + 86400}})
            self.assertIn("🗓️ \x1b[33m72% ", y)
            r = self._run(td, str(work),
                          rate_limits={"seven_day": {"used_percentage": 93, "resets_at": now + 86400}},
                          prompt_cache={"warm": False, "recache_tokens_if_cold": 42000})
            self.assertIn("🗓️ \x1b[31m93% ", r)
            self.assertTrue(r.endswith("❄️ \x1b[34m~42k\x1b[0m"), r)

    def test_other_live_sessions_in_this_directory_are_counted_and_self_is_not(self):
        with tempfile.TemporaryDirectory() as td:
            work = self._repo(td)
            enc = re.sub(r"[/._]", "-", str(work.resolve()))
            pdir = Path(td) / "projects" / enc
            pdir.mkdir(parents=True)
            (pdir / "sess-bar-0001.jsonl").write_text("self")
            (pdir / "other-1.jsonl").write_text("x")
            (pdir / "other-2.jsonl").write_text("x")
            old = pdir / "old.jsonl"
            old.write_text("x")
            os.utime(old, (time.time() - 3600, time.time() - 3600))
            self.assertIn("👥 \x1b[33m2\x1b[0m", self._run(td, str(work)))

    def test_a_missing_field_drops_only_its_segment(self):
        with tempfile.TemporaryDirectory() as td:
            work = self._repo(td)
            out = self._run(td, str(work), cost={}, rate_limits={}, context_window={})
            self.assertRegex(out, r"^ \x1b\[1;36mscry\x1b\[0m  Fable 5\.1  🔥 (39|40)m$")

    def test_the_bar_still_renders_outside_a_repository(self):
        with tempfile.TemporaryDirectory() as td:
            plain = Path(td) / "notes"
            plain.mkdir()
            out = self._run(td, str(plain))
            self.assertTrue(out.startswith(" \x1b[1;36mnotes\x1b[0m  Fable 5.1  $4.20"), out)
