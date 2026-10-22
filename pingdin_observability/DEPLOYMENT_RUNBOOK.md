# DEPLOYMENT_RUNBOOK.md — the single, ordered sequence

Run in this order. Don't skip the verification step after each part —
this build's own standing rule (`pingdin_platform`'s own BUILD_LOG,
repeated here) is that a compile/logic-review pass is not the same
thing as confirming something actually works live.

## Part 1 — Observability droplet: OS + Docker + Tailscale

On the new droplet, as root/sudo:

```bash
# extract this repo here, e.g. /opt/pingdin_observability
cd /opt/pingdin_observability
bash scripts/00_provision_and_harden.sh
bash scripts/01_tailscale_setup.sh
```

Note the Tailscale IP it prints. Join your own laptop/phone to the same
tailnet once (`tailscale up`), so you can reach Grafana later.

## Part 2 — Fill in .env

```bash
cp .env.example .env   # if 01_tailscale_setup.sh didn't already do this
```

Fill in: `GRAFANA_ADMIN_PASSWORD`, `ALERT_BRIDGE_TELEGRAM_BOT_TOKEN`,
`ALERT_BRIDGE_TELEGRAM_CHAT_ID`, `OWNER_ALERT_WEBHOOK_TOKEN` (any value
you choose — this same value goes on the main droplet too, see Part 4).
Leave `PINGDIN_DB_TAILSCALE_HOST`/`PINGDIN_GRAFANA_RO_PASSWORD` blank
for now — Part 4 fills those in.

## Part 3 — Bring the stack up

```bash
docker compose up -d --build
```

**Verify before moving on:**
```bash
docker compose ps   # every service healthy/running
TS=$(grep '^TAILSCALE_IP=' .env | cut -d= -f2)
curl -s http://$TS:9090/-/healthy      # Prometheus
curl -s http://$TS:3100/ready          # Loki
```
Prometheus and Loki are bound to the Tailscale IP, not `localhost`, so a
`localhost` curl fails even when they are healthy (BUILD_LOG Journey 2,
item 5). Tempo publishes only its OTLP ports (4317/4318) to the host, so
it has no host-side health URL: `docker compose ps` showing it `Up`, then
Grafana → Connections → Data sources → Tempo → **Save & test**, is the check.

## Part 4 — Wire the main droplet in

On the **main** `pingdin_platform` droplet:

```bash
cd /opt/pingdin_platform
# copy main-droplet-companion/provision_grafana_ro_role.sh here first
bash provision_grafana_ro_role.sh
```

This prints/writes `PINGDIN_GRAFANA_RO_PASSWORD` into that droplet's own
`.env`. Copy that exact value into the observability droplet's `.env`
under the same key.

Still on the main droplet, edit `.env`:
```bash
OBSERVABILITY_GATEWAY_TS_IP=<observability droplet's Tailscale IP>
OWNER_ALERT_WEBHOOK_URL=http://<observability droplet's Tailscale IP>:8090/owner-alert
OWNER_ALERT_WEBHOOK_TOKEN=<same value as this repo's own .env>
```

Then:
```bash
docker compose up -d pingdin-alloy-edge pingdin-edge pingdin-core
```

Back on the **observability** droplet, edit `.env`:
```bash
PINGDIN_DB_TAILSCALE_HOST=<main droplet's Tailscale IP>
PINGDIN_GRAFANA_RO_PASSWORD=<the value from provision_grafana_ro_role.sh above>
```
```bash
docker compose up -d --force-recreate pingdin-obs-grafana
```

## Deploying a change after the first install

**Never `sudo rm -rf` the repo's directories on the server before copying** — it is what made Grafana's mount stale on 3 Oct 2026
(BUILD_LOG Journeys 19/20); the sync script extracts in place and mirrors deletions safely by itself. One command from your laptop —
`bash pingdin_observability/scripts/sync_to_server.sh --deploy` — or, without the flag, that and then `cd /opt/pingdin_observability && bash scripts/deploy.sh` on the server. Never a bare `scp -r` followed by hand-typed
`docker compose` lines: a sync leaves bind-mounted services (Grafana, Prometheus, Alertmanager …) looking at stale files (and a delete-and-copy leaves them looking at deleted directories),
and `deploy.sh` is what fixes that every time (see `ROUTINE_CHECKS.md`, "Deploying a change").

## Part 5 — Verify, don't assume

