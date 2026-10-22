# pingdin_observability — BUILD_LOG

Same discipline as `pingdin_platform`'s own `BUILD_LOG.md`: every
decision, every real bug, every honestly-named gap, recorded here as it
happens — not minimized, not silently fixed without a trace.

## Journey 1 — Repo bootstrap: metrics + logs + traces + alerting + a Managed-tenant audit-trail view

**Direct instruction**: stand up real observability, separate droplet,
separate repo, improvable independently — metrics, logs, and traces,
including from n8n — plus a genuine cross-tenant "see everything, trace
any cause" capability for the platform owner, respecting the PII/
proprietary-data boundary already established in `pingdin_platform`.

### Investigated before building — real findings, not assumptions

Read `pingdin_platform`'s own current `docker-compose.yml` and
`alloy-edge/config.alloy` directly, rather than trusting the earlier,
much older `pingdin-observability-build` tarball (a full from-scratch
reconstruction of the entire platform from a disaster-recovery-style
session, predating almost everything in the current repo — confirmed
stale, not used as a source for anything here beyond historical
context).

**Real, useful finding**: `pingdin-alloy-edge` is already running on
the main droplet (confirmed via `docker ps`), already configured
correctly to ship metrics/logs/traces to `OBSERVABILITY_GATEWAY_TS_IP`
— it's just never had anywhere to send to. n8n is **already fully OTel-
instrumented** — `docker-compose.yml`'s own `pingdin-n8n` service
already sets `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_SERVICE_NAME`, and
`N8N_METRICS=true`, pointed at `pingdin-alloy-edge`. This means n8n
traces/metrics work the moment this droplet exists — zero
`pingdin_platform`-side changes needed for that specific ask.

Read `Gateway_WhatsApp_Layer4.json`/`Gateway_Telegram_Layer4.json`
directly: the `Handle Message` node POSTs plain JSON to
`http://pingdin-edge:8000/v1/messages/inbound` with no manually-set
headers — meaning trace-context propagation from n8n into Edge depends
entirely on n8n's own OTel HTTP-client auto-instrumentation setting
`traceparent` automatically. Not assumed to work — flagged in
`DEPLOYMENT_RUNBOOK.md` Part 5 as the first live thing to actually
verify once traffic flows, not claimed as working here.

