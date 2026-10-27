# Routine checks — what to look at, how often, and what "good" looks like

Covers the **observability droplet** (`pingdin-observability`, Tailscale
`100.92.158.102`) and the monitoring stack as a whole, including what to do when
each alert arrives. The **main droplet's** routine (deploys, migrations,
backups, restore drill) is in `pingdin_platform`'s `docs/ROUTINE_CHECKS.md`.

The idea: **alerts tell you when something breaks. This routine covers what
alerts cannot** — things that drift silently, things that expire, and whether
alerting itself still works. Everything here was checked against the real
servers on 29 Sep 2026 unless it says otherwise.

Once per shell (every `curl` below uses it; `-sS` so errors are visible, unlike
`-s`, which prints nothing on failure — an unset `TS` looks like a silent success):

```bash
export TS=100.92.158.102
cd /opt/pingdin_observability
```

## What is already automated (you do not do these)

| Signal | Mechanism | Reaches you as |
|---|---|---|
| A service, the main host, Postgres, Redis, Loki, Tempo or the alert bridge is down | Prometheus alert rules → Alertmanager → `alert_bridge` | 🔴 Telegram |
| CPU > 90% (10 min), memory > 90% (10 min), disk < 15% free (15 min), 5xx > 5% (5 min), p95 latency > 20 s (20 min, real traffic only) | same | 🔴 Telegram |
| Alerting itself broken (Prometheus, Alertmanager or the bridge) | `Watchdog` always-on rule, once every 24 h; `AlertBridgeDown` | 💚 heartbeat — **its absence is the signal** |
| A platform-detected failure (dashboard widget error, dead-lettered event…) | `alert_owner()` — two independent routes | ⚠️ Telegram, **two messages** (one direct, one `[pingdin-managed]` via the bridge) |
| The public site — or its TLS certificate — failing: DNS, Caddy, Edge behind `app.pingd.in` (which is also where WhatsApp and Telegram deliver since platform Journey 484) | **Outside probe** from *this* droplet over the public internet, once a minute (`blackbox-public`): `PublicEndpointDown`, `PublicCertExpiringSoon` | 🔴 / ⚠️ Telegram — **even when every internal check is green** |
| One root cause taking down several services | `inhibit_rules` | **one** 🔴 `MainDropletHostDown`, not five (the outside-probe alert is deliberately *not* suppressed) |
| Nightly Postgres backup 02:00, restore drill 03:30 (main droplet cron, **if scheduled** — the platform doc's weekly step confirms it) | cron + log files | **logs only — no alert.** Checked weekly (platform doc) |

**Not automated — this is what the routine is for:** stale/drifted containers,
backup and drill outcomes, OS updates and credential rotation (see the last section
for what is still worth building).

**What does *not* reach Telegram** — so do not read silence as "all clear" for these:
an exception that is only logged, a single 5xx (needs > 5% for 5 min), one tenant
being slow, backups and the restore drill, stale containers, the public site or its
certificate failing, and **anything on a Sovereign client's server**. Tenants and
agents are not separate in the metrics — you see three services. Detail, and the
open decision on Sovereign alerts, in `SOVEREIGN_OBSERVABILITY.md`.

## Daily — 30 seconds, no commands

- The 💚 **Alerting heartbeat** arrived today. Yes → Prometheus, Alertmanager,
  the bridge and Telegram all work. **No heartbeat for ~26 h → "Heartbeat
  missing" below.**
- No unexplained 🔴. (A 🔴 followed by its ✅ is a resolved incident; read it.)

## Deploying a change — two commands (BUILD_LOG Journey 19)

```bash
bash pingdin_observability/scripts/sync_to_server.sh        # on your LAPTOP: one compressed stream, never touches .env
cd /opt/pingdin_observability && bash scripts/deploy.sh     # on the SERVER (ssh in): validate, build, re-create, check
```
`deploy.sh` re-creates every service that bind-mounts files from this repo (Prometheus, Alertmanager, Grafana, Loki, Tempo,
the blackbox exporter) — **a sync replaces files (a single-file mount keeps the old copy) and a delete-and-copy replaces whole directories (a directory mount keeps the deleted one)**: on 3 Oct 2026 Grafana kept looking at a deleted directory, logging
`no such file or directory` every 30 s and applying none of three deployed dashboard updates until it was re-created. The list is
read from `docker-compose.yml` itself, so a new service that mounts config is covered without editing the script. Data volumes are
kept; each service is back in seconds. Expect `DEPLOY OK`; anything else names what to look at. Then the checks below.

**One command does the whole job — and never `rm -rf` anything on the server first** (BUILD_LOG Journeys 19/20):

```bash
bash pingdin_observability/scripts/sync_to_server.sh --deploy     # laptop: push → mirror → deploy.sh on the server
```
**The habit it replaces** was `sudo rm -rf … grafana/ prometheus/ scripts/ …` before each copy (eleven times in the shell history): deleting and re-creating
`grafana/provisioning` gives it a new inode while the running container keeps the old, deleted one — Grafana then logs `no such file or directory` and applies
nothing. It existed so files you had removed from the repo would not linger. The sync's **mirror step** (`scripts/prune_extras.py`) does that safely: it removes
only **files** that are no longer in the repo, never replaces a directory, never touches `.env`, logs or other server-owned files, and **refuses (deletes
nothing) if it would remove more than 40 files** — what syncing a partial or wrong folder looks like; read the list, then use `--prune-max N` only if intended.
`--no-prune` skips it. If a sync reports a permission error, fix ownership once with `sudo chown -R duthink:duthink /opt/pingdin_observability`.

## After every deploy to this droplet (or any `docker compose up -d`)

```bash
docker compose ps
bash scripts/find_stale_containers.sh
docker compose up -d --dry-run 2>&1 | grep -i recreat
curl -sSG http://$TS:9090/api/v1/query --data-urlencode 'query=up' | python3 -c "import sys,json;[print(r['metric'].get('job'),r['metric'].get('instance'),r['value'][1]) for r in sorted(json.load(sys.stdin)['data']['result'],key=lambda r:r['metric'].get('job',''))]"
curl -sS http://$TS:9090/api/v1/alerts | python3 -c "import sys,json;[print(a['labels']['alertname'],a['state']) for a in json.load(sys.stdin)['data']['alerts']]"
curl -sSG http://$TS:9090/api/v1/query --data-urlencode 'query=probe_success{job="blackbox-public"}' | python3 -c "import sys,json;[print(r['metric']['instance'],r['value'][1]) for r in json.load(sys.stdin)['data']['result']]"
curl -sSG http://$TS:9090/api/v1/query --data-urlencode 'query=(probe_ssl_earliest_cert_expiry{job="blackbox-public"} - time()) / 86400' | python3 -c "import sys,json;[print(r['metric']['instance'],round(float(r['value'][1]),1),'days') for r in json.load(sys.stdin)['data']['result']]"
```

| Command | Expect | If not |
|---|---|---|
| `docker compose ps` | 9 services `Up`; Prometheus, Alertmanager, Grafana, `pingdin-alert-bridge`, cAdvisor `(healthy)`. Loki, Tempo, node-exporter and the blackbox exporter plain `Up` (no Docker healthcheck **by design** — Prometheus watches them) | `unhealthy` → `docker inspect --format '{{json .State.Health}}' <container>` |
| `find_stale_containers.sh` | `9 container(s) checked, 0 stale.` | `STALE` → run the **printed** command: `docker compose up -d --build --force-recreate <service>`. **Plain `docker compose up -d <service>` does not fix it** (it prints `Running` and leaves it stale — seen live) |
| `up -d --dry-run … \| grep -i recreat` | **no output** | A line = that container's definition differs from what is running (you just changed compose, **or** it drifted — four exporters had, unnoticed for weeks). `docker compose up -d` applies it; it **restarts** that service |
| `up` query | **15** lines, all `1`: `blackbox-public` (**one** line) and `obs-blackbox`, plus `alert-bridge`, `cadvisor` + `node` (main droplet's, instances `pingdin-cadvisor:8080` / `pingdin-node-exporter:9100`), `obs-cadvisor`, `obs-loki`, `obs-node`, `obs-tempo`, `pingdin-compute`, `-core`, `-edge`, `postgres`, `prometheus`, `redis` | a `0` or a missing line = that scrape is down → see the alert table; **two** `node` or `cadvisor` lines for more than 5 min = a job-name collision (should be impossible — tested) |
| alerts query | **only** `Watchdog firing` | anything else → alert table |
| `probe_success` query | **one** line — `https://app.pingd.in/health` — `1` | a `0` = that URL fails from outside right now: alert table, `PublicEndpointDown`. No lines at all = the prober isn't running: `BlackboxExporterDown` |
| certificate-days query | both URLs **30–90** days (Caddy renews about 30 days before expiry) | under 14 → `PublicCertExpiringSoon`; see the alert table |

After **recreating Prometheus, Alertmanager or the bridge**: a 💚 heartbeat
arrives within about a minute. After **editing a rule or alert file**:
`docker compose up -d --force-recreate pingdin-obs-prometheus` (mounted rule
files are read at start only), then
`curl -sS http://$TS:9090/api/v1/rules | python3 -c "import sys,json;print(sum(len(g['rules']) for g in json.load(sys.stdin)['data']['groups']),'rules')"`
→ `27 rules`, and none with `"health":"err"`.

Run the tests only if `tests/` changed (PyYAML is not on the droplet, so in a
throwaway container):

```bash
docker run --rm -v "$PWD":/w -w /w python:3.12-slim sh -c "pip install -q pyyaml && for t in tests/test_*.py; do python3 \$t; done"
```

Expect every file to end `N passed, 0 failed` (a pip "running as root" warning is harmless).

## Weekly — 10 minutes

1. The **stale** and **`--dry-run`** checks above (drift happens between deploys).
2. **Alerts**: only `Watchdog` firing or pending (alerts query above).
3. **Storage growth** — you want weeks of headroom, not to meet `LowDiskSpace`:
   ```bash
   df -h /
   docker system df -v | grep -E "pingdin-obs-(prometheus|loki|tempo)-data"
   ```
   Note the three volume sizes each week. Retention is Prometheus 30 d, Loki 30 d,
   Tempo 14 d, so each **grows, then flattens** once its window is full. If one
   is still climbing steeply and would not fit the disk at full retention, shorten
   that retention in its config **now** (`prometheus` flag
   `--storage.tsdb.retention.time` in `docker-compose.yml`; `retention_period` in
   `loki/loki-config.yaml`; `block_retention` in `tempo/tempo.yaml`), don't wait
   for the alert.
   **Main droplet room** (the platform server, not this one): once a month, and after anything is added or removed, run the read-only
   room check in `pingdin_platform` `docs/RUNBOOK.md` 3t (`free -h`, `df -h /`, `docker system df`, `docker stats --no-stream`). Healthy as
   measured on 14 Oct 2026 after n8n left: about 2.2 GB of 3.8 GB available, no swapping, disk 23% used, load 1.3 on 2 cores. The
   *Host & Container Resources* dashboard shows the same numbers over time; `ContainerNearMemoryLimit` fires at 90% of a container's limit.
4. **Tailscale console** (`login.tailscale.com/admin/machines`): both machines
   `Connected`, nothing you don't recognise, and **key expiry disabled** on both
   servers (machine menu → *Disable key expiry*). By default a machine key
   expires after a fixed period; an expired key drops the droplet off the tailnet
   and telemetry stops.
5. **Grafana glance**, 2 minutes (SSH tunnel `ssh -L 3000:localhost:3000
   duthink@142.93.209.14` → `http://localhost:3000`, user `duthink`, or your
   `tailscale serve` URL): *App Services* — three UP tiles (edge, core, compute),
   error ratio `0`, latency flat, "Handlers returning 5xx" empty, and the outside-view
   row at the bottom: both public URLs UP, certificate days left 30 or more; *Host & Container
   Resources* — no line pinned near its ceiling; *Audit Trail* — the logs panel has
   real lines (routine 200s are hidden), and the audit table shows rows **only if**
   someone took an audited action in the window — "No data" alone is not a fault
   (runbook Part 5 step 6 tells quiet from broken).

## Monthly — 30 minutes

1. **OS updates** on this droplet: `sudo apt-get update && apt list --upgradable`,
   apply in a quiet hour, reboot if the kernel changed. After a reboot all 9
   containers return by themselves (`restart: unless-stopped`): confirm
   `docker compose ps`, then the heartbeat. *(29 Sep 2026: this droplet ran kernel
   `6.8.0-138`, the main one `-139` — one update behind.)*
2. **Move an image on purpose** — the only time a floating tag should change:
   `docker compose pull <service>` → `docker compose up -d --force-recreate
   <service>` → the after-deploy checks → `python3 scripts/pin_image_digests.py`
   and paste the digest **if** you have pinned that service. Pinning itself
   recreates the container once (same image, definition changed): do it in a quiet
   hour and preview with `docker compose up -d --dry-run`.
3. **Credentials** — rotate on suspicion of exposure at once, otherwise about every
   6 months: `OWNER_ALERT_WEBHOOK_TOKEN` (same value in **both** `.env` files;
   procedure in `pingdin_platform` `docs/ROUTINE_CHECKS.md`), the Grafana admin
   password, the Telegram bot token (BotFather). Check secrets with
   `bash scripts/show_container_env.sh …` on the main droplet — never by piping
   plain `printenv` into `grep`, which prints the secret.

## Quarterly — fire drill (proves the alerting chain and the suppression rule)

On the **main** droplet: `docker compose stop pingdin-alloy-edge`, wait ~5–9 min,
then `docker compose start pingdin-alloy-edge`. Expect **exactly one** 🔴
`MainDropletHostDown`, then one ✅ `RESOLVED` — and **no** 🔴 for the edge, core,
compute "Down" alerts (they are suppressed while the host alert fires).
Passed on 29 Sep 2026: 🔴 at 3:54 PM, ✅ at 3:59 PM, nothing else. Metrics, logs
and traces pause during the drill; tenant traffic is unaffected.

## When an alert arrives — first move

Run on the droplet named in the last column.

| Alert (severity) | Means | First check | Where |
|---|---|---|---|
| `MainDropletHostDown` (critical) | The main droplet's node-exporter metrics stopped arriving — the host, its Alloy relay, or Tailscale | `ssh` to it; `docker compose ps`; `docker logs --tail 30 pingdin-alloy-edge`; Tailscale console | main |
| `PingdinEdgeDown` / `PingdinCoreDown` / `PingdinComputeDown` (critical) | That service's `/metrics` scrape is gone or `0` | `docker compose ps <svc>`; `docker logs --tail 50 <svc>` | main |
| `PostgresDown` (critical) | `pg_up == 0` — the exporter can't reach Postgres | `docker compose ps pingdin-db pingdin-postgres-exporter`; `docker logs --tail 30 pingdin-db` | main |
| `RedisDown` (critical) | `redis_up == 0` | `docker compose ps pingdin-redis pingdin-redis-exporter` | main |
| `LokiDown` (critical) | Loki's own `/metrics` scrape is gone — logs are not being stored | `docker compose ps pingdin-obs-loki`; `docker logs --tail 50 pingdin-obs-loki` | obs |
| `TempoDown` (critical) | Tempo's `/metrics` scrape is gone — traces are not being stored | `docker compose ps pingdin-obs-tempo`; `docker logs --tail 50 pingdin-obs-tempo` | obs |
| `AlertBridgeDown` (critical) | The bridge isn't reporting. **This message may not reach you** (it travels through the bridge) — you will more likely notice the missing heartbeat | `docker compose ps pingdin-alert-bridge`; `docker logs --tail 50 pingdin-alert-bridge` | obs |
| `DiskAlmostFull` (critical) | A filesystem below 5% free for 5 min. **Also rings the phone.** A full disk stops the database | `df -h /`; `sudo du -xh --max-depth=2 / \| sort -h \| tail -15`; `docker system df`; on main also `du -sh backups/*` and `/tmp` | the alerting host |
| `DiskFillingFast` (critical) | The root disk will be full within about 4 hours at the last hour's rate (and is under 60% free). **Also rings the phone** | Same as above: find what grows (`du -sh backups/wal_archive`, `/tmp/pytest-*`, `docker system df`) before it is full | the alerting host |
| `SwapInUse` (warning) | Over 800 MB in swap AND pages moving to and from it (over 50 a second) for 15 min: memory is really short. Swap that is merely full of idle pages does not fire it | `free -m`; `vmstat 5 3` (`si`/`so` non-zero); `docker stats --no-stream`; resize the droplet or lower a container's limit. If swap is full but `vmstat` shows no movement and memory is available: `sudo swapoff -a && sudo swapon -a` | the alerting host |
| `HighCPUUsage` (warning) | CPU > 90% for 10 min | `top`/`docker stats --no-stream`; Grafana *Host & Container Resources* for which container | the alerting host |
| `HighMemoryUsage` (warning) | Memory > 90% for 10 min | `free -h`; `docker stats --no-stream` | the alerting host |
| `ContainerNearMemoryLimit` (warning) | One container is above 90% of its own `mem_limit` for 10 min — it will be OOM-killed and restarted | `docker stats --no-stream`; Grafana *Host & Container Resources*; raise it via `<SVC>_MEM_LIMIT` in `.env` (the platform repo's suggest-limits helper proposes values), then `docker compose up -d <svc>` | main |
| `LowDiskSpace` (warning) | A filesystem below 15% free for 15 min | `df -h /`; `docker system df`; on main also `du -sh backups/` (14-day local retention) | the alerting host |
| `PingdinHighErrorRate` (warning) | A service answers > 5% of requests with 5xx for 5 min | Grafana *App Services* → "Handlers returning 5xx"; then Explore → Loki `{container="<svc>"} \|= "ERROR"` | main logs |
| `PingdinHighRequestLatency` (warning) | p95 request time > 20 s for 20 min, and more than 6 requests a minute (Journey 22: a few slow AI-backed messages during testing used to fire it; the p95 of a handful of requests jumps a whole histogram bucket) | Grafana *App Services* p95 panel; Explore → Tempo for a slow trace | main |
| `PingdinMetricsMiddlewareBypassed` (warning) | Requests are being served without metrics (the metrics middleware fails before reaching the app) for 5 min | Users are fine but error/latency alerts are blind. `docker logs pingdin-edge --tail 200 \| grep "failed before reaching the application"` for the exception; almost always a FastAPI/`prometheus-fastapi-instrumentator` version mismatch after a rebuild — compare `docker exec pingdin-edge pip list \| grep -i -E "fastapi\|starlette\|prometheus"` with `edge_service/constraints.txt` | main |
| `PublicEndpointDown` (critical) | A public URL has failed the **outside** probe for 3 min. The `instance` in the message says which: `app.pingd.in/health` = Caddy → Edge (dashboards, Growth consent links, **and the WhatsApp/Telegram intake**). Can fire while every internal check is green | From any machine: `curl -sS -o /dev/null -w '%{http_code}\n' <that url>` (a GET: `app.pingd.in/health` answers a HEAD request 405); then `docker compose ps pingdin-caddy pingdin-edge` and `docker logs --tail 50 pingdin-caddy`; DNS: `dig +short app.pingd.in` (should return the main droplet's public IP) | main |
| `PublicCertExpiringSoon` (warning) | A public certificate has < 14 days left. Caddy renews ~30 days out, so renewal has been failing about two weeks | `docker logs --tail 100 pingdin-caddy 2>&1 \| grep -iE "certificate\|acme\|error"`; renewal needs ports 80 and 443 reachable from the internet and DNS pointing at this droplet | main |
| `BlackboxExporterDown` (critical) | The prober itself isn't reporting — **the public site is currently not being watched from outside** | `docker compose ps pingdin-obs-blackbox`; `docker logs --tail 50 pingdin-obs-blackbox`; `docker compose config -q` (config error?) | obs |
| `Watchdog` (heartbeat, not a fault) | 💚 daily. Its **absence** is the alert | see next | obs |

**Heartbeat missing (> ~26 h).** Alerting may be broken and nothing can tell you
so. On this droplet: `docker compose ps` (Prometheus, Alertmanager, bridge up?);
`curl -sS http://$TS:9090/api/v1/alerts` (is `Watchdog` `firing`?);
`docker compose logs --tail 30 pingdin-obs-alertmanager pingdin-alert-bridge`;
`curl -s -o /dev/null -w "%{http_code}\n" http://$TS:8090/health` → `200`. If the
droplet itself is unreachable, that is the one failure nothing here can announce
— see the next section.

## The pager: critical alerts also ring the phone (BUILD_LOG Journey 25)

Telegram is a chat message and is silent at 3 a.m. An alert whose severity is `critical` (a service down, the public site down, `DiskAlmostFull`, `DiskFillingFast`, the bridge or prober itself down) now also goes to Pushover at emergency priority: it rings every minute until you press Acknowledge, for up to 30 minutes. Warnings, resolved messages and the daily heartbeat never page. This droplet is the one that must page when the main droplet is down, because a dead droplet cannot page for itself.

**Switch it on** (once, on this droplet): add two lines to `/opt/pingdin_observability/.env` with the SAME Pushover application token and user (or delivery-group) key as `OWNER_ALERT_PUSHOVER_TOKEN` and `OWNER_ALERT_PUSHOVER_USER` in the main droplet's `.env`: `ALERT_BRIDGE_PUSHOVER_TOKEN=…` and `ALERT_BRIDGE_PUSHOVER_USER=…`. Then `bash scripts/deploy.sh` (it rebuilds and re-creates the bridge). Never print those values.

**Test it** (after every change to those two lines, and once a quarter with the fire drill): on this droplet

```bash
docker exec pingdin-alert-bridge python3 -c "import httpx; print(httpx.post('http://localhost:8090/alertmanager', json={'alerts':[{'status':'firing','labels':{'alertname':'PagerTest','severity':'critical'},'annotations':{'summary':'Test page from the observability droplet. Please ignore.'}}]}).text)"
```
Expect `{"delivered":1,"received":1}`, a Telegram message "[FIRING] PagerTest (critical)", and your phone ringing: press Acknowledge. No ring but the Telegram message arrived: the two Pushover lines are missing or wrong (`docker logs --tail 20 pingdin-alert-bridge` shows `Pager (Pushover) send failed`). If Telegram is down, Alertmanager retries the batch and the page can ring again on each retry.

## Automation and systems: what is built, and what is still worth adding

**Built (Journey 15): the outside view.** A blackbox exporter on *this* droplet
(a different machine and network path from the main droplet) makes a real HTTPS GET
to `https://app.pingd.in/health`
once a minute, verifying DNS, the TLS handshake and a 2xx, and records the
certificate's expiry. Nothing else here can see Caddy, DNS or a certificate fail
while edge is itself healthy. `PublicEndpointDown` (3 minutes),
`PublicCertExpiringSoon` (< 14 days) and `BlackboxExporterDown` cover it; the App
Services dashboard has a matching row. It is deliberately **not** suppressed when
`MainDropletHostDown` fires — its value is being independent of the main droplet's
own relay, so a real customer-facing outage is never hidden behind a relay problem.

*Watching another public URL:* add it to `targets:` under `blackbox-public` in
`prometheus/prometheus.yml` (any `https://` URL that answers 2xx to a GET), then
`docker compose up -d --force-recreate pingdin-obs-prometheus`. Probe health
endpoints, never anything that does work or needs auth.

*Proving it fires* (optional and safe: it adds one URL that Caddy answers with 404, a
request a minute for the few minutes it is in place, and changes nothing on the main
droplet):

```bash
sed -i 's#^          - https://app.pingd.in/health#&\n          - https://app.pingd.in/drill-404#' prometheus/prometheus.yml
docker compose up -d --force-recreate pingdin-obs-prometheus
# wait ~5 minutes: one 🔴 PublicEndpointDown for https://app.pingd.in/drill-404, nothing else
sed -i '/drill-404/d' prometheus/prometheus.yml
docker compose up -d --force-recreate pingdin-obs-prometheus
# within a few minutes: ✅ RESOLVED
```

**Built (Journey 16): the daily check** — see the section above.

**Still worth adding, in order:**

1. **Someone told when this droplet itself dies.** Everything here — `Watchdog` and
   the outside probe included — runs on it. A free external dead-man's-switch service
   (it messages you when *its* expected ping stops) closes that. A decision about an
   outside service, so yours to make.
2. **Before the first Sovereign client:** the alert relay through Core and a
   liveness signal from `core.service_credential.last_used_at` — see
   `SOVEREIGN_OBSERVABILITY.md`.

## The daily check — automated (BUILD_LOG Journey 16)

`scripts/ops_daily_check.sh` runs this droplet's machine-checkable routine once a day and
**posts to the alert bridge only when something is wrong** (a `⚠️ [pingdin-observability] Daily
ops check: N problem(s)` Telegram message, a line per problem). It checks: stale containers,
compose drift (`up -d --dry-run`), unhealthy containers, **Prometheus rule health** — a
rule or recording rule in an error state keeps Prometheus running but silently stops
evaluating it, so an alert on it just never fires — and **Grafana sees its dashboards**: if
`grafana/provisioning` was replaced on the host after the container started, Grafana keeps
looking at the deleted copy and silently applies no dashboard change (the 3 Oct 2026
incident). The fix it prints: `docker compose up -d --force-recreate pingdin-obs-grafana`.

Install once (server local time):

```bash
crontab -e     # add:
20 6 * * * cd /opt/pingdin_observability && bash scripts/ops_daily_check.sh >> ops-check.log 2>&1
```

- **Verify:** `crontab -l | grep ops_daily_check`, then run it by hand:
  `bash scripts/ops_daily_check.sh` → `OK — all daily checks passed`, exit 0.
- **Expect:** silence every day. Exit codes: `0` passed, `1` problems found and reported,
  `2` problems found but the bridge could not be reached (they are in `ops-check.log`).
- **Optional:** `OPS_CHECK_PING_URL` in `.env` — a second external check pinged only on a clean
  pass, so "the check stopped running" is noticed too. Secrets (the bridge token, the ping URL)
  are read with `grep`, never `source`d, never printed.

