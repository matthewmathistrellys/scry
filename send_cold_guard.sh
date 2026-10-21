#!/usr/bin/env bash
# send_cold_guard.sh — PreToolUse (SendMessage), Claude Code.
#
# A message to an agent whose prompt cache has gone cold makes it re-read its
# whole context at the full rate, whatever the message says. The target is a
# subagent of this session, or a session in ~/.claude/sessions. When its last
# request is older than its cache TTL, the first send is turned down once with
# the facts line (what it is doing, how big it is, what a wake re-reads) and
# the latest peek note if one exists. A second send to it within an hour goes
# through. Only a deny reaches the main agent before the send: a PreToolUse
# additionalContext arrives with the tool result, after the message is gone.
#
# The target is resolved as SendMessage does: an agent id, else a name, and the
# newest agent wins when a name is used twice. A send that only subscribes to
# the idle notice (no message) wakes nothing and is let through. Warm or cold
# is decided from the file time and the tail of the record; the record is read
# whole only for a cold target.
#
# Fails open: unknown target, unreadable record, warm cache, a subagent
# sending (agent_id set). SCRY_COLD_SEND_GUARD=0, or the file
# ~/.claude/scry-peek.off (read on every run, so it reaches sessions already
# running), turns it off.
# SCRY_COLD_SEND_WINDOW (default 3600) is how long the second send stays free.
set -uo pipefail

[ "${SCRY_COLD_SEND_GUARD:-1}" = "0" ] && exit 0
[ -e "${SCRY_PEEK_OFF_FILE:-$HOME/.claude/scry-peek.off}" ] && exit 0

payload="$(cat 2>/dev/null || true)"
[ -n "$payload" ] || exit 0

FILE_PAYLOAD="$payload" \
SCRY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)" \
SCRY_WINDOW="${SCRY_COLD_SEND_WINDOW:-3600}" \
SCRY_SESSIONS_DIR="${SCRY_SESSIONS_DIR:-$HOME/.claude/sessions}" \
python3 - <<'PY' 2>/dev/null || true
import glob, json, os, re, sys, time

try:
    d = json.loads(os.environ["FILE_PAYLOAD"])
except (json.JSONDecodeError, KeyError):
    sys.exit(0)
if d.get("tool_name") != "SendMessage" or d.get("agent_id"):
    sys.exit(0)
ti = d.get("tool_input") or {}
to = str(ti.get("to") or "").strip()
sid = d.get("session_id") or ""
tp = d.get("transcript_path") or ""
if not to or not sid or not str(ti.get("message") or "").strip():
    sys.exit(0)
to_bare = re.sub(r"\s*\[[^\]]*\]$", "", to)
sys.path.insert(0, os.environ["SCRY_ROOT"])
import peek

target = None  # (key, desc, record path, is_subagent, agent id)
if tp.endswith(".jsonl"):
    sub = os.path.join(tp[:-len(".jsonl")], "subagents")
    by_id, by_name = [], []
    for j in glob.glob(os.path.join(glob.escape(sub), "agent-*.jsonl")):
        aid = os.path.basename(j)[len("agent-"):-len(".jsonl")]
        try:
            m = os.path.getmtime(j)
        except OSError:
            continue
        name = ""
        try:
            with open(j[:-len(".jsonl")] + ".meta.json") as f:
                name = str(json.load(f).get("name") or "")
        except Exception:
            pass
        if to_bare in (aid, "agent-" + aid):
            by_id.append((m, aid, j))
        elif name and to_bare == name:
            by_name.append((m, aid, j))
    found = max(by_id or by_name, default=None)  # an id first, then a name; newest wins
    if found:
        m, aid, j = found
        target = (f"{sid}-{aid}", peek.agent_desc(j), j, True, aid)
if target is None:
    ref = ""
    m = re.search(r"\[([^\]]*)\]$", to)
    if m:
        ref = m.group(1)
    live = []
    for p in glob.glob(os.path.join(glob.escape(os.environ["SCRY_SESSIONS_DIR"]), "*.json")):
        try:
            with open(p) as f:
                r = json.load(f)
        except (OSError, ValueError):
            continue
        if not r.get("sessionId") or not peek._alive(r.get("pid")):
            continue
        if to_bare in (r.get("name"), r.get("sessionId"), str(r.get("pid"))):
            live.append(r)
    # A `[ref]` is ListAgents' own short code and cannot be rebuilt from the
    # registry, so it only narrows by the id fields. More than one live match
    # is an address SendMessage itself would refuse: not judged here.
    if ref and len(live) > 1:
        live = [r for r in live if ref in (r.get("sessionId"), str(r.get("pid"))) or r["sessionId"].startswith(ref)] or live
    if len(live) == 1:
        rec = peek.session_path(live[0]["sessionId"])
        if rec:
            target = (f"session-{live[0]['sessionId']}", str(live[0].get("name") or ""), rec, False, None)
if target is None:
    sys.exit(0)

key, desc, rec, is_sub, aid = target
try:
    last = os.path.getmtime(rec)
    quiet = time.time() - last
    if quiet <= 300:
        sys.exit(0)  # inside the shortest TTL: warm, and nothing was read
    ttl = peek.cache_ttl(rec)
except OSError:
    sys.exit(0)
if quiet <= ttl:
    sys.exit(0)

window = os.environ["SCRY_WINDOW"]
window = int(window) if window.isdigit() and int(window) > 0 else 3600
marks = os.path.join(peek.state_dir(), "guard")
# The sender is in the key: one session's turned-down send is not another's.
marker = os.path.join(marks, re.sub(r"[^A-Za-z0-9_.-]", "_", f"{sid}--{key}"))
try:
    if time.time() - os.path.getmtime(marker) < window:
        peek.log_event(sid, to_bare, "send let through inside the window")
        sys.exit(0)  # second send inside the window: let it through
except OSError:
    pass

# Everything that can overrun or fail comes before the refusal is spent: a
# guard that cannot finish lets the send through and keeps its one refusal.
try:
    line = peek.scan(rec, 0, subagent=is_sub)[1].line(desc, who="Subagent" if is_sub else "Session")
except OSError:
    sys.exit(0)
text = (f"Scry — {to_bare} has been silent {peek.age(quiet)}, past its {peek.age(ttl)} cache. {line}")
n = peek.load_note(sid, aid) if is_sub else None
if n and n.get("note"):
    text += "\n\nLatest peek note (read from its record by a small model):\n" + n["note"]
text += ("\n\nThis is a one-time pause, not a failure: the same send again goes through "
         f"(for {peek.age(window)}). The agent's record and output files can be read without waking it.")
try:
    os.makedirs(marks, exist_ok=True)
    open(marker, "w").close()
except OSError:
    sys.exit(0)  # could not record the refusal: let the send through rather than refuse it twice
peek.log_event(sid, to_bare, "send turned down once")
print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": text,
    },
    "suppressOutput": True,
}))
PY
exit 0
