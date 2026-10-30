#!/usr/bin/env bash
# scripts/ops_daily_check.sh — BUILD_LOG Journey 16.
#
# The observability droplet's machine-checkable daily routine (ROUTINE_CHECKS.md), run
# from cron. SILENT WHEN HEALTHY; on a problem it posts to THIS droplet's own alert
# bridge (the same /owner-alert route the platform uses), which sends the Telegram
# message. On a clean pass it can ping an external dead-man's-switch check so a check
# that stops RUNNING is noticed too (OPS_CHECK_PING_URL).
#
# What it checks — each a way this stack goes wrong that nothing else alerts on:
#   1. stale containers        rebuilt/re-pulled image never recreated   (find_stale_containers.sh)
#   2. compose drift           an edit to docker-compose.yml never applied (docker compose up -d --dry-run)
#   3. unhealthy containers    a healthcheck failing while the container runs
#   4. Prometheus rule health  a rule or recording rule in an ERROR state ("unknown" = not evaluated yet, fine) (Prometheus keeps running
#                              and simply stops evaluating it — the alert silently never fires)
#   5. Grafana sees its dashboards   the container lists as many provisioned dashboard files as the repo has.
#                              A bind-mounted directory that was REPLACED on the host after the container
#                              started leaves the container looking at the old, deleted one: Grafana logs
#                              "no such file or directory" every 30 s and silently stops applying every dashboard
#                              change (BUILD_LOG Journey 18 — the Journey 14/15/17 dashboards never appeared)
#   6. every image pinned      no image on a floating tag (:latest ...): pin with scripts/pin_image_digests.py --write (Obs journey 31)
#
# Install (server local time):
#   crontab -e  →  20 6 * * * cd /opt/pingdin_observability && bash scripts/ops_daily_check.sh >> ops-check.log 2>&1
#
# Exit: 0 passed · 1 problems found and reported · 2 problems found but the report could not
# be sent (they are in ops-check.log). Secrets (the bridge token, the ping URL) are read from
# .env with grep — never `source`d, never printed.

set -uo pipefail

cd "$(dirname "$0")/.."

problems=()
add_problem() { problems+=("$1"); }

env_value() {
  local key="$1" v="${!1:-}"
  if [ -z "$v" ] && [ -f .env ]; then
    v="$(grep -E "^${key}=" .env | tail -n 1 | cut -d= -f2-)"
  fi
  printf '%s' "$v"
}

# 1. stale containers ------------------------------------------------------------------
out="$(bash scripts/find_stale_containers.sh 2>&1)"; rc=$?
if [ "$rc" -ne 0 ]; then
  stale="$(printf '%s\n' "$out" | grep -E '^STALE' | awk '{print $2}' | tr '\n' ' ')"
  if [ -n "$stale" ]; then
    add_problem "stale container(s): ${stale}— fix: docker compose up -d --build --force-recreate ${stale}"
  else
    add_problem "the stale-container check could not run: $(printf '%s' "$out" | head -n 1 | cut -c1-140)"
  fi
fi

# 2. compose drift ---------------------------------------------------------------------
drift="$(docker compose up -d --dry-run 2>&1)"; rc=$?
if [ "$rc" -ne 0 ]; then
  add_problem "the compose drift check could not run (docker compose up -d --dry-run failed)"
else
  names="$(printf '%s\n' "$drift" | grep -i 'recreat' | sed -E 's/.*Container[[:space:]]+([^[:space:]]+).*/\1/' | sort -u | tr '\n' ' ')"
  [ -n "$names" ] && add_problem "compose definition differs from what is running for: ${names}— review, then: docker compose up -d (restarts them)"
fi

# 3. unhealthy containers --------------------------------------------------------------
unhealthy="$(docker ps --filter health=unhealthy --format '{{.Names}}' 2>/dev/null | grep -v '^[[:space:]]*$' | tr '\n' ' ')"
[ -n "$unhealthy" ] && add_problem "unhealthy container(s): ${unhealthy}"

# 4. Prometheus rule health ------------------------------------------------------------
rules="$(docker exec pingdin-obs-prometheus wget -qO- http://localhost:9090/api/v1/rules 2>/dev/null)"; rc=$?
if [ "$rc" -ne 0 ] || [ -z "$rules" ]; then
  add_problem "could not read Prometheus's rules (docker exec pingdin-obs-prometheus wget …/api/v1/rules failed)"
else
  bad="$(printf '%s' "$rules" | python3 -c '
import json, sys
try:
    groups = json.load(sys.stdin)["data"]["groups"]
except Exception:
    print("UNREADABLE"); sys.exit(0)
