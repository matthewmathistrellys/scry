#!/usr/bin/env python3
"""roster.py — what a session started that has not finished, read from file
metadata and the process table, never from the conversation.

One list, three readers (2026-09-14, Matt: "we can see things that it forgot
about?" — "make it so"):

  cache_handoff_monitor.sh   near the cache deadline, to decide keep-alive
                             versus summary and to name the unfinished work.
  fleet.sh                   at SessionStart, so a resumed session is told
                             what it left running before it says "nothing".
  roster.sh                  on demand (the /scry skill, or any Bash call),
                             so "what is still running?" has an answer that
                             does not depend on the model's memory.

Why this exists: on 2026-09-13 session 62aa5e9f stopped four log-watchers,
missed a fifth, and told the user "nothing running" twice over a night. The
fifth watcher's output file had no exit marker the whole time. The session
had no list of its own Monitor tasks other than what it remembered creating;
the file system had one all along. Scry reports; the session judges.

What is listed, and what Scry can read for each:
  subagent            <session>/subagents/agent-*.jsonl — nothing in the
                      metadata says "finished", so listed only while written
                      within `fresh` seconds (one cache TTL by default).
  workflow            a journal with an agent `started` and no `result` —
                      unfinished by its own record, however quiet.
  background command  <tasks>/b*.output whose last 64 bytes carry no
                      "[exited with code" or "[killed]" marker — this is
                      also what a Monitor task (tail -f) looks like on disk.
                      Left out: a Scry monitor's own notification stream
                      (opens with "Scry — ", never records an exit while the
                      monitor lives) and a finished foreground command whose
                      result was persisted to tool-results/<id>.txt (the task
                      file stays behind with no marker). Both were listed as
                      "still running" when first read live, 2026-09-14.
  plugin monitor      a process under this Claude process running a script
                      from ~/.claude/plugins/cache/<market>/<plugin>/<ver>/.
                      Claude Code starts these once per process and never
                      restarts them, so the version in the path is what is
                      actually running; when it differs from the installed
                      version, that is said. (62aa5e9f ran a 1.29.0 monitor
                      all night while 1.31.0, which fixed its loop, had been
                      installed since the afternoon.)

Nothing here reads a prompt, a transcript line, or a task's output beyond
its last 64 bytes for the exit marker (never printed).
"""
import glob
import json
import os
import re
import subprocess
import sys
import time

SID_OK = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


def age(seconds):
    s = max(0, int(seconds))
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


def roster_items(sid, projects, task_roots, fresh=3600, launch=None, now=None):
    """Every unfinished worker `sid` started, newest activity first, as
    (mtime, line) pairs. File names, times and metadata labels only — no
    comparison with any earlier call, no judgement of progress."""
    now = int(now or time.time())
    launch = launch or sid
    items = []
    session_dirs = glob.glob(os.path.join(glob.escape(projects), "*", glob.escape(sid)))
    for sdir in session_dirs:
        sub = os.path.join(sdir, "subagents")
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
            items.append((m, f"subagent {aid}{named}: last activity {age(now - m)} ago"))
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
            # <session>/workflows/scripts/<name>-<run id>.js, written at launch.
            name = ""
            for script in glob.glob(os.path.join(glob.escape(sdir), "workflows", "scripts",
                                                 "*-" + glob.escape(wid) + ".js")):
                name = label(os.path.basename(script)[:-len("-" + wid + ".js")])
                break
            shown = f"{name} ({wid})" if name else wid
            done = len(finished & started)
            items.append((m, f"workflow {shown}: {done} of {len(started)} agents done, "
                             f"last activity {age(now - m)} ago"))
    seen = set()
    for root in (task_roots or "").split(":"):
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
                # Two files that look unfinished and are not (read live
                # 2026-09-14 in 62aa5e9f): a plugin monitor's own notification
                # stream, which never records an exit while the monitor lives
                # — Scry's is known by its own opening, "Scry — "; and a
                # finished foreground command whose large result Claude Code
                # persisted to <session>/tool-results/<task id>.txt, leaving
                # the task file behind with no marker.
                try:
                    with open(out, "rb") as fh:
                        head = fh.read(8)
                except OSError:
                    continue
                if head.startswith("Scry — ".encode("utf-8")[:8]):
                    continue
                if any(os.path.exists(os.path.join(sdir, "tool-results", tid + ".txt"))
                       for sdir in session_dirs):
                    continue
                items.append((m, f"background command {tid}: still running, "
                                 f"last output {age(now - m)} ago"))
    items.sort(key=lambda x: -x[0])
    return items


def roster_lines(*args, **kwargs):
    return [text for _, text in roster_items(*args, **kwargs)]


_PLUGIN_PATH = re.compile(r"/plugins/cache/([^/\s'\"]+)/([^/\s'\"]+)/([^/\s'\"]+)/([^\s'\"]+)")


def installed_versions(installed_json):
    """{plugin name: installed version} from Claude Code's own record."""
    out = {}
    try:
        with open(installed_json) as f:
            rec = json.load(f)
    except Exception:
        return out
    plugins = rec.get("plugins", rec) if isinstance(rec, dict) else {}
    for key, entries in (plugins.items() if isinstance(plugins, dict) else []):
        name = str(key).split("@", 1)[0]
        for e in entries if isinstance(entries, list) else [entries]:
            if isinstance(e, dict) and e.get("version"):
                out[name] = str(e["version"])
    return out


