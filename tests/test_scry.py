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
# These tests run inside real iTerm2 sessions; tab_colour.sh would paint the
# tab of whoever runs them.
os.environ.pop("LC_TERMINAL", None)


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


def hold_open(test, path):
    """A process with `path` as its stdout, the way the shell running a
    command holds its task file until it exits (read live 2026-09-22:
    `zsh ... 1w ... tasks/b*.output`). Killed when the test ends."""
    with open(path, "ab") as out:
        proc = subprocess.Popen(["tail", "-f", "/dev/null"], stdout=out,
                                stderr=subprocess.DEVNULL)

    def stop():
        proc.kill()
        proc.wait()
    test.addCleanup(stop)
    time.sleep(0.05)
    return proc


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
        self.assertEqual(len(commands), 10)
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
        # session left, its transcript, what resuming it costs, and whether
        # Scry asked it for a summary, from Scry's own state file (Matt,
        # 2026-09-13: the summary lives in the conversation, not a file).
        # Insight, not action (council + Matt, 2026-09-10). Never the
        # transcript's prompts.
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
            env = {"HOME": str(base), "CODEX_HOME": str(base / ".codex"),
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
            # The monitor asked it for a summary and no user message followed.
            asked = int(time.time()) - 600
            state = base / "deadline" / "0123abcd-prev-session.state"
            state.write_text(f"requested {asked}\n")
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
            hhmm = time.strftime("%H:%M", time.localtime(asked))
            self.assertIn(f"Scry asked it for a summary at {hhmm}", report)
            self.assertIn("last message(s) should contain that summary", report)
            self.assertIn("Read the end of its transcript first", report)
            self.assertIn("send a Sonnet subagent to search the transcript", report)
            self.assertNotIn("handoff was written", report)
            self.assertNotIn("SECRET PROMPT", report)
            self.assertNotIn("Unrelated work", report)      # the guess would have said this
            self.assertNotIn("GUESS", report)
            self.assertNotIn("IT LEFT WORK RUNNING", report)  # it left nothing
            self.assertFalse(rec_path.exists())              # read once, then gone

            # A /clear the monitor has already seen turns "requested" into
            # "superseded"; the request is still the last thing that happened.
            state.write_text(f"superseded {asked + 5} requested:{asked}\n")
            run_hook("clear_record.sh", repo, {"session_id": "0123abcd-prev-session",
                                              "transcript_path": str(prev), "cwd": str(repo),
                                              "reason": "clear"}, env)
            report = context(run_hook("fleet.sh", repo, payload, env))
            self.assertIn(f"Scry asked it for a summary at {hhmm}", report)
            # A user message after the request re-armed it; so did a clear of
            # an armed session: no summary was asked for since.
            for later in (f"armed {asked + 60}\n", f"superseded {asked + 60} armed:{asked + 30}\n"):
                state.write_text(later)
                run_hook("clear_record.sh", repo, {"session_id": "0123abcd-prev-session",
                                                  "transcript_path": str(prev), "cwd": str(repo),
                                                  "reason": "clear"}, env)
                report = context(run_hook("fleet.sh", repo, payload, env))
                self.assertIn("No summary was requested for it; its transcript is the record.", report)
                self.assertNotIn("asked it for a summary", report)

            # With no record (the SessionEnd hook did not run) the guess stands
            # in, says so, and the cold-cache cost is stated.
            (base / "deadline" / "0123abcd-prev-session.deadline").unlink()
            state.unlink()
            report = context(run_hook("fleet.sh", repo, payload, env))
            self.assertIn("a GUESS", report)
            self.assertIn("Unrelated work", report)
            self.assertIn("cold or unrecorded", report)
            self.assertIn("No summary was requested for it", report)

            # A plain start keeps the old rule: the just-written transcript
            # counts as live and is not reported as the last session.
            report = context(run_hook("fleet.sh", repo, {**payload, "source": "startup"}, env))
            self.assertNotIn("began with /clear", report)
            self.assertNotIn("0123abcd", report)

    def test_health_reports_commits_another_session_pushed_to_this_branch(self):
        # 2026-09-11: four commits landed on a shared branch while a session
        # held an older HEAD. It found out when an edit failed to apply against
        # text that had already changed, one --force from reverting work it had
        # never read. health.sh reported commits that exist only on this disk
        # and never the reverse. It also gated the branch block on being in a
        # linked worktree, so the primary checkout — that session's case
        # exactly — got nothing at all.
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            origin = base / "origin.git"
            subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)

            def git(wt, *a):
                return subprocess.run(["git", "-C", str(wt), *a], check=True,
                                      capture_output=True, text=True).stdout

            repo = base / "repo"
            subprocess.run(["git", "clone", "-q", str(origin), str(repo)], check=True)
            git(repo, "config", "user.email", "a@b.c")
            git(repo, "config", "user.name", "First Session")
            (repo / "f.txt").write_text("one\n")
            git(repo, "add", "-A")
            git(repo, "commit", "-qm", "first")
            git(repo, "branch", "-M", "main")
            git(repo, "push", "-q", "-u", "origin", "main")
            git(repo, "checkout", "-q", "-b", "shared-branch")
            git(repo, "push", "-q", "-u", "origin", "shared-branch")

            # A second session clones the same branch and pushes to it.
            other = base / "other"
            subprocess.run(["git", "clone", "-q", "--branch", "shared-branch",
                            str(origin), str(other)], check=True)
            git(other, "config", "user.email", "d@e.f")
            git(other, "config", "user.name", "Second Session")
            (other / "f.txt").write_text("two\n")
            git(other, "add", "-A")
            git(other, "commit", "-qm", "landed while you were reading")
            git(other, "push", "-q", "origin", "shared-branch")

            # The first session's checkout is now behind its own branch and
            # has not noticed. This is the primary checkout, not a worktree.
            payload = {"cwd": str(repo), "session_id": "s1",
                       "transcript_path": str(base / "s1.jsonl"), "source": "startup"}
            report = context(run_hook("health.sh", repo, payload,
                                      {"HOME": str(base)}))
            self.assertIn("SOMEONE ELSE PUSHED TO THIS BRANCH", report)
            self.assertIn("origin/shared-branch is 1 commit(s) ahead", report)
            self.assertIn("Second Session", report)
            self.assertIn("this tree is not the branch", report)
            self.assertIn("HEAD..origin/shared-branch", report)

            # Silent once the checkout carries them — a signal that fires every
            # session stops carrying information (README, the output budget).
            git(repo, "pull", "-q", "--ff-only", "origin", "shared-branch")
            report = context(run_hook("health.sh", repo, payload,
                                      {"HOME": str(base)}))
            self.assertNotIn("SOMEONE ELSE PUSHED", report)

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
            hold_open(self, tasks / "running.output")
            (tasks / "zzdone.output").write_text("done\n\n[exited with code 0]\n")
            # A foreground call's leftover: no marker, no holder, finished.
            (tasks / "zzfore.output").write_text("diff --git a/x b/x\n")
            (tasks / "agent.output").symlink_to(mine / "builder.jsonl")
            stale = claude_dir / prev_id / "subagents" / "old.jsonl"
            stale.write_text(json.dumps({"cwd": str(repo)}) + "\n")
            os.utime(stale, (time.time() - 7200, time.time() - 7200))
            (tasks / "stale-agent.output").symlink_to(stale)

            env = {"HOME": str(base), "CODEX_HOME": str(base / ".codex"),
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
            # Its id is "zzdone", not "finished", so this whole-report
            # assertion tests the id and not the advisory's prose.
            self.assertIn("2 task(s) it registered that have not recorded an exit: "
                          "agent (agent), running (shell)", report)
            self.assertNotIn("zzdone", report)
            self.assertNotIn("zzfore", report)
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









    # ── Session disposal: scratch Markdown (Stop) ──────────────────────────












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
        for hook in ("md_creation_advisory.sh",):
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
        self.assertTrue(any("main_drift_advisory.sh" in c for c in sub), sub)
        # Nothing speaks at Stop (2026-09-27): the only end-shaped event that
        # delivers fires at the end of every turn, so an end-of-session note
        # landed mid-conversation and cost the model an extra turn to relay.
        self.assertNotIn("Stop", hooks["hooks"])
        end = [h["command"] for group in hooks["hooks"].get("SessionEnd", [])
               for h in group["hooks"]]
        self.assertEqual([c.rsplit("/", 1)[-1] for c in end],
                         ["clear_record.sh", "tab_colour.sh none || true"])
        for command in sub + end:
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


class EngineGuardTests(unittest.TestCase):
    """The production engine: health read through `fly checks list`, sessions
    open from this Mac. A fake fly answers from a file; no real fly runs."""

    APP = "trellys-engine"
    PASSING = '[{"name": "status", "status": "passing"}]'
    CRITICAL = '[{"name": "status", "status": "critical"}]'

    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.base = Path(td.name)
        self.guard = self.base / "guard"
        (self.guard / "slots.d").mkdir(parents=True)
        self.answer = self.base / "answer.json"
        self.calls = self.base / "calls"
        self.fly = self.base / "fakefly"
        self.fly.write_text(
            "#!/bin/sh\n"
            f'echo x >> "{self.calls}"\n'
            f'cat "{self.answer}" || exit 1\n')
        self.fly.chmod(0o755)
        self.answer.write_text(self.PASSING)
        # Real engine sessions from other tabs show up in `ps`; the shim hides
        # every engine process except the ones these tests start (their
        # command line ends at the app name).
        shim = self.base / "bin"
        shim.mkdir()
        (shim / "ps").write_text(
            "#!/bin/sh\n"
            "/bin/ps \"$@\" | awk '!/trellys-engine/ || /-a trellys-engine$/'\n")
        (shim / "ps").chmod(0o755)
        self.env = {"ENGINE_GUARD_HOME": str(self.guard),
                    "ENGINE_GUARD_REAL_FLY": str(self.fly),
                    "PATH": f"{shim}:{os.environ['PATH']}"}

    def say(self, text):
        self.answer.write_text(text)
        (self.guard / "health").unlink(missing_ok=True)

    def start(self, hook, payload):
        return run_hook(hook, self.base, payload, self.env)

    def bash(self, command):
        return {"hook_event_name": "PreToolUse", "tool_name": "Bash",
                "cwd": str(self.base), "tool_input": {"command": command}}

    def denial(self, result):
        if not result.stdout.strip():
            return ""
        out = json.loads(result.stdout)["hookSpecificOutput"]
        self.assertEqual(out["permissionDecision"], "deny")
        return out["permissionDecisionReason"]

    def hold_slot(self, pid, desc="a test"):
        started = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)],
                                 capture_output=True, text=True).stdout.strip()
        (self.guard / "slots.d" / str(pid)).write_text(f"{started}\n{int(time.time())}\n{desc}\n")

    def sleeper(self):
        proc = subprocess.Popen(["sleep", "60"])
        self.addCleanup(lambda: (proc.kill(), proc.wait()))
        return proc

    CONNECT = "fly ssh console -a trellys-engine -C 'ls'"

    def test_session_start_is_silent_when_the_engine_is_passing_and_nothing_is_open(self):
        self.assertEqual(self.start("engine.sh", {}).stdout, "")

    def test_session_start_is_silent_without_the_guard_directory(self):
        self.env["ENGINE_GUARD_HOME"] = str(self.base / "absent")
        self.assertEqual(self.start("engine.sh", {}).stdout, "")

    def test_session_start_says_when_the_health_check_is_critical(self):
        self.say(self.CRITICAL)
        text = context(self.start("engine.sh", {}))
        self.assertIn("critical", text)
        self.assertIn(self.APP, text)

    def test_session_start_says_unknown_when_health_cannot_be_read(self):
        self.say("not json")
        text = context(self.start("engine.sh", {}))
        self.assertIn("not checked", text)
        self.assertNotIn("passing", text)

    def test_session_start_counts_open_sessions(self):
        self.hold_slot(self.sleeper().pid)
        text = context(self.start("engine.sh", {}))
        self.assertIn("1 of 4", text)

    def test_a_slot_whose_process_is_gone_is_not_counted(self):
        proc = self.sleeper()
        self.hold_slot(proc.pid)
        proc.kill()
        proc.wait()
        self.assertEqual(self.start("engine.sh", {}).stdout, "")

    def test_a_slot_reused_by_another_process_is_not_counted(self):
        proc = self.sleeper()
        (self.guard / "slots.d" / str(proc.pid)).write_text("Thu Jan  1 00:00:00 1970\n0\nold\n")
        self.assertEqual(self.start("engine.sh", {}).stdout, "")

    def test_session_start_counts_a_fly_process_that_took_no_slot(self):
        proc = subprocess.Popen(["flyctl", "ssh", "console", "-a", self.APP],
                                executable="/usr/bin/yes", stdout=subprocess.DEVNULL)
        self.addCleanup(lambda: (proc.kill(), proc.wait()))
        time.sleep(0.1)
        self.assertIn("1 of 4", context(self.start("engine.sh", {})))

    def test_session_start_names_a_wrapper_that_lost_the_guard(self):
        wrapper = self.base / "fly"
        wrapper.write_text("#!/bin/sh\nexec flyctl \"$@\"\n")
        (self.guard / "wrappers").write_text(f"{wrapper}\n")
        text = context(self.start("engine.sh", {}))
        self.assertIn(str(wrapper), text)
        wrapper.write_text("#!/bin/sh\nexec engine-guard fly \"$@\"\n")
        self.assertEqual(self.start("engine.sh", {}).stdout, "")

    def test_session_start_off_switch(self):
        self.say(self.CRITICAL)
        self.env["SCRY_ENGINE_GUARD"] = "0"
        self.assertEqual(self.start("engine.sh", {}).stdout, "")
        del self.env["SCRY_ENGINE_GUARD"]
        (self.guard / "OFF").write_text("")
        self.assertEqual(self.start("engine.sh", {}).stdout, "")

    def test_guard_denies_an_engine_connection_while_critical(self):
        self.say(self.CRITICAL)
        reason = self.denial(self.start("engine_guard.sh", self.bash(self.CONNECT)))
        self.assertIn("failing its Fly health check", reason)

    def test_guard_allows_an_engine_connection_while_passing(self):
        self.assertEqual(self.start("engine_guard.sh", self.bash(self.CONNECT)).stdout, "")

    def test_guard_allows_when_health_is_unknown(self):
        self.say("not json")
        self.assertEqual(self.start("engine_guard.sh", self.bash(self.CONNECT)).stdout, "")

    def test_guard_denies_at_the_cap_and_lists_what_is_open(self):
        for i in range(4):
            self.hold_slot(self.sleeper().pid, f"session {i}")
        reason = self.denial(self.start("engine_guard.sh", self.bash(self.CONNECT)))
        self.assertIn("4 are already open", reason)
        self.assertIn("session 3", reason)

    def test_guard_cap_follows_the_slots_file(self):
        (self.guard / "slots").write_text("1\n")
        self.hold_slot(self.sleeper().pid)
        self.assertIn("cap 1", self.denial(self.start("engine_guard.sh", self.bash(self.CONNECT))))

    def test_guard_sees_a_script_that_connects(self):
        self.say(self.CRITICAL)
        (self.base / "probe.sh").write_text(f"#!/bin/sh\nfly ssh console -a {self.APP} -C ls\n")
        (self.base / "other.sh").write_text("#!/bin/sh\necho hi\n")
        self.assertIn("probe.sh", self.denial(self.start("engine_guard.sh", self.bash("bash probe.sh"))))
        self.assertEqual(self.start("engine_guard.sh", self.bash("bash other.sh")).stdout, "")

    def test_guard_ignores_commands_that_do_not_reach_the_engine(self):
        self.say(self.CRITICAL)
        for command in ("ls -la", "fly status -a trellys-engine", "fly ssh console -a other-app",
                        "git log --oneline"):
            with self.subTest(command=command):
                self.assertEqual(self.start("engine_guard.sh", self.bash(command)).stdout, "")

    def test_guard_leaves_alone_text_that_only_mentions_the_engine(self):
        self.say(self.CRITICAL)
        (self.base / "probe.sh").write_text(f"#!/bin/sh\nfly ssh console -a {self.APP} -C ls\n")
        for command in (
            f"git commit -m 'fix fly ssh console -a {self.APP} retries'",
            f"git commit -m 'one\ntwo: fly ssh console -a {self.APP}'",
            f"cat <<'EOF' > notes.txt\nfly ssh console -a {self.APP}\nEOF",
            f"echo fly ssh console -a {self.APP} >> notes.txt",
            "cat probe.sh", "git add probe.sh", "less probe.sh",
            "python3 -m unittest discover probe.sh",
            f"fly ssh console -a {self.APP}-staging",
            f"fly logs -a {self.APP} && ls console",
        ):
            with self.subTest(command=command):
                self.assertEqual(self.start("engine_guard.sh", self.bash(command)).stdout, "")

    def test_guard_denies_every_way_of_running_the_connection(self):
        self.say(self.CRITICAL)
        (self.base / "probe.sh").write_text(f"#!/bin/sh\nfly ssh console -a {self.APP} -C ls\n")
        (self.base / "probe.sh").chmod(0o755)
        (self.base / "probe.py").write_text(f"import subprocess\nsubprocess.run(['sh','-c','fly ssh console -a {self.APP}'])\n")
        for command in (
            f"fly ssh console -a {self.APP}",
            f"fly ssh console --app {self.APP} -C ls",
            f"fly ssh console --app={self.APP}",
            f"flyctl machine exec -a {self.APP} abc 'ls'",
            f"cd /tmp && FLY_X=1 env fly ssh console -a {self.APP}",
            f"ls | fly ssh console -a {self.APP}",
            f"bash -c \"fly ssh console -a {self.APP}\"",
            "./probe.sh", "bash probe.sh", "sh -x probe.sh", "python3 probe.py",
            f"echo hi; {self.CONNECT}",
        ):
            with self.subTest(command=command):
                self.assertIn("health check", self.denial(self.start("engine_guard.sh", self.bash(command))))

    def test_guard_reads_the_app_from_fly_toml_and_fly_app(self):
        self.say(self.CRITICAL)
        proj = self.base / "apps" / "engine"
        proj.mkdir(parents=True)
        (proj / "fly.toml").write_text(f"app = '{self.APP}'\nprimary_region = 'ord'\n")
        other = self.base / "apps" / "web"
        other.mkdir()
        (other / "fly.toml").write_text("app = 'web'\n")
        for command in ("cd apps/engine && fly ssh console", "fly ssh console -c apps/engine/fly.toml",
                        f"FLY_APP={self.APP} fly ssh console"):
            with self.subTest(command=command):
                self.assertIn("health check", self.denial(self.start("engine_guard.sh", self.bash(command))))
        for command in ("cd apps/web && fly ssh console", "fly ssh console"):
            with self.subTest(command=command):
                self.assertEqual(self.start("engine_guard.sh", self.bash(command)).stdout, "")

    def test_guard_sees_a_script_that_builds_fly_arguments_as_a_list(self):
        self.say(self.CRITICAL)
        (self.base / "list.py").write_text(
            f"import subprocess\nsubprocess.run(['fly', 'ssh', 'console', '-a', '{self.APP}'])\n")
        self.assertIn("list.py", self.denial(self.start("engine_guard.sh", self.bash("python3 list.py"))))

    def test_a_burst_of_sessions_makes_one_fly_call(self):
        procs = [subprocess.Popen(["bash", str(ROOT / "engine_guard.sh")], stdin=subprocess.PIPE,
                                  stdout=subprocess.DEVNULL, text=True, cwd=self.base,
                                  env={**os.environ, **self.env}) for _ in range(6)]
        for proc in procs:
            proc.communicate(json.dumps(self.bash(self.CONNECT)))
        self.assertEqual(len(self.calls.read_text().split()), 1)

    def test_guard_denies_the_shapes_review_found(self):
        self.say(self.CRITICAL)
        connect = f"fly ssh console -a {self.APP} -C ls"
        (self.base / "connect").write_text(f"#!/bin/sh\n{connect}\n")
        (self.base / "connect").chmod(0o755)
        (self.base / "connect.js").write_text(f"require('child_process').execSync('{connect}')\n")
        (self.base / "probe.sh").write_text(f"#!/bin/sh\n{connect}\n")
        for command in (
            f"# open a console\n{connect}",
            f"echo a#b && {connect}",
            f"cat <<EOF > notes.txt\nhello\nEOF\n{connect}",
            f"bash <<'EOF'\n{connect}\nEOF",
            f"python3 - <<'PY'\nimport subprocess\nsubprocess.run(['fly','ssh','console','-a','{self.APP}'])\nPY",
            "./connect", "bash connect", "node connect.js",
            f"for i in 1 2; do {connect}; done",
            f"if {connect}; then echo ok; fi",
            f"{{ {connect}; }} 2>&1",
            f"timeout 30 {connect}", f"sudo {connect}", f"echo x | xargs {connect}",
            "source ./probe.sh", ". ./probe.sh",
            f"export FLY_APP={self.APP}; fly ssh console",
            f"FLY_APP={self.APP}; fly ssh console",
            f"bash -lc '{connect}'", f"bash -e -u -c '{connect}'", f"sh -ec '{connect}'",
            f"fly -t TOKEN ssh console -a {self.APP}",
            f"out=`{connect}`",
            f"python3 -c \"import subprocess; subprocess.run(['fly','ssh','console','-a','{self.APP}'])\"",
        ):
            with self.subTest(command=command):
                self.assertIn("health check", self.denial(self.start("engine_guard.sh", self.bash(command))))

    def test_guard_leaves_alone_files_that_only_describe_the_connection(self):
        self.say(self.CRITICAL)
        for command in (f"python3 {ROOT}/scanners/engine.py status",
                        "mkfifo pipe.sh && timeout 2 bash pipe.sh"):
            with self.subTest(command=command):
                self.assertEqual(self.start("engine_guard.sh", self.bash(command)).stdout, "")

    def test_guard_denies_the_shapes_second_review_found(self):
        self.say(self.CRITICAL)
        connect = f"fly ssh console -a {self.APP}"
        (self.base / "probe.sh").write_text(f"#!/bin/sh\n{connect}\n")
        for command in (
            f"{connect} # it's done",
            f"# we can't wait\n{connect}",
            f"cd apps/engine && bash <<'X'\n{connect}\nX",
            f"echo hi; python3 - <<'PY'\nsubprocess.run(['fly','ssh','console','-a','{self.APP}'])\nPY",
            f"fly \\\n  ssh console -a {self.APP}", f"fly ssh \\\n  console -a {self.APP}",
            f"out=`{connect}`", f"echo `{connect}`",
            f"node -e \"require('child_process').execSync('{connect}')\"",
            f"sh -euo pipefail -c '{connect}'",
            f"sudo -u matt {connect}", f"sudo -E {connect}", f"nice -n 10 {connect}",
            f"env -i PATH=/bin {connect}", f"ls | xargs -n1 {connect}", f"timeout -s KILL 30 {connect}",
            f"caffeinate -i {connect}", f"eval {connect}", f"eval '{connect}'",
            "/usr/bin/env bash probe.sh",
            f"APP={self.APP}; fly ssh console -a \"$APP\"", f"APP={self.APP}; fly ssh console --app ${{APP}}",
        ):
            with self.subTest(command=command):
                self.assertIn("health check", self.denial(self.start("engine_guard.sh", self.bash(command))))

    def test_guard_leaves_alone_a_hyphenated_heredoc_and_a_huge_word(self):
        self.say(self.CRITICAL)
        for command in (f"cat <<'END-OF' > notes.txt\nfly ssh console -a {self.APP}\nEND-OF",
                        "echo '" + "x" * 300_000 + "'"):
            with self.subTest(command=command[:40]):
                self.assertEqual(self.start("engine_guard.sh", self.bash(command)).stdout, "")

    def test_guard_off_switch(self):
        self.say(self.CRITICAL)
        self.env["SCRY_ENGINE_GUARD"] = "0"
        self.assertEqual(self.start("engine_guard.sh", self.bash(self.CONNECT)).stdout, "")

    def test_guard_denial_gives_observations_not_orders(self):
        self.say(self.CRITICAL)
        reason = self.denial(self.start("engine_guard.sh", self.bash(self.CONNECT))).lower()
        for phrase in ("what to do", "you should", "do not ", "you must"):
            self.assertNotIn(phrase, reason)

    def test_one_fly_call_serves_a_minute_of_readings(self):
        for _ in range(3):
            self.start("engine.sh", {})
            self.start("engine_guard.sh", self.bash(self.CONNECT))
        self.assertEqual(len(self.calls.read_text().split()), 1)

    def test_a_reading_the_wrapper_wrote_is_reused(self):
        (self.guard / "health").write_text(f"{int(time.time())} critical critical=status\n")
        self.assertIn("critical", context(self.start("engine.sh", {})))
        self.assertFalse(self.calls.exists())


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


