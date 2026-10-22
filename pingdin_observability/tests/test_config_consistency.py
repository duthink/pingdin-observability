"""
Run:  pytest tests/ -v          (or: python3 tests/test_config_consistency.py)
No pytest/PyYAML on the droplet? Run it in a throwaway container:
  docker run --rm -v "$PWD":/w -w /w python:3.12-slim \
    sh -c "pip install -q pyyaml pytest && pytest -v tests"

BUILD_LOG Journey 7 — this repo's first tests. Each one pins a REAL defect
found by the cross-repo sync audit (or a structural invariant that would have
caught it), and each was mutation-checked: it fails against the pre-fix file.

  1. Local scrape job names must not collide with relayed ones. This droplet's
     own exporters were named `node`/`cadvisor`, the same job names the main
     droplet's Alloy relays, so `absent(up{job="node"})` (MainDropletHostDown)
     could never be true while this droplet was alive.
  2. The latency alert must read the high-resolution histogram: the default
     http_request_duration_seconds has only a few buckets, and
     histogram_quantile cannot exceed the top finite bucket, so "p95 > 3s"
     could never fire on it.
  3. Every dashboard datasource reference resolves to a provisioned
     datasource, and dashboard uids are unique.

Journey 8 adds: one definition per shared aggregate (recording rules read by
both alerts and dashboards; a recorded name that does not exist would make an
alert silently never fire), alertmanager routing/suppression referencing rules
that exist, the alert path monitoring itself (Watchdog, AlertBridgeDown +
its scrape job), every mounted-or-referenced config file actually mounted,
`service_healthy` only pointing at services that define a healthcheck, and
alert_bridge's Dockerfile copying every module main.py imports.
"""

import glob
import json
import os
import re

_ROOT = os.path.join(os.path.dirname(__file__), "..")


def _yaml(rel):
    try:
        import yaml
    except ImportError:
        import pytest
        pytest.skip("PyYAML not available in this environment")
    with open(os.path.join(_ROOT, rel), encoding="utf-8") as f:
        return yaml.safe_load(f)


def _dashboards():
    out = {}
    for path in sorted(glob.glob(os.path.join(_ROOT, "grafana/provisioning/dashboards/json/*.json"))):
        with open(path, encoding="utf-8") as f:
            out[os.path.basename(path)] = json.load(f)
    return out


def _alert_exprs():
    data = _yaml("prometheus/alert_rules.yml")
    return {r["alert"]: str(r["expr"]) for g in data["groups"] for r in g["rules"]}


def _dashboard_prom_exprs(d):
    exprs = []
    for p in d.get("panels", []):
        for t in p.get("targets", []):
            if "expr" in t:
                exprs.append(t["expr"])
    for v in d.get("templating", {}).get("list", []):
        q = v.get("query")
        exprs.append(q.get("query", "") if isinstance(q, dict) else str(q or ""))
    return exprs


def local_scrape_job_names(prom_cfg) -> list[str]:
    return [j["job_name"] for j in prom_cfg["scrape_configs"]]


def job_selectors_matching(expr: str, job_names: list[str]) -> set[str]:
    """Job names from `job_names` that a positive job="x" / job=~"regex"
    selector in `expr` would match (negations ignored)."""
    hits = set()
    for op, val in re.findall(r'\bjob\s*(=~|=)\s*"([^"]*)"', expr):
        for name in job_names:
            if re.fullmatch(val if op == "=~" else re.escape(val), name):
                hits.add(name)
    return hits


# Alerts that select a LOCAL job on purpose (they watch this droplet's own
# service, not a relayed one). Anything else matching a local job is the
# Journey 7 collision. Adding to this dict is a decision, reviewed as one.
_INTENTIONAL_LOCAL_SELECTORS = {
    "AlertBridgeDown": {"alert-bridge"},  # watches pingdin-alert-bridge, scraped locally
    "LokiDown": {"obs-loki"},  # Journey 9: this droplet's own log store
    "TempoDown": {"obs-tempo"},  # Journey 9: this droplet's own trace store
    "PublicEndpointDown": {"blackbox-public"},  # Journey 15: the outside probe's own job
    "PublicCertExpiringSoon": {"blackbox-public"},
    "BlackboxExporterDown": {"obs-blackbox"},
}
# Same idea for dashboards: panels that deliberately read a LOCAL job.
_INTENTIONAL_LOCAL_DASHBOARD_JOBS = {
    "pingdin-app-services.json": {"blackbox-public"},  # Journey 15: the outside-view panels
}


