#!/usr/bin/env python3
"""
Engine state — the one place that answers "how is the production engine, and
how many sessions from this Mac are open on it?"

Read by engine.sh (SessionStart) and engine_guard.sh (PreToolUse on Bash).
Both ask the same two questions, so both ask this module.

The state is shared with the machine-level engine-guard that wraps `fly`
(~/.engine-guard, or $ENGINE_GUARD_HOME): its health reading, its slot files,
its slot count and its OFF file. Scry reads them and writes only the health
reading, in the same format, so the wrapper and Scry share one `fly checks
list` call per minute. Scry never takes a slot and never removes one; a slot
whose process is gone is skipped, not cleaned.

Silent unless ~/.engine-guard exists: a machine without the guard has no
engine to ask about.

Run as a script: `engine.py status` prints one JSON object.
"""

import contextlib
import errno
import fcntl
import json
import os
import re
import shlex
import stat
import subprocess
import sys
import time

DEFAULT_APP = "trellys-engine"
DEFAULT_SLOTS = 4
HEALTH_TTL = 60        # a passing or critical reading is reused this long
UNKNOWN_TTL = 10       # a failed reading, so a Fly outage costs one timeout, not one per call
FLY_TIMEOUT = 10
LOCK_WAIT = 25         # seconds to wait for another session's reading


def ghome():
    return os.environ.get("ENGINE_GUARD_HOME") or os.path.expanduser("~/.engine-guard")


def app():
    return os.environ.get("SCRY_ENGINE_APP") or DEFAULT_APP


def enabled():
    if os.environ.get("SCRY_ENGINE_GUARD", "1") == "0":
        return False
    home = ghome()
    return os.path.isdir(home) and not os.path.exists(os.path.join(home, "OFF"))


def _real_fly():
    explicit = os.environ.get("ENGINE_GUARD_REAL_FLY")
    if explicit:
        return explicit if os.access(explicit, os.X_OK) else None
    cellar = "/opt/homebrew/Cellar/flyctl"
    candidates = ["/opt/homebrew/opt/flyctl/bin/flyctl"]
    try:
        versions = sorted(os.listdir(cellar), reverse=True)
    except OSError:
        versions = []
    candidates += [os.path.join(cellar, v, "bin", "flyctl") for v in versions]
    for path in candidates:
        if not os.access(path, os.X_OK):
            continue
        try:
            with open(path, "rb") as f:
                if f.read(2) == b"#!":      # the wrapper itself: never call it
                    continue
        except OSError:
            continue
        return path
    return None


def _statuses(node, out):
    if isinstance(node, dict):
        if "status" in node and "name" in node:
            out.append((str(node["status"]), str(node["name"])))
        for v in node.values():
            _statuses(v, out)
    elif isinstance(node, list):
        for v in node:
            _statuses(v, out)


def _fresh_reading():
    real = _real_fly()
    if not real:
        return "unknown", "the real fly binary was not found"
    try:
        run = subprocess.run([real, "checks", "list", "-a", app(), "--json"],
                             capture_output=True, text=True, timeout=FLY_TIMEOUT)
    except subprocess.TimeoutExpired:
        return "unknown", f"fly checks list did not answer in {FLY_TIMEOUT}s"
    except OSError as e:
        return "unknown", f"fly checks list could not start ({e.__class__.__name__})"
    if run.returncode != 0:
        tail = " ".join((run.stdout + run.stderr).split())[:120]
        return "unknown", f"fly checks list failed (exit {run.returncode}): {tail}"
    try:
        found = []
        _statuses(json.loads(run.stdout), found)
    except ValueError:
        return "unknown", "the health check answer could not be read"
    if not found:
        return "unknown", "Fly returned no health checks (is the machine stopped?)"
    detail = ",".join(f"{s}={n}" for s, n in found)
    if any(s == "critical" for s, _ in found):
        return "critical", detail
    return "passing", detail