1. **Metrics**: open Grafana (see Part 6), the "Host & Container
   Resources" dashboard should show real data from the MAIN droplet
   within ~30s (`pingdin-alloy-edge`'s own scrape interval).
2. **Logs**: first confirm the label exists (BUILD_LOG Journey 7 /
   `pingdin_platform` Journey 376 — before that, Docker logs reached Loki
   with no `container` label at all, and the query below matched nothing):
   `curl -s http://$TS:3100/loki/api/v1/labels` must list `container`.
   Then Explore → Loki → `{container=~"pingdin-.*"}` should show live log
   lines from the main droplet's containers.
3. **Traces (pingdin-edge/-core/-compute)**: send a real WhatsApp or Telegram
   message. Explore → Tempo → search by service name `pingdin-edge` — a trace
   for that request should appear within a minute or two. Since
   `pingdin_platform` Journey 484 there is no n8n in front of Edge: Meta and
   Telegram call Edge directly, so the trace starts at `pingdin-edge`
   (`POST /v1/webhooks/whatsapp` or `/v1/webhooks/telegram-bot/<id>`) and, for
   requests that reach them, includes child spans from `pingdin-core` and
   `pingdin-compute`. Tempo's search table labels a whole trace with its ROOT
   span's service, which is now `pingdin-edge`; open the trace itself and read its
   span list to confirm Core and Compute spans are inside it.
4. **Alerts, infra AND app-level** (BUILD_LOG Journey 214 — app-level
   alerting, `pingdin-app` rule group, didn't exist before this): `docker
   compose stop pingdin-obs-node-exporter` briefly on this droplet, or
   stop `pingdin-edge` briefly on the main droplet, or trigger a real
   threshold — confirm a Telegram message arrives via `alert_bridge`'s
   `/alertmanager` endpoint either way. **Also confirm the
   `pingdin-edge`/`pingdin-core`/`pingdin-compute` metrics are actually
   arriving** before trusting `PingdinEdgeDown`/`PingdinCoreDown`/
   `PingdinComputeDown` at all. Do NOT use Prometheus → Status → Targets
   for this: those jobs are scraped by Alloy on the main droplet and
   remote-written here, so they never appear on this Prometheus's Targets
   page (only its own `prometheus`, `obs-node`, `obs-cadvisor` do). Query
   `up` instead (Graph, or
   `curl -sG http://$TS:9090/api/v1/query --data-urlencode 'query=up'`):
   expect one series per job, all `1` — `pingdin-edge`, `pingdin-core`,
   `pingdin-compute`, `node`, `cadvisor`, `postgres`, `redis` from
   the main droplet, plus `prometheus`, `obs-node`, `obs-cadvisor` here.
   Two series for one job name means a local job collides with a relayed
   one (Journey 7). `http_requests_total`/
   `http_request_duration_seconds`'s own label names (BUILD_LOG Journey
   238) are now confirmed against prometheus-fastapi-instrumentator's
   own upstream source — `PingdinHighErrorRate`/`PingdinHighRequestLatency`
   use the real defaults (`status`, not the `status_code` an earlier
   pass here mistakenly assumed). That's LIBRARY-SOURCE confirmation
   only, not live-traffic confirmation — still separately confirm
   `http_requests_total`/`http_request_duration_seconds` actually show
   real, non-empty data once traffic flows (Prometheus → Graph, query
   the metric name directly) — a correct label name and an actually-
   working scrape-to-alert path are two different things, and this
   step is where the second one gets checked for real. For the latency
   alert specifically, confirm the histogram it reads exists and reaches
   past 3s: `count by (le) (http_request_duration_highr_seconds_bucket)`
   must list buckets above `3` (the default per-handler
   `http_request_duration_seconds` histogram has only a few, low buckets,
   which is why the alert no longer reads it — Journey 7).
5. **Owner-alert channel**: from the main droplet, manually POST a test
   payload matching `capabilities/alerting/owner_alert.py`'s own shape
   to confirm the `/owner-alert` endpoint and bearer-token check work:
   ```bash
   curl -X POST http://<obs-ts-ip>:8090/owner-alert \
     -H "Authorization: Bearer <OWNER_ALERT_WEBHOOK_TOKEN>" \
     -H "Content-Type: application/json" \
     -d '{"subject":"test","detail":"runbook verification","context":{"tenant_id":"test"},"source":"runbook-test"}'
   ```
   Confirm the same Telegram chat receives it. **This only proves the
   bridge.** Until `pingdin_platform` Journey 376, `docker-compose.yml` never
   forwarded `OWNER_ALERT_WEBHOOK_URL`/`_TOKEN`/`_SOURCE` into `pingdin-edge`
   and `pingdin-core`, so a value set in `.env` (Part 4) never reached the
   containers and `alert_owner()` never called the bridge. Verify the real
   path, on the main droplet, from `/opt/pingdin_platform`:
   ```bash
   bash scripts/show_container_env.sh pingdin-edge OWNER_ALERT_WEBHOOK   # URL in full, token shown as <set> — never <EMPTY>
   docker exec pingdin-edge python3 -c "from capabilities.alerting import alert_owner; alert_owner('runbook check', 'platform webhook path', {'tenant_id':'test'})"
   ```
   Do NOT check the token by piping plain `printenv` into `grep`: that prints
   the secret into the terminal and into anything you paste (that happened live —
   BUILD_LOG Journey 10). **Expect TWO Telegram messages** if the platform's
   direct Telegram pair (`OWNER_ALERT_TELEGRAM_*`) is also set — one straight
   from the platform (no source prefix) and one through this bridge
   (`[<OWNER_ALERT_SOURCE>]`, or `[unknown-deployment]` if that is unset). That
   is the two independent channels working as designed; the bridge's is the one
   that proves this repo's path. Set `OWNER_ALERT_SOURCE` in the main droplet's
   `.env` (e.g. `pingdin-managed`) so the two are easy to tell apart.
6. **Audit trail dashboard**: open "Platform Activity & Audit Trail". Two
   panels, two expectations:
   - *Matching container logs*: has lines. Routine successes (health checks and
     metrics scrapes that answered 200, and the scheduler's 15-second poll) are
     hidden on purpose — they were about nine lines in ten; failures still show.
   - *core.audit_log* table: rows appear when someone takes an **audited action**
     (opening the dashboard, chat or admin panels, approving a signal profile).
     With no such action in the time window it legitimately says **No data**. To
     tell "quiet" from "broken", run the same count as the database owner and as
     the Grafana role, and compare:
     ```bash
     # main droplet
     docker exec pingdin-db psql -U pingdin -d pingdin -tAc "SELECT count(*) AS total, count(*) FILTER (WHERE ts > now() - interval '24 hours') AS last_24h, max(ts) AS newest FROM core.audit_log"
     ```
     then in Grafana — *where to click*: left menu → **Explore** (the compass icon);
     at the top of the page is a **datasource picker** (it shows the current
     datasource, e.g. `Loki`) → click it and choose
     **`Pingdin-DB (Managed tenants, read-only)`**; in the query editor switch
     **Builder → Code** (the toggle at the editor's top right), paste the same
     `SELECT`, and press **Run query** (top right) or Shift+Enter. (Alternative:
     *Connections → Data sources → Pingdin-DB…* has an **Explore** button, and
     **Save & test** there confirms the connection is `OK`.) **Same numbers** → the role is fine and the
     window is simply quiet (open `/dashboard` as a tenant owner, refresh, and a row
     appears). **Grafana lower or zero while the owner sees rows** → the role's
     grants did not take: re-run
     `main-droplet-companion/provision_grafana_ro_role.sh` on the main droplet. The
     role has narrow `SELECT` grants and is `NOBYPASSRLS`: `core.audit_log` has no
     row-level security, so it needs no bypass (BUILD_LOG Journey 3).

7. **Alerting reliability** (BUILD_LOG Journey 8). Four things, each with a
   check:
   - *Healthchecks*: `docker compose ps` — Prometheus, Alertmanager, Grafana
     and `pingdin-alert-bridge` show `healthy` within ~1 minute (confirmed live).
     Loki, Tempo and the two exporters have NO Docker healthcheck by design
     (BUILD_LOG Journey 9: the `wget` checks wrongly reported Loki and Tempo
     `unhealthy` while serving). Their health is `up` in Prometheus:
     `curl -sG http://$TS:9090/api/v1/query --data-urlencode 'query=up{job=~"obs-.*"}'`
     → `obs-loki`, `obs-tempo`, `obs-node`, `obs-cadvisor`, all `1`, and the
     `LokiDown` / `TempoDown` alerts cover the first two.
   - *Stale containers*: `bash scripts/find_stale_containers.sh` (run from this
     directory) → every line `OK`, `0 stale`. A `STALE` line means an image was
     rebuilt or re-pulled since that container was created; fix it with
     `docker compose up -d --build --force-recreate <service>` (the script prints
     it filled in) — NOT plain `docker compose up -d <service>`, which leaves it
     stale (BUILD_LOG Journey 12).
   - *Heartbeat*: within a minute of `docker compose up -d`, Telegram receives
     "Alerting heartbeat — Prometheus, Alertmanager and this bridge are all
     working", then once every 24h. A day without it means alerting is broken
     even though nothing else is telling you so — check
     `http://$TS:9090/alerts` (`Watchdog` should be FIRING) and
     `docker compose logs pingdin-alert-bridge`.
   - *Rules loaded*: `curl -s http://$TS:9090/api/v1/rules` lists
     `pingdin-app-recording` (four `record` rules), and
     `curl -sG http://$TS:9090/api/v1/query --data-urlencode 'query=job:http_error_ratio:rate5m'`
     returns one series per service that has had traffic (blank = none yet).
   - *Suppression, optional live proof*: on the MAIN droplet
     `docker compose stop pingdin-alloy-edge`; after ~9 minutes expect ONE
     critical message (`MainDropletHostDown`) — not five — because
     `PingdinEdgeDown`/`-CoreDown`/`-ComputeDown` are suppressed
     while it fires. In Prometheus → Alerts they show FIRING; Alertmanager
     holds them back. `docker compose start pingdin-alloy-edge` afterwards.

8. **Outside probe** (BUILD_LOG Journey 15) — the one view from *outside* the main
   droplet: this droplet GETs `https://app.pingd.in/health` every minute, and reads the
   certificates' expiry. After `docker compose up -d` (pulls and starts
   `pingdin-obs-blackbox`) **and**
   `docker compose up -d --force-recreate pingdin-obs-prometheus` (the new scrape
   jobs and alert rules are read at start only), wait two minutes, then:
   ```bash
   curl -sSG http://$TS:9090/api/v1/query --data-urlencode 'query=probe_success{job="blackbox-public"}' | python3 -c "import sys,json;[print(r['metric']['instance'],r['value'][1]) for r in json.load(sys.stdin)['data']['result']]"
   curl -sSG http://$TS:9090/api/v1/query --data-urlencode 'query=probe_http_status_code{job="blackbox-public"}' | python3 -c "import sys,json;[print(r['metric']['instance'],r['value'][1]) for r in json.load(sys.stdin)['data']['result']]"
   curl -sSG http://$TS:9090/api/v1/query --data-urlencode 'query=(probe_ssl_earliest_cert_expiry{job="blackbox-public"} - time()) / 86400' | python3 -c "import sys,json;[print(r['metric']['instance'],round(float(r['value'][1]),1),'days') for r in json.load(sys.stdin)['data']['result']]"
   ```
   Expect **one** line each: `probe_success` `1`; status `200`; certificate
   days between about **30 and 90**. If a URL shows `0`: `curl -sS -o /dev/null -w '%{http_code}\n' <url>` (a GET — `-I` sends HEAD, which `app.pingd.in/health` answers 405) from the
   droplet shows why (DNS, TLS, or a non-2xx). **No lines at all** = the exporter is not
   running (`docker compose ps pingdin-obs-blackbox`, `docker logs pingdin-obs-blackbox`).
   Then Grafana → *App Services*, bottom row: both URLs UP, days left, response time.
   The optional drill that proves `PublicEndpointDown` really pages you is in
   `ROUTINE_CHECKS.md` ("Automation and systems").

9. **Dashboards are edited in this repo, not in Grafana** (BUILD_LOG Journey 18). The provider has
   `allowUiUpdates: false`: every changed file under `grafana/provisioning/dashboards/json/` is applied within
   about 30 s of landing on the server, and the UI will not let you save a provisioned dashboard. To change one,
   edit the JSON (or *Export → Copy JSON* from a UI experiment and paste it into the file), bump its `version`,
   and sync. **If a deployed dashboard does not change** after a hard refresh:
   `docker logs --since 1h pingdin-obs-grafana 2>&1 | grep -iE "provision|no such file"`. Lines like
   `lstat /etc/grafana/provisioning/dashboards: no such file or directory` mean Grafana's **bind mount is stale**
   — the host directory was replaced after the container started (anything that deletes and re-creates
   `grafana/provisioning`, instead of overwriting files in place, does it) — and it has been applying nothing.
   Fix: `docker compose up -d --force-recreate pingdin-obs-grafana` (the data volume is kept). The daily
   check (`scripts/ops_daily_check.sh`, check 5) now reports this on its own.

## Part 6 — Reach Grafana

Before Tailscale `serve` is set up: SSH tunnel from your own machine —
```bash
ssh -L 3000:localhost:3000 duthink@<observability-droplet-ip>
```
then open `http://localhost:3000`.

Permanent, no tunnel: on your laptop and phone install Tailscale and sign
in to the same tailnet; in the Tailscale admin console make sure MagicDNS
and HTTPS certificates are enabled; then on the observability droplet:
```bash
sudo tailscale serve --bg 3000
tailscale serve status        # prints the exact URL
```
Grafana is then at `https://pingdin-observability.<your-tailnet>.ts.net`
from any device on the tailnet. (The earlier `--https=3000` form of this
command listens on port 3000, so its URL needed a `:3000` this runbook did
not show; the default port needs none.) Then set `GRAFANA_ROOT_URL` in this
droplet's `.env` to that URL and
`docker compose up -d --force-recreate pingdin-obs-grafana` — Grafana builds
login redirects from it. Use either the tunnel or this URL day to day, not
both: with the root URL set to the ts.net address, the `localhost:3000`
tunnel's login redirects to the ts.net address.

## What this runbook does NOT cover

- Sovereign-tier wiring — see `SOVEREIGN_OBSERVABILITY.md`.
- Day-to-day operation after it is up — see `ROUTINE_CHECKS.md` (routine checks by
  cadence, and the first move for each alert).
