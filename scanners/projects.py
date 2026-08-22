#!/usr/bin/env python3
"""
Project resolution — the one place that answers "where is the project?"

Every scanner needs this, and before this module existed every scanner
answered it separately. That duplication was not cosmetic: on 2026-08-22 the
same up-from-cwd bug shipped TWICE in one evening. architecture.sh was fixed
to search DOWN from the repo root (commit ebc4aed) because a monorepo keeps
its mix.exs files in apps/*; an hour later elixir_build_guard.sh was written
from scratch with the identical upward walk and silently failed to guard that
exact layout. Four separate skip-lists had drifted apart by then too.

So: one prune list, one downward search, one upward walk, one `cd` resolver.
A fix here reaches every caller, which is the entire point.

Not a hook. Imported by the scanners and by elixir_build_guard.sh.
"""

import os
import re

# Directories that never contain first-party project config. Pruned DURING
# the walk, never filtered after it -- a naive repo-wide search of a real
# monorepo returned 2,512 mix.exs files, almost all vendored under deps/,
# which would report a dependency's own internals as the application's.
PRUNE_DIRS = {
    ".git", "deps", "_build", "node_modules", ".worktrees", "worktrees",
    ".venv", "venv", "__pycache__", "dist", "build", "target", "cover",
    ".next", "coverage", ".pytest_cache", ".terraform", "vendor",
    "site-packages",
}

_CD_PREFIX_RE = re.compile(
    r"""\s*cd\s+(?:'([^']+)'|"([^"]+)"|([^\s;&|]+))\s*(?:&&|;)"""
)


def prune(dirs: list) -> list:
    """In-place-safe prune helper for os.walk's dirs list."""
    return sorted(d for d in dirs if d not in PRUNE_DIRS)


def find_project_roots(root: str, marker: str, limit: int = 24,
                       max_depth: int = 4) -> list[str]:
    """Every directory at or below `root` containing `marker`, nearest first.

    `limit` bounds the result AFTER sorting rather than truncating the walk
    mid-flight, so the projects returned are the shallowest ones rather than
    whichever the filesystem happened to yield first.
    """
    found = []
    root = os.path.abspath(root)
    for cur, dirs, files in os.walk(root):
        dirs[:] = prune(dirs)
        if cur[len(root):].count(os.sep) > max_depth:
            dirs[:] = []
            continue
        if marker in files:
            found.append(cur)
    found.sort(key=lambda d: (d.count(os.sep), d))
    return found[:limit]


def nearest_root_above(start: str, marker: str) -> str:
    """Walk UP from `start` for `marker`. Returns "" if never found."""
    if not start:
        return ""
    cur = os.path.abspath(start)
    while cur != "/":
        if os.path.isfile(os.path.join(cur, marker)):
            return cur
        cur = os.path.dirname(cur)
    return ""


def git_root(start: str) -> str:
    """The repo root, found without shelling out to git."""
    cur = os.path.abspath(start or ".")
    while cur != "/":
        if os.path.exists(os.path.join(cur, ".git")):
            return cur
        cur = os.path.dirname(cur)
    return ""


def command_target_dir(command: str, cwd: str) -> str:
    """Resolve a leading `cd <path> &&` so a hook sees the real directory.

    Compound commands are the normal shape an agent emits. Ignoring the
    prefix is what let `cd apps/engine && mix compile --force` resolve to a
    repo root with no project marker of its own, and be waved through.
    """
    match = _CD_PREFIX_RE.match(command or "")
    if not match:
        return ""
    target = os.path.expanduser(match.group(1) or match.group(2) or match.group(3))
    if not os.path.isabs(target):
        target = os.path.join(cwd or ".", target)
    return os.path.abspath(target)


def resolve_project(command: str, cwd: str, marker: str) -> str:
    """Best-effort project root for a command, in decreasing confidence.

    1. the directory the command actually cd's into
    2. at or above cwd
    3. anywhere under the git root
    """
    target = command_target_dir(command, cwd)
    if target:
        found = nearest_root_above(target, marker)
        if found:
            return found
    found = nearest_root_above(cwd, marker)
    if found:
        return found
    root = git_root(cwd)
    if root:
        roots = find_project_roots(root, marker, limit=1)
        if roots:
            return roots[0]
    return ""