def _cached_reading(cache):
    try:
        with open(cache, encoding="utf-8") as f:
            parts = f.readline().rstrip("\n").split(" ", 2)
        age = time.time() - int(parts[0])
        ttl = UNKNOWN_TTL if parts[1] == "unknown" else HEALTH_TTL
        if 0 <= age < ttl:
            return parts[1], (parts[2] if len(parts) > 2 else "").replace("_", " "), int(age)
    except (OSError, ValueError, IndexError):
        pass
    return None


@contextlib.contextmanager
def _reading_lock():
    """The lock the machine-level guard takes before it reads health, so a
    burst of sessions makes one `fly checks list` call. Waits up to LOCK_WAIT
    seconds, then goes on without it: a stuck lock never stops a command."""
    handle = None
    try:
        handle = open(os.path.join(ghome(), "lock"), "a")
        deadline = time.time() + LOCK_WAIT
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as e:
                if e.errno not in (errno.EAGAIN, errno.EACCES) or time.time() >= deadline:
                    break
                time.sleep(0.1)
    except OSError:
        pass
    try:
        yield
    finally:
        if handle:
            handle.close()


def read_health():
    """(status, detail, age_seconds). status: passing | critical | unknown."""
    cache = os.path.join(ghome(), "health")
    got = _cached_reading(cache)
    if got:
        return got
    with _reading_lock():
        got = _cached_reading(cache)       # the session that held the lock wrote one
        if got:
            return got
        status, detail = _fresh_reading()
        try:
            tmp = f"{cache}.scry{os.getpid()}"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(f"{int(time.time())} {status} {detail.replace(' ', '_')}\n")
            os.replace(tmp, cache)
        except OSError:
            pass
    return status, detail, 0


def limit():
    try:
        with open(os.path.join(ghome(), "slots"), encoding="utf-8") as f:
            n = int(re.sub(r"\D", "", f.read()) or 0)
        if n > 0:
            return n
    except (OSError, ValueError):
        pass
    return DEFAULT_SLOTS


def _ps(*args):
    try:
        return subprocess.run(["ps", *args], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def open_sessions():
    """Live engine connections from this Mac: slot files whose process is
    still the one that took the slot, plus fly processes to the engine that
    started without the guard. A list of one-line descriptions."""
    held, out = set(), []
    slots = os.path.join(ghome(), "slots.d")
    try:
        names = sorted(os.listdir(slots))
    except OSError:
        names = []
    for name in names:
        if not name.isdigit():
            continue
        try:
            with open(os.path.join(slots, name), encoding="utf-8", errors="replace") as f:
                lines = f.read().split("\n")
        except OSError:
            continue
        started = _ps("-o", "lstart=", "-p", name).strip()
        if started and started == (lines[0] if lines else "").strip():
            held.add(name)
            try:
                age = int(time.time() - int(lines[1]))
            except (ValueError, IndexError):
                age = 0
            out.append(f"pid {name}, open {age}s, from: {lines[2] if len(lines) > 2 else '?'}")
    target = re.compile(r"(?:^|\s)(?:-a|--app)[ =]?" + re.escape(app()) + r"(?:\s|$)")
    verb = re.compile(r"\s(?:ssh\s+console|(?:machines?|m)\s+exec)(?:\s|$)")
    for line in _ps("-axww", "-o", "pid=,args=").splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) < 2 or parts[0] in held:
            continue
        first = parts[1].split(None, 1)[0]
        if os.path.basename(first) not in ("fly", "flyctl"):
            continue
        if verb.search(" " + parts[1]) and target.search(parts[1]):
            out.append(f"pid {parts[0]}, started without the guard")
    return out


def lapsed_wrappers():
    """Wrapper files listed in ~/.engine-guard/wrappers that no longer carry
    the guard (a flyctl upgrade puts the plain link back)."""
    lapsed = []
    try:
        with open(os.path.join(ghome(), "wrappers"), encoding="utf-8") as f:
            paths = [p.strip() for p in f if p.strip()]
    except OSError:
        return lapsed
    for path in paths:
        try:
            with open(path, "rb") as f:
                text = f.read(4096)
        except OSError:
            lapsed.append(path)
            continue
        if b"engine-guard" not in text:
            lapsed.append(path)
    return lapsed


