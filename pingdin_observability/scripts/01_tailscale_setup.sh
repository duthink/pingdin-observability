#!/usr/bin/env bash
# scripts/01_tailscale_setup.sh — installs Tailscale, brings this
# droplet onto your tailnet, and writes its own Tailscale IPv4 into
# .env as TAILSCALE_IP — docker-compose.yml's own port bindings read
# this directly, so this step must run, and .env must exist, BEFORE
# `docker compose up`.
set -euo pipefail
cd "$(dirname "$0")/.."

if ! command -v tailscale &>/dev/null; then
  curl -fsSL https://tailscale.com/install.sh | sh
fi

echo "== tailscale up — this opens an auth URL, or use --authkey= if you have one =="
sudo tailscale up --hostname=pingdin-observability

TS_IP="$(tailscale ip -4)"
echo "This droplet's Tailscale IPv4: $TS_IP"

if [ ! -f .env ]; then
  cp .env.example .env
fi

if grep -q '^TAILSCALE_IP=' .env; then
  sed -i "s/^TAILSCALE_IP=.*/TAILSCALE_IP=${TS_IP}/" .env
else
  echo "TAILSCALE_IP=${TS_IP}" >> .env
fi

echo "TAILSCALE_IP written to .env: ${TS_IP}"
echo ""
echo "Next: join your own laptop/phone to the same tailnet (once, ever),"
echo "then on the MAIN pingdin_platform droplet, set OBSERVABILITY_GATEWAY_TS_IP=${TS_IP}"
echo "in its own .env and run: docker compose up -d pingdin-alloy-edge"

echo "== ufw: allow all traffic on the tailscale0 interface =="
# Real gap found live (Hobo's own droplet, first bring-up): 00_provision_and_harden.sh
# sets default-deny-incoming and only opens SSH -- nothing ever opened the
# tailscale0 interface itself, so every ingestion port (Prometheus remote-write,
# Loki push, Tempo OTLP, alert_bridge) was unreachable from the main droplet
# even once correctly bound. Safe to allow broadly on this interface specifically
# -- only tailnet peers ever reach it at all, same trust boundary the rest of
# this repo already assumes.
sudo ufw allow in on tailscale0
sudo ufw status verbose