def test_no_alert_or_dashboard_job_selector_matches_a_locally_scraped_job():
    """THE Journey 7 regression. Local jobs (prometheus.yml's own scrape_configs)
    are this droplet watching itself; every selector in an alert or dashboard
    that names a job means a RELAYED main-droplet job. If one also matches a
    local job, absent()/up==0 style alerts are masked by this droplet's own
    healthy exporter, and dashboards silently mix two machines."""
    local = local_scrape_job_names(_yaml("prometheus/prometheus.yml"))
    collisions = []
    for name, expr in _alert_exprs().items():
        for hit in job_selectors_matching(expr, local) - _INTENTIONAL_LOCAL_SELECTORS.get(name, set()):
            collisions.append(f"alert {name} selects local job {hit!r}")
    for fname, d in _dashboards().items():
        for expr in _dashboard_prom_exprs(d):
            for hit in job_selectors_matching(expr, local) - _INTENTIONAL_LOCAL_DASHBOARD_JOBS.get(fname, set()):
                collisions.append(f"dashboard {fname} selects local job {hit!r}")
    assert not collisions, "; ".join(collisions)


def test_collision_checker_can_actually_fail():
    """A check never seen failing proves nothing: the pre-fix shape (local job
    named `node` vs MainDropletHostDown's selector) must be reported."""
    expr = 'absent(up{job="node"}) or (up{job="node"} == 0)'
    assert job_selectors_matching(expr, ["prometheus", "node", "cadvisor"]) == {"node"}
    assert job_selectors_matching(expr, ["prometheus", "obs-node", "obs-cadvisor"]) == set()
    assert job_selectors_matching('up{job=~"pingdin-.*"}', ["pingdin-obs-x", "obs-node"]) == {"pingdin-obs-x"}
    assert job_selectors_matching('up{job!="node"}', ["node"]) == set()


def test_main_droplet_host_down_still_selects_the_relayed_node_job():
    """Guards the other direction: the rename must not have been 'fixed' by
    loosening the alert. It must still select exactly job="node"."""
    assert 'job="node"' in _alert_exprs()["MainDropletHostDown"]


def _recorded_names():
    data = _yaml("prometheus/recording_rules.yml")
    return {r["record"]: str(r["expr"]) for g in data["groups"] for r in g["rules"]}


_RECORDED_NAME_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*(?::[A-Za-z0-9_]+)+\b")


def recorded_names_in(expr: str) -> set[str]:
    """Names shaped level:metric:operation (a colon only ever appears in a
    recorded-series name in PromQL)."""
    return set(_RECORDED_NAME_RE.findall(expr))


def test_latency_alert_reads_recorded_series_and_one_is_built_on_the_high_resolution_histogram():
    """Journey 7's fix (highr histogram — the default one cannot express a threshold above its top bucket), now held at the single place the expression
    lives. Journey 22: the alert also reads the request-rate series (the traffic floor), so the check is: every name is defined, none is built on the
    low-resolution histogram, and at least one — the latency one — is built on the high-resolution one."""
    expr = _alert_exprs()["PingdinHighRequestLatency"]
    names = recorded_names_in(expr)
    assert names, "alert no longer reads a recorded series — a private copy of the query has crept back"
    recorded = _recorded_names()
    for n in names:
        assert n in recorded, f"alert reads {n!r}, which recording_rules.yml does not define"
        assert "http_request_duration_seconds_bucket" not in recorded[n].replace(
            "http_request_duration_highr_seconds_bucket", "")
    assert any("http_request_duration_highr_seconds_bucket" in recorded[n] for n in names)


def test_latency_alert_needs_real_traffic_and_a_sustained_breach_not_a_few_slow_requests():
    """Journey 22 (found live, 14 Oct 2026): during testing a handful of slow AI-backed messages pushed the p95 to 10-13s, and the alert fired and resolved
    about twice an hour. The p95 of a few requests jumps a whole histogram bucket, so the rule now needs a traffic floor and a longer hold."""
    expr = _alert_exprs()["PingdinHighRequestLatency"]
    assert "job:http_requests:rate5m" in expr, "no traffic floor: a few slow requests can fire this again"
    rule = next(r for g in _yaml("prometheus/alert_rules.yml")["groups"] for r in g["rules"] if r.get("alert") == "PingdinHighRequestLatency")
    hold = str(rule["for"])
    assert hold.endswith("m") and int(hold[:-1]) >= 20, f"the breach must hold at least 20 minutes, got {hold}"
    threshold = float(re.search(r"p95_5m\s*>\s*([0-9.]+)", expr).group(1))
    assert threshold >= 10, "below the 10s bucket edge a handful of slow requests fire it"


def test_error_rate_alert_reads_a_recorded_series():
    expr = _alert_exprs()["PingdinHighErrorRate"]
    assert "http_requests_total" not in expr, "alert carries its own copy of the query again"
    names = recorded_names_in(expr)
    assert names and names <= set(_recorded_names())


def test_every_recorded_name_used_anywhere_is_defined():
    """A typo in a recorded name makes an alert silently never fire (an
    undefined series is just empty) — the worst kind of alerting bug."""
    defined = set(_recorded_names())
    used = set()
    for expr in _alert_exprs().values():
        used |= recorded_names_in(expr)
    for d in _dashboards().values():
        for expr in _dashboard_prom_exprs(d):
            used |= recorded_names_in(expr)
    # recording rules may build on each other
    for expr in _recorded_names().values():
        used |= recorded_names_in(expr)
    assert used <= defined, f"referenced but not defined: {sorted(used - defined)}"


