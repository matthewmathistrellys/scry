#!/usr/bin/env bash
# cache_handoff_monitor.sh — plugin monitor: shortly before this session's
# prompt cache goes cold, either keep it warm for work the session started and
# report that work's status as Scry can read it, or, when nothing is left to
# wait for, ask the session for a summary written into the conversation.
#
# Claude Code keeps a prompt cache with a time-to-live (an hour on this
# account's plan; the payload says which). Every request refreshes it. A
# session that goes idle — the user stepped away, the work paused on a
# question — lets it run out, and the next request re-reads the whole context
# at the full rate. An hour-old session with a large context is the expensive
# case, and it is also the one where the most is lost if the session is
# simply abandoned instead. A summary written in the last minutes of the warm
# window costs one cached request and leaves the record at the end of the
# transcript, where a /clear'd successor or a new window can find it.
#
# WHAT SCRY DOES AND DOES NOT DO (Matt, 2026-09-13, approved "do what you think
# is best"): "Scry is here to provide insight and information … make sure Scry
# isn't doing too much compared to what the orchestrator should do with the
# information. If it's deterministic and simple and reliable keep doing it; if
# not, just tell the orchestrator you've got so many agents and here's their
# status as far as I can tell." So this script reports facts read from file
# metadata and the session judges them: no stored progress fingerprints, no
# comparison between polls, no "stuck" verdict. (A fingerprint comparison was
# tried and dropped: quiet workers fell out of the list before they were
# compared, so its "no movement" was unreliable.) And: "Scry shouldn't say
# anything about agent comms or ThreadTask or anything else" — its messages
# name no other system.
#
# THE SUMMARY IS INLINE (same decision): "Why does it need a file, why can't it
# just write it inline … tell the next agent post-clear that it can go and get
# it as needed … dispatch a Sonnet subagent to go back through the
# transcript." "Put the session ID there in case I need to start a new window
# and work off of that chat." Until 1.31.0 the request named a file under
# ~/.claude/scry/handoffs; nothing is written there any more, and what is
# already there is left alone (it is the user's).
#
# How the pieces fit (designed with Codex, 2026-09-10):
#
#   cache_deadline_statusline.sh  writes <session>.deadline — when the cache
#                                 expires, from the only place Claude Code
#                                 reports it (the status-line payload).
#   cache_handoff_arm.sh          writes <session>.state = armed on every
#                                 user message, and on nothing else; and
#                                 records which session this Claude process
#                                 is running now (pid/<pid>).
#   this script                   polls those files, with no model calls, and
#                                 when the session is armed and the deadline
#                                 is inside the lead window, prints ONE line.
#                                 Claude Code delivers each stdout line of a
#                                 plugin monitor to the model as a
#                                 notification — that is what wakes an idle
#                                 session.
#
# WHICH session (2026-09-13). Claude Code starts a plugin monitor once per
# Claude process and freezes its environment, so CLAUDE_CODE_SESSION_ID is
# the id the terminal LAUNCHED with. /clear starts a new session in the same
# process and the monitor is not restarted (binary 2.1.270: monitors are
# armed once per plugin:name key, no session in it). Before this change the
# watcher kept following the launch id: session 60ef841d was cleared into
# b1746f6b at 17:54 on 09-11, 60ef841d.state froze, b1746f6b sat "armed"
# through a 36-hour idle and went cold at 13:56 on 09-13 with nothing asked.
# Worse, the old id's frozen warm deadline fired a request for the wrong
# session into the new one. So every poll resolves the CURRENT session:
#
#   1. ~/.claude/sessions/$CLAUDE_PID.json `sessionId` — Claude Code's own
#      per-process record, rewritten when /clear changes the id. Internal and
#      undocumented, so never the only source.
#   2. $state_dir/pid/$CLAUDE_PID — written by cache_handoff_arm.sh on
#      SessionStart and on every UserPromptSubmit, from the hook payload.
#   3. the environment id, only when neither exists.
#
# When 1 and 2 disagree the newer file wins. The winner and its source are
# kept in pid/$CLAUDE_PID.watch (session ids only). When the answer changes,
# the session left behind is marked `superseded` and its deadline is never
# read again; its pending request is dropped, not fired into its successor.
# The new session is left alone until a user message arms it.
#
# The state machine, in the .state file:
#
#   armed       a user message arrived; follow the deadline.
#   requested   the summary request was printed; print nothing more. fleet.sh
#               reads this after a /clear to tell the successor that the last
#               message(s) of the session it left hold a summary.
#   missed      the deadline passed while armed with no request — the machine
#               slept through it, or the lead time was shorter than the poll
#               gap. Silent HERE, deliberately: a monitor line wakes the model,
#               and waking it onto a cold cache is the cost this whole thing
#               exists to avoid. cache_handoff_arm.sh says it on the next turn,
#               which is already happening.
#   superseded  this process moved on to another session (above). The third
#               field keeps what it was before — `requested:<time>` or
#               `armed:<time>` — so fleet.sh can still say whether a summary
#               was asked for.
#
# KEEP-ALIVE (Matt, 2026-09-13, approved: "go"). A subagent, workflow or
# background shell that finishes wakes the session that started it. If that
# session's cache went cold in the meantime, the wake re-reads everything: in
# b1746f6b a workflow completion 2h08m after the last request wrote 289,347
# cache tokens instead of reading them. A notification turn on a warm cache is
# a cache read (62aa5e9f: read 125,092, wrote 749). So at the lead window,
# while THIS session has unfinished work, the line printed is a keep-alive:
# the roster, one short status line for the user, and the session compares
# that status with its previous one and checks on any worker that has not
# moved. That reply is the request that pushes the deadline out another hour.
#
#   roster      every unfinished worker this session started, with what Scry
#               can read deterministically from metadata:
#                 workflow — a journal with an agent `started` and no
#                   `result`: "<finished> of <started> agents done, last
#                   activity <age> ago". Listed however long it has been
#                   quiet: the journal itself says it is unfinished.
#                 background command — a b*.output with no exit or killed
#                   marker in its last 64 bytes: "still running, last output
#                   <age> ago". Listed however long it has been quiet, for
#                   the same reason.
#                 subagent — an agent-*.jsonl: "last activity <age> ago".
#                   Nothing in a plain subagent's metadata says it finished,
#                   so Scry cannot tell a finished one from a quiet one. It is
#                   listed only while written within SCRY_KEEPALIVE_FRESH_SECS,
#                   default 3600 — one cache TTL — so one silent that long is
#                   left out rather than reported as running.
#               Agents and workflows are looked up under the current session,
#               shell tasks under both the launch and the current session —
#               that is where Claude Code files each (read 2026-09-13).
#   bounded     SCRY_KEEPALIVE_MAX (8) per user message, and
#               SCRY_KEEPALIVE_MAX_PER_SESSION (24) that nothing resets — the
#               backstop if some other wake ever re-arms. Counted in
#               <session>.keepalive BEFORE the line is printed.
#   verified    one keep-alive per deadline. If the deadline has not moved
#               half a lead later, the ack did not happen: ask for the summary.
#
# When nothing is unfinished, a cap is reached, or a keep-alive did not take,
# the summary is requested: written as the session's reply in the
# conversation, opening with the session id and how to pick it up in a new
# window, and naming any still-unfinished workers from the roster.
#
# Every transition is written BEFORE its line is printed, so a restart between
# the two cannot produce a second line. The monitor reads no conversation
# content: small files of numbers, file names and times, agent and workflow
# labels from metadata, and the last 64 bytes of a shell task's output for its
# exit marker, which is never emitted.
#
# SCRY_CACHE_HANDOFF=0 disables it (monitor exits at once). Claude only —
# Codex has no monitor equivalent as of 2026-09-10.
set -uo pipefail

