#!/usr/bin/env bash
# Virtual Captain: daily backup of the SQLite database (online, consistent copy).
# Install (by hand, once): see DEPLOY.md, section "Messa in sicurezza operativa (C4)", point 2.
# Exit codes: 0 ok; 1 configuration / missing database; 2 backup failed; 3 integrity check failed.
# Never prints secrets: only file names and counts.
set -euo pipefail
umask 077

BASE="/var/www/virtual-captain"
DB="${DB_PATH:-$BASE/shared/virtual-captain.db}"
DEST="${BACKUP_DIR:-$BASE/shared/backup}"
KEEP=7

log() { echo "[backup] $*"; }

command -v sqlite3 >/dev/null 2>&1 || { log "ERROR: sqlite3 not installed (sudo apt install sqlite3)" >&2; exit 1; }
[ -f "$DB" ] || { log "ERROR: database not found: $DB" >&2; exit 1; }
case "$DB$DEST" in *"'"*) log "ERROR: quote in path not supported" >&2; exit 1;; esac

# Note: if BACKUP_DIR is custom, mkdir creates it with umask 077 (700); the user running the
# service must be able to write there (and the unit needs it in ReadWritePaths).
mkdir -p "$DEST"
chmod 700 "$DEST"

# Leftovers of an interrupted run (older than 1 day).
find "$DEST" -maxdepth 1 -name '.partial-*' -mtime +1 -delete

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
TMP="$DEST/.partial-$STAMP.db"
OUT="$DEST/virtual-captain-$STAMP.db"
trap 'rm -f "$TMP"' EXIT

if ! sqlite3 "$DB" ".timeout 10000" ".backup '$TMP'"; then
  log "ERROR: sqlite3 .backup failed" >&2
  exit 2
fi
if [ "$(sqlite3 "$TMP" 'PRAGMA integrity_check;')" != "ok" ]; then
  log "ERROR: integrity_check on the copy failed, copy discarded" >&2
  exit 3
fi
chmod 600 "$TMP"
mv "$TMP" "$OUT"
log "created $(basename "$OUT")"

# Rotation: keep the newest $KEEP copies (names sort by date).
COUNT=0
while IFS= read -r -d '' f; do
  COUNT=$((COUNT + 1))
  if [ "$COUNT" -gt "$KEEP" ]; then
    rm -f -- "$f"
    log "removed old $(basename "$f")"
  fi
done < <(find "$DEST" -maxdepth 1 -name 'virtual-captain-*.db' -print0 | sort -rz)
log "done ($(ls -1 "$DEST"/virtual-captain-*.db | wc -l | tr -d ' ') copies kept)"
