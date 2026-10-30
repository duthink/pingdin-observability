# Sovereign observability — what centralizes, what can't, and why

Direct question this answers: "how does this work wrt sovereign
clients, would it be still aligned?"

## The short answer

**Operational telemetry** (metrics, logs, distributed traces — timings,
status codes, error rates, container health, span structure) is safe to
centralize from every deployment, Managed or Sovereign, because it
never contains tenant content or PII by construction. This is Phase A/B
of this build, and it extends to Sovereign clients cleanly, later.

**Business data reconstruction** ("show me exactly what happened to
subject X") cannot centralize for Sovereign clients without breaking
the entire premise of that tier — their Edge (Sovereign-A) or Edge+
Compute (Sovereign-B) runs on THEIR OWN server specifically so their
data never has to leave it. This is the `Pingdin-DB (Managed tenants,
read-only)` Grafana datasource in this repo — it is Managed-only by
design, not an oversight to widen later.

## Why this split is real, not just cautious

`pingdin_platform`'s own standing architecture rule (its own memory,
restated here since this repo depends on it): "Sovereignty-aware data
placement: proprietary tenant content → Edge; Pingdin IP or de-
identified/aggregate derived values → Core." A Sovereign client's own
Edge database is proprietary tenant content, by definition, wherever it
runs. Reaching into it from a Pingdin-operated Grafana instance —
even read-only, even for legitimate incident investigation — is the
exact thing Sovereign-B in particular exists to make structurally
impossible, not just contractually promised.

## What DOES work identically across every tier, today

- Container-level metrics (CPU/memory/disk, Postgres health) —
  pseudonymous by nature, no tenant data in a `node_cpu_seconds_total`
  metric.
- Application-level traces (shipped: `pingdin_platform`'s `shared/otel.py`,
  wired in edge, core and compute) — a span says "extraction took 340ms for tenant_id=<uuid>", never the
  extracted content itself. `tenant_id`/`subject_id` as opaque UUIDs are
  already the pseudonymous currency this whole platform runs on — the
  same identifiers already move between Edge and Core over HTTP today.
- Container logs — IF, and only if, the discipline this repo assumes
  holds: application log lines never print raw message content, PII, or
  extracted field values, only structural facts (status codes,
  durations, error types, IDs). This is worth an explicit audit before
  Sovereign log shipping is ever turned on — not assumed safe just
  because it's "just logs."

## What is covered TODAY, precisely (checked against the code, 29 Sep 2026)

**Managed.** One pooled `edge`, `core` and `compute` container serve every Managed
tenant, and Agents (Shopfloor, Payments, Growth…) are code inside Edge/Core, not
separate containers. So you see **three services**, not tenants or agents:
metrics carry `job`/`handler`/`status` only, and trace spans and the log format have
no tenant or agent field (a log line carries a `trace_id`; an individual message may
mention an id in its text). Per-tenant insight
comes from the database dashboards instead — *Tenant Operational Signals*
(de-identified, all tiers) and *Audit Trail* (Managed only) — and from
`alert_owner()` pages, whose `context` carries `tenant_id`.

**What reaches the Telegram admin bot.** (1) The 20 Prometheus alert rules about
the main droplet and this stack. (2) `alert_owner()` — roughly 30 call sites in
Edge, Core and agent/capability code (inbound-message failures, Compute failures
seen by Edge, dashboard widget errors, insights pipeline, Growth voice, DSR
fulfilment, scheduler failures…). **Compute never calls it**: a Compute failure
reaches you as an Edge/Core page plus `PingdinComputeDown` / error-rate / latency.
**What does not reach it:** an exception that is only logged; a single 5xx (the
error-rate alert needs > 5% for 5 minutes); one tenant being slow; backup and
restore-drill outcomes; stale containers; the public site or its certificate
failing while services are individually healthy; and **anything on a Sovereign
client's server**. "All issues" is therefore not true — it is every issue the code
was written to page about, plus the infrastructure rules.

**Sovereign, today.** No Sovereign tenant exists, and `core.pingd.in` — the public
endpoint a client's Edge would call — is **not active** in the Caddyfile. Once one
does: Pingdin-hosted **Core** (and **Compute** for Sovereign-A) are covered like
Managed, and a client Edge's requests show up there as ordinary unlabelled traffic.
The **client-hosted Edge, database and Caddy are not monitored at all** — no
scrape, no logs, no host metrics, no `up` — so if a client's Edge dies, nothing
tells you. (Its tracing also points at `pingdin-alloy-edge:4317`, which does not
resolve on a client's server: background export attempts fail and are logged;
requests are unaffected.)