def test_dashboard_plots_exactly_the_series_the_alerts_use():
    """The point of recording rules here: the panel and the alert cannot
    disagree, because they read the same stored series."""
    dash = set()
    for expr in _dashboard_prom_exprs(_dashboards()["pingdin-app-services.json"]):
        dash |= recorded_names_in(expr)
    for alert in ("PingdinHighErrorRate", "PingdinHighRequestLatency"):
        alert_names = recorded_names_in(_alert_exprs()[alert])
        assert alert_names <= dash, f"{alert} alerts on {alert_names - dash} which no dashboard panel plots"


def test_alert_names_are_unique():
    """Caught a real defect while building Journey 8: a botched edit appended a
    second copy of half the file, and YAML parsing does not object."""
    data = _yaml("prometheus/alert_rules.yml")
    names = [r["alert"] for g in data["groups"] for r in g["rules"]]
    assert len(names) == len(set(names)), sorted(n for n in set(names) if names.count(n) > 1)
    groups = [g["name"] for g in data["groups"]]
    assert len(groups) == len(set(groups))


def test_alerting_watches_itself():
    alerts = _alert_exprs()
    assert alerts["Watchdog"].strip() == "vector(1)"
    assert "alert-bridge" in alerts["AlertBridgeDown"]
    prom = _yaml("prometheus/prometheus.yml")
    jobs = {j["job_name"]: j for j in prom["scrape_configs"]}
    assert "alert-bridge" in jobs, "AlertBridgeDown selects a job Prometheus never scrapes"
    compose = _yaml("docker-compose.yml")["services"]
    target = jobs["alert-bridge"]["static_configs"][0]["targets"][0]
    host, port = target.split(":")
    assert host == compose["pingdin-alert-bridge"]["container_name"]
    dockerfile = open(os.path.join(_ROOT, "alert_bridge", "Dockerfile"), encoding="utf-8").read()
    assert f'"--port", "{port}"' in dockerfile, "scrape target port is not the port uvicorn listens on"


def test_watchdog_has_its_own_slow_route_to_a_real_receiver():
    cfg = _yaml("alertmanager/alertmanager.yml")
    receivers = {r["name"] for r in cfg["receivers"]}
    assert cfg["route"]["receiver"] in receivers
    routes = [r for r in cfg["route"].get("routes", []) if any("Watchdog" in m for m in r["matchers"])]
    assert len(routes) == 1, "exactly one Watchdog route expected"
    assert routes[0]["receiver"] in receivers
    assert routes[0]["repeat_interval"] == "24h"


def test_inhibit_rules_reference_real_alerts_and_only_suppress_dependents():
    alerts = _alert_exprs()
    cfg = _yaml("alertmanager/alertmanager.yml")
    assert cfg["inhibit_rules"], "no inhibit_rules"
    for rule in cfg["inhibit_rules"]:
        sources = {m.split("=", 1)[1].strip().strip('"') for m in rule["source_matchers"]}
        assert sources <= set(alerts), f"inhibit source is not a real alert: {sources - set(alerts)}"
        targets = set()
        for m in rule["target_matchers"]:
            targets |= set(m.split("=~", 1)[1].strip().strip('"').split("|"))
        assert targets <= set(alerts), f"inhibit target is not a real alert: {targets - set(alerts)}"
        assert not (sources & targets), "an alert may not suppress itself"
        for t in targets:
            # only absent()-style rules are dependents of a host-down event
            assert "absent(" in alerts[t], f"{t} is not an absent()-based alert; it can fire independently of the host"


def test_every_rule_file_prometheus_loads_is_mounted_into_its_container():
    prom = _yaml("prometheus/prometheus.yml")
    volumes = " ".join(_yaml("docker-compose.yml")["services"]["pingdin-obs-prometheus"]["volumes"])
    for path in prom["rule_files"]:
        assert path in volumes, f"{path} is loaded by prometheus.yml but not mounted — Prometheus would refuse to start"
        local = path.replace("/etc/prometheus/", "prometheus/")
        assert os.path.exists(os.path.join(_ROOT, local)), local


def healthy_dependency_problems(services: dict) -> list[str]:
    problems = []
    for name, svc in services.items():
        deps = svc.get("depends_on") or {}
        if isinstance(deps, list):
            continue
        for dep, cfg in deps.items():
            if (cfg or {}).get("condition") == "service_healthy" and "healthcheck" not in services.get(dep, {}):
                problems.append(f"{name} -> {dep}")
    return problems


def test_service_healthy_only_points_at_services_that_define_a_healthcheck():
    assert healthy_dependency_problems(_yaml("docker-compose.yml")["services"]) == []
    bad = {"a": {"depends_on": {"b": {"condition": "service_healthy"}}}, "b": {}}
    assert healthy_dependency_problems(bad) == ["a -> b"]


