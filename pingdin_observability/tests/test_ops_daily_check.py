"""
Run: python3 tests/test_ops_daily_check.py   (or pytest)

BUILD_LOG Journey 16. scripts/ops_daily_check.sh runs for real (bash) with its real
sub-script (find_stale_containers.sh) in a scratch copy of the repo layout; only the
`docker` and `curl` executables are stubs (scenario fixtures in, recorded calls out).
"""

import json
import os
import shutil
import stat
import subprocess
import tempfile

_ROOT = os.path.join(os.path.dirname(__file__), "..")
_A, _B = "sha256:" + "a" * 64, "sha256:" + "b" * 64

_DOCKER_STUB = r'''#!/usr/bin/env python3
import json, os, sys
fx = json.load(open(os.environ["STUB_FIXTURE"]))
a = sys.argv[1:]
if a[:2] == ["compose", "ps"]:
    print("\n".join(fx["ids"])); sys.exit(0)
if a[0] == "inspect" and a[1] == "--format":
    print(fx["inspect"][a[3]]); sys.exit(0)
if a[:2] == ["image", "inspect"]:
    ref = a[4]
    if ref in fx["images"]:
        print(fx["images"][ref]); sys.exit(0)
    sys.exit(1)
if a[:2] == ["compose", "up"]:
    if fx.get("dry_run") is None:
        sys.stderr.write("compose: no configuration file provided\n"); sys.exit(1)
    print(fx["dry_run"]); sys.exit(0)
if a[0] == "ps":
    print("\n".join(fx.get("unhealthy", []))); sys.exit(0)
if a[0] == "exec" and "pingdin-obs-grafana" in a:
    if fx.get("grafana_files") is None:
        sys.exit(1)
    print(fx["grafana_files"]); sys.exit(0)
if a[0] == "exec" and "pingdin-obs-prometheus" in a:
    if fx.get("rules") is None:
        sys.exit(1)
    sys.stdout.write(fx["rules"]); sys.exit(0)
sys.stderr.write("unexpected docker call: " + " ".join(a) + "\n"); sys.exit(99)
'''

_CURL_STUB = r'''#!/usr/bin/env python3
import json, os, sys
open(os.environ["STUB_CURL_LOG"], "a").write(json.dumps(sys.argv[1:]) + "\n")
sys.exit(int(os.environ.get("STUB_CURL_RC", "0")))
'''


def _rules(unhealthy=(), unknown=()):
    rules = [{"name": n, "health": "ok"} for n in ("Watchdog", "job:http_error_ratio:rate5m")]
    rules += [{"name": n, "health": "err"} for n in unhealthy]
    rules += [{"name": n, "health": "unknown"} for n in unknown]
    return json.dumps({"status": "success", "data": {"groups": [{"name": "g", "rules": rules}]}})


def _run(*, stale=False, dry_run="ok", unhealthy=(), bad_rules=(), rules_raw="default", dotenv="TAILSCALE_IP=100.92.158.102\nOWNER_ALERT_WEBHOOK_TOKEN=tok-secret-123\n",
         curl_rc=0, env_extra=None, grafana_files=3, compose=None):
    with tempfile.TemporaryDirectory() as d:
        os.makedirs(os.path.join(d, "scripts"))
        for s in ("ops_daily_check.sh", "find_stale_containers.sh"):
            shutil.copy(os.path.join(_ROOT, "scripts", s), os.path.join(d, "scripts", s))
        if dotenv is not None:
            open(os.path.join(d, ".env"), "w").write(dotenv)
        if compose is not None:   # Obs journey 31 (section 6): the images the compose file names
            open(os.path.join(d, "docker-compose.yml"), "w").write(compose)
        dash = os.path.join(d, "grafana", "provisioning", "dashboards", "json")
        os.makedirs(dash)
        for n in ("a", "b", "c"):
            open(os.path.join(dash, f"{n}.json"), "w").write("{}")
        fx = {
            "ids": ["c1", "c2"],
            "inspect": {"c1": f"/pingdin-obs-prometheus|{_A}|prom/prometheus:latest|pingdin-obs-prometheus",
                        "c2": f"/pingdin-obs-blackbox|{_A}|prom/blackbox-exporter:latest|pingdin-obs-blackbox"},
            "images": {"prom/prometheus:latest": _A, "prom/blackbox-exporter:latest": _B if stale else _A},
            "dry_run": " ✔ Container pingdin-obs-prometheus  Healthy  0.1s" if dry_run == "ok" else dry_run,
            "unhealthy": list(unhealthy),
            "rules": _rules(bad_rules) if rules_raw == "default" else rules_raw,
            "grafana_files": grafana_files,
        }
        json.dump(fx, open(os.path.join(d, "fixture.json"), "w"))
        bin_ = os.path.join(d, "bin")
        os.makedirs(bin_)
        for name, body in (("docker", _DOCKER_STUB), ("curl", _CURL_STUB)):
            p = os.path.join(bin_, name)
            open(p, "w").write(body)
            os.chmod(p, os.stat(p).st_mode | stat.S_IEXEC)
        env = dict(os.environ, PATH=bin_ + os.pathsep + os.environ["PATH"], STUB_FIXTURE=os.path.join(d, "fixture.json"),
                   STUB_CURL_LOG=os.path.join(d, "curl.log"), STUB_CURL_RC=str(curl_rc))
        for k in ("OPS_CHECK_PING_URL", "TAILSCALE_IP", "OWNER_ALERT_WEBHOOK_TOKEN"):
            env.pop(k, None)
        env.update(env_extra or {})
        r = subprocess.run(["bash", os.path.join(d, "scripts", "ops_daily_check.sh")], capture_output=True, text=True, env=env, cwd=d)
        calls = [json.loads(l) for l in open(os.path.join(d, "curl.log")).read().splitlines()] if os.path.exists(os.path.join(d, "curl.log")) else []
        return r, calls


