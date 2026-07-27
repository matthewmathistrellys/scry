# ash_scry

[Claude Code](https://claude.com/claude-code) `SessionStart` hooks that
give a fresh agent session — or a fresh you — the state of the project
the instant it starts, instead of making it dig or making you remember
to ask.

A session is told its working directory and a git snapshot, and nothing
else. Not what else is running, not what the last session was doing, not
what the machine is carrying. Four independent checks fill that in —
install any or all:

| | Answers | Scope |
|---|---|---|
| **`architecture.sh`** | What *is* this codebase? | dispatches to `scanners/` by stack |
| **`health.sh`** | Is this repo in good shape? | git + worktrees, language-agnostic |
| **`fleet.sh`** | What else is happening *right now*? | other sessions and agents |
| **`pressure.sh`** | What shape is this machine in? | load, swap, disk, open ports |

- **`architecture.sh`** — a one-line map of your Ash domains (module,
  resource count, first sentence of `@moduledoc`). It is a *dispatcher*,
  not a language scanner: it detects the stack and hands off to
  `scanners/elixir_ash.py`, with Python and TypeScript to follow. The
  language name lives on the scanner so one hook, wired once, works in
  every repo including a polyglot tree.
- **`health.sh`** — is the main worktree actually on `main`? Is it
  dirty? Has local `main` drifted from `origin/main`? Are any worktrees
  already merged and safe to delete, or unmerged and quietly abandoned
  for days? And when the main worktree *isn't* on `main`, it reports
  what that is costing you — see [Receipts](#receipts).
- **`fleet.sh`** — how many other sessions are live in this repo *and
  its worktrees*, how old they are, whether one is in your exact
  directory, **which subagents are editing here without a session of
  their own**, whether other agent CLIs (Codex, Gemini, aider) are
  competing for the same machine, and what the last session here was
  doing. See [The output budget](#the-output-budget),
  [Subagents count](#subagents-count), and
  [Why fleet.sh emits titles only](#why-fleetsh-emits-titles-only).
- **`pressure.sh`** — load per core, swap in use, disk headroom, and
  which dev servers are already listening. A session cannot see the cost
  of its own parallelism; this is that cost.

Nothing here boots `mix`, and nothing touches the network except the
optional deploy-drift check. `architecture.sh` is pure text parsing
(grep + AST-free regex), `health.sh` is plain `git`, and `fleet.sh` and
`pressure.sh` read local files and `sysctl`.

Measured on a busy 8-core laptop (load 18, five sessions in the target
repo) — the slowest case, not the quiet one:

```
architecture.sh   115ms
pressure.sh       188ms
fleet.sh          386ms
```

## The output budget

The hard part is not gathering signals — it is not drowning you in them.

`health.sh`'s own comments record the failure this avoids: a correct
warning printed identically at every session start for 15 days while the
condition got worse, because being told the same true thing on day 15 as
on day 1 gives you no reason to act. Four checks all reporting healthy
state every session would industrialise that problem.

**So a signal speaks only when it would change a decision.**

| Signal | Silent | Speaks |
|---|---|---|
| Load | 4 on 8 cores | ≥1.5× cores |
| Swap | none | ≥2GB in use |
| Disk | 60% used | ≥90% used, or <20GB free |
| Sessions in this repo | just you | any other live one |
| Last session | none recorded | a title exists |

Silence is the default and the feature. A check that reports nothing is
reporting something: *nothing here needs your attention.* Every
threshold above is overridable — see [Tuning](#tuning).

## Example output

`architecture.sh`:
```
Ash domains (4):
  MyApp.Accounts (3 resources) — Users, sessions, and API tokens.
  MyApp.Billing (5 resources) — Subscriptions, invoices, and usage metering.
  MyApp.Catalog (2 resources) — Products and pricing tiers.
  MyApp.Support (1 resource) — Support tickets.
```

`health.sh`:
```
Dev environment health (SessionStart):
Primary worktree: /Users/you/Dev/myapp
- On main. Modified: 0, untracked: 0.
Worktrees: 6 total (5 besides primary).
- 2 worktree(s) already fully merged into origin/main and safe to remove (git worktree remove): fix/typo, feat/old-thing
```

`fleet.sh` — real output from a machine running five sessions in one
repo:
```
Session fleet (SessionStart):
- 5 other Claude session(s) active in this repo family in the last 15 min: myapp (4), myapp/.worktrees/dependabot-catchall. Oldest has been running 1h50m.
- 1 subagent from other sessions is working in this repo family: 1 in this directory. Subagents edit files without a session of their own, so a directory can be under active change with no session in it.
- COLLISION RISK: 4 of them are in THIS exact directory. Check before editing shared files, and do not assume a clean tree stays clean.
- Other agent CLI(s) also running on this machine: codex (running 5d13h).
- Last session in this directory: "Fix coding assistant accessibility issue" (ended 36m ago).
```

"Repo family" means the repo **and every one of its linked worktrees** —
they are one work stream, so a session in `.worktrees/foo` is a
neighbour, not a stranger.

`pressure.sh`:
```
Machine pressure (SessionStart):
- MACHINE OVERSUBSCRIBED: load 18.7 on 8 cores (2.3x). More parallel agents or test runs will slow everything already running rather than finish sooner.
- Swapping: 15171MB of swap in use. RAM is exhausted; expect builds and test runs to be much slower than they look.
- Local servers already listening: node :3000 node :4321 postgres :5432. Check before starting another — the port may be taken by a session you cannot see.
```

## Install

Two ways. **Global** is the one to prefer: `fleet.sh` and `pressure.sh`
are about the machine and the other sessions on it, so they are worth
having in every repo, not just the ones you remembered to wire up.

### Global (recommended)

1. Copy the four hooks plus `scanners/` into `~/.claude/hooks/`.
2. `chmod +x ~/.claude/hooks/*.sh` — a non-executable hook is a **silent
   no-op**, so don't skip this.
3. Wire them into `~/.claude/settings.json` (same `hooks` block as
   below, with `~/.claude/hooks/` paths).

Every check degrades to silence where it doesn't apply:
`architecture.sh` falls back to a directory listing outside an Elixir
app, `health.sh` skips a directory that isn't a git repo, and `fleet.sh`
says nothing when you're the only session. So a global install costs
nothing in repos it has little to say about.

### Per-project

1. Copy `architecture.sh`, `scanners/`, `health.sh`, `fleet.sh` and
   `pressure.sh` into `.claude/hooks/`.
2. `chmod +x .claude/hooks/*.sh`.
3. Wire whichever you want into `.claude/settings.json`:

```json
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          { "type": "command", "command": ".claude/hooks/architecture.sh" },
          { "type": "command", "command": ".claude/hooks/fleet.sh" },
          { "type": "command", "command": ".claude/hooks/pressure.sh" }
        ]
      },
      {
        "hooks": [
          {
            "type": "command",
            "command": "d=\"$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null)\" && f=\"${d%/.git}/.claude/hooks/health.sh\" && [ -x \"$f\" ] && \"$f\" || true",
            "timeout": 60
          }
        ]
      }
    ]
  }
}
```

`architecture.sh` keeps `scanners/` beside it — it resolves the scanner
relative to its own location, so the two move together. `fleet.sh` reads
the `SessionStart` JSON payload on stdin to learn its own session id
(so it never reports itself as a collision) and the session's cwd.

`architecture.sh` walks up from wherever the session starts looking for
the nearest `mix.exs` and scans that project's `lib/`; outside any Elixir
app in a polyglot monorepo, it falls back to a directory layout.
`health.sh` always
resolves to the **primary** worktree via `git-common-dir`, regardless
of which worktree or subdirectory the session actually started in —
that indirection in the command above is what makes that work; a bare
`.claude/hooks/health.sh` would only check whatever worktree happens to
be cwd.

### Deploy drift (optional)

`health.sh` also reports whether **merged work is actually live** — the
one thing every other check here misses, because they all compare git
refs to other git refs and never ask what production is running.

It is off until configured. Create `.claude/scry.env` in the project:

```
SCRY_HEALTH_URL=https://your-app.example.com/health
SCRY_HEALTH_SHA_FIELD=git_sha        # optional, this is the default
```

The file is parsed as plain `KEY=VALUE` and never sourced, so a config
file cannot execute anything. `SCRY_HEALTH_URL` in the environment wins
over the file.

Your app must expose the commit it is serving as JSON at that URL. If
it does not, add it — that is a smaller job than living without this.

**It never stays silent.** No URL configured, offline, endpoint down,
unparseable response, or a commit this repo has never seen: each says
so explicitly as `Deploy state UNKNOWN`, with the reason. A check that
quietly reports nothing teaches you it looked and found nothing wrong,
which is worse than having no check. The request times out after 3
seconds — a session-start hook that hangs is worse than one that skips.

All four hooks emit the `hookSpecificOutput.additionalContext` envelope
Claude Code's SessionStart event expects — plain stdout doesn't
reliably reach the model, only this does. Each exits `0` unconditionally:
a session-start hook that fails, or that hangs, is worse than one that
skips.

## Subagents count

A subagent is not a session, but it **is** a concurrent writer — and it
does not necessarily work where its parent lives. A session in a repo
root routinely dispatches one into a worktree.

Counting only top-level transcripts therefore reports an actively-edited
directory as empty. Found by cross-checking against `/prune-worktrees`,
which guards worktrees by file mtime and correctly held one that
`fleet.sh` called idle: no session had that worktree as its cwd, while
28,682 files in it had been modified within four hours. The writer was a
subagent dispatched from the repo root.

So subagent transcripts are read too, and attributed by **the cwd they
record for themselves**, never by their parent's. Attribution uses every
directory a subagent touched recently rather than just its latest,
because the useful question is *"has it been editing here"* rather than
*"where is it standing now"* — a false warning costs a line of output, a
missed one costs an edit.

Cost is negligible: `stat` on ~1000 transcripts is 2ms, and only the
handful written inside the window are opened at all.

## Why `fleet.sh` emits titles only

`fleet.sh` reports what the last session in a directory was *called*.
It deliberately does not report what that session said, decided, or
concluded.

A title is a label, and it reads as a label — *"Fix coding assistant
accessibility issue"* tells you the neighbourhood without claiming
anything is still true. Content is different. A summary of a session you
cannot see reads as **current** context when it may have been reversed an
hour later in a session you also cannot see, and acting on stale
conclusions is worse than having no context at all. The failure mode is
silent and confident, which is the worst combination available.

This also costs nothing: Claude Code already writes the title into the
transcript itself, so there is no inference, no extra model call, and no
tokens spent. `fleet.sh` reads a bounded tail of the file rather than
parsing megabytes of history.

If you want the detail, ask for it in-session — that way it arrives as
something you went and got, not something you were handed as fact.

## Tuning

Every threshold is an environment variable. Defaults are set where the
number starts changing a decision:

| Variable | Default | Controls |
|---|---|---|
| `SCRY_FLEET_ACTIVE_MINUTES` | `15` | how recently a session must have written to count as live |
| `SCRY_LOAD_PER_CORE_WARN` | `1.5` | load-per-core before "oversubscribed" |
| `SCRY_SWAP_USED_MB_WARN` | `2048` | swap in use before it's reported |
| `SCRY_DISK_FREE_GB_WARN` | `20` | free-space floor |
| `SCRY_DISK_USED_PCT_WARN` | `90` | used-percentage ceiling |

Raising a threshold buys silence. Lowering one buys warning. Neither
changes what is measured.

## Receipts

"The main worktree isn't on main" reads exactly the same on day 15 as on
day 1, which is how a correct warning gets worked around fifteen days
running while the situation quietly gets worse. So when the primary
worktree is off `main`, `health.sh` also reports what it is costing:

```
- WARNING: on branch 'feat/thing', not main. ... Modified: 207, untracked: 3.
- Parked on 'feat/thing' for 15 day(s). This warning has been repeating, unchanged, that whole time.
- This branch's content is ALREADY IN origin/main. Nothing here is pending review — the worktree is simply stranded on finished work.
- EXPOSURE: 92 file(s) here exist in NO commit on ANY branch — checked all 95. A checkout, reset or clean in this worktree destroys them permanently.
```

Three facts, no policy. **Duration** turns a repeated warning into an
escalating one. **Already merged** is the difference between "work in
progress in an odd place" and "stranded on something finished" — and it
uses patch-id equivalence, so a squash or rebase merge, which rewrites
SHAs and hides from `git branch --merged`, is still caught. **Exposure**
is the only number that measures real risk: files present in no commit
on any ref are the ones a `checkout`, `reset` or `clean` destroys for
good.

The exposure scan is bounded by a 3-second budget, because the history
walk is slowest for exactly the never-committed files it is looking for.
If it runs out of time it says how far it got (`checked 40 of 300`)
rather than quietly reporting a number that means less than it appears
to.

This hook stays advisory on purpose. Blocking the edit is a policy
opinion — trunk-based, work-in-linked-worktrees — that belongs in the
project enforcing it, not in the tool everyone installs to *see* their
repo. Scry's job is to make the cost impossible to misread.

## Why

Ash's `@moduledoc` convention already documents domain intent well;
the gap is that nothing surfaces it automatically. `mix docs` is too
heavy for a session-start glance, and asking an agent to `grep` around
for domain boundaries burns a round-trip every single session.

Deploy drift is in here for the same reason. On 2026-07-18 four PRs
sat merged and undeployed for about eight hours on a project using
these hooks, while work continued on top of them and the person who
needed the fix kept hitting the bug it fixed. Nothing noticed, because
nothing was looking: merging and deploying are deliberately separate
events, and separate events need someone watching the gap between them.

The health check exists because the alternative — a stray branch
sitting in your main worktree, silently accumulating uncommitted work
for days, with dozens of already-merged or abandoned worktrees nobody
ever cleans up — is a real failure mode, not a hypothetical one. A
warning that only prints to a terminal you don't read fixes nothing;
telling the agent directly, every session, is what actually closes the
gap.

`fleet.sh` exists because that branch sprawl has a cause, and the cause
is structural rather than careless. On 2026-07-26 one laptop was running
six Claude sessions and a Codex process, five of them in the same repo,
three of them more than 36 hours old. **Every one believed it was
alone.** No session can yield to another it cannot see, so parallel
sessions fan work out faster than anything lands it, and the branches
pile up downstream. Reporting the collision at session start is the
cheapest point of intervention there is.

`pressure.sh` exists because a session cannot observe the cost of its
own parallelism. It dispatches eight agents, starts test runs and dev
servers, and sees none of the contention that follows — so the tenth
parallel task looks exactly as free as the first. That same laptop was
carrying load 18.7 across 8 cores and 15GB of swap, and nothing running
on it could tell. A number nobody can see cannot inform a decision.

Neither is a policy. Both report facts and stop; what to do about four
sessions in one directory is yours to decide.

## License

MIT
