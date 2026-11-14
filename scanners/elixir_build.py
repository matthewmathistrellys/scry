#!/usr/bin/env python3
"""
Elixir build state — the Elixir scanner behind architecture.sh.

architecture.sh hands it the repo root; it finds every mix project at or
below it and reports what a session would otherwise discover mid-command:
deps not fetched, and a cold build (the next compile is a full one). It prints nothing when a
project is fetched, built and clean. Pure text and file checks, no BEAM
boot, well under a second.

It says nothing about what the code holds (domains, resources): that is a
description of the work, not of the environment, and a repo's own pages
carry it.

Usage:
    python3 scanners/elixir_build.py --repo-root .
"""

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import projects  # noqa: E402  (path set above so hooks can run from anywhere)



def find_mix_projects(root: str) -> list[str]:
    """Every first-party mix project at or below root, shallowest first."""
    return projects.find_project_roots(root, "mix.exs")


def deps_not_fetched(mix_root: str) -> bool:
    """True when this checkout has never run `mix deps.get` — no deps/ dir.

    A fresh worktree carries mix.exs and lib/ (checked into git) but not
    deps/ or _build/ (both gitignored), so a session that starts compiling
    without noticing hits a bare Mix.Error instead of a clear next step.
    Presence of deps/ is the cheap, reliable signal — it only exists once
    `mix deps.get` has actually run.
    """
    return bool(mix_root) and os.path.isdir(mix_root) and not os.path.isdir(os.path.join(mix_root, "deps"))


def build_is_cold(mix_root: str) -> bool:
    """True when deps are fetched but nothing is compiled yet.

    A cold `_build` does not look like anything from the outside: you run
    `mix test`, it starts compiling every dependency, and twenty minutes are
    gone with no warning that they were about to be. It is the normal state
    of a freshly created worktree, which is exactly where it surprises you.
    Reported alongside the missing-deps check because it is the same fact one
    step further along -- no deps/ means nothing will compile, a cold _build
    means everything will.
    """
    if not mix_root or not os.path.isdir(os.path.join(mix_root, "deps")):
        return False               # deps_not_fetched already covers this
    build = os.path.join(mix_root, "_build")
    try:
        return not os.path.isdir(build) or not os.listdir(build)
    except OSError:
        return False


def emit(sections: list[str]) -> None:
    """SessionStart hooks must emit this JSON envelope to reach the model as
    context — plain stdout doesn't reliably get there. A prior version of
    this script just print()'d, which meant its notes went nowhere useful
    even when the hook was wired up correctly.
    """
    if not sections:
        return
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": "\n\n".join(sections),
        }
    }))


def main():
    parser = argparse.ArgumentParser(description="Elixir build state")
    parser.add_argument("--repo-root", default=".",
                        help="Check every mix project at or below this directory")
    args = parser.parse_args()

    sections = []
    for project in find_mix_projects(args.repo_root):
        body = []
        if deps_not_fetched(project):
            body.append(
                "Elixir deps not fetched in this worktree yet (no deps/ directory "
                f"found under {project}): nothing here compiles until they are."
            )
        if build_is_cold(project):
            body.append(
                f"Elixir build is COLD in {project} (deps fetched, nothing compiled "
                "yet): the next `mix compile`/`mix test` is a FULL build of every "
                "dependency, not an incremental one."
            )
        if body:
            sections.append("\n\n".join(body))
    emit(sections)


if __name__ == "__main__":
    main()