def _post(calls):
    """The one POST to the bridge, decoded: (url, headers, payload)."""
    posts = [c for c in calls if "-X" in c]
    assert len(posts) == 1, calls
    c = posts[0]
    url = [a for a in c if a.startswith("http://")][0]
    headers = [c[i + 1] for i, a in enumerate(c) if a == "-H"]
    payload = json.loads(c[c.index("-d") + 1])
    return url, headers, payload


def test_healthy_is_silent_and_exits_zero():
    r, calls = _run()
    assert r.returncode == 0, r.stdout + r.stderr
    assert "OK — all daily checks passed" in r.stdout and calls == []


def test_clean_pass_pings_the_external_check_and_a_problem_does_not():
    url = "https://hc.example/obs-secret-uuid"
    r, calls = _run(env_extra={"OPS_CHECK_PING_URL": url})
    assert r.returncode == 0 and calls == [["-fsS", "-m", "10", "--retry", "2", "-o", "/dev/null", url]]
    assert url not in r.stdout + r.stderr
    r, calls = _run(stale=True, env_extra={"OPS_CHECK_PING_URL": url})
    assert r.returncode == 1 and all(url not in c for c in calls), "a failing check must not ping the external check"


def test_a_problem_is_posted_to_the_local_bridge_with_the_token_and_a_source():
    r, calls = _run(stale=True)
    assert r.returncode == 1
    url, headers, payload = _post(calls)
    assert url == "http://100.92.158.102:8090/owner-alert"
    assert "Authorization: Bearer tok-secret-123" in headers and "Content-Type: application/json" in headers
    assert payload["subject"].startswith("Daily ops check: 1 problem(s)")
    assert payload["source"] == "pingdin-observability" and payload["context"] == {"check": "ops_daily_check"}
    assert "pingdin-obs-blackbox" in payload["detail"] and "docker compose up -d --build --force-recreate pingdin-obs-blackbox" in payload["detail"]
    assert "tok-secret-123" not in r.stdout + r.stderr, "the bridge token must never be printed"


def test_a_message_containing_quotes_and_newlines_survives_json_encoding():
    r, calls = _run(dry_run=' ✔ Container "weird\'name"  Recreated  0.2s')
    _, _, payload = _post(calls)  # would raise if the payload were not valid JSON
    assert payload["detail"].startswith("- ")


def test_compose_drift_names_the_containers():
    dry = " ✔ Container pingdin-obs-loki  Recreated  0.2s\n ✔ Container pingdin-obs-grafana  Healthy  0.1s"
    r, calls = _run(dry_run=dry)
    _, _, payload = _post(calls)
    assert "pingdin-obs-loki" in payload["detail"] and "pingdin-obs-grafana" not in payload["detail"]


def test_a_failing_dry_run_is_a_problem_not_a_silent_pass():
    r, calls = _run(dry_run=None)
    assert r.returncode == 1 and "could not run" in _post(calls)[2]["detail"]


def test_unhealthy_container_is_reported():
    r, calls = _run(unhealthy=["pingdin-alert-bridge"])
    assert r.returncode == 1 and "pingdin-alert-bridge" in _post(calls)[2]["detail"]


def test_a_rule_in_an_error_state_is_reported_by_name():
    r, calls = _run(bad_rules=["PublicEndpointDown"])
    assert r.returncode == 1
    d = _post(calls)[2]["detail"]
    assert "PublicEndpointDown" in d and "silently stop evaluating" in d


