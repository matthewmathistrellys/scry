# Claude Code Hooks — Quick Reference Matrix

**All 25 hook events, decision control, and capabilities at a glance**

## Hook Events by Lifecycle

### Session Lifecycle (3)
| Event | Can Block? | Matchers | Key Output | Use Case |
|-------|-----------|----------|-----------|----------|
| **SessionStart** | No | startup, resume, clear, compact, fork | additionalContext, sessionTitle, watchPaths, reloadSkills | Load context, set title, watch files |
| **Setup** | No | init, maintenance | additionalContext | One-time deps, cleanup (explicit trigger) |
| **SessionEnd** | No | normal, interrupted, error, timeout | (observability) | Archive, cleanup, stats logging |

### Turn Lifecycle (5)
| Event | Can Block? | Matchers | Key Output | Use Case |
|-------|-----------|----------|-----------|----------|
| **UserPromptSubmit** | **YES** | — | permissionDecision, updatedInput, additionalContext | Validate/modify prompt before Claude sees it |
| **UserPromptExpansion** | **YES** | — | permissionDecision, updatedInput | Prevent Claude elaboration |
| **MessageDisplay** | No | — | displayContent | Transform display (not stored) |
| **Stop** | **YES** | — | permissionDecision | Prevent early stop, require min turns |
| **StopFailure** | No | — | (observability) | Log stop errors |

### Tool Lifecycle (5)
| Event | Can Block? | Matchers | Key Output | Use Case |
|-------|-----------|----------|-----------|----------|
| **PreToolUse** | **YES** | Tool name (Bash, Read, Write, ...) | permissionDecision, updatedInput, systemMessage | Deny rm, guard rebuilds, block reads |
| **PermissionRequest** | No (use decision) | — | decision (allow/deny/ask) | Pre-approve tools |
| **PostToolUse** | No | Tool name | additionalContext, updatedToolResponse | Inject advisory, modify response |
| **PostToolUseFailure** | No | Tool name | additionalContext, updatedToolResponse | Log errors, provide fix context |
| **PostToolBatch** | **YES** | — | permissionDecision | Checkpoint after batch, stop loop |

### State Changes (6)
| Event | Can Block? | Matchers | Key Output | Use Case |
|-------|-----------|----------|-----------|----------|
| **FileChanged** | No | — | (observability) | React to config changes, rebuild on watch |
| **DirectoryAdded** | No | — | (observability) | Index new dir, track scope |
| **CwdChanged** | No | — | (observability) | Load context per directory |
| **ConfigChange** | **YES** | — | permissionDecision | Audit changes, lock policies |
| **WorktreeCreate** | **YES** | — | (any nonzero = abort) | Clone template structure |
| **WorktreeRemove** | No | — | (observability) | Cleanup, archive |

### Compaction (2)
| Event | Can Block? | Matchers | Key Output | Use Case |
|-------|-----------|----------|-----------|----------|
| **PreCompact** | **YES** | manual, auto | permissionDecision | Block compaction on unmerged branch |
| **PostCompact** | No | manual, auto | (observability) | Validate summary, archive |

### Instructions (1)
| Event | Can Block? | Matchers | Key Output | Use Case |
|-------|-----------|----------|-----------|----------|
| **InstructionsLoaded** | No (async) | session_start, nested_traversal, path_glob_match, include, compact | (observability) | Audit policy loads |

### Agents (5)
| Event | Can Block? | Matchers | Key Output | Use Case |
|-------|-----------|----------|-----------|----------|
| **SubagentStart** | No | Agent type | (observability) | Warn before long subagent |
| **SubagentStop** | **YES** | Agent type | permissionDecision | Keep subagent working |
| **TeammateIdle** | **YES** | Agent type | permissionDecision | Keep teammate active |
| **TaskCreated** | **YES** | — | permissionDecision | Validate task, enforce naming |
| **TaskCompleted** | **YES** | — | permissionDecision | Require checklist before mark done |

