#!/usr/bin/env python3
"""peek.py — Scry's one reader of session and subagent records.

Used by the check-sessions skill (skills/check-sessions/scrum-gather.py) and by
the hooks (subagent_cold_advisory.sh, peek_deliver.sh, send_cold_guard.sh,
schedule_cost_advisory.sh). It reads what an agent said and was told, because
a quiet agent cannot be judged from file times alone. Only a
note about it, written by a small model, ever reaches the main agent.

  transcript_facts / write_evidence   what the agent was told, said, ran
  facts_line                          status, open background commands, size,
                                      and what a wake would re-read, by script
  run_peek                            evidence -> Haiku -> stored note
  take_note / latest_note             delivery state, one file per subagent

Command line (used by the hook, in the background):
  peek.py run <session-id> <agent-id> <agent-jsonl>
"""
import fcntl
import glob
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from roster import age  # noqa: E402

HOME = os.path.expanduser("~")
TAIL_BYTES = 3_000_000
EVIDENCE_CHARS = 24_000
ROOT = os.path.dirname(os.path.abspath(__file__))
PROMPT = os.path.join(ROOT, "skills", "check-sessions", "summarizer-prompt.md")
HAIKU_TIMEOUT = 180


def state_dir():
    return os.environ.get("SCRY_PEEK_STATE_DIR") or os.path.join(
        os.environ.get("TMPDIR") or "/tmp", "scry-peek")


def off_file():
    return os.environ.get("SCRY_PEEK_OFF_FILE") or os.path.join(HOME, ".claude", "scry-peek.off")


def is_off():
    """A file switch, read on every hook run, so it reaches sessions already
    running (an environment variable does not). `touch` it to turn the peek and
    the guard off; remove it to turn them on."""
    return os.path.exists(off_file())


LOG_KEEP_SECS = 30 * 86400


def log_event(sid, subject, event):
    """One line per event, never any record text: time, session, subagent or
    target, event. Best effort."""
    try:
        os.makedirs(state_dir(), exist_ok=True)
        with open(os.path.join(state_dir(), "events.log"), "a") as fh:
            fh.write(f"{datetime.now().astimezone().isoformat(timespec='seconds')}\t{sid}\t{subject}\t{event}\n")
    except OSError:
        pass


def trim_log():
    p = os.path.join(state_dir(), "events.log")
    cutoff = datetime.fromtimestamp(time.time() - LOG_KEEP_SECS).astimezone().isoformat(timespec="seconds")
    try:
        with open(p) as fh:
            lines = fh.readlines()
        keep = [l for l in lines if l[:25] >= cutoff]
        if len(keep) != len(lines):
            with open(p, "w") as fh:
                fh.writelines(keep)
    except OSError:
        pass