def test_every_service_is_health_checked_or_scraped_by_prometheus():
    """Journey 9. A Docker healthcheck depends on what tools each image ships:
    Journey 8's `wget` checks reported Loki and Tempo `unhealthy` on the real
    server while both were serving. Prometheus scraping the service's own
    /metrics does not depend on the image, so a service without a healthcheck
    is only acceptable if Prometheus scrapes it (a job whose target host is its
    container_name). Built-locally services must have a healthcheck."""
    services = _yaml("docker-compose.yml")["services"]
    prom = _yaml("prometheus/prometheus.yml")
    scraped_hosts = {t.split(":")[0] for j in prom["scrape_configs"]
                     for sc in j.get("static_configs", []) for t in sc["targets"]}
    unwatched = []
    for name, sv in services.items():
        if "healthcheck" in sv:
            continue
        host = sv.get("container_name", name)
        if host not in scraped_hosts:
            unwatched.append(name)
    # the Prometheus container itself is scraped as localhost:9090 and has a healthcheck
    assert unwatched == [], f"neither a healthcheck nor a Prometheus scrape target: {unwatched}"


def test_loki_and_tempo_scrape_targets_use_the_ports_their_own_configs_listen_on():
    """The Loki/Tempo scrape jobs hard-name a port; the truth lives in
    loki-config.yaml / tempo.yaml. A mismatch makes `up` 0 forever (LokiDown
    firing falsely) or, worse, if the job were dropped, alert silence."""
    prom = _yaml("prometheus/prometheus.yml")
    targets = {j["job_name"]: j["static_configs"][0]["targets"][0] for j in prom["scrape_configs"]}
    loki_port = _yaml("loki/loki-config.yaml")["server"]["http_listen_port"]
    tempo_port = _yaml("tempo/tempo.yaml")["server"]["http_listen_port"]
    assert targets["obs-loki"] == f"pingdin-obs-loki:{loki_port}"
    assert targets["obs-tempo"] == f"pingdin-obs-tempo:{tempo_port}"
    alerts = _alert_exprs()
    assert 'job="obs-loki"' in alerts["LokiDown"] and 'job="obs-tempo"' in alerts["TempoDown"]
    services = _yaml("docker-compose.yml")["services"]
    assert services["pingdin-obs-loki"]["container_name"] == "pingdin-obs-loki"
    assert services["pingdin-obs-tempo"]["container_name"] == "pingdin-obs-tempo"


def test_grafana_never_waits_for_loki_or_tempo_to_be_healthy():
    """By design (Journey 9): Grafana must come up when Loki/Tempo are down,
    because that is when you need it to look at Prometheus and the alerts."""
    deps = _yaml("docker-compose.yml")["services"]["pingdin-obs-grafana"]["depends_on"]
    assert deps["pingdin-obs-loki"]["condition"] == "service_started"
    assert deps["pingdin-obs-tempo"]["condition"] == "service_started"


def test_live_docs_never_tell_the_operator_to_print_env_values():
    """Journey 10. A bare `printenv | grep` / `env | grep` check prints token
    values into the terminal and any chat it is pasted into — it did, live.
    Use pingdin_platform's scripts/show_container_env.sh, which masks them.
    BUILD_LOG.md is a historical record (append-only) and exempt."""
    import re
    offenders = []
    for fn in ("README.md", "DEPLOYMENT_RUNBOOK.md", "SOVEREIGN_OBSERVABILITY.md", "ROUTINE_CHECKS.md",
               os.path.join("scripts", "README.md"), os.path.join("main-droplet-companion", "README.md")):
        path = os.path.join(_ROOT, fn)
        if not os.path.exists(path):
            continue
        for n, line in enumerate(open(path, encoding="utf-8"), 1):
            if re.search(r"(printenv|\benv)\s*\|\s*grep", line):
                offenders.append(f"{fn}:{n}: {line.strip()[:80]}")
    assert offenders == [], offenders


def test_every_alert_is_documented_in_the_routine_checks_alert_table():
    """Journey 12, tightened in Journey 15. ROUTINE_CHECKS.md tells the operator what to
    do when each alert arrives; an alert added to alert_rules.yml without a row in THAT
    TABLE would page you with no documented first move. It is not enough for the name to
    appear somewhere in the doc — once the alerts are also mentioned in prose (the
    outside-probe section) a missing table row would go unnoticed — so the check reads
    the first cell of every row of the "When an alert arrives" table."""
    import re
    doc = open(os.path.join(_ROOT, "ROUTINE_CHECKS.md"), encoding="utf-8").read()
    start = doc.index("## When an alert arrives")
    end = doc.index("**Heartbeat missing", start)
    documented = set()
    for line in doc[start:end].splitlines():
        if line.startswith("| `"):
            first_cell = line.split(" | ", 1)[0]
            documented |= set(re.findall(r"`([A-Za-z0-9]+)`", first_cell))
    missing = sorted(set(_alert_exprs()) - documented)
    assert missing == [], f"alerts with no row in the alert-response table: {missing}"
    assert documented - set(_alert_exprs()) == set(), f"table rows for alerts that do not exist: {sorted(documented - set(_alert_exprs()))}"


