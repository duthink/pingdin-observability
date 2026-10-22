"""
Run: python3 tests/test_deploy_scripts.py   (or pytest)

BUILD_LOG Journey 19. scripts/deploy.sh (server) and scripts/sync_to_server.sh (laptop) run for real under
bash in a scratch layout; only `docker`, `ssh` and `sleep` are stubs. What is pinned: the services re-created
after a sync are DERIVED from the compose file (nothing hard-coded), nothing is touched when the compose file is
invalid, and the sync never sends the server-owned .env or caches.
"""
import io
import json
import os
import shutil
import stat
import subprocess
import tarfile
import tempfile

_ROOT = os.path.join(os.path.dirname(__file__), "..")

_DOCKER = r'''#!/usr/bin/env python3
import json, os, sys
fx = json.load(open(os.environ["STUB_FIXTURE"]))
a = sys.argv[1:]
open(os.environ["STUB_LOG"], "a").write(" ".join(a) + "\n")
if a[:3] == ["compose", "config", "-q"]:
    sys.exit(fx.get("config_rc", 0))
if a[:4] == ["compose", "config", "--format", "json"]:
    sys.stdout.write(json.dumps(fx["config"]).replace("@REPO@", os.environ["STUB_REPO"])); sys.exit(0)
if a[:3] == ["compose", "up", "-d"] and "--force-recreate" in a:
    sys.exit(fx.get("recreate_rc", 0))
if a[:3] == ["compose", "up", "-d"]:
    sys.exit(fx.get("up_rc", 0))
if a[0] == "ps" and "health=starting" in " ".join(a):
    n = int(open(os.environ["STUB_COUNTER"]).read() or 0) if os.path.exists(os.environ["STUB_COUNTER"]) else 0
    open(os.environ["STUB_COUNTER"], "w").write(str(n + 1))
    if n < fx.get("starting_polls", 0):
        print("pingdin-obs-prometheus")
    sys.exit(0)
if a[0] == "ps" and "health=unhealthy" in " ".join(a):
    print("\n".join(fx.get("unhealthy", []))); sys.exit(0)
sys.stderr.write("unexpected docker call: " + " ".join(a) + "\n"); sys.exit(99)
'''
_SLEEP = "#!/bin/sh\necho x >> \"$STUB_SLEEPS\"\n"

_CONFIG = {"services": {
    "pingdin-obs-prometheus": {"volumes": [{"type": "bind", "source": "@REPO@/prometheus/prometheus.yml"},
                                           {"type": "volume", "source": "prom-data"}]},
    "pingdin-obs-grafana": {"volumes": [{"type": "bind", "source": "@REPO@/grafana/provisioning"}]},
    "pingdin-obs-alertmanager": {"volumes": [{"type": "bind", "source": "@REPO@/alertmanager/alertmanager.yml"}]},
    "pingdin-obs-cadvisor": {"volumes": [{"type": "bind", "source": "/var/run"}, {"type": "bind", "source": "/sys"}]},
    "pingdin-alert-bridge": {"build": {"context": "@REPO@/alert_bridge"}, "volumes": []},
    "pingdin-image-only": {"volumes": []},
}}


def _run_deploy(*, config=None, stale_rc=0, **fx_extra):
    with tempfile.TemporaryDirectory() as d:
        repo = os.path.join(d, "repo")
        os.makedirs(os.path.join(repo, "scripts"))
        shutil.copy(os.path.join(_ROOT, "scripts", "deploy.sh"), os.path.join(repo, "scripts", "deploy.sh"))
        open(os.path.join(repo, "scripts", "find_stale_containers.sh"), "w").write(f"#!/bin/bash\nexit {stale_rc}\n")
        bindir = os.path.join(d, "bin")
        os.makedirs(bindir)
        for name, body in (("docker", _DOCKER), ("sleep", _SLEEP)):
            p = os.path.join(bindir, name)
            open(p, "w").write(body)
            os.chmod(p, os.stat(p).st_mode | stat.S_IEXEC)
        fx = dict(config=config if config is not None else _CONFIG, **fx_extra)
        json.dump(fx, open(os.path.join(d, "fx.json"), "w"))
        env = dict(os.environ, PATH=bindir + os.pathsep + os.environ["PATH"], STUB_FIXTURE=os.path.join(d, "fx.json"),
                   STUB_LOG=os.path.join(d, "calls.log"), STUB_REPO=os.path.realpath(repo), STUB_COUNTER=os.path.join(d, "n"),
                   STUB_SLEEPS=os.path.join(d, "sleeps"))
        r = subprocess.run(["bash", os.path.join(repo, "scripts", "deploy.sh")], capture_output=True, text=True, env=env, cwd=repo)
        rd = lambda n: open(os.path.join(d, n)).read().splitlines() if os.path.exists(os.path.join(d, n)) else []
        return r, rd("calls.log"), len(rd("sleeps"))


