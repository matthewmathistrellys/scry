#!/usr/bin/env python3
"""
Ash domain overview — the Elixir/Ash scanner behind architecture.sh.

One of several stack scanners under scanners/; architecture.sh detects
which one applies and dispatches. The language name lives here rather
than on the hook so that a single hook, wired once, works in any repo.

Greps a lib/ tree for `use Ash.Domain` modules and prints one line per
domain: module name, resource count, first sentence of @moduledoc. No
mix/BEAM boot required — pure text parsing, meant to run in well under
a second at session start.

Usage:
    python3 scanners/elixir_ash.py --repo-root .      # discover all projects
    python3 scanners/elixir_ash.py --path lib         # scan one lib/ tree
"""

import argparse
import json
import os
import re

# Directories that never hold a FIRST-PARTY mix project. Pruned during the
# walk rather than filtered after it: a naive repo-wide search of a real
# monorepo found 2,512 mix.exs files, almost all vendored under deps/ --
# which would report Ash's own domains as the app's. Excluding deps/,
# _build/ and node_modules/ left 62; excluding .worktrees/ (~20 checkouts
# of the same 3 projects) left the real 3.
PRUNE_DIRS = {
    ".git", "deps", "_build", "node_modules", ".worktrees", "worktrees",
    ".venv", "venv", "__pycache__", "dist", "build", "target", "cover",
}

MODULE_RE = re.compile(r"^\s*defmodule\s+([\w.]+)\s+do")
MODULEDOC_RE = re.compile(r'@moduledoc\s+"""\s*\n(.*?)\n\s*"""', re.DOTALL)
RESOURCE_RE = re.compile(r"^\s*resource\s+[\w.]+", re.MULTILINE)


def first_sentence(doc: str) -> str:
    doc = doc.strip()
    if not doc:
        return ""
    # Collapse to the first paragraph, then the first sentence within it.
    paragraph = re.sub(r"\s+", " ", doc.split("\n\n", 1)[0]).strip()
    match = re.search(r"^.*?[.!?](?=\s|$)", paragraph)
    return (match.group(0) if match else paragraph).strip()


def find_mix_projects(root: str, limit: int = 12) -> list[str]:
    """Every first-party mix project at or below root, nearest first.

    Prunes rather than filters, so the vendored trees are never entered.
    """
    found = []
    for cur, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in PRUNE_DIRS)
        if "mix.exs" in files:
            found.append(cur)
            # An umbrella's children are projects too, but their domains
            # are reached through apps/ below; keep descending.
        if len(found) >= limit:
            break
    found.sort(key=lambda d: (d.count(os.sep), d))
    return found


def find_domains(base_path: str) -> list[dict]:
    domains = []
    for root, dirs, files in os.walk(base_path):
        dirs[:] = [d for d in dirs if d not in {"_build", "deps"}]
        for file in files:
            if not file.endswith(".ex"):
                continue
            filepath = os.path.join(root, file)
            try:
                with open(filepath, encoding="utf-8") as f:
                    content = f.read()
            except (OSError, UnicodeDecodeError):
                continue

            if "use Ash.Domain" not in content:
                continue

            module_match = MODULE_RE.search(content)
            if not module_match:
                continue

            doc_match = MODULEDOC_RE.search(content)
            description = first_sentence(doc_match.group(1)) if doc_match else ""
            resource_count = len(RESOURCE_RE.findall(content))

            domains.append(
                {
                    "module": module_match.group(1),
                    "resources": resource_count,
                    "description": description,
                }
            )

    domains.sort(key=lambda d: d["module"])
    return domains


def render(domains: list[dict], label: str = "") -> str:
    total = sum(d["resources"] for d in domains)
    lines = [f"Ash domains{label} ({len(domains)}, {total} resources):"]
    for d in domains:
        count = f"{d['resources']} resource" + ("s" if d["resources"] != 1 else "")
        desc = f" — {d['description']}" if d["description"] else ""
        lines.append(f"  {d['module']} ({count}){desc}")
    return "\n".join(lines)


def deps_not_fetched(mix_root: str) -> bool:
    """True when this checkout has never run `mix deps.get` — no deps/ dir.

    A fresh worktree carries mix.exs and lib/ (checked into git) but not
    deps/ or _build/ (both gitignored), so a session that starts compiling
    without noticing hits a bare Mix.Error instead of a clear next step.
    Presence of deps/ is the cheap, reliable signal — it only exists once
    `mix deps.get` has actually run.
    """
    return bool(mix_root) and os.path.isdir(mix_root) and not os.path.isdir(os.path.join(mix_root, "deps"))


def emit(sections: list[str]) -> None:
    """SessionStart hooks must emit this JSON envelope to reach the model as
    context — plain stdout doesn't reliably get there. A prior version of
    this script just print()'d, which meant the domain map went nowhere
    useful even when the hook was wired up correctly.
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
    parser = argparse.ArgumentParser(description="Ash domain overview")
    parser.add_argument("--path", default="lib", help="Base path to scan")
    parser.add_argument(
        "--repo-root",
        default=None,
        help="Discover every mix project at or below this directory",
    )
    parser.add_argument(
        "--mix-root",
        default=None,
        help="Directory containing mix.exs (defaults to the parent of --path)",
    )
    args = parser.parse_args()

    if args.repo_root:
        sections = []
        for project in find_mix_projects(args.repo_root):
            body = []
            if deps_not_fetched(project):
                body.append(
                    "Elixir deps not fetched in this worktree yet (no deps/ directory "
                    f"found under {project}) — run `mix deps.get` before anything here "
                    "compiles."
                )
            domains = find_domains(os.path.join(project, "lib"))
            if domains:
                label = os.path.relpath(project, args.repo_root)
                header = "" if label == "." else f" [{label}]"
                body.append(render(domains, header))
            if body:
                sections.append("\n\n".join(body))
        emit(sections)
        return

    mix_root = args.mix_root or os.path.dirname(os.path.normpath(args.path))

    sections = []
    if deps_not_fetched(mix_root):
        sections.append(
            "Elixir deps not fetched in this worktree yet (no deps/ directory found "
            f"under {mix_root}) — run `mix deps.get` before anything here compiles."
        )

    domains = find_domains(args.path)
    if domains:
        sections.append(render(domains))

    emit(sections)


if __name__ == "__main__":
    main()