def test_routine_checks_only_references_files_that_exist():
    """Every scripts/… and tests/… path the routine tells the operator to run
    must exist — a doc that names a deleted script fails at the worst time."""
    import re
    doc = open(os.path.join(_ROOT, "ROUTINE_CHECKS.md"), encoding="utf-8").read()
    refs = set(re.findall(r"\b((?:scripts|tests)/[A-Za-z0-9_./*-]+\.(?:sh|py))\b", doc))
    assert refs, "the scan found no script/test references — the regex is broken"
    # Scripts that live in the OTHER repo (pingdin_platform) and are run on the
    # main droplet — the doc says so at each mention. Adding to this set is a
    # deliberate, reviewed decision, not a way to silence the check.
    platform_side = {"scripts/show_container_env.sh"}
    missing = sorted(r for r in refs - platform_side if "*" not in r and not os.path.exists(os.path.join(_ROOT, r)))
    assert missing == [], missing
    assert "scripts/find_stale_containers.sh" in refs and "scripts/pin_image_digests.py" in refs


def test_routine_checks_states_the_numbers_the_config_actually_has():
    """The doc quotes counts and thresholds; each is checked against the source
    of truth so the doc cannot drift from the config it describes."""
    doc = open(os.path.join(_ROOT, "ROUTINE_CHECKS.md"), encoding="utf-8").read()
    n_alerts = len(_alert_exprs())
    n_records = len(_recorded_names())
    assert f"{n_alerts + n_records} rules" in doc, f"expected the doc to say '{n_alerts + n_records} rules'"
    prom = _yaml("prometheus/prometheus.yml")
    for j in prom["scrape_configs"]:   # every local scrape job is in the doc's `up` row
        assert f"`{j['job_name']}`" in doc, f"local scrape job {j['job_name']!r} missing from the doc's up list"
    services = _yaml("docker-compose.yml")["services"]
    assert f"{len(services)} container(s) checked" in doc
    assert "--storage.tsdb.retention.time=30d" in " ".join(services["pingdin-obs-prometheus"]["command"])
    assert _yaml("loki/loki-config.yaml")["limits_config"]["retention_period"] == "720h"


def test_audit_trail_logs_panel_hides_routine_successes_but_never_failures():
    """Journey 14. Live, the panel was ~9 lines in 10 health checks, metrics scrapes
    and scheduler polls (all 200s). The exclusion must hide exactly those and
    nothing that signals a problem. Fixtures are real lines from the operator's
    Loki (29 Sep 2026); the patterns are read out of the dashboard's own query."""
    import re
    d = _dashboards()["platform-audit-trail.json"]
    expr = [t["expr"] for p in d["panels"] if p["type"] == "logs" for t in p["targets"]][0]
    excludes = [re.compile(m) for m in re.findall(r'!~ "((?:[^"\\]|\\.)*)"', expr)]
    assert excludes, "the logs panel no longer filters routine lines"
    hidden = [
        'INFO:     127.0.0.1:33160 - "GET /health HTTP/1.1" 200 OK',
        'INFO:     172.19.0.11:43364 - "GET /metrics HTTP/1.1" 200 OK',
        'INFO     httpx [trace_id=-]: HTTP Request: POST http://pingdin-core:8200/v1/scheduler/poll-due?limit=50 "HTTP/1.1 200 OK"',
        'INFO:     172.19.0.8:39602 - "POST /v1/scheduler/poll-due?limit=50 HTTP/1.1" 200 OK',
    ]
    shown = [
        'INFO:     127.0.0.1:33160 - "GET /health HTTP/1.1" 503 Service Unavailable',
        'INFO:     172.19.0.11:43364 - "GET /metrics HTTP/1.1" 500 Internal Server Error',
        'INFO:     172.19.0.8:39602 - "POST /v1/scheduler/poll-due?limit=50 HTTP/1.1" 500 Internal Server Error',
        'INFO     httpx [trace_id=-]: HTTP Request: POST http://pingdin-core:8200/v1/scheduler/poll-due?limit=50 "HTTP/1.1 502 Bad Gateway"',
        'INFO:     172.19.0.8:38092 - "POST /v1/scheduler/0d43d656-e227-4fb4-9c5f-875546c69ead/complete HTTP/1.1" 200 OK',
        'INFO:     10.0.0.5:1234 - "POST /v1/webhooks/telegram/acme HTTP/1.1" 200 OK',
        'ERROR    capabilities.dashboard.engine [trace_id=ab12]: widget failed',
    ]
    for line in hidden:
        assert any(r.search(line) for r in excludes), f"routine line not hidden: {line}"
    for line in shown:
        assert not any(r.search(line) for r in excludes), f"a line that matters is hidden: {line}"


def _job_regex_of(expr: str) -> str:
    import re
    m = re.search(r'job=~"([^"]+)"', expr)
    assert m, expr
    return m.group(1)


