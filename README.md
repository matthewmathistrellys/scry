# scry

[Claude Code](https://claude.com/claude-code) `SessionStart` checks that give
a fresh agent session — or a fresh you — the state of the world the instant it
starts, instead of making it dig or making you remember to ask.

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
  directory layout.
- **`health.sh`** — is the main worktree on `main`? Is it dirty? Has local
  `main` drifted from `origin/main`? Are any worktrees already merged and safe
  to delete, or unmerged and quietly aging? Optionally, is merged work actually
  deployed? When the primary worktree is *off* `main` it also reports how long
  it has been parked, whether the branch is already merged, and how many files
  exist in no commit on any ref — the ones a `checkout`, `reset` or `clean`
  would destroy for good.
- **`fleet.sh`** — how many other sessions are live in this repo *and its
  worktrees*, how old they are, whether one is in your exact directory, which
  subagents are editing here without a session of their own, whether other
  agent CLIs (Codex, Gemini, aider) are competing for the same machine, and
  what the last session here was called.
- **`pressure.sh`** — load per core, swap in use, disk headroom, and which dev
  servers are already listening.

Nothing boots a build tool, and nothing touches the network except the optional
deploy-drift check. `architecture.sh` is text parsing, `health.sh` is plain
`git`, and `fleet.sh` and `pressure.sh` read local files and `sysctl`. On a
busy 8-core laptop — the slow case, not the quiet one — `architecture.sh` runs
in ~115ms, `pressure.sh` ~190ms, `fleet.sh` ~390ms.

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
- On main. Modified: 0, untracked: 0.
Worktrees: 6 total (5 besides primary).
- 2 worktree(s) already fully merged into origin/main and safe to remove (git worktree remove): fix/typo, feat/old-thing
```

```
Session fleet (SessionStart):
- 3 other Claude session(s) active in this repo family in the last 15 min: myapp (2), myapp/.worktrees/dependabot-catchall. Oldest has been running 1h50m.
- COLLISION RISK: one of them is working in this same tree, not just this repo family. This tree has 4 file(s) modified or untracked right now — if either side commits or resets first, the other's changes are what's exposed. Check before editing shared files, and do not assume a clean tree stays clean.
- Last session in this directory: "Fix accessibility issue" (ended 36m ago).
```

```
Machine pressure (SessionStart):
- MACHINE OVERSUBSCRIBED: load 18.7 on 8 cores (2.3x). More parallel agents or test runs will slow everything already running rather than finish sooner.
- Local servers already listening: node :3000 postgres :5432. Check before starting another — the port may be taken by a session you cannot see.
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

```
/plugin marketplace add matthewmathistrellys/scry
/plugin install scry
```

That is the whole install. The hooks register themselves and run from the
plugin's own directory, so updating is `/plugin update scry` — there are no
copies on your machine to keep in sync.

All four checks are worth having everywhere, not just in the repos you
remembered to wire up: `fleet.sh` and `pressure.sh` are about the machine, and
every check degrades to silence where it doesn't apply. `architecture.sh` falls
back to a directory listing, `health.sh` skips a directory that isn't a git
repo, and `fleet.sh` says nothing when you're the only session.

To try it without installing:

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
subdirectory the session started in. `fleet.sh` reads the `SessionStart` JSON
payload on stdin to learn its own session id, so it never reports itself as a
collision.

All four emit the `hookSpecificOutput.additionalContext` envelope Claude Code's
`SessionStart` event expects — plain stdout doesn't reliably reach the model,
only this does. Each exits `0` unconditionally: a session-start hook that
fails, or that hangs, is worse than one that skips.

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
transcripts therefore reports an actively-edited directory as empty. Subagent
transcripts are read too, and attributed by the cwd they record for themselves,
never by their parent's, using every directory touched recently rather than
just the latest: the useful question is *has it been editing here*, not *where
is it standing now*.

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
current state — "this tree has 4 files exposed if either side commits or
resets first" — never a recommendation like "consider a worktree." A
prediction of what *will* happen is a guess dressed as sight; a statement of
what *is* exposed right now is a fact, and a name is the difference between
scrying and giving orders.

## License

MIT
