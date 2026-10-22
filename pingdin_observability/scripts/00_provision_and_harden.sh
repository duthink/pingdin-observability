#!/usr/bin/env bash
# scripts/00_provision_and_harden.sh — run once, on the fresh
# observability droplet, as root or via sudo. Mirrors the baseline
# hardening pingdin_platform's own scripts/00_vps_hardening.sh applies
# to the main droplet (same non-root deploy user pattern, same UFW
# posture), NOT copied from that file directly — this is a from-scratch
# script for a from-scratch, much simpler box (no Postgres, no
# tenant-facing services here at all).
set -euo pipefail

# Real bug, found live (Hobo's own terminal output, first run on
# pingdin-obs): this script assumed root and had no self-elevation —
# every apt-get command failed with a silent-ish permission error when
# run as the plain deploy user, and set -e didn't reliably catch it
# because `apt-get update`'s own exit code doesn't always reflect a
# per-source lock failure. Fixed here: re-exec under sudo automatically
# if not already root, so `bash scripts/00_provision_and_harden.sh` and
# `sudo bash scripts/00_provision_and_harden.sh` both just work.
if [ "$(id -u)" -ne 0 ]; then
  exec sudo -E bash "$0" "$@"
fi

DEPLOY_USER="${DEPLOY_USER:-duthink}"

echo "== apt update/upgrade =="
apt-get update -y
apt-get upgrade -y

echo "== base packages =="
apt-get install -y ca-certificates curl gnupg ufw fail2ban unattended-upgrades

echo "== deploy user =="
if ! id "$DEPLOY_USER" &>/dev/null; then
  adduser --disabled-password --gecos "" "$DEPLOY_USER"
  usermod -aG sudo "$DEPLOY_USER"
fi

echo "== Docker =="
if ! command -v docker &>/dev/null; then
  curl -fsSL https://get.docker.com | sh
  usermod -aG docker "$DEPLOY_USER"
fi

echo "== UFW — deliberately tight: this droplet has NO public-facing service =="
# Everything this stack does (metrics/log/trace ingestion, Grafana UI)
# happens over Tailscale, never the public interface. SSH is the only
# port opened on the public interface at all.
ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
ufw --force enable

echo "== unattended-upgrades =="
dpkg-reconfigure -plow unattended-upgrades

echo "Done. Next: scripts/01_tailscale_setup.sh"