def test_a_rule_that_has_not_been_evaluated_yet_is_not_a_problem():
    """Journey 20. Right after a restart every rule reports health "unknown" until its first evaluation; deploy.sh was
    immediately followed by this check and it paged "AlertBridgeDown Watchdog not healthy" — a false alarm."""
    r, calls = _run(rules_raw=_rules(unknown=("AlertBridgeDown", "Watchdog")))
    assert r.returncode == 0 and calls == [], r.stdout
    r, calls = _run(rules_raw=_rules(unhealthy=("PublicEndpointDown",), unknown=("Watchdog",)))
    d = _post(calls)[2]["detail"]
    assert r.returncode == 1 and "PublicEndpointDown" in d and "Watchdog" not in d


def test_unreadable_or_missing_rules_is_a_problem():
    r, calls = _run(rules_raw=None)
    assert r.returncode == 1 and "could not read Prometheus's rules" in _post(calls)[2]["detail"]
    r, calls = _run(rules_raw="<html>not json</html>")
    assert r.returncode == 1 and "could not read" in _post(calls)[2]["detail"]


def test_a_stale_grafana_mount_is_reported_with_the_fix():
    """Journey 18: the container saw NO provisioning directory (the host one had been replaced after it
    started), so Grafana logged errors every 30 s and applied no dashboard change — silently."""
    for seen in (0, 2):                                  # nothing visible / fewer files than the repo has
        r, calls = _run(grafana_files=seen)
        assert r.returncode == 1, seen
        d = _post(calls)[2]["detail"]
        assert f"has {seen} file(s) but the repo has 3" in d and "bind mount is stale" in d
        assert "docker compose up -d --force-recreate pingdin-obs-grafana" in d


def test_an_unreachable_grafana_is_reported_not_passed():
    r, calls = _run(grafana_files=None)                  # docker exec failed (container down / no shell)
    assert r.returncode == 1 and "has 0 file(s) but the repo has 3" in _post(calls)[2]["detail"]


def test_if_the_bridge_cannot_be_reached_the_exit_code_says_so():
    r, calls = _run(stale=True, curl_rc=22)
    assert r.returncode == 2 and "COULD NOT SEND THE REPORT" in r.stderr and "stale container" in r.stdout
    r, calls = _run(stale=True, dotenv="OWNER_ALERT_WEBHOOK_TOKEN=x\n")   # no TAILSCALE_IP at all
    assert r.returncode == 2 and "TAILSCALE_IP unset" in r.stderr and calls == []


def test_dotenv_is_never_executed():
    marker = "/tmp/obs_ops_check_pwned"
    if os.path.exists(marker):
        os.unlink(marker)
    r, calls = _run(dotenv=f"NOTE=hello world $(touch {marker})\nTAILSCALE_IP=100.92.158.102\nOWNER_ALERT_WEBHOOK_TOKEN=t\n", stale=True)
    assert not os.path.exists(marker), ".env was executed"
    assert _post(calls)[0] == "http://100.92.158.102:8090/owner-alert"


def test_the_cron_line_in_the_docs_matches_the_script():
    doc = open(os.path.join(_ROOT, "docs", "ROUTINE_CHECKS.md"), encoding="utf-8").read()
    line = "20 6 * * * cd /opt/pingdin_observability && bash scripts/ops_daily_check.sh >> ops-check.log 2>&1"
    assert line in doc
    assert line in open(os.path.join(_ROOT, "scripts", "ops_daily_check.sh"), encoding="utf-8").read()


if __name__ == "__main__":
    import traceback

    tests = [o for n, o in list(globals().items()) if n.startswith("test_") and callable(o)]
    passed = failed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except Exception:
            failed += 1
            print(f"FAILED: {t.__name__}")
            traceback.print_exc()
    print(f"{passed} passed, {failed} failed (of {len(tests)})")


def test_an_image_on_a_floating_tag_is_a_problem_and_a_digest_or_fixed_tag_is_not():
    """Obs journey 31."""
    pinned = "services:\n  a:\n    image: prom/prometheus@sha256:" + "a" * 64 + "\n  g:\n    image: grafana/grafana:12.1.1\n  b:\n    build: .\n"
    r, _ = _run(compose=pinned)
    assert r.returncode == 0, r.stdout + r.stderr
    r, calls = _run(compose=pinned + "  l:\n    image: grafana/loki:latest   # logs\n  t:\n    image: grafana/tempo\n")
    assert r.returncode == 1 and "grafana/loki:latest" in r.stdout and "grafana/tempo" in r.stdout and "pin_image_digests.py --write" in r.stdout
    assert "grafana/grafana" not in r.stdout.split("floating tag")[1].split("pin them")[0]
    assert _run(compose=pinned + "  l:\n    image: grafana/loki:latest\n", env_extra={"OPS_CHECK_SKIP_PINS": "1"})[0].returncode == 0
