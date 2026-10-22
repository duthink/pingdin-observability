# pingdin_observability

The observability droplet's own repo — separate from `pingdin_platform`,
deployed on its own machine, improvable independently.

**Goal this exists for** (Hobo's own framing): see everything that has
happened, is happening, and will happen in Pingdin, across any tenant/
user combination, while respecting the PII/proprietary-data boundary
already established in `pingdin_platform`'s own architecture — and be
able to exactly trace the cause of anything, for any user, at any time.

## What's here

| Path | Purpose |
|---|---|
| `docker-compose.yml` | Prometheus, Loki, Tempo, Grafana, Alertmanager, `alert_bridge`, self-monitoring exporters |
| `prometheus/` | Scrape/remote-write config, `recording_rules.yml` (aggregates shared by alerts AND dashboards), `alert_rules.yml` (incl. the `Watchdog` heartbeat and `AlertBridgeDown`) |
| `loki/` | Log storage config, 30d retention |
| `tempo/` | Trace storage config, 14d retention, generates RED metrics into Prometheus |
| `blackbox/` | Config for the **outside probe**: a blackbox exporter that GETs the public URLs from this droplet (`prometheus.yml` job `blackbox-public`) — the one view that catches Caddy, DNS or a TLS certificate failing while the services behind them are healthy |
| `alertmanager/` | Routes every alert to `alert_bridge`; suppresses dependents of `MainDropletHostDown`; daily cadence for the `Watchdog` heartbeat |
| `alert_bridge/` | Small FastAPI service — turns Alertmanager webhooks AND `pingdin_platform`'s existing `OWNER_ALERT_WEBHOOK_URL` calls into Telegram messages, one chat, both sources. `formatting.py` holds the pure logic (testable with nothing installed); answers 502 when Telegram fails so senders retry; exposes its own `/metrics` |
| `grafana/provisioning/` | Datasources (Prometheus/Loki/Tempo + a read-only Postgres view of `pingdin_platform`'s own `core.audit_log`) and dashboards: Host & Container Resources, App Services (edge/core/compute request rate, errors, latency), Platform Activity & Audit Trail, Tenant Operational Signals, Tenant Capacity & Usage (storage, rows, messages, LLM tokens vs. each tenant's quota) |
| `tests/` | `test_config_consistency.py` (structural checks across rules, dashboards, compose, alertmanager), `test_alert_bridge_formatting.py`, `test_pin_image_digests.py`. Run each: `python3 tests/<file>.py` |
| `scripts/` | Droplet provisioning (hardening, Tailscale) and `pin_image_digests.py` (prints the digest of each floating-tag image already running, to pin) |
| `main-droplet-companion/` | The ONE script that runs on the main droplet instead — see its own README |
| `SOVEREIGN_OBSERVABILITY.md` | What centralizes across sovereignty tiers, what deliberately doesn't, and why |
| `DEPLOYMENT_RUNBOOK.md` | The actual, ordered, tested sequence to stand this up |
| `ROUTINE_CHECKS.md` | Day-to-day: what to check after a deploy / daily / weekly / monthly / quarterly, what "good" looks like, **what to do when each alert arrives**, and what is worth automating next |
| `BUILD_LOG.md` | This repo's own authoritative decision record — same discipline `pingdin_platform`'s own `BUILD_LOG.md` follows |

## Architecture, one paragraph

`pingdin_platform`'s own `pingdin-alloy-edge` collects metrics, logs and
traces on the main droplet and ships them here, over Tailscale, to this
droplet's Prometheus/Loki/Tempo. Traces come from
`pingdin-edge`/`-core`/`-compute` (`shared/otel.py`, one trace across all
three; n8n, which used to start them, was removed in platform Journey 484); metrics from those three services (`shared/metrics.py`) plus the
Postgres/Redis/node/cAdvisor exporters; logs from every container, labelled by
container name. Grafana visualizes all three signal types, correlated (a
trace_id in a log line jumps to its full trace; a trace jumps to matching
logs), plus a fourth, business-data view for Managed tenants only — a direct,
read-only look (narrow `SELECT` grants, `NOBYPASSRLS` — those tables have no RLS, so nothing needs bypassing) at `core.audit_log`, the
same hash-chained table `pingdin_platform`'s own `core_service` already writes
to on every mutating action.

Alerting: Prometheus evaluates `alert_rules.yml` (which read shared aggregates
from `recording_rules.yml`, the same series the App Services dashboard plots)
and sends to Alertmanager, which groups, suppresses dependents of a root cause
(`inhibit_rules`) and forwards everything to `alert_bridge` — the one place an
alert becomes a Telegram message, for both Alertmanager alerts and the
platform's own `OWNER_ALERT_WEBHOOK_URL` calls. The alert path watches itself:
`alert_bridge` is scraped (`AlertBridgeDown`) and a daily `Watchdog`
heartbeat message proves the whole chain works — its absence is the signal.

## Status: deployed and confirmed working end to end

Metrics, logs, and traces are live and connected — a real message produces one trace spanning `pingdin-edge` through `pingdin-core`/`-compute` (it started at `pingdin-n8n` until platform Journey 484) (confirmed via the `traceparent` header value matching the Tempo trace ID exactly, not inferred). Telegram alerting works through both channels `alert_bridge` serves. The audit-trail dashboard renders real `core.audit_log` rows.

Seven real bugs were found and fixed getting here — see `BUILD_LOG.md` Journey 2 for the full account, including a confirmed upstream Grafana 12.2.0 regression that required pinning the image version, and a Tempo 3.0 breaking config change that landed after this repo's own knowledge cutoff.

## Not built yet, named honestly

- **Sovereign telemetry forwarding** — see `SOVEREIGN_OBSERVABILITY.md`.
  No Sovereign tenant exists yet; nothing here blocks Managed-tier
  rollout by deferring this.