def ts_of(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0


def clock(ts):
    return datetime.fromtimestamp(ts).strftime("%a %-I:%M %p") if ts else "?"


def clock_short(ts):
    """Local time with the date, for a facts line that may be read days later."""
    return datetime.fromtimestamp(ts).strftime("%b %-d %-I:%M %p")


def one_line(s, n=240):
    s = re.sub(r"\s+", " ", s).strip()
    return s if len(s) <= n else s[:n] + " …[cut]"


def tail_records(path, tail_bytes=TAIL_BYTES):
    """Records of a transcript, one line at a time. `tail_bytes=None` reads
    the whole file: a subagent's early records hold its brief and what it
    already committed. Lines are never gathered into one buffer: a long
    subagent record runs to tens of MB."""
    size = os.path.getsize(path)
    cut = tail_bytes is not None and size > tail_bytes
    with open(path, "rb") as f:
        if cut:
            f.seek(size - tail_bytes)
            f.readline()  # the first line is cut in the middle
        for raw in f:
            try:
                yield json.loads(raw)
            except Exception:
                continue


def session_path(session_id):
    hits = glob.glob(f"{HOME}/.claude/projects/*/{session_id}.jsonl")
    return hits[0] if hits else None


def new_facts():
    return {"found": True, "branch": None, "texts": [], "inputs": [], "tt": [], "last_ts": 0,
            "ctx": 0, "turns": 0, "ttl": 3600, "last_any": [], "actions": []}


def fold_facts(f, d, since, until, subagent):
    """Add one record to the facts of what the agent was told, said and ran."""
    ts = ts_of(d.get("timestamp") or "")
    if until and ts > until:
        return
    f["branch"] = d.get("gitBranch") or f["branch"]
    f["last_ts"] = max(f["last_ts"], ts)
    msg = d.get("message") or {}
    blocks = msg.get("content") if isinstance(msg.get("content"), list) else []
    if d.get("type") == "assistant":
        u = msg.get("usage") or {}
        if u:
            f["ctx"] = u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0) + u.get("cache_creation_input_tokens", 0)
            cc = u.get("cache_creation") or {}
            if cc.get("ephemeral_5m_input_tokens", 0) and not cc.get("ephemeral_1h_input_tokens", 0):
                f["ttl"] = 300
            elif cc.get("ephemeral_1h_input_tokens", 0):
                f["ttl"] = 3600
        for b in blocks:
            if b.get("type") == "text" and b.get("text", "").strip():
                f["last_any"] = (f["last_any"] + [(ts, b["text"].strip())])[-6:]
        if ts <= since:
            return
        f["turns"] += 1
        for b in blocks:
            if b.get("type") == "text" and b.get("text", "").strip():
                f["texts"].append((ts, b["text"].strip()))
            if b.get("type") == "tool_use":
                i = b.get("input") or {}
                cmd = i.get("command") or i.get("description") or i.get("file_path") or i.get("prompt") or json.dumps(i)
                f["actions"].append((ts, f"RAN {b.get('name', '').split('__')[-1]}: {one_line(str(cmd), 220)}"))
            if b.get("type") == "tool_use" and "ThreadTask" in b.get("name", ""):
                f["tt"].append(b["name"].split("__")[-1])
    elif d.get("type") == "user" and (subagent or not d.get("isSidechain")) and ts > since:
        c = msg.get("content")
        for b in blocks:
            if b.get("type") == "tool_result":
                r = b.get("content")
                r = r if isinstance(r, str) else " ".join(x.get("text", "") for x in r if isinstance(x, dict))
                if r.strip():
                    f["actions"].append((ts, f"RETURNED: {one_line(r, 320)}"))
        t = c if isinstance(c, str) else " ".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        if t.strip() and (not t.startswith("<") or t.startswith("<cross-session-message") or t.startswith("<pasted_content")):
            kind = (d.get("origin") or {}).get("kind") or "typed"
            if t.startswith("<cross-session-message"):
                kind = "from another session"
            elif t.startswith("<pasted_content"):
                kind = "pasted (may be relayed from another tab)"
            f["inputs"].append((ts, f"({kind}) " + t.strip()))


def scan(path, since=0, until=0, subagent=False):
    """One pass over a record: (facts, facts-line state). The record is read
    once, line by line, whatever the two readers need from it."""
    f, acc = new_facts(), FactsAcc()
    if not path:
        f["found"] = False
        return f, acc
    for d in tail_records(path, None if subagent else TAIL_BYTES):
        fold_facts(f, d, since, until, subagent)
        if d.get("timestamp"):
            acc.feed(d)
    return f, acc


def transcript_facts(path, since, until=0, subagent=False):
    """Everything the agent was told, said and ran after `since`. For a
    subagent every record is `isSidechain`, so none are skipped, and the whole
    file is read rather than its last 3 MB."""
    return scan(path, since, until, subagent)[0]


