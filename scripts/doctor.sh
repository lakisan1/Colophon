#!/usr/bin/env bash
# One-line health check for humans, timers and freshly started agents.
#
# Prints GREEN or RED plus what to do, exit 0 on GREEN. It answers the question
# that cost 8 silent hours this project: "is the run still alive, and where is
# it?" Nothing here fixes anything; it only tells the truth fast.
#
#   scripts/doctor.sh            # status
#   scripts/doctor.sh --tests    # also run the suite (~7s)
set -uo pipefail
cd "$(dirname "$0")/.." || exit 2

STALE_MINUTES="${COLOPHON_STALE_MINUTES:-90}"
run_tests=0
[[ "${1:-}" == "--tests" ]] && run_tests=1

red=0
notes=()

head_commit=$(git rev-parse --short HEAD 2>/dev/null || echo "no-git")
milestone=$(sed -n 's/.*"milestone": "\(.*\)".*/\1/p' STATE.json 2>/dev/null || echo "?")
status=$(sed -n 's/.*"milestone_status": "\(.*\)".*/\1/p' STATE.json 2>/dev/null || echo "?")

# --- liveness: a running agent refreshes .run/heartbeat via `make beat` ---
heartbeat=".run/heartbeat"
if [[ -f "$heartbeat" ]]; then
  age_min=$(( ( $(date +%s) - $(stat -c %Y "$heartbeat") ) / 60 ))
  if (( age_min > STALE_MINUTES )); then
    red=1
    notes+=("heartbeat STALE: ${age_min}min old (limit ${STALE_MINUTES}m) — the run is not alive; poke it or resume the ${milestone} milestone")
  fi
else
  red=1
  notes+=("no heartbeat file (.run/heartbeat) — nothing has run 'make beat'; start the milestone or mark it done")
fi

# --- intent vs reality: STATE.json must agree with docs/PLAN.md ---
if [[ -f docs/PLAN.md ]]; then
  plan_current=$(grep -oE '^- \[ \] \*\*M[0-9]+' docs/PLAN.md | head -1 | grep -oE 'M[0-9]+')
  if [[ -n "$plan_current" && "$plan_current" != "$milestone" ]]; then
    red=1
    notes+=("STATE.json says ${milestone}, docs/PLAN.md says ${plan_current} — the handoff drifted; re-sync STATE.json")
  fi
fi

# --- working tree: an uncommitted mess means something stopped mid-edit ---
dirty=$(git status --porcelain 2>/dev/null | wc -l | tr -d ' ')
if [[ "$dirty" != "0" ]]; then
  notes+=("working tree has ${dirty} uncommitted paths — something stopped mid-edit")
fi

# --- gate ---
if [[ $run_tests -eq 1 ]]; then
  if [[ -x .venv/bin/pytest ]]; then
    out=$(.venv/bin/pytest -q 2>&1 | tail -1)
    if [[ "$out" == *"failed"* || "$out" == *"error"* ]]; then
      red=1
      notes+=("tests: ${out}")
    fi
  else
    red=1
    notes+=("no .venv/bin/pytest — run 'make venv'")
  fi
fi

if [[ $red -eq 0 ]]; then
  echo "GREEN  HEAD ${head_commit}  ${milestone} ${status}  heartbeat ${age_min:-?}min old"
  for n in "${notes[@]:-}"; do [[ -n "$n" ]] && echo "  note: $n"; done
  exit 0
fi

echo "RED    HEAD ${head_commit}  ${milestone} ${status}"
for n in "${notes[@]:-}"; do [[ -n "$n" ]] && echo "  - $n"; done
exit 1
