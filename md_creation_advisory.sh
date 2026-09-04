#!/usr/bin/env bash
# md_creation_advisory.sh — PostToolUse hook: answers "does a new Markdown
# file belong in this repo at all?"
#
# Companion to md_advisory.sh (which stamps trust doctrine on every Read of a
# .md file). This hook fires at the other end of a Markdown file's life: the
# moment one is CREATED, before it has a chance to become the clutter the
# read-side hook has to keep warning about.
#
# The fix is not "never write Markdown" — CLAUDE.md, AGENTS.md, a README, a
# CHANGELOG are all legitimate. The fix is naming which of two things a new
# file is BEFORE it exists:
#   1. Ephemeral / session / anything a human wants to watch  -> a Claude
#      Code Artifact, not a repo file. (Claude Code only — see below.)
#   2. Instructions meant to bind future sessions               -> the only
#      case a permanent .md file is actually correct.
# Anything else, including a markdown file standing in for a task tracker or
# a decision log, falls to the closing line: it's scratch, not a third named
# home. A "tracked work -> your task system" bullet was cut deliberately —
# unlike the two above, it named a destination this hook can never verify
# exists, which is the same failure the Codex wording below was fixed for.
#
# Two things are structurally exempt and never see this advisory:
#   - The standard ecosystem files every repo/tool convention already
#     expects (README, LICENSE, CHANGELOG, CLAUDE.md/AGENTS.md, GitHub's
#     community-health files, anything under .claude/). These already have
#     an established, low-drift job. Naming them is the whitelist below.
#   - A repo whose actual PRODUCT is Markdown content — an Astro/Docusaurus/
#     similar content-site generator — where a new .md file under its content
#     tree isn't clutter, it's the work. Detected structurally (a generator
#     config at the repo root), not by filename, because the same filename
#     (index.md) is dangerous in a code repo and normal in a docs-site repo.
#
# "New" means untracked in git, not merely "written this call" — Write also
# fully overwrites files a session already owns, and that isn't creation.
#
# Advisory only: no blocking, no enforcement, just context injection. Fires
# at most once per file per session, same as scale_advisory.sh, so an agent
# iterating on the same scratch file isn't renagged on every draft.
set -uo pipefail

read -r payload

parsed="$(FILE_PAYLOAD="$payload" python3 -c '
import json, os, sys
try:
    d = json.loads(os.environ["FILE_PAYLOAD"])
except (json.JSONDecodeError, KeyError):
    sys.exit(0)
if d.get("tool_name") != "Write":
    sys.exit(0)
fp = (d.get("tool_input") or {}).get("file_path") or ""
if not fp:
    sys.exit(0)
print(fp)
print(d.get("session_id") or "nosession")
print(d.get("transcript_path") or "")
')"

[ -n "$parsed" ] || exit 0
file_path="$(printf '%s' "$parsed" | sed -n 1p)"
session_id="$(printf '%s' "$parsed" | sed -n 2p)"
transcript_path="$(printf '%s' "$parsed" | sed -n 3p)"

# Same client detection fleet.sh already uses: a Codex transcript path
# contains /.codex/, everything else is treated as Claude Code. Matters here
# because "Claude Code Artifact" is a real product feature of one client and
# not the other — Codex CLI has no built-in way to publish a shareable page
# (verified 2026-09-03: OpenAI's "Codex Sites" is a Codex-app feature with no
# documented path from Codex CLI). Saying it unconditionally would be wrong
# for every Codex session this hook fires in.
client="claude"
case "$transcript_path" in
  */.codex/*) client="codex" ;;
esac
# Codex gets no bullet here at all, not even a "no equivalent exists" line —
# quiet by default (scry's own contract): a line that has nothing to tell the
# reader to do differently isn't a signal, it's noise.
if [ "$client" = "codex" ]; then
  EPHEMERAL_BULLET=""
else
  EPHEMERAL_BULLET="— Ephemeral or session work (a plan being drafted, a progress tracker, anything with a graph or a state a human wants to watch, anything that dies when this session ends) belongs in a Claude Code Artifact, not a repo file.
"
fi

case "$file_path" in
  *.md) ;;
  *) exit 0 ;;
esac
[ -f "$file_path" ] || exit 0

repo_root="$(cd "$(dirname "$file_path")" && git rev-parse --show-toplevel 2>/dev/null)" || exit 0
[ -n "$repo_root" ] || exit 0
rel="${file_path#"$repo_root"/}"

# ── Bucket 4: this repo's product IS Markdown content ───────────────────────
# Structural, not filename-based, and repo-wide: a docs-content generator's
# whole job is producing .md/.mdx files, so nothing under it is clutter.
# Starter set — extend the same way when another generator shows up.
for marker in astro.config.mjs astro.config.ts astro.config.js \
              docusaurus.config.js docusaurus.config.ts; do
  [ -f "$repo_root/$marker" ] && exit 0
done
[ -d "$repo_root/.vitepress" ] && exit 0

# ── Bucket 1-3: the standard, ecosystem-recognized files ────────────────────
case "$rel" in
  CLAUDE.md|AGENTS.md|README.md|LICENSE.md|CONTRIBUTING.md| \
  CODE_OF_CONDUCT.md|SECURITY.md|SUPPORT.md|CHANGELOG.md) exit 0 ;;
  .github/PULL_REQUEST_TEMPLATE.md|.github/copilot-instructions.md) exit 0 ;;
  .github/ISSUE_TEMPLATE/*.md) exit 0 ;;
  .claude/*|*/.claude/*) exit 0 ;;
esac

# ── "New" = untracked in git, not merely written this call ──────────────────
git -C "$repo_root" ls-files --error-unmatch -- "$rel" >/dev/null 2>&1 && exit 0

# ── Once per file per session ────────────────────────────────────────────────
state_dir="${TMPDIR:-/tmp}/scry-md-creation-advisory"
mkdir -p "$state_dir" 2>/dev/null || exit 0
seen_key="$(printf '%s|%s' "$session_id" "$file_path" | shasum | cut -d' ' -f1)"
seen_marker="$state_dir/seen-$seen_key"
[ -e "$seen_marker" ] && exit 0
: > "$seen_marker" 2>/dev/null

ADVISORY_TEXT="$(cat <<EOF
NEW MARKDOWN — $rel is not one of this repo's standard Markdown files. Before it exists, check whether it fits one of these:

${EPHEMERAL_BULLET}— Instructions meant to bind future sessions (CLAUDE.md, AGENTS.md) are the only markdown genuinely worth checking in.

A file created for today's convenience is still here in six months, read by a future session as settled fact. If this doesn't fit one of these, it's scratch: write it, use it, delete it before the session ends.
EOF
)"

CTX="$ADVISORY_TEXT" python3 - <<'PY'
import json, os
print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": "PostToolUse",
        "additionalContext": os.environ["CTX"],
    },
    "suppressOutput": True,
}))
PY

exit 0
