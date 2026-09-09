#!/usr/bin/env bash
# md_exemptions.sh — the one answer to "is this Markdown file scratch?"
#
# NOT A HOOK. It is sourced, defines two functions, and does nothing on its
# own. Two hooks ask the same question at opposite ends of a file's life:
#
#   md_creation_advisory.sh      at Write, before the file has a history
#   session_disposal_advisory.sh at Stop, over everything still untracked
#
# Two copies of a whitelist is two answers to one question, and the copy that
# drifts is the one that starts nagging about README.md. There is one list, and
# it lives here.
#
# Both exemptions are structural, never a judgement about content:
#
#   scry_md_product_repo  — this repo's PRODUCT is Markdown. An Astro/
#     Docusaurus/VitePress content site's whole job is producing .md files, so
#     nothing under it is clutter. Detected by a generator config at the repo
#     root, not by filename, because the same filename (index.md) is dangerous
#     in a code repo and normal in a docs-site repo. Extend the marker list the
#     same way when another generator shows up.
#
#   scry_md_standard_file — the files every repo and tool convention already
#     expects: README, LICENSE, CHANGELOG, CLAUDE.md/AGENTS.md, GitHub's
#     community-health files, anything under .claude/. These have an
#     established, low-drift job; they are not what a session leaves behind.

# scry_md_product_repo <repo_root> — 0 when the repo's product is Markdown.
scry_md_product_repo() {
  local root="$1" marker
  [ -n "$root" ] || return 1
  for marker in astro.config.mjs astro.config.ts astro.config.js \
                docusaurus.config.js docusaurus.config.ts; do
    [ -f "$root/$marker" ] && return 0
  done
  [ -d "$root/.vitepress" ] && return 0
  return 1
}

# scry_md_standard_file <rel> — 0 when the repo-relative path is one of the
# standard, ecosystem-recognized Markdown files.
scry_md_standard_file() {
  case "$1" in
    CLAUDE.md|AGENTS.md|README.md|LICENSE.md|CONTRIBUTING.md| \
    CODE_OF_CONDUCT.md|SECURITY.md|SUPPORT.md|CHANGELOG.md) return 0 ;;
    .github/PULL_REQUEST_TEMPLATE.md|.github/copilot-instructions.md) return 0 ;;
    .github/ISSUE_TEMPLATE/*.md) return 0 ;;
    .claude/*|*/.claude/*) return 0 ;;
  esac
  return 1
}