### Permissions & Notifications (2)
| Event | Can Block? | Matchers | Key Output | Use Case |
|-------|-----------|----------|-----------|----------|
| **PermissionDenied** | No | — | retry | Signal model can retry |
| **Notification** | No | Notification type | (observability) | Forward to external system |

---

## Decision Control — Which Output Fields Work Where

### permissionDecision: allow | deny | ask
✓ PreToolUse, UserPromptSubmit, UserPromptExpansion, PermissionRequest, PostToolBatch, Stop, SubagentStop, TeammateIdle, TaskCreated, TaskCompleted, ConfigChange, PreCompact

### updatedInput (modify tool input)
✓ PreToolUse, UserPromptSubmit, UserPromptExpansion

### updatedToolResponse (modify what Claude sees)
✓ PostToolUse, PostToolUseFailure

### additionalContext (inject into Claude's context)
✓ SessionStart, Setup, PreToolUse, PostToolUse, PostToolUseFailure, UserPromptSubmit, UserPromptExpansion, PermissionDenied

### systemMessage (show to user)
✓ PreToolUse, UserPromptSubmit, UserPromptExpansion, PostToolBatch, Stop, SubagentStop, TeammateIdle, TaskCreated, TaskCompleted, PreCompact

### sessionTitle (auto-name session)
✓ SessionStart only

### watchPaths (watch files)
✓ SessionStart only

### reloadSkills (re-scan skills)
✓ SessionStart only

### displayContent (transform display)
✓ MessageDisplay only

### retry (signal can retry)
✓ PermissionDenied only

---

## Exit Code Reference

| Code | Blocks? | What it means | JSON honored? |
|------|---------|--------------|---------------|
| **0** | No | Success | Yes (if valid JSON) |
| **2** | **YES** (most events) | Blocking error | Yes, but block takes precedence |
| **Other (1,3,etc)** | No | Non-blocking error | Yes (if valid JSON) |

**Exception:** `WorktreeCreate` aborts on ANY nonzero exit, regardless of JSON.

### Exit Code 2 Behavior Per Event

| Event | Effect on exit 2 |
|-------|-----------------|
| PreToolUse | Blocks tool call |
| UserPromptSubmit | Blocks prompt, erases input |
| UserPromptExpansion | Blocks expansion |
| Stop | Prevents stop, continues |
| SubagentStop | Prevents stop |
| TeammateIdle | Prevents idle |
| TaskCreated | Rolls back creation |
| TaskCompleted | Prevents mark complete |
| ConfigChange | Blocks change (except policy_settings) |
| PostToolBatch | Stops agentic loop |
| PreCompact | Blocks compaction |
| **All other events** | No blocking (observability only) |

---

## Matcher Patterns — Syntax

| Syntax | Example | Matches |
|--------|---------|---------|
| Omitted or `*` | — | Everything |
| Exact name | `Bash` | Bash tool only |
| Pipe (OR) | `Edit\|Write` | Edit OR Write |
| Regex | `^Notebook`, `.*config` | Case-sensitive ECMAScript regex |

**Regex support:** `^` start anchor, `$` end anchor, `.` any char, `*` zero-or-more, `+` one-or-more, `\|` or.

---

## Handler Types — When to Use

| Type | Runs on | Blocks? | Use for |
|------|---------|---------|---------|
| `command` | Local machine | Yes | Shell scripts, local validation |
| `http` | Any webhook URL | Yes | External logging, webhooks |
| `mcp_tool` | MCP server | Yes | Database queries, rich logic |
| `prompt` | Claude API | Limited | Nuanced LLM-based decisions |
| `agent` | Subagent spawn | No | Verification/review before action |

---

## Timeouts & Performance