class PrOpenAdvisoryTests(unittest.TestCase):
    """Opening a pull request is the last cheap moment for a second reader.

    Observed 2026-10-12 in trellys-app: auto-merge is armed on open and merge
    deploys, and a session built every finding a reviewer returned, including
    a guard against a worry the reviewer called an unconfirmed suspicion.
    """

    @staticmethod
    def _git(cwd, *args):
        return subprocess.run(["git", "-C", str(cwd), *args], check=True,
                              capture_output=True, text=True).stdout.strip()

    def _branch_with(self, base, files):
        """A clone on a feature branch whose commit adds `files` {path: text}."""
        origin = base / "origin"
        repo = base / "repo"
        g = self._git
        origin.mkdir(parents=True)
        g(origin, "init", "-q", "-b", "main")
        g(origin, "config", "user.name", "Scry Test")
        g(origin, "config", "user.email", "scry@example.test")
        (origin / "seed.txt").write_text("seed\n")
        g(origin, "add", "seed.txt")
        g(origin, "commit", "-qm", "first")
        subprocess.run(["git", "clone", "-q", str(origin), str(repo)],
                       check=True, capture_output=True)
        g(repo, "config", "user.name", "Scry Test")
        g(repo, "config", "user.email", "scry@example.test")
        g(repo, "checkout", "-q", "-b", "feat/thing")
        for path, text in files.items():
            (repo / path).parent.mkdir(parents=True, exist_ok=True)
            (repo / path).write_text(text)
            g(repo, "add", path)
        g(repo, "commit", "-qm", "change")
        return repo

    def _run(self, repo, command, state, session="pr-1"):
        payload = {
            "session_id": session,
            "transcript_path": "/dev/null",
            "cwd": str(repo),
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": command},
        }
        return run_hook("pr_open_advisory.sh", repo, payload,
                        env={"TMPDIR": str(state)})

    def _code(self, n):
        return {"app.py": "".join(f"x{i} = {i}\n" for i in range(n))}

    def test_it_states_the_size_and_the_evidence_before_a_pr_opens(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            repo = self._branch_with(base, self._code(40))
            report = context(self._run(repo, 'gh pr create --title "x" --body "y"', base / "state"))
            self.assertIn("changes 40 lines across 1 files", report)
            self.assertIn("91.5% → 88.0%", report)
            self.assertNotIn("A finding is input", report)
            self.assertIn("`/code-review`, a separate agent, or a person", report)

    def test_it_is_said_once_per_branch_per_session(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            repo = self._branch_with(base, self._code(40))
            state = base / "state"
            self.assertNotEqual(context(self._run(repo, "gh pr create", state)), "")
            self.assertEqual(context(self._run(repo, "gh pr create", state)), "")
            self.assertNotEqual(context(self._run(repo, "gh pr create", state, session="pr-2")), "")

    def test_it_is_silent_for_other_commands(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            repo = self._branch_with(base, self._code(40))
            for cmd in ("gh pr view", "gh pr merge --auto", "git push", "gh pr checks"):
                self.assertEqual(context(self._run(repo, cmd, base / "state")), "", cmd)

    def test_it_is_silent_for_a_tiny_change(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            repo = self._branch_with(base, self._code(5))
            self.assertEqual(context(self._run(repo, "gh pr create", base / "state")), "")

    def test_it_is_silent_when_only_prose_changed(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            text = "".join(f"line {i}\n" for i in range(60))
            repo = self._branch_with(base, {"README.md": text, "docs/guide.txt": text})
            self.assertEqual(context(self._run(repo, "gh pr create", base / "state")), "")

    def test_it_never_fails_outside_a_repo(self):
        with tempfile.TemporaryDirectory() as td:
            result = self._run(Path(td), "gh pr create", Path(td) / "state")
            self.assertEqual(result.returncode, 0)
            self.assertEqual(context(result), "")


class ReviewHandbackAdvisoryTests(unittest.TestCase):
    """A reviewer's report comes back; the note separates kinds of finding.

    Observed 2026-10-12: every finding of an adverse review was built,
    including a guard against a worry the reviewer called unconfirmed.
    """

    @staticmethod
    def _handback(report):
        return ('Another Claude session sent a message:\n<agent-message from="a1">\n'
                "[Subagent hand-back] The text below is the final report of a subagent "
                "this session delegated to. The report follows:\n  " + report)

    def _run(self, prompt):
        payload = {"session_id": "rv-1", "hook_event_name": "UserPromptSubmit",
                   "cwd": "/tmp", "prompt": prompt}
        return run_hook("review_handback_advisory.sh", "/tmp", payload)

    def test_it_speaks_when_a_report_reads_as_a_review(self):
        for report in ("Verdict: not safe to run.\n1. Blocker: undo can run twice.",
                       "## Findings\n1. x\nSmallest fix: add a check.",
                       "Two must-fix items, most serious first."):
            note = context(self._run(self._handback(report)))
            self.assertIn("three kinds of finding", note, report)
            self.assertIn("A quality finding", note)

    def test_it_is_silent_for_a_report_that_is_not_a_review(self):
        report = "Researched three libraries. Recommendation: use the second one."
        self.assertEqual(context(self._run(self._handback(report))), "")

    def test_it_never_reads_a_human_message(self):
        """Review words typed by a person are not a reviewer's report."""
        self.assertEqual(context(self._run("Verdict: smallest fix is fine, safe to merge")), "")

    def test_it_never_fails_on_a_bad_payload(self):
        result = run_hook("review_handback_advisory.sh", "/tmp", {"prompt": None})
        self.assertEqual(result.returncode, 0)
        self.assertEqual(context(result), "")


class CodeCommentAdvisoryTests(unittest.TestCase):
    """An edit adds comment or doc text to a source file. The note always says
    what keeps a comment in step with its code, and adds a sentence for each flagged kind found
    in the added text or already in the file."""

    LEAD_PLAIN = "Scry: this edit adds comment text."
    CLOSE = "What lasts is what the code cannot show"
    HISTORY = "git and the pull request already hold who and when"
    OTHER = "tells the next reader exactly where to look"

    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.state = Path(td.name) / "state"
        self.work = Path(td.name) / "work"
        self.state.mkdir()
        self.work.mkdir()

    def _run(self, tool, tool_input, session="cc-1"):
        payload = {"session_id": session, "hook_event_name": "PostToolUse",
                   "tool_name": tool, "tool_input": tool_input}
        return run_hook("code_comment_advisory.sh", "/",
                        payload, env={"TMPDIR": str(self.state)})

    def _edit(self, path, new, old="x = 1\n", session="cc-1"):
        return context(self._run("Edit", {"file_path": path, "old_string": old,
                                          "new_string": new}, session))

    def _write(self, path, content):
        return context(self._run("Write", {"file_path": path, "content": content}))

    def assertHistory(self, note):
        self.assertIn("carries a name, a date or the story of a decision", note)
        self.assertIn(self.HISTORY, note)
        self.assertIn(self.CLOSE, note)

    # History in the added text

    def test_a_name_and_date_tag_in_a_shell_comment(self):
        note = self._edit("/work/ops/run.sh", '# (Matt, 2026-10-07: "pull the calls")\nx = 1\n')
        self.assertTrue(note.startswith("Scry: comment text this edit added carries"), note)
        self.assertHistory(note)

    def test_a_secondhand_name_in_a_moduledoc(self):
        content = ('defmodule App.Models do\n  @moduledoc """\n'
                   "  Calls go to one model (Matt via the orchestrator, 2026-10-08).\n"
                   '  """\nend\n')
        self.assertHistory(self._write("/work/app/lib/models.ex", content))

    def test_a_dated_docstring_line(self):
        content = ('def f():\n    """\n    WHY THIS EXISTS (2026-10-09). Two cases need it.\n'
                   '    """\n    return 1\n')
        self.assertHistory(self._write("/work/app/check.py", content))

    def test_an_incident_story_without_a_date(self):
        self.assertHistory(self._edit(
            "/work/app/lib/tenant.ex",
            "# Born from the tenant-16 incident, where two jobs ran at once.\nx = 1\n"))

    def test_a_multiedit_whose_second_edit_adds_a_per_name(self):
        edits = [{"old_string": "a = 1", "new_string": "a = 2"},
                 {"old_string": "b = 1", "new_string": "# per Matt, keep acronyms\nb = 1"}]
        self.assertHistory(context(self._run("MultiEdit", {"file_path": "/work/app/names.py",
                                                           "edits": edits})))

    def test_a_test_file_is_not_excluded(self):
        self.assertHistory(self._edit(
            "/work/app/test/foo_test.exs",
            "# Matt, 2026-10-09: an acronym stays upper case.\nx = 1\n"))

    # Any comment text

    def test_a_plain_why_comment_gets_the_plain_lead_and_the_close(self):
        note = self._edit("/work/app/lib/poll.ex",
                          "# Retries stop at three because the upstream lock expires.\nx = 1\n")
        self.assertTrue(note.startswith(self.LEAD_PLAIN), note)
        self.assertIn("relies on a comment as written", note)
        self.assertIn(self.CLOSE, note)
        self.assertNotIn(self.HISTORY, note)
        self.assertNotIn(self.OTHER, note)

    def test_a_doctest_date_is_not_history(self):
        content = ('defmodule App.Log do\n  @doc """\n'
                   "      iex> log_name(:cleanup, ~U[2026-10-12 21:05:09Z])\n"
                   '  """\n  def log_name(_, _), do: :ok\nend\n')
        self.assertTrue(self._write("/work/app/lib/log.ex", content).startswith(self.LEAD_PLAIN))

    def test_a_date_inside_a_path_is_not_history(self):
        note = self._edit("/work/app/lib/checks.ex",
                          "# Plan lives at docs/plans/2026-08-17-disbursement-check-tracking.md\nx = 1\n")
        self.assertTrue(note.startswith(self.LEAD_PLAIN), note)

    def test_per_and_an_acronym_is_not_history(self):
        note = self._edit("/work/ops/run.sh", "# Per AGENTS.md this exits zero\nx = 1\n")
        self.assertNotIn(self.HISTORY, note)

    def test_a_normative_word_is_not_a_kind(self):
        note = self._edit("/work/app/lib/lock.ex",
                          "# The lock must never be held across a call out.\nx = 1\n")
        self.assertTrue(note.startswith(self.LEAD_PLAIN), note)

    # Another module or file

    def test_a_named_function_elsewhere_adds_the_other_file_sentence(self):
        note = self._edit("/work/app/lib/invoice.ex", "# see Billing.charge/2\nx = 1\n")
        self.assertTrue(note.startswith(
            "Scry: comment text this edit added describes another module or file."), note)
        self.assertIn(self.OTHER, note)
        self.assertIn(self.CLOSE, note)

    def test_another_file_name_counts(self):
        self.assertIn(self.OTHER, self._edit("/work/app/lib/reader.ex",
                                             "# The order matches writer.ex.\nx = 1\n"))

    def test_the_files_own_module_is_not_another(self):
        content = ("defmodule App.Billing.Invoice do\n"
                   "  # App.Billing.Invoice.total/1 sums the lines; App.Billing groups them.\n"
                   "  def total(lines), do: Enum.sum(lines)\nend\n")
        note = self._write("/work/app/lib/invoice.ex", content)
        self.assertTrue(note.startswith(self.LEAD_PLAIN), note)

    def test_a_library_function_is_not_another_module(self):
        note = self._edit("/work/app/lib/sum.ex", "# Enum.reduce/3 keeps the order.\nx = 1\n")
        self.assertTrue(note.startswith(self.LEAD_PLAIN), note)

    # Already in the file

    def test_existing_dated_tags_with_an_added_plain_comment(self):
        path = self.work / "run.py"
        path.write_text("# (Matt, 2026-10-07) pull the calls\n"
                        "a = 1\n\n"
                        "# Born from the outage of the week before.\n"
                        "# Second line of the same block.\n"
                        "b = 2\n"
                        "# A plain reason added now.\nc = 3\n")
        note = self._edit(str(path), "# A plain reason added now.\nc = 3\n", old="c = 3\n")
        self.assertTrue(note.startswith(
            "Scry: comment text already in this file (2 comments) carries a name, "
            "a date or the story of a decision."), note)
        self.assertIn(self.HISTORY, note)

    def test_added_and_existing_both_flagged(self):
        path = self.work / "both.ex"
        path.write_text("defmodule App.Both do\n  # (Matt, 2026-10-07) old tag\n"
                        "  # see Billing.charge/2\n  def f, do: 1\nend\n")
        note = self._edit(str(path), "  # see Billing.charge/2\n  def f, do: 1\n",
                          old="  def f, do: 1\n")
        self.assertTrue(note.startswith(
            "Scry: comment text this edit added, and already in this file (1 comment), "
            "describes another module or file and carries a name"), note)
        self.assertIn(self.OTHER, note)
        self.assertIn(self.HISTORY, note)

    # Silent

    def test_silent_for_a_code_only_edit_even_with_tags_in_the_file(self):
        path = self.work / "tagged.sh"
        path.write_text("# (Matt, 2026-10-07) pull the calls\nx = 2\n")
        self.assertEqual(self._edit(str(path), "x = 2\n"), "")

    def test_silent_when_the_comment_line_was_already_there(self):
        line = "# A plain reason.\n"
        self.assertEqual(self._edit("/work/ops/run.sh", line + "x = 2\n",
                                    old=line + "x = 1\n"), "")

    def test_silent_for_a_date_in_code_with_no_comment(self):
        self.assertEqual(self._edit("/work/app/cut.py", 'CUTOFF = "2026-10-14"\n'), "")

    def test_silent_for_markdown(self):
        self.assertEqual(self._write("/work/app/NOTES.md",
                                     "# History\n(Matt, 2026-10-07) decided this.\n"), "")

    def test_silent_under_tmpdir_or_a_scratchpad(self):
        line = "# A plain reason.\nx = 1\n"
        self.assertEqual(self._edit(str(self.state / "work" / "run.sh"), line), "")
        self.assertEqual(self._edit("/work/scratchpad/probe.py", line), "")
        self.assertEqual(self._edit("/tmp/probe.py", line), "")

    def test_once_per_file_per_session_marked_only_when_said(self):
        path = "/work/ops/run.sh"
        self.assertEqual(self._edit(path, "x = 2\n"), "")
        self.assertNotEqual(self._edit(path, "# one\nx = 1\n"), "")
        self.assertEqual(self._edit(path, "# two\nx = 1\n"), "")
        self.assertNotEqual(self._edit("/work/ops/other.sh", "# two\n"), "")
        self.assertNotEqual(self._edit(path, "# two\n", session="cc-2"), "")

    def test_it_never_fails_on_a_bad_payload(self):
        for raw in ("not json", "{}", '{"tool_name": "Edit"}',
                    '{"tool_name": "Edit", "tool_input": null}', "[]"):
            result = subprocess.run(["bash", str(ROOT / "code_comment_advisory.sh")],
                                    input=raw, text=True, capture_output=True,
                                    env={**os.environ, "TMPDIR": str(self.state)})
            self.assertEqual(result.returncode, 0, raw)
            self.assertEqual(result.stdout, "", raw)

    def test_registered_after_edit_write_and_multiedit(self):
        groups = json.loads((ROOT / "hooks/hooks.json").read_text())["hooks"]["PostToolUse"]
        matchers = [g.get("matcher") for g in groups
                    if any("code_comment_advisory.sh" in h["command"] for h in g["hooks"])]
        self.assertEqual(matchers, ["Edit|Write|MultiEdit"])
        self.assertNotIn("comment_history_advisory.sh",
                         (ROOT / "hooks/hooks.json").read_text())


class GuidanceAttributionAdvisoryTests(unittest.TestCase):
    """An edit to an instruction file adds a line that opens with a name and a
    date. The note quotes the line and says how a later session weighs it."""

    INSIGHT = "reads as one person's note from one day"

    MATCH = ["Matt, 2026-10-18.",
             "— Matt, Oct 18, 2026",
             "> Claude 2026-10-18:",
             "- **Matt Mathis**, 2026-10-18",
             "Matt Mathis, October 18, 2026: the run moved",
             "* Matt 18 October 2026",
             "Matt, 2026-10-18 — moved the pins check",
             "> Claude 2026-10-18: keep it short"]
    NO_MATCH = ["2026-10-18: released 1.40",
                "See the 2026-10-18 note",
                "The Oct 18 build",
                "Matt said on 2026-10-18 that the run moved",
                "Updated 2026-10-18",
                "October 18, 2026",
                "Verified 2026-10-18 against prod",
                "Added 2026-10-18 for the pipeline",
                "Postgres 2026-10-18 migration rules"]

    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.state = Path(td.name) / "state"
        self.work = Path(td.name) / "work"
        self.state.mkdir()
        self.work.mkdir()

    def _run(self, tool, tool_input, session="ga-1", env=None):
        payload = {"session_id": session, "hook_event_name": "PostToolUse",
                   "tool_name": tool, "tool_input": tool_input}
        return context(run_hook("guidance_attribution_advisory.sh", "/", payload,
                                env={"TMPDIR": str(self.state), **(env or {})}))

    def _write(self, path, content, session="ga-1", env=None):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return self._run("Write", {"file_path": str(path), "content": content}, session, env)

    def _edit(self, path, old, new, session="ga-1"):
        path = Path(path)
        path.write_text(path.read_text().replace(old, new, 1))
        return self._run("Edit", {"file_path": str(path), "old_string": old,
                                  "new_string": new}, session)

    def _repo(self):
        repo = self.work / "repo"
        repo.mkdir()
        git = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t"]
        subprocess.run(git[:3] + ["init", "-q"], check=True)
        return repo, git

    # The pattern

    def test_each_attribution_line_speaks_and_is_quoted(self):
        for i, line in enumerate(self.MATCH):
            note = self._write(self.work / f"m{i}" / "AGENTS.md",
                               "# Guide\n\nRuns exit zero.\n" + line + "\n")
            self.assertIn(self.INSIGHT, note, line)
            self.assertIn(f'line 4 of {self.work / f"m{i}" / "AGENTS.md"} opens with', note)
            self.assertIn(f'"{line.strip()}"', note)

    def test_lines_without_a_leading_name_and_date_are_silent(self):
        for i, line in enumerate(self.NO_MATCH):
            self.assertEqual(self._write(self.work / f"n{i}" / "CLAUDE.md",
                                         "# Guide\n" + line + "\n"), "", line)

    def test_a_line_in_a_fenced_code_block_is_silent(self):
        self.assertEqual(self._write(self.work / "CLAUDE.md",
                                     "# Guide\n```\nMatt, 2026-10-18.\n```\n"), "")
        note = self._write(self.work / "f" / "CLAUDE.md",
                           "```\nx\n```\nMatt, 2026-10-18.\n")
        self.assertIn("line 4 of", note)

    # Which lines count

    def test_an_edit_that_adds_the_line_speaks(self):
        path = self.work / "CLAUDE.md"
        path.write_text("# Guide\n\nRuns exit zero.\n")
        note = self._edit(path, "Runs exit zero.\n", "Runs exit zero.\nMatt, 2026-10-18.\n")
        self.assertIn("line 4 of", note)
        self.assertIn(self.INSIGHT, note)

    def test_an_edit_beside_an_existing_dated_line_is_silent(self):
        path = self.work / "CLAUDE.md"
        path.write_text("# Guide\nMatt, 2026-10-18.\nRuns exit zero.\n")
        self.assertEqual(self._edit(path, "Matt, 2026-10-18.\nRuns exit zero.\n",
                                    "Matt, 2026-10-18.\nRuns exit one.\n"), "")

    def test_a_multiedit_whose_second_edit_adds_the_line(self):
        path = self.work / "AGENTS.md"
        path.write_text("a\nb\n")
        path.write_text("a2\nb\n— Claude, Oct 18, 2026\n")
        edits = [{"old_string": "a", "new_string": "a2"},
                 {"old_string": "b\n", "new_string": "b\n— Claude, Oct 18, 2026\n"}]
        self.assertIn("line 3 of", self._run("MultiEdit", {"file_path": str(path),
                                                           "edits": edits}))

    def test_a_write_to_a_tracked_file_that_already_had_the_line_is_silent(self):
        repo, git = self._repo()
        (repo / "AGENTS.md").write_text("# Guide\nMatt, 2026-10-18.\n")
        subprocess.run(git + ["add", "."], check=True)
        subprocess.run(git + ["commit", "-qm", "init"], check=True)
        self.assertEqual(self._write(repo / "AGENTS.md",
                                     "# Guide\nMatt, 2026-10-18.\nRuns exit zero.\n"), "")
        self.assertIn("line 4 of", self._write(
            repo / "AGENTS.md", "# Guide\nMatt, 2026-10-18.\nRuns exit zero.\n"
            "Claude, 2026-10-19.\n", session="ga-2"))

    def test_a_write_of_a_new_file_in_a_repo_speaks(self):
        repo, _ = self._repo()
        self.assertIn(self.INSIGHT, self._write(repo / "CLAUDE.md", "Matt, 2026-10-18.\n"))

    def test_a_file_outside_a_repo_speaks(self):
        self.assertIn(self.INSIGHT, self._write(self.work / "home" / "AGENTS.md",
                                                "# Mine\nMatt, 2026-10-18.\n"))

    # Which files count

    def test_claude_local_and_markdown_under_dot_claude_count(self):
        self.assertIn(self.INSIGHT, self._write(self.work / "CLAUDE.local.md",
                                                "Matt, 2026-10-18.\n"))
        self.assertIn(self.INSIGHT, self._write(self.work / ".claude" / "agents" / "rev.md",
                                                "Matt, 2026-10-18.\n"))

    def test_other_markdown_is_silent(self):
        for name in ("README.md", "notes/AGENTS.md.bak", "docs/plan.md"):
            self.assertEqual(self._write(self.work / name, "Matt, 2026-10-18.\n"), "", name)
        self.assertEqual(self._write(self.work / ".claude" / "settings.json",
                                     '"Matt, 2026-10-18."\n'), "")

    # Once, and off

    def test_once_per_file_per_session_marked_only_when_said(self):
        path = self.work / "AGENTS.md"
        self.assertEqual(self._write(path, "Runs exit zero.\n"), "")
        self.assertIn(self.INSIGHT, self._write(path, "Matt, 2026-10-18.\n"))
        self.assertEqual(self._write(path, "Claude, 2026-10-19.\n"), "")
        self.assertIn(self.INSIGHT, self._write(self.work / "x" / "AGENTS.md",
                                                "Claude, 2026-10-19.\n"))
        self.assertIn(self.INSIGHT, self._write(path, "Claude, 2026-10-19.\n", session="ga-3"))

    def test_the_off_switch(self):
        self.assertEqual(self._write(self.work / "AGENTS.md", "Matt, 2026-10-18.\n",
                                     env={"SCRY_GUIDANCE_ATTRIBUTION": "0"}), "")

    def test_it_never_fails_on_a_bad_payload(self):
        for raw in ("not json", "{}", '{"tool_name": "Edit", "x": "AGENTS.md"}',
                    '{"tool_name": "Edit", "tool_input": null, "x": "CLAUDE.md"}', "[]"):
            result = subprocess.run(["bash", str(ROOT / "guidance_attribution_advisory.sh")],
                                    input=raw, text=True, capture_output=True,
                                    env={**os.environ, "TMPDIR": str(self.state)})
            self.assertEqual(result.returncode, 0, raw)
            self.assertEqual(result.stdout, "", raw)

    def test_registered_after_edit_write_and_multiedit(self):
        groups = json.loads((ROOT / "hooks/hooks.json").read_text())["hooks"]["PostToolUse"]
        matchers = [g.get("matcher") for g in groups
                    if any("guidance_attribution_advisory.sh" in h["command"]
                           for h in g["hooks"])]
        self.assertEqual(matchers, ["Edit|Write|MultiEdit"])


class ShellWritesTests(unittest.TestCase):
    """A Bash command changes files. The before and after hooks run around a
    real command in a temp git repo, and the write hooks' notes for the files
    that changed come back through the after hook."""

    LEAD = "changed while that command ran"
    NEW_MD = "NEW MARKDOWN"
    COMMENT = "Scry: comment text"
    PLAIN_COMMENT = "Scry: this edit adds comment text."
    ATTRIBUTION = "reads as one person's note from one day"

    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        base = Path(td.name)
        self.state, self.home, self.repo = base / "state", base / "home", base / "repo"
        for p in (self.state, self.home, self.repo):
            p.mkdir()
        self.env = {"TMPDIR": str(self.state), "HOME": str(self.home)}
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Scry Test")
        self.git("config", "user.email", "scry@example.test")
        (self.repo / ".gitignore").write_text("*.log\nbuild/\n")
        (self.repo / "a.py").write_text("# Matt decided this on 2026-01-02\nx = 1\n")
        (self.repo / "b.py").write_text("y = 2\n")
        self.git("add", ".")
        self.git("commit", "-qm", "Initial")
        self.uses = 0

    def git(self, *args, env=None):
        return subprocess.run(["git", "-C", str(self.repo), *args], check=True,
                              capture_output=True, text=True,
                              env={**os.environ, **self.env, **(env or {})}).stdout.strip()

    def payload(self, event, use, cwd):
        return {"session_id": "sw-1", "tool_use_id": use, "cwd": str(cwd),
                "hook_event_name": event, "tool_name": "Bash",
                "tool_input": {"command": "(not read)"}}

    def bash(self, command, cwd=None, env=None):
        """Before hook, the command, after hook; returns (event, context)."""
        cwd = cwd or self.repo
        env = {**self.env, **(env or {})}
        self.uses += 1
        use = f"toolu_{self.uses}"
        self.assertEqual(run_hook("shell_writes.sh", cwd, self.payload("PreToolUse", use, cwd),
                                  env).stdout, "")
        code = subprocess.run(["bash", "-c", command], cwd=cwd, capture_output=True,
                              env={**os.environ, **env}).returncode
        event = "PostToolUse" if code == 0 else "PostToolUseFailure"
        out = run_hook("shell_writes.sh", cwd, self.payload(event, use, cwd), env).stdout
        self.assertEqual(list((self.state / "scry-shell-writes").glob("*.json")), [])
        if not out.strip():
            return event, ""
        spec = json.loads(out)["hookSpecificOutput"]
        self.assertEqual(spec["hookEventName"], event)
        return event, spec["additionalContext"]

    def note(self, command, **kw):
        return self.bash(command, **kw)[1]

    def named(self, ctx):
        lead = ctx.split("\n", 1)[0]
        self.assertIn(self.LEAD, lead)
        return lead.split("ran: ", 1)[1].split(". ")[0].split(", ")

    def test_a_heredoc_into_a_new_file_reads_as_a_write(self):
        ctx = self.note("cat > notes.md <<'EOF'\n# Notes\n\nkept for later\nEOF")
        self.assertEqual(self.named(ctx), ["notes.md"])
        self.assertIn(self.NEW_MD, ctx)
        self.assertTrue(ctx.startswith("Scry: 1 file changed while that command ran"))

    def test_sed_on_a_tracked_file_reads_as_an_edit_of_the_added_lines(self):
        ctx = self.note("sed -i.bak 's/^x = 1$/x = 1  # counts the runs/' a.py && rm a.py.bak")
        self.assertEqual(self.named(ctx), ["a.py"])
        # The dated comment was already in the file; only the plain one was added.
        self.assertIn(self.COMMENT + " already in this file (1 comment)", ctx)
        self.assertNotIn("this edit added", ctx)
        self.assertNotIn(self.NEW_MD, ctx)

    def test_cp_and_mv(self):
        (self.repo / "a.md").write_text("# A\n")
        self.git("add", "a.md")
        self.git("commit", "-qm", "md")
        ctx = self.note("cp a.md copy.md && mv a.md moved.md")
        self.assertEqual(self.named(ctx), ["copy.md", "moved.md"])
        self.assertEqual(ctx.count(self.NEW_MD), 2)

    def test_git_show_into_a_new_file(self):
        ctx = self.note("git show HEAD:a.py > restored.py")
        self.assertEqual(self.named(ctx), ["restored.py"])
        self.assertIn(self.COMMENT + " this edit added carries a name", ctx)

    def test_a_script_writes_a_file_the_command_does_not_name(self):
        (self.repo / "gen.py").write_text(
            "open('out.py', 'w').write('# the report total\\nz = 3\\n')\n")
        self.git("add", "gen.py")
        self.git("commit", "-qm", "gen")
        ctx = self.note("python3 gen.py")
        self.assertEqual(self.named(ctx), ["out.py"])
        self.assertIn(self.PLAIN_COMMENT, ctx)

    def test_a_second_edit_to_an_already_dirty_file(self):
        (self.repo / "b.py").write_text("y = 2\nw = 4\n")
        (self.repo / "dirty.md").write_text("# untouched by the command\n")
        ctx = self.note("printf '# the second edit\\n' >> b.py")
        self.assertEqual(self.named(ctx), ["b.py"])
        self.assertIn(self.PLAIN_COMMENT, ctx)

    def test_a_commit_made_by_the_command(self):
        ctx = self.note("printf '# made in a commit\\nq = 1\\n' > c.py && git add c.py "
                        "&& git commit -qm c")
        self.assertEqual(self.named(ctx), ["c.py"])
        self.assertIn(self.PLAIN_COMMENT, ctx)

    def test_a_commit_of_files_edited_before_the_command_is_silent(self):
        (self.repo / "b.py").write_text("y = 2  # edited with the file tool\n")
        self.assertEqual(self.note("git commit -qam x"), "")

    def test_a_fast_forward_to_older_work_is_not_counted(self):
        old = {"GIT_COMMITTER_DATE": "2020-01-01T00:00:00", "GIT_AUTHOR_DATE": "2020-01-01T00:00:00"}
        self.git("checkout", "-q", "-b", "older")
        (self.repo / "older.md").write_text("# from older work\n")
        self.git("add", "older.md")
        self.git("commit", "-qm", "older", env=old)
        self.git("checkout", "-q", "main")
        self.assertEqual(self.note("git merge -q --ff-only older"), "")

    def test_a_guidance_file_outside_the_repo(self):
        (self.home / ".claude").mkdir()
        guide = self.home / ".claude" / "CLAUDE.md"
        guide.write_text("Sam, 2026-10-01.\n")
        # The line already there is in the saved copy, so adding a plain line is silent.
        self.assertEqual(self.note("printf 'Keep replies short.\\n' >> \"$HOME/.claude/CLAUDE.md\"",
                                   cwd=self.home), "")
        ctx = self.note("printf 'Matt, 2026-10-18.\\n' >> \"$HOME/.claude/CLAUDE.md\"",
                        cwd=self.home)
        self.assertEqual(self.named(ctx), ["~/.claude/CLAUDE.md"])
        self.assertIn("line 3 of ~/.claude/CLAUDE.md", ctx)
        self.assertIn(self.ATTRIBUTION, ctx)

    def test_a_heredoc_into_home_agents_md_brings_the_attribution_note(self):
        ctx = self.note("cat > \"$HOME/AGENTS.md\" <<'EOF'\n# Agents\n\nMatt, 2026-10-18.\nEOF")
        self.assertEqual(self.named(ctx), ["~/AGENTS.md"])
        self.assertIn('opens with a name and a date: "Matt, 2026-10-18."', ctx)
        self.assertIn(self.ATTRIBUTION, ctx)

    def test_a_read_only_command_is_silent(self):
        self.assertEqual(self.note("cat a.py && git status && ls -la"), "")

    def test_a_failing_command_that_wrote_a_file(self):
        event, ctx = self.bash("printf '# Draft\\n' > draft.md; exit 3")
        self.assertEqual(event, "PostToolUseFailure")
        self.assertEqual(self.named(ctx), ["draft.md"])
        self.assertIn(self.NEW_MD, ctx)

    def test_more_than_ten_files(self):
        ctx = self.note("for i in $(seq -w 1 12); do printf '# n\\n' > n$i.md; done")
        self.assertIn("12 files changed while that command ran", ctx)
        self.assertEqual(len(self.named(ctx)), 11)  # ten names, then the count
        self.assertIn("and 2 more not read", ctx)
        self.assertEqual(ctx.count(self.NEW_MD), 10)

    def test_a_file_over_256_kb_goes_without_its_text(self):
        filler = "v = 0\\n" * 50000
        ctx = self.note(f"printf '# big\\n{filler}' > big.py && printf '# Big\\n{filler}' > big.md")
        self.assertEqual(self.named(ctx), ["big.md", "big.py"])
        self.assertIn(self.NEW_MD, ctx)  # read from disk
        self.assertNotIn(self.COMMENT, ctx)  # needs the text
        self.assertNotIn(self.PLAIN_COMMENT, ctx)

    def test_no_snapshot_is_silent(self):
        out = run_hook("shell_writes.sh", self.repo,
                       self.payload("PostToolUse", "toolu_none", self.repo), self.env)
        self.assertEqual(out.stdout, "")

    def test_the_off_switch(self):
        self.assertEqual(self.note("printf '# Off\\n' > off.md",
                                   env={"SCRY_SHELL_WRITES": "0"}), "")
        self.assertFalse((self.state / "scry-shell-writes").exists())

    def test_an_ignored_file_is_silent(self):
        self.assertEqual(self.note("mkdir -p build && printf '# B\\n' > build/out.md "
                                   "&& printf 'x\\n' > run.log"), "")

    def test_old_snapshots_are_swept(self):
        folder = self.state / "scry-shell-writes"
        folder.mkdir()
        stale = folder / "stale.json"
        stale.write_text("{}")
        os.utime(stale, (time.time() - 2 * 86400,) * 2)
        self.note("true")
        self.assertFalse(stale.exists())

    def test_it_never_fails_on_a_bad_payload(self):
        for raw in ("not json", "{}", "[]", '{"tool_name": "Bash"}',
                    '{"tool_name": "Bash", "hook_event_name": "PostToolUse", "session_id": "s"}'):
            result = subprocess.run(["bash", str(ROOT / "shell_writes.sh")], input=raw,
                                    text=True, capture_output=True,
                                    env={**os.environ, **self.env})
            self.assertEqual(result.returncode, 0, raw)
            self.assertEqual(result.stdout, "", raw)

    def test_registered_before_and_after_bash(self):
        hooks = json.loads((ROOT / "hooks/hooks.json").read_text())["hooks"]
        for event in ("PreToolUse", "PostToolUse", "PostToolUseFailure"):
            matchers = [g.get("matcher") for g in hooks[event]
                        if any("shell_writes.sh" in h["command"] for h in g["hooks"])]
            self.assertEqual(matchers, ["Bash"], event)


class CacheHandoffTests(unittest.TestCase):
    """One summary request per user-work cycle, shortly before the prompt
    cache goes cold, and only a user message re-arms it (designed with Codex,
    2026-09-10). The summary is written in the conversation, never to a file,
    and while work is unfinished a keep-alive reports its roster instead
    (Matt, 2026-09-13). Three scripts share small files of numbers per session.
    """

    SID = "sess-cache-0001"
    PID = "424242"

    def _env(self, td, **extra):
        # Every Claude Code location the monitor reads is pointed into the
        # temp dir: these tests run inside real sessions, whose own
        # CLAUDE_PID and ~/.claude would otherwise answer "which session".
        env = {
            "SCRY_CACHE_STATE_DIR": str(Path(td) / "state"),
            "CLAUDE_CODE_SESSION_ID": self.SID,
            "CLAUDE_PID": self.PID,
            "SCRY_CLAUDE_SESSIONS_DIR": str(Path(td) / "sessions"),
            "SCRY_CLAUDE_PROJECTS_DIR": str(Path(td) / "projects"),
            "SCRY_TASK_STATE_DIR": str(Path(td) / "tasks"),
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

    def _arm(self, td, sid=None, prompt="SECRET PROMPT TEXT", event="UserPromptSubmit"):
        return run_hook("cache_handoff_arm.sh", td,
                        {"session_id": sid or self.SID, "prompt": prompt,
                         "hook_event_name": event},
                        env=self._env(td))

    def _monitor(self, td, **extra):
        return subprocess.run(
            ["bash", str(ROOT / "cache_handoff_monitor.sh")], cwd=td, text=True,
            capture_output=True, check=True,
            env={**os.environ, **self._env(td, **extra)},
        )

    def _state(self, td, sid=None):
        p = Path(td) / "state" / f"{sid or self.SID}.state"
        return p.read_text().split() if p.exists() else []

    def _armed_near_expiry(self, td, sid=None):
        """A user message, then the status line reporting a warm cache that
        goes cold in 90 s — inside the default two-minute lead."""
        self._arm(td, sid=sid)
        time.sleep(1.1)  # the deadline must be observed after arming
        self._statusline(td, 90, sid=sid)

    def _session_dir(self, td, sid=None):
        d = Path(td) / "projects" / "-Users-someone-repo" / (sid or self.SID)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _subagent(self, td, age=0, description="build:opus"):
        sub = self._session_dir(td) / "subagents"
        sub.mkdir(parents=True, exist_ok=True)
        j = sub / "agent-a1b2c3.jsonl"
        j.write_text('{"type":"assistant","message":"SECRET AGENT TEXT"}\n')
        (sub / "agent-a1b2c3.meta.json").write_text(json.dumps(
            {"agentType": "general-purpose", "description": description}))
        t = time.time() - age
        os.utime(j, (t, t))
        return j

    def _shell_task(self, td, text, age=0, sid=None, held=False):
        d = Path(td) / "tasks" / "-Users-someone-repo" / (sid or self.SID) / "tasks"
        d.mkdir(parents=True, exist_ok=True)
        out = d / "bq7shell1.output"
        out.write_text(text)
        if held:
            hold_open(self, out)
        t = time.time() - age
        os.utime(out, (t, t))
        return out

    def _workflow(self, td, wid="wf_abc-123", age=0, name=None, finished_all=False):
        sdir = self._session_dir(td)
        wf = sdir / "subagents" / "workflows" / wid
        wf.mkdir(parents=True)
        journal = ('{"type":"launched"}\n'
                   '{"type":"started","key":"v2:k1","agentId":"a1","label":"spike:facts","phase":"Spike"}\n'
                   '{"type":"result","key":"v2:k1","agentId":"a1","result":"SECRET RESULT"}\n'
                   '{"type":"started","key":"v2:k2","agentId":"a2","label":"build:opus","phase":"Build"}\n')
        if finished_all:
            journal += '{"type":"result","key":"v2:k2","agentId":"a2","result":"r"}\n'
        (wf / "journal.jsonl").write_text(journal)
        (wf / "agent-a2.jsonl").write_text('{"message":"SECRET AGENT TEXT"}\n')
        if name:
            scripts = sdir / "workflows" / "scripts"
            scripts.mkdir(parents=True, exist_ok=True)
            (scripts / f"{name}-{wid}.js").write_text("export const meta = {}\n")
        t = time.time() - age
        for f in wf.iterdir():
            os.utime(f, (t, t))
        return wf

    def test_the_status_line_records_the_deadline_and_nothing_else(self):
        with tempfile.TemporaryDirectory() as td:
            self._statusline(td, 3000)
            rec = json.loads((Path(td) / "state" / f"{self.SID}.deadline").read_text())
            self.assertEqual(set(rec), {"session_id", "observed_at", "warm",
                                        "ttl", "expires_at", "requests", "tab"})
            self.assertTrue(rec["warm"])
            self.assertEqual(rec["tab"], "none")
            self.assertNotIn("model", json.dumps(rec))

    def test_the_tab_is_uncoloured_while_warm_yellow_near_expiry_and_red_once_cold(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "state" / f"{self.SID}.deadline"
            for expires_in, warm, colour in ((3000, True, "none"), (300, True, "yellow"),
                                             (-60, False, "red"), (3000, True, "none")):
                self._statusline(td, expires_in, warm=warm)
                self.assertEqual(json.loads(path.read_text())["tab"], colour)

    def test_the_tab_colour_script_is_silent_outside_iterm(self):
        with tempfile.TemporaryDirectory() as td:
            r = run_hook("tab_colour.sh", td, {}, env={"LC_TERMINAL": ""})
            self.assertEqual((r.returncode, r.stdout), (0, ""))

    def test_the_status_line_passes_the_payload_to_the_inner_command_and_appends_the_cache(self):
        with tempfile.TemporaryDirectory() as td:
            r = self._statusline(td, 3000, inner="python3 -c 'import json,sys; print(\"inner:\"+json.load(sys.stdin)[\"model\"][\"id\"])'")
            # The bar the user already had, then the cache at the end of it
            # (Matt, 2026-09-10: the TTL "at the bottom" is the point).
            self.assertRegex(r.stdout.strip(), r"^inner:claude-fable-5-1  \u267b\ufe0f (49|50)m$")
            # An inner that prints nothing leaves the cache segment alone.
            r = self._statusline(td, 3000, inner="true")
            self.assertRegex(r.stdout.strip(), r"^\u267b\ufe0f (49|50)m$")

    def test_the_status_line_is_a_cache_status_when_no_inner_command_is_set(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertRegex(self._statusline(td, 3000).stdout, r"\u267b\ufe0f (49|50)m")
            self.assertRegex(self._statusline(td, 3000, warm=False).stdout.strip(), r"^\U0001f5d1\ufe0f \x1b\[31mcold\x1b\[0m$")

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
            self.assertRegex(run(3000, True), r"^\u267b\ufe0f (49|50)m$")
            self.assertRegex(run(250, True), r"^\u267b\ufe0f \x1b\[33m4m ~153k\x1b\[0m$")
            self.assertEqual(run(-5, False), "\U0001f5d1\ufe0f \x1b[31m~153k\x1b[0m")
            # The record the monitor reads is unchanged by any of this.
            rec = json.loads((Path(td) / "state" / f"{self.SID}.deadline").read_text())
            self.assertEqual(set(rec), {"session_id", "observed_at", "warm",
                                        "ttl", "expires_at", "requests", "tab"})

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
            for p in (Path(td) / "state").rglob("*"):
                if p.is_file():
                    self.assertNotIn("SECRET", p.read_text())

    def test_it_asks_once_inside_the_lead_window_and_never_again(self):
        with tempfile.TemporaryDirectory() as td:
            self._arm(td)
            time.sleep(1.1)  # the deadline must be observed after arming
            self._statusline(td, 90)
            first = self._monitor(td)
            self.assertIn("goes cold", first.stdout)
            self.assertEqual(first.stdout.count("\n"), 1)
            self.assertEqual(self._state(td)[0], "requested")
            self.assertEqual(len(self._state(td)), 2)  # no path, nothing else
            # The summary itself refreshes the cache; that must not re-arm.
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

    def test_the_summary_is_asked_for_in_the_conversation_with_the_full_session_id(self):
        # Matt, 2026-09-13: "Why does it need a file, why can't it just write
        # it inline" and "Put the session ID there in case I need to start a
        # new window". And Scry names no other system.
        with tempfile.TemporaryDirectory() as td:
            self._armed_near_expiry(td)
            said = self._monitor(td, HOME=td).stdout
            self.assertIn("as your reply here in the conversation — not to any file", said)
            self.assertIn(f'"Session {self.SID}"', said)
            self.assertIn(f"To continue in a new window, say: pick up from session {self.SID}", said)
            for part in ("objective and the latest user direction",
                         "decisions and open questions",
                         "what was done and what was actually verified",
                         "what remains and the next concrete step",
                         "the working directory and branch"):
                self.assertIn(part, said)
            self.assertNotIn(".md", said)
            self.assertNotIn("/", said.replace("goes cold", ""))  # no path of any kind
            for other in ("agent-comms", "agent comms", "ThreadTask", "handoff"):
                self.assertNotIn(other.lower(), said.lower())
            # No handoff directory, under the default or anywhere else.
            self.assertFalse((Path(td) / ".claude" / "scry" / "handoffs").exists())
            self.assertEqual(sorted(p.name for p in Path(td).iterdir()), ["state"])

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

    # ---- which session: /clear inside a process whose monitor never restarts

    def _sessions_file(self, td, sid, when=None):
        d = Path(td) / "sessions"
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{self.PID}.json"
        p.write_text(json.dumps({"pid": int(self.PID), "sessionId": sid, "status": "idle"}))
        t = when or time.time()
        os.utime(p, (t, t))

    def test_the_monitor_follows_the_session_a_clear_starts(self):
        # 60ef841d cleared into b1746f6b (2026-09-11): the old id's warm
        # deadline must not fire into the new session, and the new session
        # must be watched once a user message arms it.
        new = "sess-cache-0002"
        with tempfile.TemporaryDirectory() as td:
            self._armed_near_expiry(td)  # the launch session, about to fire
            self._sessions_file(td, new, when=time.time() + 5)  # /clear
            self.assertEqual(self._monitor(td).stdout, "")
            self.assertEqual(self._state(td)[0], "superseded")
            self.assertEqual(self._state(td, new), [])  # not armed by the clear
            self.assertEqual(self._monitor(td).stdout, "")  # and said nothing, twice
            self._armed_near_expiry(td, sid=new)
            self._sessions_file(td, new, when=time.time() + 5)
            first = self._monitor(td)
            self.assertIn("goes cold", first.stdout)
            self.assertIn(f"pick up from session {new}", first.stdout)
            self.assertNotIn(self.SID, first.stdout)
            self.assertEqual(self._state(td, new)[0], "requested")
            self.assertEqual(self._state(td)[0], "superseded")
            # It was armed, not asked, when the clear came.
            self.assertTrue(self._state(td)[2].startswith("armed:"))
            self.assertEqual(self._monitor(td).stdout, "")
            # A clear after the request keeps the fact that it was asked, for
            # fleet.sh to tell the next session.
            asked_at = self._state(td, new)[1]
            self._sessions_file(td, "sess-cache-0009", when=time.time() + 10)
            self.assertEqual(self._monitor(td).stdout, "")
            self.assertEqual(self._state(td, new), ["superseded", self._state(td, new)[1],
                                                    f"requested:{asked_at}"])

    def test_the_hook_record_alone_is_enough_to_follow_the_session(self):
        # Claude Code's sessions file is internal; the arm hook's own
        # pid -> session record covers a release that drops or renames it.
        new = "sess-cache-0003"
        with tempfile.TemporaryDirectory() as td:
            self._armed_near_expiry(td)
            run_hook("cache_handoff_arm.sh", td,
                     {"session_id": new, "hook_event_name": "SessionStart", "source": "clear"},
                     env=self._env(td))
            self.assertEqual((Path(td) / "state" / "pid" / self.PID).read_text().split()[0], new)
            self.assertEqual(self._state(td, new), [])  # a start never arms
            self.assertEqual(self._monitor(td).stdout, "")
            self.assertEqual(self._state(td)[0], "superseded")
            self._armed_near_expiry(td, sid=new)
            self.assertIn(f"pick up from session {new}", self._monitor(td).stdout)

    def test_without_any_record_it_watches_the_environment_session(self):
        with tempfile.TemporaryDirectory() as td:
            self._arm(td)
            (Path(td) / "state" / "pid" / self.PID).unlink()
            time.sleep(1.1)
            self._statusline(td, 90)
            self.assertIn(f"pick up from session {self.SID}", self._monitor(td).stdout)

    # ---- only a user message arms

    def test_a_notification_does_not_arm_or_rearm(self):
        # b1746f6b read "armed" the second a workflow completion arrived
        # (2026-09-13), and 62aa5e9f was asked for a summary every hour
        # because each request re-armed the next.
        with tempfile.TemporaryDirectory() as td:
            for prompt in ("<task-notification>\n<task-id>wabc</task-id> SECRET",
                           "Another Claude session sent a message: SECRET",
                           # A hand-back as the hook really receives it: the
                           # transcript's "Another Claude session" preamble is
                           # not in the payload (livetest, a28191cb, 10-14).
                           '<agent-message from="aa1dc33f4f3a64814">\n'
                           "[Subagent hand-back] The text below is SECRET",
                           '  <agent-message from="a1">\nSECRET'):
                with self.subTest(prompt=prompt[:20]):
                    self.assertEqual(self._arm(td, prompt=prompt).stdout, "")
                    self.assertEqual(self._state(td), [])
            self._armed_near_expiry(td)
            self.assertIn("goes cold", self._monitor(td).stdout)
            # Scry's own request comes back through UserPromptSubmit.
            self._arm(td, prompt="<task-notification>\n<summary>Monitor event</summary>")
            self.assertEqual(self._state(td)[0], "requested")
            time.sleep(1.1)
            self._statusline(td, 90)
            self.assertEqual(self._monitor(td).stdout, "")
            for p in (Path(td) / "state").rglob("*"):
                if p.is_file():
                    self.assertNotIn("SECRET", p.read_text())

    # ---- keep-alive while this session's work is running

    def test_unfinished_work_gets_a_keep_alive_with_the_roster_not_a_summary(self):
        with tempfile.TemporaryDirectory() as td:
            self._subagent(td)
            self._armed_near_expiry(td)
            first = self._monitor(td)
            self.assertIn("keep-alive", first.stdout)
            self.assertRegex(first.stdout, r'subagent a1b2c3 "build:opus": last activity \d+s ago')
            self.assertIn("As far as Scry can tell from file metadata", first.stdout)
            self.assertIn("A one-line plain-language status for the user is enough of a reply",
                          first.stdout)
            # Scry reports; the session judges (Matt, 2026-09-13) — and
            # nothing in the line reads as "wait" (2026-10-10).
            self.assertIn("one that reads the same as in your previous status has not moved, "
                          "and it is this session's own to look at", first.stdout)
            self.assertNotIn("take no other action", first.stdout)
            self.assertNotIn("gone silent", first.stdout)
            self.assertNotIn("SECRET", first.stdout)
            self.assertNotIn("summary", first.stdout)
            self.assertEqual(first.stdout.count("\n"), 1)
            self.assertEqual(self._state(td)[0], "armed")
            # Once per deadline: nothing more while its ack lands.
            self.assertEqual(self._monitor(td).stdout, "")
            # The ack is a request; the deadline moves; the next window
            # gets the next keep-alive.
            time.sleep(1.1)
            self._statusline(td, 3500)
            self.assertEqual(self._monitor(td).stdout, "")
            self._statusline(td, 90)
            self.assertIn("Keep-alive 2 of", self._monitor(td).stdout)

    def test_no_progress_fingerprint_is_stored(self):
        # PR #27's stored-fingerprint comparison was dropped as unreliable.
        # The only per-session files are the deadline, the state, and the
        # keep-alive counter — and the counter holds counts and times only.
        with tempfile.TemporaryDirectory() as td:
            self._subagent(td)
            self._workflow(td, name="piece-2")
            self._shell_task(td, "compiling\n", held=True)
            self._armed_near_expiry(td)
            self.assertIn("keep-alive", self._monitor(td).stdout)
            state = Path(td) / "state"
            files = sorted(str(p.relative_to(state)) for p in state.rglob("*") if p.is_file())
            self.assertEqual(files, sorted([f"{self.SID}.deadline", f"{self.SID}.keepalive",
                                            f"{self.SID}.state", f"pid/{self.PID}",
                                            f"pid/{self.PID}.sessions", f"pid/{self.PID}.watch"]))
            ka = json.loads((state / f"{self.SID}.keepalive").read_text())
            self.assertEqual(set(ka), {"armed_at", "cycle", "sent_at", "sent_expires", "silent"})
            # A count of keep-alives, not a record of what any worker wrote.
            self.assertEqual(ka["silent"], 0)

    def test_a_subagent_is_listed_only_within_one_ttl_of_its_last_write(self):
        # Nothing in a plain subagent's metadata says it finished, so its
        # window is the only bound: SCRY_KEEPALIVE_FRESH_SECS, default 3600.
        with tempfile.TemporaryDirectory() as td:
            self._subagent(td, age=1300)  # quiet for 20+ minutes, inside one TTL
            self._armed_near_expiry(td)
            said = self._monitor(td).stdout
            self.assertRegex(said, r'keep-alive.*subagent a1b2c3 "build:opus": last activity 2\dm ago')
            self.assertNotIn("its own cache goes cold", said)
        with tempfile.TemporaryDirectory() as td:
            # The last quarter of the window: what the silence means rides
            # the keep-alive line (Matt, 2026-09-22) — never a wake of its own.
            self._subagent(td, age=2800)
            self._armed_near_expiry(td)
            said = self._monitor(td).stdout
            self.assertIn("keep-alive", said)
            self.assertIn('subagent a1b2c3 "build:opus": last activity 46m ago — its own cache '
                          "goes cold at 1h00m of silence: finished, nothing happens; still "
                          "working, its next step re-reads its context once at the full rate; "
                          "stuck, stop it now rather than after that read", said)
            self.assertEqual(said.count("\n"), 1)
        with tempfile.TemporaryDirectory() as td:
            self._subagent(td, age=3700)  # outside the window
            self._armed_near_expiry(td)
            out = self._monitor(td).stdout
            self.assertIn("goes cold", out)
            self.assertNotIn("keep-alive", out)
            self.assertNotIn("a1b2c3", out)
            self.assertNotIn("has not finished", out)
            self.assertEqual(self._state(td)[0], "requested")

    def test_a_background_command_is_listed_while_a_process_holds_its_file_however_quiet(self):
        with tempfile.TemporaryDirectory() as td:
            self._shell_task(td, "SECRET OUTPUT\n[exited with code 0]\n")
            self._armed_near_expiry(td)
            self.assertNotIn("keep-alive", self._monitor(td).stdout)
        with tempfile.TemporaryDirectory() as td:
            # 2026-09-22: a foreground call's file — no marker, no holder —
            # is finished work. Fifty of these kept one session warm hourly.
            self._shell_task(td, "diff --git a/x b/x\nSECRET\n", age=7200)
            self._armed_near_expiry(td)
            said = self._monitor(td).stdout
            self.assertNotIn("keep-alive", said)
            self.assertNotIn("bq7shell1", said)
        with tempfile.TemporaryDirectory() as td:
            out = self._shell_task(td, "SECRET OUTPUT still going\n", age=7200, held=True)
            self._armed_near_expiry(td)
            said = self._monitor(td).stdout
            self.assertIn("keep-alive", said)
            self.assertRegex(said, r"background command bq7shell1: a process still holds its "
                                   r"output open, last output 2h0\dm ago")
            self.assertNotIn(str(out), said)
            self.assertNotIn("SECRET", said)

    def test_a_workflow_is_listed_while_an_agent_has_no_result_however_quiet(self):
        with tempfile.TemporaryDirectory() as td:
            self._workflow(td, name="piece-2-build-inspect")
            self._armed_near_expiry(td)
            said = self._monitor(td).stdout
            self.assertRegex(said, r"workflow piece-2-build-inspect \(wf_abc-123\): 1 of 2 agents "
                                   r"done, last activity \d+s ago")
            self.assertNotIn("SECRET", said)
        with tempfile.TemporaryDirectory() as td:
            # Three hours without a write, no script file: still unfinished
            # by its own journal, so still listed, by id, with its age.
            self._workflow(td, age=3 * 3600)
            self._armed_near_expiry(td)
            said = self._monitor(td).stdout
            self.assertIn("keep-alive", said)
            self.assertRegex(said, r"workflow wf_abc-123: 1 of 2 agents done, "
                                   r"last activity 3h0\dm ago")
        with tempfile.TemporaryDirectory() as td:
            self._workflow(td, wid="wf_done", finished_all=True)
            self._armed_near_expiry(td)
            said = self._monitor(td).stdout
            self.assertNotIn("keep-alive", said)
            self.assertNotIn("wf_done", said)

    def test_work_silent_for_a_whole_ttl_is_said_as_the_sessions_own_to_look_at(self):
        # 2026-10-09: three background commands hung on stdin; the old line
        # said "still running … when that work finishes, its notification
        # wakes this session" and asked for a status, so a session answered
        # "still waiting" hourly for a night and never looked.
        with tempfile.TemporaryDirectory() as td:
            self._shell_task(td, "SECRET waiting\n", age=12 * 3600 + 60, held=True)
            self._armed_near_expiry(td)
            said = self._monitor(td).stdout
            self.assertIn("keep-alive", said)
            self.assertIn("the only thing keeping it warm is work this session started "
                          "that has gone silent", said)
            self.assertRegex(said, r"Nothing listed has written anything for 12h0\dm — longer "
                                   r"than a whole cache lifetime \(1h00m\)")
            self.assertIn("it cannot see whether one is working quietly or will never finish", said)
            self.assertIn("work that never finishes never sends it, so waiting does not "
                          "resolve this", said)
            self.assertIn("These are this session's own tasks: nobody else is watching them, "
                          "and looking at them needs nothing from the user", said)
            self.assertIn("this is 1 of at most 3 for work this silent", said)
            # Nothing that reads as "wait", nothing asking for a status,
            # and no claim about what the command is doing.
            for gone in ("still running", "status line", "wakes this session", "in a row",
                         "hung", "stuck", "SECRET"):
                self.assertNotIn(gone, said)
            self.assertEqual(said.count("\n"), 1)
        with tempfile.TemporaryDirectory() as td:
            # One worker still writing: the silent one is counted beside it,
            # and the line stays the ordinary keep-alive.
            self._workflow(td, age=3 * 3600)
            self._subagent(td)
            self._armed_near_expiry(td)
            said = self._monitor(td).stdout
            self.assertIn("1 of these wrote nothing for over 1h00m, a whole cache lifetime", said)
            self.assertNotIn("gone silent", said)

    def test_silent_work_is_counted_across_keep_alives_and_then_no_longer_kept_warm(self):
        with tempfile.TemporaryDirectory() as td:
            self._workflow(td, age=3 * 3600)
            self._armed_near_expiry(td)
            first = self._monitor(td, SCRY_KEEPALIVE_SILENT_MAX="2").stdout
            self.assertIn("this is 1 of at most 2 for work this silent", first)
            time.sleep(1.1)
            self._statusline(td, 90)
            second = self._monitor(td, SCRY_KEEPALIVE_SILENT_MAX="2").stdout
            self.assertIn("This is keep-alive 2 in a row to find exactly that: nothing "
                          "changed in between.", second)
            self.assertIn("the next one like this is the summary request instead", second)
            self.assertEqual(self._state(td)[0], "armed")
            time.sleep(1.1)
            self._statusline(td, 90)
            third = self._monitor(td, SCRY_KEEPALIVE_SILENT_MAX="2").stdout
            self.assertNotIn("keep-alive (", third)
            self.assertRegex(third, r"will not be kept warm for \(none of it has written for "
                                    r"3h0\dm, through 2 keep-alives in a row\)")
            self.assertIn("workflow wf_abc-123: 1 of 2 agents done", third)
            self.assertIn("whether they are still worth running is something the user would "
                          "act on now", third)
            self.assertEqual(self._state(td)[0], "requested")
            self.assertEqual(self._monitor(td, SCRY_KEEPALIVE_SILENT_MAX="2").stdout, "")
        with tempfile.TemporaryDirectory() as td:
            # A write in between ends the run: the count starts again.
            wf = self._workflow(td, age=3 * 3600)
            self._armed_near_expiry(td)
            self.assertIn("this is 1 of at most 3", self._monitor(td).stdout)
            os.utime(wf / "journal.jsonl")
            time.sleep(1.1)
            self._statusline(td, 90)
            self.assertNotIn("gone silent", self._monitor(td).stdout)
            ka = json.loads((Path(td) / "state" / f"{self.SID}.keepalive").read_text())
            self.assertEqual(ka["silent"], 0)

    def test_the_cap_ends_keep_alives_with_a_summary_that_names_the_work(self):
        with tempfile.TemporaryDirectory() as td:
            self._subagent(td)
            self._armed_near_expiry(td)
            self.assertIn("keep-alive", self._monitor(td, SCRY_KEEPALIVE_MAX="1").stdout)
            time.sleep(1.1)
            self._statusline(td, 90)
            said = self._monitor(td, SCRY_KEEPALIVE_MAX="1").stdout
            self.assertIn("goes cold", said)
            self.assertIn("allowed per user message are spent", said)
            self.assertIn("End the summary with those unfinished workers", said)
            self.assertIn(f"pick up from session {self.SID}", said)
            self.assertRegex(said, r'subagent a1b2c3 "build:opus": last activity \d+s ago')
            self.assertEqual(self._state(td)[0], "requested")
            self.assertEqual(self._monitor(td, SCRY_KEEPALIVE_MAX="1").stdout, "")

    def test_a_new_user_message_starts_the_count_again_and_nothing_caps_a_session(self):
        # Matt, 2026-09-22: a user message is the signal the session is
        # alive; a per-session ceiling "defeats the purpose". Three messages,
        # three keep-alives at a cap of one each, and no state that
        # remembers a session total.
        with tempfile.TemporaryDirectory() as td:
            self._subagent(td)
            cap = {"SCRY_KEEPALIVE_MAX": "1", "SCRY_KEEPALIVE_MAX_PER_SESSION": "2"}
            for n in (1, 2, 3):
                self._armed_near_expiry(td)
                self._subagent(td)
                said = self._monitor(td, **cap).stdout
                self.assertIn("Keep-alive 1 of at most 1", said)
                self.assertNotIn("per session", said)
                time.sleep(1.1)
            ka = json.loads((Path(td) / "state" / f"{self.SID}.keepalive").read_text())
            self.assertNotIn("total", ka)

    def test_a_keep_alive_that_does_not_move_the_deadline_falls_back_to_the_summary(self):
        with tempfile.TemporaryDirectory() as td:
            self._subagent(td)
            self._armed_near_expiry(td)
            self.assertIn("keep-alive", self._monitor(td).stdout)
            # Half the lead passes and the status line still reports the
            # same expiry: the ack did not happen.
            ka_path = Path(td) / "state" / f"{self.SID}.keepalive"
            ka = json.loads(ka_path.read_text())
            ka["sent_at"] -= 100
            ka_path.write_text(json.dumps(ka))
            said = self._monitor(td).stdout
            self.assertIn("did not refresh the cache", said)
            self.assertIn("as your reply here in the conversation", said)
            self.assertEqual(self._state(td)[0], "requested")


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
                r"^ \x1b\[1;36mscry\x1b\[0m  Fable 5\.1  \$4\.20  🗓️ 64% " + day + r"  🌕 42k/200k  ♻️ (39|40)m$")

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

    def test_week_turns_yellow_then_red_and_cold_cache_is_red(self):
        with tempfile.TemporaryDirectory() as td:
            work = self._repo(td)
            now = int(time.time())
            y = self._run(td, str(work), rate_limits={"seven_day": {"used_percentage": 72, "resets_at": now + 86400}})
            self.assertIn("🗓️ \x1b[33m72% ", y)
            r = self._run(td, str(work),
                          rate_limits={"seven_day": {"used_percentage": 93, "resets_at": now + 86400}},
                          prompt_cache={"warm": False, "recache_tokens_if_cold": 42000})
            self.assertIn("🗓️ \x1b[31m93% ", r)
            self.assertTrue(r.endswith("🗑️ \x1b[31m~42k\x1b[0m"), r)

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
            self.assertRegex(out, r"^ \x1b\[1;36mscry\x1b\[0m  Fable 5\.1  ♻️ (39|40)m$")

    def test_the_bar_still_renders_outside_a_repository(self):
        with tempfile.TemporaryDirectory() as td:
            plain = Path(td) / "notes"
            plain.mkdir()
            out = self._run(td, str(plain))
            self.assertTrue(out.startswith(" \x1b[1;36mnotes\x1b[0m  Fable 5.1  $4.20"), out)


class RosterTests(unittest.TestCase):
    """roster.sh / roster.py: what this session started that has not
    finished, read from disk and the process table (2026-09-14)."""
    SID = "sess-roster-0001"
    PID = "424242"
    PS = "\n".join([
        "  424242   1000 Sun Sep 13 11:31:48 2026 /Users/x/.local/bin/claude",
        "  424243 424242 Sun Sep 13 11:31:51 2026 /bin/zsh -c source snap.sh && eval "
        "'\"/Users/x/.claude/plugins/cache/scry/scry/1.29.0\"/cache_handoff_monitor.sh' < /dev/null",
        "  424244 424243 Sun Sep 13 11:31:51 2026 bash /Users/x/.claude/plugins/cache/scry/scry/1.29.0/cache_handoff_monitor.sh",
        "  424245 424242 Sun Sep 13 11:31:50 2026 node /Users/x/.hindsight/mcp-server.js",
        # A hook running under the same process, from the plugin cache: not a monitor.
        "  424246 424242 Sun Sep 13 11:31:52 2026 bash /Users/x/.claude/plugins/cache/scry/scry/1.29.0/fleet.sh",
        "  424247 424242 Sun Sep 13 11:31:52 2026 python3 /Users/x/.claude/plugins/cache/guard/security/2.0.8/hooks/reminder.py",
        "  999999 999998 Sun Sep 13 11:31:51 2026 bash /Users/x/.claude/plugins/cache/scry/scry/1.31.0/cache_handoff_monitor.sh",
    ])

    def _env(self, td, installed="1.31.0", **extra):
        base = Path(td)
        (base / "plugins").mkdir(exist_ok=True)
        for ver in ("1.29.0", "1.31.0"):
            mon = base / "plugins" / "cache" / "scry" / "scry" / ver / "monitors"
            mon.mkdir(parents=True, exist_ok=True)
            (mon / "monitors.json").write_text(json.dumps([
                {"name": "cache-handoff",
                 "command": "\"${CLAUDE_PLUGIN_ROOT}\"/cache_handoff_monitor.sh"}]))
        (base / "plugins" / "installed_plugins.json").write_text(json.dumps({
            "version": 2, "plugins": {"scry@scry": [{"version": installed, "scope": "user"}]}}))
        env = {
            "CLAUDE_CODE_SESSION_ID": self.SID,
            "CLAUDE_PID": self.PID,
            "SCRY_CLAUDE_SESSIONS_DIR": str(base / "sessions"),
            "SCRY_CLAUDE_PROJECTS_DIR": str(base / "projects"),
            "SCRY_TASK_STATE_DIR": str(base / "tasks"),
            "SCRY_CACHE_STATE_DIR": str(base / "state"),
            "SCRY_INSTALLED_PLUGINS": str(base / "plugins" / "installed_plugins.json"),
            "SCRY_PLUGINS_ROOT": str(base / "plugins"),
            "SCRY_PS_OUTPUT": self.PS,
        }
        env.update(extra)
        return env

    def _task(self, td, tid, text, age=0, sid=None, held=False):
        d = Path(td) / "tasks" / "-Users-someone-repo" / (sid or self.SID) / "tasks"
        d.mkdir(parents=True, exist_ok=True)
        out = d / f"{tid}.output"
        out.write_text(text)
        if held:
            hold_open(self, out)
        t = time.time() - age
        os.utime(out, (t, t))
        return out

    def _roster(self, td, **extra):
        merged = os.environ.copy()
        merged.update(self._env(td, **extra))
        # stdin is an open pipe nobody closes — exactly the shape that hung a
        # backgrounded call on 2026-09-14. A timeout here is the test failing.
        return subprocess.run(["bash", str(ROOT / "roster.sh")], cwd=td, text=True,
                              capture_output=True, check=True, env=merged,
                              stdin=subprocess.PIPE, timeout=20)

    def test_a_forgotten_watcher_is_listed_by_its_file_not_by_memory(self):
        # 62aa5e9f: four tails stopped, a fifth missed, "nothing running" said
        # twice. Its b*.output had no exit marker all night.
        with tempfile.TemporaryDirectory() as td:
            self._task(td, "b55udo2rk", "[round 3] SECRET compile exit=1\n", age=7 * 3600 + 30, held=True)
            self._task(td, "b2287w1k9", "SECRET\n\n[killed]\n", age=7 * 3600)
            self._task(td, "b4jdo82tu", "SECRET\n[exited with code 0]\n")
            out = self._roster(td).stdout
            self.assertIn(f"roster for session {self.SID}", out)
            self.assertRegex(out, r"- background command b55udo2rk: a process still holds its output open, last output 7h0\dm ago")
            self.assertNotIn("b2287w1k9", out)
            self.assertNotIn("b4jdo82tu", out)
            self.assertNotIn("SECRET", out)
            self.assertIn("Scry stops nothing", out)

    def test_the_monitors_own_stream_and_a_finished_foreground_call_are_not_work(self):
        # The cache monitor's notifications land in a b*.output it holds open
        # for as long as it lives (read live 2026-09-14), and a foreground
        # call — any size — leaves its task file behind with no marker and
        # no holder (read live 2026-09-22: fifty of them in one session, up
        # to four days old, each "still running" to the old marker test).
        with tempfile.TemporaryDirectory() as td:
            self._task(td, "bmonitor1", "Scry — cache deadline (this is a Scry monitor "
                                        "notification, not a user message): SECRET\n",
                       age=3600, held=True)
            self._task(td, "bbigfind1", "/Users/x/a\n/Users/x/b\n", age=1500)
            self._task(td, "bcredo001", "Checking 2349 source files\nSECRET\n", age=80 * 3600)
            self._task(td, "bstillon1", "going\n", age=1530, held=True)
            out = self._roster(td, SCRY_PS_OUTPUT="").stdout
            self.assertNotIn("bmonitor1", out)
            self.assertNotIn("bbigfind1", out)
            self.assertNotIn("bcredo001", out)
            self.assertIn("background command bstillon1: a process still holds its output open, last output 25m ago", out)
            self.assertNotIn("SECRET", out)

    def test_a_plugin_monitor_is_listed_with_the_version_it_is_running(self):
        with tempfile.TemporaryDirectory() as td:
            out = self._roster(td).stdout
            # One line per script, not one per wrapper process; the stranger
            # under another Claude process is not this session's.
            self.assertEqual(out.count("cache_handoff_monitor.sh"), 1)
            # Hooks and skills run under the same process from the same cache;
            # only what monitors/monitors.json registers is a monitor.
            self.assertNotIn("fleet.sh", out)
            self.assertNotIn("reminder.py", out)
            self.assertIn("- plugin monitor scry/cache_handoff_monitor.sh (v1.29.0, started Sep 13 11:31; "
                          "installed is v1.31.0 — a monitor keeps the copy its session launched with "
                          "until the session restarts)", out)
            self.assertNotIn("1.31.0/cache", out)
        with tempfile.TemporaryDirectory() as td:
            out = self._roster(td, installed="1.29.0").stdout
            self.assertIn("(v1.29.0, started Sep 13 11:31)", out)
            self.assertNotIn("installed is", out)

    def test_nothing_running_says_so_in_one_line(self):
        with tempfile.TemporaryDirectory() as td:
            out = self._roster(td, SCRY_PS_OUTPUT="").stdout
            self.assertIn("nothing this session started is still unfinished", out)
            self.assertEqual(out.count("\n"), 1)

    def test_it_follows_a_clear_to_the_current_session(self):
        # The environment id is the launch id; /clear changes the session
        # without restarting the process, and Claude Code's own record says so.
        with tempfile.TemporaryDirectory() as td:
            new = "sess-roster-0002"
            sd = Path(td) / "sessions"
            sd.mkdir()
            (sd / f"{self.PID}.json").write_text(json.dumps({"sessionId": new}))
            self._task(td, "bnew00001", "going\n", sid=new, held=True)
            out = self._roster(td, SCRY_PS_OUTPUT="").stdout
            self.assertIn(f"roster for session {new}", out)
            self.assertIn("background command bnew00001: a process still holds its output open", out)

    def test_fleet_names_this_sessions_unfinished_work_on_resume_and_its_monitors(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            repo = base / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            enc = re.sub(r"[/._]", "-", str(repo.resolve()))
            tasks = base / "taskroot" / enc / self.SID / "tasks"
            tasks.mkdir(parents=True)
            (tasks / "bwatch001.output").write_text("SECRET partial\n")
            hold_open(self, tasks / "bwatch001.output")
            (tasks / "bdone0001.output").write_text("SECRET\n[exited with code 0]\n")
            (base / "plugins").mkdir()
            (base / "plugins" / "installed_plugins.json").write_text(json.dumps(
                {"plugins": {"scry@scry": [{"version": "1.31.0"}]}}))
            mon = base / "plugins" / "cache" / "scry" / "scry" / "1.29.0" / "monitors"
            mon.mkdir(parents=True)
            (mon / "monitors.json").write_text(json.dumps(
                [{"command": "\"${CLAUDE_PLUGIN_ROOT}\"/cache_handoff_monitor.sh"}]))
            env = {"HOME": str(base), "CODEX_HOME": str(base / ".codex"),
                   "SCRY_CLEAR_STATE_DIR": str(base / "cleared"),
                   "SCRY_CACHE_STATE_DIR": str(base / "deadline"),
                   "SCRY_TASK_STATE_DIR": str(base / "taskroot"),
                   "SCRY_INSTALLED_PLUGINS": str(base / "plugins" / "installed_plugins.json"),
                   "SCRY_PLUGINS_ROOT": str(base / "plugins"),
                   "SCRY_PS_OUTPUT": self.PS, "CLAUDE_PID": self.PID}
            payload = {"cwd": str(repo), "session_id": self.SID,
                       "transcript_path": str(base / ".claude/projects" / enc / f"{self.SID}.jsonl"),
                       "source": "resume"}
            report = context(run_hook("fleet.sh", repo, payload, env))
            self.assertIn("WORK THIS SESSION STARTED HAS NOT FINISHED", report)
            self.assertIn("background command bwatch001: a process still holds its output open", report)
            self.assertNotIn("bdone0001", report)
            self.assertNotIn("SECRET", report)
            self.assertIn("Plugin monitor(s) under this Claude process: plugin monitor "
                          "scry/cache_handoff_monitor.sh (v1.29.0, started Sep 13 11:31; "
                          "installed is v1.31.0", report)
            # A clear reports the cleared session's work instead, unchanged.
            payload["source"] = "clear"
            report = context(run_hook("fleet.sh", repo, payload, env))
            self.assertNotIn("WORK THIS SESSION STARTED", report)


class SubagentColdAdvisoryTests(unittest.TestCase):
    """subagent_cold_advisory.sh (PostToolUse): the cold-cache note rides a
    tool result the main agent is about to read anyway — awake, this; idle,
    the keep-alive line — never a wake of its own (Matt, 2026-09-22)."""
    SID = "sess-cold-0001"

    def _stage(self, td, age, aid="a1b2c3", description="build:opus"):
        proj = Path(td) / "projects" / "-Users-someone-repo"
        sub = proj / self.SID / "subagents"
        sub.mkdir(parents=True, exist_ok=True)
        j = sub / f"agent-{aid}.jsonl"
        j.write_text('{"type":"assistant","message":"SECRET AGENT TEXT"}\n')
        (sub / f"agent-{aid}.meta.json").write_text(json.dumps({"description": description}))
        t = time.time() - age
        os.utime(j, (t, t))
        return str(proj / f"{self.SID}.jsonl")

    def _run(self, td, tp, agent_id=""):
        payload = {"session_id": self.SID, "transcript_path": tp, "tool_name": "Bash",
                   "tool_input": {"command": "SECRET"}, "tool_response": "SECRET"}
        if agent_id:
            payload["agent_id"] = agent_id
        return context(run_hook("subagent_cold_advisory.sh", td, payload,
                                {"SCRY_SUBAGENT_COLD_STATE_DIR": str(Path(td) / "cold")}))

    def test_it_speaks_once_in_the_last_quarter_of_the_ttl_and_not_inside_a_subagent(self):
        with tempfile.TemporaryDirectory() as td:
            tp = self._stage(td, age=1300)
            self.assertEqual(self._run(td, tp), "")
            tp = self._stage(td, age=2800)
            self.assertEqual(self._run(td, tp, agent_id="a1b2c3"), "")
            said = self._run(td, tp)
            self.assertIn('Scry — a subagent this session started is going quiet: subagent '
                          'a1b2c3 "build:opus": silent 46m — its own cache goes cold at 1h00m '
                          "of silence: finished, nothing happens; still working, its next step "
                          "re-reads its context once at the full rate; stuck, stop it now "
                          "rather than after that read", said)
            self.assertIn("ListAgents", said)
            self.assertNotIn("SECRET", said)
            self.assertEqual(self._run(td, tp), "")
            # Past the TTL it is left alone: cold already, and Scry cannot
            # tell a finished subagent from a quiet one.
            tp = self._stage(td, age=3700, aid="d4e5f6")
            self.assertEqual(self._run(td, tp), "")


class TeammateRosterTests(unittest.TestCase):
    """Idle in-process teammates (2026-09-21): twenty of them, four days
    old, sat registered in a trellys-app process and no report named them.
    Scry ties a teammate to the process that can still hold it through its
    own per-process session chain, says so once per process, and never
    guesses whether one is still registered — ListAgents does that."""
    LIVE_PID = "424242"
    DEAD_PID = "313131"
    PS = "  424242   1000 Sun Sep 13 11:31:48 2026 /Users/x/.local/bin/claude\n"

    def _teammate(self, td, sid, name, age=0, kind="in_process_teammate"):
        d = Path(td) / "projects" / "-Users-x-repo" / sid / "subagents"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"agent-a{name}-0123.meta.json").write_text(json.dumps(
            {"name": name, "taskKind": kind, "teamName": f"session-{sid[:8]}"}))
        j = d / f"agent-a{name}-0123.jsonl"
        j.write_text('{"cwd":"/Users/x/repo","content":"SECRET"}\n')
        t = time.time() - age
        os.utime(j, (t, t))

    def _chain(self, td, pid, *sids):
        d = Path(td) / "state" / "pid"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{pid}.sessions").write_text("".join(s + "\n" for s in sids))

    def _run(self, td, skip_sid=None):
        sys.path.insert(0, str(ROOT))
        import roster
        fam = [str(Path(td) / "projects" / "-Users-x-repo")]
        return roster.teammate_finding(fam, str(Path(td) / "state"), str(Path(td) / "told"),
                                       self.LIVE_PID, ps_text=self.PS, skip_sid=skip_sid)

    def test_a_teammate_of_a_session_a_live_process_held_is_named_with_its_age(self):
        with tempfile.TemporaryDirectory() as td:
            old = "73249ef1-48bb-4d27-9cbf-f0111a441857"
            self._chain(td, self.LIVE_PID, old, "bb0ab591-b4c8-4714-9f97-56bfbc89af54")
            self._teammate(td, old, "wt-audit-marks", age=4 * 86400 + 3660)
            self._teammate(td, old, "plain-sub", age=60, kind="subagent")
            out = self._run(td)
            self.assertIn("1 in-process teammate(s)", out)
            self.assertIn("teammate wt-audit-marks (session 73249ef1): last wrote 4d01h ago", out)
            self.assertNotIn("plain-sub", out)
            self.assertNotIn("SECRET", out)
            self.assertIn("ListAgents", out)

    def test_a_teammate_whose_process_is_gone_is_not_a_candidate(self):
        with tempfile.TemporaryDirectory() as td:
            gone = "9e30b605-75fd-4ef6-bbe3-487e79f8e6b8"
            self._chain(td, self.DEAD_PID, gone)
            self._teammate(td, gone, "orphan-of-dead", age=3600)
            self.assertEqual(self._run(td), "")

    def test_the_current_sessions_own_teammates_are_skipped_at_start(self):
        with tempfile.TemporaryDirectory() as td:
            me = "5665a0d1-bdcf-44db-87ed-083638ae11d5"
            self._chain(td, self.LIVE_PID, me)
            self._teammate(td, me, "mine", age=60)
            self.assertEqual(self._run(td, skip_sid=me), "")
            self.assertIn("teammate mine", self._run(td))

    def test_a_reused_pid_does_not_inherit_a_dead_processs_sessions(self):
        # pid/<pid>.sessions is never swept; a new claude on the same pid
        # must not report the dead one's teammates (review, 2026-09-21).
        with tempfile.TemporaryDirectory() as td:
            sid = "73249ef1-48bb-4d27-9cbf-f0111a441857"
            self._chain(td, self.LIVE_PID, sid)
            self._teammate(td, sid, "phantom", age=3600)
            chain = Path(td) / "state" / "pid" / f"{self.LIVE_PID}.sessions"
            before = time.mktime(time.strptime("Sun Sep 13 11:31:48 2026", "%a %b %d %H:%M:%S %Y")) - 60
            os.utime(chain, (before, before))
            self.assertEqual(self._run(td), "")

    def test_a_same_named_teammate_from_a_later_session_is_still_reported(self):
        with tempfile.TemporaryDirectory() as td:
            a = "aaaaaaaa-0000-0000-0000-000000000001"
            b = "bbbbbbbb-0000-0000-0000-000000000002"
            self._chain(td, self.LIVE_PID, a, b)
            self._teammate(td, a, "reviewer", age=600)
            self.assertIn("teammate reviewer (session aaaaaaaa)", self._run(td))
            d = Path(td) / "projects" / "-Users-x-repo" / b / "subagents"
            d.mkdir(parents=True)
            (d / "agent-areviewer-9999.meta.json").write_text(json.dumps(
                {"name": "reviewer", "taskKind": "in_process_teammate"}))
            (d / "agent-areviewer-9999.jsonl").write_text("{}\n")
            self.assertIn("teammate reviewer (session bbbbbbbb)", self._run(td))

    def test_the_stop_hook_does_not_consume_the_one_shot_the_clear_successor_needs(self):
        # Session A's Stop reminds A of its own teammate; the session /clear
        # starts next in the same process must still be told (review).
        with tempfile.TemporaryDirectory() as td:
            a = "aaaaaaaa-0000-0000-0000-000000000001"
            self._chain(td, self.LIVE_PID, a)
            self._teammate(td, a, "left-behind", age=600)
            sys.path.insert(0, str(ROOT))
            import roster
            fam = [str(Path(td) / "projects" / "-Users-x-repo")]
            args = (fam, str(Path(td) / "state"), str(Path(td) / "told"), self.LIVE_PID)
            self.assertIn("left-behind", roster.teammate_finding(*args, ps_text=self.PS, mark=False))
            self.assertIn("left-behind", roster.teammate_finding(
                *args, ps_text=self.PS, skip_sid="bbbbbbbb-0000-0000-0000-000000000002"))

    def test_said_once_per_process_then_only_for_new_names(self):
        # Stopping a teammate writes nothing to disk, so without a marker
        # every later session in the process would be told the same names.
        with tempfile.TemporaryDirectory() as td:
            sid = "73249ef1-48bb-4d27-9cbf-f0111a441857"
            self._chain(td, self.LIVE_PID, sid)
            self._teammate(td, sid, "first", age=600)
            self.assertIn("teammate first", self._run(td))
            self.assertEqual(self._run(td), "")
            self._teammate(td, sid, "second", age=1)
            again = self._run(td)
            self.assertIn("teammate second", again)
            self.assertNotIn("teammate first", again)

    def test_the_arm_hook_records_every_session_a_process_has_held(self):
        with tempfile.TemporaryDirectory() as td:
            # A real pid: the hook keeps a chain only while ps says the
            # process started before the chain was written.
            env = {"CLAUDE_PID": str(os.getpid()), "SCRY_CACHE_STATE_DIR": str(Path(td) / "state")}
            for sid in ("aaaaaaaa-0000-0000-0000-000000000001",
                        "bbbbbbbb-0000-0000-0000-000000000002",
                        "aaaaaaaa-0000-0000-0000-000000000001"):
                run_hook("cache_handoff_arm.sh", td,
                         {"session_id": sid, "hook_event_name": "SessionStart"}, env=env)
            chain = (Path(td) / "state" / "pid" / f"{os.getpid()}.sessions").read_text().split()
            self.assertEqual(chain, ["aaaaaaaa-0000-0000-0000-000000000001",
                                     "bbbbbbbb-0000-0000-0000-000000000002"])



class SeedDataAdvisoryTests(unittest.TestCase):
    """seed_data_advisory.sh: seed and test data read or found by a search, and
    an empty search in a repo that keeps records in a database. Born
    2026-09-24 after a session read a kind's absence from the seed file as
    its absence from the database."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        base = Path(self._td.name)
        self.repo = base / "repo"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.repo)], check=True)
        self.env = {"TMPDIR": str(base / "state")}

    def tearDown(self):
        self._td.cleanup()

    def hook(self, tool, tool_input, response=None, session="s1"):
        payload = {"session_id": session, "cwd": str(self.repo), "tool_name": tool,
                   "tool_input": tool_input, "tool_response": response}
        return context(run_hook("seed_data_advisory.sh", self.repo, payload, env=self.env))

    def add_migration(self):
        m = self.repo / "priv/repo/migrations/20260101_init.exs"
        m.parent.mkdir(parents=True)
        m.write_text("defmodule M do end\n")
        subprocess.run(["git", "-C", str(self.repo), "add", "."], check=True)

    def test_read_of_a_seed_file_gives_the_insight_once(self):
        seeds = str(self.repo / "priv/repo/seeds.exs")
        first = self.hook("Read", {"file_path": seeds})
        self.assertIn("Insight: this is a seed file.", first)
        self.assertIn("untrustworthy", first)
        self.assertEqual(self.hook("Read", {"file_path": seeds}), "")
        self.assertIn("seed file", self.hook("Read", {"file_path": seeds}, session="s2"))

    def test_read_of_test_data_names_it_as_test_data(self):
        for name in ("test/support/factory.ex", "tests/fixtures/kinds.json", "lib/kinds_fixture.py"):
            with self.subTest(name=name):
                self.assertIn("Insight: this is test data.",
                              self.hook("Read", {"file_path": str(self.repo / name)}))

    def test_read_of_ordinary_code_is_silent(self):
        for name in ("lib/kinds.ex", "lib/seedling.ex", "README.md",
                     "priv/repo/migrations/20260904_phase1_seed_from_live.exs"):
            with self.subTest(name=name):
                self.assertEqual(self.hook("Read", {"file_path": str(self.repo / name)}), "")

    def test_grep_that_reaches_a_seed_file_names_it(self):
        report = self.hook("Grep", {"pattern": "certificate"},
                           {"mode": "files_with_matches", "numFiles": 2,
                            "filenames": ["lib/kinds.ex", "priv/repo/seeds.exs"]})
        self.assertIn("priv/repo/seeds.exs", report)
        self.assertNotIn("lib/kinds.ex", report)

    def test_grep_content_mode_reads_paths_from_lines(self):
        report = self.hook("Grep", {"pattern": "kind", "output_mode": "content"},
                           {"mode": "content", "numFiles": 0, "filenames": [],
                            "content": "priv/repo/seeds/kinds.exs:12:  %{name: \"motion\"}\nlib/a.ex:3:  x"})
        self.assertIn("priv/repo/seeds/kinds.exs", report)

    def test_grep_content_lines_of_code_are_not_read_as_paths(self):
        report = self.hook("Grep", {"pattern": "factory", "path": "lib/a.ex", "output_mode": "content"},
                           {"mode": "content", "filenames": [], "content": "3:  def factory_for(x), do: x"})
        self.assertEqual(report, "")

    def test_bash_search_output_is_read(self):
        report = self.hook("Bash", {"command": "rg -l certificate"},
                           {"stdout": "priv/repo/seeds.exs\nlib/kinds.ex\n", "stderr": ""})
        self.assertIn("priv/repo/seeds.exs", report)
        self.assertEqual(self.hook("Bash", {"command": "cat priv/repo/seeds2.exs"},
                                   {"stdout": "priv/repo/seeds2.exs\n"}), "")

    def test_empty_search_speaks_only_in_a_repo_with_a_database_and_once(self):
        empty = {"mode": "files_with_matches", "numFiles": 0, "filenames": []}
        self.assertEqual(self.hook("Grep", {"pattern": "certificate of service"}, empty), "")
        self.add_migration()
        report = self.hook("Grep", {"pattern": "certificate of service"}, empty)
        self.assertIn("Insight: no match in this repo's code.", report)
        self.assertEqual(self.hook("Grep", {"pattern": "other"}, empty), "")

    def test_empty_bash_search_speaks_but_not_through_a_pipeline(self):
        self.add_migration()
        self.assertEqual(self.hook("Bash", {"command": "grep -r x lib | head"}, {"stdout": ""}), "")
        self.assertEqual(self.hook("Bash", {"command": "grep -r '[' lib"},
                                   {"stdout": "", "stderr": "grep: brackets not balanced"}), "")
        self.assertIn("no match", self.hook("Bash", {"command": "grep -r x lib"}, {"stdout": ""}))

    def test_registered_on_read_and_on_searches(self):
        hooks = json.loads((ROOT / "hooks/hooks.json").read_text())["hooks"]["PostToolUse"]
        matchers = {g.get("matcher") for g in hooks
                    if any("seed_data_advisory.sh" in h["command"] for h in g["hooks"])}
        self.assertEqual(matchers, {"Read", "Grep|Glob|Bash"})


class CvssScoreTests(unittest.TestCase):
    """scanners/cvss_score.py: severity from a CVSS vector, for the advisories
    that publish a vector and no label. Born 2026-10-11: on one repo 16
    runtime packages -- `ash` with 24 advisories among them -- were "unrated"
    for that reason, and unrated never reaches the startup line or the bar.

    The expected scores below are the ones FIRST publishes. During development
    the scorer was also compared with the `cvss` package from PyPI on every
    CVSS 4.0 base vector (104,976) and every 3.x one (2,592), and on random
    vectors carrying threat and environmental metrics; that package is not a
    dependency and is not imported here."""

    @classmethod
    def setUpClass(cls):
        import importlib.util

        def load(name):
            spec = importlib.util.spec_from_file_location(
                "scry_" + name, ROOT / "scanners" / (name + ".py"))
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
        cls.cvss = load("cvss_score")
        cls.pins = load("pins")

    # FIRST, "CVSS v4.0 Examples" (first.org/cvss/v4.0/examples), the score in
    # each example's summary row; and the reference calculator's README.
    V4 = [
        (7.3, "AV:L/AC:L/AT:P/PR:L/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N"),
        (7.7, "AV:N/AC:L/AT:P/PR:N/UI:P/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N"),
        (5.2, "AV:N/AC:L/AT:P/PR:N/UI:P/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N/E:U"),
        (8.3, "AV:N/AC:L/AT:P/PR:N/UI:N/VC:H/VI:L/VA:L/SC:N/SI:N/SA:N"),
        (8.1, "AV:N/AC:L/AT:P/PR:N/UI:N/VC:H/VI:L/VA:L/SC:N/SI:N/SA:N"
              "/CR:H/IR:L/AR:L/MAV:N/MAC:H/MVC:H/MVI:L/MVA:L"),
        (4.6, "AV:L/AC:L/AT:N/PR:N/UI:A/VC:L/VI:N/VA:N/SC:N/SI:N/SA:N"),
        (5.1, "AV:N/AC:L/AT:N/PR:N/UI:A/VC:N/VI:N/VA:N/SC:L/SI:L/SA:N"),
        (6.9, "AV:N/AC:L/AT:N/PR:N/UI:N/VC:N/VI:N/VA:N/SC:L/SI:L/SA:N"),
        (5.9, "AV:L/AC:L/AT:N/PR:H/UI:N/VC:N/VI:N/VA:N/SC:H/SI:N/SA:N"),
        (9.4, "AV:L/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:H/SI:H/SA:H"),
        (9.3, "AV:N/AC:L/AT:N/PR:N/UI:N/VC:N/VI:H/VA:N/SC:H/SI:H/SA:H"),
        (8.3, "AV:P/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:H/SA:N/S:P/V:D"),
        (10.0, "AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:H/SI:H/SA:H/E:A"),
        (8.7, "AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:N/VA:N/SC:N/SI:N/SA:N/E:A"),
        (9.3, "AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N"),
        (5.5, "AV:N/AC:H/AT:P/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N"
              "/E:P/MAC:L/MAT:N/MVC:N/MVI:N/MVA:L"),
        (8.2, "AV:N/AC:H/AT:P/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N/E:P"),
        (6.4, "AV:A/AC:L/AT:N/PR:N/UI:N/VC:N/VI:L/VA:N/SC:H/SI:N/SA:H"),
        (8.4, "AV:L/AC:L/AT:N/PR:H/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N/R:I"),
        (7.0, "AV:P/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N"),
        (7.1, "AV:N/AC:L/AT:N/PR:L/UI:N/VC:H/VI:N/VA:N/SC:N/SI:N/SA:N"),
        (6.6, "AV:N/AC:L/AT:N/PR:N/UI:N/VC:N/VI:N/VA:H/SC:N/SI:N/SA:L/E:U/MVA:H/MSA:N"),
        (8.5, "AV:N/AC:L/AT:N/PR:L/UI:A/VC:H/VI:H/VA:H/SC:L/SI:L/SA:N"),
        (5.6, "AV:L/AC:L/AT:N/PR:H/UI:N/VC:H/VI:H/VA:N/SC:N/SI:N/SA:N/E:U"),
        (5.7, "AV:L/AC:L/AT:N/PR:N/UI:P/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N/E:U/MUI:A"),
        (5.4, "AV:P/AC:L/AT:P/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N"),
        (2.7, "AV:N/AC:L/AT:N/PR:N/UI:N/VC:N/VI:N/VA:N/SC:N/SI:L/SA:N/E:U/CR:L/IR:L/AR:L"),
        (7.8, "AV:N/AC:L/AT:N/PR:N/UI:N/VC:N/VI:L/VA:N/SC:L/SI:N/SA:H"),
        (9.4, "AV:N/AC:L/AT:N/PR:L/UI:N/VC:H/VI:H/VA:H/SC:H/SI:H/SA:H/S:P/AU:Y/V:C/RE:L"),
        (7.0, "AV:N/AC:L/AT:N/PR:L/UI:N/VC:H/VI:H/VA:H/SC:H/SI:H/SA:H/E:P/CR:L/IR:H/AR:L"
              "/MAV:L/MAC:H/MAT:N/MPR:N/MUI:N/MVC:N/MVI:H/MVA:L/MSC:N/MSI:S/MSA:L"),
        (7.4, "AV:N/AC:L/AT:N/PR:L/UI:N/VC:H/VI:H/VA:H/SC:H/SI:H/SA:H/MAV:A/MAC:H/MAT:N"
              "/MPR:L/MUI:N/MVC:L/MVI:H/MVA:H/MSC:L/MSI:S/MSA:S/CR:L/IR:H/AR:H/E:P"),
        (8.7, "AV:N/AC:L/AT:N/PR:L/UI:N/VC:H/VI:H/VA:H/SC:H/SI:H/SA:H/MAV:N/MAC:H/MAT:N"
              "/MPR:L/MUI:N/MVC:H/MVI:H/MVA:H/MSC:H/MSI:S/MSA:H/CR:M/IR:H/AR:M/E:P"),
        (9.7, "AV:A/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:N/SC:N/SI:N/SA:N/MSI:S/S:P"),
        (2.4, "AV:N/AC:L/AT:N/PR:L/UI:N/VC:N/VI:N/VA:N/SC:H/SI:L/SA:H/E:U/MAV:A/R:U/V:C"),
        (8.9, "AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:H/SI:H/SA:H"
              "/E:A/CR:H/IR:H/AR:H/MAV:L/MAT:P"),
        (0.0, "AV:L/AC:L/AT:N/PR:L/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N/E:A/MVC:N/MVI:N/MVA:N"),
        (0.0, "AV:N/AC:L/AT:N/PR:N/UI:N/VC:N/VI:N/VA:N/SC:N/SI:N/SA:N"),
        (10.0, "AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:H/SI:H/SA:H"),
        (5.1, "AV:N/AC:L/AT:N/PR:H/UI:N/VC:L/VI:L/VA:N/SC:N/SI:N/SA:N"),  # README
    ]
    # FIRST, "CVSS v3.1 Examples", and the v3.1 scores beside the v4.0
    # examples above.
    V3 = [
        (6.1, "AV:N/AC:L/PR:N/UI:R/S:C/C:L/I:L/A:N"),     # phpMyAdmin XSS
        (6.4, "AV:N/AC:L/PR:L/UI:N/S:C/C:L/I:L/A:N"),     # MySQL stored SQLi
        (3.1, "AV:N/AC:H/PR:N/UI:R/S:U/C:L/I:N/A:N"),     # POODLE
        (9.9, "AV:N/AC:L/PR:L/UI:N/S:C/C:H/I:H/A:H"),     # VMware guest to host
        (7.5, "AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:N/A:N"),     # Heartbleed
        (9.8, "AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"),
        (10.0, "AV:N/AC:L/PR:N/UI:N/S:C/C:H/I:H/A:H"),
        (7.6, "AV:P/AC:L/PR:N/UI:N/S:C/C:H/I:H/A:H"),
        (8.2, "AV:L/AC:L/PR:H/UI:N/S:C/C:H/I:H/A:H"),
        (7.5, "AV:N/AC:L/PR:N/UI:N/S:U/C:N/I:H/A:N"),
        (0.0, "AV:N/AC:L/PR:N/UI:N/S:U/C:N/I:N/A:N"),
    ]

    def test_cvss_4_scores_are_the_ones_first_publishes(self):
        for expected, body in self.V4:
            self.assertEqual(self.cvss.score("CVSS:4.0/" + body), expected, body)

    def test_cvss_3_base_scores_are_the_ones_first_publishes(self):
        for expected, body in self.V3:
            for version in ("3.1", "3.0"):
                self.assertEqual(self.cvss.score("CVSS:%s/%s" % (version, body)),
                                 expected, body)
        # Temporal and environmental metrics do not move the BASE score.
        self.assertEqual(self.cvss.score(
            "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H/E:U/RL:O/RC:C/CR:L/MAV:P"), 9.8)

    def test_every_macrovector_in_the_table_is_reachable_and_in_range(self):
        table = self.cvss.V4_LOOKUP
        self.assertEqual(len(table), 270)
        self.assertEqual(table["000000"], 100)
        self.assertTrue(all(0 < tenths <= 100 for tenths in table.values()))
        seen = set()
        import itertools
        base = self.cvss.V4_BASE
        for combo in itertools.product(*base.values()):
            metrics = dict(zip(base, combo))
            if all(metrics[m] == "N" for m in ("VC", "VI", "VA", "SC", "SI", "SA")):
                continue
            eqs = self.cvss._v4_macrovector(self.cvss._v4_effective(metrics))
            seen.add("".join(map(str, eqs)))
        self.assertTrue(seen <= set(table))

    def test_a_malformed_vector_scores_none_and_never_raises(self):
        good = "CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N"
        for bad in (None, "", 7.5, [], {}, "garbage", "CVSS:4.0", "CVSS:4.0/",
                    "CVSS:2.0/AV:N/AC:L/Au:N/C:P/I:P/A:P", "AV:N/AC:L/Au:N/C:P/I:P/A:P",
                    "CVSS:5.0/" + good[9:],
                    good.replace("/AT:N", ""),              # a base metric missing
                    good.replace("AV:N", "AV:Z"),           # a value that is not one
                    good.replace("AV:N", "AV"),             # no value at all
                    good + "/AV:N",                         # a metric twice
                    good + "/XX:Y", good + "/", good + "/E:",
                    good.replace("SI:N", "SI:S"),           # S is for MSI only
                    good.lower(), " " + good.replace("/", " / "),
                    "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H",          # no A
                    "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:X/C:H/I:H/A:H",
                    "CVSS:3.1/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N",
                    "CVSS:4.0/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"):
            self.assertIsNone(self.cvss.score(bad), repr(bad))
            self.assertIsNone(self.cvss.band(self.cvss.score(bad)))

    def test_bands_are_the_standard_ones(self):
        band = self.cvss.band
        self.assertEqual([band(v) for v in (10.0, 9.0, 8.9, 7.0, 6.9, 4.0, 3.9, 0.1, 0.0)],
                         ["CRITICAL", "CRITICAL", "HIGH", "HIGH", "MEDIUM", "MEDIUM",
                          "LOW", "LOW", "NONE"])

    def test_an_advisory_is_rated_from_its_vector_and_a_label_wins(self):
        digest = self.pins.digest_vuln
        v4 = lambda body: {"type": "CVSS_V4", "score": "CVSS:4.0/" + body}
        v3 = lambda body: {"type": "CVSS_V3", "score": "CVSS:3.1/" + body}
        critical = "AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N"    # 9.3
        high = "AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:N/VA:N/SC:N/SI:N/SA:N"        # 8.7
        medium = "AV:N/AC:L/AT:N/PR:H/UI:N/VC:L/VI:L/VA:N/SC:N/SI:N/SA:N"      # 5.1
        low = "AV:N/AC:L/AT:N/PR:N/UI:N/VC:N/VI:N/VA:N/SC:N/SI:L/SA:N/E:U/CR:L/IR:L/AR:L"
        rank = lambda record: (digest(record)["sev"], digest(record)["rated"])
        # What a Hex advisory looks like: a vector, and nothing else.
        self.assertEqual(rank({"severity": [v4(critical)]}), (4, True))
        self.assertEqual(rank({"severity": [v4(high)]}), (3, True))
        self.assertEqual(rank({"severity": [v4(medium)]}), (2, True))
        self.assertEqual(rank({"severity": [v4(low)]}), (1, True))
        self.assertEqual(rank({"severity": [v3("AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:N/A:N")]}),
                         (3, True))
        # 4.0 before 3.x when a record has both, whichever is the higher.
        self.assertEqual(rank({"severity": [
            v3("AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"), v4(medium)]}), (2, True))
        # ...but a 4.0 vector that cannot be read does not hide a 3.x that can.
        self.assertEqual(rank({"severity": [
            {"type": "CVSS_V4", "score": "CVSS:4.0/nonsense"},
            v3("AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H")]}), (4, True))
        # A vector published on the affected package counts as well.
        self.assertEqual(rank({"affected": [{"severity": [v4(high)]}]}), (3, True))
        # The publisher's label wins over anything worked out here.
        self.assertEqual(rank({"database_specific": {"severity": "LOW"},
                               "severity": [v4(critical)]}), (1, True))
        self.assertEqual(rank({"database_specific": {"severity": "MODERATE"},
                               "severity": [v4(critical)]}), (2, True))
        # No impact at all is a usable vector with no severity: rated, rank 0.
        self.assertEqual(rank({"severity": [v4(
            "AV:N/AC:L/AT:N/PR:N/UI:N/VC:N/VI:N/VA:N/SC:N/SI:N/SA:N")]}), (0, True))
        # Nothing usable stays unrated, and nothing raises.
        for record in ({}, {"severity": None}, {"severity": []}, {"severity": "HIGH"},
                       {"severity": [None, 7, "x", {}, {"type": "CVSS_V4"},
                                     {"type": "CVSS_V4", "score": None},
                                     {"type": "CVSS_V4", "score": "CVSS:4.0/AV:N"},
                                     {"type": "CVSS_V2", "score": "AV:N/AC:L/Au:N/C:P/I:P/A:P"},
                                     {"type": "Ubuntu", "score": "high"}]},
                       {"affected": [None, {"severity": [None]}]},
                       {"database_specific": {"severity": "who knows"}}):
            self.assertEqual(rank(record), (0, False), record)


class PinsTests(unittest.TestCase):
    """pins.sh: a pinned version with a published advisory, or far behind the
    latest release. Born 2026-10-09: `config :tailwind, version: "4.1.12"`
    could not run on the current macOS and no tool read it, and a mix.lock's
    advisories surfaced only in a hand-run audit.

    Reworked the same day, before it shipped: the first cut put ~1,300
    characters into the session at every start, resume, clear and compact.
    Startup is now silent but for one line said once; the count is in the
    status line, the detail in --full and in a note on the edited manifest.

    The registries are a local server here. Nothing in these tests reaches
    the network, and the first test is that the hook does not either."""

    LOCK = (
        '%{\n'
        '  "plug": {:hex, :plug, "1.18.1", "aa11", [:mix], [{:mime, "~> 2.0", [hex: :mime, repo: "hexpm", optional: false]}], "hexpm", "bb22"},\n'
        '  "mime": {:hex, :mime, "2.0.0", "aa11", [:mix], [], "hexpm", "bb22"},\n'
        '  "jason": {:hex, :jason, "1.4.4", "aa11", [:mix], [], "hexpm", "bb22"},\n'
        '  "oban_pro": {:hex, :oban_pro, "1.5.0", "aa11", [:mix], [], "oban", "bb22"},\n'
        '  "local_fork": {:git, "https://example.test/fork.git", "abc123", []},\n'
        '}\n')
    MIX = ('defp deps do\n  [{:plug, "~> 1.18"}, {:jason, "~> 1.4"},\n'
           '   {:oban_pro, "~> 1.5", repo: "oban"}]\nend\n')
    CONFIG = ('import Config\n\n# config :tailwind, version: "0.0.1"\n'
              'config :esbuild,\n  version: "0.28.2",\n  app: [args: ~w(js/app.js)]\n\n'
              'config :tailwind,\n  version: "4.1.12",\n  app: [args: ~w(--input=x)]\n\n'
              'config :app, version: "9.9.9"\n')
    OLD = "2026-01-01T00:00:00.000000Z"
    GITHUB = "/repos/tailwindlabs/tailwindcss/releases?per_page=20"
    ADVISORIES = {("Hex", "plug", "1.18.1"): ["EEF-CVE-2026-1", "GHSA-aaaa-bbbb-cccc"],
                  ("Hex", "mime", "2.0.0"): ["GHSA-dddd-eeee-ffff"]}
    VULNS = {
        "EEF-CVE-2026-1": {"aliases": ["CVE-2026-1", "GHSA-aaaa-bbbb-cccc"], "affected": [
            {"package": {"ecosystem": "Hex", "name": "plug"}, "ranges": [
                {"type": "SEMVER", "events": [{"introduced": "1.18.0"}, {"fixed": "1.18.5"}]},
                {"type": "SEMVER", "events": [{"introduced": "1.19.0"}, {"fixed": "1.19.2"}]}]}]},
        "GHSA-aaaa-bbbb-cccc": {"aliases": ["CVE-2026-1"],
                                "database_specific": {"severity": "HIGH"}, "affected": []},
        "GHSA-dddd-eeee-ffff": {"database_specific": {"severity": "LOW"}, "affected": []},
        "GHSA-high-high-high": {"database_specific": {"severity": "HIGH"}, "affected": []},
        "GHSA-crit-crit-crit": {"database_specific": {"severity": "CRITICAL"}, "affected": []},
    }

    # The registries' answers, in the shapes each one really sends.
    @classmethod
    def hex_doc(cls, *releases):
        """releases: (version, published) newest first; a bare string is OLD."""
        pairs = [(r, cls.OLD) if isinstance(r, str) else r for r in releases]
        return {"latest_stable_version": pairs[0][0],
                "releases": [{"version": v, "inserted_at": at} for v, at in pairs]}

    @classmethod
    def npm_doc(cls, version, at=None):
        at = at or cls.OLD
        return {"dist-tags": {"latest": version},
                "time": {"created": cls.OLD, "modified": at, version: at}}

    @classmethod
    def github_doc(cls, version, at=None):
        return [{"tag_name": "v" + version, "published_at": at or cls.OLD,
                 "draft": False, "prerelease": False}]

    @staticmethod
    def days_ago(days):
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - days * 86400))

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = self.tmp / "repo"
        self.cache = self.tmp / "cache"
        (self.tmp / "tmp").mkdir()
        app = self.repo / "apps" / "engine"
        (app / "config").mkdir(parents=True)
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.repo)], check=True)
        (app / "mix.exs").write_text(self.MIX)
        (app / "mix.lock").write_text(self.LOCK)
        (app / "config" / "config.exs").write_text(self.CONFIG)
        # A vendored lock must never be read: deps/ is pruned during the walk.
        (app / "deps" / "x").mkdir(parents=True)
        (app / "deps" / "x" / "mix.lock").write_text(
            '%{\n  "vendored": {:hex, :vendored, "0.0.1", "a", [:mix], [], "hexpm", "b"},\n}\n')
        self.requests = []
        self.delay = 0
        self.down = False
        self.latest = {"/api/packages/plug": self.hex_doc("1.20.3"),
                       "/api/packages/jason": self.hex_doc("1.4.9"),
                       "/esbuild": self.npm_doc("0.28.2"),
                       self.GITHUB: self.github_doc("4.3.3")}
        self.url = self.serve()

    def serve(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        import threading
        test = self

        class Registry(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def answer(self, body):
                time.sleep(test.delay)
                found = body is not None and not test.down
                data = json.dumps(body if found else {}).encode()
                self.send_response(200 if found else 404)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                test.requests.append(("GET", self.path, ""))
                if self.path.startswith("/v1/vulns/"):
                    return self.answer(test.VULNS.get(self.path.rsplit("/", 1)[1]))
                self.answer(test.latest.get(self.path))

            def do_POST(self):
                raw = self.rfile.read(int(self.headers["Content-Length"])).decode()
                test.requests.append(("POST", self.path, raw))
                results = []
                for q in json.loads(raw)["queries"]:
                    key = (q["package"]["ecosystem"], q["package"]["name"], q["version"])
                    ids = test.ADVISORIES.get(key, [])
                    results.append({"vulns": [{"id": i, "modified": "m1"} for i in ids]}
                                   if ids else {})
                self.answer({"results": results})

        server = ThreadingHTTPServer(("127.0.0.1", 0), Registry)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return "http://127.0.0.1:%d" % server.server_address[1]

    def env(self, url=None, **extra):
        url = url or self.url
        # TMPDIR holds the once-per-session markers of the edit note.
        env = {"SCRY_PINS_CACHE_DIR": str(self.cache), "TMPDIR": str(self.tmp / "tmp")}
        env.update({"SCRY_PINS_%s_URL" % k: url
                    for k in ("OSV", "HEX", "NPM", "PYPI", "GITHUB")})
        env.update(extra)
        return env

    def refresh(self, again=False, **extra):
        if again:  # an answer under a day old is not asked for again
            (self.cache / "pins.json").unlink()
        env = os.environ.copy()
        env.update(self.env(**extra))
        subprocess.run([sys.executable, str(ROOT / "scanners/pins.py"),
                        "--root", str(self.repo), "--refresh"], check=True, env=env)

    def startup(self, source="startup", **extra):
        """What a session start puts in the session's context."""
        payload = {"hook_event_name": "SessionStart", "source": source} if source else {}
        result = run_hook("pins.sh", self.repo, payload, self.env(**extra))
        self.assertEqual(result.stderr, "")
        return context(result)

    def full(self, **extra):
        env = os.environ.copy()
        env.update(self.env(**extra))
        return subprocess.run(["bash", str(ROOT / "pins.sh"), "--full"], cwd=self.repo,
                              text=True, capture_output=True, check=True, env=env).stdout

    def edit(self, path, session="sess-1", tool="Edit", **extra):
        payload = {"hook_event_name": "PostToolUse", "tool_name": tool,
                   "session_id": session, "cwd": str(self.repo),
                   "tool_input": {"file_path": str(self.repo / path)}}
        return context(run_hook("pins_edit_advisory.sh", self.repo, payload,
                                self.env(**extra)))

    def bar(self, **extra):
        env = self.env(SCRY_CACHE_STATE_DIR=str(self.tmp / "state"),
                       SCRY_CLAUDE_PROJECTS=str(self.tmp / "projects"), **extra)
        payload = {"session_id": "sess-1", "cwd": str(self.repo),
                   "workspace": {"current_dir": str(self.repo)},
                   "model": {"display_name": "Fable 5.1"}}
        out = run_hook("statusline.sh", self.repo, payload, env).stdout
        return re.sub(r"\x1b\[[0-9;]*m", "", out)

    def git(self, *args):
        env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
        subprocess.run(["git", "-C", str(self.repo), *args], check=True,
                       capture_output=True, env=env)

    def wait_for_cache(self, seconds=15):
        deadline = time.time() + seconds
        path = self.cache / "pins.json"
        while time.time() < deadline:
            if path.exists():
                return json.loads(path.read_text())
            time.sleep(0.05)
        self.fail("the background refresh never wrote a cache")

    # ── the hook never waits, and never reaches the network itself ────────

    def test_session_start_does_not_wait_on_an_unreachable_network(self):
        """No cache and nothing listening: the hook is silent and returns at
        once; the lookup fails by itself in a detached process."""
        started = time.time()
        result = run_hook("pins.sh", self.repo, {}, self.env(url="http://127.0.0.1:1"))
        self.assertLess(time.time() - started, 3)
        self.assertEqual(result.stdout.strip(), "")
        cache = self.wait_for_cache()
        self.assertIn("fail_since", cache["meta"])
        self.assertEqual(cache["adv"], {})
        self.assertEqual(self.requests, [])

    def test_hook_returns_before_a_slow_registry_answers(self):
        self.delay = 1.0
        started = time.time()
        result = run_hook("pins.sh", self.repo, {}, self.env())
        self.assertLess(time.time() - started, 1.0)
        self.assertEqual(result.stdout.strip(), "")
        self.assertTrue(self.wait_for_cache()["adv"])
        # The attempt is on record, so the next session start does not spawn
        # a second refresh on top of it.
        seen = len(self.requests)
        self.delay = 0
        self.startup()
        time.sleep(0.5)
        self.assertEqual(len(self.requests), seen)

    # ── startup: silent, or one line said once ────────────────────────────

    def test_startup_is_silent_when_no_runtime_advisory_is_high(self):
        """A low advisory, a config pin two minors behind and a package two
        minors behind are all still true -- and none of them is said."""
        self.ADVISORIES = {("Hex", "mime", "2.0.0"): ["GHSA-dddd-eeee-ffff"]}
        self.refresh()
        result = run_hook("pins.sh", self.repo, {"source": "startup"}, self.env())
        self.assertEqual(result.stdout, "")
        self.assertIn("tailwind 4.1.12", self.full())   # not lost, just not pushed

    def test_startup_says_one_line_once_per_advisory_set(self):
        self.refresh()
        line = self.startup()
        self.assertEqual(len(line.splitlines()), 1)
        self.assertLessEqual(len(line), 200)
        self.assertIn("1 runtime dependency has a high or critical advisory (plug)", line)
        self.assertIn("pins.sh --full", line)
        for absent in ("mime", "tailwind", "jason", "behind"):
            self.assertNotIn(absent, line)       # low, or merely out of date
        # Said. Every later session start in this repository is silent...
        self.assertEqual(self.startup(), "")
        self.assertEqual(self.startup(), "")
        # ...in every worktree of it: the record is the repository's.
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "init")
        other = self.tmp / "other-worktree"
        self.git("worktree", "add", "-q", str(other), "-b", "side")
        self.assertEqual(context(run_hook("pins.sh", other, {}, self.env())), "")
        # ...until an advisory appears that was not in what was said...
        self.ADVISORIES = dict(self.ADVISORIES)
        self.ADVISORIES[("Hex", "jason", "1.4.4")] = ["GHSA-crit-crit-crit"]
        self.refresh(again=True)
        line = self.startup()
        self.assertIn("2 runtime dependencies have a high or critical advisory", line)
        self.assertIn("jason", line)
        self.assertEqual(self.startup(), "")
        # ...or a day passes (the default). One going away is not news.
        del self.ADVISORIES[("Hex", "jason", "1.4.4")]
        self.refresh(again=True)
        self.assertEqual(self.startup(), "")
        said = next(self.cache.glob("said-*.json"))
        record = json.loads(said.read_text())
        self.assertEqual(record["ids"], ["CVE-2026-1", "GHSA-crit-crit-crit"])
        record["at"] = time.time() - 23 * 3600
        said.write_text(json.dumps(record))
        self.assertEqual(self.startup(), "")
        record["at"] = time.time() - 25 * 3600
        said.write_text(json.dumps(record))
        self.assertIn("1 runtime dependency has", self.startup())
        self.assertEqual(self.startup(), "")
        self.assertIn("plug", self.startup(SCRY_PINS_REMIND_DAYS="0"))

    def test_the_line_stays_short_however_many_there_are(self):
        names = ["a_rather_long_package_name_%02d" % i for i in range(12)]
        lock = "%{\n" + "".join(
            '  "%s": {:hex, :%s, "1.0.0", "aa", [:mix], [], "hexpm", "bb"},\n' % (n, n)
            for n in names) + "}\n"
        (self.repo / "apps/engine/mix.lock").write_text(lock)
        (self.repo / "apps/engine/mix.exs").write_text(
            "defp deps do\n  [%s]\nend\n" % ", ".join('{:%s, "~> 1.0"}' % n for n in names))
        self.ADVISORIES = {("Hex", n, "1.0.0"): ["GHSA-high-high-high"] for n in names}
        self.refresh()
        line = self.startup()
        self.assertIn("12 runtime dependencies have", line)
        self.assertEqual(len(line.splitlines()), 1)
        # The cap is on the sentence; the command after it is a path.
        self.assertLessEqual(len(line.split(" Full list: ")[0]), 160)
        self.assertLessEqual(line.count("a_rather_long_package_name"), 3)
        self.assertIn("more)", line)

    def test_only_a_startup_speaks(self):
        self.refresh()
        for source in ("resume", "clear", "compact"):
            self.assertEqual(self.startup(source=source), "", source)
        # None of those used up the one time it is said.
        self.assertIn("plug", self.startup(source="startup"))
        groups = json.loads((ROOT / "hooks/hooks.json").read_text())["hooks"]["SessionStart"]
        ours = [g for g in groups if any(h["command"].endswith("/pins.sh") for h in g["hooks"])]
        self.assertEqual([g.get("matcher") for g in ours], ["startup"])
        # Alone in that group, so the matcher narrows nothing else.
        self.assertEqual(len(ours[0]["hooks"]), 1)

    def test_nothing_at_commit_stop_or_session_end(self):
        hooks = json.loads((ROOT / "hooks/hooks.json").read_text())["hooks"]
        where = {event for event, groups in hooks.items() for g in groups
                 for h in g["hooks"] if "pins" in h["command"]}
        self.assertEqual(where, {"SessionStart", "PostToolUse"})

    # ── runtime or dev ────────────────────────────────────────────────────

    def test_a_dev_only_dependency_never_triggers_the_startup_line(self):
        app = self.repo / "apps/engine"
        (app / "mix.exs").write_text(
            'defp deps do\n  [{:plug, "~> 1.18"},\n'
            '   # {:credo, "~> 1.7"},\n'
            '   {:credo, "~> 1.7", only: [:dev, :test], runtime: false},\n'
            '   {:bypass, "~> 2.1", only: :test},\n'
            '   {:tidewave, "~> 0.4", only: [:dev]}]\nend\n')
        hexdep = '{:%s, "~> 1.0", [hex: :%s, repo: "hexpm", optional: false]}'
        line = '  "%s": {:hex, :%s, "1.0.0", "aa", [:mix], [%s], "hexpm", "bb"},\n'
        (app / "mix.lock").write_text(
            "%{\n"
            + line % ("plug", "plug", hexdep % ("mime", "mime"))
            + line % ("mime", "mime", "")
            + line % ("credo", "credo", hexdep % ("bunt", "bunt"))
            + line % ("bunt", "bunt", "")              # reached only from credo
            + line % ("bypass", "bypass", hexdep % ("mime", "mime"))  # mime: both
            + line % ("tidewave", "tidewave", "")
            + line % ("orphan", "orphan", "")          # nothing declares it
            + "}\n")
        (self.repo / "package-lock.json").write_text(json.dumps({
            "lockfileVersion": 3, "packages": {
                "": {"dependencies": {"left": "^1"}, "devDependencies": {"vitest": "^4"}},
                "node_modules/vitest": {
                    "version": "4.1.0", "dev": True,
                    "resolved": "https://registry.npmjs.org/vitest/-/vitest-4.1.0.tgz"},
                "node_modules/vite": {     # pulled in by vitest alone
                    "version": "7.0.0", "dev": True,
                    "resolved": "https://registry.npmjs.org/vite/-/vite-7.0.0.tgz"}}}))
        (self.repo / "requirements-dev.txt").write_text("pytest==9.0.0\n")
        high = ["GHSA-high-high-high"]
        self.ADVISORIES = {("Hex", n, "1.0.0"): high
                           for n in ("credo", "bunt", "bypass", "tidewave")}
        self.ADVISORIES.update({("npm", "vitest", "4.1.0"): high,
                                ("npm", "vite", "7.0.0"): high,
                                ("PyPI", "pytest", "9.0.0"): high})
        self.refresh()
        self.assertEqual(self.startup(), "")
        self.assertNotIn("pins", self.bar())
        full = self.full()
        self.assertIn("Dev/test-only dependencies with a published security advisory: "
                      "7 (7 high)", full)
        self.assertNotIn("Runtime dependencies with", full)
        self.assertIn("- bunt 1.0.0 (apps/engine/mix.lock) [dev]: high", full)
        self.assertIn("- pytest 9.0.0 (requirements-dev.txt) [dev]: high", full)

        # The same flaw in what ships is said. mime is reached from plug as
        # well as from a test dependency; an undeclared lock line cannot be
        # told, so it counts; a plain requirements.txt is runtime.
        self.ADVISORIES.update({("Hex", "mime", "1.0.0"): high,
                                ("Hex", "orphan", "1.0.0"): high,
                                ("PyPI", "fastapi", "0.141.1"): high})
        (self.repo / "requirements.txt").write_text("fastapi==0.141.1\n")
        self.refresh(again=True)
        line = self.startup()
        self.assertIn("3 runtime dependencies have", line)
        for name in ("mime", "orphan", "fastapi"):
            self.assertIn(name, line)
        for name in ("credo", "bunt", "bypass", "tidewave", "vitest", "vite", "pytest"):
            self.assertNotIn(name, line)
        self.assertIn("pins 3!", self.bar())

    def test_an_npm_runtime_dependency_is_said_and_its_dev_twin_is_not(self):
        def lock(dev):
            entry = {"version": "3.0.3",
                     "resolved": "https://registry.npmjs.org/braces/-/braces-3.0.3.tgz"}
            if dev:
                entry["dev"] = True
            return json.dumps({"lockfileVersion": 3, "packages": {
                "": {"dependencies": {}}, "node_modules/braces": entry}})
        self.ADVISORIES = {("npm", "braces", "3.0.3"): ["GHSA-crit-crit-crit"]}
        (self.repo / "package-lock.json").write_text(lock(dev=True))
        self.refresh()
        self.assertEqual(self.startup(), "")
        (self.repo / "package-lock.json").write_text(lock(dev=False))
        self.assertIn("(braces)", self.startup())

    def test_a_pin_this_branch_changes_is_not_said_to_it(self):
        lock = self.repo / "apps/engine/mix.lock"
        self.ADVISORIES = dict(self.ADVISORIES)
        self.ADVISORIES[("Hex", "plug", "1.18.2")] = ["GHSA-high-high-high"]
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "init")
        self.git("checkout", "-q", "-b", "bump-plug")
        every = {"SCRY_PINS_REMIND_DAYS": "0"}   # take say-once out of the way
        # Uncommitted, the bump is already this branch's change.
        lock.write_text(lock.read_text().replace('"1.18.1"', '"1.18.2"'))
        self.refresh()
        self.assertIn("plug 1.18.2", self.full())           # still a finding
        self.assertEqual(self.startup(**every), "")
        self.git("commit", "-q", "-am", "bump plug")
        self.assertEqual(self.startup(**every), "")
        # A branch that touches something else hears about plug.
        self.git("checkout", "-q", "-b", "other-work", "main")
        (self.repo / "notes.txt").write_text("x")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "other")
        self.refresh()                     # plug 1.18.1 is back, and unasked
        self.assertIn("(plug)", self.startup(**every))
        self.git("checkout", "-q", "main")
        self.assertIn("(plug)", self.startup(**every))

    def test_an_advisory_with_a_vector_and_no_label_is_no_longer_unrated(self):
        """Most Hex advisories: a CVSS 4.0 vector, no severity label, and a
        GHSA alias OSV does not hold. Unscored they were "unrated", and never
        reached the startup line or the bar (2026-10-11)."""
        critical = "CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N"
        medium = "CVSS:4.0/AV:N/AC:L/AT:N/PR:H/UI:N/VC:L/VI:L/VA:N/SC:N/SI:N/SA:N"
        self.VULNS = dict(self.VULNS)
        self.VULNS.update({
            "EEF-CVE-2026-9": {"aliases": ["CVE-2026-9", "GHSA-gone-gone-gone"],
                               "severity": [{"type": "CVSS_V4", "score": critical}],
                               "affected": []},
            "EEF-CVE-2026-8": {"aliases": ["CVE-2026-8"], "affected": [],
                               "severity": [{"type": "CVSS_V4", "score": medium}]},
            # No label and no vector that can be read: the alias is still asked.
            "EEF-CVE-2026-7": {"aliases": ["CVE-2026-7", "GHSA-high-high-high"],
                               "severity": [{"type": "CVSS_V4", "score": "CVSS:4.0/?"}],
                               "affected": []},
            "EEF-CVE-2026-6": {"aliases": ["CVE-2026-6"], "affected": []},
        })
        self.ADVISORIES = {("Hex", "jason", "1.4.4"): ["EEF-CVE-2026-9"],
                           ("Hex", "mime", "2.0.0"): ["EEF-CVE-2026-8"],
                           ("Hex", "plug", "1.18.1"): ["EEF-CVE-2026-7", "EEF-CVE-2026-6"]}
        self.refresh()
        full = self.full()
        self.assertIn("Runtime dependencies with a published security advisory: "
                      "3 (1 critical, 1 high, 1 moderate)", full)
        self.assertIn("- jason 1.4.4 (apps/engine/mix.lock): critical CVE-2026-9", full)
        self.assertIn("- mime 2.0.0 (apps/engine/mix.lock): moderate CVE-2026-8", full)
        self.assertIn("- plug 1.18.1 (apps/engine/mix.lock): 2 advisories, "
                      "worst high CVE-2026-7", full)
        self.assertNotIn("unrated", full.replace("unrated CVE-2026-6", ""))
        line = self.startup()
        self.assertIn("2 runtime dependencies have a high or critical advisory", line)
        self.assertIn("jason", line)
        self.assertIn("pins 2!", self.bar())
        asked = [path for _, path, _ in self.requests]
        # A record rated from its own vector is not looked up a second way.
        self.assertNotIn("/v1/vulns/GHSA-gone-gone-gone", asked)
        self.assertIn("/v1/vulns/GHSA-high-high-high", asked)

    def test_a_cache_from_before_vectors_were_scored_is_asked_again(self):
        self.refresh()
        path = self.cache / "pins.json"
        cache = json.loads(path.read_text())
        self.assertEqual(cache["schema"], 3)
        cache["schema"] = 2                       # digests with `sev: 0` in them
        for record in cache["vuln"].values():
            record["sev"] = 0
        path.write_text(json.dumps(cache))
        self.assertNotIn("high", self.full())     # none of it is believed
        seen = len(self.requests)
        self.refresh()
        self.assertGreater(len(self.requests), seen)
        self.assertIn("plug 1.18.1 (apps/engine/mix.lock): high CVE-2026-1", self.full())

    # ── cooldown ──────────────────────────────────────────────────────────

    def test_a_release_counts_only_once_it_has_been_out_for_the_cooldown(self):
        fresh = self.days_ago(2)
        self.latest["/api/packages/plug"] = self.hex_doc(
            ("1.20.3", fresh), ("1.20.2", self.days_ago(4)), "1.19.0", "1.18.1")
        # jason's only newer release is two days old: it is not behind yet.
        self.latest["/api/packages/jason"] = self.hex_doc(("1.6.0", fresh), "1.4.4")
        self.latest[self.GITHUB] = (self.github_doc("4.3.3", fresh)
                                    + self.github_doc("4.2.0"))
        self.refresh()
        full = self.full()
        self.assertIn("- plug 1.18.1 → 1.19.0 (apps/engine/mix.lock)", full)
        self.assertNotIn("→ 1.20", full)
        self.assertNotIn("jason 1.4.4 →", full)
        self.assertIn("latest release 4.2.0 (1 minor behind)", full)
        self.assertIn("A release counts once it has been published 5 day(s)", full)
        self.assertIn("Newer releases not counted yet", full)
        for held in ("jason 1.6.0 (2 days ago)", "plug 1.20.3 (2 days ago)",
                     "tailwind 4.3.3 (2 days ago)"):
            self.assertIn(held, full)
        # An advisory is exempt: the fix is named however new it is.
        self.assertIn("plug 1.18.1 (apps/engine/mix.lock): high CVE-2026-1, "
                      "fixed in 1.18.5", full)
        # The dates are in the cache, so the setting takes effect with no lookup.
        self.down = True
        self.assertIn("plug 1.18.1 → 1.20.2", self.full(SCRY_PINS_COOLDOWN_DAYS="3"))
        now = self.full(SCRY_PINS_COOLDOWN_DAYS="0")
        self.assertIn("plug 1.18.1 → 1.20.3", now)
        self.assertIn("jason 1.4.4 → 1.6.0", now)
        self.assertNotIn("not counted yet", now)

    # ── the note on an edited manifest ────────────────────────────────────

    def test_editing_a_manifest_says_that_files_findings_once_per_session(self):
        (self.repo / "requirements.txt").write_text("fastapi==0.141.1\n")
        (self.repo / ".tool-versions").write_text("elixir 1.19.0\n")
        (self.repo / "apps/engine/lib").mkdir()
        (self.repo / "apps/engine/lib/app.ex").write_text("defmodule App do\nend\n")
        self.latest["/pypi/fastapi/json"] = {
            "info": {"version": "0.141.1"},
            "releases": {"0.141.1": [{"upload_time_iso_8601": self.OLD}]}}
        self.refresh()
        seen = len(self.requests)
        self.down = True

        note = self.edit("apps/engine/mix.lock")
        self.assertEqual(len(note.splitlines()), 1)
        self.assertIn("Pinned versions in apps/engine/mix.lock, from Scry's local cache", note)
        self.assertIn("2 with a published security advisory (1 high, 1 low): "
                      "plug 1.18.1: high CVE-2026-1, fixed in 1.18.5; "
                      "mime 2.0.0: low GHSA-dddd-eeee-ffff.", note)
        self.assertIn("1 behind the latest release: plug 1.18.1 → 1.20.3.", note)
        self.assertIn("pins.sh --full", note)
        self.assertNotIn("tailwind", note)             # another file's finding
        # Once per session per file; Write counts as the same edit.
        self.assertEqual(self.edit("apps/engine/mix.lock"), "")
        self.assertEqual(self.edit("apps/engine/mix.lock", tool="Write"), "")
        self.assertIn("plug 1.18.1", self.edit("apps/engine/mix.lock", session="sess-2"))

        # mix.exs answers for what IT declares, through the lock beside it.
        note = self.edit("apps/engine/mix.exs")
        self.assertIn("apps/engine/mix.exs (as locked in apps/engine/mix.lock)", note)
        self.assertIn("1 with a published security advisory (1 high): plug 1.18.1", note)
        self.assertNotIn("mime", note)                 # nothing mix.exs declares
        note = self.edit("apps/engine/config/config.exs")
        self.assertIn("1 behind the latest release: tailwind 4.1.12 → 4.3.3.", note)
        self.assertNotIn("plug", note)

        # Silent: a manifest with nothing found, a file Scry reads no pins
        # from, a source file, and a tool that is not an edit.
        self.assertEqual(self.edit("requirements.txt"), "")
        self.assertEqual(self.edit(".tool-versions"), "")
        self.assertEqual(self.edit("apps/engine/lib/app.ex"), "")
        self.assertEqual(self.edit("apps/engine/mix.lock", session="s3", tool="Read"), "")
        self.assertEqual(self.edit("apps/engine/mix.lock", session="s4", SCRY_PINS="0"), "")
        # No lookup, and none started: the registry heard nothing at all.
        time.sleep(0.5)
        self.assertEqual(len(self.requests), seen)

        groups = json.loads((ROOT / "hooks/hooks.json").read_text())["hooks"]["PostToolUse"]
        matchers = [g.get("matcher") for g in groups
                    if any("pins_edit_advisory.sh" in h["command"] for h in g["hooks"])]
        self.assertEqual(matchers, ["Edit|Write"])

    def test_the_edit_note_reads_the_file_as_it_now_is(self):
        self.refresh()
        lock = self.repo / "apps/engine/mix.lock"
        lock.write_text(lock.read_text().replace('"1.18.1"', '"1.20.3"')
                        .replace('"2.0.0"', '"2.0.9"'))
        self.assertIn("pins 1!", self.bar())           # the bar before the edit
        # The bumped versions have no answer in the cache: nothing is claimed.
        self.assertEqual(self.edit("apps/engine/mix.lock"), "")
        self.assertNotIn("pins", self.bar())           # ...and the bar follows

    def test_package_json_answers_through_its_lockfile(self):
        (self.repo / "web").mkdir()
        (self.repo / "web/package.json").write_text(json.dumps(
            {"dependencies": {"braces": "^3"}, "devDependencies": {"vitest": "^4"}}))
        (self.repo / "web/package-lock.json").write_text(json.dumps({
            "lockfileVersion": 3, "packages": {
                "": {"dependencies": {"braces": "^3"}, "devDependencies": {"vitest": "^4"}},
                "node_modules/braces": {
                    "version": "3.0.3",
                    "resolved": "https://registry.npmjs.org/braces/-/braces-3.0.3.tgz"},
                "node_modules/deep": {
                    "version": "1.0.0",
                    "resolved": "https://registry.npmjs.org/deep/-/deep-1.0.0.tgz"},
                "node_modules/vitest": {
                    "version": "4.1.0", "dev": True,
                    "resolved": "https://registry.npmjs.org/vitest/-/vitest-4.1.0.tgz"}}}))
        self.latest.update({"/vitest": self.npm_doc("5.0.3"), "/braces": self.npm_doc("3.0.3")})
        self.ADVISORIES = {("npm", "braces", "3.0.3"): ["GHSA-high-high-high"],
                           ("npm", "deep", "1.0.0"): ["GHSA-high-high-high"]}
        self.refresh()
        note = self.edit("web/package.json")
        self.assertIn("web/package.json (as locked in web/package-lock.json)", note)
        self.assertIn("1 with a published security advisory (1 high): braces 3.0.3", note)
        self.assertIn("1 behind the latest release: vitest 4.1.0 → 5.0.3.", note)
        self.assertNotIn("deep", note)
        self.assertIn("deep 1.0.0", self.edit("web/package-lock.json"))

    # ── the status line ───────────────────────────────────────────────────

    def test_status_line_shows_the_count_from_the_cache_alone(self):
        # No cache directory at all: no segment, and the bar is whole.
        bar = self.bar()
        self.assertNotIn("pins", bar)
        self.assertIn("Fable 5.1", bar)
        self.assertFalse(self.cache.exists())          # the bar created nothing
        self.refresh()
        seen = len(self.requests)
        self.down = True
        bar = self.bar()
        self.assertIn("pins 1!", bar)                  # plug; mime is low
        self.assertLess(bar.index("pins 1!"), bar.index("Fable 5.1"))
        self.assertEqual(len(self.requests), seen)     # and asked nobody
        self.assertNotIn("pins", self.bar(SCRY_PINS="0"))
        # Another repository has its own summary, or none.
        elsewhere = self.tmp / "elsewhere"
        subprocess.run(["git", "init", "-q", "-b", "main", str(elsewhere)], check=True)
        out = run_hook("statusline.sh", elsewhere,
                       {"cwd": str(elsewhere), "model": {"display_name": "Fable 5.1"}},
                       self.env(SCRY_CACHE_STATE_DIR=str(self.tmp / "state"),
                                SCRY_CLAUDE_PROJECTS=str(self.tmp / "projects"))).stdout
        self.assertNotIn("pins", out)
        # A summary nobody can read costs the bar nothing.
        next(self.cache.glob("summary-*.json")).write_text("{not json")
        self.assertIn("Fable 5.1", self.bar())
        self.assertNotIn("pins", self.bar())

    def test_status_line_is_clean_when_nothing_is_high(self):
        self.ADVISORIES = {("Hex", "mime", "2.0.0"): ["GHSA-dddd-eeee-ffff"]}
        self.refresh()
        self.assertNotIn("pins", self.bar())

    # ── the full list ─────────────────────────────────────────────────────

    def test_full_lists_advisories_config_pins_and_packages_behind(self):
        self.refresh()
        report = self.full()
        # One flaw published under three ids is one advisory, rated by the
        # record that carries a label, with the fix for THIS version's line.
        self.assertIn("Runtime dependencies with a published security advisory: "
                      "2 (1 high, 1 low)", report)
        self.assertIn("- plug 1.18.1 (apps/engine/mix.lock): high CVE-2026-1, "
                      "fixed in 1.18.5", report)
        self.assertIn("- mime 2.0.0 (apps/engine/mix.lock): low GHSA-dddd-eeee-ffff", report)
        self.assertIn("- tailwind 4.1.12 in apps/engine/config/config.exs, "
                      "latest release 4.3.3 (2 minor behind)", report)
        self.assertIn("no outdated or audit command reads it", report)
        self.assertNotIn("esbuild", report)       # pinned at the latest
        self.assertNotIn("9.9.9", report)         # some other app's version key
        self.assertIn("Behind the latest release: 1 of 2 directly declared package(s)", report)
        self.assertIn("- plug 1.18.1 → 1.20.3", report)
        self.assertNotIn("jason", report)         # a patch behind is not far
        self.assertNotIn("Dev/test-only", report)
        self.assertNotIn("hookSpecificOutput", report)   # plain text, for a person

    def test_full_is_never_empty_and_says_what_it_does_not_know(self):
        report = self.full()                      # before anything was looked up
        self.assertIn("holds no answer for this repo yet", report)
        self.assertIn("lookup(s) for this repo have no answer", report)
        self.assertIn("pins.sh --refresh", report)
        self.assertEqual(self.requests, [])       # asking for the list asks nobody
        self.ADVISORIES = {}
        self.latest.update({"/api/packages/plug": self.hex_doc("1.18.9"),
                            self.GITHUB: self.github_doc("4.1.14")})
        self.refresh()
        self.assertIn("No published advisory, and nothing behind the threshold",
                      self.full())
        self.assertEqual(self.startup(), "")

    def test_sends_only_public_package_names_and_versions(self):
        (self.repo / "requirements.txt").write_text(
            "--index-url https://user:s3cret@pypi.internal.test/simple\n"
            "fastapi==0.141.1  # pinned\nuvicorn[standard]>=0.50\n"
            "private @ git+https://tok3n@example.test/private.git\n")
        self.latest["/pypi/fastapi/json"] = {
            "info": {"version": "0.141.1"},
            "releases": {"0.141.1": [{"upload_time_iso_8601": self.OLD}]}}
        self.refresh()
        sent = json.dumps(self.requests)
        for absent in ("oban_pro", "local_fork", "vendored", "s3cret", "tok3n",
                       "uvicorn", "private", str(self.repo)):
            self.assertNotIn(absent, sent)
        paths = [path for _, path, _ in self.requests]
        self.assertIn("/pypi/fastapi/json", paths)
        # Advisories for every locked version; a latest-release lookup only
        # for what the project declares itself.
        self.assertIn('"name": "mime"', self.requests[0][2])
        self.assertNotIn("/api/packages/mime", paths)

    def test_threshold_and_switches(self):
        self.ADVISORIES = {}
        self.refresh()
        self.assertIn("tailwind 4.1.12", self.full())
        self.assertNotIn("tailwind", self.full(SCRY_PINS_BEHIND_MINORS="3"))
        self.assertNotIn("tailwind", self.full(SCRY_PINS_BEHIND="0"))
        self.assertEqual(self.full(SCRY_PINS="0"), "")
        self.assertIn("jason 1.4.4 → 1.4.9", self.full(SCRY_PINS_BEHIND_MINORS="0"))
        self.latest["/api/packages/plug"] = self.hex_doc("2.0.0")
        self.refresh(again=True)
        self.assertIn("1 of them a major version",
                      self.full(SCRY_PINS_BEHIND_MINORS="99"))

    def test_a_bumped_pin_stops_being_reported_without_a_lookup(self):
        self.ADVISORIES = {}
        self.latest["/api/packages/plug"] = self.hex_doc("1.18.1")
        self.refresh()
        self.assertIn("tailwind 4.1.12", self.full())
        config = self.repo / "apps/engine/config/config.exs"
        config.write_text(config.read_text().replace("4.1.12", "4.3.3"))
        self.down = True
        self.assertNotIn("tailwind", self.full())

    def test_github_refusing_falls_back_to_the_npm_release(self):
        del self.latest[self.GITHUB]
        self.latest["/tailwindcss"] = self.npm_doc("4.3.3")
        self.refresh()
        self.assertIn("latest release 4.3.3", self.full())

    def test_an_npm_packument_that_will_not_come_still_names_the_latest(self):
        """The packument has the dates and is sometimes megabytes; `latest`
        is small. Without a date the release cannot be held back."""
        del self.latest[self.GITHUB]
        self.latest["/tailwindcss"] = "cut short"
        self.latest["/tailwindcss/latest"] = {"version": "4.3.3"}
        self.refresh()
        self.assertIn("latest release 4.3.3", self.full())

    def test_old_data_is_labelled_and_a_dark_check_is_not_silent(self):
        self.refresh()
        path = self.cache / "pins.json"
        cache = json.loads(path.read_text())
        old = time.time() - 9 * 86400
        for section in ("adv", "latest", "vuln"):
            for entry in cache[section].values():
                entry["at"] = old
        cache["meta"] = {"fail_since": old}
        path.write_text(json.dumps(cache))
        dead = "http://127.0.0.1:1"
        self.assertIn("LAST CHECKED 9 DAYS AGO", self.full(url=dead))
        # Nothing flagged, and the lookups have failed for over a week:
        # silence everywhere would claim a check that did not happen. The
        # session is still not told at startup; the bar and the list say so.
        for entry in cache["adv"].values():
            entry["ids"] = []
        for entry in cache["latest"].values():
            entry["v"], entry["rel"] = None, []
        path.write_text(json.dumps(cache))
        report = self.full(url=dead)
        self.assertIn("NOT CHECKED", report)
        self.assertIn("not a finding that the rest are clean", report)
        self.assertIn("pins ?", self.bar(url=dead))
        self.assertEqual(self.startup(url=dead), "")
        self.assertNotIn("NOT CHECKED", self.full(url=dead, SCRY_PINS_STALE_DAYS="30"))
        self.assertNotIn("pins", self.bar(url=dead, SCRY_PINS_STALE_DAYS="30"))

    def test_npm_lock_and_non_repositories(self):
        (self.repo / "package-lock.json").write_text(json.dumps({
            "lockfileVersion": 3, "packages": {
                "": {"devDependencies": {"vitest": "^4.0.0"}},
                "node_modules/vitest": {
                    "version": "4.1.0", "dev": True,
                    "resolved": "https://registry.npmjs.org/vitest/-/vitest-4.1.0.tgz"},
                "node_modules/braces": {
                    "version": "3.0.3",
                    "resolved": "https://registry.npmjs.org/braces/-/braces-3.0.3.tgz"},
                "node_modules/inhouse": {
                    "version": "1.0.0",
                    "resolved": "https://npm.internal.test/inhouse-1.0.0.tgz"},
                "node_modules/linked": {"resolved": "packages/linked", "link": True}}}))
        self.latest["/vitest"] = self.npm_doc("5.0.3")
        self.ADVISORIES = {("npm", "braces", "3.0.3"): ["GHSA-dddd-eeee-ffff"]}
        self.refresh()
        report = self.full()
        self.assertIn("braces 3.0.3 (package-lock.json): low GHSA-dddd-eeee-ffff", report)
        self.assertIn("vitest 4.1.0 → 5.0.3 (package-lock.json) [dev]", report)
        sent = json.dumps(self.requests)
        self.assertNotIn("inhouse", sent)
        self.assertNotIn("/braces", sent)
        outside = self.tmp / "not-a-repo"
        outside.mkdir()
        shutil.copy(self.repo / "apps/engine/mix.lock", outside / "mix.lock")
        result = run_hook("pins.sh", outside, {}, self.env())
        self.assertEqual(result.stdout.strip(), "")


class StatuslineSetupTests(unittest.TestCase):
    """statusline_setup.sh: a plugin cannot set `statusLine`, so a fresh
    install has no bar and nothing said why (2026-10-09, a new Mac). The hook
    says so; --install does it when asked. Every test uses a temporary HOME:
    the real settings.json is never read or written."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.home, True)
        self.settings = self.home / ".claude" / "settings.json"
        self.settings.parent.mkdir()
        self.project = self.home / "project"
        (self.project / ".claude").mkdir(parents=True)
        (self.project / ".git").mkdir()

    def env(self, **extra):
        env = os.environ.copy()
        env.pop("SCRY_STATUSLINE_CHECK", None)
        env.update({"HOME": str(self.home), "CLAUDE_CONFIG_DIR": "",
                    "XDG_CACHE_HOME": str(self.home / ".cache"),
                    "CLAUDE_PLUGIN_ROOT": str(ROOT)})
        env.update(extra)
        return env

    def hook(self, **extra):
        result = subprocess.run(
            ["bash", str(ROOT / "statusline_setup.sh")], cwd=self.project,
            input=json.dumps({"cwd": str(self.project)}), text=True,
            capture_output=True, check=True, env=self.env(**extra))
        return context(result)

    def setup(self, *args):
        return subprocess.run(
            ["bash", str(ROOT / "statusline_setup.sh"), *args],
            text=True, capture_output=True, env=self.env())

    def write(self, data, path=None):
        (path or self.settings).write_text(json.dumps(data))

    def read(self):
        return json.loads(self.settings.read_text())

    def test_says_the_bar_is_not_set_up_until_it_is(self):
        self.write({"model": "opus"})
        report = self.hook()
        self.assertIn("not set up", report)
        self.assertIn("a plugin cannot set one", report)
        self.assertIn("statusline_setup.sh --install", report)
        self.assertIn("/scry-statusline", report)
        self.assertEqual(len(report.splitlines()), 1)
        self.assertIn("not set up", self.hook())     # every session, until set up
        self.assertEqual(self.read(), {"model": "opus"})   # the hook wrote nothing
        self.assertEqual(self.setup("--install").returncode, 0)
        self.assertEqual(self.hook(), "")

    def test_no_settings_file_at_all_is_the_new_machine_case(self):
        self.assertIn("not set up", self.hook())
        done = self.setup("--install")
        self.assertEqual(done.returncode, 0, done.stdout)
        self.assertIn("statusline.sh", self.read()["statusLine"]["command"])
        self.assertEqual(self.hook(), "")

    def test_install_adds_the_documented_setting_and_keeps_every_other_key(self):
        before = {"model": "opus", "permissions": {"allow": ["Bash(ls:*)"]},
                  "env": {"A": "1"}, "hooks": {"Stop": []}, "ünï": "çödé"}
        self.write(before)
        done = self.setup("--install")
        self.assertEqual(done.returncode, 0)
        self.assertIn("every other key is unchanged", done.stdout)
        after = self.read()
        line = after.pop("statusLine")
        self.assertEqual(after, before)
        self.assertEqual(list(after), list(before))     # order kept too
        # The setting is the one the README documents, character for character.
        readme = (ROOT / "README.md").read_text()
        documented = json.loads("{" + re.search(
            r'("statusLine": \{.*?\n\})', readme, re.S).group(1) + "}")["statusLine"]
        self.assertEqual(line, documented)
        again = self.setup("--install")
        self.assertIn("Already set", again.stdout)

    def test_a_different_status_line_is_never_overwritten_unasked(self):
        self.write({"statusLine": {"type": "command", "command": "~/bin/mybar.sh"},
                    "model": "opus"})
        refused = self.setup("--install")
        self.assertEqual(refused.returncode, 2)
        self.assertIn("~/bin/mybar.sh", refused.stdout)
        self.assertIn("will not overwrite it unasked", refused.stdout)
        self.assertEqual(self.read()["statusLine"]["command"], "~/bin/mybar.sh")
        replaced = self.setup("--replace")
        self.assertEqual(replaced.returncode, 0)
        self.assertIn("The previous statusLine was:\n  ~/bin/mybar.sh", replaced.stdout)
        self.assertIn("statusline.sh", self.read()["statusLine"]["command"])
        self.assertEqual(self.read()["model"], "opus")
        backup = json.loads(Path(str(self.settings) + ".scry-bak").read_text())
        self.assertEqual(backup["statusLine"]["command"], "~/bin/mybar.sh")

    def test_a_chosen_status_line_is_mentioned_once_and_can_be_silenced(self):
        self.write({"statusLine": {"type": "command", "command": "~/bin/mybar.sh"}})
        first = self.hook()
        self.assertIn("sets a different statusLine", first)
        self.assertIn("Said once", first)
        self.assertEqual(self.hook(), "")
        # A different one is a different choice, and is said once as well.
        self.write({"statusLine": {"type": "command", "command": "~/bin/other.sh"}})
        self.assertIn("different statusLine", self.hook())
        self.assertEqual(self.hook(), "")
        self.write({})
        self.assertEqual(self.hook(SCRY_STATUSLINE_CHECK="0"), "")
        self.assertEqual(self.setup("--silence").returncode, 0)
        self.assertEqual(self.read(), {"env": {"SCRY_STATUSLINE_CHECK": "0"}})
        self.assertEqual(self.hook(), "")

    def test_scry_reached_another_way_counts_as_set_up(self):
        for command in ("bash ~/Developer/scry/statusline.sh",
                        "SCRY_STATUSLINE_INNER='~/bin/mybar.sh' bash "
                        "/opt/plugins/cache_deadline_statusline.sh"):
            self.write({"statusLine": {"type": "command", "command": command}})
            self.assertEqual(self.hook(), "", command)
        # The project's settings decide which status line is in effect.
        self.write({})
        self.write({"statusLine": {"type": "command",
                                   "command": "bash ~/x/scry/statusline.sh"}},
                   self.project / ".claude" / "settings.local.json")
        self.assertEqual(self.hook(), "")

    def test_unreadable_settings_are_left_alone(self):
        self.settings.write_text('{"model": "opus",}  // not JSON\n')
        self.assertEqual(self.hook(), "")
        refused = self.setup("--install")
        self.assertEqual(refused.returncode, 1)
        self.assertIn("could not be read", refused.stdout)
        self.assertEqual(self.settings.read_text(), '{"model": "opus",}  // not JSON\n')

    def test_silent_on_codex_and_registered_as_a_read_only_hook(self):
        self.write({})
        self.assertEqual(self.hook(CLAUDE_PLUGIN_ROOT=""), "")
        hooks = json.loads((ROOT / "hooks/hooks.json").read_text())["hooks"]["SessionStart"]
        commands = [h["command"] for g in hooks for h in g["hooks"]]
        ours = [c for c in commands if "statusline_setup.sh" in c]
        # No argument: the only mode that can write is never what a hook runs.
        self.assertEqual(len(ours), 1)
        self.assertTrue(ours[0].endswith("/statusline_setup.sh"), ours)
        skill = (ROOT / "skills/scry-statusline/SKILL.md").read_text()
        self.assertIn("statusline_setup.sh\" --install", skill)
