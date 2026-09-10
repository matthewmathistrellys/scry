# scry

[Claude Code](https://claude.com/claude-code) and
[Codex](https://developers.openai.com/codex/) `SessionStart` checks that give a
fresh agent session — or a fresh you — the state of the world the instant it
starts, instead of making it dig or making you remember to ask. Both clients
run the same shared scripts from this repository; only their package manifests
are client-specific.

A session is told its working directory and a git snapshot, and nothing else.
Not what else is running, not what the last session was doing, not what the
machine is carrying. Independent checks and advisories fill that in.

| | Answers |
|---|---|
| **`architecture.sh`** | What *is* this codebase? |
| **`stack.sh`** | What is it built *on*? |
| **`elixir_build_guard.sh`** | Is this command about to cost me 20 minutes? |
| **`health.sh`** | Is this repo in good shape? |
| **`fleet.sh`** | What else is happening *right now*? |
| **`pressure.sh`** | What shape is this machine in? |
| **`main_drift_advisory.sh`** | Is the tree this subagent was handed the current one? |
| **`session_disposal_advisory.sh`** | What is this session leaving behind? |
| **`cache_handoff_monitor.sh`** | Is this session about to lose its prompt cache, and is anything written down? |
| **Markdown trust** | What goes wrong when repository prose is mistaken for authority? |

- **`architecture.sh`** — a map of the codebase. It is a *dispatcher*, not a
  language scanner: it detects the stack and hands off to `scanners/`
  (Elixir/Ash today, others to follow), so one hook works in every repo
  including a polyglot tree. It searches **down from the repo root**, not up
  from the session's directory — an upward walk only finds a project marker at
  or above cwd, so a monorepo whose `mix.exs` files live in `apps/*` reported a
  bare folder list and the domain map went silently missing, in exactly the
  repo that most needed it. Every mix project found is reported, each with its
  domains, resource counts, and `@moduledoc` first sentences.

  The search prunes vendored trees as it walks rather than filtering after —
  a naive repo-wide search of a real monorepo returned **2,512** `mix.exs`
  files, almost all under `deps/`, which would have reported Ash's own domains
  as the application's. Excluding `deps/`, `_build/` and `node_modules/` left
  62; excluding `.worktrees/` (~20 checkouts of the same projects) left the
  real 3. Pruned, it runs in ~240ms where a naive `find` takes ~2s.

  Outside a recognised project it falls back to a directory layout. The
  Elixir/Ash scanner also flags a worktree whose dependencies were never
  fetched (no `deps/` directory) — the fastest way to turn a fresh worktree's
  first `mix compile` from a bare `Mix.Error` into a heads-up instead.
- **`stack.sh`** — the stack the system actually runs on, read from live
  config and never from documentation: managed-database provider, hosting
  units, runtime and frameworks, and the third-party services that are wired
  up. It exists because an instruction file described the app's database as
  Fly.io managed Postgres months after it became Neon, and a session believed
  it. Two rules make it trustworthy where prose was not:
  - **Role-bound, never name-counted.** A provider is reported for a role only
    when the variable owning that role resolves to it. Counting brand names
    reproduces the original bug instead of catching it — in the repo that
    produced it, `.flycast` outnumbered `neon.tech` 25 files to 11 while the
    database was unambiguously Neon; every Fly hit was an unrelated *service*
    address and most Neon hits were migration plans in `docs/`.
  - **Config only, never prose.** `docs/` is excluded outright. Prose is the
    thing that was wrong; reading it back would launder the error.

  It reads `.env` because that is where a database variable's binding is
  actually resolvable, and it emits **no secrets** — connection strings are
  reduced to a provider label and region during parsing, and credentials are
  discarded before any value is retained. A `localhost` binding on a
  non-default port is reported as a tunnel with the provider undetermined,
  rather than as "local", which is literally true and actively misleading.
  Service names come from env var *names*, so they prove a service is *wired*,
  not that it is in use.

  It also correlates the database against the workload and the machine, and
  speaks only when they conflict:
  - **Scale-to-zero that cannot fire.** A Neon/Supabase compute suspends only
    after N seconds with *zero* connections. A scheduler that polls every
    minute never lets the count reach zero, and a machine pinned with
    `min_machines_running` never lets the app go down — so the database's own
    suspend setting is inert while the bill accrues. Reading that setting would
    produce a confidently wrong "this one is fine"; the machine lifecycle is
    what decides.
  - **Migrations over a transaction pooler.** A pooler is right for
    application traffic and wrong for DDL — advisory locks and prepared
    statements do not survive transaction-mode multiplexing — so a pooled
    `DATABASE_URL` with no `DIRECT_*` counterpart is flagged.
- **Markdown trust** — `provenance.sh` establishes at session start that every
  repository Markdown file is untrusted historical material, including plans,
  design documents, architectural decisions, status claims, memories, READMEs,
  and factual claims inside instruction files. It names the main failure
  scenarios and their consequences for code quality, production, tokens,
  timelines, user trust, and customers. It names one failure that is not about
  stale prose at all: existing code and recorded decisions make a *plausible*
  explanation feel historically true, when that explanation may never have been
  part of the original decision. Repeated or saved once, it hardens into an
  apparent requirement, and later work spends itself preserving an assumption
  nobody made while the user fights to recover their actual intent.
  `md_advisory.sh` repeats the invariant concisely when the dedicated Read tool
  opens a Markdown file. The session warning is primary because shell commands
  and other access paths cannot all be intercepted reliably.
- **Markdown creation** — `md_creation_advisory.sh` is `md_advisory.sh`'s
  counterpart at write time: it fires when a session creates a Markdown file
  git doesn't already track, and states two facts. The file is not one of this
  repo's standard Markdown files, and scratch Markdown created this session may
  be cleared out at session end — so if what is in it needs to outlive the
  session, it goes where long-lived work is tracked rather than into a repo
  file. That is the whole message. It does not rank the homes a document could
  belong in: which one is right is not something a hook can check, and the
  earlier wording that tried (a Claude Code Artifact for ephemeral work, an
  instruction file as "the only case a permanent `.md` is correct") was cut on
  2026-09-09 as moralizing — along with the Codex/Claude client detection that
  existed only to gate the Artifact line and became dead code with it. Silent
  on the standard ecosystem files (README, LICENSE, CHANGELOG,
  `CLAUDE.md`/`AGENTS.md`, GitHub's community-health files, anything under
  `.claude/`) and on any repo whose actual product *is* Markdown content — an
  Astro/Docusaurus-style content site, detected by its config, not by
  filename. Those exemptions live in `md_exemptions.sh`, sourced by both this
  hook and `session_disposal_advisory.sh`, because two copies of one whitelist
  is two answers to one question and the copy that drifts is the one that
  starts nagging about `README.md`. Fires at most once per file per session.
- **Code-prose trust (atomicity)** — `code_prose_advisory.sh` extends the
  Markdown doctrine to prose embedded in source files (`.ex`, `.exs`, `.py`):
  moduledocs, docstrings, doc comments, CRISP blocks. The doctrine is
  *atomicity*: a file's prose may speak only for its own module. A claim that
  reaches wider — other modules, the pipeline, production behavior, "the
  system always/never" — is **stricken**: invalid as evidence no matter how
  authoritative it reads, usable only as a signal of where to investigate.
  Injected on every Read of a source file. Born 2026-08-27, after a stale
  moduledoc claiming "the pipeline is text-only" was re-asserted by four
  consecutive sessions while the disproving sibling module sat two files away.

  It also says what an *accurate* citation still cannot establish. Reading a
  line settles what that line says; its behavior lives in its callers,
  conditions, and consumers. Skip those and a correct quotation becomes a false
  conclusion — then a test that enshrines it, or code that acts on the wrong
  records — and the next reviewer makes the same leap from the same line.
- **Stale-tree advisory (subagents)** — `main_drift_advisory.sh` fires on
  `SubagentStart` and tells the *child* agent that the worktree it was handed
  is behind `origin/main`, by how many commits and how many days, and how to
  read the live tip instead (`git show origin/main:<path>`,
  `git grep <pattern> origin/main`, `git ls-tree -r --name-only origin/main`).
  It exists because none of Scry's other surfaces reach a subagent:
  `SessionStart` output does not propagate into one, and `PreToolUse` /
  `PostToolUse` `additionalContext` on the Agent tool lands in the **parent**.
  `SubagentStart` is the only event whose `additionalContext` is delivered into
  the spawned agent.

  Born 2026-09-09, after subagents dispatched to describe a repo's
  architecture read a checkout whose local `main` was 427 commits and 14 days
  behind, and reported a five-stage pipeline that has six. Nothing errored: the
  sixth stage's files were simply not on disk, `grep` found nothing, and
  **nothing read as non-existence**. That is the sentence the advisory carries,
  because a bare commit count changes no behavior — the identical count was
  already on screen during the incident.

  Narrow on purpose. It speaks only when the agent's worktree is checked out
  on the repo's *default* branch — a feature branch is expected to diverge and
  its author knows what it is based on. It never fetches (`health.sh` already
  fetched at session start; a fetch here would put network latency in front of
  every dispatch) and never mutates: missing refs mean silence, not a fetch.
  Past `SCRY_SUBAGENT_STALE_DAYS` (7) or `SCRY_SUBAGENT_STALE_COMMITS` (50) the
  wording escalates to say plainly that an architecture answer from that tree
  is unreliable — that is the distance at which whole features land. There is
  no `agent_type` exemption: the built-in Explore agent, whose entire output is
  what is and isn't in a tree, is the type *most* exposed to this, not least.
- **Session disposal** — `session_disposal_advisory.sh` reports, at most once
  per session, what the session is leaving behind. Two kinds of leftover, one
  compact note, and it deletes nothing, ever.

  *Worktrees*: how many linked worktrees the repo has, roughly what they
  occupy, how many hold branches already merged into `origin/main` (ancestry
  *and* patch-id, so a squash merge still counts), and the command to clean
  them — `prune-worktrees` where it is on `PATH`, `git worktree remove`
  otherwise. Agent sessions create isolated worktrees and never tear the
  finished ones down; on 2026-09-09 that took a machine to 0 bytes free.

  *Scratch Markdown*: the untracked `.md` files in the session's own working
  tree whose mtime is at or after the session started — the far end of the
  sentence `md_creation_advisory.sh` began when each one appeared. It lists
  them (up to `SCRY_SCRATCH_MD_LIST_MAX`, then a count), says plainly that
  nothing has been deleted, and says that anything worth keeping belongs where
  long-lived work is tracked. It applies the same `md_exemptions.sh` list the
  creation hook uses, so a file exempt at birth is exempt here. "Untracked"
  means what `git status` means by it, `.gitignore` included: a file the repo
  has already decided not to keep is not news, and scanning ignored trees would
  put `node_modules/` under `du`-shaped pressure at the end of every turn.

  **"Created this session" is a fact here, not a guess.** The cutoff is the
  transcript file's birth time — the same metadata the age gate already reads,
  never the transcript's contents. Where a filesystem does not record birth
  time there is no second mechanism: the Markdown half stays silent rather than
  substituting the transcript's *m*time, which records its last write and would
  silently misdate every file in the tree.

  Named `worktree_disposal_advisory.sh` until 2026-09-09, when the Markdown
  half made the old name an undersell.

  **It runs on `Stop`, not `SessionEnd`, and the reason is measured.**
  `SessionEnd` output goes nowhere: Claude Code's own event contract gives it
  "exit code 0 — command completes successfully", with no stdout-to-model and
  no `additionalContext` path, unlike `SessionStart` or `SubagentStart`. A
  probe confirmed it — a `SessionEnd` hook that printed a unique token *and*
  returned it as `additionalContext` demonstrably ran and its token appeared
  zero times in the CLI output and zero times in the transcript, while the same
  probe on `Stop` appeared three times and the model quoted it back. A
  `SessionEnd` version of this hook would be inert. `Stop` costs firing every
  turn, which three gates absorb: `stop_hook_active` (the loop guard — this
  hook's own output makes the model continue, so the next `Stop` must be
  silent), once per session id, and a minimum session age from the transcript
  file's birth time (`SCRY_WORKTREE_REMINDER_MINUTES`, default 45) so a
  reminder about finished work does not arrive on turn one. It then speaks only
  when something is actually reclaimable, or the pile exceeds
  `SCRY_WORKTREE_DISK_MB_WARN` (default 2048).

- **Cache handoff** — `cache_handoff_monitor.sh` (Claude only) asks the
  session, at most once per user message, to write a handoff shortly before
  its prompt cache goes cold. Claude Code's prompt cache has a time-to-live;
  every request refreshes it, and an idle session lets it run out, after which
  the next request re-reads the whole context at the full rate. That idle
  session is also the one most likely to be abandoned with nothing written
  down. One cached request in the last minutes of the warm window buys a
  handoff file the next session can start from.

  Three pieces, two small files of numbers per session:

  *`cache_deadline_statusline.sh`* records when the cache expires. The
  status-line payload is the only place Claude Code reports it
  (`prompt_cache.expires_at`, verified against the 2.1.267 binary on
  2026-09-10), and no plugin can install a status line, so this is a wrapper
  the user points `statusLine.command` at once (see [Install](#install)).
  It keeps `observed_at`, `warm`, `ttl`, `expires_at`, `requests` and passes
  the payload through to the status line that was already there, appending
  one segment to the end of the bar, styled like worktrunk's own (icon, space,
  value): `🔥 43m` in green while warm, `🔥 4m ~150k` in yellow with the
  re-read size inside the last ten minutes, and `❄️ ~150k` in red once it has
  expired. The size is Claude
  Code's own `recache_tokens_if_cold` — what the next request re-reads at the
  full rate if nobody speaks first. Nothing avoids that re-read once the cache
  is cold: a `/compact` sends the same history to write its summary, and
  resuming does too. The only free move is `/clear`, which is why the handoff
  exists and why the new session is handed it (see `fleet.sh`).

  *`cache_handoff_arm.sh`* is a `UserPromptSubmit` hook that writes "armed".
  It is the whole re-arm rule: only a user message starts a new cycle. Tool
  calls, cache hits, monitor notifications, and the handoff itself never do —
  the handoff is a request, a request refreshes the cache, and a monitor that
  re-armed on a warm cache would ask every hour forever with no user in the
  loop (designed with Codex, 2026-09-10). It reads `session_id` and nothing
  else from the payload.

  *`cache_handoff_monitor.sh`* is a plugin monitor (`monitors/monitors.json`):
  a background process Claude Code arms at session start whose every stdout
  line reaches the model as a notification — which is what wakes an idle
  session. It polls the two files with no model calls and, when the session
  is armed and the deadline is within the lead window, prints one line naming
  the expiry time and the path to write the handoff to
  (`~/.claude/scry/handoffs/<repo>/<date>-<session>.md`), then marks the
  cycle `requested` before printing so a restart cannot ask twice. A deadline
  that passes while armed is marked `missed` and said on the *next* user turn
  by the arm hook, never by the monitor: a monitor line wakes the model onto a
  cold cache, which is the exact cost this exists to avoid.

  Bounded: at most one request per user message, none without one.
  `SCRY_CACHE_HANDOFF=0` switches it off. It writes the handoff to nothing
  itself; the session does, with its own context.
- **Scale** — `scale_advisory.sh` speaks on first contact with a source file
  that is both large *and* actively worked, whether that contact is a Read or
  an Edit. It reports the file's length, its churn, and — on a Read of a file
  over 2000 lines — exactly how many lines the Read never returned and did not
  mark as missing. Then it names what goes wrong silently at that size: a
  helper you are about to add may already exist hundreds of lines away, and the
  duplicate compiles, passes review, and diverges later in production; and
  behavior you never read is behavior you can still break, because a match,
  guard or default further down may depend on the shape you just changed. The
  remedy asked for is seconds long — grep *this file* for the name before
  introducing it — and the advisory explicitly says to finish the assigned work
  rather than turn the note into a refactor.

  The trigger is deliberately **not** raw line count. Size alone flags the
  wrong files: the largest source file in the codebase this was built for
  (3026 lines) is a flat declarative field registry that must not be split,
  while the file that actually costs the most (2498 lines, 20 commits in six
  months) is well factored and scores as unremarkable on any density measure.
  Size is therefore gated on **churn** — a large file nobody touches costs
  nothing, and warning about it only teaches sessions to skim every advisory.
  The size threshold self-calibrates to the repository (p95 of that language's
  own file-length distribution, clamped to 400–1200) rather than importing a
  folklore constant, so it neither nags a codebase of small files nor goes
  silent as one bloats. Fires at most once per file per session: the risk is
  first contact, not sustained work in a file already being reasoned about.
  Declaration-only files are told they are legitimately long.
- **Prose drift** — a `check_prose_drift` advisory inside `elixir_advisory.sh`:
  when an Edit changes function-level code (`def`/`defp` in the edited region)
  without touching any prose marker, and the file carries a prose block, one
  advisory notes that the prose was left behind and asks for a conscious look.
  Deliberately skippable — pressure toward good behavior, not a gate — and
  quiet by default: full-file Writes, body-only tweaks, prose-touching edits,
  and files with no prose block all stay silent.
- **`agent_model_guard.sh`** — one rule on every surface that dispatches work
  to a model — `Agent`, `Workflow`, and a metered agent CLI run through `Bash`:
  **the model must be chosen.** A blank model is not "the default" — it
  silently inherits whatever the parent session runs on, invisibly at the call
  site, and a fan-out multiplies it. On 2026-08-31 that consumed most of a
  week's premium allowance in a single session: four verifier agents and a
  builder, none naming a model, none needing the model they got. Nobody typed
  the expensive model even once. They typed nothing, and got it anyway.

  It is **not against any model**. A cheaper agent asking the strongest model a
  hard question is legitimate and useful, and a premium model named
  deliberately — including on every agent in a workflow — passes without
  objection. Only the *absence of a decision* is blocked. It also refuses to
  name which model to pick: model rosters age out, the rule does not, so the
  instruction is to choose the cheapest one that can actually do the job and
  leave that judgement where it belongs. A `fork`, which inherits by design and
  ignores the override, gets one speed bump and then proceeds.

  Afterwards, a `PostToolUse` note reports what a premium dispatch costs —
  that it draws on a separate, much smaller allowance, that a single bounded
  question is cheap while duration and breadth are not, and explicitly that
  nothing is wrong, because the choice was deliberate and that was the point.
  Every internal error fails **open**: an unparseable payload, an unwritable
  state dir, a missing `python3` all allow the dispatch, because a cost guard
  that wedges a session costs more than the tokens it saves.
  `SCRY_PREMIUM_MODELS` (default `fable`) sets which models draw the note.

  The same rule reaches one layer further out, to an agent CLI invoked through
  `Bash`. With no model flag the CLI's own config default answers — a value set
  once, months before the call, invisible at the call site, and usually the
  strongest model the account can reach. On 2026-09-06 an overnight session ran
  seventeen design reviews that way, every one on the premium config default,
  draining the month's budget and locking the account out mid-day. A `codex`
  call naming no model is denied; `-m`, `--model`, `--model=` and `-c model=`
  all count as naming one, and management verbs that run no inference
  (`login`, `--help`, `mcp`, `doctor`, …) pass untouched.

  The command is **lexed and split on shell separators**, not grepped, because
  a whole-string search gets it wrong in both directions: `mkdir -m 755 x &&
  codex exec` looks pinned and is not, while `echo "run codex now"` looks like
  a call and is not. Splitting on separators keeps a `-m` from vouching for a
  command it does not belong to, and quoted separators stay inside their token
  so an `&&` in a prompt does not split the call around it. A command shlex
  cannot read fails **open**, like everything else here.

  Which CLIs are metered is configuration, never a hardcoded roster — a tool
  that bills today may not tomorrow, and one this file has never heard of may
  bill the most. `SCRY_METERED_CLIS` (default `codex`) sets the list,
  `SCRY_METERED_CLI_FREE` extends the no-inference verbs, and
  `SCRY_METERED_CLI_GUARD=0` switches the check off.

  One more thing on the same surface, and it is a **speed bump rather than a
  block**: `claude -p` bypasses the subscription login by design and reads
  `ANTHROPIC_API_KEY`, so with a key in the environment the run is metered per
  token against that API account while the subscription pays for none of it.
  Nothing in the command says so and nothing in the session shows it — people
  have found out at $447 and at $1,818, and consumed API credit is not
  refundable. Billing the API deliberately is a legitimate thing to want, so the
  first attempt is refused with that stated and the same command repeated within
  five minutes goes through untouched. `SCRY_API_BILLING_GUARD=0` silences it.

- **`elixir_build_guard.sh`** — a `PreToolUse` speed bump in front of the
  commands that throw away compiled Elixir artifacts: `mix compile --force`,
  `mix deps.compile --force`, `mix clean --deps`, `rm -rf _build`, `rm -rf
  deps`. None of these are destructive — nothing is corrupted and nothing needs
  repairing — but on an Ash project with 140+ dependencies a cold rebuild costs
  tens of minutes and saturates the machine while it runs.

  Because the harm is time rather than damage, it is a **two-strike gate**, not
  a block: the first attempt is denied with the cost stated and the cheaper
  options named; retrying within the window (default 5 minutes) lets it through
  untouched. The window is keyed on the project and the *intent*, never the raw
  command string — an agent's retry is almost never byte-identical, so a string
  key would deny the retry a second time and produce exactly the thrash the
  design exists to avoid. A reflex becomes a decision, and nothing
  is ever truly blocked. A plain `mix compile` — which is already incremental,
  and is the thing we want people running — never fires it.

  Fails open everywhere: not Bash, not an Elixir project, malformed payload or
  an unwritable state directory all allow the command silently. Disable with
  `SCRY_BUILD_GUARD=0`; retune with `SCRY_BUILD_GUARD_WINDOW`.
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
    local commits or a stale local tip. Fetches remote state for comparison;
    never advances local branches or changes checked-out files.
  - **Deploy drift:** whether merged work is actually live. Opt-in: configure a
    health URL and it never stays silent again; leave it unconfigured and it
    stays out of the way, because a repo with nothing deployed cannot act on
    the warning (see [Deploy drift](#deploy-drift)).
  - **Worktree hygiene sweep:** merged worktrees safe to remove, abandoned
    branches quietly aging, missing directories, detached HEADs.
  - **Open PRs and CI status:** every open PR with CI rollup (green, failing,
    interrupted, pending, or unknown) and review state (approved, changes
    requested, needs review).
    The session's own branch is marked. Zero config — derived from the git
    remote via `gh`. Silently skipped if `gh` isn't installed.
- **`fleet.sh`** — how many other Claude and Codex sessions are live in this repo *and its
  worktrees*, how old they are, whether one is in your exact directory, which
  subagents are editing here without a session of their own, whether other
  agent CLIs (Gemini, aider, and others) are competing for the same machine, and
  what the last session here was called. After `/clear` it does one more thing:
  Claude Code ends the session and starts a new one (SessionStart fires with
  `source: "clear"`), and the session just left is the one you were in seconds
  ago, so the new session is told its title, its id, the `claude --resume`
  command that reopens it, and — when the cache-handoff monitor got one
  written — the handoff itself. Without that, the live-window rule hid exactly
  that session (2026-09-10: "the new one has no idea where it just came from").
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
reason to act. Six checks reporting healthy state every session would
industrialise that problem.

**So a signal speaks only when it would change a decision.**

| Signal | Silent | Speaks |
|---|---|---|
| Load | 4 on 8 cores | ≥1.5× cores |
| Swap | none | ≥2GB in use |
| Disk | 60% used | ≥90% used, or <20GB free |
| Sessions in this repo | just you | any other live one |
| Last session | none recorded | a title exists |
| Session left by `/clear` | start was not a `/clear` | always: id, title, resume command, and the handoff if one was written |
| Session worktree location | never silent | states primary-worktree consequences, or the linked-worktree lock + `ExitWorktree` escape hatch — whichever applies |
| Session worktree merged | not merged | content already in main |
| Session worktree drift | up to date | origin/main ahead of fork point |
| Unpushed commits | all pushed | commits only on disk |
| Branch age | recent | ≥3 days since last commit |
| Orphan files (primary or session worktree) | none | a file exists in no commit on any branch |
| Stashes | none | any stash entry exists, with its age |
| Elixir/Ash worktree deps | `deps/` present | `deps/` missing — `mix deps.get` needed |
| Ash domains | no mix project in the repo | every project found, with descriptions |
| Scale-to-zero conflict | machine can actually stop | a polling worker + a pinned machine make it inert |
| Pooler for migrations | direct URL configured | DDL pointed at a transaction pooler |
| Dev tooling | none depended on | Tidewave / Livebook / LiveReload present |
| Build-time config | env reads only in `runtime.exs` | `System.get_env` in `config.exs` |
| Stale migration | migrations newer than any RESOURCE file | a resource changed since the last codegen |
| Ash read without an actor | target resource has no policies, or an explicit bypass | a policy-bearing resource is read with no actor |
| Spark extension edit | ordinary module | a DSL extension / transformer / verifier |
| Elixir build state | `_build` populated | `_build` cold — next compile is a FULL build |
| Force-rebuild command | any ordinary command | first `--force`/`rm -rf _build` attempt |
| Open PRs | none, or `gh` unavailable | any open PR exists |
| Stack | no config found | always — see below |

Silence is the default and the feature. A check that reports nothing is
reporting something: *nothing here needs your attention.* Every threshold is
overridable — see [Tuning](#tuning).

Four signals are deliberate exceptions, and the list is meant to be exhaustive —
a doctrine that quietly accumulates unlisted exceptions is just a preference.
Deploy drift never stays silent **once configured** (unconfigured it says
nothing, since a repo with no running service can never satisfy it); the
Markdown trust census reports whenever repository Markdown exists; the stack summary
reports whenever it finds config; and the Ash domain map reports whenever a mix
project exists. The first two were granted exemptions previously; the last two
were added deliberately (Matt, 2026-08-21/22) on the grounds that a stale doc
crosses no threshold and trips no alarm, so orientation facts have to be stated
rather than waited for. So does `stack.sh`: a stale doc crosses no threshold and trips no alarm,
so the stack is stated every session rather than only when something looks
wrong. Markdown trust pays for its fuller session-start insight with a concise
read-time reminder, avoiding a repeated token-heavy explanation.

## Example output

```
Ash domains [apps/engine] (36, 122 resources):
  MyApp.Accounts (3 resources) — Users, sessions, and API tokens.
  MyApp.Billing (5 resources) — Subscriptions, invoices, and usage metering.
  ... every domain, with its @moduledoc first sentence

Ash domains [apps/exhibits] (1, 2 resources):
  Exhibits.Jobs (2 resources)
```

```
Stack (SessionStart, read from live config — fly.toml, .env, manifests; docs
deliberately not consulted):
- Data: Neon (us-east-2) [KB_DATABASE_URL]; tunnel via localhost:5433 —
  provider NOT determinable offline (likely `fly proxy`/ssh to a remote DB)
  [DATABASE_URL]
- Hosting: Fly.io, 7 app(s) (myapp-engine, myapp-ingress, myapp-ocr, myapp-web, +3)
- Runtime: Elixir/Ash/Phoenix (3 mix project(s)); Python; Node; FastAPI; Docker
- Services wired: Anthropic, Hatchet, Logfire, Outseta, Stripe, Stytch, Zep
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

One thing a plugin cannot do for you: the cache-handoff monitor needs the
status-line payload, and `statusLine` is a user setting. Point it at Scry's
adapter once, naming the status line you already had in
`SCRY_STATUSLINE_INNER` (leave it unset to get a plain `cache warm 42m`):

```json
"statusLine": {
  "type": "command",
  "command": "f=\"$(ls -d \"$HOME\"/.claude/plugins/cache/scry/scry/*/ 2>/dev/null | sort -V | tail -1)cache_deadline_statusline.sh\"; if [ -f \"$f\" ]; then SCRY_STATUSLINE_INNER='wt list statusline --format=claude-code' exec bash \"$f\"; else exec wt list statusline --format=claude-code; fi"
}
```

The resolver picks the newest installed Scry so `/plugin update scry` keeps
working, and falls back to the inner status line when Scry is not installed
at all. Without this the monitor still arms, sees no deadline, and stays
silent — it does not guess.

Claude Code re-runs the status line on events (a new assistant message, a
compaction, and the moment a warm cache reaches `expires_at`), so the ❄️
flip is on time by itself. The minute count only ticks between events; add
`"refreshInterval": 60` next to `command` to keep it live while you are away
from the keyboard.

### Codex

```sh
codex plugin marketplace add matthewmathistrellys/scry
codex plugin add scry@scry
```

Review and trust the six bundled hooks when Codex asks. Codex deliberately
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

Three of the hooks ride events Claude Code defines — `SubagentStart`
(`main_drift_advisory.sh`), `Stop` (`session_disposal_advisory.sh`), and
`UserPromptSubmit` (`cache_handoff_arm.sh`). The cache-handoff monitor itself
(`monitors/monitors.json`) is a Claude Code plugin component with no Codex
equivalent as of 2026-09-10, and its status-line adapter reads a payload only
Claude Code produces; on Codex those two files are inert.
Whether Codex fires those event names has **not** been verified against a
Codex build; if it does not, those two groups simply never run there, which is
the same degradation-to-silence every other check has. The scripts themselves
are shared and client-agnostic — nothing about them is Claude-specific — so
adding Codex's equivalent event names is a manifest change, not a second
implementation.

All six checks are worth having everywhere, not just in the repos you
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

Copy the scripts plus `scanners/` into `~/.claude/hooks/`, `chmod +x` them
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

All six emit the `hookSpecificOutput.additionalContext` envelope supported by
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

Successful, neutral, and intentionally skipped checks roll up as green. A
known failure, timeout, action-required result, or startup failure takes
precedence over checks still running. Cancelled and stale checks are reported
as interrupted because they ended without a test verdict. Unrecognised or
malformed completed states are unknown rather than silently healthy.

No configuration needed — the git remote provides the repo. If `gh` isn't
installed or authentication fails, the block is silently skipped (fails open).

## Branch point

A branch is created from whatever ref the command names, and from `HEAD` when it
names none. Git raises nothing when that commit is one origin moved past hours
ago: the branch is created, and the work begins on files that are already the
older ones.

A `PreToolUse` advisory speaks when a Bash command creates a branch
(`git checkout -b`, `git switch -c`, `git worktree add`, `git branch <name>`)
**without naming a start point**. It states two facts and no instruction: how far
`HEAD` sits behind `origin/<default>`, and how long ago origin was last fetched —
because a `behind` count measured against a remote-tracking ref last updated
yesterday is itself yesterday's answer, and a zero from a checkout that has never
fetched means "nothing had landed as of the clone", not "nothing has landed".

It is silent when the command chooses its own start point, and when the tree is
current and the fetch is recent enough for that to mean something. It never
fetches, never moves a ref, and never denies.

Observed 2026-09-09 on a freshly built remote box: an agent asked to make one
change ran `git checkout -b <name>` with no start point and no preceding fetch.
`git reflog` recorded "Created from HEAD" and the checkout had no `FETCH_HEAD` at
all. The branch was current only because the clone was minutes old.

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
| `SCRY_SUBAGENT_STALE_DAYS` | `7` | drift in days before a subagent is told the tree is unreliable |
| `SCRY_SUBAGENT_STALE_COMMITS` | `50` | drift in commits before the same escalation |
| `SCRY_WORKTREE_REMINDER_MINUTES` | `45` | how long a session must run before the disposal reminder speaks |
| `SCRY_WORKTREE_DISK_MB_WARN` | `2048` | worktree disk at which an unmerged pile is worth saying out loud |
| `SCRY_WORKTREE_DU_BUDGET` | `4` | seconds of `du` allowed before the size is reported as a floor |
| `SCRY_SCRATCH_MD_LIST_MAX` | `5` | scratch `.md` files named in the disposal note before the rest become a count |
| `SCRY_BRANCH_POINT_FETCH_HOURS` | `2` | age of the last fetch past which a branch-point count is reported as dated |
| `SCRY_CACHE_HANDOFF_LEAD_SECONDS` | `120` | how long before the prompt cache expires the handoff is requested |
| `SCRY_CACHE_HANDOFF_POLL_SECONDS` | `15` | how often the monitor re-reads the deadline (no model calls) |
| `SCRY_CACHE_HANDOFF_DIR` | `~/.claude/scry/handoffs` | where handoff files are asked to be written |
| `SCRY_CACHE_HANDOFF` | `1` | `0` disables the cache-handoff monitor entirely |

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
The `/clear` case is the one exception, and it is a different thing: the
session just left is yours, from seconds ago, and the handoff it may have
written is a document made to be read next, not a summary of a stranger's
conversation. Even then only the handoff is printed — never the transcript's
prompts or responses.

**Merged means content, not commits.** Whether a branch is merged is decided by
ancestry *and* patch-id equivalence, so a squash or rebase merge — which
rewrites SHAs and hides from `git branch --merged` — is still recognised.
Deciding it on ancestry alone files finished work under "possibly abandoned",
and a review list full of false alarms is a list you learn to skim.

**Pick the event whose output actually lands.** A hook on the semantically
right event that no one ever reads is worse than no hook: it costs a process
per turn and buys the belief that the warning was given. Before wiring an
event, check what its exit-0 contract does with output — Claude Code documents
this per event, and a probe hook returning a unique token settles it in one
run. `SessionEnd` reads like the natural home for an end-of-session reminder
and discards everything it is handed; `Stop` delivers to the model and is why
`session_disposal_advisory.sh` lives there instead. The same question is why
`main_drift_advisory.sh` is on `SubagentStart`: `SessionStart` context never
reaches a child agent, and Agent-tool `additionalContext` lands in the parent.

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
