#!/usr/bin/env bash
# pressure.sh — Claude Code SessionStart hook: answers "what shape is
# this machine in?"
#
# A session cannot see the cost of its own parallelism. It dispatches
# eight agents, spawns test runs and dev servers, and observes none of
# the contention that follows — so the tenth parallel task looks exactly
# as free as the first. On 2026-07-26 one laptop carried load 19.6 across
# 8 cores with six Claude sessions, a Codex process, five BEAM VMs and
# two node servers live; nothing running on it could tell.
#
# Reports load, swap, disk and locally-listening dev servers. All of it
# comes from local macOS/Linux system interfaces — instant, local, no network.
#
# Silence is the default. Every threshold below is set where the number
# starts changing a decision, not where it becomes non-zero: load 4 on 8
# cores is a working machine and says nothing, load 19 is a warning. A
# hook that reports healthy state every session trains you to skim it,
# and then it cannot warn you. See README "The output budget".
set -uo pipefail

# Load per core. Above this the machine is oversubscribed enough that
# more parallelism buys nothing and everything already running slows.
LOAD_PER_CORE_WARN=${SCRY_LOAD_PER_CORE_WARN:-1.5}
# Swap in use. Any sustained swapping on a dev box means RAM is gone and
# builds are about to get mysteriously slow.
SWAP_USED_MB_WARN=${SCRY_SWAP_USED_MB_WARN:-2048}
# Disk. Below either bound, builds and containers start failing in ways
# that look like code bugs.
DISK_FREE_GB_WARN=${SCRY_DISK_FREE_GB_WARN:-20}
DISK_USED_PCT_WARN=${SCRY_DISK_USED_PCT_WARN:-90}

emit() {
  [ -n "$1" ] || return 0
  CTX="$1" python3 - <<'PY'
import json, os
print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": "SessionStart",
        "additionalContext": os.environ["CTX"],
    }
}))
PY
}

lines=()

# ── Load ────────────────────────────────────────────────────────────────
cores="$(sysctl -n hw.ncpu 2>/dev/null || nproc 2>/dev/null || getconf _NPROCESSORS_ONLN 2>/dev/null || echo 0)"
load1="$(sysctl -n vm.loadavg 2>/dev/null | awk '{print $2}')"
[ -n "$load1" ] || load1="$(awk '{print $1}' /proc/loadavg 2>/dev/null)"
[ -n "$load1" ] || load1="$(uptime | sed 's/.*load averages*:[ ]*//' | awk '{print $1}' | tr -d ,)"
if [ -n "$load1" ] && [ "$cores" -gt 0 ] 2>/dev/null; then
  verdict="$(LOAD="$load1" CORES="$cores" WARN="$LOAD_PER_CORE_WARN" python3 - <<'PY'
import os
try:
    load, cores, warn = float(os.environ["LOAD"]), int(os.environ["CORES"]), float(os.environ["WARN"])
except ValueError:
    raise SystemExit
ratio = load / cores
if ratio >= warn:
    print(f"- MACHINE OVERSUBSCRIBED: load {load:.1f} on {cores} cores "
          f"({ratio:.1f}x). More parallel agents or test runs will slow "
          f"everything already running rather than finish sooner.")
PY
)"
  [ -n "$verdict" ] && lines+=("$verdict")
fi

# ── Swap ────────────────────────────────────────────────────────────────
swap_used="$(sysctl -n vm.swapusage 2>/dev/null | sed -n 's/.*used = \([0-9.]*\)M.*/\1/p')"
[ -n "$swap_used" ] || swap_used="$(awk '
  /^SwapTotal:/ { total=$2 }
  /^SwapFree:/ { free=$2 }
  END { if (total != "" && free != "") printf "%.0f", (total-free)/1024 }
' /proc/meminfo 2>/dev/null)"
if [ -n "$swap_used" ]; then
  used_int="${swap_used%%.*}"
  if [ "${used_int:-0}" -ge "$SWAP_USED_MB_WARN" ] 2>/dev/null; then
    lines+=("- Swapping: ${used_int}MB of swap in use. RAM is exhausted; expect builds and test runs to be much slower than they look.")
  fi
fi

# ── Disk ────────────────────────────────────────────────────────────────
read -r free_gb used_pct <<<"$(df -Pk / 2>/dev/null | awk 'NR==2 {gsub(/%/,"",$5); printf "%d %s\n", $4/1048576, $5}')"
if [ -n "${free_gb:-}" ]; then
  if [ "$free_gb" -lt "$DISK_FREE_GB_WARN" ] 2>/dev/null || [ "${used_pct:-0}" -ge "$DISK_USED_PCT_WARN" ] 2>/dev/null; then
    lines+=("- Disk low: ${free_gb}GB free (${used_pct}% used). Builds, containers and git operations fail in confusing ways below this.")
  fi
fi

# ── Local dev servers ───────────────────────────────────────────────────
# "Is the app already running?" — cheap to answer and it stops a session
# starting a second copy on a taken port. Filtered twice: by port range
# (dev servers live high, daemons live low) and by name, because macOS
# parks Control Center on 5000 and 7000 for AirPlay and it is not yours.
SYSTEM_LISTENERS='ControlCe|rapportd|sharingd|Dropbox|AirPlay|identityservicesd'
servers="$(lsof -nP -iTCP -sTCP:LISTEN 2>/dev/null \
  | awk 'NR>1 {n=split($9,a,":"); p=a[n]+0; if (p>=3000 && p<=9999) print $1" :"p}' \
  | grep -Ev "^($SYSTEM_LISTENERS) " \
  | sort -u | head -8 | tr '\n' ' ')"
if [ -n "${servers// /}" ]; then
  lines+=("- Local servers already listening: ${servers%% }. Check before starting another — the port may be taken by a session you cannot see.")
fi

if [ ${#lines[@]} -gt 0 ]; then
  body="$(printf '%s\n' "${lines[@]}")"
  emit "Machine pressure (SessionStart):
$body"
fi
exit 0