def test_happy_path_validates_builds_then_recreates_exactly_the_repo_mounting_services():
    r, calls, _ = _run_deploy()
    assert r.returncode == 0, r.stdout + r.stderr
    assert calls[0] == "compose config -q" and calls[1] == "compose up -d --build"
    recreate = [c for c in calls if "--force-recreate" in c]
    assert recreate == ["compose up -d --force-recreate pingdin-alert-bridge pingdin-obs-alertmanager pingdin-obs-grafana pingdin-obs-prometheus"], recreate
    assert "pingdin-image-only" not in recreate[0], "a service with neither a build nor a repo mount needs no recreate"
    assert "DEPLOY OK" in r.stdout


def test_the_recreate_list_comes_from_the_compose_file_not_from_the_script():
    """A new service that mounts repo config must be picked up with no edit to deploy.sh."""
    cfg = json.loads(json.dumps(_CONFIG))
    cfg["services"]["pingdin-obs-newthing"] = {"volumes": [{"type": "bind", "source": "@REPO@/newthing/config.yml"}]}
    r, calls, _ = _run_deploy(config=cfg)
    assert [c for c in calls if "--force-recreate" in c][0].endswith("pingdin-obs-newthing pingdin-obs-prometheus")
    assert "pingdin-alert-bridge" in [c for c in calls if "--force-recreate" in c][0], "a locally built service must be recreated after a build"
    assert "pingdin-obs-cadvisor" not in " ".join(calls), "system-path mounts (/var/run, /sys) are not repo config"


def test_an_invalid_compose_file_changes_nothing():
    r, calls, _ = _run_deploy(config_rc=1)
    assert r.returncode == 1 and "nothing was changed" in r.stdout
    assert calls == ["compose config -q"]


def test_a_failed_build_stops_before_any_recreate():
    r, calls, _ = _run_deploy(up_rc=1)
    assert r.returncode == 1 and "docker compose up failed" in r.stdout
    assert not [c for c in calls if "--force-recreate" in c]


def test_it_refuses_to_guess_when_no_service_mounts_repo_files():
    r, calls, _ = _run_deploy(config={"services": {"a": {"volumes": [{"type": "bind", "source": "/etc"}]}}})
    assert r.returncode == 1 and "refusing to guess" in r.stdout
    assert not [c for c in calls if "--force-recreate" in c]


def test_a_failed_recreate_is_a_failed_deploy():
    r, _, _ = _run_deploy(recreate_rc=1)
    assert r.returncode == 1 and "re-creating the mounting services failed" in r.stdout


def test_it_waits_for_health_checks_to_settle():
    r, _, sleeps = _run_deploy(starting_polls=3)
    assert r.returncode == 0 and sleeps == 3, sleeps


def test_unhealthy_or_stale_containers_make_the_deploy_fail():
    r, _, _ = _run_deploy(unhealthy=["pingdin-obs-grafana"])
    assert r.returncode == 1 and "UNHEALTHY: pingdin-obs-grafana" in r.stdout
    r, _, _ = _run_deploy(stale_rc=1)
    assert r.returncode == 1 and "STALE container(s)" in r.stdout


def test_the_real_compose_file_has_the_mounting_services_the_incident_involved():
    import yaml
    c = yaml.safe_load(open(os.path.join(_ROOT, "docker-compose.yml"), encoding="utf-8"))["services"]
    mounting = {n for n, s in c.items() if any(isinstance(v, str) and v.startswith("./") for v in (s.get("volumes") or []))}
    assert {"pingdin-obs-prometheus", "pingdin-obs-grafana", "pingdin-obs-alertmanager"} <= mounting, mounting