def test_app_services_variable_lists_exactly_the_services_the_recording_rules_cover():
    """Journey 14. The dashboard is titled edge/core/compute and its headline panels
    read the recording rules (edge|core|compute only), but the Service variable was
    label_values(http_requests_total, job) — which also lists alert-bridge (it exports
    the same metric), so the raw-metric panels showed a fourth service the title and
    the other panels did not. One source of truth: the recording rules' job regex."""
    d = _dashboards()["pingdin-app-services.json"]
    var = [v for v in d["templating"]["list"] if v["name"] == "job"][0]
    q = var["query"]["query"]
    assert var["definition"] == q
    assert _job_regex_of(q) == _job_regex_of(_recorded_names()["job:http_requests:rate5m"])
    assert "alert-bridge" not in q


def test_no_live_doc_claims_the_grafana_role_bypasses_rls():
    """Journey 3 established BYPASSRLS was never needed (the tables have no RLS) and the
    provisioning script sets NOBYPASSRLS. The runbook and, in Journey 8, this README
    kept saying otherwise — and a wrong "the BYPASSRLS fix didn't take" diagnosis sends
    the operator hunting a fault that cannot exist. BUILD_LOG.md is historical: exempt."""
    import re
    offenders = []
    for fn in ("README.md", "DEPLOYMENT_RUNBOOK.md", "ROUTINE_CHECKS.md", "SOVEREIGN_OBSERVABILITY.md"):
        for n, line in enumerate(open(os.path.join(_ROOT, fn), encoding="utf-8"), 1):
            if re.search(r"RLS-bypassing|(?<!NO)BYPASSRLS", line):
                offenders.append(f"{fn}:{n}: {line.strip()[:80]}")
    assert offenders == [], offenders
    script = open(os.path.join(_ROOT, "main-droplet-companion", "provision_grafana_ro_role.sh"), encoding="utf-8").read()
    assert "NOBYPASSRLS" in script


def _prom():
    return _yaml("prometheus/prometheus.yml")


def _job(name):
    return [j for j in _prom()["scrape_configs"] if j["job_name"] == name][0]


def _duration_seconds(text: str) -> float:
    import re
    m = re.fullmatch(r"(\d+)(ms|s|m|h)", str(text))
    assert m, text
    return int(m.group(1)) * {"ms": 0.001, "s": 1, "m": 60, "h": 3600}[m.group(2)]


def test_outside_probe_is_wired_end_to_end():
    """Journey 15. Prometheus -> blackbox exporter -> public URL. Each link is
    checked against the file that defines the other end, so a rename, a wrong port or
    a missing module cannot leave the probe silently probing nothing."""
    job = _job("blackbox-public")
    bb = _yaml("blackbox/blackbox.yml")
    compose = _yaml("docker-compose.yml")["services"]["pingdin-obs-blackbox"]
    assert job["metrics_path"] == "/probe"
    module = job["params"]["module"][0]
    assert module in bb["modules"], f"scrape asks for module {module!r}, blackbox.yml defines {sorted(bb['modules'])}"
    targets = job["static_configs"][0]["targets"]
    assert targets and all(t.startswith("https://") for t in targets), "every probe target must be an https:// URL"
    replacement = [r["replacement"] for r in job["relabel_configs"] if r.get("target_label") == "__address__"][0]
    assert replacement == f"{compose['container_name']}:9115"
    # the plain scrape of the exporter itself points at the same container
    assert _job("obs-blackbox")["static_configs"][0]["targets"] == [replacement]
    # the compose service mounts the file its own --config.file flag names
    cfg_flag = [c for c in compose["command"] if c.startswith("--config.file=")][0].split("=", 1)[1]
    assert any(v.split(":")[1] == cfg_flag for v in compose["volumes"]), (cfg_flag, compose["volumes"])
    assert os.path.exists(os.path.join(_ROOT, "blackbox", "blackbox.yml"))
    # timing: prober must finish inside Prometheus's scrape timeout, which must fit the interval
    prober = _duration_seconds(bb["modules"][module]["timeout"])
    timeout, interval = _duration_seconds(job["scrape_timeout"]), _duration_seconds(job["scrape_interval"])
    assert prober < timeout < interval, (prober, timeout, interval)
    # TLS must actually be verified, or an expired/invalid certificate passes as healthy
    http = bb["modules"][module]["http"]
    assert http.get("fail_if_not_ssl") is True
    assert http.get("tls_config", {}).get("insecure_skip_verify") is not True


def test_outside_probe_alerts_read_the_probe_job_and_are_not_hidden_by_a_host_alert():
    """PublicEndpointDown must stay independent of the main droplet's relay — that
    independence is the whole point — so it may never be an inhibit target."""
    alerts = _alert_exprs()
    assert 'job="blackbox-public"' in alerts["PublicEndpointDown"] and "probe_success" in alerts["PublicEndpointDown"]
    cert = alerts["PublicCertExpiringSoon"]
    assert "probe_ssl_earliest_cert_expiry" in cert and 'job="blackbox-public"' in cert
    assert "> 0" in cert, "a probe that never reached TLS must not read as an expired certificate"
    assert 'job="obs-blackbox"' in alerts["BlackboxExporterDown"] and "absent(" in alerts["BlackboxExporterDown"]
    import re
    for rule in _yaml("alertmanager/alertmanager.yml")["inhibit_rules"]:
        for m in rule["target_matchers"]:
            names = set(m.split("=~", 1)[1].strip().strip('"').split("|")) if "=~" in m else {m.split("=", 1)[1].strip().strip('"')}
            assert not (names & {"PublicEndpointDown", "PublicCertExpiringSoon", "BlackboxExporterDown"}), names


