#!/usr/bin/env bash
# agent_model_guard.sh — PreToolUse gate on Agent and Workflow dispatch.
#
# Answers one question: is this dispatch about to spend the MAIN SESSION'S
# model on work that never needed it?
#
# The failure this exists for is inheritance x fan-out. A subagent dispatched
# with no `model` inherits whatever the parent session runs on. That is
# invisible at the call site, costs nothing extra to write, and multiplies:
# four verifier agents and a builder, each dragging a large working context
# through hundreds of tool calls, all silently on the parent's model. On
# 2026-08-31 that pattern consumed most of a week's Fable quota in a single
# session -- roughly 600-700k tokens of premium model on verification and
# grunt work that was specified to run on cheaper models. A second session the
# day before had already done the same thing with a sync agent (~236k) and
# nobody noticed, because nothing anywhere names the model a subagent is
# actually going to use.
#
# What this does NOT do is ban the expensive model. Asking the strongest model
# one bounded question is a legitimate, cheap, deliberate act, and an explicit
# `model: "fable"` on a single Agent call is allowed through in silence. The
# distinction that matters is not which model -- it is whether a human or an
# agent CHOSE it at the call site, and whether it is being fanned out.
#
# Rules, in order of severity:
#   1. Agent with no `model`            -> DENY. Inheritance is never intended.
#   2. Workflow whose script names the premium model -> DENY. Fan-out x premium
#      is the exact shape of the incident.
#   3. Workflow with agent() calls that do not all name a model -> DENY.
#   4. `subagent_type: "fork"`          -> two-strike speed bump. A fork
#      inherits by design and cannot be overridden, so it is legitimate but
#      must be a conscious choice; run it again and it proceeds.
#   5. Anything else, including an explicit premium model on ONE agent -> allow.
#
# Fails OPEN on every internal error: an unparseable payload, an unwritable
# state directory, a missing python -- all allow the dispatch. A cost guard
# that wedges a session costs more than the tokens it saves.
set -uo pipefail

read -r payload

PAYLOAD="$payload" python3 - <<'PY'
import hashlib, json, os, re, sys, time

def allow():
    sys.exit(0)

def deny(reason):
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
    }}))
    sys.exit(0)

try:
    d = json.loads(os.environ["PAYLOAD"])
except Exception:
    allow()

tool = d.get("tool_name") or ""
if tool not in ("Agent", "Workflow"):
    allow()
# A payload with no inspectable tool_input is a shape this guard does not
# understand. Denying what it cannot read would wedge a session over a parse
# failure -- the one cost that exceeds the tokens this exists to save.
inp = d.get("tool_input")
if not isinstance(inp, dict):
    allow()

# Models that must never be reached by inheritance. Configurable so this
# survives a rename without a code change.
PREMIUM = {
    m.strip().lower()
    for m in (os.environ.get("SCRY_PREMIUM_MODELS") or "fable").split(",")
    if m.strip()
}

def two_strike(key, message, window=300):
    """Block once, then let an immediate retry through. Fails open."""
    state_dir = os.path.join(os.environ.get("TMPDIR", "/tmp"), "scry-agent-model-guard")
    try:
        os.makedirs(state_dir, exist_ok=True)
        marker = os.path.join(state_dir, hashlib.sha256(key.encode()).hexdigest()[:32])
        now = time.time()
        if os.path.exists(marker) and (now - os.path.getmtime(marker)) < window:
            os.remove(marker)
            allow()
        with open(marker, "w") as f:
            f.write(str(now))
    except OSError:
        allow()
    deny(message)

# ── Agent ──────────────────────────────────────────────────────────────────
if tool == "Agent":
    subagent = (inp.get("subagent_type") or "").strip().lower()
    model = (inp.get("model") or "").strip().lower()

    # A fork inherits the parent model by definition; `model` is ignored on it.
    if subagent == "fork":
        two_strike(
            "fork:" + (inp.get("description") or "")[:120],
            "COST GUARD (first strike) — a fork inherits THIS session's model and "
            "the override is ignored, so if this session is on a premium model the "
            "fork is too, for its whole run.\n\n"
            "If you need the parent's context, dispatch it again and it proceeds. "
            "If you only need the task done, use a regular agent with an explicit "
            "cheaper model instead — that is almost always what was meant.",
        )

    if not model:
        deny(
            "COST GUARD — this Agent names no model, so it INHERITS the main "
            "session's model for its entire run. That is how a week of premium "
            "quota was consumed in one session on 2026-08-31: four verifiers and a "
            "builder, none of them naming a model, none of them needing the model "
            "they got.\n\n"
            "Set `model` explicitly on this dispatch:\n"
            "  • opus   — builders, inspectors, anything writing or judging code\n"
            "  • sonnet — search, summarisation, mechanical edits\n"
            "  • haiku  — trivial lookups\n\n"
            "Naming the model is never wrong. Omitting it is never intended."
        )

    # An explicit premium model on a single agent is the legitimate
    # "ask the strongest model one bounded question" path. Allowed, silently.
    allow()

# ── Workflow ───────────────────────────────────────────────────────────────
script = inp.get("script") or ""
if not script:
    sp = inp.get("scriptPath")
    if sp and os.path.isfile(sp):
        try:
            with open(sp, encoding="utf-8") as f:
                script = f.read()
        except OSError:
            allow()

# A named/saved workflow with no inline script cannot be inspected here.
if not script.strip():
    allow()

# Strip comments so prose about models is not mistaken for a call site.
code = re.sub(r"//.*?$|/\*.*?\*/", "", script, flags=re.S | re.M)

named_premium = sorted({
    m.group(1).lower()
    for m in re.finditer(r"""model\s*:\s*['"]([A-Za-z0-9._\-\[\]]+)['"]""", code)
    if m.group(1).lower() in PREMIUM
})
if named_premium:
    deny(
        "COST GUARD — this workflow names the premium model "
        f"({', '.join(named_premium)}) inside a FAN-OUT. A workflow multiplies every "
        "agent it launches, and premium x fan-out is precisely the shape that "
        "consumed most of a week's quota on 2026-08-31.\n\n"
        "Use opus / sonnet / haiku for every agent() call. If one genuinely hard "
        "question needs the premium model, ask it as a single Agent dispatch "
        "outside the workflow, where it is one bounded call and visible as a choice."
    )

agent_calls = len(re.findall(r"\bagent\s*\(", code))
modelled = len(re.findall(r"""model\s*:\s*['"][A-Za-z0-9._\-\[\]]+['"]""", code))
if agent_calls > modelled:
    deny(
        f"COST GUARD — this workflow has {agent_calls} agent() call(s) but only "
        f"{modelled} name a model. Every agent() that does not name one inherits the "
        "main session's model, multiplied across the whole fan-out.\n\n"
        "Give every agent() an explicit `model:` — opus for builders and "
        "inspectors, sonnet or haiku for mechanical work. If a helper builds the "
        "options object, inline the model there so it is visible at the call site."
    )

allow()
PY

exit 0