def write_evidence(path, label, f, commits, tail, recap, since, unsent, facts=""):
    out = []
    if facts:
        out += [f"FACTS: {facts}", ""]
    out += [f"# {label}", f"window: since {clock(since)}; last turn {clock(f['last_ts'])}; turns in window: {f['turns']}; context {f['ctx'] // 1000}k"]
    if unsent:
        out.append(f"typed in the input box but NOT sent (nothing ran from this): {unsent}")
    if recap:
        out.append(f"recap line on screen: {recap}")
    out.append("\n## What the tab was told (oldest first)")

    def cap(x, n):
        return x if len(x) <= n else x[:n] + f" [...cut by the check-in at {n} characters; the session's own text goes on]"
    out += [f"[{clock(t)}] {cap(x, 6000 if i == 0 else 1500)}" for i, (t, x) in enumerate(f["inputs"])] or ["(nothing new)"]
    if not f["turns"]:
        out.append("\n## Nothing new in the window. Last known messages (OLDER than the window, still the tab's latest state)")
        out += [f"[{clock(t)}] {x[:1500]}" for t, x in f["last_any"]] or ["(no messages found)"]
    out.append("\n## What the tab said in the window (oldest first)")
    body = "\n\n".join(f"[{clock(t)}] {x[:3000]}" for t, x in f["texts"]) or "(nothing new)"
    out.append(body[-EVIDENCE_CHARS:])
    acts = "\n".join(f"[{clock(t)}] {x}" for t, x in f["actions"])
    out.append("\n## What the tab ran and what came back (oldest first; results are cut to 320 characters)\n" + (acts[-EVIDENCE_CHARS:] if acts else "(nothing new)"))
    out.append("\n## Git commits since last check\n" + (commits or "(none, or this folder is not a repo)"))
    out.append("\n## Last lines on its screen\n" + "\n".join(tail))
    with open(path, "w") as fh:
        fh.write("\n".join(out))


# ── The facts line ──────────────────────────────────────────────────────────

def _blocks(d):
    c = (d.get("message") or {}).get("content")
    return c if isinstance(c, list) else []


def _csize(d):
    u = (d.get("message") or {}).get("usage") or {}
    return sum(u.get(k, 0) for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))


def _result_text(b):
    r = b.get("content")
    return r if isinstance(r, str) else " ".join(x.get("text", "") for x in r or [] if isinstance(x, dict))


# The harness's own end-of-record messages. A phrase inside a tool result (a
# Grep for "usage limit", a file that quotes one) is not one of them.
LIMIT_RE = re.compile(r"^\W*(?:you've|you have) hit your [\w -]{1,30}limit\b[^\n]*?(?:resets ([^\n]+))?$", re.I)


