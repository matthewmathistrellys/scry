# scry

[Claude Code](https://claude.com/claude-code) and
[Codex](https://developers.openai.com/codex/) `SessionStart` checks that give a
fresh agent session — or a fresh you — the state of the world the instant it
starts, instead of making it dig or making you remember to ask. Both clients
run the same four scripts from this repository; only their package manifests
are client-specific.

A session is told its working directory and a git snapshot, and nothing else.
Not what else is running, not what the last session was doing, not what the
machine is carrying. Four independent checks fill that in.

| | Answers |
|---|---|
| **`architecture.sh`** | What *is* this codebase? |
| **`health.sh`** | Is this repo in good shape? |
| **`fleet.sh`** | What else is happening *right now*? |
| **`pressure.sh`** | What shape is this machine in? |

- **`architecture.sh`** — a one-line map of the codebase. It is a *dispatcher*,
  not a language scanner: it detects the stack and hands off to `scanners/`
  (Elixir/Ash today, others to follow), so one hook works in every repo
  including a polyglot tree. Outside a recognised project it falls back to a
  directory layout. The Elixir/Ash scanner also flags a worktree whose
  dependencies were never fetched (no `deps/` directory) — the fastest way to
  turn a fresh worktree's first `mix compile` into a bare `Mix.Error` into a
  heads-up instead.
- **`health.sh`** — the repo's health from this session's perspective:
  - **Primary worktree state:** on main? dirty? stranded on a merged branch?
    How long parked? Files that exist in no commit on any branch?
  - **Primary worktree consequences:** when the session is in the primary
    worktree, projects the mechanical consequences — orphaned edits inherited
    by future sessions, silent ref corruption from concurrent checkouts,
    repo-wide merge blockage if left on a feature branch.
  - **Session worktree health:** when the session is in a linked worktree,
    states unprompted that Claude Code has locked the session to it and gives
    the one working way out (`ExitWorktree`) — the lock is real, not a
    Worktrunk setting, and losing an hour to relearning that live is what this
    line exists to prevent. Then reports the branch's relationship to the
    world — already merged (stranded on finished work), base drift
    (origin/main has moved since the fork point), unpushed commits (work that
    exists only on this disk), branch age (last commit N days ago), and files
    that exist in no commit on any branch (the same unrecoverable-on-cleanup
    check the primary worktree gets, run here too — the file at risk is just
    as often in the linked worktree as in the primary one).
  - **Local main vs origin/main:** divergence in either direction — stranded
    local commits or a stale local tip. Auto-fast-forwards when safe.
  - **Deploy drift:** whether merged work is actually live (optional,
    requires config — see [Deploy drift](#deploy-drift)).
  - **Worktree hygiene sweep:** merged worktrees safe to remove, abandoned
    branches quietly aging, missing directories, detached HEADs.
  - **Open PRs and CI status:** every open PR with CI rollup (green, failing,
    pending) and review state (approved, changes requested, needs review).
    The session's own branch is marked. Zero config — derived from the git
    remote via `gh`. Silently skipped if `gh` isn't installed.
- **`fleet.sh`** — how many other Claude and Codex sessions are live in this repo *and its
  worktrees*, how old they are, whether one is in your exact directory, which
  subagents are editing here without a session of their own, whether other
  agent CLIs (Gemini, aider, and others) are competing for the same machine, and
  what the last session here was called.
- **`pressure.sh`** — load per core, swap in use, disk headroom, and which dev
  servers are already listening.

Nothing boots a build tool, and nothing touches the network except the optional
deploy-drift check and the PR/CI lookup (via `gh`, when installed). On a busy
8-core laptop — the slow case, not the quiet one — the full suite runs in
~2 seconds.

## Quiet by default

The hard part is not gathering signals — it is not drowning you in them. A
correct warning that prints identically at every session start stops carrying
information: being told the same true thing on day 15 as on day 1 gives you no
reason to act. Four checks reporting healthy state every session would
industrialise that problem.

**So a signal speaks only when it would change a decision.**

| Signal | Silent | Speaks |
|---|---|---|
| Load | 4 on 8 cores | ≥1.5× cores |
| Swap | none | ≥2GB in use |
| Disk | 60% used | ≥90% used, or <20GB free |
| Sessions in this repo | just you | any other live one |
| Last session | none recorded | a title exists |
| Session worktree location | never silent | states primary-worktree consequences, or the linked-worktree lock + `ExitWorktree` escape hatch — whichever applies |
| Session worktree merged | not merged | content already in main |
| Session worktree drift | up to date | origin/main ahead of fork point |
| Unpushed commits | all pushed | commits only on disk |
| Branch age | recent | ≥3 days since last commit |
| Orphan files (primary or session worktree) | none | a file exists in no commit on any branch |
| Elixir/Ash worktree deps | `deps/` present | `deps/` missing — `mix deps.get` needed |
| Open PRs | none, or `gh` unavailable | any open PR exists |

Silence is the default and the feature. A check that reports nothing is
reporting something: *nothing here needs your attention.* Every threshold is
overridable — see [Tuning](#tuning).

The one exception is deploy drift, which never stays silent — see below.

## Example output

```
Ash domains (4):
  MyApp.Accounts (3 resources) — Users, sessions, and API tokens.
  MyApp.Billing (5 resources) — Subscriptions, invoices, and usage metering.
```

```
Dev environment health (SessionStart):
Primary worktree: /Users/you/Dev/myapp
- THIS SESSION IS IN THE PRIMARY WORKTREE — the shared checkout for the repo.
  Edits here are not isolated: uncommitted changes are inherited by every
  session that arrives after this one, with no indication of ownership. A
  branch switch from any concurrent session moves the checked-out ref silently
  — commits land on the wrong branch without warning. If this worktree is left
  on a feature branch, the repo-wide merge path stays blocked until someone
  returns it to main.
- On main. Modified: 0, untracked: 0.
- Production is up to date with origin/main (a1b2c3d).
Worktrees: 3 total (2 besides primary).
- 1 worktree(s) already fully merged into origin/main and safe to remove
  (git worktree remove): fix/old-thing
Open PRs: 2
  #42 feat/new-thing -- CI green, approved <- this session
  #43 fix/edge-case -- CI failing (1/4), no reviews
```

```
Dev environment health (SessionStart):
Primary worktree: /Users/you/Dev/myapp
- On main. Modified: 0, untracked: 0.
- Production is up to date with origin/main (a1b2c3d).
This session's worktree: /Users/you/Dev/myapp/.worktrees/feat-new-thing
- THIS SESSION IS LOCKED TO THIS WORKTREE — Claude Code confines a session to
  whatever worktree it started in, so it cannot directly create, enter, or cd
  into another one. The one way out is ExitWorktree, which returns to the
  primary worktree and unlocks the session — the conversation is preserved.
- Branch: feat/new-thing. Modified: 2, untracked: 1.
- origin/main is 5 commit(s) ahead of this branch's fork point — base has
  drifted.
- No remote tracking branch. 3 commit(s) exist only on this disk.
Worktrees: 3 total (2 besides primary).
Open PRs: 1
  #42 feat/new-thing -- CI green, no reviews <- this session
```

```
Session fleet (SessionStart):
- 3 other Claude session(s) active in this repo family in the last 15 min:
  myapp (2), myapp/.worktrees/dependabot-catchall. Oldest has been running
  1h50m.
- COLLISION RISK: one of them is working in this same tree, not just this repo
  family. This tree has 4 file(s) modified or untracked right now — if either
  side commits or resets first, the other's changes are what's exposed. Check
  before editing shared files, and do not assume a clean tree stays clean.
- Last session in this directory: "Fix accessibility issue" (ended 36m ago).
```

```
Machine pressure (SessionStart):
- MACHINE OVERSUBSCRIBED: load 18.7 on 8 cores (2.3x). More parallel agents
  or test runs will slow everything already running rather than finish sooner.
- Local servers already listening: node :3000 postgres :5432. Check before
  starting another — the port may be taken by a session you cannot see.
```

"Repo family" means the repo **and every one of its linked worktrees** — they
are one work stream, so a session in `.worktrees/foo` is a neighbour, not a
stranger. COLLISION RISK is scoped narrower, to the **same working tree**:
two sessions in `/repo` and `/repo/apps/web` are the same checkout even
though they're in different directories, and a `checkout`, `reset` or
`clean` in one is exactly as destructive to the other's uncommitted work. A
session in a different linked worktree doesn't carry that exposure — that's
the entire point of a worktree — so it stays in the family count, not the
collision line.

## Install

### Claude Code

```
/plugin marketplace add matthewmathistrellys/scry
/plugin install scry
```

That is the whole install. The hooks register themselves and run from the
plugin's own directory, so updating is `/plugin update scry` — there are no
copies on your machine to keep in sync.

### Codex

```sh
codex plugin marketplace add matthewmathistrellys/scry
codex plugin add scry@scry
```

Review and trust the four bundled hooks when Codex asks. Codex deliberately
does not run newly installed, non-managed plugin hooks until their definitions
have been trusted. Updating the marketplace snapshot and reinstalling refreshes
the cached plugin:

```sh
codex plugin marketplace upgrade scry
codex plugin add scry@scry
```

The Codex package uses `.codex-plugin/plugin.json`; Claude uses
`.claude-plugin/plugin.json`. Both discover the same `hooks/hooks.json`, skill,
scripts, and scanners, so there is no copied implementation to drift.

All four checks are worth having everywhere, not just in the repos you
remembered to wire up: `fleet.sh` and `pressure.sh` are about the machine, and
every check degrades to silence where it doesn't apply. `architecture.sh` falls
back to a directory listing, `health.sh` skips a directory that isn't a git
repo, and `fleet.sh` says nothing when you're the only session.

To try the Claude package without installing:

```
claude --plugin-dir /path/to/scry
```

<details>
<summary>Manual install, without the plugin system</summary>

Copy the four scripts plus `scanners/` into `~/.claude/hooks/`, `chmod +x` them
— a non-executable hook is a **silent no-op** — and wire them into the
`SessionStart` block of `~/.claude/settings.json`:

```json
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          { "type": "command", "command": "~/.claude/hooks/architecture.sh" },
          { "type": "command", "command": "~/.claude/hooks/health.sh", "timeout": 60 },
          { "type": "command", "command": "~/.claude/hooks/fleet.sh" },
          { "type": "command", "command": "~/.claude/hooks/pressure.sh" }
        ]
      }
    ]
  }
}
```

This works, but the scripts are copies: a fix in the repo does not reach the
running hook until you copy it again, and nothing reports the drift.
</details>

`architecture.sh` keeps `scanners/` beside it and resolves the scanner relative
to its own location, so the two move together. `health.sh` always resolves to
the **primary** worktree via `git-common-dir`, regardless of which worktree or
subdirectory the session started in, and additionally reports the session's own
worktree health when it differs from the primary. `fleet.sh` reads the common
`SessionStart` JSON payload on stdin to learn its own session id, cwd, and
transcript path, so it never reports itself as a collision. Claude activity is
derived from recent metadata under `~/.claude/projects`; Codex activity is
derived from only the first `session_meta` record and mtime of rollouts under
`${CODEX_HOME:-~/.codex}/sessions`. Scry does not inspect Codex conversation
content.

All four emit the `hookSpecificOutput.additionalContext` envelope supported by
both clients. Each exits `0` unconditionally: a session-start hook that fails,
or that hangs, is worse than one that skips.

## Deploy drift (optional)

`health.sh` can also report whether **merged work is actually live** — the one
thing every other check misses, because they all compare git refs to other git
refs and never ask what production is running.

It is off until configured. Create `.claude/scry.env` in the project:

```
SCRY_HEALTH_URL=https://your-app.example.com/health
SCRY_HEALTH_SHA_FIELD=git_sha        # optional, this is the default
```

The file is parsed as plain `KEY=VALUE` and never sourced, so a config file
cannot execute anything. `SCRY_HEALTH_URL` in the environment wins over the
file. Your app must expose the commit it is serving as JSON at that URL.

**It never stays silent.** No URL configured, offline, endpoint down,
unparseable response, or a commit this repo has never seen: each says so
explicitly as `Deploy state UNKNOWN`, with the reason. A check that quietly
reports nothing teaches you it looked and found nothing wrong, which is worse
than having no check. The request times out after 3 seconds.

## PR and CI status (zero config)

When `gh` (the GitHub CLI) is installed and authenticated, `health.sh` lists
every open PR in the repo with a one-line summary: PR number, branch, CI
rollup, and review state. The session's own branch is marked `<- this session`.

No configuration needed — the git remote provides the repo. If `gh` isn't
installed or authentication fails, the block is silently skipped (fails open).

## Tuning

Every threshold is an environment variable. Defaults are set where the number
starts changing a decision.

| Variable | Default | Controls |
|---|---|---|
| `SCRY_FLEET_ACTIVE_MINUTES` | `15` | how recently a session must have written to count as live |
| `SCRY_LOAD_PER_CORE_WARN` | `1.5` | load-per-core before "oversubscribed" |
| `SCRY_SWAP_USED_MB_WARN` | `2048` | swap in use before it's reported |
| `SCRY_DISK_FREE_GB_WARN` | `20` | free-space floor |
| `SCRY_DISK_USED_PCT_WARN` | `90` | used-percentage ceiling |

Raising a threshold buys silence. Lowering one buys warning. Neither changes
what is measured.

## Design notes

**Subagents count.** A subagent is not a session, but it *is* a concurrent
writer, and it does not necessarily work where its parent lives — a session in
a repo root routinely dispatches one into a worktree. Counting only top-level
transcripts therefore reports an actively-edited directory as empty. Claude
subagents are attributed using recent cwd metadata from their transcript tail;
Codex subagents are attributed using their first `session_meta` record. The
useful question is *has another writer been active here*, not merely how many
top-level sessions exist.

**Titles, never content.** `fleet.sh` reports what the last session in a
directory was *called*, never what it said or concluded. A title reads as a
label. A summary of a session you cannot see reads as **current** when it may
have been reversed an hour later in another session you also cannot see, and
acting on stale conclusions is worse than having no context. If you want the
detail, ask for it in-session, so it arrives as something you went and got
rather than something you were handed as fact.

**Merged means content, not commits.** Whether a branch is merged is decided by
ancestry *and* patch-id equivalence, so a squash or rebase merge — which
rewrites SHAs and hides from `git branch --merged` — is still recognised.
Deciding it on ancestry alone files finished work under "possibly abandoned",
and a review list full of false alarms is a list you learn to skim.

**Advisory, never blocking.** Blocking an edit is a policy opinion —
trunk-based, work-in-linked-worktrees — that belongs in the project enforcing
it, not in the tool everyone installs to *see* their repo. Scry reports facts
and stops. What to do about four sessions in one directory is yours to decide.

**Projects consequences, never actions.** When signals combine into something
worth a line, that line states the mechanical, verifiable consequence of the
current state — "commits land on the wrong branch without warning" — never a
recommendation like "consider a worktree." A prediction of what *will* happen
is a guess dressed as sight; a statement of what *is* exposed right now is a
fact, and a name is the difference between scrying and giving orders.

**Zero config where possible.** Deploy drift requires configuration because
Scry can't guess where your app lives. PR/CI status requires none — the git
remote is already there. The principle: if the information is derivable from
what's already in the repo, don't ask the user to configure it.

**Fails open.** Every hook exits 0 unconditionally. A session-start hook that
blocks or hangs is worse than one that skips. Optional features (`gh` for
PR/CI, `SCRY_HEALTH_URL` for deploy drift) degrade to silence, never to
errors.

## License

MIT
