"""
Run: pytest tests/test_find_stale_containers.py -v   (or: python3 tests/test_find_stale_containers.py)

BUILD_LOG Journey 12 (copy of pingdin_platform's Journeys 380/381 test). Runs scripts/find_stale_containers.sh for real (bash)
against a stub `docker` that serves canned `compose ps -a -q`, `inspect` and
`image inspect` answers from a fixture directory — only the docker CLI is
faked. The scenario mirrors the live finding: pingdin-compute's container
was created from an image the tag has since moved away from.
"""

import os
import stat
import subprocess
import tempfile

_ROOT = os.path.join(os.path.dirname(__file__), "..")
_SCRIPT = os.path.join(_ROOT, "scripts", "find_stale_containers.sh")

_STUB = r"""#!/bin/sh
D="$STUB_DIR"
case "$1 $2" in
  "compose ps")
    [ -f "$D/compose_fail" ] && { cat "$D/compose_fail" >&2; exit 1; }
    cat "$D/ids" 2>/dev/null; exit 0 ;;
  "inspect --format") cat "$D/inspect_$4" || exit 1; exit 0 ;;
  "image inspect")
    f="$D/image_$(printf '%s' "$5" | tr '/:' '__')"
    [ -f "$f" ] || exit 1
    cat "$f"; exit 0 ;;
esac
echo "unexpected docker call: $*" >&2; exit 99
"""


def _run(containers, images, compose_fail=None):
    """containers: [(id, name, running_image_id, config_image_ref[, service])],
    images: {ref: current_image_id}. `service` defaults to the container name."""
    with tempfile.TemporaryDirectory() as d:
        stub = os.path.join(d, "docker")
        with open(stub, "w") as f:
            f.write(_STUB)
        os.chmod(stub, os.stat(stub).st_mode | stat.S_IEXEC)
        with open(os.path.join(d, "ids"), "w") as f:
            f.write("".join(c[0] + "\n" for c in containers))
        for c in containers:
            cid, name, running, ref = c[:4]
            service = c[4] if len(c) > 4 else name
            with open(os.path.join(d, f"inspect_{cid}"), "w") as f:
                f.write(f"/{name}|{running}|{ref}|{service}\n")
        for ref, image_id in images.items():
            with open(os.path.join(d, "image_" + ref.replace("/", "_").replace(":", "_")), "w") as f:
                f.write(image_id + "\n")
        if compose_fail:
            with open(os.path.join(d, "compose_fail"), "w") as f:
                f.write(compose_fail)
        env = dict(os.environ, PATH=d + os.pathsep + os.environ["PATH"], STUB_DIR=d)
        return subprocess.run(["bash", _SCRIPT], capture_output=True, text=True, env=env)


_A, _B = "sha256:" + "a" * 64, "sha256:" + "b" * 64


def test_all_current_exits_zero():
    r = _run([("c1", "pingdin-edge", _A, "pingdin_platform-pingdin-edge"),
              ("c2", "pingdin-n8n", _B, "n8nio/n8n:latest")],
             {"pingdin_platform-pingdin-edge": _A, "n8nio/n8n:latest": _B})
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.count("OK ") == 2 and "STALE" not in r.stdout
    assert "2 container(s) checked, 0 stale." in r.stdout


def test_the_live_compute_case_is_reported_stale_and_fails():
    """Container built from image A; the tag now points to B (rebuilt since)."""
    r = _run([("c1", "pingdin-edge", _A, "pingdin_platform-pingdin-edge"),
              ("c3", "pingdin-compute", _A, "pingdin_platform-pingdin-compute")],
             {"pingdin_platform-pingdin-edge": _A, "pingdin_platform-pingdin-compute": _B})
    assert r.returncode == 1
    stale_lines = [l for l in r.stdout.splitlines() if l.startswith("STALE")]
    assert len(stale_lines) == 1 and "pingdin-compute" in stale_lines[0]
    assert "aaaaaaaaaaa" in stale_lines[0] and "bbbbbbbbbbb" in stale_lines[0]  # running vs now
    assert any(l.startswith("OK") and "pingdin-edge" in l for l in r.stdout.splitlines())
    assert "2 container(s) checked, 1 stale." in r.stdout


def test_stale_output_gives_the_command_that_actually_fixes_it():
    """Journey 381: plain `docker compose up -d <svc>` printed 'Running' and
    left pingdin-compute stale on the real server. The script must print the
    force-recreate form, name the SERVICE (compose label, not the container),
    list every stale service in one command, and warn off the plain form."""
    r = _run([("c1", "ctr-a", _A, "img-a", "svc-a"), ("c2", "ctr-b", _A, "img-b", "svc-b"),
              ("c3", "ctr-c", _A, "img-c", "svc-c")],
             {"img-a": _B, "img-b": _B, "img-c": _A})
    assert r.returncode == 1
    assert "docker compose up -d --build --force-recreate svc-a svc-b" in r.stdout
    assert "svc-c" not in r.stdout.split("Fix")[1]      # only the stale ones
    assert "ctr-a" not in r.stdout.split("Fix")[1]      # service name, not container name
    assert "does NOT fix this" in r.stdout


def test_no_fix_block_when_nothing_is_stale():
    r = _run([("c1", "pingdin-edge", _A, "img-a")], {"img-a": _A})
    assert r.returncode == 0 and "Fix" not in r.stdout and "force-recreate" not in r.stdout


def test_a_re_pulled_floating_tag_is_stale_too():
    r = _run([("c1", "pingdin-n8n", _A, "n8nio/n8n:latest")], {"n8nio/n8n:latest": _B})
    assert r.returncode == 1 and "STALE" in r.stdout and "n8nio/n8n:latest" in r.stdout


def test_unresolvable_reference_is_unknown_not_stale():
    r = _run([("c1", "x", _A, "gone/image:1")], {})
    assert r.returncode == 0 and r.stdout.startswith("UNKNOWN") and "0 stale" in r.stdout


def test_name_has_its_leading_slash_stripped():
    r = _run([("c1", "pingdin-db", _A, "postgres:16")], {"postgres:16": _A})
    assert "OK       pingdin-db " in r.stdout and "/pingdin-db" not in r.stdout


def test_empty_project_is_not_an_error():
    r = _run([], {})
    assert r.returncode == 0 and "0 container(s) checked, 0 stale." in r.stdout


def test_a_compose_failure_is_passed_through_not_swallowed():
    r = _run([], {}, compose_fail="no configuration file provided: not found\n")
    assert r.returncode == 1 and "no configuration file" in r.stderr and "checked" not in r.stdout


def test_copy_is_identical_in_code_to_the_platform_repo_copy_when_both_are_present():
    """Only meaningful with both repos side by side (a dev machine / the
    sandbox); silently passes on a droplet, which has just one."""
    other = os.path.join(_ROOT, "..", "pingdin_platform", "scripts", "find_stale_containers.sh")
    if not os.path.exists(other):
        return
    body = lambda t: [l for l in t.splitlines() if not l.startswith("#")]
    assert body(open(other, encoding="utf-8").read()) == body(open(_SCRIPT, encoding="utf-8").read())


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