class FactsAcc:
    """What the facts line needs, kept as the record streams past: no list of
    records is held."""

    def __init__(self):
        self.last = None            # last assistant record, without its content
        self.last_pending = []      # its tool_use blocks (id, name, input)
        self.ctx = 0
        self.names, self.descs = {}, {}
        self.returned = set()
        self.started, self.done = {}, set()
        self.handback = None        # (time, tool_use id)
        self.stopped = None
        self.tail = []              # (time, type) of the last records
        self.last_msg = None        # (type, time) of the last user/assistant record

    def feed(self, d):
        t = ts_of(d["timestamp"])
        typ = d.get("type")
        self.tail = (self.tail + [t])[-3:]
        blocks = _blocks(d)
        if self.handback and typ in ("user", "assistant"):
            # A hand-back stands only while nothing came in after it; the
            # result of the hand-back call itself is not something coming in.
            own = typ == "user" and blocks and all(
                b.get("type") == "tool_result" and b.get("tool_use_id") == self.handback[1] for b in blocks)
            if not own:
                self.handback = None
        if typ == "assistant":
            if _csize(d):
                self.ctx = _csize(d)
            for b in blocks:
                if b.get("type") == "text" and LIMIT_RE.match(b.get("text", "").strip().split("\n")[0]):
                    line = b["text"].strip().split("\n")[0]
                    mm = re.search(r"resets ([^\n]+)", line)
                    self.stopped = (t, "hit the session limit" + (f" (resets {mm.group(1).strip()})" if mm else ""))
            self.last = {"time": t, "stop": (d.get("message") or {}).get("stop_reason")}
            self.last_pending = [b for b in blocks if b.get("type") == "tool_use"]
        if typ in ("user", "assistant"):
            self.last_msg = (typ, t)
        done_ids = re.findall(r"task-id>(\w+)<", json.dumps(d)) if "task-id" in json.dumps(d) else []
        self.done.update(done_ids)
        for b in blocks:
            if b.get("type") == "text" and typ == "user" and b.get("text", "").lstrip().startswith("[Request interrupted by user"):
                self.stopped = (t, "was interrupted by the user (or a tool call was turned down)")
            if b.get("type") == "tool_use":
                i = b.get("input") or {}
                self.names[b["id"]] = b.get("name")
                self.descs[b["id"]] = i.get("description") or re.sub(
                    r'^\s*cd\s+("[^"]*"|\S+)\s*(&&|;)\s*', "", i.get("command") or b.get("name") or "")
                if b.get("name") == "SubagentHandback":
                    self.handback = (t, b["id"])
                if b.get("name") == "TaskStop" and (i.get("task_id") or i.get("shell_id")):
                    self.done.add(i.get("task_id") or i.get("shell_id"))
            elif b.get("type") == "tool_result":
                self.returned.add(b.get("tool_use_id"))
                if typ == "user" and _result_text(b).lstrip().startswith("The user doesn't want to proceed"):
                    self.stopped = (t, "was interrupted by the user (or a tool call was turned down)")
                tr = d.get("toolUseResult") if isinstance(d.get("toolUseResult"), dict) else {}
                bid = tr.get("backgroundTaskId") or (tr.get("taskId") if self.names.get(b.get("tool_use_id")) == "Monitor" else None)
                if bid:
                    self.started[bid] = (t, one_line(str(self.descs.get(b.get("tool_use_id"), "")), 70))

    def handed_off(self):
        """Finished by its own hand-back, nothing after it, and nothing left
        running that could wake it: the facts line says all there is to say."""
        stopped = self.stopped and self.stopped[0] in self.tail
        return bool(self.handback and not stopped
                    and not [k for k in self.started if k not in self.done])

    def line(self, desc="", who="Subagent"):
        if self.last is None:
            return f'{who} "{desc}": no reply in its record yet.'
        last_t = self.last["time"]
        # A limit or interruption only counts when it is the end of the record.
        stopped = self.stopped if self.stopped and self.stopped[0] in self.tail else None
        pending = [b for b in self.last_pending if b["id"] not in self.returned and b.get("name") != "SubagentHandback"]
        openbg = [k for k in self.started if k not in self.done]
        if stopped:
            status = f"stopped at {clock_short(stopped[0])}: it {stopped[1]}. It will not continue on its own; a message restarts it."
        elif self.handback:
            status = f"finished: it handed back its report at {clock_short(self.handback[0])}."
        elif pending:
            i0 = pending[0].get("input") or {}
            what = one_line(str(i0.get("command") or i0.get("description") or i0), 160)
            status = f"waiting on a {pending[0]['name']} call started {clock_short(last_t)}: {what}"
        elif self.last["stop"] == "end_turn" and openbg:
            status = "ended its turn with background commands still running, so it acts again only when one reports back or someone messages it."
        elif self.last["stop"] == "end_turn":
            status = "finished: its last reply ended its turn with no tool call."
        elif self.last_msg and self.last_msg[0] == "user":
            status = f"working: its last tool call returned at {clock_short(self.last_msg[1])} and no reply has come since."
        else:
            status = "unclear from the record."
        bg = ""
        if openbg:
            bg = (" Background commands started and not reported done in its record: "
                  + "; ".join(f'"{self.started[k][1]}" (from {clock_short(self.started[k][0])})' for k in openbg)
                  + ". If one ends, its notice wakes it, and it re-reads its whole context.")
        if self.ctx:
            k = self.ctx // 1000
            size = (f"Context {k}k tokens: a message to it after its cache goes cold re-reads all {k}k; "
                    "a background notice wakes it the same way.")
        else:
            size = "Its record has no context size, so what a wake re-reads is not known."
        return f'{who} "{desc}". Status: {status}{bg} {size}'


