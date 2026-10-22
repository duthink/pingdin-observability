#!/usr/bin/env bash
# scripts/find_stale_containers.sh — BUILD_LOG Journey 380 (pingdin_platform), fix
# hint corrected Journey 381; copied VERBATIM into pingdin_observability (its
# Journeys 11/12): the two repos deploy independently to different servers and
# this one is deliberately self-contained. Keep the two copies' code identical.
#
# Which containers of THIS compose project are running an OLDER image than the
# one their configured image name now points to?
#
# How a container goes stale without anyone noticing: an image is rebuilt
# (`docker compose build`, or `up -d --build` for some OTHER service that
# drags this one's build along) or re-pulled (`docker compose pull`), the tag
# moves to the new image, and the running container — created from the old
# one — keeps running until it is recreated. `docker compose ps` gives one
# hint (the IMAGE column falls back to a raw `sha256:…` once the name no
# longer matches; seen live on pingdin-compute, Journey 380); this makes it a
# check instead of an eyeball.
#
#   OK       the container runs exactly the image its name resolves to now
#   STALE    the name now resolves to a different image (rebuilt / re-pulled
#            since the container was created) — recreate it:
#              docker compose up -d --build --force-recreate <service>
#            NOT plain `docker compose up -d <service>`: observed live
#            (BUILD_LOG Journey 381), it prints "Running" and leaves the
#            container stale — Compose compares its own service definition,
#            not the image behind a moved tag. --build first rebuilds a locally
#            built service from the current source (ignored for pulled images).
#            The script prints this command, filled in, when anything is stale.
#   UNKNOWN  the configured image reference no longer resolves at all
#
# Exit status: 0 if nothing is STALE, 1 if anything is. Read-only. Run from the
# directory containing docker-compose.yml. Host-only (needs the docker CLI).

set -euo pipefail

ids="$(docker compose ps -a -q)" || exit $?

total=0
stale=0
stale_services=()
while IFS= read -r id; do
  [ -n "$id" ] || continue
  total=$((total + 1))
  line="$(docker inspect --format '{{.Name}}|{{.Image}}|{{.Config.Image}}|{{index .Config.Labels "com.docker.compose.service"}}' "$id")" || exit $?
  IFS='|' read -r name running ref service <<<"$line"
  name="${name#/}"
  service="${service:-$name}"
  if current="$(docker image inspect --format '{{.Id}}' "$ref" 2>/dev/null)"; then
    if [ "$current" = "$running" ]; then
      printf 'OK       %-28s %s\n' "$name" "$ref"
    else
      stale=$((stale + 1))
      stale_services+=("$service")
      printf 'STALE    %-28s %s  (running %.19s, now %.19s)\n' "$name" "$ref" "$running" "$current"
    fi
  else
    printf 'UNKNOWN  %-28s %s  (image reference no longer resolves)\n' "$name" "$ref"
  fi
done <<<"$ids"

echo "$total container(s) checked, $stale stale."
if [ "$stale" -gt 0 ]; then
  echo
  echo "Fix (recreates onto the image the name resolves to now; --build rebuilds locally built services from the current source first):"
  echo "  docker compose up -d --build --force-recreate ${stale_services[*]}"
  echo "Plain 'docker compose up -d <service>' does NOT fix this — it leaves the container running and stale."
fi
[ "$stale" -eq 0 ]
