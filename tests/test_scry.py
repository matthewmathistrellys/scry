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


if __name__ == "__main__":
    unittest.main()