if [ "${SCRY_CACHE_HANDOFF:-1}" = "0" ]; then
  exit 0
fi

sid="${CLAUDE_CODE_SESSION_ID:-}"
if [ -z "$sid" ]; then
  # Never silently wrong: the watch cannot run without knowing whose cache it
  # is watching, and exiting quietly would look like "watching".
  echo "Scry — cache deadline: automatic summary request unavailable in this session (no session id in the monitor environment). Nothing is watching the prompt-cache expiry."
  exit 0
fi

state_dir="${SCRY_CACHE_STATE_DIR:-${TMPDIR:-/tmp}/scry-cache-deadline}"
lead="${SCRY_CACHE_HANDOFF_LEAD_SECONDS:-120}"
poll="${SCRY_CACHE_HANDOFF_POLL_SECONDS:-15}"
ka_max="${SCRY_KEEPALIVE_MAX:-8}"
ka_session_max="${SCRY_KEEPALIVE_MAX_PER_SESSION:-24}"
ka_fresh="${SCRY_KEEPALIVE_FRESH_SECS:-3600}"
case "$lead" in ''|*[!0-9]*) lead=120 ;; esac
case "$poll" in ''|*[!0-9]*) poll=15 ;; esac
case "$ka_max" in ''|*[!0-9]*) ka_max=8 ;; esac
case "$ka_session_max" in ''|*[!0-9]*) ka_session_max=24 ;; esac
case "$ka_fresh" in ''|*[!0-9]*) ka_fresh=3600 ;; esac

