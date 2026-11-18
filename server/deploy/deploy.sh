#!/usr/bin/env bash
# Virtual Captain: local deploy on the VPS, run by the GitHub Actions self-hosted runner.
# Installed (by hand, once) as /var/www/virtual-captain/scripts/deploy.sh, root:root 755.
# Idempotent: can be run again at any time. Needs GITHUB_SHA and GITHUB_WORKSPACE.
set -euo pipefail

BASE="/var/www/virtual-captain"
SERVICE="virtual-captain"
PORT="8100"

: "${GITHUB_WORKSPACE:?GITHUB_WORKSPACE is not set}"
: "${GITHUB_SHA:?GITHUB_SHA is not set}"
: "${BASE:?BASE is empty}"

if ! [[ "$GITHUB_SHA" =~ ^[0-9a-f]{7,64}$ ]]; then
  echo "ERROR: GITHUB_SHA is not a valid commit hash" >&2
  exit 1
fi

CURRENT="$BASE/current"
PREVIOUS="$BASE/previous"
SHARED="$BASE/shared"
VENV="$BASE/venv"

SWITCHED=0        # 1 once current/ has been modified
HAD_PREVIOUS=0    # 1 if there was a working copy to go back to

log() { echo "[deploy] $*"; }

smoke_test() {
  # $1 = expected rev
  local expected="${1:?expected rev missing}" i body
  for i in $(seq 1 20); do
    if body="$(curl -fsS --max-time 3 "http://127.0.0.1:${PORT}/healthz" 2>/dev/null)"; then
      if printf '%s' "$body" | grep -Eq "\"rev\"[[:space:]]*:[[:space:]]*\"${expected}\""; then
        log "smoke test ok (rev ${expected})"
        return 0
      fi
    fi
    sleep 1
  done
  return 1
}

rollback() {
  if [[ "$HAD_PREVIOUS" -ne 1 ]]; then
    echo "[deploy] ERROR: failed and there is no previous version to restore" >&2
    # a broken first deploy must not look like a good version to the next run
    rm -f "${CURRENT:?}/REVISION"
    return 0
  fi
  echo "[deploy] ERROR: restoring the previous code from $PREVIOUS" >&2
  rsync -a --delete "${PREVIOUS:?}/" "${CURRENT:?}/" || true
  sudo -n systemctl restart "$SERVICE" || true
  local old
  old="$(cat "$CURRENT/REVISION" 2>/dev/null || echo unknown)"
  if smoke_test "$old"; then
    echo "[deploy] rollback done, running rev ${old}" >&2
  else
    echo "[deploy] ROLLBACK FAILED: check 'journalctl -u ${SERVICE}'" >&2
  fi
}

on_exit() {
  local rc=$?
  if [[ $rc -ne 0 && "$SWITCHED" -eq 1 ]]; then
    rollback
  fi
  exit "$rc"
}
trap on_exit EXIT

SRC="${GITHUB_WORKSPACE:?}"
[[ -d "$SRC/server" ]] || { echo "ERROR: $SRC/server not found" >&2; exit 1; }
[[ -d "$SRC/web" ]] || { echo "ERROR: $SRC/web not found" >&2; exit 1; }
command -v rsync >/dev/null || { echo "ERROR: rsync not installed" >&2; exit 1; }
command -v curl >/dev/null || { echo "ERROR: curl not installed" >&2; exit 1; }

log "rev ${GITHUB_SHA}"
mkdir -p "$CURRENT" "$PREVIOUS" "$SHARED"

# 1. keep the running version for rollback
if [[ -f "$CURRENT/REVISION" ]]; then
  rsync -a --delete "${CURRENT:?}/" "${PREVIOUS:?}/"
  HAD_PREVIOUS=1
fi

# 2. virtualenv outside current/ (created on the first run)
if [[ ! -x "$VENV/bin/python" ]]; then
  log "creating virtualenv"
  python3 -m venv "$VENV"
fi

# 3. copy the code (tests, docs and deploy files stay out of the server)
SWITCHED=1
rsync -a --delete \
  --exclude '__pycache__/' --exclude 'deploy/' --exclude 'test_*.py' \
  --exclude 'requirements-dev.txt' --exclude '*.md' --exclude 'env.example' \
  "$SRC/server/" "${CURRENT:?}/server/"
rsync -a --delete --exclude '__pycache__/' "$SRC/web/" "${CURRENT:?}/web/"
echo "$GITHUB_SHA" > "$CURRENT/REVISION"

# 4. dependencies and import check
"$VENV/bin/pip" install --disable-pip-version-check -q -r "$CURRENT/server/requirements.txt"
(cd "$CURRENT" && "$VENV/bin/python" -c "import server.app")

# 5. restart and verify
sudo -n systemctl restart "$SERVICE"
smoke_test "$GITHUB_SHA"

SWITCHED=0
log "deploy completed, rev ${GITHUB_SHA}"
