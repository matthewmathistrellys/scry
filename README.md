# ash_scry

[Claude Code](https://claude.com/claude-code) `SessionStart` hooks that
give a fresh agent session — or a fresh you — the state of the project
the instant it starts, instead of making it dig or making you remember
to ask. Two independent checks, install either or both:

- **`scry.sh`** — context: a one-line architectural map of your Ash
  domains (module, resource count, first sentence of `@moduledoc`).
  Elixir/Ash-specific today; the shape is meant to grow sibling
  scanners for other stacks (Python, TypeScript) as they're written —
  same idea, different language.
- **`health.sh`** — health: git/worktree hygiene, language-agnostic.
  Is the main worktree actually on `main`? Is it dirty? Has local
  `main` drifted from `origin/main`? Are any of your other worktrees
  already merged and safe to delete, or unmerged and quietly
  abandoned for days with nobody watching them? And when the main
  worktree *isn't* on `main`, it reports what that is costing you —
  see [Receipts](#receipts).

No `mix`/BEAM boot required for `scry.sh` — pure text parsing (grep +
AST-free regex over your `.ex` files) — and `health.sh` is plain
`git`, so both run in well under a second.

## Example output

`scry.sh`:
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

## Install

1. Copy `scry.sh`, `scry_domains.py`, and `health.sh` into your
   project, e.g. `.claude/hooks/`.
2. Make the hooks executable: `chmod +x .claude/hooks/scry.sh
   .claude/hooks/health.sh` — a non-executable hook is a silent no-op,
   so don't skip this.
3. Wire whichever you want into `.claude/settings.json`:

```json
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          { "type": "command", "command": ".claude/hooks/scry.sh" }
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

`scry.sh` walks up from wherever the session starts looking for the
nearest `mix.exs` and scans that project's `lib/`; outside any Elixir
app in a polyglot monorepo, it's a silent no-op. `health.sh` always
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

Both hooks emit the `hookSpecificOutput.additionalContext` envelope
Claude Code's SessionStart event expects — plain stdout doesn't
reliably reach the model, only this does.

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

## License

MIT
