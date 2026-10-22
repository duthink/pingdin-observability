#!/usr/bin/env bash
# scripts/deploy.sh — BUILD_LOG Journey 19. Run ON THE SERVER, from the repo, after scripts/sync_to_server.sh.
#
# WHY IT EXISTS: a sync replaces files, and a delete-and-copy (`sudo rm -rf` of the repo's directories before
# copying, the habit behind the 3 Oct 2026 incident) replaces whole directories, leaving a running container
# looking at the deleted one. Services that bind-mount repo files keep reading the OLD copy
# until they are re-created: Prometheus kept stale rules, and Grafana logged "no such file or directory"
# every 30 s while applying none of the Journey 14/15/17 dashboards. So after every sync the SAME thing
# must happen, and it used to be four commands someone had to remember (and once ran on the wrong machine).
#
#   1. validate the compose file (nothing is touched if it is invalid)
#   2. docker compose up -d --build            new/changed services, rebuilt images
#   3. re-create EVERY service that bind-mounts files from this repo OR is built locally (a rebuilt image gets a new ID
#      even when no layer changed, which left the alert bridge "STALE" after every deploy). The list is read from the compose file
#      itself (`docker compose config --format json`), never hard-coded, so adding a service that mounts
#      config cannot be forgotten. Their data volumes are kept; each is back in seconds.
#   4. wait for health checks to settle, then report anything unhealthy or stale
#
# Exit: 0 clean · 1 a step failed or something is unhealthy.
set -uo pipefail

cd "$(dirname "$0")/.."
REPO="$(pwd)"

echo "== 1/4 validate compose =="
docker compose config -q || { echo "DEPLOY STOPPED: docker-compose.yml is invalid — nothing was changed"; exit 1; }

echo "== 2/4 build and start new or changed services =="
docker compose up -d --build || { echo "DEPLOY STOPPED: docker compose up failed"; exit 1; }

echo "== 3/4 re-create services that bind-mount files from this repo =="
services="$(docker compose config --format json | REPO="$REPO" python3 -c '
import json, os, sys
repo = os.environ["REPO"] + "/"
cfg = json.load(sys.stdin)
for name, svc in sorted((cfg.get("services") or {}).items()):
    if svc.get("build"):          # locally built image: BuildKit provenance changes its ID on EVERY build, so the running
        print(name)               # container always looks stale afterwards (Journey 20) — re-create it with the new build
        continue
    for v in svc.get("volumes") or []:
        if v.get("type") == "bind" and str(v.get("source", "")).startswith(repo):
            print(name)
            break
')" || { echo "DEPLOY STOPPED: could not read the compose configuration"; exit 1; }
if [ -z "$services" ]; then
  echo "DEPLOY STOPPED: found no service mounting files from $REPO — refusing to guess (is this the right directory?)"
  exit 1
fi
echo "  $(echo $services)"
# shellcheck disable=SC2086
docker compose up -d --force-recreate $services || { echo "DEPLOY STOPPED: re-creating the mounting services failed"; exit 1; }

echo "== 4/4 wait for health, then check =="
for _ in $(seq 1 45); do
  [ -z "$(docker ps --filter health=starting --format '{{.Names}}' 2>/dev/null | grep -v '^[[:space:]]*$')" ] && break
  sleep 2
done
problems=0
unhealthy="$(docker ps --filter health=unhealthy --format '{{.Names}}' 2>/dev/null | grep -v '^[[:space:]]*$' | tr '\n' ' ')"
[ -n "$unhealthy" ] && { echo "UNHEALTHY: ${unhealthy}"; problems=1; }
bash scripts/find_stale_containers.sh >/dev/null 2>&1 || { echo "STALE container(s) — run: bash scripts/find_stale_containers.sh"; problems=1; }
if [ "$problems" -eq 0 ]; then
  echo "DEPLOY OK — mounting services re-created; checks: bash scripts/ops_daily_check.sh"
  exit 0
fi
echo "DEPLOY FINISHED WITH PROBLEMS (above)"
exit 1