def facts_line(recs, desc="", who="Subagent"):
    """One line, worked out by script from the records: what state the agent
    is in, which background commands are open, how big its context is, and what
    a wake re-reads. Times are local. Nothing here is a model's reading."""
    acc = FactsAcc()
    for r in recs:
        if r.get("timestamp"):
            acc.feed(r)
    return acc.line(desc, who)


def agent_desc(jsonl):
    try:
        with open(jsonl[:-len(".jsonl")] + ".meta.json") as fh:
            meta = json.load(fh)
        return re.sub(r"\s+", " ", str(meta.get("description") or meta.get("name") or "")).strip()[:60]
    except Exception:
        return ""


def cache_ttl(path):
    """The cache TTL a record's last request bought, from its tail only."""
    ttl = 3600
    try:
        for d in tail_records(path, 400_000):
            if d.get("type") == "assistant":
                cc = ((d.get("message") or {}).get("usage") or {}).get("cache_creation") or {}
                if cc.get("ephemeral_5m_input_tokens", 0) and not cc.get("ephemeral_1h_input_tokens", 0):
                    ttl = 300
                elif cc.get("ephemeral_1h_input_tokens", 0):
                    ttl = 3600
    except OSError:
        pass
    return ttl


def session_ctx(transcript_path):
    """Context size of a session from the last assistant usage in its tail."""
    ctx = 0
    try:
        for d in tail_records(transcript_path, 400_000):
            if d.get("type") == "assistant" and _csize(d):
                ctx = _csize(d)
    except OSError:
        pass
    return ctx


# ── Haiku ───────────────────────────────────────────────────────────────────

def haiku_command(prompt_text):
    return ["claude", "-p", "--model", "haiku", "--settings", '{"disableAllHooks":true}',
            "--setting-sources", "", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
            "--disable-slash-commands", "--tools", "", "--system-prompt", prompt_text,
            "--no-session-persistence", "--output-format", "json"]


_children = []
_evidence_dirs = []


def _kill_children(*_):
    for p in _children:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except OSError:
            pass


def _cleanup(*_):
    """On SIGTERM: stop Haiku and remove the evidence folder (record text)."""
    _kill_children()
    for d in list(_evidence_dirs):
        shutil.rmtree(d, ignore_errors=True)


def ask_haiku(evidence_text):
    """The note, or "" when `claude` is missing or the call fails. Never --bare:
    it drops the subscription login. The call runs in its own process group,
    which is killed on a timeout, and on a SIGTERM to the peek."""
    if not shutil.which("claude") or not os.path.exists(PROMPT):
        return ""
    with open(PROMPT) as fh:
        prompt_text = fh.read()
    with tempfile.TemporaryDirectory() as empty:
        proc = subprocess.Popen(haiku_command(prompt_text), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, cwd=empty, text=True, start_new_session=True,
                                env={**os.environ, "SCRY_PEEK_RUNNING": "1"})
        _children.append(proc)
        try:
            out, _ = proc.communicate(evidence_text, timeout=float(os.environ.get("SCRY_PEEK_HAIKU_TIMEOUT") or HAIKU_TIMEOUT))
            return (json.loads(out).get("result") or "").strip()
        except Exception:
            return ""
        finally:
            _kill_children()
            _children.remove(proc)


# ── Stored notes ────────────────────────────────────────────────────────────

def note_path(sid, aid):
    return os.path.join(state_dir(), sid, f"{aid}.json")


NOTE_KEEP_SECS = 7 * 86400


def sweep(root, keep=NOTE_KEEP_SECS):
    """Notes hold text drawn from the agent's record, which can be client
    material: none outlives a week."""
    cutoff = time.time() - keep
    for dirpath, _dirs, files in os.walk(root, topdown=False):
        for name in files:
            if name in ("events.log", "peek.lock"):
                continue
            p = os.path.join(dirpath, name)
            try:
                if os.path.getmtime(p) < cutoff:
                    os.unlink(p)
            except OSError:
                pass
        if dirpath != root:
            try:
                os.rmdir(dirpath)  # only when empty
            except OSError:
                pass


