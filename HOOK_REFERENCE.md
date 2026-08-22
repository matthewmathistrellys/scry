# Claude Code Hook System — Complete Authoritative Reference

**Source:** Official Claude Code documentation (https://code.claude.com/docs/en/hooks.md and hooks-guide.md)  
**Last verified:** 2026-08-22

---

## Hook Events — Complete Enumeration

Claude Code fires hooks at **25 distinct events** across three cadences:

| Cadence | Events | Frequency |
|---------|--------|-----------|
| **Session lifecycle** | SessionStart, Setup, SessionEnd | Once per session |
| **Turn lifecycle** | UserPromptSubmit, UserPromptExpansion, MessageDisplay, Stop, StopFailure | Once per turn (or per prompt) |
| **Tool lifecycle** | PreToolUse, PermissionRequest, PostToolUse, PostToolUseFailure, PostToolBatch | Per tool call inside the agentic loop |
| **State changes** | FileChanged, DirectoryAdded, CwdChanged, ConfigChange, WorktreeCreate, WorktreeRemove | On file system or session state changes |
| **Compaction** | PreCompact, PostCompact | During conversation compaction |
| **Instructions** | InstructionsLoaded | When CLAUDE.md or rules files load |
| **Agents** | SubagentStart, SubagentStop, TeammateIdle, TaskCreated, TaskCompleted | When subagents/teammates spawn or tasks change |
| **Other** | Notification, PermissionDenied | On notifications or when Claude lacks permission |

---

## Hook Events — Detailed Reference

### 1. SessionStart
**When:** New session or resume (startup/resume/clear/compact/fork)  
**Handler types:** `command`, `mcp_tool` only (not HTTP, prompt, or agent)  
**Matchers (how session started):**
- `startup` — new session
- `resume` — `--resume`, `--continue`, `/resume`
- `clear` — `/clear`
- `compact` — auto or manual compaction
- `fork` — `--fork-session`, `/fork`, `/branch`

**Input:**
```json
{
  "session_id": "abc123",
  "transcript_path": "/path/to/transcript.jsonl",
  "cwd": "/current/directory",
  "hook_event_name": "SessionStart",
  "source": "startup|resume|clear|compact|fork",
  "model": "claude-sonnet-5",  // optional
  "agent_type": "agent-name",  // optional, if --agent used
  "session_title": "...",      // optional, if already set
  "permission_mode": "default|plan|auto|..."
}
```

**Output (JSON `hookSpecificOutput` fields):**
- `additionalContext` — text added to Claude's context at session start
- `initialUserMessage` — first turn message (only in `-p` non-interactive mode)
- `sessionTitle` — auto-set session title (from branch/folder/etc)
- `watchPaths` — array of absolute paths for FileChanged events
- `reloadSkills` — boolean to re-scan skills after hook completes

**Decision control:** None (exit code 2 shows stderr, doesn't block)  
**Capabilities:** Inject context, set title, watch files, reload skills  
**Timeout:** Default, configurable in hook config  
**Access:** `CLAUDE_ENV_FILE` to persist environment variables into Bash commands

**Use cases:** Load recent issues, branch name into context, set up dev environment variables

---

### 2. Setup
**When:** `claude --init-only`, `claude -p --init`, or `claude -p --maintenance`  
**Handler types:** `command`, `mcp_tool` only  
**Matchers:**
- `init` — `claude --init-only` or `claude -p --init`
- `maintenance` — `claude -p --maintenance`

**Input:**
```json
{
  "session_id": "abc123",
  "transcript_path": "...",
  "cwd": "...",
  "hook_event_name": "Setup",
  "trigger": "init|maintenance"
}
```

**Output fields:**
- `additionalContext` — passed to Claude

**Decision control:** None (non-blocking, doesn't fire on every session)  
**Access:** `CLAUDE_ENV_FILE`  
**Use cases:** One-time dependency installation, scheduled cleanup (triggered explicitly from CI)

---

### 3. InstructionsLoaded
**When:** CLAUDE.md or `.claude/rules/*.md` file loads  
**Async:** Yes (observability only, no decision control)  
**Matchers:** `session_start`, `nested_traversal`, `path_glob_match`, `include`, `compact`

**Input:**
```json
{
  "session_id": "...",
  "cwd": "...",
  "hook_event_name": "InstructionsLoaded",
  "file_path": "/absolute/path/to/CLAUDE.md",
  "memory_type": "User|Project|Local|Managed",
  "load_reason": "session_start|nested_traversal|path_glob_match|include|compact",
  "globs": ["path/patterns"],  // if path_glob_match
  "trigger_file_path": "...",  // for lazy loads
  "parent_file_path": "..."    // for include loads
}
```

**Output:** No effect (async observability only)  
**Use cases:** Log when instructions load, audit policy file access

---

### 4. UserPromptSubmit
**When:** User submits a prompt (before Claude sees it)  
**Can block:** Yes (exit code 2)  
**Matchers:** None (wildcard only)

**Input:**
```json
{
  "session_id": "...",
  "cwd": "...",
  "hook_event_name": "UserPromptSubmit",
  "prompt": "the user's prompt text",
  "turn_number": 5
}
```

**Output fields:**
- `permissionDecision` — `allow`, `deny`, `ask`
- `permissionDecisionReason` — why (shown on deny)
- `updatedInput` — modified prompt (e.g., prepend instructions)
- `additionalContext` — added to Claude's context
- `systemMessage` — shown to user

**Decision control:** `permissionDecision` (allow/deny/ask)  
**Exit code 2:** Blocks prompt, erases input  
**Use cases:** Validate prompt content, inject context, modify prompt before Claude sees it

---

### 5. UserPromptExpansion
**When:** When Claude expands or elaborates on a user prompt  
**Can block:** Yes (exit code 2)  
**Matchers:** None

**Input:**
```json
{
  "session_id": "...",
  "cwd": "...",
  "hook_event_name": "UserPromptExpansion",
  "original_prompt": "original text",
  "expanded_prompt": "expanded text"
}
```

**Output fields:**
- `permissionDecision`, `permissionDecisionReason`, `updatedInput`, `additionalContext`

**Decision control:** Block expansion via exit code 2  
**Use cases:** Prevent automatic prompt elaboration, enforce strict prompt form

---

### 6. MessageDisplay
**When:** Each batch of Claude's response displays (or full message in non-interactive)  
**No decision control** (observability/display only)  
**Async:** Yes

**Input:**
```json
{
  "session_id": "...",
  "turn_id": "uuid",
  "message_id": "uuid",  // stable across batches of same message
  "index": 0,            // zero-based batch within message
  "final": false,        // true on last batch
  "delta": "text of newly completed lines\n",  // or full message in -p mode
  "hook_event_name": "MessageDisplay"
}
```

**Output fields:**
- `displayContent` — replace delta on screen (affects display only, not transcript)

**Use cases:** Strip markdown, log message text, display transformation (no business logic)

---

### 7. PreToolUse
**When:** Claude creates tool parameters, before processing the call  
**Can block:** Yes (exit code 2)  
**Matchers (tool name):** `Bash`, `PowerShell`, `Read`, `Write`, `Edit`, `Glob`, `Grep`, `Agent`, `WebFetch`, `WebSearch`, `AskUserQuestion`, `ExitPlanMode`, MCP tool names, or `*` for all

**Input:**
```json
{
  "session_id": "...",
  "cwd": "...",
  "hook_event_name": "PreToolUse",
  "tool_name": "Bash|Read|Write|...",
  "tool_use_id": "unique-id",
  "tool_input": {
    // Depends on tool; examples:
    "command": "npm test",        // Bash
    "file_path": "/path/to/file", // Read|Write|Edit
    "pattern": "*.ts",            // Glob
    "url": "https://example.com", // WebFetch
    "query": "search query",      // WebSearch
    "prompt": "task description"  // Agent
  }
}
```

**Output fields:**
- `permissionDecision` — `allow`, `deny`, `ask`
- `permissionDecisionReason`
- `updatedInput` — modify tool_input before execution
- `systemMessage` — shown to user
- `continue` — bypass permission gates (if decision is `allow`)

**Decision control:** Full (allow/deny/ask/defer)  
**Exit code 2:** Blocks tool call  
**Timeout:** Timed-out `command`/`http`/`mcp_tool` doesn't block; Agent SDK callback hook timeout does block  
**Matchers:** Exact (Bash, Read) or regex (`.*config`)

**Use cases:** Deny `rm -rf`, guard expensive builds, block reads of sensitive files, modify commands before execution

---

### 8. PermissionRequest
**When:** Claude requests permission to run a tool  
**Can block:** No (exit code 2 ignored; deny via `decision` object)  
**Async:** No

**Input:**
```json
{
  "session_id": "...",
  "cwd": "...",
  "hook_event_name": "PermissionRequest",
  "tool_name": "Bash",
  "required_permission": "bash",
  "tool_input": { ... }
}
```

**Output fields:**
- `decision` — `allow`, `deny`, `ask`
- `reason` — why (shown on deny)

**Use cases:** Pre-approve tool calls before Claude even asks, enforce blanket policies

---

### 9. PostToolUse
**When:** After a tool call completes successfully  
**Can block:** No (tool already ran)  
**Matches on:** Tool name (same as PreToolUse)

**Input:**
```json
{
  "session_id": "...",
  "cwd": "...",
  "hook_event_name": "PostToolUse",
  "tool_name": "Read",
  "tool_use_id": "...",
  "tool_input": { "file_path": "..." },
  "tool_response": {
    // Depends on tool; for Read: file content
    // For Bash: stdout, stderr, exit code
    // For Agent: status, agentId, content[], resolvedModel, totalTokens, totalDurationMs, totalToolUseCount, usage
  }
}
```

**Output fields:**
- `additionalContext` — added to Claude's context
- `systemMessage` — shown to user
- `updatedToolResponse` — modify what Claude sees (not stored in transcript)
- `continue` — proceed without waiting for user approval

**Decision control:** Limited (observe, modify what Claude sees)  
**Exit code 2:** Shows stderr to Claude (tool already ran)  
**Use cases:** Inject advisory (doc trust, anti-patterns), log tool use, modify response before Claude sees it

**From scry:** `md_advisory.sh` injects doc-artifact trust notices after Read; `elixir_advisory.sh` warns on Elixir anti-patterns after Edit/Write

---

### 10. PostToolUseFailure
**When:** Tool call fails or times out  
**Can block:** No  
**Async:** No

**Input:**
```json
{
  "session_id": "...",
  "cwd": "...",
  "hook_event_name": "PostToolUseFailure",
  "tool_name": "Bash",
  "tool_use_id": "...",
  "tool_input": { ... },
  "error": "error message",
  "error_type": "timeout|permission|execution|..."
}
```

**Output fields:** Same as PostToolUse  
**Exit code 2:** Shows stderr to Claude (already failed)  
**Use cases:** Log errors, inject context about fix, modify error message

---

### 11. PostToolBatch
**When:** After a batch of tool calls completes (before next Claude turn)  
**Can block:** Yes (exit code 2)

**Input:**
```json
{
  "session_id": "...",
  "cwd": "...",
  "hook_event_name": "PostToolBatch",
  "tool_use_count": 3,
  "tool_uses": [ { "tool_name": "...", "tool_input": {...} }, ... ]
}
```

**Output fields:** `permissionDecision`, `systemMessage`  
**Exit code 2:** Stops agentic loop before next model call  
**Use cases:** Checkpoint after batch, validate combined tool effects, stop loop if threshold exceeded

---

### 12. Stop
**When:** Claude requests to stop (before stopping)  
**Can block:** Yes (exit code 2)  
**Matchers:** `*` (wildcard only)

**Input:**
```json
{
  "session_id": "...",
  "cwd": "...",
  "hook_event_name": "Stop",
  "reason": "task_complete|user_request|context_limit|...",
  "turn_number": 5,
  "message": "..."
}
```

**Output fields:** `permissionDecision`, `systemMessage`  
**Exit code 2:** Prevents Claude from stopping, conversation continues  
**Use cases:** Enforce minimum turns, require explicit user confirmation, prevent early stop

---

### 13. StopFailure
**When:** Stop fails or produces an error  
**Can block:** No  
**Output:** Ignored except `terminalSequence` (for terminal control codes)

**Use cases:** Log stop failures (observability only)

---

### 14. PermissionDenied
**When:** Claude lacks permission to run a tool  
**Can block:** No  
**Async:** No

**Input:**
```json
{
  "session_id": "...",
  "cwd": "...",
  "hook_event_name": "PermissionDenied",
  "permission_type": "required_permission",
  "required_permission": "bash",
  "tool_name": "Bash",
  "tool_input": { ... }
}
```

**Output fields:**
- `retry` — `true` to tell model it may retry (ignored for no-verdict denials)

**Exit code 2:** Ignored (denial already occurred)  
**Use cases:** Log denials, signal to model that retry is possible

---

### 15. Notification
**When:** Claude Code or a skill sends a notification  
**Can block:** No  
**Matchers:** Notification type  

**Input:**
```json
{
  "session_id": "...",
  "cwd": "...",
  "hook_event_name": "Notification",
  "notification_type": "info|warning|error",
  "message": "notification text"
}
```

**Output:** Ignored  
**Use cases:** Log notifications externally, forward to external system

---

### 16. SubagentStart
**When:** Subagent spawns  
**Can block:** No  
**Async:** No

**Input:**
```json
{
  "session_id": "...",
  "cwd": "...",
  "hook_event_name": "SubagentStart",
  "subagent_type": "Explore|Fix|Review|...",
  "subagent_id": "...",
  "prompt": "task description"
}
```

**Output:** stderr shown to user  
**Use cases:** Log subagent launches, warn before long-running subagent

---

### 17. SubagentStop
**When:** Subagent stops (before stopping)  
**Can block:** Yes (exit code 2)  
**Matchers:** Agent type

**Input:**
```json
{
  "session_id": "...",
  "cwd": "...",
  "hook_event_name": "SubagentStop",
  "subagent_id": "...",
  "subagent_type": "...",
  "reason": "..."
}
```

**Output fields:** `permissionDecision`, `systemMessage`  
**Exit code 2:** Prevents subagent from stopping  
**Use cases:** Require subagent to reach completion, block early termination

---

### 18. TaskCreated
**When:** Task created (before persisting)  
**Can block:** Yes (exit code 2)  
**Matchers:** `*`

**Input:**
```json
{
  "session_id": "...",
  "cwd": "...",
  "hook_event_name": "TaskCreated",
  "task_id": "...",
  "task_name": "...",
  "description": "..."
}
```

**Output fields:** `permissionDecision`  
**Exit code 2:** Rolls back task creation  
**Use cases:** Validate task content, enforce naming convention, prevent task creation under certain conditions

---

### 19. TaskCompleted
**When:** Task marked complete (before persisting)  
**Can block:** Yes (exit code 2)  
**Matchers:** `*`

**Input:**
```json
{
  "session_id": "...",
  "cwd": "...",
  "hook_event_name": "TaskCompleted",
  "task_id": "...",
  "task_name": "...",
  "completion_time": "ISO8601"
}
```

**Output fields:** `permissionDecision`  
**Exit code 2:** Prevents task from being marked complete  
**Use cases:** Enforce checklist before completion, validate completion status

---

### 20. TeammateIdle
**When:** Teammate about to go idle  
**Can block:** Yes (exit code 2)  
**Matchers:** Agent type

**Input:**
```json
{
  "session_id": "...",
  "cwd": "...",
  "hook_event_name": "TeammateIdle",
  "teammate_id": "...",
  "teammate_type": "..."
}
```

**Output fields:** `permissionDecision`  
**Exit code 2:** Prevents idle, teammate continues working  
**Use cases:** Keep teammate active during critical work, log teammate status changes

---

### 21. CwdChanged
**When:** Working directory changes (via `cd` in Bash or `/cd`)  
**Can block:** No  
**Async:** No

**Input:**
```json
{
  "session_id": "...",
  "cwd": "/new/directory",
  "previous_cwd": "/old/directory",
  "hook_event_name": "CwdChanged"
}
```

**Output:** stderr shown to user  
**Access:** `CLAUDE_ENV_FILE`  
**Use cases:** Load project-specific context when entering directory, set environment variables per project

---

### 22. DirectoryAdded
**When:** Directory added to session scope (via `/add-dir` or `watchPaths` in SessionStart)  
**Can block:** No  
**Async:** Yes

**Input:**
```json
{
  "session_id": "...",
  "cwd": "...",
  "hook_event_name": "DirectoryAdded",
  "directory_path": "/absolute/path"
}
```

**Output:** stderr to debug log only  
**Use cases:** Index new directory, log scope changes

---

### 23. FileChanged
**When:** Watched file changes (after change, via `watchPaths`)  
**Can block:** No  
**Async:** Yes

**Input:**
```json
{
  "session_id": "...",
  "cwd": "...",
  "hook_event_name": "FileChanged",
  "file_path": "/absolute/path",
  "change_type": "created|modified|deleted",
  "is_binary": false
}
```

**Output:** stderr shown to user  
**Use cases:** React to file changes (rebuild, test, lint), trigger workflows on config file updates

---

### 24. ConfigChange
**When:** Settings, permissions, or environment change during session  
**Can block:** Yes (exit code 2) — except `policy_settings`  
**Matchers:** `*`

**Input:**
```json
{
  "session_id": "...",
  "cwd": "...",
  "hook_event_name": "ConfigChange",
  "change_type": "settings|permissions|environment",
  "changed_fields": [ "model", "permission_mode", ... ]
}
```

**Output fields:** `permissionDecision`  
**Exit code 2:** Blocks change (except policy_settings)  
**Use cases:** Audit config changes, prevent model changes mid-run, enforce policy locks

---

### 25. PreCompact
**When:** Before compaction (manual `/compact` or auto)  
**Can block:** Yes (exit code 2)  
**Matchers:** `manual`, `auto`

**Input:**
```json
{
  "session_id": "...",
  "cwd": "...",
  "hook_event_name": "PreCompact",
  "trigger": "manual|auto",
  "custom_instructions": "from /compact command or empty"
}
```

**Output fields:** `permissionDecision`  
**Exit code 2:** Blocks compaction  
**Use cases:** Prevent compaction under certain conditions, warn before compaction, validate compaction readiness

---

### 26. PostCompact
**When:** After compaction completes  
**Can block:** No  
**Matchers:** `manual`, `auto`

**Input:**
```json
{
  "session_id": "...",
  "cwd": "...",
  "hook_event_name": "PostCompact",
  "trigger": "manual|auto",
  "compact_summary": "The generated summary..."
}
```

**Output:** stderr shown to user  
**Use cases:** Log compaction, validate summary, archive compacted state

---

### 27. SessionEnd
**When:** Session ends  
**Can block:** No  
**Matchers:** Exit reason (`normal`, `interrupted`, `error`, `timeout`, ...)

**Input:**
```json
{
  "session_id": "...",
  "cwd": "...",
  "hook_event_name": "SessionEnd",
  "exit_reason": "normal|interrupted|error|timeout",
  "duration_seconds": 3600,
  "turn_count": 12
}
```

**Output:** stderr shown to user  
**Use cases:** Cleanup, log session stats, archive transcript, notify external system

---

### 28. WorktreeCreate
**When:** Worktree created (before finalizing)  
**Can block:** Yes (any non-zero exit)  
**Matchers:** `*`

**Input:**
```json
{
  "session_id": "...",
  "cwd": "...",
  "hook_event_name": "WorktreeCreate",
  "worktree_path": "/path/to/.worktrees/feature-branch",
  "branch_name": "feature/auth"
}
```

**Output:** Any nonzero exit aborts creation  
**Use cases:** Create initial structure, clone dependencies, validate branch before worktree creation

---

### 29. WorktreeRemove
**When:** Worktree removed (before deletion)  
**Can block:** No  
**Async:** No

**Input:**
```json
{
  "session_id": "...",
  "cwd": "...",
  "hook_event_name": "WorktreeRemove",
  "worktree_path": "/path/to/.worktrees/..."
}
```

**Output:** stderr to debug log only  
**Use cases:** Cleanup (remove version control state, archive changes)

---

## Common Input Fields (All Events)

Every hook receives:
```json
{
  "session_id": "unique-session-id",
  "transcript_path": "/absolute/path/to/transcript.jsonl",
  "cwd": "/current/working/directory",
  "hook_event_name": "EventName",
  "permission_mode": "default|plan|auto|...",
  "effort": { "level": "low|medium|high|max" }
}
```

---

## JSON Output Schema (Common Fields)

All hooks can return these fields in a `hookSpecificOutput` object:

```json
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",  // event name
    "additionalContext": "text to add to Claude's context",
    "systemMessage": "shown to user",
    "permissionDecision": "allow|deny|ask",  // if event supports it
    "permissionDecisionReason": "why denied/asked",
    "updatedInput": { "modified": "tool input" },
    "updatedToolResponse": "modified response",
    "displayContent": "changed display",
    "retry": true,  // PermissionDenied only
    "sessionTitle": "auto-set title",
    "continue": true,
    "terminalSequence": "\x1b[31m"  // escape code
  }
}
```

---

## Exit Code Behavior

### Exit Code 0
- **Success.** JSON on stdout is parsed and honored. Plain stdout (not JSON) is logged to debug.
- **For SessionStart, UserPromptSubmit, UserPromptExpansion:** Plain stdout is added to Claude's context.
- **For most other events:** Plain stdout is written to debug log only.
- **Valid JSON that fails schema validation:** Treated as non-blocking error.

### Exit Code 2
- **Blocking error.** Prevents the action (tool call, prompt, compaction, etc.).
- **Blocks regardless of JSON.** Even `"permissionDecision": "allow"` in JSON can't override exit 2.
- **Message source:** Blocking reason from JSON if present, otherwise stderr.
- **Behavior per event:** See [Exit code 2 behavior per event table](#exit-code-2-behavior-per-event) above.

### Other Exit Codes (1, 3, etc.)
- **Non-blocking error by default.**
- **If valid JSON on stdout:** JSON is honored (exit code ignored).
- **If invalid JSON or plain text:** Shows `<hook name> hook error` notice with stderr.
- **Action proceeds.** Tool calls, prompts, etc. are not blocked by non-zero exit alone.
- **Exception:** `WorktreeCreate` aborts on any nonzero exit, regardless of JSON.

---

## Timeouts & Constraints

| Aspect | Constraint |
|--------|-----------|
| **Hook timeout** | Configurable per hook (default ~600s). On timeout, hook output is discarded. |
| **PreToolUse command timeout** | Timed-out hook doesn't block the tool call (proceeds via normal permission flow). Agent SDK callback hook timeout does block. |
| **SessionStart speed** | Runs on every session — keep fast (~2s total for scry's 6 hooks). |
| **Parallel execution** | Hooks within a matcher group run sequentially. Hooks across matchers may run in parallel (implementation detail). |
| **PostToolUse, MessageDisplay** | Run asynchronously; don't block Claude or the UI. |
| **Stderr access** | On macOS/Linux, command hooks run without controlling terminal (no `/dev/tty`). |
| **Plugin vs project hooks** | Plugin hooks (in `plugin/hooks.json`) apply when plugin is enabled. Project hooks (in `.claude/settings.json` or `~/.claude/settings.json`) always apply (user-level) or apply to that project (project-level). |

---

## Matchers — Pattern Matching

### Matcher Syntax

| Syntax | Meaning | Example |
|--------|---------|---------|
| Omitted or `"*"` | Match all | matches all tools |
| Exact name | Tool or event | `"Bash"`, `"Read"`, `"manual"` |
| Pipe-separated | OR logic | `"Edit\|Write"` (both tools) |
| Regex | Regular expression | `"^Notebook"`, `".*config"`, `"Read.*History"` |

### Regex Support
- Matchers support **case-sensitive regex** (ECMAScript dialect in Claude Code).
- `^` anchors start, `$` anchors end, `.` matches any char, `*` is zero-or-more, `+` is one-or-more.
- Example: match all Bash-like tools: `"Bash|PowerShell"`.

### Per-Event Matchers

| Event | Matcher values |
|-------|----------------|
| **SessionStart** | `startup`, `resume`, `clear`, `compact`, `fork` |
| **Setup** | `init`, `maintenance` |
| **PreToolUse, PostToolUse, PostToolUseFailure** | Tool name: `Bash`, `PowerShell`, `Read`, `Write`, `Edit`, `Glob`, `Grep`, `Agent`, `WebFetch`, `WebSearch`, `AskUserQuestion`, `ExitPlanMode`, MCP tool names, `*` |
| **PreCompact, PostCompact** | `manual`, `auto` |
| **SubagentStart, SubagentStop** | Agent type (exact match or regex) |
| **CwdChanged, DirectoryAdded, FileChanged, ConfigChange** | `*` (wildcard only) or `startup`, `nested_traversal`, etc. for some |
| **Most other events** | `*` only |

---

## Configuration Locations & Scope

| Location | Scope | Shareable | Precedence |
|----------|-------|-----------|-----------|
| `~/.claude/settings.json` | All projects, all machines | Local only | Lowest |
| `~/.claude/settings.local.json` | Overrides user settings | No (gitignored) | Medium |
| `.claude/settings.json` | This project only | Yes (checked in) | Higher |
| `.claude/settings.local.json` | This project only | No (gitignored) | Highest |
| Plugin `hooks/hooks.json` | When plugin enabled | Yes (in plugin) | Merged with above |
| Skill/Agent frontmatter `hooks` | Session scope only | Yes (in skill) | Merged |
| Managed settings (admin) | Organization-wide | Admin control | Overrides user settings |

Hooks from all sources are merged and run in sequence when matching.

---

## Handler Types

| Type | When to use | Runs where | Supports |
|------|------------|-----------|----------|
| `command` | Shell script | Local machine | Full JSON I/O, blocking |
| `http` | POST to webhook | Any URL | JSON I/O, blocking, async |
| `mcp_tool` | MCP server tool call | Via MCP server | Full feature set |
| `prompt` | Claude evaluates condition | Via Claude API | Decision control only |
| `agent` | Spawn subagent | Local machine | Verification/review tasks |

---

## Real Use Cases from scry

Your hooks already implement:

1. **SessionStart (6 scanners):** Architecture, stack, health, fleet, pressure, provenance
   - Inject architectural context on startup
   - State system dependencies live (Neon region, Fly region, etc.)
   - Warn of dev environment issues (worktree locks, deploy drift, orphaned files)

2. **PostToolUse on Read:** `md_advisory.sh`
   - Read-time trust advisory on docs
   - Flags artifact (doc that's packaged or delivered) vs guidance

3. **PostToolUse on Edit|Write:** `elixir_advisory.sh`
   - Anti-pattern detection after Elixir code changes
   - Warn on common mistakes (e.g., Ash resource design issues)

4. **PreToolUse on Bash:** `elixir_build_guard.sh`
   - Two-strike gate on expensive rebuilds (`mix compile --force`, `rm -rf _build`)
   - First attempt: deny with cost + cheaper options
   - Retry within 5 min window: allow silently
   - Fails open (not Bash, not Elixir project, unwritable state = allow)

---

## Events You're Not Using

### High-value candidates for scry:

1. **FileChanged** (watch config files)
   - Watch `.env`, `fly.toml`, manifest files for changes
   - Trigger re-read of stack when env changes
   - Warn if critical config files are deleted

2. **CwdChanged** (context per directory)
   - Load domain/context when entering `apps/engine` vs `apps/web`
   - Set `SCRY_*` tuning variables per domain
   - Track which directory the session is in

3. **ConfigChange** (audit permission/model changes)
   - Log permission mode changes for security audit
   - Warn if model changes mid-important-work
   - Enforce policy locks (e.g., model pinning)

4. **PreCompact** (prevent data loss)
   - Warn before compaction if unpushed commits exist
   - Block auto-compact if branch is unmerged
   - Require confirmation for manual compaction on feature branch

5. **SessionEnd** (archive session)
   - Log session stats (duration, turns, tools used)
   - Archive transcript if branch is experimental
   - Notify if session ended with errors

### Lower-value or niche:

6. **TeammateIdle** — Keep teammate working (if using teammates)
7. **TaskCreated/TaskCompleted** — Validate task naming (if using tasks)
8. **PermissionDenied** — Audit denials (if tracking security)
9. **Notification** — Forward notifications externally (if integrated with Slack/etc)
10. **WorktreeCreate** — Clone template structure (if doing standardized worktrees)

---

## Schema Validation & Error Handling

JSON output is validated against expected schema per event. **Invalid JSON doesn't block:**

- Exit 0 + invalid JSON = non-blocking error + debug log entry
- Exit 1-9 + invalid JSON = non-blocking error + shows stderr + debug log
- Exit 2 + invalid JSON = **blocks anyway** + uses stderr as reason + logs validation failure
- No JSON (empty stdout) = non-blocking error if exit ≠ 0

---

## Advanced Features

### Async Hooks
Events like `MessageDisplay`, `InstructionsLoaded`, `DirectoryAdded`, `FileChanged`, `PostCompact` run **asynchronously** and don't block the session. They're for observability and side effects, not for decision control.

### HTTP Hooks
POST same JSON to a webhook URL. Response body is parsed as JSON and honored the same way as command hook stdout. No timeout override per hook (respects API limits). Useful for logging to external systems.

### MCP Tool Hooks
Call a tool on an MCP server. Can access context from the MCP server (e.g., database queries, external APIs). Useful for rich decision logic without shell scripts.

### Prompt Hooks
Send condition to Claude via API. Claude evaluates and returns a decision. Useful for nuanced, context-aware decisions that benefit from LLM reasoning.

### Agent Hooks
Spawn subagent to verify/review decision. Example: before allowing a destructive Bash command, ask Explore agent to verify intent. Useful for high-stakes decisions.

---

## Environment & Path Variables

| Variable | Available in | Value | Example |
|----------|-------------|-------|---------|
| `CLAUDE_PROJECT_DIR` | All hooks | Project root | `/Users/matt/Dev/myapp` |
| `CLAUDE_PLUGIN_ROOT` | Plugin hooks | Plugin installation dir | `~/.claude/plugins/scry` |
| `CLAUDE_PLUGIN_DATA` | Plugin hooks | Plugin persistent data | `~/.claude/plugin-data/scry` |
| `CLAUDE_ENV_FILE` | SessionStart, Setup, CwdChanged, FileChanged | Path to env file | Append `export VAR=value` |
| `${PLUGIN_ROOT}` | Any hook | Fallback to CLAUDE_PLUGIN_ROOT | For portability |

---

## Debugging Hooks

Enable debug logging to see hook execution:
```bash
claude --debug-file /tmp/debug.log
```

Check for:
- Hook execution order and timing
- JSON parse errors
- Exit codes and stderr
- Timeouts
- Schema validation failures

Hook output is separate from Claude's output — use debug log to inspect what hooks returned.

---

## Best Practices

1. **Fail open:** Hook errors shouldn't block the session. Exit 0 on unexpected input; log to debug.
2. **Fast:** SessionStart and PreToolUse hooks should complete in <1s per hook.
3. **Idempotent:** A hook may run multiple times for the same event; it should be safe to re-run.
4. **Deterministic:** Exit codes and decisions should be consistent for the same input.
5. **No side effects in decision hooks:** A hook that blocks shouldn't modify state (files, env, etc.).
6. **Use exit 2 for blocks:** Exit 2 is the reliable way to enforce a policy; exit 1 is not.
7. **Regex is case-sensitive:** `^Read` matches Read but not read.
8. **Paths are absolute:** All file paths in hook input are absolute; normalize separators on Windows.
9. **JSON over stdout:** For structured control, return JSON; plain text is debug-logged only (except SessionStart/UserPromptSubmit/UserPromptExpansion).
10. **Versioning:** Hook schemas may evolve; check `hook_event_name` in input and use `additionalContext` for forward compatibility.