# ── Which commands reach the engine ─────────────────────────────────────────
_GAP = r"""[\s'",\[]+"""
_CONNECT = re.compile(r"ssh" + _GAP + r"console|machines?" + _GAP + r"exec|\bm" + _GAP + r"exec")
# A file or code string connects when it calls fly that way and names the app.
_FLY_CALL = re.compile(r"\bfly(?:ctl)?\b" + _GAP + r"(?:-\w+" + _GAP + r"[^\s'\",\[]+" + _GAP + r")*(?:ssh" + _GAP
                       + r"console|machines?" + _GAP + r"exec|m" + _GAP + r"exec)")
_SEPARATORS = {";", "&&", "||", "|", "|&", "&", "(", ")"}
_WRAPPERS = {"env", "command", "exec", "time", "nohup", "builtin", "sudo", "xargs", "timeout", "nice",
             "caffeinate", "eval",
             "do", "then", "if", "elif", "else", "while", "until", "!", "{", "}"}
_WRAPPER_VALUE_FLAGS = {"sudo": {"-u", "-g", "-C", "-h", "-p", "-U", "-D"}, "nice": {"-n"},
                        "timeout": {"-s", "-k", "--signal", "--kill-after"},
                        "xargs": {"-I", "-n", "-P", "-L", "-s", "-d", "-a", "-E"},
                        "env": {"-u", "-C", "-S"}, "caffeinate": {"-t", "-w"}}
_SHELLS = {"bash", "sh", "zsh", "dash", "ksh"}
_INTERPRETERS = _SHELLS | {"python", "python3", "ruby", "node", "perl", "elixir"}
_FLY_VALUE_FLAGS = {"-t", "--access-token", "-o", "--org", "-r", "--region", "-m", "--machine", "-C",
                    "--command", "--config"}
_SCRY_ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))


def _split_lines(command):
    """Unquoted newlines separate commands; newlines inside quotes do not."""
    out, quote, i = [], "", 0
    while i < len(command):
        c = command[i]
        if c == "\\" and quote != "'" and i + 1 < len(command):
            out.append(command[i:i + 2])
            i += 2
            continue
        if quote:
            if c == quote:
                quote = ""
        elif c in "'\"":
            quote = c
        elif c == "#" and (i == 0 or command[i - 1] in " \t\n;&|()"):
            end = command.find("\n", i)         # a comment runs to the end of its line
            i = len(command) if end < 0 else end
            continue
        elif c == "\n":
            c = " ; "
        out.append(c)
        i += 1
    return "".join(out)


MAX_PARSED = 200_000                    # shlex is quadratic in the length of one word