# Claude Code writes task output under /tmp/claude-<uid> whatever TMPDIR says
# (this machine, 2026-09-13: TMPDIR is under /var/folders, tasks are not), so
# both are looked at unless the tuning variable names one.
uid="$(id -u 2>/dev/null || echo 0)"
task_roots="${SCRY_TASK_STATE_DIR:-${TMPDIR:-/tmp}/claude-$uid:/tmp/claude-$uid}"

pass() {
  SCRY_SID="$sid" SCRY_DIR="$state_dir" SCRY_LEAD="$lead" \
  SCRY_PID="${CLAUDE_PID:-}" \
  SCRY_SESSIONS_DIR="${SCRY_CLAUDE_SESSIONS_DIR:-$HOME/.claude/sessions}" \
  SCRY_PROJECTS_DIR="${SCRY_CLAUDE_PROJECTS_DIR:-$HOME/.claude/projects}" \
  SCRY_TASK_ROOTS="$task_roots" SCRY_KA_MAX="$ka_max" \
  SCRY_KA_SESSION_MAX="$ka_session_max" SCRY_KA_FRESH="$ka_fresh" \
  python3 - <<'PY' 2>/dev/null
import glob, json, os, re, sys, time

launch = os.environ["SCRY_SID"]
state_dir = os.environ["SCRY_DIR"]
lead = int(os.environ["SCRY_LEAD"])
pid = os.environ.get("SCRY_PID", "")
now = int(time.time())
SID_OK = re.compile(r"^[A-Za-z0-9._-]{1,128}$")

def atomic_write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w") as f:
        f.write(text)
    os.replace(tmp, path)

# ---- which session this process is running now --------------------------
def current_session():
    found = []  # (mtime, rank, sid, source); rank breaks an mtime tie
    if pid.isdigit():
        p = os.path.join(os.environ["SCRY_SESSIONS_DIR"], pid + ".json")
        try:
            with open(p) as f:
                s = json.load(f).get("sessionId")
            if isinstance(s, str) and SID_OK.match(s):
                found.append((os.path.getmtime(p), 1, s, "sessions"))
        except Exception:
            pass
        p = os.path.join(state_dir, "pid", pid)
        try:
            with open(p) as f:
                parts = f.read().split()
            if parts and SID_OK.match(parts[0]):
                found.append((os.path.getmtime(p), 0, parts[0], "hook"))
        except Exception:
            pass
    if found:
        _, _, s, source = max(found)
        return s, source
    return launch, "env"

def read_state(s):
    try:
        with open(os.path.join(state_dir, s + ".state")) as f:
            parts = f.read().split()
    except Exception:
        return ("unarmed", 0, "")
    if not parts:
        return ("unarmed", 0, "")
    when = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
    extra = parts[2] if len(parts) > 2 else ""
    return (parts[0], when, extra)

def write_state(s, name, when, extra=""):
    atomic_write(os.path.join(state_dir, s + ".state"),
                 f"{name} {when} {extra}".rstrip() + "\n")

sid, source = current_session()

watch_path = os.path.join(state_dir, "pid", (pid if pid.isdigit() else launch) + ".watch")
watched = launch
try:
    with open(watch_path) as f:
        w = f.read().split()
    # <watched sid> <source> <launch sid>; another process's file (a reused
    # pid) is not this monitor's memory.
    if len(w) >= 3 and w[2] == launch:
        watched = w[0]
        if w[0] == sid and w[1] == source:
            watch_path = None
except Exception:
    pass
if watched != sid:
    old = read_state(watched)
    if old[0] in ("armed", "requested"):
        try:
            # What it was, and since when: fleet.sh tells a /clear successor
            # whether a summary was asked for.
            write_state(watched, "superseded", now, f"{old[0]}:{old[1]}")
        except Exception:
            sys.exit(0)
if watch_path:
    try:
        atomic_write(watch_path, f"{sid} {source} {launch}\n")
    except Exception:
        pass

# ---- the cycle ------------------------------------------------------------
state, since, extra = read_state(sid)

if state != "armed":
    sys.exit(0)

try:
    with open(os.path.join(state_dir, sid + ".deadline")) as f:
        d = json.load(f)
except Exception:
    sys.exit(0)
expires = d.get("expires_at")
if not d.get("warm") or not isinstance(expires, int) or d.get("session_id", sid) != sid:
    sys.exit(0)

# A deadline observed before this cycle was armed is not this cycle's: the
# user message that armed it is itself a request, and the status line will
# report the refreshed expiry shortly. Waiting for that avoids acting on a
# stale number in the seconds between the two.
if int(d.get("observed_at") or 0) < since:
    sys.exit(0)

if now >= expires:
    write_state(sid, "missed", expires)
    sys.exit(0)
if now < expires - lead:
    sys.exit(0)

# ---- inside the lead window: keep-alive or summary -------------------------
ka_path = os.path.join(state_dir, sid + ".keepalive")
try:
    with open(ka_path) as f:
        ka = json.load(f)
    if not isinstance(ka, dict):
        ka = {}
except Exception:
    ka = {}
total = int(ka.get("total") or 0)
if ka.get("armed_at") == since:
    cycle = int(ka.get("cycle") or 0)
    sent_expires = ka.get("sent_expires")
    sent_at = int(ka.get("sent_at") or 0)
else:
    # A new user message: the per-message count starts again. The session
    # count does not.
    cycle, sent_expires, sent_at = 0, None, 0

why_not = ""
if isinstance(sent_expires, int) and expires <= sent_expires:
    # A keep-alive already went out for this deadline. Give its ack time to
    # land and the status line time to record the new expiry; after half the
    # lead, it did not take.
    if now - sent_at < max(1, lead // 2):
        sys.exit(0)
    why_not = "the last keep-alive did not refresh the cache"

fresh = int(os.environ["SCRY_KA_FRESH"])
projects = os.environ["SCRY_PROJECTS_DIR"]

def age(t):
    s = max(0, now - int(t))
    if s >= 3600:
        return f"{s // 3600}h{(s % 3600) // 60:02d}m"
    return f"{s // 60}m" if s >= 60 else f"{s}s"

def label(text):
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return text[:60]

def mtime(p):
    try:
        return os.path.getmtime(p)
    except OSError:
        return None

def roster_items():
    """Every unfinished worker this session started, newest activity first,
    as roster lines. File names, times and metadata labels only — no
    comparison with any earlier poll, no judgement of progress."""
    items = []
    for sdir in glob.glob(os.path.join(glob.escape(projects), "*", glob.escape(sid))):
        sub = os.path.join(sdir, "subagents")
        # Subagents: nothing says "finished", so only a recent write counts.
        for j in glob.glob(os.path.join(sub, "agent-*.jsonl")):
            m = mtime(j)
            if m is None or m < now - fresh:
                continue
            aid = os.path.basename(j)[len("agent-"):-len(".jsonl")]
            desc = ""
            try:
                with open(j[:-len(".jsonl")] + ".meta.json") as f:
                    meta = json.load(f)
                desc = label(meta.get("description") or meta.get("name"))
            except Exception:
                pass
            named = f' "{desc}"' if desc else ""
            items.append((m, f"subagent {aid}{named}: last activity {age(m)} ago"))
        # Workflows: the journal says which agents have no result yet, and
        # that holds however long the workflow has been quiet.
        for wdir in glob.glob(os.path.join(sub, "workflows", "wf_*")):
            started, finished = set(), set()
            journal = os.path.join(wdir, "journal.jsonl")
            try:
                with open(journal, errors="replace") as f:
                    for line in f:
                        # The record's head only: a `result` line carries the
                        # agent's output after these fields, and it is not read.
                        k = re.search(r'"type":"(started|result)","key":"([^"]+)"', line[:320])
                        if k:
                            (started if k.group(1) == "started" else finished).add(k.group(2))
            except OSError:
                continue
            if not (started - finished):
                continue
            stamps = [t for t in (mtime(p) for p in [journal] + glob.glob(
                os.path.join(glob.escape(wdir), "agent-*.jsonl"))) if t is not None]
            if not stamps:
                continue
            m = max(stamps)
            wid = os.path.basename(wdir)
            # The workflow's name is in its script's file name,
            # <session>/workflows/scripts/<name>-<run id>.js, written at launch
            # (read 2026-09-13 against a running workflow).
            name = ""
            for script in glob.glob(os.path.join(glob.escape(sdir), "workflows", "scripts",
                                                 "*-" + glob.escape(wid) + ".js")):
                name = label(os.path.basename(script)[:-len("-" + wid + ".js")])
                break
            shown = f"{name} ({wid})" if name else wid
            done = len(finished & started)
            items.append((m, f"workflow {shown}: {done} of {len(started)} agents done, "
                             f"last activity {age(m)} ago"))
    # Background commands: unfinished until the output records an exit, and
    # that holds however long the command has been quiet.
    seen = set()
    for root in os.environ["SCRY_TASK_ROOTS"].split(":"):
        if not root:
            continue
        for s in {launch, sid}:
            for out in glob.glob(os.path.join(glob.escape(root), "*", glob.escape(s), "tasks", "b*.output")):
                real = os.path.realpath(out)
                if real in seen or os.path.islink(out):
                    continue
                seen.add(real)
                try:
                    m = os.path.getmtime(out)
                    with open(out, "rb") as fh:
                        fh.seek(0, 2)
                        fh.seek(max(0, fh.tell() - 64))
                        tail = fh.read()
                except OSError:
                    continue
                if b"[exited with code" in tail or b"[killed]" in tail:
                    continue
                tid = os.path.basename(out)[:-len(".output")]
                items.append((m, f"background command {tid}: still running, last output {age(m)} ago"))
    items.sort(key=lambda x: -x[0])
    return [text for _, text in items]

roster = roster_items()
listed = "; ".join(roster[:8]) + (f"; and {len(roster) - 8} more" if len(roster) > 8 else "")
ka_max = int(os.environ["SCRY_KA_MAX"])
ka_session_max = int(os.environ["SCRY_KA_SESSION_MAX"])
at = time.strftime("%H:%M", time.localtime(expires))
minutes = max(0, (expires - now) // 60)

if roster and not why_not:
    if cycle >= ka_max:
        why_not = f"the {ka_max} keep-alives allowed per user message are spent"
    elif total >= ka_session_max:
        why_not = f"the {ka_session_max} keep-alives allowed per session are spent"

if roster and not why_not:
    # Counted before printing: a restart between the two cannot send twice.
    try:
        atomic_write(ka_path, json.dumps({
            "armed_at": since, "cycle": cycle + 1, "total": total + 1,
            "sent_at": now, "sent_expires": expires,
        }))
    except Exception:
        sys.exit(0)
    print(
        "Scry — cache keep-alive (this is a Scry monitor notification, not a user "
        f"message): this session's prompt cache goes cold at {at}, in about "
        f"{minutes} min, and work it started has not finished. As far as Scry can "
        f"tell from file metadata: {listed}. When that work finishes, its "
        "notification wakes this session, and on a cold cache that wake re-reads "
        "the whole context at the full rate; your reply to this keeps it warm. "
        "Post ONE short plain-language status line for the user from this list "
        "(e.g. \"Piece 2: 5 of 8 agents done; build and review still running\"). "
        "Compare it with your previous status in this conversation: if a worker "
        "shows no movement since then, check on it and handle it as the "
        "orchestrator; otherwise take no other action — the work reports back by "
        f"itself. Keep-alive {cycle + 1} of at most {ka_max} for this user message."
    )
    sys.exit(0)

# Written before printing: a restart between the two cannot ask twice.
write_state(sid, "requested", now)

running = ""
if roster:
    running = (
        f" Work this session started has not finished and will not be kept warm "
        f"for ({why_not}). As far as Scry can tell from file metadata: {listed}. "
        "End the summary with those still-running workers, as listed, so whoever "
        "picks this up collects them rather than starting them again."
    )
print(
    "Scry — cache deadline (this is a Scry monitor notification, not a user "
    f"message): this session's prompt cache goes cold at {at}, in about "
    f"{minutes} min. The first request after that re-reads the whole context "
    "at the full rate instead of the cached one. While it is still warm, write "
    "a summary of this session as your reply here in the conversation — not to "
    f"any file. Open it with \"Session {sid}\" and this line: \"To continue in "
    f"a new window, say: pick up from session {sid}\". Then: the objective and "
    "the latest user direction; decisions and open questions; what was done "
    "and what was actually verified; what remains and the next concrete step; "
    "the working directory and branch."
    f"{running} Then stop and wait for the user. This will not repeat until "
    "the user sends another message."
)
PY
}

if [ "${SCRY_CACHE_HANDOFF_ONCE:-0}" = "1" ]; then
  pass
  exit 0
fi

while :; do
  if [ -n "${CLAUDE_PID:-}" ] && ! kill -0 "$CLAUDE_PID" 2>/dev/null; then
    exit 0
  fi
  pass
  sleep "$poll"
done