| Constraint | Value/Note |
|------------|-----------|
| Default hook timeout | ~600 seconds (configurable per hook) |
| PreToolUse timeout behavior | Command/HTTP/MCP timeout: doesn't block. Agent SDK callback timeout: does block |
| SessionStart speed target | <2s total (runs every session) |
| Async events | MessageDisplay, InstructionsLoaded, DirectoryAdded, FileChanged, PostCompact (don't block UI) |
| Stderr access | Hooks run without controlling terminal (no `/dev/tty` access on Unix) |

---

## Configuration Precedence

Highest to lowest:
1. Managed settings (admin)
2. `.claude/settings.local.json` (project, gitignored)
3. `.claude/settings.json` (project, checked in)
4. `~/.claude/settings.local.json` (user, gitignored)
5. `~/.claude/settings.json` (user, checked in)
6. Plugin `hooks/hooks.json` (when plugin enabled)
7. Skill/Agent frontmatter `hooks` (session scope)

All sources merge; hooks run in order matched.

---

## Environment Variables & Paths

| Variable | Available in | Value |
|----------|-------------|-------|
| `CLAUDE_PROJECT_DIR` | All | Project root |
| `CLAUDE_PLUGIN_ROOT` | Plugin hooks | Plugin install dir |
| `CLAUDE_PLUGIN_DATA` | Plugin hooks | Plugin persistent data |
| `CLAUDE_ENV_FILE` | SessionStart, Setup, CwdChanged, FileChanged | Path to append `export VAR=value` |
| `${PLUGIN_ROOT}` | Any hook | Fallback to CLAUDE_PLUGIN_ROOT |

**All file paths in hook input are absolute.** Normalize separators on Windows (backslash → forward slash for regex).

---

## Scry's Existing Hooks

**SessionStart (6 scanners):**
- `architecture.sh` — map of codebase (detects stack, calls scanners)
- `stack.sh` — live config: DB, hosting, runtime, services
- `health.sh` — repo state, worktree locks, deploy drift, PR/CI status
- `fleet.sh` — other sessions active, collision risk, last session title
- `pressure.sh` — load, swap, disk, dev servers listening
- `provenance.sh` — doc artifact census + read-time trust advisory

**PostToolUse on Read:**
- `md_advisory.sh` — flags artifacts vs guidance (doc trust)

**PostToolUse on Edit|Write:**
- `elixir_advisory.sh` — Elixir anti-pattern detection

**PreToolUse on Bash:**
- `elixir_build_guard.sh` — two-strike gate on expensive rebuilds (cost + cheaper options on first attempt, allow on retry)

---

## High-Value Events NOT Currently Used

| Event | Why valuable for scry | Suggested implementation |
|-------|----------------------|-------------------------|
| **FileChanged** | Watch `.env`, `fly.toml` for config drift | Re-trigger stack.sh on change, warn if critical files deleted |
| **CwdChanged** | Load domain/context per directory | Set SCRY_* tuning variables per domain, track which directory |
| **ConfigChange** | Audit permission/model changes | Log model swaps, enforce policy locks |
| **PreCompact** | Prevent accidental data loss | Block on unmerged branch, warn before auto-compact |
| **SessionEnd** | Archive and audit | Log session stats, archive if experimental branch, notify on errors |

---

## JSON Schema Validation

- **Exit 0 + invalid JSON** → non-blocking error, debug log only
- **Exit 1-9 + invalid JSON** → non-blocking error, shows stderr, debug log
- **Exit 2 + invalid JSON** → **BLOCKS anyway**, stderr is reason, logs validation error
- **Exit N + empty stdout** → non-blocking error if N ≠ 0

For structured control, return valid JSON on stdout; plain text is debug-logged only (exceptions: SessionStart, UserPromptSubmit, UserPromptExpansion add plain text to Claude's context).

---

## Best Practices Checklist

- [ ] Fail open (errors shouldn't block session)
- [ ] Keep SessionStart/PreToolUse fast (<1s each)
- [ ] Idempotent (safe to re-run for same input)
- [ ] Deterministic (consistent output)
- [ ] Exit 2 to block (exit 1 is non-blocking by default)
- [ ] Regex is case-sensitive
- [ ] Paths are absolute, normalize separators on Windows
- [ ] Use JSON for structured control
- [ ] Version for schema changes (check `hook_event_name`, use `additionalContext`)