def plugin_monitors(claude_pid, installed_json, ps_text=None):
    """Plugin monitor processes running under this Claude process, as lines.
    A monitor is a child (or grandchild, through the shell wrapper Claude
    Code starts it with) whose command names a script under the plugin
    cache. The version in that path is the version running."""
    if not str(claude_pid or "").isdigit():
        return []
    if ps_text is None:
        try:
            ps_text = subprocess.run(
                ["ps", "-axo", "pid=,ppid=,lstart=,command="],
                capture_output=True, text=True, timeout=10).stdout
        except Exception:
            return []
    procs = []
    for line in ps_text.splitlines():
        parts = line.split(None, 7)
        if len(parts) < 8 or not parts[0].isdigit() or not parts[1].isdigit():
            continue
        # lstart is five tokens: "Sun Sep 13 11:31:51 2026".
        procs.append((int(parts[0]), int(parts[1]), " ".join(parts[2:7]), parts[7]))
    root = int(claude_pid)
    children = {p for p, pp, _, _ in procs if pp == root}
    family = children | {p for p, pp, _, _ in procs if pp in children}
    versions = installed_versions(installed_json)
    lines, seen = [], set()
    for p, _, started, cmd in procs:
        if p not in family:
            continue
        m = _PLUGIN_PATH.search(cmd)
        if not m:
            continue
        _, plugin, version, script = m.groups()
        # The shell wrapper and the script it runs both name the path; one
        # line per script.
        key = (plugin, version, script)
        if key in seen:
            continue
        seen.add(key)
        when = started.split()
        when = f"{when[1]} {when[2]} {when[3][:5]}" if len(when) == 5 else started
        installed = versions.get(plugin)
        drift = ""
        if installed and installed != version:
            drift = (f"; installed is v{installed} — a monitor keeps the copy its "
                     f"session launched with until the session restarts")
        lines.append(f"plugin monitor {plugin}/{script} (v{version}, started {when}{drift})")
    return lines


def current_session(env=None, sessions_dir=None, state_dir=None):
    """The session this Claude process is running now: Claude Code's own
    per-process record, then the arm hook's, then the environment id — the
    same resolution cache_handoff_monitor.sh uses, since /clear changes the
    id without restarting the process."""
    env = env if env is not None else os.environ
    launch = env.get("CLAUDE_CODE_SESSION_ID", "")
    pid = env.get("CLAUDE_PID", "")
    found = []
    if pid.isdigit():
        p = os.path.join(sessions_dir or os.path.join(os.path.expanduser("~"), ".claude", "sessions"),
                         pid + ".json")
        try:
            with open(p) as f:
                s = json.load(f).get("sessionId")
            if isinstance(s, str) and SID_OK.match(s):
                found.append((os.path.getmtime(p), 1, s))
        except Exception:
            pass
        if state_dir:
            p = os.path.join(state_dir, "pid", pid)
            try:
                with open(p) as f:
                    parts = f.read().split()
                if parts and SID_OK.match(parts[0]):
                    found.append((os.path.getmtime(p), 0, parts[0]))
            except Exception:
                pass
    if found:
        return max(found)[2], launch
    return launch, launch


def main():
    env = os.environ
    home = os.path.expanduser("~")
    sid = ""
    # A hook payload on stdin names the session; a terminal does not.
    if not sys.stdin.isatty():
        try:
            d = json.loads(sys.stdin.read() or "{}")
            sid = str(d.get("session_id") or "")
        except Exception:
            sid = ""
    if not sid or not SID_OK.match(sid) or sid == "on-demand":
        sid, launch = current_session(
            env,
            sessions_dir=env.get("SCRY_CLAUDE_SESSIONS_DIR"),
            state_dir=env.get("SCRY_CACHE_STATE_DIR") or os.path.join(
                env.get("TMPDIR") or "/tmp", "scry-cache-deadline"))
    else:
        launch = env.get("CLAUDE_CODE_SESSION_ID") or sid
    if not sid or not SID_OK.match(sid):
        print("Scry — roster: no session id (CLAUDE_CODE_SESSION_ID unset and no payload); "
              "nothing can be attributed.")
        return 0
    uid = os.getuid() if hasattr(os, "getuid") else 0
    task_roots = env.get("SCRY_TASK_STATE_DIR") or ":".join(
        [os.path.join(env.get("TMPDIR") or "/tmp", f"claude-{uid}"), f"/tmp/claude-{uid}"])
    projects = env.get("SCRY_CLAUDE_PROJECTS_DIR") or os.path.join(home, ".claude", "projects")
    fresh = env.get("SCRY_KEEPALIVE_FRESH_SECS", "3600")
    fresh = int(fresh) if fresh.isdigit() else 3600
    lines = roster_lines(sid, projects, task_roots, fresh=fresh, launch=launch)
    installed = env.get("SCRY_INSTALLED_PLUGINS") or os.path.join(
        home, ".claude", "plugins", "installed_plugins.json")
    ps_text = env.get("SCRY_PS_OUTPUT")  # tests inject a process table
    lines += plugin_monitors(env.get("CLAUDE_PID", ""), installed, ps_text)
    if not lines:
        print(f"Scry — roster for session {sid}: nothing this session started is "
              "still unfinished, as far as file metadata and the process table show.")
        return 0
    print(f"Scry — roster for session {sid}, as far as Scry can tell from file "
          "metadata and the process table (the session judges; Scry stops nothing):")
    for line in lines:
        print(f"- {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