def maybe_sweep(root, keep=NOTE_KEEP_SECS):
    """`sweep`, at most once an hour per folder: hooks that run on every tool
    call must not walk the folder each time."""
    if not os.path.isdir(root):
        return
    mark = os.path.join(root, ".swept")
    try:
        if time.time() - os.path.getmtime(mark) < 3600:
            return
    except OSError:
        pass
    try:
        open(mark, "w").close()
    except OSError:
        return
    sweep(root, keep)


def sweep_evidence_dirs(older=3600):
    """Evidence folders a killed peek left behind: they hold record text."""
    for d in glob.glob(os.path.join(glob.escape(tempfile.gettempdir()), "scry-peek-*")):
        try:
            if os.path.isdir(d) and time.time() - os.path.getmtime(d) > older:
                shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass


def store_note(sid, aid, desc, facts, note, quiet_for, quiet_at=None):
    p = note_path(sid, aid)
    os.makedirs(state_dir(), exist_ok=True)
    sweep(state_dir())
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + f".{os.getpid()}.tmp"
    with open(tmp, "w") as fh:
        json.dump({"agent": aid, "desc": desc, "facts": facts, "note": note,
                   "quiet_for": quiet_for, "quiet_at": quiet_at, "at": time.time()}, fh)
    os.replace(tmp, p)  # a reader never sees half a note


def pending_path(sid, aid):
    return os.path.join(state_dir(), sid, f"{aid}.pending")


def write_pending(sid, aid, desc, facts, quiet_at):
    """What would be said if this peek died: its facts line, and its pid so
    delivery can tell a dead peek from a working one."""
    p = pending_path(sid, aid)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + f".{os.getpid()}.tmp"
    with open(tmp, "w") as fh:
        json.dump({"agent": aid, "desc": desc, "facts": facts, "note": "", "quiet_at": quiet_at,
                   "pid": os.getpid(), "at": time.time()}, fh)
    os.replace(tmp, p)


def clear_pending(sid, aid):
    try:
        os.unlink(pending_path(sid, aid))
    except OSError:
        pass


def _alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except PermissionError:
        return True  # it exists; it is another user's
    except (OSError, ValueError, TypeError):
        return False


def load_note(sid, aid):
    try:
        with open(note_path(sid, aid)) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def take_unsent_notes(sid):
    """Stored notes of this session not yet delivered, each claimed once by an
    exclusive create so two hooks landing together cannot both say it. The
    claim is per note, so a subagent that goes quiet again and is peeked again
    is told again."""
    out = []
    for p in sorted(glob.glob(os.path.join(glob.escape(state_dir()), glob.escape(sid), "*.json"))):
        try:
            with open(p) as fh:
                n = json.load(fh)
            os.close(os.open(f"{p[:-len('.json')]}.{n['at']}.delivered", os.O_CREAT | os.O_EXCL | os.O_WRONLY))
        except (OSError, ValueError, KeyError):
            continue
        out.append(n)
    for p in sorted(glob.glob(os.path.join(glob.escape(state_dir()), glob.escape(sid), "*.pending"))):
        try:
            with open(p) as fh:
                n = json.load(fh)
            if _alive(n.get("pid")):
                continue  # a peek still at work: its note is coming
            stored = load_note(sid, n.get("agent", ""))
            if stored and stored.get("at", 0) >= n.get("at", 0):
                os.unlink(p)  # its note was stored; the facts line is in it
                continue
            os.unlink(p)  # claimed by whoever removes it
        except (OSError, ValueError):
            continue
        n["note"] = ""
        out.append(n)
    return out


def format_note(n):
    named = f' "{n["desc"]}"' if n.get("desc") else ""
    quiet = time.time() - n["quiet_at"] if n.get("quiet_at") else (n.get("quiet_for") or 0)
    head = f"Scry — subagent {n['agent']}{named} has been silent {age(quiet)}. "
    body = n["facts"]
    if n.get("note"):
        body += "\n\nWhat it was doing, read from its record by a small model:\n" + n["note"]
    return head + body