def test_the_dashboard_outside_view_reads_the_probe_metrics():
    d = _dashboards()["pingdin-app-services.json"]
    exprs = " ".join(_dashboard_prom_exprs(d))
    for metric in ("probe_success", "probe_ssl_earliest_cert_expiry", "probe_duration_seconds"):
        assert metric in exprs, f"the App Services dashboard has no panel reading {metric}"


# Jobs the main droplet's pingdin-alloy-edge relays (source of truth: pingdin_platform's
# alloy-edge/config.alloy — this repo cannot import it). Adding one here is a deliberate
# act; the point of the test below is that a MISSPELT job in a rule fails the build.
_RELAYED_JOBS = {"node", "cadvisor", "postgres", "redis", "pingdin-edge", "pingdin-core", "pingdin-compute"}


def test_every_job_named_in_a_rule_or_panel_is_a_job_that_exists():
    """A selector on a job that is not scraped is empty forever: an alert on it never
    fires (the worst kind of alerting bug) and a panel on it is blank. Every job name
    used anywhere must be a local scrape job or a known relayed one."""
    import re
    known = {j["job_name"] for j in _prom()["scrape_configs"]} | _RELAYED_JOBS
    exprs = list(_alert_exprs().values()) + list(_recorded_names().values())
    for d in _dashboards().values():
        exprs += _dashboard_prom_exprs(d)
    bad = set()
    for e in exprs:
        for op, val in re.findall(r'\bjob\s*(=~|=)\s*"([^"$]*)"', e):
            for name in (val.split("|") if op == "=~" else [val]):
                if name and not any(re.fullmatch(name, k) for k in known):
                    bad.add(name)
    assert bad == set(), f"job selector(s) matching no scrape job: {sorted(bad)}"


def test_the_drill_sed_commands_in_the_routine_doc_do_what_they_say():
    """ROUTINE_CHECKS.md tells the operator to add a deliberately-404 target with sed
    to prove PublicEndpointDown fires, then remove it. Run those exact commands on a
    copy of the real file: the result must be valid YAML with the extra target, and
    the revert must restore the original."""
    import re, shutil, subprocess, tempfile
    doc = open(os.path.join(_ROOT, "ROUTINE_CHECKS.md"), encoding="utf-8").read()
    add = re.search(r"^(sed -i 's#\^ +- https://app\.pingd\.in/health#.*)$", doc, re.M)
    rev = re.search(r"^(sed -i '/drill-404/d' prometheus/prometheus\.yml)$", doc, re.M)
    assert add and rev, "the drill's sed commands are missing from ROUTINE_CHECKS.md"
    with tempfile.TemporaryDirectory() as d:
        os.makedirs(os.path.join(d, "prometheus"))
        path = os.path.join(d, "prometheus", "prometheus.yml")
        shutil.copy(os.path.join(_ROOT, "prometheus", "prometheus.yml"), path)
        original = open(path).read()
        subprocess.run(["bash", "-c", add.group(1)], cwd=d, check=True)
        import yaml
        targets = [j for j in yaml.safe_load(open(path)) ["scrape_configs"] if j["job_name"] == "blackbox-public"][0]["static_configs"][0]["targets"]
        assert "https://app.pingd.in/drill-404" in targets and len(targets) == 2
        subprocess.run(["bash", "-c", rev.group(1)], cwd=d, check=True)
        assert open(path).read() == original, "the revert did not restore the file"


def test_compose_top_level_blocks_only_contain_what_belongs_in_them():
    """Journey 15 caught this for real: a service pasted just above `volumes:` landed
    under the top-level `networks:` block (which sits between `services:` and
    `volumes:`) — valid YAML, invalid Compose, and the deploy would have failed. Every
    service must be under `services:`; `networks:`/`volumes:` may hold only definitions."""
    compose = _yaml("docker-compose.yml")
    assert set(compose) <= {"services", "networks", "volumes", "name"}, set(compose)
    service_keys = {"image", "build", "container_name", "command", "restart", "ports", "depends_on", "healthcheck", "environment"}
    for block in ("networks", "volumes"):
        for name, definition in (compose.get(block) or {}).items():
            leaked = service_keys & set(definition or {})
            assert not leaked, f"{block}.{name} looks like a service (has {sorted(leaked)}) — misplaced under {block}:"
    for name, svc in compose["services"].items():
        assert "image" in svc or "build" in svc, f"services.{name} has neither image nor build"