# Prometheus health is "ok" | "err" | "unknown". "unknown" is a rule that has not been evaluated YET — every rule is
# "unknown" for its first interval after a restart, so counting it paged the operator right after a deploy
# (BUILD_LOG Journey 20). Only "err" means the rule has stopped working.
print(" ".join(sorted(r["name"] for g in groups for r in g["rules"] if r.get("health") == "err")))
')"
  if [ "$bad" = "UNREADABLE" ]; then
    add_problem "Prometheus returned rules in a shape this check could not read"
  elif [ -n "$bad" ]; then
    add_problem "Prometheus rule(s) in an error state (they silently stop evaluating): ${bad}"
  fi
fi

# 5. Grafana sees its dashboards -------------------------------------------------------
host_n="$(ls grafana/provisioning/dashboards/json/*.json 2>/dev/null | wc -l | tr -d ' ')"
if [ "${host_n:-0}" -gt 0 ]; then
  ctr_n="$(docker exec pingdin-obs-grafana sh -c 'ls /etc/grafana/provisioning/dashboards/json/*.json 2>/dev/null | wc -l' 2>/dev/null | tr -d ' ')"
  if [ "${ctr_n:-0}" != "$host_n" ]; then
    add_problem "Grafana's view of its provisioned dashboards has ${ctr_n:-0} file(s) but the repo has ${host_n} — its bind mount is stale (the host directory was replaced after the container started), so dashboard changes are NOT being applied. Usual cause: the repo's directories were deleted and re-copied (e.g. sudo rm -rf before a sync) — scripts/sync_to_server.sh never deletes, so use it. Fix: docker compose up -d --force-recreate pingdin-obs-grafana"
  fi
fi

# 6. every image pinned (Obs journey 31) -----------------------------------------------
# An image on a floating tag (:latest, :main ...) can change under a restart with no change here: Grafana's :latest once moved onto a real regression.
# `python3 scripts/pin_image_digests.py --write` pins each to the exact build running now (it changes nothing that runs); then copy the file back to the
# laptop and commit it. OPS_CHECK_SKIP_PINS=1 switches this off.
if [ "${OPS_CHECK_SKIP_PINS:-0}" != "1" ] && [ -f docker-compose.yml ]; then
  floating="$(grep -E '^[[:space:]]+image:[[:space:]]+[^#[:space:]]+' docker-compose.yml | grep -vE '@sha256:' | grep -E ':(latest|main|main-stable|edge|nightly)([[:space:]]|$)|image:[[:space:]]+[^:@[:space:]]+([[:space:]]|$)' | sed -E 's/^[[:space:]]*image:[[:space:]]*//; s/[[:space:]].*//' | tr '\n' ' ')"
  [ -n "$floating" ] && add_problem "image(s) on a floating tag, which can change under a restart: ${floating}— pin them to what runs now: python3 scripts/pin_image_digests.py --write, then copy docker-compose.yml back to the laptop and commit it (docs/DEPLOYMENT_RUNBOOK.md, Obs journey 31)"
fi

# report -------------------------------------------------------------------------------
if [ "${#problems[@]}" -gt 0 ]; then
  subject="Daily ops check: ${#problems[@]} problem(s) on $(hostname -s)"
  detail="$(printf -- '- %s\n' "${problems[@]}")"
  echo "$(date -u +%FT%TZ) PROBLEMS FOUND"
  echo "$detail"
  ts_ip="$(env_value TAILSCALE_IP)"; token="$(env_value OWNER_ALERT_WEBHOOK_TOKEN)"
  payload="$(python3 -c 'import json,sys; print(json.dumps({"subject": sys.argv[1], "detail": sys.argv[2], "context": {"check": "ops_daily_check"}, "source": "pingdin-observability"}))' "$subject" "$detail")"
  if [ -z "$ts_ip" ] || ! curl -fsS -m 10 -o /dev/null -X POST "http://${ts_ip}:8090/owner-alert" \
       -H "Authorization: Bearer ${token}" -H "Content-Type: application/json" -d "$payload"; then
    echo "$(date -u +%FT%TZ) COULD NOT SEND THE REPORT (alert bridge at ${ts_ip:-<TAILSCALE_IP unset>}:8090 did not accept it) — the problems above are unreported" >&2
    exit 2
  fi
  exit 1
fi

echo "$(date -u +%FT%TZ) OK — all daily checks passed"
url="$(env_value OPS_CHECK_PING_URL)"
if [ -n "$url" ]; then
  curl -fsS -m 10 --retry 2 -o /dev/null "$url" || echo "WARNING: could not ping OPS_CHECK_PING_URL (the external check will notice)" >&2
fi
exit 0