def run_peek(sid, aid, jsonl, quiet_for=0, quiet_at=None):
    desc = agent_desc(jsonl)
    f, acc = scan(jsonl, 0, subagent=True)
    facts = acc.line(desc)
    write_pending(sid, aid, desc, facts, quiet_at)  # a death from here on still says this

    def facts_only(why):
        store_note(sid, aid, desc, facts, "", quiet_for, quiet_at)
        clear_pending(sid, aid)
        log_event(sid, aid, f"peek stored, facts only ({why})")

    if acc.handed_off():
        return facts_only("handed off")  # no model call
    if is_off():  # the switch was set while this peek queued
        return facts_only("switched off")
    evdir = tempfile.mkdtemp(prefix="scry-peek-")
    _evidence_dirs.append(evdir)
    ev = os.path.join(evdir, "evidence.txt")
    try:
        write_evidence(ev, f'subagent "{desc}"', f, "", [], "", 0, "", facts=facts)
        del f, acc
        if is_off():  # ... or while its evidence was written
            return facts_only("switched off")
        with open(ev) as fh:
            note = ask_haiku(fh.read())
    finally:
        shutil.rmtree(evdir, ignore_errors=True)
        _evidence_dirs.remove(evdir)
    store_note(sid, aid, desc, facts, note, quiet_for, quiet_at)
    clear_pending(sid, aid)
    log_event(sid, aid, "peek stored with note" if note else "peek stored, facts only (no note)")


LOCK_WAIT_SECS = 300


def _lock(fh, wait):
    deadline = time.time() + wait
    while True:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            if time.time() >= deadline:
                return False
            time.sleep(0.2)


def run_peek_safely(sid, aid, jsonl, quiet_for=0):
    """One peek at a time (a flock on the state directory), so several
    subagents going quiet together never hold several readers and several
    Haiku calls at once. The wait has a limit: a burst must not leave notes
    arriving long after the silence they describe, so a peek that cannot get
    the lock in time says its facts line alone. Whatever fails, the facts line
    is stored: the once-per-silence marker is already spent."""
    quiet_at = time.time() - float(quiet_for or 0)
    signal.signal(signal.SIGTERM, lambda *a: (_cleanup(), os._exit(143)))
    os.makedirs(state_dir(), exist_ok=True)
    trim_log()
    sweep_evidence_dirs()
    desc = agent_desc(jsonl)
    try:  # before any wait, so even a peek killed in the queue leaves something to say
        write_pending(sid, aid, desc, f'Subagent "{desc}": its record was not read before this peek ended.', quiet_at)
    except OSError:
        pass
    with open(os.path.join(state_dir(), "peek.lock"), "w") as lock:
        if not _lock(lock, float(os.environ.get("SCRY_PEEK_LOCK_WAIT") or LOCK_WAIT_SECS)):
            try:
                facts = scan(jsonl, 0, subagent=True)[1].line(desc)
            except Exception:
                facts = f'Subagent "{desc}": its record could not be read, so what it is doing is not known.'
            store_note(sid, aid, desc, facts, "", quiet_for, quiet_at)
            clear_pending(sid, aid)
            log_event(sid, aid, "peek timed out waiting its turn, facts only")
            return
        try:
            run_peek(sid, aid, jsonl, quiet_for, quiet_at)
        except Exception:
            try:
                facts = scan(jsonl, 0, subagent=True)[1].line(desc)
            except Exception:
                facts = "Subagent: its record could not be read, so what it is doing is not known."
            store_note(sid, aid, desc, facts, "", quiet_for, quiet_at)
            clear_pending(sid, aid)
            log_event(sid, aid, "peek failed, facts only")


if __name__ == "__main__":
    if len(sys.argv) >= 5 and sys.argv[1] == "run":
        if os.environ.get("SCRY_PEEK_RUNNING") or is_off():
            sys.exit(0)
        run_peek_safely(sys.argv[2], sys.argv[3], sys.argv[4], float(sys.argv[5]) if len(sys.argv) > 5 else 0)
