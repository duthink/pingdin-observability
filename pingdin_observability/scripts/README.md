# scripts/ — numbering convention

Same convention pingdin_platform's own `scripts/README.md` established:
numbered in the order they're actually meant to run, once, on a fresh
droplet. Idempotent where it matters (`01_tailscale_setup.sh` is safe
to re-run — `tailscale up` and the `.env` write are both no-ops or safe
overwrites on a second run).

| Script | Purpose |
|---|---|
| `00_provision_and_harden.sh` | Base OS hardening, deploy user, Docker, UFW (public-facing SSH only) |
| `01_tailscale_setup.sh` | Installs Tailscale, joins the tailnet, writes `TAILSCALE_IP` into `.env` |

After both: `docker compose up -d`, then follow `DEPLOYMENT_RUNBOOK.md`
for Grafana access and confirming the main droplet's own
`pingdin-alloy-edge` is actually reaching this droplet.

`main-droplet-companion/` is deliberately NOT numbered alongside these
— its one script runs on the OTHER droplet entirely. See that
directory's own `README.md`.