# ── sync_to_server.sh ─────────────────────────────────────────────────────────────────────

_SSH = r'''#!/usr/bin/env python3
import os, sys
cmd = " ".join(sys.argv[1:])
open(os.environ["STUB_SSH_LOG"], "a").write(cmd + "\n")
data = sys.stdin.buffer.read()
if "tar xzf" in cmd:
    open(os.environ["STUB_SSH_TAR"], "wb").write(data); sys.exit(int(os.environ.get("STUB_SSH_RC", "0")))
if "prune_extras" in cmd:
    open(os.environ["STUB_SSH_MANIFEST"], "wb").write(data); sys.exit(int(os.environ.get("STUB_PRUNE_RC", "0")))
if "deploy.sh" in cmd:
    sys.exit(int(os.environ.get("STUB_DEPLOY_RC", "0")))
sys.exit(99)
'''


def _run_sync(*flags, conf="PINGDIN_DEPLOY_TARGET=duthink@142.93.209.14:/opt\n", ssh_rc=0, prune_rc=0, deploy_rc=0):
    with tempfile.TemporaryDirectory() as d:
        repo = os.path.join(d, "pingdin_observability")
        for rel, txt in (("scripts/sync_to_server.sh", None), ("README.md", "hi"), ("prometheus/prometheus.yml", "x"),
                         (".env", "SECRET=laptop"), ("__pycache__/a.pyc", "x"), ("tests/__pycache__/b.pyc", "x"),
                         (".git/HEAD", "ref"), (".pytest_cache/v", "v"), ("ops-check.log", "log")):
            path = os.path.join(repo, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            if txt is None:
                shutil.copy(os.path.join(_ROOT, "scripts", "sync_to_server.sh"), path)
            else:
                open(path, "w").write(txt)
        if conf is not None:
            open(os.path.join(repo, "scripts", "deploy_target.conf"), "w").write(conf)
        bindir = os.path.join(d, "bin")
        os.makedirs(bindir)
        p = os.path.join(bindir, "ssh")
        open(p, "w").write(_SSH)
        os.chmod(p, os.stat(p).st_mode | stat.S_IEXEC)
        env = dict(os.environ, PATH=bindir + os.pathsep + os.environ["PATH"], STUB_SSH_LOG=os.path.join(d, "ssh.log"),
                   STUB_SSH_TAR=os.path.join(d, "pushed.tar.gz"), STUB_SSH_RC=str(ssh_rc),
                   STUB_SSH_MANIFEST=os.path.join(d, "manifest.txt"), STUB_PRUNE_RC=str(prune_rc), STUB_DEPLOY_RC=str(deploy_rc))
        env.pop("PINGDIN_DEPLOY_TARGET", None)
        r = subprocess.run(["bash", os.path.join(repo, "scripts", "sync_to_server.sh"), *flags], capture_output=True, text=True, env=env, cwd=d,
                           stdin=subprocess.DEVNULL)
        r.manifest = open(os.path.join(d, "manifest.txt")).read().splitlines() if os.path.exists(os.path.join(d, "manifest.txt")) else None
        ssh = open(os.path.join(d, "ssh.log")).read().splitlines() if os.path.exists(os.path.join(d, "ssh.log")) else []
        names = []
        if os.path.exists(os.path.join(d, "pushed.tar.gz")) and os.path.getsize(os.path.join(d, "pushed.tar.gz")):
            with tarfile.open(os.path.join(d, "pushed.tar.gz")) as t:
                names = t.getnames()
        return r, ssh, names


def test_the_push_sends_the_repo_but_never_the_server_owned_or_junk_files():
    r, ssh, names = _run_sync()
    assert r.returncode == 0, r.stdout + r.stderr
    assert "pingdin_observability/README.md" in names and "pingdin_observability/prometheus/prometheus.yml" in names
    assert "pingdin_observability/scripts/sync_to_server.sh" in names
    for junk in (".env", "__pycache__", ".git", ".pytest_cache", "ops-check.log", ".pyc"):
        assert not [n for n in names if junk in n.split("/")[-1] or f"/{junk}/" in "/" + n + "/"], f"{junk} must not be pushed"


def test_it_extracts_into_the_parent_so_the_repo_lands_where_the_compose_project_lives():
    r, ssh, _ = _run_sync()
    assert ssh == ["duthink@142.93.209.14 mkdir -p '/opt' && tar xzf - -C '/opt'",
                   "duthink@142.93.209.14 cd '/opt/pingdin_observability' && python3 scripts/prune_extras.py --manifest - --max 40"], ssh
    assert "cd /opt/pingdin_observability && bash scripts/deploy.sh" in r.stdout, "without --deploy it must say the NEXT step"


def test_the_mirror_receives_exactly_the_pushed_file_list():
    r, _, names = _run_sync()
    assert r.manifest is not None
    assert "pingdin_observability" not in " ".join(r.manifest), "paths must be relative to the repo root"
    for must in ("README.md", "prometheus/prometheus.yml", "scripts/sync_to_server.sh"):
        assert must in r.manifest, f"{must} missing from the manifest"
    for junk in (".env", "__pycache__", ".git/", ".pytest_cache", "ops-check.log", ".pyc"):
        assert not [m for m in r.manifest if junk in m], f"{junk} must not be in the manifest (the server owns it)"
    pushed = {n.split("/", 1)[1] for n in names if "/" in n}
    assert set(r.manifest) <= pushed, f"the manifest lists files that were not pushed: {sorted(set(r.manifest) - pushed)}"


def test_no_prune_skips_the_mirror_and_prune_max_is_passed_through():
    r, ssh, _ = _run_sync("--no-prune")
    assert len(ssh) == 1 and "prune_extras" not in " ".join(ssh) and "skipped" in r.stdout
    r, ssh, _ = _run_sync("--prune-max", "7")
    assert "--max 7" in ssh[1]
    r, ssh, _ = _run_sync("--prune-max", "lots")
    assert r.returncode == 2


def test_a_refused_or_failed_mirror_is_a_warning_the_sync_still_succeeded():
    r, ssh, _ = _run_sync(prune_rc=3)
    assert r.returncode == 0 and "WARNING: the mirror step did not complete (exit 3)" in r.stderr


def test_deploy_flag_runs_the_server_side_deploy_after_the_mirror_and_propagates_its_result():
    r, ssh, _ = _run_sync("--deploy")
    assert r.returncode == 0 and len(ssh) == 3 and ssh[2] == "duthink@142.93.209.14 cd '/opt/pingdin_observability' && bash scripts/deploy.sh"
    assert "scripts/deploy.sh" not in " ".join(_run_sync()[1]), "no deploy without the flag"
    r, ssh, _ = _run_sync("--deploy", deploy_rc=1)
    assert r.returncode == 1


def test_a_failed_push_never_prunes_or_deploys():
    r, ssh, _ = _run_sync("--deploy", ssh_rc=255)
    assert r.returncode == 1 and len(ssh) == 1


def test_dry_run_never_connects():
    r, ssh, names = _run_sync("--dry-run", "--deploy")
    assert r.returncode == 0 and ssh == [] and names == [] and "[dry-run]" in r.stdout
    assert "prune_extras.py" in r.stdout and "deploy.sh" in r.stdout, "a dry run should show every step it would take"


def test_a_failed_push_is_an_error_not_a_success():
    r, _, _ = _run_sync(ssh_rc=255)
    assert r.returncode == 1 and "the push failed" in r.stderr and "mirror" not in r.stdout


def test_target_comes_from_the_conf_or_the_flag_and_must_be_valid():
    r, _, _ = _run_sync(conf="PINGDIN_DEPLOY_TARGET=nonsense\n")
    assert r.returncode == 1 and "no valid target" in r.stderr
    r, ssh, _ = _run_sync("--target", "me@other:/srv")
    assert r.returncode == 0 and ssh[0].startswith("me@other mkdir -p '/srv'") and "cd '/srv/pingdin_observability'" in ssh[1]
    r, _, _ = _run_sync("--bogus")
    assert r.returncode == 2


def test_the_shipped_deploy_target_points_at_the_observability_droplet():
    conf = open(os.path.join(_ROOT, "scripts", "deploy_target.conf"), encoding="utf-8").read()
    assert "PINGDIN_DEPLOY_TARGET=duthink@142.93.209.14:/opt" in conf


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
