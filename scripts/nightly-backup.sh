#!/usr/bin/env bash
# What the nightly cron entry or systemd timer runs (SPEC §14: nightly, 30-day
# retention, photos included; T10: through Python, never the sqlite3 CLI).
#
#   0 3 * * * /srv/colophon/scripts/nightly-backup.sh
#
# Two commands, both idempotent: `create` writes a fresh dated directory and
# `prune` removes the ones past retention, so running this twice in a row is safe —
# the second run adds one backup and prunes nothing new.
#
# The exit code is the point: a timer or cron mail alerts on non-zero, so nothing
# here swallows a failure, and a half-taken backup counts as a failed night.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 2

# The project's own venv when it is there (T4); inside the Docker image dependencies
# are already installed, so the interpreter on PATH is the right one.
PYTHON="python3"
[[ -x .venv/bin/python ]] && PYTHON=".venv/bin/python"

# Same default as app/settings.py; COLOPHON_* remains the only way to move anything.
BACKUP_DIR="${COLOPHON_BACKUP_DIR:-data/backups}"
KEEP_DAYS="${COLOPHON_BACKUP_KEEP_DAYS:-30}"

if ! mkdir -p "$BACKUP_DIR"; then
  echo "cannot create backup directory ${BACKUP_DIR}" >&2
  exit 1
fi
LOG="${BACKUP_DIR}/backup.log"

status=0
{
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] nightly-backup start"
  "$PYTHON" -m app.tools.backup create || status=$?
  # Prune only after a backup actually landed: a night that wrote nothing must not
  # also throw away last week's, or one broken night becomes an empty backup dir.
  if [[ "$status" -eq 0 ]]; then
    "$PYTHON" -m app.tools.backup prune --keep-days "$KEEP_DAYS" || status=$?
  else
    echo "skipping prune: the backup failed, keeping everything for now"
  fi
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] nightly-backup done status=${status}"
} >>"$LOG" 2>&1

# Show the tail on stdout too: that is what a cron mail or `journalctl -u` shows.
tail -n 8 "$LOG"
exit "$status"
