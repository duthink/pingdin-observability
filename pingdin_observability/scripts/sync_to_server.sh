#!/usr/bin/env bash
# scripts/sync_to_server.sh — BUILD_LOG Journey 19 (sync) / 20 (mirror + --deploy). Run on your LAPTOP, from anywhere.
#
#   bash pingdin_observability/scripts/sync_to_server.sh --deploy        # the whole job: sync, mirror, deploy
#   bash pingdin_observability/scripts/sync_to_server.sh [--dry-run] [--no-prune] [--prune-max N] [--deploy] [--target user@host:/parent]
#
# What it does, in order:
#   1. pushes the repo as ONE compressed stream (`tar | ssh tar`) — extracting OVER the existing files, never sending the
#      server-owned .env, caches, .git or the daily check's log;
#   2. MIRRORS: runs scripts/prune_extras.py on the server to remove FILES that are no longer in the repo (an obsolete test,
#      an old dashboard) — never a directory replacement, never a server-owned file; it refuses if it would delete more than
#      --prune-max (default 40), which is what a partial local folder looks like;
#   3. with --deploy, runs scripts/deploy.sh on the server (validate, build, re-create the services that mount repo files).
#
# This REPLACES the old hand-run `sudo rm -rf <every repo directory>` followed by a copy. That habit is what made Grafana's
# bind mount stale on 3 Oct 2026 (a deleted-and-recreated directory gets a new inode; the running container keeps the deleted
# one) and — on the platform — deleted the server-generated version locks. NEVER do it; this does what it was for, safely.
set -uo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
REPO_NAME="$(basename "$REPO")"
DRY=0; PRUNE=1; PRUNE_MAX=40; DEPLOY=0; TARGET="${PINGDIN_DEPLOY_TARGET:-}"
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY=1 ;;
    --no-prune) PRUNE=0 ;;
    --prune-max) shift; PRUNE_MAX="${1:-}" ;;
    --deploy) DEPLOY=1 ;;
    --target) shift; TARGET="${1:-}" ;;
    *) echo "unknown flag: $1" >&2; exit 2 ;;
  esac
  shift
done
case "$PRUNE_MAX" in ''|*[!0-9]*) echo "ERROR: --prune-max needs a number" >&2; exit 2 ;; esac
if [ -z "$TARGET" ]; then
  TARGET="$(sed -n 's/^PINGDIN_DEPLOY_TARGET=//p' "$REPO/scripts/deploy_target.conf" 2>/dev/null | tail -1)"
fi
case "$TARGET" in *@*:*) ;; *) echo "ERROR: no valid target (need user@host:/parent) — set scripts/deploy_target.conf" >&2; exit 1 ;; esac
HOST="${TARGET%%:*}"; PARENT="${TARGET#*:}"; REMOTE="$PARENT/$REPO_NAME"

EXCLUDES=(--exclude=.env --exclude=.git --exclude=__pycache__ --exclude=.pytest_cache --exclude='*.pyc' --exclude=ops-check.log)

echo "== target: $HOST  ->  $REMOTE"
if [ "$DRY" -eq 1 ]; then
  echo "[dry-run] tar czf - ${EXCLUDES[*]} $REPO_NAME | ssh $HOST \"mkdir -p '$PARENT' && tar xzf - -C '$PARENT'\""
  [ "$PRUNE" -eq 1 ] && echo "[dry-run] ssh $HOST \"cd '$REMOTE' && python3 scripts/prune_extras.py --manifest - --max $PRUNE_MAX\"   (manifest = files in this repo)"
  [ "$DEPLOY" -eq 1 ] && echo "[dry-run] ssh $HOST \"cd '$REMOTE' && bash scripts/deploy.sh\""
  exit 0
fi

echo "== 1/3 push (one compressed stream) =="
export COPYFILE_DISABLE=1
start=$(date +%s)
if ! tar czf - -C "$(dirname "$REPO")" "${EXCLUDES[@]}" "$REPO_NAME" \
     | ssh "$HOST" "mkdir -p '$PARENT' && tar xzf - -C '$PARENT'"; then
  echo "ERROR: the push failed — nothing is known to be in a half-synced state, but re-run it" >&2
  exit 1
fi
echo "  pushed in $(( $(date +%s) - start ))s"

echo "== 2/3 mirror: remove files that are no longer in the repo =="
if [ "$PRUNE" -eq 1 ]; then
  manifest="$(mktemp)"
  ( cd "$REPO" && find . \( -name .git -o -name __pycache__ -o -name .pytest_cache \) -prune -o \
      -type f ! -name .env ! -name '*.pyc' ! -name ops-check.log -print ) | sed 's#^\./##' | sort > "$manifest"
  ssh "$HOST" "cd '$REMOTE' && python3 scripts/prune_extras.py --manifest - --max $PRUNE_MAX" < "$manifest"
  prc=$?
  rm -f "$manifest"
  if [ "$prc" -ne 0 ]; then
    echo "WARNING: the mirror step did not complete (exit $prc) — the sync itself is done; read the message above" >&2
  fi
else
  echo "  skipped (--no-prune)"
fi

if [ "$DEPLOY" -eq 1 ]; then
  echo "== 3/3 deploy on the server =="
  ssh "$HOST" "cd '$REMOTE' && bash scripts/deploy.sh" < /dev/null
  exit $?
fi
echo "== synced. Next, on the server (or re-run this with --deploy):"
echo "   ssh $HOST"
echo "   cd $REMOTE && bash scripts/deploy.sh"
