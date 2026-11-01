#!/usr/bin/env bash
# One-line health check for humans and timers.
#
# Prints GREEN or RED plus what to do, exit 0 on GREEN. Nothing here fixes
# anything; it only tells the truth fast.
#
#   scripts/doctor.sh            # status
#   scripts/doctor.sh --tests    # also run the test suite
set -uo pipefail
cd "$(dirname "$0")/.." || exit 2

run_tests=0
[[ "${1:-}" == "--tests" ]] && run_tests=1

red=0
notes=()

head_commit=$(git rev-parse --short HEAD 2>/dev/null || echo "no-git")

# --- working tree: an uncommitted mess means something stopped mid-edit ---
dirty=$(git status --porcelain 2>/dev/null | wc -l | tr -d ' ')
if [[ "$dirty" != "0" ]]; then
  notes+=("working tree has ${dirty} uncommitted paths — something stopped mid-edit")
fi

# --- schema drift: a dev DB left behind head does not fail loudly, it 500s on
# "no such table" the first time someone logs in. ---
db_file="${COLOPHON_DB_PATH:-data/colophon.sqlite3}"
if [[ -f "$db_file" && -x .venv/bin/alembic ]]; then
  db_rev=$(.venv/bin/alembic current 2>/dev/null | grep -oE '^[0-9a-z_]+' | head -1)
  head_rev=$(.venv/bin/alembic heads 2>/dev/null | grep -oE '^[0-9a-z_]+' | head -1)
  if [[ -n "$head_rev" && "$db_rev" != "$head_rev" ]]; then
    red=1
    notes+=("schema drift: ${db_file} is at '${db_rev:-nothing}', head is ${head_rev} — run 'make migrate'")
  fi
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
  echo "GREEN  HEAD ${head_commit}"
  for n in "${notes[@]:-}"; do [[ -n "$n" ]] && echo "  note: $n"; done
  exit 0
fi

echo "RED    HEAD ${head_commit}"
for n in "${notes[@]:-}"; do [[ -n "$n" ]] && echo "  - $n"; done
exit 1
