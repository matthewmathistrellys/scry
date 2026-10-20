#!/usr/bin/env python3
"""
Remote calls — the one place that answers "does this command call something
shared, and does it call it more than once at a time?"

Read by load_fanout_advisory.sh (before a command) and
service_distress_advisory.sh (after one). Both need the same reading of a
command line, so both ask this module.

A command is read as words. Quoted text, comments and heredoc bodies are not
commands, so a commit message that names `curl` never counts. The reading is
advisory: where it cannot tell, it says nothing.

Not a hook. Imported by the two scripts above.
"""

import os
import re
import shlex

# Tools whose job is to talk to a service somebody else shares: a server, a
# database, an API, a hosting platform, a remote repository.
REMOTE_TOOLS = {
    "ssh", "scp", "sftp", "rsync", "curl", "wget", "http", "https", "xh", "nc", "ncat",
    "psql", "mysql", "mariadb", "redis-cli", "mongosh", "mongo", "sqlcmd", "cqlsh",
    "kubectl", "helm", "aws", "gcloud", "az", "fly", "flyctl", "heroku", "vercel",
    "railway", "netlify", "supabase", "doctl", "terraform", "gsutil", "s3cmd",
}
# `gh api`, and git commands that go to a remote.
GIT_REMOTE = {"fetch", "pull", "push", "clone", "ls-remote"}

_OPERATORS = re.compile(r"&>|>&|<&|&&|\|\||\|&|<<<|<<|>>|;|\(|\)|\||&|<|>")
_SEPARATORS = {";", "&&", "||", "|", "|&", "(", ")", "&"}
_WRAPPERS = {"env", "command", "exec", "time", "nohup", "builtin", "sudo", "nice", "timeout",
             "do", "then", "if", "elif", "else", "while", "until", "!", "{", "}"}
_LOOPS = {"for", "while", "until"}
MAX_PARSED = 100_000


def _prepare(command):
    """Drop comments and heredoc bodies; unquoted newlines become `;`."""
    command = re.sub(r"<<-?\s*(['\"]?)\\?([^\s'\"<>;&|()]+)\1[^\n]*\n.*?\n[ \t]*\2[ \t]*(?=\n|$)", " ", command,
                     flags=re.S) if command.count("<<") <= 20 else command
    command = command.replace("\\\n", " ")
    out, quote, i = [], "", 0
    while i < len(command):
        c = command[i]
        if c == "\\" and quote != "'" and i + 1 < len(command):
            out.append(command[i:i + 2])
            i += 2
            continue
        if quote:
            quote = "" if c == quote else quote
        elif c in "'\"":
            quote = c
        elif c == "#" and (i == 0 or command[i - 1] in " \t\n;&|()"):
            end = command.find("\n", i)
            i = len(command) if end < 0 else end
            continue
        elif c == "\n":
            c = " ; "
        out.append(c)
        i += 1
    return "".join(out)


def segments(command):
    """[(words, ended_by)] where ended_by is the separator that closed the
    segment ("" for the last one). Empty when the command cannot be read."""
    if len(command) > MAX_PARSED:
        return []
    lex = shlex.shlex(_prepare(command), posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    lex.commenters = ""
    try:
        tokens = list(lex)
    except ValueError:
        return []
    out, cur, skip = [], [], False
    flat = []
    for tok in tokens:      # shlex joins neighbouring operators, as in `);`
        flat += _OPERATORS.findall(tok) if re.fullmatch(r"[();<>|&]+", tok) else [tok]
    for tok in flat:
        if skip:
            skip = False
        elif tok in _SEPARATORS:
            if cur:
                out.append((cur, tok))
            cur = []
        elif re.fullmatch(r"&>|>&|<&|<<<|<<|>>|<|>", tok):
            skip = True
        else:
            cur.append(tok)
    if cur:
        out.append((cur, ""))
    return out


def _command_words(words):
    """The words from the command itself: leading assignments, `sudo`, `do`
    and the like dropped."""
    while words and (re.fullmatch(r"\w+=.*", words[0]) or os.path.basename(words[0]) in _WRAPPERS):
        wrapper = os.path.basename(words[0])
        words = words[1:]
        while wrapper in ("sudo", "nice", "timeout", "env") and words and (
                words[0].startswith("-") or re.fullmatch(r"[\d.]+[smhd]?", words[0])):
            words = words[1:]
    return words


def remote_tool(words):
    """The remote tool this segment runs, or ''."""
    words = _command_words(words)
    if not words:
        return ""
    first = os.path.basename(words[0])
    rest = words[1:]
    if first in REMOTE_TOOLS:
        return first
    if first == "gh" and rest[:1] == ["api"]:
        return "gh api"
    if first == "git" and any(w in GIT_REMOTE for w in rest[:3]):
        return "git " + next(w for w in rest[:3] if w in GIT_REMOTE)
    if first in ("xargs", "parallel"):
        for w in rest:
            found = remote_tool([w] + rest[rest.index(w) + 1:])
            if found:
                return found
    return ""


def target(text):
    """A host or URL the text names, or ''."""
    m = re.search(r"https?://([^/\s'\"]+)", text) or re.search(r"[\w.-]+@([\w.-]+\.[\w.-]+)", text)
    return m.group(1) if m else ""


def _parallel_xargs(words):
    for i, w in enumerate(words):
        m = re.fullmatch(r"-P(\d*)|--max-procs(?:=(\d+))?", w)
        if not m:
            continue
        n = m.group(1) or m.group(2)
        if not n and i + 1 < len(words):
            n = words[i + 1]
        if n is not None and n.isdigit() and (int(n) == 0 or int(n) > 1):
            return n
    return None


def fanout(command):
    """What this command repeats against a remote tool: a list of
    (shape, tool, detail) with shape loop, parallel or background."""
    found, depth = [], 0
    for words, ended in segments(command):
        cmd = _command_words(words)
        first = os.path.basename(cmd[0]) if cmd else ""
        if words and os.path.basename(words[0]) in _LOOPS:
            depth += 1
        tool = remote_tool(words)
        text = " ".join(words)
        if tool and depth > 0 and first not in ("done",):
            found.append(("loop", tool, target(text)))
        if tool and ended == "&":
            found.append(("background", tool, target(text)))
        if first == "xargs":
            n = _parallel_xargs(cmd[1:])
            if n is not None and tool:
                found.append(("parallel", tool, f"{n} at a time" if n != "0" else "as many as it can"))
        elif first == "parallel" and tool:
            found.append(("parallel", tool, ""))
        if first == "done":
            depth = max(0, depth - 1)
    return found