### Sovereign alerts through `OWNER_ALERT_WEBHOOK_URL`: the mechanism exists, the path doesn't

The Sovereign templates forward `OWNER_ALERT_WEBHOOK_URL` / `_TOKEN` / `_SOURCE`, and
a Sovereign Edge's `alert_owner()` would POST there. Setting it to
`http://100.92.158.102:8090/owner-alert` does **not** work by itself:

1. **Unreachable.** That is a Tailscale address and the bridge listens only on it.
   A client's server cannot reach it unless it has joined your tailnet.
2. **The client never gets the variables.** `scripts/onboard_sovereign_client.sh`
   writes no `OWNER_ALERT_*` into the client's `.env`, so today a Sovereign Edge has
   no alert channel at all — failures are only logged on the client's server.
3. **One shared secret.** The bridge accepts a single bearer token. Anyone with
   access to a client's server can read it and send fake alerts to your Telegram,
   and rotating it breaks every client at once. `OWNER_ALERT_SOURCE` is set by the
   client, so it can be spoofed.
4. **Content.** `detail` is written by each call site (many include exception text
   and ids). Sovereign's premise is that raw content never reaches Pingdin, so the
   call sites need an audit before any Sovereign deployment sends alerts here.
5. **An unreachable URL used to stall the Edge**: `alert_owner()` is synchronous and
   the webhook routes call it on the event loop, with a flat 15 s timeout per
   channel. Bounded since `pingdin_platform` Journey 382 (connect wait 2–3 s).

**Options** (a decision is needed before the first client — not built):

| | How | For | Against |
|---|---|---|---|
| **A. Client joins the tailnet** | The Phase D plan below: a tagged device whose ACL allows only this droplet's `:8090` (and `:4317` if telemetry is wanted) | The bridge works unchanged | A client-owned machine on your private network; still one shared token |
| **B. Relay through Core** *(recommended for alerts)* | A new authenticated Core endpoint; a Sovereign Edge already holds `PINGDIN_CORE_BASE_URL` and a tenant-bound token from onboarding. Core calls `alert_owner()` itself, with the source set from the token's tenant | No new client config, no tailnet, no shared secret; per-tenant, revocable and rate-limitable; the source cannot be spoofed; rides the same allowlist + bearer layers as every other Core call | Needs building (Core endpoint, a third route in `owner_alert.py`, tests); needs `core.pingd.in` active |
| **C. Expose the bridge publicly** | Caddy + TLS in front of `:8090` | Simple | A new public surface guarded by one shared token — not recommended |

Also worth having with B, at no cost on the client's side: *is the client's Edge
alive?* `core.service_credential.last_used_at` is updated on every authenticated
Core call, so "a tenant-bound credential unused for N minutes in business hours"
is a liveness signal from what Core already knows — a Grafana panel or alert on
the read-only Postgres datasource, needing one extra column grant.

## The plan for Sovereign, when a Sovereign tenant actually exists

Not built in this pass — no Sovereign tenant exists yet, so there's
nothing real to design this against (same reasoning
`pingdin_platform`'s own BUILD_LOG uses repeatedly for not building
ahead of a confirmed consumer). When one does:

1. `deployment-templates/docker-compose.sovereign-a.yml` /
   `-b.yml` (in `pingdin_platform`) gain their own `pingdin-alloy-edge` service, config
   pointed at a Tailscale IP — either THIS central droplet (telemetry
   only, same as Managed) or, for a client with stricter requirements,
   a small local Grafana/Loki/Tempo/Prometheus stack running on their
   own server, reachable to Hobo only via Tailscale to that specific
   deployment. The second option gives Hobo full reconstruction
   ability for that client without centralizing anything — it just
   isn't one pane of glass, it's one pane per Sovereign deployment.
2. Either way: the client should know, in plain language, what
   telemetry leaves their server and what doesn't — this is a
   contractual/compliance conversation as much as a technical one, not
   something to default silently either direction.

## Table: what's reachable from where, today

| Signal | Managed tenant | Sovereign-A | Sovereign-B |
|---|---|---|---|
| Infra metrics (Postgres/CPU/mem) | Central Grafana | Not shipped yet (Phase D) | Not shipped yet (Phase D) |
| App traces (once Phase C ships) | Central Grafana | Not shipped yet (Phase D) | Not shipped yet (Phase D) |
| Container logs | Central Grafana | Not shipped yet (Phase D) | Not shipped yet (Phase D) |
| Raw business data ("reshow what happened") | Central Grafana, read-only, `pingdin_grafana_ro` | Never centrally — local only, if/when built | Never centrally — local only, if/when built |
