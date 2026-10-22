# main-droplet-companion/ — the one piece that doesn't run here

This repo (`pingdin_observability`) is meant to be self-contained and
deployable on its own droplet, independent of `pingdin_platform`. There
is exactly one exception: Grafana's "reshow what happened" datasource
needs a read-only role on `pingdin_platform`'s own database, and a role
can only be created where that database actually lives — the MAIN
droplet, not this one.

## What to run, where

1. **On the main droplet** (`/opt/pingdin_platform`): copy
   `provision_grafana_ro_role.sh` there (or run it directly from an
   extracted copy of this repo, from that directory — it `cd`s to
   `/opt/pingdin_platform` on its own if not already there) and run it.
   It prints/writes `PINGDIN_GRAFANA_RO_PASSWORD` into that droplet's
   own `.env`.
2. **Copy that same password** into the observability droplet's own
   `.env`, as `PINGDIN_GRAFANA_RO_PASSWORD` (same key name).
3. **On the main droplet's own `.env`**, also set:
   - `OBSERVABILITY_GATEWAY_TS_IP` — this droplet's Tailscale IP
     (`scripts/01_tailscale_setup.sh` prints it)
   - `OWNER_ALERT_WEBHOOK_URL=http://<this-droplet-ts-ip>:8090/owner-alert`
   - `OWNER_ALERT_WEBHOOK_TOKEN` — any value you choose, matching this
     repo's own `.env`'s `OWNER_ALERT_WEBHOOK_TOKEN` exactly
   Then: `docker compose up -d pingdin-alloy-edge pingdin-edge pingdin-core`
   (the first picks up the new gateway IP; the latter two pick up the
   new webhook env vars, only if changed since their last start).

Re-run the script after a platform release that adds tables: it is
idempotent, and its grants (column-level on tenant data, plus
`core.tenant_usage` for the *Tenant Capacity & Usage* dashboard) are
what let Grafana read them.

Why this script isn't just permanently folded into `pingdin_platform`'s
own `scripts/` directory instead: that repo's own philosophy already
states "no capability without a confirmed consumer" — this capability's
only consumer is this observability build, so it lives here, with this
README making the one cross-repo dependency explicit rather than
silent.
