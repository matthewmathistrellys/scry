#!/usr/bin/env python3
"""Read every Claude session in the chosen herdr workspaces, or on this machine without herdr, without waking any of them.

Read-only sources: `herdr agent list`, `herdr agent read` (screen), the session
registry in ~/.claude/sessions, each session's transcript tail, and git in the
tab's folder. Nothing is sent to a tab.

Prints a count line, then one block per tab that changed since the last run
(state file keyed on session id), then flags. With --evidence-dir, writes one
file per changed tab holding everything the tab said and was told since the
last run, for a model to read and summarize.

usage: scrum-gather.py [--workspace w0,w1] [--name a,b] [--skip own-tab]
                       [--all] [--state FILE] [--evidence-dir DIR] [--lines 12]
"""
import argparse, collections, glob, json, os, re, subprocess, sys, time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
from peek import clock, one_line, session_path, transcript_facts, ts_of, write_evidence

HOME = os.path.expanduser("~")
BIG_CTX = 250_000
NO_WAKE_CTX = 600_000
COLD_BIG_CTX = 300_000
SILENT_S = 1800
FIRST_WINDOW_S = 6 * 3600


def run(cmd, cwd=None, timeout=20):
    try:
        return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout).stdout
    except Exception:
        return ""


def screen(name, lines):
    out = run(["herdr", "agent", "read", name, "--lines", str(lines)])
    recap = next((one_line(l, 300) for l in out.splitlines() if "recap:" in l), None)
    raw = next((one_line(l, 200) for l in out.splitlines() if "$" in l and "k/" in l), "")
    m = re.search(r"((?:Opus|Sonnet|Fable|Haiku)[^$]*\$[0-9.]+).*?(\d+k/\d+k)", raw)
    bar = f"{m.group(1)} {m.group(2)}" if m else raw or None
    lines_ = out.splitlines()
    unsent = None
    for i in range(len(lines_) - 1, -1, -1):
        if lines_[i].strip().startswith("❯"):
            below = [l for l in lines_[i + 1:i + 3] if l.strip()]
            if below and set(below[0].strip()) <= set("─━ "):
                unsent = one_line(lines_[i].strip().lstrip("❯ "), 240) or None
            break
    noise = ("monitor", "auto mode", "⏵", "recap:", "disable recaps", "Update installed")
    tail = [one_line(l, 160) for l in out.splitlines()
            if l.strip() and not set(l.strip()) <= set("─━ ") and not l.strip().startswith("❯")
            and not any(n in l for n in noise) and not ("$" in l and "k/" in l)][-3:]
    return recap, bar, tail, unsent


def registry_agents(reg):
    """Every live interactive session in the registry, shaped like a herdr agent row."""
    status_map = {"busy": "working", "waiting": "blocked"}
    rows = []
    for sid, r in reg.items():
        if r.get("kind") not in (None, "interactive") or not alive(r.get("pid")):
            continue
        name = r.get("name") or str(r.get("pid"))
        rows.append({"name": name, "workspace_id": "-", "pane_id": str(r.get("pid")), "terminal_title_stripped": name,
                     "agent_status": status_map.get(r.get("status"), r.get("status") or "unknown"),
                     "agent_session": {"value": sid}, "cwd": r.get("cwd"), "state_change_seq": r.get("updatedAt")})
    return rows


def registry():
    reg = {}
    for p in glob.glob(f"{HOME}/.claude/sessions/*.json"):
        try:
            d = json.load(open(p))
            if d.get("sessionId"):
                reg[d["sessionId"]] = d
        except Exception:
            continue
    return reg


def alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except Exception:
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", default="")
    ap.add_argument("--name", default="")
    ap.add_argument("--skip", default="", help="names to leave out, e.g. the orchestrator's own tab")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--no-herdr", action="store_true", help="list sessions from the session registry instead of herdr (any terminal: iTerm, Terminal, tmux)")
    ap.add_argument("--until", default="", help="ignore anything after this ISO time (for fixed-window tests)")
    ap.add_argument("--state", default=f"{HOME}/.claude/scrum-state.json")
    ap.add_argument("--evidence-dir", default="")
    ap.add_argument("--lines", type=int, default=12)
    a = ap.parse_args()
    ws = {x for x in a.workspace.split(",") if x}
    names = {x for x in a.name.split(",") if x}
    norm = lambda v: re.sub(r"[^a-z0-9]+", "", v.lower())
    skip = {norm(x) for x in a.skip.split(",") if x}
    try:
        state = json.load(open(a.state))
    except Exception:
        state = {}

    reg = registry()
    herdr_out = "" if a.no_herdr else run(["herdr", "agent", "list"])
    use_herdr = bool(herdr_out.strip())
    agents = json.loads(herdr_out).get("result", {}).get("agents", []) if use_herdr else registry_agents(reg)
    if not use_herdr:
        print("# herdr not used: sessions come from the session registry; screen reads (recap, unsent input, background shells, blocked menu) are unavailable.")
    for x in agents:
        x.setdefault("name", x["pane_id"])
        x.setdefault("terminal_title_stripped", "")
    agents = [x for x in agents if ((not ws and not names) or x["workspace_id"] in ws or x["name"] in names)
              and norm(x["name"]) not in skip and norm(x["terminal_title_stripped"]) not in skip]
    if a.evidence_dir:
        os.makedirs(a.evidence_dir, exist_ok=True)
    now = time.time()
    blocks, flags, counts = [], [], collections.Counter()
    changed_n = 0
    quiet_files = []
    real_n = 0
    for x in agents:
        sid = x["agent_session"]["value"]
        prev = state.get(sid, {})
        since = prev.get("checked", now - FIRST_WINDOW_S)
        f = transcript_facts(session_path(sid), since, ts_of(a.until) if a.until else 0)
        commits = run(["git", "log", f"--since=@{int(since)}", "--oneline", "-n", "8"], cwd=x.get("cwd")).strip()
        if use_herdr:
            recap, bar, tail, unsent = screen(x["name"], a.lines)
            tail_full = run(["herdr", "agent", "read", x["name"], "--lines", "40"]).splitlines()
        else:
            recap, bar, tail, unsent, tail_full = None, None, [], None, []
        if not f["ctx"]:
            m = re.search(r"(\d+)k/", bar or "")
            f["ctx"] = int(m.group(1)) * 1000 if m else 0
        status = x["agent_status"]
        for l in tail_full:
            mm = re.search(r"(\d+)h ?(\d+)m", l)
            if mm and "tokens" in l and int(mm.group(1)) >= 1:
                flags.append(f"LONG BACKGROUND AGENT: {x['name']}: {one_line(l, 140)}")
        r = reg.get(sid, {})
        waiting = r.get("status") == "waiting"
        dead = bool(r) and not alive(r.get("pid"))
        sig = [status, f["last_ts"], commits]
        first = not prev
        changed = first or sig != prev.get("sig")
        real = f["turns"] > 0 or bool(commits) or (not first and sig != prev.get("sig"))
        state[sid] = {"checked": now, "sig": sig, "name": x["name"]}
        idle_s = now - f["last_ts"] if f["last_ts"] else 0
        cold = idle_s > f["ttl"]
        label = f"{x['name']} ({x['terminal_title_stripped']})"
        shown_status = "idle at prompt" if status == "done" else status
        shells = re.search(r"(\d+) shells?", "\n".join(tail_full[-12:]))
        counts["waiting/blocked" if (status == "blocked" or waiting) else shown_status] += 1
        if unsent:
            flags.append(f"UNSENT TEXT in the input box (typed, not sent; nothing ran from it): {label}: \"{unsent}\"")
        if status == "blocked":
            ask = [one_line(l, 160) for l in tail_full if l.strip() and not set(l.strip()) <= set("─━ ") and "k/" not in l][-15:]
            flags.append(f"BLOCKED: {label}: {' | '.join(ask)}")
        if waiting:
            flags.append(f"WAITING for an answer (permission or question): {label}")
        if dead:
            flags.append(f"NOT RUNNING: {label} has no live process")
        if status == "working" and idle_s > SILENT_S:
            flags.append(f"SILENT: {label} says working but has been quiet {idle_s / 60:.0f} min")
        if f["ctx"] > BIG_CTX:
            flags.append(f"OVER 250k context: {label} at {f['ctx'] // 1000}k")
        if status != "working" and (f["ctx"] > NO_WAKE_CTX or (cold and f["ctx"] > COLD_BIG_CTX)):
            flags.append(f"DO NOT WAKE: {label} (cache {'cold' if cold else 'warm'}, {f['ctx'] // 1000}k, idle {idle_s / 3600:.1f}h); a message rewrites its whole context")
        if not (changed or a.all):
            if a.evidence_dir:
                ep = os.path.join(a.evidence_dir, re.sub(r"[^A-Za-z0-9_-]+", "-", x["name"]) + ".md")
                write_evidence(ep, label, f, commits, tail, recap, since, unsent)
                quiet_files.append(f"{label} [{shown_status}] no change since {clock(since)}, context {f['ctx'] // 1000}k: {ep}")
            continue
        changed_n += 1
        real_n += real
        branch = f["branch"] if f["branch"] not in (None, "HEAD") else "detached/no repo"
        blk = [f"## {label} [{shown_status}{', ' + shells.group(1) + ' background shell(s) running' if shells else ''}] ws={x['workspace_id']} last turn {clock(f['last_ts'])} ({idle_s / 60:.0f}m ago), "
               f"{f['turns']} turns since, context {f['ctx'] // 1000}k, cache {'cold' if cold else 'warm'}",
               f"- branch {branch}; bar: {bar}"]
        if a.evidence_dir:
            ep = os.path.join(a.evidence_dir, re.sub(r"[^A-Za-z0-9_-]+", "-", x["name"]) + ".md")
            write_evidence(ep, label, f, commits, tail, recap, since, unsent)
            blk.append(f"- evidence: {ep}")
        else:
            blk += [f"- said: {one_line(t, 400)}" for _, t in f["texts"][-3:]]
        blocks.append("\n".join(blk))
    print(f"# {len(agents)} tabs: " + ", ".join(f"{n} {k}" for k, n in counts.most_common()) + f"; {real_n} with new activity, {changed_n - real_n} first look or quiet, window since each tab's last scrum (6h on a first look)")
    print("\n\n".join(blocks))
    if quiet_files:
        print("\n# No new turns since the last check; each still has an evidence file with its last known messages, so it gets a short note marked 'no change since <time>':")
        for q in quiet_files:
            print(f"- {q}")
    print()
    for fl in flags:
        print(f"- {fl}")
    if os.environ.get("SCRUM_NO_SAVE") != "1":
        os.makedirs(os.path.dirname(a.state), exist_ok=True)
        json.dump(state, open(a.state, "w"))


if __name__ == "__main__":
    main()