**What's genuinely missing, confirmed by grep against the live repo**:
zero `opentelemetry` packages anywhere in `edge_service`/`core_service`/
`compute_service`'s own `requirements.txt` — a trace starting at n8n has
nowhere to continue into today. That's real, separate follow-up work
(`pingdin_platform`'s own Phase C), not attempted in this repo.

### Built

Full stack: Prometheus (30d retention, remote-write receiver enabled),
Loki (30d), Tempo (14d, native OTLP receiver, generates RED metrics
back into Prometheus), Grafana (four datasources: the three signal
types plus a fourth, Postgres, read-only view into
`pingdin_platform`'s own `core.audit_log`), Alertmanager, and a new
small standalone service, `alert_bridge` — the single place an alert
becomes a Telegram message, for BOTH Alertmanager's own webhook shape
AND `pingdin_platform`'s existing `capabilities/alerting/owner_alert.py`
`OWNER_ALERT_WEBHOOK_URL` payload shape (read that file directly to
match its exact JSON structure and bearer-token convention, rather than
inventing a new one) — one Telegram chat receives both infra alerts and
existing app-level alerts.

**Real bug caught and fixed before delivery, not after**: the initial
Grafana Postgres-datasource design would have silently returned zero
rows for every query. `core.audit_log` is `FORCE ROW LEVEL SECURITY`
(`pingdin_platform` migration `core/012`), keyed on a per-session
`app.current_tenant_id` that a plain Grafana datasource connection
never sets — the identical failure mode `pingdin_platform`'s own
BUILD_LOG Journey 129 already found once in `core_service` itself.
Fixed with a deliberate, narrowly-scoped `BYPASSRLS` grant on the new
`pingdin_grafana_ro` role — safe specifically because that role's own
`GRANT`s are limited to `core.audit_log` and three columns of
`core.tenant`, nothing in `edge.*`, nothing on `core.pii_vault`/
`core.subject_key_ref`/`core.consent`/`core.masked_record` — bypassing
row-level filtering on tables the role can't reach at all changes
nothing. Documented in the script's own header, not just fixed silently.

**Sovereign alignment, decided and documented, not guessed**:
`SOVEREIGN_OBSERVABILITY.md` — operational telemetry (metrics/logs/
traces, pseudonymous IDs only) can centralize across every tier
identically; raw business-data reconstruction cannot, for Sovereign
tenants, without contradicting the entire premise of that tier. Default
proposed for when a Sovereign tenant actually exists: a local Grafana/
Tempo/Loki stack per Sovereign deployment, reachable to the platform
owner only via Tailscale to that specific server — full reconstruction
ability, zero centralization of the client's own data. Not built this
pass — no Sovereign tenant exists yet, named as real follow-up rather
than attempted against nothing real to design it against, same posture
`pingdin_platform`'s own BUILD_LOG already uses for exactly this
situation.

**One cross-repo dependency, made explicit rather than silent**:
`main-droplet-companion/provision_grafana_ro_role.sh` must run on the
MAIN droplet (that's where `pingdin-db` lives) — not folded permanently
into `pingdin_platform`'s own `scripts/` since this repo's only
consumer of that role is this build, per the "no capability without a
confirmed consumer" rule already established there.

### What this pass deliberately did NOT build

- App-level OTel instrumentation in `edge_service`/`core_service`/
  `compute_service` — `pingdin_platform`'s own Phase C, separate repo,
  separate BUILD_LOG entry there.
- `core.audit_log.trace_id` alignment with the real OTel trace ID —
  same, Phase C, flagged as a deliberate decision (changes a
  hash-chained column) not to bundle silently into a migration here.
- Sovereign-tier alloy-edge wiring — see above.
- A second Alloy instance in "gateway mode" on this droplet — considered
  and rejected: Prometheus/Loki/Tempo already natively accept the exact
  protocols `config.alloy`'s existing edge-mode agent already speaks
  (remote-write, Loki push, OTLP) — inserting a second Alloy between
  them would be a redundant hop with no real function, not "more
  robust."

### Verification, actually run

`docker-compose.yml` parsed with `yaml.safe_load` — valid. Every other
YAML file (`prometheus.yml`, `alert_rules.yml`, `alertmanager.yml`,
`loki-config.yaml`, `tempo.yaml`, both Grafana provisioning files)
parsed the same way — valid. Both dashboard JSON files parsed with
`json.load` — valid. `alert_bridge/main.py` — `py_compile` clean; no
`fastapi`/`httpx` installed in this sandbox (same standing limitation
`pingdin_platform`'s own BUILD_LOG names every Journey it lacks network
access), so request-handling logic is correct by inspection and
matches `owner_alert.py`'s own real payload shape exactly, but not
executed here — the real server's own test in
`DEPLOYMENT_RUNBOOK.md` Part 5 is the actual confirmation. Both shell
scripts pass `bash -n`.

**Not run, and can't be from this sandbox**: anything requiring a real
Postgres with RLS actually enabled (the `BYPASSRLS` fix's own real
effect), a real Tailscale network, or real container-to-container
traffic between two droplets. `DEPLOYMENT_RUNBOOK.md` Part 5 exists
specifically to be the authoritative live check for all of this — read
it in full before considering this "done."

### Deployment

See `DEPLOYMENT_RUNBOOK.md` for the full ordered sequence. Short
version: bootstrap the new droplet (Part 1), fill `.env` (Part 2),
`docker compose up -d --build` (Part 3), wire the main droplet in
(Part 4 — includes the one companion script that runs there), then
verify every signal actually flows (Part 5) before calling this live.

## Journey 2 — first real deployment: seven live bugs found and fixed, full stack confirmed working end to end

Everything in this Journey was found on Hobo's own real droplets, not in review — same discipline as `pingdin_platform`'s own `BUILD_LOG.md`: every bug named with its actual root cause, not just its fix.

### 1. `00_provision_and_harden.sh` — wrong directory on first run
Ran as `bash scripts/00_provision_and_harden.sh` from `/opt/pingdin_observability`, not via `sudo` — every `apt-get` call failed with a permission error, since the script assumed root without checking. **Fix**: self-elevates via `exec sudo -E bash "$0" "$@"` if not already root — works either invocation now.

### 2. Missing `ufw` rule for the `tailscale0` interface
`00_provision_and_harden.sh`'s own hardening (default-deny-incoming, SSH only) blocked every ingestion port from the main droplet, even once correctly bound — nothing had ever opened the tailscale interface itself. **Fix**: `scripts/01_tailscale_setup.sh` now runs `ufw allow in on tailscale0` right after joining the tailnet, the one point in the sequence where that interface actually exists.

### 3. `TAILSCALE_IP` unset at first `docker compose up`
Port bindings like `"${TAILSCALE_IP}:9090:9090"` silently failed to bind at all (not to 0.0.0.0 — to nothing) when the env var was empty at container-start time — confirmed live: even `curl localhost:9090` on the same host failed. **Fix**: no code change needed, just sequencing — `.env` must have `TAILSCALE_IP` set before `docker compose up`, and `--force-recreate` is required after any port-binding-affecting `.env` edit, not just `up -d`.

### 4. Tempo 3.0 breaking config change
`tempo.yaml` was written against Tempo 2.x's schema (`ingester:`, `compactor:` blocks) — Tempo 3.0, GA'd after this repo's own knowledge cutoff, removed both entirely; monolithic mode now handles compaction in-process automatically. Confirmed via Tempo's own current migration docs, not guessed. **Fix**: both blocks removed; the one real setting they used to carry — `block_retention` — moved to `overrides.defaults.compaction.block_retention`, its actual 3.0 home.

### 5. `alert_bridge` port bound to Tailscale IP, not `localhost`
First `curl http://localhost:8090/owner-alert` test failed — Docker only publishes to the exact address configured (`${TAILSCALE_IP}:8090:8090`), same as every other ingestion port by design. Not a bug — a reminder that `localhost` and the Tailscale IP are genuinely different addresses on the same box.

### 6. Grafana Postgres datasource — three compounding issues, found and fixed in sequence
- **`database` field placement**: originally top-level on the datasource entry; Grafana's Postgres provisioning silently ignores it there — confirmed against Grafana's own current docs, every real example places it under `jsonData`.
- **Version-gated provisioning**: Grafana's file provisioner only re-applies an existing datasource if the file's own `version:` is higher than what's stored — this file never set one, so every subsequent edit after the datasource's first (broken) creation was silently ignored, confirmed via Grafana's own GitHub issue #79694. Fixed with an explicit `deleteDatasources` block (forces clean recreate, sidesteps version-gating for this one change) plus `version: 3` going forward.
- **Confirmed Grafana 12.2.0 regression**: even with both fixes applied and directly confirmed correct via `GET /api/datasources/name/...` (`jsonData.database` showing `"pingdin"` correctly), every query still failed with "You do not currently have a default database configured." Matched to a real, closed upstream issue (#112418, labeled `type/regression`) — Postgres queries broken in 12.2.0 even with provably correct config. The API response also revealed the plugin identifies as `grafana-postgresql-datasource` (a newer, separately-versioned plugin) with a **separate top-level `database` field**, empty, that the query engine actually reads from — not `jsonData.database` as the general docs describe. **Fix**: `docker-compose.yml` pins `grafana/grafana:12.1.1` (the exact version the original bug report itself confirms working, since no specific fixed-version number was findable at the time), and `datasources.yml` sets `database:` in BOTH the top level and `jsonData`, covering whichever field the running version actually honors.

### 7. `pingdin-db` unreachable from the observability droplet
`dial tcp 100.111.137.8:5432: connect: connection refused` — the main droplet's own `pingdin-db` container published only `127.0.0.1:5432`, unreachable from anywhere but itself, Tailscale included. This is a `pingdin_platform` repo fix, not this one — see that repo's own `BUILD_LOG.md` (`docker-compose.yml`'s `pingdin-db` now also binds `${MAIN_TAILSCALE_IP}:5432:5432`, plus a matching `ufw allow in on tailscale0` on that droplet, which had no such rule at all before this).

### Also fixed this Journey, smaller
- `main-droplet-companion/provision_grafana_ro_role.sh`: relative-path directory resolution broke when run directly from `/opt/pingdin_platform` (landed in `/` instead, the fallback never triggered) — now `cd`s straight to the documented path. Also used `-U postgres`, a role that doesn't exist in this deployment (`pingdin`, per `pingdin_platform` migration Journey 16's own rename) — fixed to match.

### End-to-end confirmation, real, not assumed
- Metrics: main droplet → Prometheus, confirmed via Grafana's own Host & Container Resources dashboard rendering real data.
- Logs: confirmed via the same dashboard and ad-hoc Loki queries during debugging.
- Traces: a real WhatsApp-triggered n8n execution produced a trace connecting `pingdin-n8n` → `pingdin-edge` as one tree (48 child spans), confirmed via the `http.request.header.traceparent` value on Edge's own span matching the Tempo trace ID exactly — not inferred, read directly off a real span's attributes.
- Alerting: `alert_bridge`'s `/owner-alert` endpoint confirmed delivering to the real Telegram chat (`{"delivered":true}`).
- Audit-trail dashboard: confirmed rendering a real `core.audit_log` row after `pingdin_platform`'s own Journey 133 fix (that table had never held a row since it was created — a separate, serious finding surfaced specifically because this dashboard existed to check).

All fixes above are already applied in this repo's own tracked files (verified directly before this BUILD_LOG entry was written: `docker-compose.yml`'s Grafana image tag, `tempo.yaml`'s removed blocks, both `scripts/*.sh` files, `datasources.yml`'s three fixes, and `provision_grafana_ro_role.sh`'s two fixes) — not just described here as having been pasted onto a server.

## Journey 3 — Real correction: core.audit_log was never actually RLS-protected, BYPASSRLS was an unneeded elevation; new dashboard reading core.signal_ledger, the Membrane-safe answer to the "see everything" gap

Cross-repo work — found and fixed during `pingdin_platform`'s own Journey 134 (an Edge-side RLS audit there), landed here since both findings are about this repo's own provisioning script and dashboards.

### 1. Real, harmless-but-wrong finding: `BYPASSRLS` was never actually needed

Journey 1's own header comment in `provision_grafana_ro_role.sh` claimed `core.audit_log` is `FORCE ROW LEVEL SECURITY` and that `BYPASSRLS` was needed to read it cross-tenant. Checked directly against `pingdin_platform`'s own `migrations/core/012_rls_policies.sql` — **false**. That migration explicitly and deliberately EXCLUDES `core.audit_log` from RLS entirely, with its own stated reason recorded right there: "the platform owner's own cross-tenant audit review... is a legitimate, real access pattern this table must support, and RLS would make that a fight against the database rather than a feature of it." `core.signal_ledger` (that repo's migration 065) was never put under RLS either.

**What this means in practice: nothing broke, and nothing was ever actually protected by `BYPASSRLS`.** The real, correct protection this whole time was the narrow `GRANT`s (`SELECT` on exactly two tables, `USAGE` on `core` schema only, zero `edge.*`) — those were and are correct. `BYPASSRLS` was an elevation this role held but never had a real reason to use, since neither table it can read was ever RLS-protected to begin with.

**Fixed**: `main-droplet-companion/provision_grafana_ro_role.sh` — header comment corrected in place (states the real situation, doesn't just delete the wrong claim and move on), `ALTER ROLE pingdin_grafana_ro BYPASSRLS` replaced with an explicit `ALTER ROLE pingdin_grafana_ro NOBYPASSRLS` — deliberately explicit, not just removed, so re-running this idempotent script on the real server also revokes the already-provisioned role's existing `BYPASSRLS`, not just skips granting it to a future one. Least privilege: a role shouldn't hold a capability with no real table to exercise it against, even one that's causing no active harm.

### 2. New dashboard: `Pingdin — Tenant Operational Signals`, the Membrane-safe answer to Journey 2's own "see everything" gap

Journey 133 addendum 2 (`pingdin_platform`'s own BUILD_LOG) named the real fork this dashboard's own scope raised: widen `pingdin_grafana_ro` into `edge.*` (a real Membrane violation — raw tenant content one query away from an observability role, and a path that only works for Managed tenants, since Sovereign-A/B tenants' own Edge never lives on infrastructure this droplet can reach), or build a deliberate, de-identified Core-side aggregation instead. Hobo's own direction: build the Core-side aggregation properly, after confirming `core.signal_ledger`'s own design is sound (it is — see `pingdin_platform`'s Journey 134 for the full audit).

- `provision_grafana_ro_role.sh` — added `GRANT SELECT ON core.signal_ledger TO pingdin_grafana_ro`. Still `core`-schema-only, zero `edge.*` — the exact same narrow-grant discipline this script already applied to `core.audit_log`, not a widening of the Membrane boundary.
- `grafana/provisioning/dashboards/json/tenant-operational-signals.json` (new) — a time series and a "latest per tenant" table, both filtered to `subject_token IS NULL` rows only (`pingdin_platform` migration core/091's own convention for a tenant/agent-level aggregate row, as opposed to a per-person one). Structurally can never show which person did what or what any message said — only a tenant's own trailing-7-day activity/message/active-subject counts, computed nightly by `capabilities/signals/operational.py` on the platform side. Same folder, same datasource UID, same template-variable convention as the existing `platform-audit-trail.json` dashboard.

**What this is not**: not a replacement for the audit-trail dashboard (still correctly scoped to PII de-tokenization events specifically, per Journey 2's own addendum finding), and not a general activity log — it shows exactly what its own name says, tenant-level operational volume, nothing more granular.

### Verification

Both dashboard JSON files and every YAML config in this repo re-parsed clean (`json.load`/`yaml.safe_load`) after these edits — `platform-audit-trail.json` unaffected, `tenant-operational-signals.json` valid. `provision_grafana_ro_role.sh` — `bash -n` clean.

**Not verified, and can't be from this sandbox** (same standing limitation named every Journey lacking a real Postgres/Tailscale network here): the real `ALTER ROLE ... NOBYPASSRLS` and `GRANT SELECT ON core.signal_ledger` executing against the live `pingdin-db`, and the new dashboard actually rendering real rows once `pingdin_platform`'s own Journey 134 migrations have run and the nightly signal has computed at least once. Re-run this script on the main droplet, then check the dashboard.

### Deployment

```bash
# On the MAIN pingdin_platform droplet (this script's own documented target):
cd /opt/pingdin_platform
bash main-droplet-companion/provision_grafana_ro_role.sh
```
Re-running is safe and required even for an already-provisioned role — this is what actually applies `NOBYPASSRLS` and the new `signal_ledger` grant to the live role, not just to a fresh one.

```bash
# On the OBSERVABILITY droplet, after pingdin_platform's own migrations
# 091/092 have been applied (that repo's own deploy note):
cd /opt/pingdin_observability
docker compose up -d --force-recreate pingdin-obs-grafana
```
No image change — this only picks up the new dashboard JSON via the existing file-provisioner (`grafana/provisioning/dashboards/dashboards.yml`'s own 30-second poll would also pick it up without a restart, but a force-recreate is the same "verify it took" discipline this repo's own `docker-compose.yml` header already documents for Caddy elsewhere).

**Live verification, after both deploys**: open the `Pingdin — Tenant Operational Signals` dashboard, confirm the "latest per tenant" table shows a real row for at least one tenant with Shopfloor bound (may need to wait for the `0 3 * * *` cron tick, or trigger `/v1/scheduler/poll-due` once manually on the main droplet).

## Journey 4 — Full audit, this repo's own turn: two real, live bugs found, everything else confirmed still correct against the platform repo's current state

Direct instruction: check this repo properly, not just its dependencies on the platform side. Journey 3's own cross-repo work had already re-verified `core.audit_log`'s RLS exclusion and `core.signal_ledger`'s own shape once; this pass went further and read every remaining file in this repo for the first time — `prometheus.yml`, `alert_rules.yml`, `tempo.yaml`, `loki-config.yaml`, `datasources.yml`, all three dashboard JSONs, both provisioning scripts, `.env.example`, and re-confirmed `alertmanager.yml`/`alert_bridge/main.py`'s own already-checked contract once more directly.

### Bug 1: Prometheus could never actually reach Alertmanager

`prometheus/prometheus.yml`'s own `alerting.alertmanagers` target read `pingdin-alertmanager:9093`. The real container, per `docker-compose.yml`'s own `container_name`, is `pingdin-obs-alertmanager` — checked directly (`grep container_name docker-compose.yml`), not assumed from the service's own YAML key (which happens to match the container_name for every OTHER service in this file, which is likely why this one slipped through — the same convention held everywhere else). Docker's internal DNS only resolves the real container_name; the wrong hostname means Prometheus could never deliver a firing alert to Alertmanager at all. Every rule in `alert_rules.yml` — `MainDropletHostDown`, `HighCPUUsage`, `HighMemoryUsage`, `LowDiskSpace`, `PostgresDown`, `RedisDown`, `N8NDown` — would fire correctly inside Prometheus's own alerting engine and then silently go nowhere, never reaching `alert_bridge`, never reaching Telegram. The entire infra-alerting half of this stack (as opposed to the app-alerting half, `owner_alert.py`'s own webhook path, which was already confirmed correct in Journey 3) was silently dead since this repo's first bring-up.

Checked whether the same mistake repeated anywhere else in this repo (`grep -rn "pingdin-alertmanager\b" .`) — it didn't; every other cross-container hostname in `alertmanager.yml`, `tempo.yaml`, `datasources.yml` already correctly uses the real `container_name`. One instance, fixed, with the mechanism named in place rather than silently corrected.

### Bug 2: `datasources.yml`'s own comment overclaimed a protection that only existed for one of four entries

The comment documenting the Grafana version-gating bug (found and fixed for the Pingdin-DB entry specifically) claimed "every entry now also carries an explicit version" — checked directly against the actual YAML (`grep version: datasources.yml`) and found that claim false: Prometheus, Loki, and Tempo had no `version:` field at all, only Pingdin-DB did. This meant those three entries were silently exposed to the exact same silent-ignore trap the comment itself describes — any future edit to their own `jsonData` would look correct in the file and be silently ignored by Grafana's provisioner on restart, identical failure mode, just never yet triggered because none of their own configs had been touched since first creation.

**Fixed**: all four entries now carry an explicit `version:` — `1` for the three that never had one, Pingdin-DB's own `version: 3` left unchanged (already past its own real edit history). Comment corrected in place to state what actually happened, not what was previously (incorrectly) claimed to have happened — same "correct in place, don't just delete and move on" convention this repo's own prior corrections (Journey 3's `BYPASSRLS` finding) already established.

### Everything else, checked and confirmed still correct — not re-guessed, not skipped

- `main-droplet-companion/provision_grafana_ro_role.sh`'s own three grants (`core.audit_log`, `core.signal_ledger`, `core.tenant(id,name,slug)`) — every referenced column and table confirmed to still exist, unchanged, against `pingdin_platform`'s own current migrations (008, 065, 001).
- `grafana/provisioning/dashboards/json/tenant-operational-signals.json`'s own `tenant_operational_volume` signal — traced all the way to `capabilities/signals/operational.py`'s own `compute_tenant_operational_volume()` and `capabilities/signals/runner.py`'s own write path (line 194, `subject_token=None` for a `schedule_tenant`-triggered signal) — the dashboard's own `subject_token IS NULL` filter is exactly what the real write path produces.
- `platform-audit-trail.json`'s own raw SQL — all ten referenced columns (`ts, trace_id, tenant_id, principal_id, action, resource_kind, resource_ref, outcome, subject_token, metadata`) checked directly against `core.audit_log`'s real current schema (migration core/008) — exact match, nothing renamed or dropped since.
- `alert_bridge/main.py`'s own `/owner-alert` payload shape re-confirmed directly against `capabilities/alerting/owner_alert.py`'s own `_send_webhook()` (re-read on the platform side this same session, Journey 164) — still an exact match.
- `prometheus/alert_rules.yml`'s own `job="node"`/`job="n8n"` label expectations checked directly against `pingdin_platform`'s own `alloy-edge/config.alloy` scrape targets — exact match, no drift.
- `.env.example` cross-checked against every `${VAR}` actually referenced across `docker-compose.yml` and `datasources.yml` — complete in both directions, nothing referenced-but-undocumented, nothing documented-but-unused.
- All three shell scripts — `bash -n` clean.
- Every YAML/JSON config in the repo — re-parsed clean after the two fixes above, not just before them.

### Verification

`python3 -c "import yaml/json..."` re-parse of every `.yml`/`.yaml`/`.json` file in the repo, post-fix — all clean. Both fixed files' own `version:`/hostname values printed and confirmed directly, not assumed from the diff alone.

### Deployment

```bash
# On the OBSERVABILITY droplet:
cd /opt/pingdin_observability
docker compose up -d --force-recreate pingdin-obs-prometheus pingdin-obs-grafana
```
`prometheus.yml` and `alert_rules.yml` are read at Prometheus startup only — `--force-recreate` (not just `restart`) ensures the corrected config is actually picked up, not just the container cycling with the same stale volume-mounted file still in effect from before. `datasources.yml`'s own new `version:` fields take effect via Grafana's existing 30-second file-provisioner poll, but a `--force-recreate` on Grafana too avoids waiting and gives the same "verify it took" discipline the rest of this repo's own deploy notes already use.

**Live verification**: trigger a real (or manually forced) Prometheus alert — e.g., temporarily stop `pingdin-node-exporter` on the main droplet for a couple minutes to fire `MainDropletHostDown` — and confirm the Telegram message actually arrives this time. Before this fix, it silently would not have.

## Journey 5 — App-level alerting (the real, named gap), offsite backups, and closing a documentation-drift audit trail across both repos

Direct instruction, three real items at once: close the app-level alerting gap (this repo had infra-only alerts — host/CPU/memory/disk/Postgres/Redis/n8n, nothing about pingdin-edge/-core/-compute themselves), close the offsite-backup gap (`pingdin_platform`'s own `backup_postgres.sh` had the DO Spaces upload written but commented out), and fix the stale `tempo.yaml` comment (still claimed "edge/core/compute send nothing yet" after `pingdin_platform`'s own Phase C, BUILD_LOG Journey 132, closed exactly that gap).

### 1. App-level alerting — required a real capability that didn't exist yet, not just new rules

Investigated first, not assumed: `pingdin-edge`/`pingdin-core`/`pingdin-compute` had OTel tracing (Journey 132, confirmed working end-to-end) but **no Prometheus scrape target at all** — Tempo had real span data about these three services, Prometheus had nothing. Writing alert rules against metrics that don't exist would have been the same silent-dead-alert failure mode Journey 4's own Alertmanager-hostname bug already was, just for a different reason (no data instead of no route).

**Fix, cross-repo, `pingdin_platform`'s own side**: `shared/metrics.py` (new) — the same "one shared module, fails open" posture `shared/otel.py` (Journey 132) already established, mounting a real `/metrics` endpoint (via `prometheus-fastapi-instrumentator`) on each of the three services, wired in immediately alongside each `instrument_app(app)` call. `alloy-edge/config.alloy`'s own existing `prometheus.scrape "host_metrics"` block (the same relay path node/postgres/redis/n8n already use to reach this droplet across Tailscale) gained three new targets, same shape as the existing `n8n` entry.

**This repo's own side**: `prometheus/alert_rules.yml` gained a new `pingdin-app` group — `PingdinEdgeDown`/`PingdinCoreDown`/`PingdinComputeDown` (real, same `absent(up{...}) or (up{...} == 0)` shape every existing infra rule already uses, now with real data behind it), `PingdinHighErrorRate` (5xx ratio over 5m), `PingdinHighRequestLatency` (p95 over 3s for 10m).

**Honestly flagged, not silently assumed correct**: the two rate/latency rules' own metric and label names (`http_requests_total{status=...}`, `http_request_duration_seconds_bucket`) are `prometheus-fastapi-instrumentator`'s own documented defaults, but NOT yet confirmed against a real running instance from this sandbox (same standing limitation every Journey in this repo has already named for anything needing a real Postgres/Tailscale/live-traffic check) — `DEPLOYMENT_RUNBOOK.md` Part 5's own verification step 4 (this same Journey) is where that confirmation actually happens, and where to correct the expression if the real label differs.

### 2. Offsite backups — `pingdin_platform`'s own `backup_postgres.sh`

Real, not this repo's own file, but the same audit pass found it: the DO Spaces upload was written as a comment, never executed — every backup was local-only, meaning a droplet-level disaster (not just a bad migration) would take primary AND backup out together. Fixed there: reuses the SAME `DO_SPACES_BUCKET`/`_ENDPOINT`/`_KEY`/`_SECRET` env vars `capabilities/media/object_store.py` already consumes (no second credential set), via `s3cmd` (not boto3/Python — this script runs on the bare host via cron, outside any container that actually has boto3 installed, so a real CLI tool is genuinely simpler than shelling into a container to invoke Python for one upload), with a short-lived config file (credentials on disk only for the duration of one run, not a permanent `~/.s3cfg`). A failed upload logs loudly and leaves the local backup exactly as it was — never silently pruned by the retention step before someone's had a chance to notice and retry.

### 3. Documentation drift, fixed at the source, not just noted

`tempo/tempo.yaml`'s own comment corrected in place (same "correct in place, don't just delete and move on" convention this repo's Journey 3/4 corrections already established) — no longer claims edge/core/compute "send nothing yet." `DEPLOYMENT_RUNBOOK.md` Part 5's own trace-verification step had the identical staleness (referenced Phase C as not-yet-shipped) plus a genuinely useful, previously-undocumented gotcha from `pingdin_platform`'s own Journey 132 addendum 2 (Tempo's trace-search table labels a trace by its ROOT span's service regardless of which span matched the search — a fully-connected trace can still LOOK disconnected in the table view alone) — folded in so a future person verifying this doesn't have to rediscover that the hard way.

**Full cross-check performed, not just these three fixes**: every `pingdin-*` hostname referenced across every config file in this repo (`prometheus.yml`, `alertmanager.yml`, `tempo.yaml`, `loki-config.yaml`, `datasources.yml`, `docker-compose.yml`, `alert_bridge/main.py`) grepped and compared against the real `container_name` values — no second instance of Journey 4's own hostname-mismatch bug class found anywhere else in the repo.

### Verification

Every YAML/JSON config in this repo re-parsed clean (`yaml.safe_load`/`json.load`) after all edits. `pingdin_platform`'s own three touched `main.py` files, `shared/metrics.py`, and `edge_service/payments_store.py` (unrelated file from the same overall session, unaffected here) all `py_compile` clean; that repo's own full test sweep (62 sandbox-runnable files) still passes with zero regressions.

**Not verified, and can't be from this sandbox** — same standing limitation this repo's BUILD_LOG has named every Journey lacking a real Postgres/Tailscale/live-traffic network: the actual `/metrics` endpoints returning real Prometheus-format output from a running `pingdin-edge`/`-core`/`-compute`, the new scrape targets actually reporting `up`, the exact `http_requests_total`/`http_request_duration_seconds_bucket` label shape, and the offsite backup script's own `s3cmd` upload actually succeeding against a real DigitalOcean Spaces bucket. `DEPLOYMENT_RUNBOOK.md` Part 5 (this Journey) is the authoritative live check for the metrics/alerting half; `backup_postgres.sh`'s own header comment (that repo) is the authoritative live check for the backup half.

### Deployment

```bash
# On the MAIN pingdin_platform droplet:
cd /opt/pingdin_platform
docker compose up -d --build pingdin-edge pingdin-core pingdin-compute
docker compose restart pingdin-alloy-edge   # picks up config.alloy's new scrape targets
source .venv/bin/activate && pytest tests/ -v
# Set DO_SPACES_* in .env if not already, and: apt-get install -y s3cmd

# On the OBSERVABILITY droplet:
cd /opt/pingdin_observability
docker compose up -d --force-recreate pingdin-obs-prometheus
```
`prometheus/alert_rules.yml` is read at Prometheus startup only — `--force-recreate` ensures the new `pingdin-app` rule group is actually picked up, not just the container cycling with the same stale volume-mounted file still in effect. Live verification: `DEPLOYMENT_RUNBOOK.md` Part 5, steps 3 and 4 (both rewritten this Journey).

## Journey 6 — README doc drift: "Not built yet" section still claimed app-level spans, trace_id alignment, and metrics didn't exist, after Journeys 3–5 confirmed they do

Found during a full external review of both `pingdin_platform` and this repo (no code change requested — a documentation-accuracy pass). `README.md`'s own "Not built yet, named honestly" section had not been updated since this repo's Journey 1, and had drifted out of sync with its own later Journeys:

- App-level OTel spans from edge/core/compute — shipped `pingdin_platform` Journey 132, cross-checked directly against that repo's real code in this repo's own Journey 3/4 (not re-guessed).
- `core.audit_log.trace_id` alignment with the real OTel trace ID — same Journey 132, same cross-check.
- App-level Prometheus metrics/alerting for those three services — shipped `pingdin_platform` Journey 214 and this repo's own Journey 5 (`pingdin-app` alert group).

All three were sitting in a section titled "named honestly" while no longer being honest — the exact kind of drift this repo's own Journey 4 audit pass already found once in `datasources.yml`'s comments, now found again in the README. Fixed in place: the two done items removed from the pending list with a note stating what shipped and where (same "correct in place, don't just delete and move on" convention Journey 3/4 already established for code comments, applied here to the top-level README). Sovereign telemetry forwarding — the one item that's still genuinely open — left as the sole remaining entry.

### Verification
`README.md` re-read in full after the edit; no other section referenced the same stale claim. No code, config, or schema touched — documentation only.

### Deployment
None required — this is a documentation-only change, no container needs rebuilding or restarting.


## Journey 7 — Cross-repo sync audit against `pingdin_platform` Journey 373: a masked host-down alert, an unreachable latency alert, no app-service dashboard, and a runbook that could not be followed as written

Direct request: is this layer actually in sync with `pingdin_platform`, can the
operator see everything, and how is Grafana reached day to day. Both repos were
read end to end, then the operator ran read-only queries against the live
Prometheus and Loki. The last cross-check here (Journeys 3–5) predated the
platform's current state by roughly 135 Journeys.

### Confirmed in sync (live where noted)

`up` returned 1 for all eleven series (n8n, postgres, redis, node, cadvisor,
pingdin-edge/-core/-compute relayed; prometheus, node, cadvisor local);
`http_requests_total` had rows for all three services; the platform's log
format still matches Loki's derived-field regex; `core.audit_log` /
`core.signal_ledger` / `core.tenant` are unchanged in shape and the read-only
grants remain valid; the `/owner-alert` payload still matches
`capabilities/alerting/owner_alert.py`.

### 1. `MainDropletHostDown` could not fire when the whole main droplet died

Live: `up{job="node"}` returned TWO series — the main droplet's relayed
exporter and this droplet's own — because `prometheus.yml` also named its local
job `node` (and `cadvisor`). `absent(up{job="node"})` is false while any series
exists, so the alert built to catch the main droplet dying was masked by this
droplet's own healthy exporter. Not silent — `PingdinEdgeDown` and friends
still fire, since those jobs exist only on the main droplet — but the dedicated
host alert was dead. Journey 4 checked the alert's job label against Alloy's
config and never against this repo's own scrape jobs.

Fix: local jobs renamed `obs-node` / `obs-cadvisor` (every locally scraped job
is now unambiguously this droplet). Series history under the old names ages out
with retention. Held by `test_no_alert_or_dashboard_job_selector_matches_a_locally_scraped_job`
(generic: fails on ANY alert or dashboard selector matching a local job, not
just this pair), `test_collision_checker_can_actually_fail`, and
`test_main_droplet_host_down_still_selects_the_relayed_node_job` (the rename
must not be "fixed" by loosening the alert).

### 2. `PingdinHighRequestLatency` could not fire

It read `http_request_duration_seconds`, prometheus-fastapi-instrumentator's
low-resolution histogram ("only a few buckets to keep cardinality low", per the
library's own README). `histogram_quantile` never returns more than the top
finite bucket, so p95 > 3s was unreachable on it however slow requests got.
Journey 238 confirmed the metric NAME against the library and never asked
whether its BUCKETS could express the threshold. Fix: the rule reads
`http_request_duration_highr_seconds_bucket` (no labels, 20+ buckets; per-job
is all this alert needs). Held by `test_latency_alert_reads_the_high_resolution_histogram`.
One assumption is library documentation, not live-confirmed: that the highr
histogram's buckets extend past 3s — `DEPLOYMENT_RUNBOOK.md` Part 5 step 4 now
has the one-query check (`count by (le) (http_request_duration_highr_seconds_bucket)`).

### 3. Collected but not visible: no dashboard for the app services

Request rate, error ratio and latency for edge/core/compute reach Prometheus
but only alerts used them. New `pingdin-app-services.json` (uid
`pingdin-app-services`): up/down per service, request rate, 5xx ratio, p95
latency (same series as the alert), status classes, busiest handlers, handlers
returning 5xx. Service selection is a Grafana variable from
`label_values(http_requests_total, job)`, not a hardcoded list.

### 4. Log labels (fixed in `pingdin_platform` Journey 376)

Live: `/loki/api/v1/labels` returned only `["service_name"]` — no `container`.
`alloy-edge/config.alloy` had no relabel rule, so this repo's audit-trail
logs panel and the runbook's log check selected on a label that did not exist
(Journey 2's "logs confirmed" had cited a dashboard with no logs panel). Not
fixable from this repo; the platform-side rule is in that repo's Journey 376.
The runbook now checks the label exists before trusting a log query.

### 5. Platform owner alerts never reached `alert_bridge` (fixed in `pingdin_platform` Journey 376)

`docker-compose.yml` there never forwarded `OWNER_ALERT_WEBHOOK_URL`/`_TOKEN`/
`_SOURCE` into `pingdin-edge`/`-core`, so Part 4's `.env` values never reached a
container. Part 5 step 5's `curl` only proved the bridge. The runbook now adds
the check that exercises the real path (`printenv` in the container, then
`alert_owner()` from inside it).
That Journey also fixed the same gap in the Sovereign compose templates, where
the webhook is the ONLY alert channel (latent: no Sovereign tenant exists yet).

### 6. Runbook corrections (documentation)

Part 3: `localhost` curls for 9090/3100 fail even when healthy (bound to the
Tailscale IP); Tempo publishes no HTTP port. Part 5 step 4: remote-written
jobs never appear on Prometheus → Status → Targets, so "all three show UP" was
unsatisfiable — query `up` instead. Part 6: `tailscale serve --https=3000`
serves on port 3000 but the runbook's URL had no port; replaced with the
default-port form plus the tailnet prerequisites and the `GRAFANA_ROOT_URL`
step. `.env.example` pointed at "Part 4" for the serve step; it is Part 6.

### Not changed, named

`PostgresDown`/`RedisDown` use `== 0` with no `absent()`, so they do not fire
if the exporter itself disappears (the main-droplet-down and Alloy-down cases
are covered by other rules). Left as-is; a decision, not a defect found live.
There is still no Sovereign telemetry forwarding (unchanged).

### Files changed

Modified: `prometheus/prometheus.yml`, `prometheus/alert_rules.yml`,
`DEPLOYMENT_RUNBOOK.md`, `README.md`, `.env.example`. New:
`grafana/provisioning/dashboards/json/pingdin-app-services.json`,
`tests/test_config_consistency.py` (7 tests — this repo's first).

### Verification (sandbox — no Docker, Prometheus or Grafana here)

`tests/test_config_consistency.py` 7/7 passing. Mutation-checked: original
`prometheus.yml` → collision test fails naming `MainDropletHostDown` and job
`node`; original `alert_rules.yml` → latency test fails; a misspelled
datasource uid in the new dashboard → datasource test fails. Every
`.yml`/`.yaml`/`.json` re-parsed clean. NOT verified here: that Grafana renders
the new dashboard (the JSON follows the shape of the existing three at
schemaVersion 39) — that is the live check below.

### Deployment

```bash
# OBSERVABILITY droplet:
cd /opt/pingdin_observability
docker compose up -d --force-recreate pingdin-obs-prometheus pingdin-obs-grafana
```
Prometheus reads `prometheus.yml`/`alert_rules.yml` at startup only, hence
`--force-recreate`; Grafana picks up the new dashboard via its file provisioner
(30 s poll), recreated for the "verify it took" discipline used elsewhere here.
Run the two `pingdin_platform` Journey 376 deploy steps on the main droplet too
(items 4 and 5 above are fixed there, not here).

### What to check

- `TS=<this droplet's Tailscale IP>`;
  `curl -sG http://$TS:9090/api/v1/query --data-urlencode 'query=up'` — the two
  local jobs are now `obs-node` / `obs-cadvisor`; `node` and `cadvisor` appear
  once each (main droplet only).
- `curl -sG http://$TS:9090/api/v1/query --data-urlencode 'query=count by (le) (http_request_duration_highr_seconds_bucket)'`
  — buckets above `3` are listed (the assumption in item 2). If absent, the
  latency alert stays silent: tell me and it gets a real bucket-aware fix.
- Grafana → Dashboards → Pingdin → **App Services**: the three services show UP,
  request rate has lines (core at least), the Service variable lists the
  services with traffic.
- Prometheus → Alerts (`http://$TS:9090/alerts`): all rules load with no error
  state; `PingdinHighRequestLatency` present.
- Optional live proof of item 1 (do NOT use `pingdin-node-exporter` for this:
  stopping only the exporter makes Alloy report `up == 0`, which fired the alert
  even before the fix, so it proves nothing). What item 1 fixed is the whole host
  or its Alloy going silent. On the MAIN droplet:
  `docker compose stop pingdin-alloy-edge`, wait ~9 minutes (5 min for the series
  to go stale + the 2 min `for:` + grouping), then expect Telegram alerts
  including `MainDropletHostDown`; before the fix it never fired here.
  `docker compose start pingdin-alloy-edge` afterwards. Only metrics/logs/traces
  ingestion pauses meanwhile; tenant traffic is unaffected.
- `docker run --rm -v "$PWD":/w -w /w python:3.12-slim sh -c "pip install -q pyyaml pytest && pytest -v tests"` — 7 passed.

Tarball hygiene: `.git`/`__pycache__`/`.pytest_cache`/`.DS_Store` stripped;
every YAML/JSON re-parsed.


## Journey 8 — The alert path made trustworthy: a Telegram outage silently dropped alerts, nothing watched the bridge, one incident meant five messages, and alerts and dashboards each carried their own copy of the same queries

Direct instruction: go through this repo slowly, find every defect and gap, and
bring it to best practice — reusable, modular, composable, efficient. Read end
to end again (every config, `alert_bridge`, compose, dashboards, runbook, docs)
against the current `pingdin_platform` (through its Journey 377). Journey 7's
fixes are unchanged and still held by their tests.

### 1. REAL BUG — a Telegram outage silently dropped alerts (`alert_bridge`)

`alert_bridge/main.py`'s own docstring says a failure to reach Telegram "must
return a clean error to the caller (Alertmanager/Edge will retry)". The code
returned HTTP **200** with `delivered: 0` (or `false`) — and Alertmanager, and
`pingdin_platform`'s `owner_alert.py` (`raise_for_status`), retry only on a
non-2xx. So during any Telegram outage or bad-token window, every alert in
flight was reported as delivered and lost. Fixed: both handlers answer **502**
when delivery fails (a partial batch too — retrying duplicates a message,
never loses one). While there: the bearer-token comparison is now
constant-time (`hmac.compare_digest`), and the always-firing `Watchdog` renders
as a green heartbeat instead of a red "FIRING" incident. The logic moved to a
new dependency-free `alert_bridge/formatting.py` (one atomic function per
concern, importable with nothing installed) so it is finally testable —
`main.py` needs FastAPI/httpx and had no tests at all. **Consequence to know:**
`alert_bridge/Dockerfile` copied only `main.py`; splitting it created the
Journey-302 failure class (image builds, crashes at start). It now
`COPY *.py`, and a test fails the build if `main.py` imports a local module the
Dockerfile would not include.

### 2. The alert path had no watcher

Every alert — Alertmanager's and the platform's own — leaves through one
container, and nothing monitored it: if it died, every channel went silent with
no signal at all. Added: `alert_bridge` exposes its own `/metrics`
(prometheus-fastapi-instrumentator, same pin as the platform), a local
`alert-bridge` scrape job, `AlertBridgeDown`, and the standard **`Watchdog`**
dead-man's-switch (`vector(1)`, always firing) on its own Alertmanager route:
one message at start, then **once a day** (not the default 4h — a message
about something meant to be true forever, six times a day, would be noise).
The signal is its *absence*: a day without the heartbeat means rule
evaluation, Alertmanager or the bridge stopped working — the failures that
cannot page you about themselves. `AlertBridgeDown` says so in its own
description: it is delivered by the thing it watches.

### 3. One root cause fired five messages — suppression added

Every application "down" alert is an `absent(up{job=…})` rule, so when the
main droplet, its Alloy relay, or the Tailscale link dies, all of
`MainDropletHostDown`, `PingdinEdgeDown`, `-CoreDown`, `-ComputeDown` and
`N8NDown` fire within a minute, each in its own group (`group_by alertname`).
`inhibit_rules` now suppress the four dependents while `MainDropletHostDown`
fires. Postgres/Redis rules are deliberately not listed (`== 0`, they cannot
fire when the host is gone, and one firing next to a live host is a real,
separate problem). A test refuses any target that is not `absent()`-based.

### 4. One definition per shared query (`recording_rules.yml`)

`PingdinHighErrorRate` / `PingdinHighRequestLatency` and their dashboard panels
each carried a private copy of the same `rate()` / `histogram_quantile()`
expression — copies that can drift (a panel showing a number the alert does
not use), each recomputed from raw buckets on every evaluation and every page
load. Now `prometheus/recording_rules.yml` defines them once
(`job:http_requests:rate5m`, `job:http_requests_5xx:rate5m`,
`job:http_error_ratio:rate5m`, `job:http_request_duration_highr_seconds:p95_5m`)
and both the alerts and the App Services panels read them by name. The error
ratio is **zero-filled**: previously a service with traffic and no 5xx produced
*no series*, so the panel was blank for "no errors" and "no traffic" alike
(its own description was wrong about that). Tests fail if an alert or panel
goes back to a raw copy, or names a recorded series that does not exist
(a typo there makes an alert silently never fire).

### 5. Healthchecks — none existed

`docker compose ps` could not show any service unhealthy, and `depends_on` only
ever waited for a container to *start*. Added for Prometheus, Alertmanager,
Loki, Tempo, Grafana and `alert_bridge` (the last with the same python3/urllib
idiom the platform's services use; the rest `wget --spider` on each image's
documented endpoint: `/-/healthy`, `/-/healthy`, `/ready`, `/status`,
`/api/health`). **Confirmed from public examples, not run live from this
pass.** Because a wrong check under `service_healthy` would stop Grafana from
ever starting, gating is deliberately narrow: only Grafana → Prometheus is
`service_healthy`; Loki/Tempo stay start-order until you have seen them
`healthy` (runbook Part 5 step 7 says when and how to tighten). The node/cAdvisor
exporters are exempt — Prometheus's own `up{job=obs-*}` already watches them.
A test enforces that `service_healthy` only ever points at a service that
defines a healthcheck.

### 6. Documentation errors fixed

`README.md`'s architecture paragraph still said edge/core/compute "don't
export traces yet" while its own footnote further down said they do — the
paragraph is rewritten and the footnote removed. `SOVEREIGN_OBSERVABILITY.md`
pointed at `sovereign-templates/`; the directory is `deployment-templates/`.
The runbook's "does NOT cover" list still named a Phase C that is covered by
Part 5 step 3. `README.md`'s file table updated for everything above.

### 7. `scripts/pin_image_digests.py` (verbatim copy of the platform's)

`prometheus`, `alertmanager`, `loki`, `tempo`, `node-exporter` and `cadvisor`
still float on `:latest` (only Grafana was pinned, after its own real
regression). As in the platform repo: no guessed versions — it prints the
digest of what is running now, read-only. **Nothing was pinned by this
Journey.** A test compares the two copies' code when both repos are side by side.

### A defect in my own work, caught before shipping

While rewiring the alerts, my edit script located the latency rule's end marker
with a plain `index("for: 10m")`, which matched `HighCPUUsage` earlier in the
file and duplicated half of `alert_rules.yml`. YAML parsing did not object.
I noticed only because a printed excerpt showed the wrong summary text under
the latency rule, restored the file from the last delivered tarball, and redid
the edit with anchors searched from the right alert. `test_alert_names_are_unique`
now exists so this cannot pass silently; a mutation that reproduces it fails.

### Named, not hidden — still open

- **The observability droplet itself dying alerts nobody.** Everything here
  runs on it, `Watchdog` included. Covering that needs an *external*
  dead-man's-switch (a free third-party heartbeat monitor that texts you when
  pings stop) — a decision about an outside service, not built here.
- `PostgresDown` / `RedisDown` still use `== 0` with no `absent()` (Journey 7).
- Only Prometheus gates Grafana (item 5); Loki/Tempo gating waits on live proof.
- `alert_bridge` makes a blocking `httpx.post` inside `async` handlers and does
  not split Telegram messages over 4096 characters — neither reachable at
  today's alert sizes and volumes; noted for when they are.
- No image was pinned (items 5/7), and no `promtool`/`amtool` exists here, so
  the rule and Alertmanager files were validated structurally (parsed, every
  name and reference cross-checked by test), **not** by Prometheus's or
  Alertmanager's own checkers.

### Files changed

Modified: `alert_bridge/main.py`, `alert_bridge/Dockerfile`,
`alert_bridge/requirements.txt`, `alertmanager/alertmanager.yml`,
`prometheus/alert_rules.yml`, `prometheus/prometheus.yml`, `docker-compose.yml`,
`grafana/provisioning/dashboards/json/pingdin-app-services.json` (version 2),
`README.md`, `SOVEREIGN_OBSERVABILITY.md`, `DEPLOYMENT_RUNBOOK.md`,
`BUILD_LOG.md`. New: `alert_bridge/formatting.py`,
`prometheus/recording_rules.yml`, `scripts/pin_image_digests.py`,
`tests/test_alert_bridge_formatting.py` (8), `tests/test_pin_image_digests.py`
(5); `tests/test_config_consistency.py` 7 → 17.

### Verification (sandbox — no Docker, Prometheus, Alertmanager, FastAPI or network)

Test functions 7 → 30 across three files, all passing when run directly.
Mutation-checked, each failing with the intended test: 14 config mutations
(recording-rules mount removed; alert typo'd or carrying a raw copy; Dockerfile
back to `COPY main.py`; an inhibit target that is not `absent()`-based or
misspelled; Watchdog route removed or at 4h; scrape job removed; duplicate
alerts; a healthcheck removed; `service_healthy` on a service with none;
dashboard back to a raw expression; `AlertBridgeDown` on an unscraped job) and
5 logic mutations (502 → 200, partial delivery accepted, token check loosened,
Watchdog rendered as an incident, `owner-alert` back to a bare dict). All
YAML/JSON re-parsed. NOT verified here: that Prometheus accepts the rule
files, that Alertmanager accepts the routes/matchers, that any healthcheck
command works in its image, or the HTTP behaviour of `main.py` itself.

### Deployment

```bash
scp -r pingdin_observability/ duthink@142.93.209.14:/opt/
cd /opt/pingdin_observability
docker compose up -d --build      # alert_bridge is rebuilt; Prometheus/Alertmanager recreated for their new config
docker compose ps                 # wait ~1 minute
```
No `.env` change. The main droplet needs nothing from this Journey.

### What to check

- `docker compose ps` — all services `Up`; Prometheus, Alertmanager, Loki,
  Tempo, Grafana and `pingdin-alert-bridge` show `healthy`. **If any shows
  `unhealthy` while working**, the image lacks `wget` (or the path differs):
  send me `docker inspect --format '{{json .State.Health}}' <container>`. If
  it is **Prometheus**, Grafana will not start — that is the one gate; fall back
  with `depends_on … condition: service_started` for it and tell me.
- **Within about a minute, Telegram receives the heartbeat** ("💚 Alerting
  heartbeat — Prometheus, Alertmanager and this bridge are all working"), then
  once every 24h.
- `TS=<obs Tailscale IP>`;
  `curl -sG http://$TS:9090/api/v1/query --data-urlencode 'query=up{job="alert-bridge"}'`
  → value `1`.
- `curl -s http://$TS:9090/api/v1/rules | python3 -c "import sys,json;[print(g['name'],[r['name'] for r in g['rules']]) for g in json.load(sys.stdin)['data']['groups']]"`
  → five groups: `pingdin-app-recording` (4 records) plus the four alert
  groups (`pingdin-infra`/`-services`/`-app`/`-meta`), all `health: ok` (`...|grep -c '"health":"err"'` → 0).
- `curl -sG http://$TS:9090/api/v1/query --data-urlencode 'query=job:http_error_ratio:rate5m'`
  — one series per service with recent traffic, value `0` while healthy (send a
  Telegram/WhatsApp message first).
- Grafana → **App Services**: same three panels as before, now plotting the
  recorded series; the error-ratio panel shows `0`, no longer blank, for a
  service with traffic and no errors.
- Alert path works: `curl -s -o /dev/null -w "%{http_code}\n" -X POST http://$TS:8090/owner-alert -H "Authorization: Bearer <OWNER_ALERT_WEBHOOK_TOKEN>" -H "Content-Type: application/json" -d '{"subject":"journey 8 check","detail":"bridge","source":"runbook"}'`
  → `200` and a Telegram message. (The new **502** path — Telegram failing —
  is covered by unit tests only; do not break the bot token in production to
  see it.) A wrong token still gives `401`.
- Suppression, optional: runbook Part 5 step 7 (stop `pingdin-alloy-edge` ~9
  minutes; expect **one** critical message, not five).
- `python3 scripts/pin_image_digests.py` on this droplet — lists the six
  floating services with a digest line each. Pin when ready; then
  `docker compose up -d` should recreate nothing.
- `python3 tests/test_config_consistency.py && python3 tests/test_alert_bridge_formatting.py && python3 tests/test_pin_image_digests.py`
  (PyYAML needed; or the throwaway-container line in the test file header) —
  17 + 8 + 5 passed.

Tarball hygiene: `.git`/`__pycache__`/`.pytest_cache`/`.DS_Store` stripped;
every YAML/JSON re-parsed.


## Journey 9 — Live results from the Journey 8 deploy: what it confirmed, Loki and Tempo reported `unhealthy` while serving (healthchecks replaced by Prometheus watching them), and one check that proved nothing

The operator deployed Journey 8 and ran its "What to check" on the real
server (and the platform's, see `pingdin_platform` Journey 378). This records
what came back and changes only what it showed was wrong.

### Confirmed live

- The `Watchdog` heartbeat arrived in Telegram ("💚 Alerting heartbeat …") —
  Prometheus evaluated the always-on rule, Alertmanager routed it on its own
  route, the rebuilt `alert_bridge` (with `formatting.py` in the image)
  delivered it. Also implies Prometheus and Alertmanager accepted every new
  rule/route/matcher: either would have refused to start on a bad file.
- `docker compose ps`: **Prometheus, Alertmanager, Grafana and
  `pingdin-alert-bridge` `healthy`** — their `wget` / python checks are right
  (Grafana's `/api/health` included; Grafana started, so the one
  `service_healthy` gate, on Prometheus, works).
- `/api/v1/rules`: five groups, every rule `health: ok` — `pingdin-app` 5,
  `pingdin-infra` 4, `pingdin-meta` 2, `pingdin-services` 3,
  `pingdin-app-recording` 4.
- `up`: `alert-bridge` = 1; `pingdin-edge`/`-core`/`-compute`, `n8n`,
  `postgres`, `redis`, `node`, `cadvisor` (relayed) and `obs-node`,
  `obs-cadvisor`, `prometheus` (local) all 1. `alert_bridge`'s `/metrics`
  serves Prometheus text.
- **Journey 7's open assumption is confirmed:** `http_request_duration_highr_seconds_bucket`
  has buckets at 3.5, 4, 4.5, 5, 7.5, 10, 30, 60 — the histogram
  `PingdinHighRequestLatency` now reads *can* express "p95 above 3s". (The
  query counted 4 series per `le`: the three services plus `alert-bridge`,
  which now exports the same histogram via its own instrumentator; the
  recording rules' job regex correctly excludes it — inferred from the regex,
  not separately queried.)
- **Platform Journey 376's Loki label is live:** `/loki/api/v1/labels` now
  lists `container` alongside `service_name`.
- `job:http_error_ratio:rate5m`: `pingdin-core` = `0` (traffic, no errors —
  the zero-fill working); `pingdin-edge` = `NaN` (no requests in the last 5
  minutes: 0/0, which the panel shows blank and the alert treats as false);
  `pingdin-compute` absent (instrumentator counters appear on a service's
  first request, and it has had none yet). All three are the documented
  meanings, not faults.

### Exposed: Loki and Tempo `unhealthy` while working

Journey 8 flagged this risk in advance: their `wget --spider` checks were
confirmed from public examples, not run live. On the real server both show
`unhealthy`, yet Loki answered its labels API and Alloy was delivering to
both. So the checks — not the services — are wrong (a missing tool or a
different path in those images; the cause was not diagnosed and does not
matter to the fix). Journey 8's narrow gating is what kept this harmless:
Grafana started regardless. The fix is not to guess a third check but to stop
depending on the image at all: **Prometheus now scrapes each one's own
`/metrics`** (`obs-loki` → `pingdin-obs-loki:3100`, `obs-tempo` →
`pingdin-obs-tempo:3200`, ports asserted against `loki-config.yaml` /
`tempo.yaml` by test), and **`LokiDown` / `TempoDown`** alert on it — which
also closes a gap Journey 8 had not named: nothing alerted if either store
died. Their Docker healthchecks are removed. A new invariant test says a
service without a healthcheck must be scraped by Prometheus, so no service can
silently be neither. Grafana's `depends_on` for Loki/Tempo stays
`service_started` **permanently** — Journey 8 called that "until proven"; the
right reason is that Grafana must still come up when they are down, since that
is when you need it to look at Prometheus and the alerts. (Same principle as
`pingdin_platform` Journey 378's Caddy/n8n decision.) Runbook Part 5 step 7
rewritten to match.

### A check that proved less than it looked

The Journey 8 owner-alert `curl` was run with the literal placeholder
`<OWNER_ALERT_WEBHOOK_TOKEN>` still in the header, so it returned **401**.
That does prove the bearer check rejects a wrong token, but it does not test
the delivery path — the 200-and-Telegram result has still not been seen. Rerun
it with the real token (read from `.env`, not typed into a chat).

### Explained, not a fault

`up` briefly showed two `node` and two `cadvisor` series: this droplet's
Prometheus had been recreated two minutes earlier, and the pre-Journey-7 local
series (`job="node"`, `job="cadvisor"`, instance `pingdin-obs-*`) stay
visible for the 5-minute lookback window after their last sample. They vanish
by themselves; the check that they are gone (and that the surviving `node` and
`cadvisor` are the main droplet's) is in the list below.

### Files changed

Modified: `docker-compose.yml` (Loki/Tempo healthchecks removed, Grafana
`depends_on` comment), `prometheus/prometheus.yml` (+2 jobs),
`prometheus/alert_rules.yml` (+`LokiDown`, `TempoDown`; 16 alerts),
`DEPLOYMENT_RUNBOOK.md` (Part 5 step 7), `tests/test_config_consistency.py`
(17 → 19), `BUILD_LOG.md`. No new files.

### Verification (sandbox — no Prometheus/Docker here)

`test_config_consistency.py` 19/19, `test_alert_bridge_formatting.py` 8/8,
`test_pin_image_digests.py` 5/5. Five new mutations each fail the intended
test: Loki scrape job removed (two tests), wrong Loki port, `TempoDown`
selecting a misspelt job, Grafana gated on Loki being healthy (two tests), a
service left with neither a healthcheck nor a scrape target. YAML/JSON
re-parsed. Not verified here: that Loki/Tempo serve `/metrics` on those ports
in the deployed versions (standard for both; the `up{job=~"obs-.*"}` query
below is the check).

### Deployment

`prometheus.yml` and `alert_rules.yml` are bind-mounted and read only at
start, and the Prometheus service definition itself did not change, so a
plain `up -d` would NOT reload them:
```bash
scp -r pingdin_observability/ duthink@142.93.209.14:/opt/
cd /opt/pingdin_observability
docker compose up -d                                  # recreates Loki and Tempo (healthcheck removed)
docker compose up -d --force-recreate pingdin-obs-prometheus
docker compose ps
```
Loki and Tempo restart briefly; the main droplet's Alloy retries and buffers,
so expect a short burst of `connection refused` in `docker logs pingdin-alloy-edge`.

### What to check

- `docker compose ps` — Prometheus, Alertmanager, Grafana, `pingdin-alert-bridge`
  `healthy`; Loki and Tempo now show plain `Up` (no health column); no `unhealthy`.
- `TS=100.92.158.102; curl -sG http://$TS:9090/api/v1/query --data-urlencode 'query=up' | python3 -c "import sys,json;[print(r['metric'].get('job'),r['metric'].get('instance'),r['value'][1]) for r in sorted(json.load(sys.stdin)['data']['result'],key=lambda r:r['metric'].get('job',''))]"`
  → **14** lines, all `1`, exactly one `node` and one `cadvisor` (instances
  `pingdin-node-exporter:9100` / `pingdin-cadvisor:8080`, the main droplet's),
  plus `obs-loki`, `obs-tempo`, `obs-node`, `obs-cadvisor`, `prometheus`,
  `alert-bridge`, `n8n`, `postgres`, `redis`, `pingdin-edge`, `-core`,
  `-compute`. Run it ≥5 minutes after the deploy.
- `curl -s http://$TS:9090/api/v1/rules | python3 -c "import sys,json;print(sum(len(g['rules']) for g in json.load(sys.stdin)['data']['groups']), 'rules')"`
  → `20 rules` (16 alerts + 4 records), none in an error state.
- Owner-alert path, with the real token: on this droplet
  `T=$(grep '^OWNER_ALERT_WEBHOOK_TOKEN=' .env | cut -d= -f2); curl -s -o /dev/null -w "%{http_code}\n" -X POST http://$TS:8090/owner-alert -H "Authorization: Bearer $T" -H "Content-Type: application/json" -d '{"subject":"journey 9 check","detail":"bridge","source":"runbook"}'`
  → `200` and a Telegram message.
- After a few minutes, on the main droplet: `docker logs pingdin-alloy-edge --since 5m 2>&1 | grep -ciE "refused|too old"` → `0`.


## Journey 10 — Second live run: the Journey 9 deploy confirmed, and this repo's runbook told the operator to print a secret

The operator deployed Journey 9, recreated the platform's edge and core with
the webhook URL set (`pingdin_platform` Journey 379 has that side), and ran
the checks again. Only what the output showed was wrong is changed here.

### Confirmed live

- `docker compose ps`: **no `unhealthy`**. Prometheus (recreated), Alertmanager,
  Grafana and `pingdin-alert-bridge` show `healthy`; **Loki and Tempo now show
  plain `Up`** — Journey 9's removal of their two wrong `wget` checks did what
  it said. (`pingdin-obs-cadvisor` shows `healthy` because its image ships its
  own healthcheck; not ours.)
- The owner-alert path works end to end: one `alert_owner()` call from
  `pingdin-edge` produced a second Telegram message through this bridge
  (`[unknown-deployment]` — `OWNER_ALERT_SOURCE` is unset on the main droplet)
  alongside the platform's own direct one. That is the first time the real
  platform → bridge → Telegram path has been observed; it is why the runbook's
  Part 5 step 5 now says to expect **two** messages, not one.

### Not yet seen

The three `curl` commands at the end of the pasted terminal session
(`count by (le) …`, Loki labels, `job:http_error_ratio:rate5m`) echoed with no
output. Their results were already confirmed on the previous run, so nothing
depends on this — but the most likely cause is worth knowing: `TS` was not set
in that shell, and `curl -s` prints nothing on failure. `export
TS=100.92.158.102`, or use `curl -sS` to see errors.

### A defect in this repo's own runbook

`DEPLOYMENT_RUNBOOK.md` Part 5 step 5 told the operator to run
`docker exec pingdin-edge printenv | grep OWNER_ALERT_WEBHOOK` on the main
droplet and said "URL + token must print". It printed the token; the real
`OWNER_ALERT_WEBHOOK_TOKEN` was pasted into a chat twice. Replaced with
`pingdin_platform`'s new `scripts/show_container_env.sh` (its Journey 379),
which shows secrets as `<set>` / `<EMPTY>` and everything else in full; the
step now explains why. `tests/test_config_consistency.py` gains
`test_live_docs_never_tell_the_operator_to_print_env_values` (scans the live
docs; `BUILD_LOG.md` exempt) — mutation-checked by putting the old command back.
The scan caught **my own new sentence** quoting the forbidden command on its
first run; reworded.

The token should still be rotated (it has been exposed twice): new value in
BOTH `.env` files, `docker compose up -d --force-recreate pingdin-alert-bridge`
here, `--force-recreate pingdin-edge pingdin-core` on the main droplet.

### Files changed

Modified: `DEPLOYMENT_RUNBOOK.md` (Part 5 step 5), `tests/test_config_consistency.py`
(19 → 20), `BUILD_LOG.md`. No config, compose, rule, or application change —
nothing to recreate or restart on this droplet.

### Verification (sandbox)

`test_config_consistency.py` 20/20, `test_alert_bridge_formatting.py` 8/8,
`test_pin_image_digests.py` 5/5; the new test fails with the old
`printenv | grep` line back in the runbook.

### Deployment

Docs and a test only:
```
scp -r pingdin_observability/ duthink@142.93.209.14:/opt/
```

### What to check

- `export TS=100.92.158.102` (this droplet), then the checks that were still
  pending from Journey 9:
  - `curl -sSG http://$TS:9090/api/v1/query --data-urlencode 'query=up' | python3 -c "import sys,json;[print(r['metric'].get('job'),r['metric'].get('instance'),r['value'][1]) for r in sorted(json.load(sys.stdin)['data']['result'],key=lambda r:r['metric'].get('job',''))]"`
    → **14** lines, all `1`, exactly one `node` and one `cadvisor` (the main
    droplet's), plus `obs-node`, `obs-cadvisor`, `obs-loki`, `obs-tempo`,
    `prometheus`, `alert-bridge`, `n8n`, `postgres`, `redis`, `pingdin-edge`,
    `-core`, `-compute`.
  - `curl -sS http://$TS:9090/api/v1/rules | python3 -c "import sys,json;[print(r['name'],r['state']) for g in json.load(sys.stdin)['data']['groups'] for r in g['rules'] if r['type']=='alerting']"`
    → 16 alerting rules; **only `Watchdog` is `firing`**, all others
    `inactive` (`LokiDown` / `TempoDown` included).
- Grafana (see the runbook's Part 6 for the tunnel / `tailscale serve` URL;
  login user `duthink`): **App Services** shows three services UP with request
  rate lines; **Platform Activity & Audit Trail** logs panel now has lines
  (empty before the platform's `container` label); Explore → Loki
  `{container="pingdin-edge"}` shows lines, and clicking a `trace_id` opens the
  trace in Tempo; Explore → Tempo lists `pingdin-n8n`, `-edge`, `-core`,
  `-compute` after some traffic.


## Journey 11 — Third live run: this droplet fully confirmed; a stale-container check added; and Journey 8's "pinning recreates nothing" corrected

The operator ran the pending Journey 10 checks on this droplet (and the
platform's, `pingdin_platform` Journey 380). Nothing here was wrong; one
expectation of mine was.

### Confirmed live

- `up`: **14 series, all `1`**, exactly one `node` (`pingdin-node-exporter:9100`)
  and one `cadvisor` (`pingdin-cadvisor:8080`) — the main droplet's — plus
  `obs-node`, `obs-cadvisor`, `obs-loki`, `obs-tempo`, `prometheus`,
  `alert-bridge`, `n8n`, `postgres`, `redis`, `pingdin-edge`, `-core`,
  `-compute`. The Journey 7 collision (`MainDropletHostDown` masked by this
  droplet's own `job="node"`) is confirmed gone in production, and the
  transient duplicate series Journey 9 explained have cleared.
- Alerting rules: 16, all `inactive` except `Watchdog` `firing` — `LokiDown`,
  `TempoDown` and `AlertBridgeDown` included, each on a job Prometheus really
  scrapes.
- `docker compose ps`: Prometheus, Alertmanager, Grafana, `pingdin-alert-bridge`
  `healthy`; Loki, Tempo and the node exporter plain `Up`; cAdvisor `healthy`
  (its own image's check). Two hours of uptime since the Journey 9 deploy.

### Correction to Journey 8

Journey 8's "What to check" said that after pasting pinned digests over the
floating tags (`scripts/pin_image_digests.py`), `docker compose up -d` should
"recreate nothing". Wrong: Compose derives each service's config hash from its
definition, `image:` included, so changing `prom/prometheus:latest` to
`prom/prometheus@sha256:…` changes the hash and recreates the container — onto
the identical image, but a restart. Here that is a brief gap in Prometheus /
Alertmanager / Loki / Tempo (the main droplet's Alloy retries and buffers) —
not dangerous, but not nothing. Preview it with
`docker compose up -d --dry-run` (lists each service it would Recreate).
(Basis: Compose's own issue tracker; **not run on these servers**. Full
account in `pingdin_platform` Journey 380.)

### New: `scripts/find_stale_containers.sh` (verbatim copy of the platform's)

Compares, for every container of this compose project, the image it runs with
the image its configured name resolves to now: `OK` / `STALE` / `UNKNOWN`, exit
1 on any `STALE`, and it names the fix (`docker compose up -d <service>`).
It exists because the platform droplet had a container (`pingdin-compute`) that
had been running an older build than its tag for hours with nothing noticing;
the same can happen here after any `docker compose pull` or rebuild that is not
followed by a recreate — and this droplet's exporters are 7 weeks old, the
likeliest to drift. Runbook Part 5 step 7 has a new bullet. The test file is a
copy of the platform's (`tests/test_find_stale_containers.py`, 8 tests: the real
script under bash against a stub `docker`), plus a check that the two scripts'
code is identical when both repos are side by side (verified identical now).

### Files changed

New: `scripts/find_stale_containers.sh`, `tests/test_find_stale_containers.py`
(8). Modified: `DEPLOYMENT_RUNBOOK.md` (Part 5 step 7), `BUILD_LOG.md`. No
config, compose, rule, or application change.

### Verification (sandbox)

`test_find_stale_containers.py` 8/8 (the same file as the platform's, whose six script mutations each fail it); `test_config_consistency.py`
20/20, `test_alert_bridge_formatting.py` 8/8, `test_pin_image_digests.py` 5/5.
Not verified here: the script against a real Docker daemon.

### Deployment

Scripts, docs and tests only — nothing to rebuild or restart:
```
scp -r pingdin_observability/ duthink@142.93.209.14:/opt/
```

### What to check

- `cd /opt/pingdin_observability && bash scripts/find_stale_containers.sh` —
  most likely every line `OK` and `8 container(s) checked, 0 stale.` — but the exporters
  and cAdvisor were last created 7 weeks ago on a floating tag, so a `STALE`
  line for one of them is plausible and harmless: `docker compose up -d
  <service>` recreates it on the image already present (nothing is pulled).
  Report what it prints.


## Journey 12 — Fourth live run: the alert-suppression drill proved in production; the stale-container fix corrected; and the routine — checks by cadence, and a first move for every alert — written down

The operator ran the Journey 11 checklist (platform side: `pingdin_platform`
Journey 381). This records what came back and what was added.

### Confirmed live (29 Sep 2026)

- **Journey 8's `inhibit_rules` work.** With `pingdin-alloy-edge` stopped on the
  main droplet and then started, Telegram received exactly one
  `🔴 [FIRING] MainDropletHostDown (critical)` (3:54 PM) and one
  `✅ [RESOLVED]` (3:59 PM) — and nothing for `PingdinEdgeDown`, `-CoreDown`,
  `-ComputeDown` or `N8NDown`, which fire alongside it whenever the host's
  metrics go silent. This is also the first end-to-end proof of the full alert
  chain for a real condition, not a synthetic one: rule → Alertmanager → bridge →
  Telegram, firing and resolving.
- `find_stale_containers.sh` on this droplet: 8 checked, **0 stale**. The four
  test files (20 / 8 / 5 / 8) pass in a throwaway `python:3.12-slim` container.
- **Token rotation is consistent**: the bridge was recreated with the new
  `OWNER_ALERT_WEBHOOK_TOKEN`, and the platform's next `alert_owner()` still
  reached Telegram through it, now labelled `[pingdin-managed]`.
- Tailscale console: both machines `Connected`, client 1.102.3. Kernels differ by
  one patch (`6.8.0-138` here, `-139` on the main droplet) — noted in the monthly
  routine as an OS-update prompt, not a fault.

### Correction: the fix for a stale container

Journey 11 (and its runbook step and the script's header) said to fix a `STALE`
container with `docker compose up -d <service>`. On the platform droplet that
printed `Running` and left `pingdin-compute` stale — Compose compares its own
service definition, not the image behind a moved tag. The working fix is
`docker compose up -d --build --force-recreate <service>`. The script (a verbatim
copy of the platform's, code identical — checked) now prints that command with the
compose **service** names filled in and warns off the plain form; the runbook's
Part 5 step 7 is corrected. Full account: `pingdin_platform` Journey 381.

### The routine, written down: `ROUTINE_CHECKS.md`

Requested directly ("how to do all these, what to check at what intervals, what to
expect, any automations"). This repo's `ROUTINE_CHECKS.md` is the hub for the
monitoring stack: what is already automated (and how each thing reaches you),
what is not; **daily** (the 💚 heartbeat), **after every deploy** (a command/expect/
if-not table, including the `up` query with its 14 expected lines and the two
drift checks), **weekly** (stale + drift, alerts, storage growth with the actual
volume names and retention windows, Tailscale — including *disable key expiry*, a
Grafana glance), **monthly** (OS updates, deliberate image moves, credential
rotation), **quarterly** (the suppression fire drill above, with its measured
timings), and a **first-move table for every alert** (what it means, the first
command, which droplet) plus a "heartbeat missing" procedure.
The main droplet's routine — migrations, backups, restore drill — is in the
platform's `docs/ROUTINE_CHECKS.md`; the two cross-reference each other.
Held by three new tests in `tests/test_config_consistency.py` (20 → 23): every
alert in `alert_rules.yml` has a row in the doc (a new alert without a documented
first move fails the build); every script/test path the doc names exists (the one
platform-side script is an explicit allowlist entry); and the numbers it quotes —
rule count, the local scrape jobs in the `up` list, container count, retention —
match the config. Six mutations each fail the intended test. The Journey 10
docs-scan for bare secret-printing commands now covers it too.

### Automation: recommended, NOT built

In the doc's last section, in order of value: an **outside probe** of
`https://app.pingd.in/health` and the TLS certificate from *this* droplet (a
blackbox exporter — one container, one scrape job, two rules; nothing alerts today
if Caddy, DNS or the certificate fails while the services behind it are healthy);
an **external dead-man's-switch** for this droplet dying (everything here,
`Watchdog` included, runs on it); a **daily drift check** cron using the scripts
that already exist. Plus the no-build item: disable Tailscale key expiry on both
servers. Not built without the operator's decision — the first adds a container
and probes production URLs, the second is an outside service.

### Files changed

New: `ROUTINE_CHECKS.md`. Modified: `scripts/find_stale_containers.sh` (fix hint),
`tests/test_find_stale_containers.py` (8 → 10), `tests/test_config_consistency.py`
(20 → 23), `DEPLOYMENT_RUNBOOK.md` (Part 5 step 7 corrected; pointer),
`README.md` (file table), `BUILD_LOG.md`. No config, compose, rule or application
change.

### Verification (sandbox)

`test_config_consistency.py` 23/23, `test_alert_bridge_formatting.py` 8/8,
`test_pin_image_digests.py` 5/5, `test_find_stale_containers.py` 10/10; the
platform copy's five output-mutations each fail that shared test file. All
YAML/JSON re-parsed.

### Deployment

Docs, one script, tests — nothing to rebuild or restart:
```
scp -r pingdin_observability/ duthink@142.93.209.14:/opt/
```

### What to check

- `export TS=100.92.158.102; cd /opt/pingdin_observability` then the "After every
  deploy" block in `ROUTINE_CHECKS.md` — `docker compose ps` (8 `Up`, the five named
  `healthy`), `find_stale_containers.sh` (`8 container(s) checked, 0 stale.`),
  `docker compose up -d --dry-run 2>&1 | grep -i recreat` (no output), the `up`
  query (14 lines, all `1`), the alerts query (only `Watchdog firing`).
- The four test files in the throwaway container (command in that doc's deploy
  section) → 23 / 8 / 5 / 10 passed.


## Journey 13 — What is and isn't covered, precisely (tenants, agents, Sovereign, Telegram): documented, with the Sovereign alert-transport decision laid out

Direct questions from the operator: will all edge/core/compute containers across
tenants and agents be visible, are Sovereign clients covered, will everything reach
the Telegram admin bot (including Sovereign via `OWNER_ALERT_WEBHOOK_URL`), and
would anything break. Answered from the code in `pingdin_platform` (its
Journey 382 has the full findings and the one code fix); recorded here because
this repo owns the picture of what monitoring covers. Documentation only.

### What the documentation now says (`SOVEREIGN_OBSERVABILITY.md`, new section
### "What is covered TODAY, precisely")

- **Tenants and agents are not separate in the telemetry.** One pooled
  edge/core/compute serve all Managed tenants; Agents are code inside Edge/Core.
  Metrics have `job`/`handler`/`status` only; no tenant/agent field in spans or the
  log format. You see three *services*. Per-tenant insight is the database
  dashboards (*Tenant Operational Signals*, all tiers; *Audit Trail*, Managed only)
  and `alert_owner()` pages carrying `tenant_id`.
- **"All issues in Telegram" is not true**, and the doc lists both sides: the 16
  rules and roughly 30 `alert_owner()` call sites (none in Compute) reach it;
  logged-only exceptions, a single 5xx, one slow tenant, backups/drill, stale
  containers, the public site or certificate failing, and anything on a Sovereign
  client's server do not.
- **Sovereign, today:** no Sovereign tenant exists; `core.pingd.in` is not active.
  Pingdin-hosted Core (and Compute for A) are covered like Managed; the
  client-hosted Edge/DB/n8n/Caddy are not monitored at all, so a dead client Edge
  tells nobody.
- **The `OWNER_ALERT_WEBHOOK_URL` for Sovereign assumption, corrected**: the
  mechanism exists, but the URL is a tailnet address the bridge alone listens on
  (unreachable from a client's server); onboarding writes no `OWNER_ALERT_*` into a
  client's `.env`; there is one shared bearer token (readable by the client, breaks
  everyone when rotated; the source label is spoofable); alert `detail` text needs
  an audit against Sovereign's no-raw-content premise; and an unreachable URL used
  to stall the Edge (bounded in platform Journey 382).
- **Options with a recommendation, not built:** (A) client joins the tailnet
  under a tight ACL; (B, recommended) relay through Core using the tenant-bound
  credential onboarding already issues — no new client config, per-tenant,
  revocable, unspoofable source, and needs `core.pingd.in` active; (C) publicly
  exposed bridge, not recommended. Plus a no-client-agent liveness signal from
  `core.service_credential.last_used_at`.

Also fixed a stale line in that doc: it said traces would ship "once Phase C ships";
they have (`shared/otel.py`, edge/core/compute). `ROUTINE_CHECKS.md` gains a short
"what does *not* reach Telegram" note pointing at it.

### Files changed

Modified: `SOVEREIGN_OBSERVABILITY.md`, `ROUTINE_CHECKS.md`, `BUILD_LOG.md`. No
config, compose, rule, script or test change.

### Verification (sandbox)

`test_config_consistency.py` 23/23 (its doc checks include `ROUTINE_CHECKS.md`),
`test_alert_bridge_formatting.py` 8/8, `test_pin_image_digests.py` 5/5,
`test_find_stale_containers.py` 10/10. Every factual claim above was checked
against the platform code (the call sites by AST, the Caddyfile, the onboarding
script's written env, the Sovereign compose templates) — none is inferred from
memory.

### Deployment

Docs only — nothing to rebuild or restart:
```
scp -r pingdin_observability/ duthink@142.93.209.14:/opt/
```

### What to check

Nothing to verify on the server for this Journey. The decision it asks for — how a
Sovereign Edge's alerts should reach you, before the first client is onboarded — is
the operator's.


## Journey 14 — Live check of the Grafana dashboards: a logs panel buried in noise, a fourth service on a three-service dashboard, an audit table that says "No data" (not a query bug), and a stale RLS claim I had repeated

The operator finished the platform deploy of `pingdin_platform` Journey 382 and ran
the Grafana checks with real traffic. This records what came back and fixes only
what it showed was wrong.

### Confirmed live (29 Sep 2026, evening)

- **Platform deploy:** `find_stale_containers.sh` → 13 checked, **0 stale** (the
  standard `up -d --build` recreated compute onto the rebuilt image); the new
  `owner_alert.py` timeouts are live in Edge (`connect=3.0` Telegram, `connect=2.0`
  webhook); `pytest` 3466 passed / 4 skipped / 0 failed (as predicted); the
  fingerprint diff was exactly `pending_migrations.sh` added and `owner_alert.py` +
  `find_stale_containers.sh` modified. One `alert_owner()` call gave two Telegram
  messages **both with `tenant_id: test`** — so the earlier bridge message that
  seemed to lack it was clipping in the paste, not a defect.
- **Tailscale key expiry disabled** on both servers.
- **App Services dashboard:** three UP tiles (compute, core, edge); request-rate,
  error-ratio and p95 series for all three; "Handlers returning 5xx" empty; busiest
  handlers show core's scheduler poll and the bridge's `/owner-alert`.
- **Traces (Grafana Drilldown → Traces, Tempo):** span rate for all four services —
  `pingdin-edge`, `-core`, `-compute` and `pingdin-n8n` — with sub-millisecond to
  ~34 ms durations. So traces flow from every service, n8n included. One error
  spike near 19:20 IST coincides with the deploy window (edge/core/compute
  recreated about then); **inferred, not confirmed** — clicking the red bar opens
  the failing trace.
- **Explore → Loki "No data":** an empty query (no label chosen), not a fault. Pick
  label `container` = `pingdin-edge`, then Run query (or Code mode:
  `{container="pingdin-edge"}`); Grafana 12's banner about Explore having "moved"
  to Drilldown refers to the metrics/logs/traces *apps*, not this query editor.

### Fixed: the Audit Trail logs panel was ~9 lines in 10 noise

The panel worked (the `container` label from platform Journey 376 is why it now has
lines at all) but showed almost only `GET /health`, `GET /metrics` and the
scheduler's 15-second `poll-due` — every one a routine 200. The query now excludes
exactly those successes (`!~ "GET /(health|metrics) HTTP/1.1. 200"` and
`!~ "scheduler/poll-due.* 200 OK"`); a 503 on `/health`, a 500 on a poll, the
scheduler's `/complete` events and everything else still show, and the panel's
description says how to see everything (Explore → Loki). Held by
`test_audit_trail_logs_panel_hides_routine_successes_but_never_failures`, which
reads the patterns out of the dashboard and applies them to **the operator's real
log lines** as fixtures (4 routine lines hidden, 7 that matter shown); four
mutations each fail it (filter removed; health/metrics hidden at any status; the
poll filter dropping its status check; a filter broad enough to hide `/complete`).

### Fixed: `alert-bridge` showed on a dashboard titled edge · core · compute

The Service variable was `label_values(http_requests_total, job)`. Since Journey 8
the bridge exports the same metric, so the raw-metric panels (status class, busiest
handlers) included it while the recording-rule panels did not. The variable is now
restricted to the same three-service regex the recording rules use, and
`test_app_services_variable_lists_exactly_the_services_the_recording_rules_cover`
takes that regex **from `recording_rules.yml`** so the two cannot diverge. (The
bridge's own 4xx — visible in the screenshot — is your earlier deliberate wrong-token
test returning 401; a signal worth an alert if it ever appears unprompted, not built.)

### Investigated, not a bug: "core.audit_log — No data"

The panel's SQL was checked against migration 008's real columns (`ts`,
`tenant_id`, `resource_ref`, `subject_token`, …) and is correct; the role
`pingdin_grafana_ro` has `SELECT` on the table and the table has no row-level
security. `audit_log` rows are written only for **audited actions** (dashboard,
chat, admin panels, signal-profile approvals — `core_service/audit.py`), so a
window with none legitimately shows "No data". Not provable from here whether that
is the case, so runbook Part 5 step 6 now gives a two-step proof: the same
`SELECT count(*) … max(ts)` as the database owner and, in Grafana Explore, as the
read-only role — same numbers means the role is fine and the window is quiet
(open `/dashboard` as a tenant owner and a row appears); Grafana lower or zero
while the owner sees rows means the grants did not take (re-run
`provision_grafana_ro_role.sh`). **The result of that comparison is still outstanding.**

### Fixed: I had repeated a claim Journey 3 already corrected

Journey 3 established that `BYPASSRLS` was never needed (those tables have no RLS)
and the provisioning script sets `NOBYPASSRLS`. Runbook Part 5 step 6 still called
the dashboard "the direct test of the `BYPASSRLS` role fix", and the README
paragraph I rewrote in Journey 8 said "RLS-bypassing-but-narrowly-scoped" — copied
from older text without cross-checking the script. A wrong "the BYPASSRLS fix didn't
take" diagnosis sends the operator after a fault that cannot exist. Both corrected;
`test_no_live_doc_claims_the_grafana_role_bypasses_rls` scans the live docs (this
file's history is exempt) and fails on either phrase, and two mutations confirm it.

### Files changed

Modified: `grafana/provisioning/dashboards/json/platform-audit-trail.json` (v2),
`grafana/provisioning/dashboards/json/pingdin-app-services.json` (v3),
`DEPLOYMENT_RUNBOOK.md` (Part 5 step 6), `README.md`, `ROUTINE_CHECKS.md` (Grafana
glance), `tests/test_config_consistency.py` (23 → 26), `BUILD_LOG.md`. No compose,
rule, script or application change; nothing in `pingdin_platform`.

### Verification (sandbox — no Loki or Grafana here)

`test_config_consistency.py` 26/26, `test_alert_bridge_formatting.py` 8/8,
`test_pin_image_digests.py` 5/5, `test_find_stale_containers.py` 10/10; eight
mutations each fail the intended test; every YAML/JSON re-parsed. **Not verified
here:** that Loki and Grafana accept the new panel query (it is a standard chain of
line filters and the regexes are applied to real lines in Python's engine, not
Loki's RE2), or how Grafana renders the restricted variable.

### Deployment

Dashboards are file-provisioned and re-read every 30 s; no restart needed:
```
scp -r pingdin_observability/ duthink@142.93.209.14:/opt/
```
(If a dashboard has not changed after a minute and a hard refresh, run
`docker compose up -d --force-recreate pingdin-obs-grafana`.)

### What to check

- Grafana → *Audit Trail*: the logs panel now shows real activity — scheduler
  `complete` events, webhook traffic, errors — not a wall of `/health`. Induce one
  failure to see it: nothing you need do; a 503 would appear if it happened.
- *App Services*: "Requests by status class" and "Busiest handlers" list only
  edge/core/compute; the Service dropdown offers exactly those three.
- The audit-table proof: run the count as the owner (command in runbook Part 5 step
  6) and the same `SELECT` in Explore on the `Pingdin-DB (Managed tenants,
  read-only)` datasource, and report both numbers.
- Explore → Loki: choose `container` = `pingdin-edge` → Run query → lines with
  `[trace_id=…]`; click one → the trace opens in Tempo.


## Journey 15 — The outside view: a blackbox exporter probes the public site and its certificates from this droplet

The operator's go-ahead for the item recommended since Journey 12. Also answers a
usability question (below). Built and tested here; **not yet run on a real server**.

### The gap it closes

Every other check in this repo looks at the main droplet from *inside*: its exporters,
relayed over Tailscale by Alloy. None can see Caddy, DNS or a TLS certificate failing
while edge and n8n are individually healthy — customers unable to reach you with every
internal panel green, and (until now) no page. The probe runs from *this* droplet, a
different machine and network path, and does what a customer's phone does: resolve the
name, complete the TLS handshake, get a 2xx.

### What was built

- `blackbox/blackbox.yml` — one module, `http_2xx`: GET, follow redirects,
  `fail_if_not_ssl`, TLS **verified** (never `insecure_skip_verify` — an expired or
  mismatched certificate is exactly what this is for), IPv4 preferred with fallback,
  10 s timeout.
- `pingdin-obs-blackbox` in `docker-compose.yml` — `prom/blackbox-exporter:latest`
  (floating, like its siblings; `pin_image_digests.py` freezes it), no published port,
  no Docker healthcheck (Journey 9's reasoning): Prometheus scrapes it.
- `prometheus.yml`: job **`obs-blackbox`** (the exporter's own `/metrics`) and job
  **`blackbox-public`** (`/probe`, every **60 s** — these are real requests to
  production, so not the global 15 s; scrape timeout 15 s, above the prober's 10 s).
  Targets, chosen by reading `pingdin_platform`'s `caddy/Caddyfile`:
  `https://app.pingd.in/health` (Caddy allowlist → Edge: dashboards, Growth consent
  links) and `https://gateway.pingd.in/healthz/readiness` (Caddy → n8n, which forwards
  everything, so readiness — including its database — is public: the WhatsApp/Telegram
  intake). Adding a URL is one line under `targets:`.
- `alert_rules.yml`, group `pingdin-public`: **`PublicEndpointDown`** (`probe_success ==
  0`, 3 min, critical; the message's `instance` is the URL), **`PublicCertExpiringSoon`**
  (< 14 days, 30 min, warning — Caddy renews ~30 days out, so this means renewal has been
  failing ~two weeks; `$value` is the seconds left, and a `> 0` guard stops a probe that
  never reached TLS reading as "expired in 1970"), **`BlackboxExporterDown`** (the prober
  itself; without it a dead exporter leaves `probe_success` absent and nothing fires).
- **Deliberately not suppressed by `MainDropletHostDown`.** The first instinct — add it
  to the inhibit list to avoid a second message when the host is down — would defeat the
  point: `MainDropletHostDown` can fire because only the Alloy *relay* died, and if Caddy
  also failed at the same moment the outside outage would be hidden. If the host really is
  down you get two messages. A test forbids adding it as an inhibit target.
- App Services dashboard (v4), bottom row: **public site — outside probe** (UP/DOWN per
  URL), **TLS certificate — days left** (red < 7, orange < 14), **probe response time**.
- Docs: `ROUTINE_CHECKS.md` (automated table; `up` list now 17 series; `probe_success` and
  certificate-days rows in the after-deploy table; three alert-response rows; the
  "Automation" section rewritten — the probe moved from "worth adding" to "built", with how
  to watch another URL and an optional drill), `DEPLOYMENT_RUNBOOK.md` Part 5 step 8,
  `README.md`. Platform side: a contract test so the two public routes cannot be removed by accident (see Journey 16 below; the platform's own
  Journey 383 is an unrelated change).
- Also answers "where do I click for Explore?": runbook Part 5 step 6 now spells it out —
  left menu **Explore** → the datasource picker at the top → `Pingdin-DB (Managed
  tenants, read-only)` → **Builder → Code** → paste the `SELECT` → **Run query**; or
  *Connections → Data sources → Pingdin-DB… → Explore*.

### Defects caught while building (by the tests, before shipping)

1. **My compose edit put the new service in the wrong block.** The file has a top-level
   `networks:` between `services:` and `volumes:`; inserting "before `volumes:`" landed
   the service *under `networks:`* — valid YAML, invalid Compose, and the deploy would
   have failed. `test_outside_probe_is_wired_end_to_end` surfaced it as a `KeyError`; I
   moved the service and added `test_compose_top_level_blocks_only_contain_what_belongs_in_them`,
   which flags any service-shaped entry under `networks:`/`volumes:` and any service with
   neither `image` nor `build` (a mutation that reproduces the mistake now fails both).
2. **A weak test of mine from Journey 12.** `test_every_alert_is_documented_in_the_routine_checks_alert_table`
   only checked that an alert's name appeared *anywhere* in the doc. The new section
   mentions the alerts in prose, so a missing table row would have passed — my own
   mutation matrix found it ("NOT CAUGHT"). It now reads the first cell of every row of the
   alert-response table (and flags rows for alerts that don't exist). Its first version
   used `[A-Za-z]+` and failed on `N8NDown` — the same digit bug as the platform test in
   Journey 381; fixed to `[A-Za-z0-9]+`.

### Tests

`tests/test_config_consistency.py` 26 → **32**: the probe is wired end to end (module
exists in `blackbox.yml`; relabel target = the container's name and `:9115`; the exporter
scrape hits the same container; the compose service mounts the file its `--config.file`
names; prober timeout < scrape timeout < interval; every target is `https://`; TLS
verified, `fail_if_not_ssl`); the alerts read the probe job, the cert alert keeps its `>
0` guard, and none is an inhibit target; the dashboard reads the three `probe_*` metrics;
**every job named in any rule or panel is a job that exists** (a misspelt job silently
never fires — the relayed names are an explicit list); the drill's `sed` commands in the
doc are *executed* against a copy of the real `prometheus.yml` (valid YAML with the extra
target, and the revert restores the file byte for byte); and the compose-structure guard.
20 mutations across the new and tightened tests each fail the intended test — wrong
module, timeouts out of order, wrong port, an `http://` target, TLS verification off,
`fail_if_not_ssl` off, the alert made an inhibit target, the `> 0` guard removed, job-name
typos in two alerts, panels removed, drill indentation wrong in the doc, stale rule
count, a missing table row (three variants), an unmounted config, and the misplaced
service.

### Not verified here (no Docker, Prometheus, Alertmanager or network)

That `prom/blackbox-exporter` starts with this config; that Prometheus accepts the
`/probe` job (it is the standard relabelling from the exporter's own documentation); that
this droplet can reach the public URLs; and the actual probe values. `promtool`/`amtool`
were not run. The commands below are the check.

### Files changed

New: `blackbox/blackbox.yml`. Modified: `docker-compose.yml`, `prometheus/prometheus.yml`,
`prometheus/alert_rules.yml` (16 → 19 alerts; 23 rules with the recordings),
`grafana/provisioning/dashboards/json/pingdin-app-services.json` (v4),
`tests/test_config_consistency.py`, `ROUTINE_CHECKS.md`, `DEPLOYMENT_RUNBOOK.md`,
`README.md`, `BUILD_LOG.md`.

### Deployment

**First**, from any machine, both URLs must already answer 200 — if not, fix that before
deploying or the probe pages at once:
`curl -sSI https://app.pingd.in/health | head -1` and
`curl -sSI https://gateway.pingd.in/healthz/readiness | head -1`.
```bash
scp -r pingdin_observability/ duthink@142.93.209.14:/opt/
cd /opt/pingdin_observability
docker compose up -d --dry-run 2>&1 | grep -iE "create|recreat"   # expect: only pingdin-obs-blackbox Created
docker compose up -d                                              # pulls prom/blackbox-exporter, starts it
docker compose up -d --force-recreate pingdin-obs-prometheus      # new jobs + alert rules are read at start only
docker compose ps
```
Rollback if the exporter will not start: `docker logs pingdin-obs-blackbox` shows the
config error; `docker compose stop pingdin-obs-blackbox` leaves only
`BlackboxExporterDown` firing while you fix it.

### What to check

- `docker compose ps` → **9** services `Up`; `find_stale_containers.sh` → `9 container(s)
  checked, 0 stale.`
- After ~2 minutes, runbook Part 5 step 8's three queries: `probe_success` `1` for **both**
  URLs; `probe_http_status_code` `200`; certificate days about 30–90.
- `up` → **17** lines, all `1` (adds `blackbox-public` ×2 and `obs-blackbox`).
- `/api/v1/rules` → **23 rules**, none `"health":"err"`; alerts query → only `Watchdog`.
- Grafana → *App Services*, bottom row: both URLs UP, days left, response time.
- Optional drill (in `ROUTINE_CHECKS.md`): add `https://app.pingd.in/drill-404`, recreate
  Prometheus, wait ~5 min → one 🔴 `PublicEndpointDown` naming that URL; revert → ✅.
- `docker run --rm -v "$PWD":/w -w /w python:3.12-slim sh -c "pip install -q pyyaml && for t in tests/test_*.py; do python3 \$t; done"` → 32 / 8 / 5 / 10 passed.


## Journey 16 — Re-applying Journey 15 onto the current repo, plus the daily check for this droplet

Context: the repo uploaded on 1 Oct 2026 was Journey 14 plus one alert the operator added
(`PingdinMetricsMiddlewareBypassed`, from `pingdin_platform` Journey 383's incident); Journey 15's outside probe had not been
taken in. Verified by diffing the upload against the last delivered tarball: the only operator-side difference in the
config files was that one alert, and in the docs its alert-table row and counts. Journey 15 was therefore re-applied on top,
not rewritten.

- **Merge:** Journey 15 as delivered (`blackbox/`, the `pingdin-obs-blackbox` service, `blackbox-public` / `obs-blackbox`
  scrape jobs, `PublicEndpointDown` / `PublicCertExpiringSoon` / `BlackboxExporterDown`, the App Services outside-view row,
  its docs and tests) **plus the operator's `PingdinMetricsMiddlewareBypassed` alert, inserted verbatim after
  `PingdinHighRequestLatency` where it sits in their file**, its alert-table row copied verbatim. Alerts: 17 + 3 = **20**;
  rules with the 4 recordings: **24** (the `/api/v1/rules` check in the docs now says 24; the Sovereign doc's alert count is 20).
- **The platform-side contract test IS included** (in `pingdin_platform`, Journey 394): `tests/test_observability_probe_contract.py`
  fails where the Caddyfile is edited if `app.pingd.in/health` leaves the allowlist or the gateway gains a gate that would
  404 `gateway.pingd.in/healthz/readiness`, and compares the probe's targets with this repo's when both are side by side.
- **`scripts/ops_daily_check.sh`** (new): stale containers, compose drift, unhealthy containers, **Prometheus rule health**
  (a rule in an error state silently stops evaluating). Silent when healthy; on a problem it posts to this droplet's alert
  bridge (`/owner-alert`, source `pingdin-observability`). Optional `OPS_CHECK_PING_URL`. Exit `0/1/2`. Reads `.env` with
  `grep`, never `source`. Cron line and expectations in `ROUTINE_CHECKS.md`. `tests/test_ops_daily_check.py` (+12): the real
  script under bash with only `docker`/`curl` stubbed; 16 mutations each fail a test (including: `.env` executed, the token
  printed, a payload built by string concatenation that breaks on a quote, a problem still pinging the external check).
- **Tests:** `test_config_consistency.py` 26 → 32 (Journey 15), `test_ops_daily_check.py` 12, all four existing files pass:
  32 / 12 / 8 / 5 / 10. Not verified: Prometheus / Alertmanager / blackbox-exporter acceptance of the new files (no
  `promtool`/`amtool`/Docker here); the deploy checks are the proof.

### Deployment

```
scp -r pingdin_observability/ duthink@142.93.209.14:/opt/        # or the same compressed-stream sync you use for the platform
cd /opt/pingdin_observability
docker compose up -d --dry-run 2>&1 | grep -iE "create|recreat"   # expect only pingdin-obs-blackbox created
docker compose up -d                                              # pulls prom/blackbox-exporter, starts it
docker compose up -d --force-recreate pingdin-obs-prometheus      # new jobs and rules are read at start only
crontab -e                                                        # add the daily-check line from ROUTINE_CHECKS.md
```

### What to check

`docker compose ps` → 9 services `Up`; `probe_success{job="blackbox-public"}` → `1` for both URLs after ~2 minutes;
`/api/v1/rules` → **24 rules**, none `"health":"err"` (your `PingdinMetricsMiddlewareBypassed` included); the four test files
in the throwaway container (`ROUTINE_CHECKS.md` gives the command) → 32 / 12 / 8 / 5 / 10 passed;
`bash scripts/ops_daily_check.sh` → `OK — all daily checks passed`. Both public URLs must answer 200 BEFORE you deploy
(`curl -sSI https://app.pingd.in/health | head -1`, `curl -sSI https://gateway.pingd.in/healthz/readiness | head -1`) or the
probe pages at once.


## Journey 17 — Live confirmation of Journeys 15/16, and two corrections of mine found by running them

### Confirmed live (3 Oct 2026)

`probe_success` → `1` for both `https://app.pingd.in/health` and `https://gateway.pingd.in/healthz/readiness`; certificate days
left **78.3** and **65.9** (inside the expected 30–90); `/api/v1/rules` → **24 rules**; alerts → only `Watchdog firing`;
`docker compose ps` → 9 services; `find_stale_containers.sh` → 9 checked, 0 stale; the four test files in the throwaway
container → 8 / 32 / 10 / 12 / 5 passed; daily-check cron installed and `ops_daily_check.sh` → `OK — all daily checks passed`.
The platform's daily check also fired its first real finding (see `pingdin_platform` Journey 395).

### Corrected

1. **My documented pre-deploy check was wrong.** I told the operator to run `curl -sSI <url>` (a HEAD request) and expect
   `HTTP/2 200`; `app.pingd.in/health` answered **405** because Edge's `/health` is GET-only. The probe itself uses GET and
   reported healthy, so this was a bad check, not a bad endpoint. The `ROUTINE_CHECKS.md` alert-response row and the
   `DEPLOYMENT_RUNBOOK.md` step now use `curl -sS -o /dev/null -w '%{http_code}\n' <url>`, and
   `test_probe_checks_in_the_live_docs_use_get_not_head` forbids `curl -sSI` in the live docs. (Journey 15/16 above still show
   the old command in their text; history is not edited.)
2. **The "5xx error ratio" panel's empty axis ran 0 to 10000%** (`percentunit`, `min: 0`, no max, all-zero data). It now has
   `axisSoftMax: 0.05` — the alert threshold — so an error-free chart is readable; held by
   `test_error_ratio_panel_has_a_bounded_axis_when_there_are_no_errors`. Both new tests mutation-checked.

### The screenshot

The App Services tab shown on 3 Oct still had `alert-bridge` in "Requests by status class" and "Busiest handlers" and no
outside-view row: that is the dashboard as it was **before Journey 14**, i.e. a long-open tab (auto-refresh updates data, not
the dashboard definition). A hard refresh (Ctrl+Shift+R) loads the provisioned version; if `alert-bridge` or a missing
bottom row persists after that, provisioning is the thing to check.

### Verification / deployment

`test_config_consistency.py` 32 → 34; the other four files unchanged. Dashboards reload on their own (about 30 s), no restart:
`scp -r pingdin_observability/ duthink@142.93.209.14:/opt/`, then hard-refresh the dashboard.


## Journey 18 — The deployed dashboards never appeared because Grafana's bind mount was stale; a daily check now catches it (my first diagnosis, `allowUiUpdates`, was wrong)

Evidence (3 Oct 2026): after a hard refresh the App Services dashboard still had `alert-bridge` in two panels and no outside-probe
row, although Journeys 14 and 15 had been synced. First hypothesis (mine, in an earlier draft of this entry): the provider's
`allowUiUpdates: true` lets Grafana quietly ignore changed files. The operator then ran the log check and it showed the real cause:

```
logger=provisioning.dashboard … level=error msg="Failed to read content of symlinked path"
  path=/etc/grafana/provisioning/dashboards/json error="lstat /etc/grafana/provisioning/dashboards: no such file or directory"
msg="failed to search for dashboards" error="stat /etc/grafana/provisioning/dashboards/json: no such file or directory"
```
every 30 s. **Inside the container the provisioning directory did not exist.** The most consistent explanation: `grafana/provisioning` is a
bind-mounted *directory*; something replaced it on the host (deleted and re-created, rather than overwritten in place) after the
container started, so the container kept the old, deleted directory. Grafana then applied no dashboard change from that moment, and
showed whatever was already in its database. **How the host directory was replaced is not established** — the documented sync
(`scp -r`) overwrites in place and would not do it; the operator was asked how obs had been synced before. Recreating the container
(done 3 Oct, `--force-recreate pingdin-obs-grafana`) re-mounts the current directory.

Also found: the operator pasted the whole command block, including the laptop-side `scp`, on the server (`scp: stat local
"pingdin_observability/": No such file or directory`) — so no sync happened in that session; only the recreate did.

### Changes

- **Daily check, check 5 (`scripts/ops_daily_check.sh`):** the Grafana container must list as many provisioned dashboard files
  as the repo has; otherwise one problem, with the stale-mount explanation and the `--force-recreate` fix. A container that cannot
  be exec'd counts as zero (reported, not passed). `tests/test_ops_daily_check.py` 12 → 14; 4 mutations each fail a test.
- **`allowUiUpdates: false`** kept as hardening (the repo is the source of truth; a UI save can no longer freeze a dashboard) with an
  honest comment saying it was *not* this incident's cause; `test_dashboard_files_always_win_over_the_grafana_database` pins it.
- **Runbook Part 5 step 9** now documents the failure signature (`no such file or directory` in Grafana's log), the cause, the fix, and
  that dashboards are edited in the repo; `ROUTINE_CHECKS.md` lists the new check. Journey 17's "dashboards reload on their own" holds
  only while the mount is healthy.

### Verification / deployment / what to check

`test_config_consistency.py` 35; `test_ops_daily_check.py` 14; the other three unchanged — in the throwaway container
(alphabetical) 8 / 35 / 10 / 14 / 5 passed. **Not verified here:** the cause of the directory replacement, and Grafana's behaviour — the checks below are the proof.
Sync **from the laptop**, then on the observability server:
```
scp -r pingdin_observability/ duthink@142.93.209.14:/opt/          # LAPTOP
cd /opt/pingdin_observability && docker compose up -d --force-recreate pingdin-obs-grafana     # SERVER (provider config is read at startup)
docker logs --since 2m pingdin-obs-grafana 2>&1 | grep -iE "provision|no such file" | tail    # no "no such file" lines
bash scripts/ops_daily_check.sh                                                               # OK — all daily checks passed
```
After a hard refresh: no `alert-bridge` series or `/owner-alert` row; the error-ratio axis tops out at 5%; a bottom row shows both public
URLs UP, certificate days left and probe response time.


## Journey 19 — The sync was the cause after all: every sync leaves bind-mounted services stale, so syncing and recreating are now one scripted flow

### What the evidence settled — the cause is the manual sync procedure

Journey 18's first diagnosis (`allowUiUpdates`) was wrong; the second (a stale bind mount) was right; and a third guess in an earlier draft of this entry
(that `scp -r` itself replaces directories) was also wrong. The operator's shell history on the observability server settles it: **eleven** runs of
`sudo rm -rf .env.example BUILD_LOG.md … alertmanager/ grafana/ loki/ … prometheus/ scripts/ tempo/ tests/ …` (later with `blackbox/`), each followed by a fresh copy.
Deleting `grafana/provisioning` and re-creating it gives it a new inode; the running Grafana keeps the old, deleted one, logs `no such file or directory`
every 30 s and applies nothing — until it is re-created. Timeline, all consistent: sync 07:50 → errors 07:52 → recreate 07:54 → clean; then another
delete-and-copy at 10:57 (`stat` shows the repo root and `grafana/` touched 10:57; the container's `/etc/grafana/provisioning` inode 291606 vs the host's 291607)
→ the daily check's new check 5 reported "0 files vs 4" at 10:58 with no sync in between that I had known about. (The same re-sync of an *older* folder
is also why `scripts/deploy.sh` was missing when first run.)

**Same cause, other repo:** the version locks `deploy_guard.sh` wrote on 3 Oct 06:12 vanished before the next sync's pull (see `pingdin_platform` Journey 398) —
consistent with the same delete-then-copy habit applied to the platform's service directories (not yet confirmed from that server's history).

### Changes

- **`scripts/sync_to_server.sh`** (laptop; mirrors the platform's): one compressed stream, never deletes, never sends `.env`, caches, `.git` or
  `ops-check.log`; target in `scripts/deploy_target.conf`; `--dry-run`; a failed push is an error; it ends by printing the next step.
- **`scripts/deploy.sh`** (server): 1) `docker compose config -q` — nothing is touched if the file is invalid; 2) `up -d --build`; 3) **re-create every
  service that bind-mounts files from this repo, the list read from `docker compose config --format json`** (never hard-coded; system mounts such
  as `/var/run` are ignored; it refuses to guess if the list is empty); 4) wait for health checks, report unhealthy or stale containers. Exit 0/1.
- **`tests/test_deploy_scripts.py`** (+15): real bash, only `docker`/`ssh`/`sleep` stubbed; 15 mutations each fail a test (validation skipped, a failed
  build not stopping, a hard-coded list, system mounts counted, an empty list not refused, a failed recreate ignored, no health wait, unhealthy/stale
  ignored, `.env` or caches pushed, a failed push reported as success, the wrong extract directory, no next step, a dry run that connects).
- **Certificate panel** now reads "78 days" (unit `suffix: days`); Grafana's `d` unit auto-scaled it to "11 weeks" under a "days left" title.
  `test_certificate_panel_is_shown_in_days_not_an_auto_scaled_unit` (+1). `test_config_consistency.py` 36.
- Docs: `ROUTINE_CHECKS.md` "Deploying a change — two commands"; runbook section of the same name.

### Verification / deployment / what to check

Sandbox: scripts under bash with `docker`/`ssh` stubbed. **Not verified:** `docker compose config --format json` output on this host's Compose
version (the script needs it and says "could not read the compose configuration" if it is unsupported), and real ssh. First use — on the laptop:
`bash pingdin_observability/scripts/sync_to_server.sh --dry-run`, then without the flag; on the server: `cd /opt/pingdin_observability && bash scripts/deploy.sh`
→ the three sections, a service list that includes `pingdin-obs-prometheus pingdin-obs-grafana pingdin-obs-alertmanager pingdin-obs-loki pingdin-obs-tempo pingdin-obs-blackbox`,
and `DEPLOY OK`. Then `bash scripts/ops_daily_check.sh` → `OK`. This first sync still has to be the old way if `sync_to_server.sh` is not yet on the
laptop copy of the repo — it ships with this very tarball, so extract it, then use it.


## Journey 20 — "Should the manual rm -rf be automated?" Yes: a safe mirror and `--deploy`; plus two bugs the first `deploy.sh` run exposed

Operator: the habit is `sudo rm -rf <every repo directory>` then `scp` from the laptop, on both servers (the exact commands were pasted). That habit is the cause
found in Journey 19 (Grafana's directory mount orphaned; on the platform, the server-generated version locks deleted). It exists for a good reason — files
removed from the repo must not linger — so the fix is to automate *that*, safely, not just to forbid it.

### 1. The mirror — `scripts/prune_extras.py`, run by `sync_to_server.sh` after every push

Removes **files** that are not in the manifest (the list of files just pushed), then directories left empty that the repo no longer has. It **never replaces a
directory** (inodes kept — tested), never touches `.env`, `.venv/`, `.fingerprints/`, `backups/`, `*.log`, caches, `.git`, version locks (`*constraints.txt`)
or the manifest, never follows or removes symlinks, rejects an unsafe or tiny manifest, and **refuses (exit 3, deletes nothing) if more than `--max` (40)
files would go** — the signature of syncing a partial/wrong folder, the one new risk automation adds. A refused mirror is a warning; the sync itself stands.
`tests/test_prune_extras.py` (+12, real directory trees); 13 mutations each fail a test. Found by its own test: a chain of emptied directories
(`gone/sub/` then `gone/`) only lost the innermost one — fixed by marking a directory removable when everything inside it is itself marked.
### 2. `sync_to_server.sh --deploy` — one command from the laptop

push → mirror → `scripts/deploy.sh` on the server (its exit code is the command's). `--no-prune`, `--prune-max N`, `--dry-run` shows every step including the mirror
and the deploy without connecting. A failed push never mirrors or deploys. `tests/test_deploy_scripts.py` 15 → 20; 9 mutations on the sync script, each caught.

### 3. Two bugs in the first real `deploy.sh` run (Compose v5.5.0; the `config --format json` it needs works)

- **A false page.** `ops_daily_check.sh` flagged "Prometheus rule(s) not healthy: AlertBridgeDown Watchdog" seconds after Prometheus restarted: a rule's health is
  `unknown` until its first evaluation, and the check counted anything not `ok`. It sent the 4:47 PM Telegram message. Now only `err` counts; a test pins both
  directions (`unknown` is fine; `err` is reported even next to an `unknown`).
- **`deploy.sh` exited 1: `STALE pingdin-alert-bridge`.** `up -d --build` rebuilds the locally-built bridge image on every deploy and BuildKit's provenance
  attestation changes the image ID even when every layer is cached, so the running container always looks stale afterwards. `deploy.sh` now also re-creates every
  service that has a `build:` section (list still derived from the compose file); tests cover it (a built service is recreated; an image-only service is not).

### Verification / deployment / what to check

`test_config_consistency.py` 36; `test_ops_daily_check.py` 15; `test_deploy_scripts.py` 20; `test_prune_extras.py` 12; unchanged: 8 / 10 / 5. Sandbox: scripts under bash with
`docker`/`ssh` stubbed. **Not verified:** the first real `--deploy` and the mirror against the real server. Suggested first runs, on the laptop, from the extracted tarball:
`bash pingdin_observability/scripts/sync_to_server.sh --dry-run --deploy`, then without `--dry-run`. Expect `pushed in Ns`, `prune: nothing to remove — the server matches the repo`
(or a short list of files that left the repo), then `DEPLOY OK` — without the STALE line — and a silent daily check. Do NOT run the `sudo rm -rf` line any more.



## Journey 21 — Per-tenant capacity dashboard and a per-container memory alert (pairs with pingdin_platform Journey 466)

The platform now records a daily usage row per tenant (`core.tenant_usage`: Spaces bytes/objects, Edge rows, messages, failures, LLM requests/tokens) and sets a
`mem_limit` on every container. Two additions here, nothing duplicated from the platform: 
- **Dashboard `Tenant Capacity & Usage`** (`pingdin-tenant-capacity`): seven panels reading `core.tenant_usage` joined to `core.tenant` through the existing read-only
  datasource; a tenant-slug variable narrows it. Quotas live in the platform (`config.capacity`), so the dashboard shows usage and trend, and the ops page shows the limit.
- **`ContainerNearMemoryLimit`** (warning): a `pingdin-*` container above 90% of its own limit for 10 min (limit 0 = unlimited is excluded). Row added to ROUTINE_CHECKS; the
  rule count there is now 25.
- `provision_grafana_ro_role.sh` also grants `SELECT` on `core.tenant_usage`.

### Deploy (after the platform's Journey 466 is deployed and its Core migration applied)

1. Main droplet: `bash provision_grafana_ro_role.sh` (idempotent) → ends with the grants listed, including `core.tenant_usage`.
2. Laptop: `bash pingdin_observability/scripts/sync_to_server.sh --deploy` → `DEPLOY OK`.
3. Obs droplet: `curl -sS http://$TS:9090/api/v1/rules | python3 -c "import sys,json;print(sum(len(g['rules']) for g in json.load(sys.stdin)['data']['groups']),'rules')"` → `25 rules`.
4. Grafana → *Tenant Capacity & Usage* shows rows after the platform's first usage sweep (up to 6 h, or trigger it per the platform runbook).

Tests: `test_config_consistency.py` 36/36 and the other obs suites unchanged and green. **Not verified:** the dashboard against live data; the alert expression against real cAdvisor labels (check `name` carries the `pingdin-` prefix in Prometheus → Graph).


## Journey 22 — The latency alert fired on a handful of slow test messages; it now needs real traffic and a longer hold (pairs with pingdin_platform Journey 483)

Direct question (14 Oct 2026): `PingdinHighRequestLatency` fired and resolved about twice an hour, for `pingdin-compute` and then `pingdin-edge` ("what to do about it?").

**Diagnosis from the live Prometheus (Explore).** The platform's `p95` series for edge sat at 10–13s from 12:28 to 13:15 and compute at a flat 10s, then edge fell back to about 2s. The only handler with requests over 1s was `/v1/messages/inbound` on edge, at about 0.004 requests a second (one slow message every four minutes), i.e. the AI-backed read, mask and publish of a few test messages. With so few requests the p95 is not a real percentile: `histogram_quantile` interpolates inside the next bucket (10s, then 30s), so one slow request moves the line by a whole bucket. The alert was behaving as written; the rule was wrong for low traffic. Not a platform fault.

**Change.** `PingdinHighRequestLatency` is now `job:http_request_duration_highr_seconds:p95_5m > 20 and on (job) job:http_requests:rate5m > 0.1` for 20m: a p95 above 20s, only while more than 6 requests a minute arrive, held for 20 minutes. Both names are recorded series that already existed (Journey 8), so no query copy was added and the dashboard still plots exactly what the alert reads. `PingdinHighErrorRate` is untouched and remains the "something is broken" alert. The dashboard panel description and the two ROUTINE_CHECKS rows say the new numbers. Rule count stays 25.

**Tests.** `test_latency_alert_reads_a_recorded_series_built_on_the_high_resolution_histogram` became `..._reads_recorded_series_and_one_is_built_on_the_high_resolution_histogram` (the alert now reads two recorded names, so the old "every name is built on the highr bucket" assertion no longer fit; it now requires every name defined, none built on the low-resolution histogram, at least one on the high-resolution one). New `test_latency_alert_needs_real_traffic_and_a_sustained_breach_not_a_few_slow_requests` fails on the old rule (no traffic floor, 10m hold, 3s threshold). Suite: 107 passed.

**Honest limits.** A real slowdown at low traffic (under 6 requests a minute) no longer alerts on latency; the error-rate alert and the outside probes still cover outages. Messages that take 10s or more to confirm are an experience issue to be tuned in the platform, not hidden by the alert; the 20s threshold is a judgement from one afternoon's data, so revisit it once real tenants send traffic.

### Deployment — Journey 22
1. Laptop: `bash pingdin_observability/scripts/sync_to_server.sh --deploy` → `DEPLOY OK`; the deploy re-creates the services that mount rule files, so Prometheus reads the new rule.
2. Obs droplet: `curl -sS http://$TS:9090/api/v1/rules | python3 -c "import sys,json;print(sum(len(g['rules']) for g in json.load(sys.stdin)['data']['groups']),'rules')"` → `25 rules`, none with `"health":"err"`.
3. A 💚 heartbeat arrives within about a minute of Prometheus being recreated.

### What to check — Journey 22
Prometheus → Alerts: `PingdinHighRequestLatency` is inactive; its expression shows `> 20 and on (job) job:http_requests:rate5m > 0.1` and `for: 20m`. Send a few test messages: no latency alert. **Not verified:** firing under real load.


## Journey 23 — n8n is gone from the stack; its alert, probe target and panel go with it (pairs with pingdin_platform Journey 484)

WhatsApp and Telegram now deliver straight to Edge on `app.pingd.in`, and `pingdin-n8n` and `gateway.pingd.in` no longer exist. Left alone, this repo would have paged on both (`N8NDown` on a missing job, `PublicEndpointDown` on a host with no site).

**Removed.** The `N8NDown` rule (and its name in the `MainDropletHostDown` suppression list); the `https://gateway.pingd.in/healthz/readiness` target of `blackbox-public` (the `app.pingd.in/health` probe now covers the intake as well, because that is where Meta and Telegram call); the "n8n up" stat panel on *Host Resources*; `n8n` from the audit-trail log filter; `n8n` from the relayed-jobs test list. Rule count is now **24**; the `up` check is **15** lines; `probe_success` is one line. ROUTINE_CHECKS, DEPLOYMENT_RUNBOOK and the fire-drill test (two targets, not three) are updated to match.

**Not added.** No panel or alert for the new delivery / runs data: it is in Postgres, shown at `/ops/runs`, and failed deliveries already reach you through the platform's own alert channel (`alert_owner`), so a Grafana copy would add a query path and a second place to look for no new signal. Say so if you want a panel on `edge.outbound_log` delivery status anyway; it would reuse the existing read-only datasource at no extra load.

**Docs sweep.** README, SOVEREIGN_OBSERVABILITY, DEPLOYMENT_RUNBOOK (the trace check now starts at `pingdin-edge`), `tempo.yaml` and `docker-compose.yml` comments, and the image-pin script note no longer describe n8n as part of the stack. Older BUILD_LOG entries stay as history.

### Deployment — Journey 23
Do this **after** the platform's Journey 484 cutover is confirmed (otherwise the old n8n alert and probe are still the right ones).
1. Laptop: `bash pingdin_observability/scripts/sync_to_server.sh --deploy` → `DEPLOY OK` (recreates the services that mount rule and config files).
2. Obs droplet: `curl -sS http://$TS:9090/api/v1/rules | python3 -c "import sys,json;print(sum(len(g['rules']) for g in json.load(sys.stdin)['data']['groups']),'rules')"` → `24 rules`, none with `"health":"err"`.
3. The main droplet's Alloy stops sending the `n8n` job once the platform is redeployed; the old series fade from Prometheus on their own.

### What to check — Journey 23
`probe_success` shows one line (`https://app.pingd.in/health`) with value `1`; Prometheus → Alerts has no `N8NDown`; *Host Resources* has no "n8n up" tile; the 💚 heartbeat arrives. Tests: 107 passed.

## Journey 24 — verified live after the platform cutover; room check noted (14 Oct 2026; docs only)
Journey 23 was deployed on 14 Oct 2026: `PublicEndpointDown` for `gateway.pingd.in` resolved and nothing else paged. The platform's cutover (Journeys 484 to 488) then removed n8n for good (container, volumes, image, database, role) and its DNS record. Findings recorded here so the two repos agree: the main droplet has room (about 2.2 GB of 3.8 GB available, no swapping, disk 23%, load 1.3 on 2 cores); Edge, Alloy and Core sit at 55 to 65% of their container limits, so `ContainerNearMemoryLimit` (90% for 10 minutes) is the right early warning. No rule, probe or dashboard changed. The `.env` name `OBSERVABILITY_GATEWAY_TS_IP` is historical (it is this droplet's Tailscale IP, not a gateway) and stays, because renaming it would break every existing server. ROUTINE_CHECKS weekly step 3 now points at the platform's room check.

