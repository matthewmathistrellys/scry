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


if __name__ == "__main__":
    unittest.main()