def test_dashboard_files_always_win_over_the_grafana_database():
    """Journey 18. allowUiUpdates: true lets a UI save push the database version ahead of the file's,
    after which Grafana quietly ignores file changes — the repo's dashboards then never reach the
    screen. The repo is the source of truth, so file changes must always be applied."""
    providers = _yaml("grafana/provisioning/dashboards/dashboards.yml")["providers"]
    assert providers, "no dashboard providers"
    for p in providers:
        assert p.get("allowUiUpdates") is False, f"provider {p.get('name')!r}: allowUiUpdates must be false"
        assert 0 < int(p.get("updateIntervalSeconds", 0)) <= 60, p


def test_certificate_panel_is_shown_in_days_not_an_auto_scaled_unit():
    """Journey 19. Grafana's `d` unit auto-scales: a panel titled "days left" read "11 weeks" / "9 weeks"."""
    d = _dashboards()["pingdin-app-services.json"]
    panel = [p for p in d["panels"] if p["title"] == "TLS certificate — days left"][0]
    assert panel["fieldConfig"]["defaults"]["unit"] == "suffix: days"


def test_error_ratio_panel_has_a_bounded_axis_when_there_are_no_errors():
    """Journey 17. All-zero data gave an auto axis of 0-10000%: percentunit with min 0 and no
    max. A soft max at the alert threshold keeps the empty chart readable."""
    d = _dashboards()["pingdin-app-services.json"]
    panel = [p for p in d["panels"] if p["title"] == "5xx error ratio"][0]
    soft = panel["fieldConfig"]["defaults"].get("custom", {}).get("axisSoftMax")
    assert soft is not None and 0 < soft <= 0.1, soft


def test_probe_checks_in_the_live_docs_use_get_not_head():
    """Journey 17. `curl -I` sends HEAD; Edge's /health is GET-only and answers 405, so the
    documented check reported a failure for a healthy endpoint (the probe itself uses GET)."""
    for fn in ("ROUTINE_CHECKS.md", "DEPLOYMENT_RUNBOOK.md", "README.md", "SOVEREIGN_OBSERVABILITY.md"):
        for n, line in enumerate(open(os.path.join(_ROOT, fn), encoding="utf-8"), 1):
            assert "curl -sSI" not in line, f"{fn}:{n} uses a HEAD request: {line.strip()[:90]}"


def test_alert_bridge_dockerfile_copies_every_module_main_imports():
    """Journey 302's failure class in pingdin_platform: a COPY that names
    fewer files than the code imports builds fine and crashes at start."""
    import ast
    bridge = os.path.join(_ROOT, "alert_bridge")
    local_modules = {f[:-3] for f in os.listdir(bridge) if f.endswith(".py")}
    tree = ast.parse(open(os.path.join(bridge, "main.py"), encoding="utf-8").read())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
        elif isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
    needed = (imported & local_modules) - {"main"}
    assert "formatting" in needed  # the scan sees the real import
    dockerfile = open(os.path.join(bridge, "Dockerfile"), encoding="utf-8").read()
    copies = [l.split()[1:-1] for l in dockerfile.splitlines() if l.startswith("COPY ") and "requirements" not in l]
    copied = {f for c in copies for f in c}
    covered = "*.py" in copied or all(f"{m}.py" in copied for m in needed)
    assert covered, f"Dockerfile COPY does not include: {sorted(needed)}"


def test_every_dashboard_datasource_reference_resolves_and_uids_are_unique():
    ds_cfg = _yaml("grafana/provisioning/datasources/datasources.yml")
    known = set()
    for ds in ds_cfg["datasources"]:
        known.add(ds["name"])
        if ds.get("uid"):
            known.add(ds["uid"])
    dashboards = _dashboards()
    uids = [d["uid"] for d in dashboards.values()]
    assert len(uids) == len(set(uids)), f"duplicate dashboard uids: {uids}"
    unresolved = []
    for fname, d in dashboards.items():
        refs = [p.get("datasource") for p in d.get("panels", [])]
        refs += [t.get("datasource") for p in d.get("panels", []) for t in p.get("targets", [])]
        refs += [v.get("datasource") for v in d.get("templating", {}).get("list", [])]
        for ref in refs:
            if isinstance(ref, dict) and ref.get("uid") and ref["uid"] not in known:
                unresolved.append(f"{fname}: {ref['uid']}")
    assert not unresolved, f"dashboard datasource uid not provisioned: {sorted(set(unresolved))}"


def test_every_dashboard_panel_has_a_unique_id():
    for fname, d in _dashboards().items():
        ids = [p["id"] for p in d["panels"] if "id" in p]
        assert len(ids) == len(set(ids)), f"{fname}: duplicate panel ids"


if __name__ == "__main__":
    import traceback

    tests = [o for n, o in list(globals().items()) if n.startswith("test_") and callable(o)]
    passed = failed = skipped = 0
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as e:
            if "skip" in type(e).__name__.lower() or "skip" in type(e).__module__.lower():
                skipped += 1
                continue
            failed += 1
            print(f"FAILED: {t.__name__}")
            traceback.print_exc()
    print(f"{passed} passed, {failed} failed, {skipped} skipped (of {len(tests)})")