def _tokens(command):
    """The command's words; None when it cannot be parsed (an unbalanced
    quote, often an apostrophe in a comment)."""
    if len(command) > MAX_PARSED:
        return []
    lex = shlex.shlex(_split_lines(command.replace("\\\n", " ")), posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    lex.commenters = ""                 # comments were dropped by _split_lines, which knows the quotes
    try:
        return list(lex)
    except ValueError:
        return None


def _segments(command):
    segments, current, skip = [], [], False
    for tok in _tokens(command) or []:
        if skip:
            skip = False
        elif tok in _SEPARATORS:
            segments.append(current)
            current = []
        elif re.fullmatch(r"[<>]+", tok):
            skip = True                 # a redirection and its target
        else:
            if re.match(r"\w+=[^`]*`", tok):
                tok = tok.split("`", 1)[1]      # out=`fly ...`: the command inside the backticks
                current = []
            elif tok.startswith("`") and current:
                segments.append(current)
                current = []
            tok = tok.strip("`")
            if tok:
                current.append(tok)
    segments.append(current)
    return [seg for seg in segments if seg]


_HEREDOC = re.compile(r"<<-?\s*(['\"]?)([^\s'\"<>;&|()]+)\1([^\n]*)\n(.*?)(?:\n[ \t]*\2[ \t]*(?=\n|$))", re.S)


def _strip_heredocs(command, cwd, depth):
    """(command without heredoc bodies, what an interpreter reading one runs).
    The body of `cat <<EOF` is text; the body of `bash <<EOF` is a program."""
    found = []

    def take(m):
        before = command[:m.start()].rsplit("\n", 1)[-1]
        parts = _segments(before)
        words = parts[-1] if parts else []
        while words and (re.fullmatch(r"\w+=.*", words[0]) or words[0] in _WRAPPERS):
            words = words[1:]
        if words and os.path.basename(words[0]) in _INTERPRETERS and not found:
            if os.path.basename(words[0]) in _SHELLS:
                got = engine_command(m.group(4), cwd, depth + 1)
                if got:
                    found.append("heredoc")
            elif _text_connects(m.group(4)):
                found.append("heredoc")
        return m.group(3)

    return _HEREDOC.sub(take, command), (found[0] if found else "")


def _text_connects(text):
    named = re.search(r"(?<![\w-])" + re.escape(app()) + r"(?![\w-])", text)
    return bool(named and _FLY_CALL.search(text))


def _toml_app(path):
    if not _regular(path):
        return ""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                m = re.match(r"\s*app\s*=\s*[\"']?([^\"'\s#]+)", line)
                if m:
                    return m.group(1)
    except OSError:
        pass
    return ""


def _expand(word, variables):
    return re.sub(r"\$(?:\{(\w+)\}|(\w+))", lambda m: variables.get(m.group(1) or m.group(2), m.group(0)), word)


def _regular(path):
    try:
        return stat.S_ISREG(os.stat(path).st_mode)       # a FIFO would block the read
    except (OSError, ValueError):
        return False


def _fly_targets_engine(words, cwd, env_app=""):
    """True when fly's own arguments are `ssh console` / `machine exec` and the
    app is the engine: named with -a or --app, else FLY_APP, else the app in
    the fly.toml fly would read (-c/--config, or fly.toml in the folder)."""
    verbs, target, config, i = [], "", "", 0
    while i < len(words):
        w = words[i]
        if w in ("-a", "--app", "-c", "--config") and i + 1 < len(words):
            if w in ("-a", "--app"):
                target = words[i + 1]
            else:
                config = words[i + 1]
            i += 2
            continue
        if w.startswith("--app="):
            target = w[6:]
        elif w.startswith("--config="):
            config = w[9:]
        elif w.startswith("-a=") or (w.startswith("-a") and len(w) > 2 and not w.startswith("--")):
            target = w[3:] if w.startswith("-a=") else w[2:]
        elif w in _FLY_VALUE_FLAGS and i + 1 < len(words):
            i += 2
            continue
        elif not w.startswith("-"):
            verbs.append(w)
        i += 1
    if not _CONNECT.search(" ".join(verbs[:2])):
        return False
    if not target:
        target = env_app or os.environ.get("FLY_APP", "")
    if not target:
        path = os.path.expanduser(config or "fly.toml")
        target = _toml_app(path if os.path.isabs(path) else os.path.join(cwd, path))
    return target == app()


def _script_connects(path, cwd):
    path = os.path.expanduser(path)
    if not os.path.isabs(path):
        path = os.path.join(cwd, path)
    # Scry's own files describe the connection in prose and constants.
    try:
        if os.path.realpath(path).startswith(_SCRY_ROOT + os.sep) or not _regular(path):
            return False
    except ValueError:
        return False
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return _text_connects(f.read(2_000_000))
    except OSError:
        return False


def _script_word(rest):
    """The file an interpreter runs: the first word that is not a flag or the
    value of -o."""
    for i, w in enumerate(rest):
        if w.startswith("-"):
            continue
        if i and re.fullmatch(r"-[a-zA-Z]*o", rest[i - 1]):
            continue
        return w
    return ""


def _code_string(rest, letters="c"):
    """The argument after the flag that takes code, however it is grouped
    (-c, -lc, -e -u -c, -euo pipefail -c)."""
    for i, w in enumerate(rest):
        if not w.startswith("-"):
            if i and re.fullmatch(r"-[a-zA-Z]*o", rest[i - 1]):
                continue                # the value of -o
            return ""
        if not w.startswith("--") and any(c in w[1:] for c in letters) and i + 1 < len(rest):
            return rest[i + 1]
    return ""


def engine_command(command, cwd, depth=0):
    """What in `command` opens an engine session: "this command" for a typed
    fly call, the script file or code it runs, "" when nothing does. Reads
    the command's words, so a commit message, a heredoc fed to `cat`, a `cat`
    of a script or another app's name never counts."""
    command, heredoc = _strip_heredocs(command, cwd, depth)
    if heredoc:
        return heredoc
    if len(command) <= MAX_PARSED and _tokens(command) is None:
        # Unbalanced quotes that are not a comment or a heredoc: read it as text.
        return "this command" if _text_connects(command) else ""
    shell_app, shell_vars = "", {}
    for words in _segments(command):
        if depth < 2:
            for w in words:
                start = w.find("$(")
                if start >= 0:
                    found = engine_command(w[start + 2:w.rfind(")") if ")" in w else len(w)], cwd, depth + 1)
                    if found:
                        return found
        env_app = ""
        while words and (re.fullmatch(r"\w+=.*", words[0]) or os.path.basename(words[0]) in _WRAPPERS
                         or words[0] == "export"):
            if re.fullmatch(r"\w+=.*", words[0]):
                name, _, value = words[0].partition("=")
                shell_vars[name] = value
                if name == "FLY_APP":
                    env_app = value
                    if len(words) == 1:
                        shell_app = value   # `FLY_APP=x` alone holds for the commands after it
                words = words[1:]
            elif words[0] == "export":
                words = words[1:]
            else:
                if words[0] == "eval":
                    return engine_command(" ".join(words[1:]), cwd, depth + 1) if depth < 2 else ""
                # skip the wrapper's own flags (and their values) to the command it runs
                wrapper = os.path.basename(words[0])
                words = words[1:]
                takes = _WRAPPER_VALUE_FLAGS.get(wrapper, set())
                while words and (words[0].startswith("-") or re.fullmatch(r"[\d.]+[smhd]?", words[0])):
                    flag = words[0]
                    words = words[1:]
                    if flag in takes and words:
                        words = words[1:]
        if not words:
            continue
        first = os.path.basename(words[0])
        rest = words[1:]
        if first in ("cd", "pushd"):
            if rest:
                target = os.path.expanduser(rest[0])
                cwd = target if os.path.isabs(target) else os.path.join(cwd, target)
        elif first in ("fly", "flyctl"):
            rest = [_expand(w, shell_vars) for w in rest]
            if _fly_targets_engine(rest, cwd, env_app or shell_app):
                return "this command"
        elif first in ("source", ".") and rest:
            if _script_connects(rest[0], cwd):
                return rest[0]
        elif first in _INTERPRETERS:
            code = _code_string(rest, "c" if first in _SHELLS else "ce")
            if code:
                if first in _SHELLS and depth < 2:
                    found = engine_command(code, cwd, depth + 1)
                    if found:
                        return found
                elif first not in _SHELLS and _text_connects(code):
                    return "the code given to " + first
            elif rest and rest[0] != "-m":
                script = _script_word(rest)
                if script and _script_connects(script, cwd):
                    return script
        elif first in ("mix", "uv") and rest[:1] == ["run"]:
            tail = rest[1:]
            if first == "uv" and tail and os.path.basename(tail[0]).startswith("python"):
                tail = tail[1:]
            script = _script_word(tail)
            if script and _script_connects(script, cwd):
                return script
        elif "/" in words[0] and _script_connects(words[0], cwd):
            return words[0]
    return ""


def status():
    if not enabled():
        return {"enabled": False}
    health, detail, age = read_health()
    sessions = open_sessions()
    return {"enabled": True, "app": app(), "health": health, "detail": detail,
            "age": age, "limit": limit(), "open": len(sessions),
            "sessions": sessions, "lapsed": lapsed_wrappers()}


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "status":
        print(json.dumps(status()))
