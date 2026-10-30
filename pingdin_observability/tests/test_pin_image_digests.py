"""
Run: python3 tests/test_pin_image_digests.py   (or pytest)

BUILD_LOG Journey 8. scripts/pin_image_digests.py is a verbatim copy of the
one in pingdin_platform (its Journey 377); this pins its classification
against THIS repo's real docker-compose.yml, and that the two copies stay
identical in behaviour (the fixed-vs-floating rule is the whole point).
"""

import importlib.util
import os
import sys
from unittest import mock

_ROOT = os.path.join(os.path.dirname(__file__), "..")
_spec = importlib.util.spec_from_file_location("pin_image_digests", os.path.join(_ROOT, "scripts", "pin_image_digests.py"))
pin = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pin)


def test_real_compose_classification():
    images = {n: s["image"] for n, s in pin.load_services(os.path.join(_ROOT, "docker-compose.yml")).items() if "image" in s}
    floating = {n for n, i in images.items() if pin.is_floating_tag(i)}
    # Grafana is the one image this repo already pinned (after a real regression)
    assert images["pingdin-obs-grafana"] == "grafana/grafana:12.1.1"
    assert "pingdin-obs-grafana" not in floating
    # Obs journey 31: the others float until `python3 scripts/pin_image_digests.py --write` has run on the server and the file came back;
    # from then on they are digests. Either way none of them is classified as a fixed tag by mistake.
    for name in ("pingdin-obs-prometheus", "pingdin-obs-alertmanager", "pingdin-obs-loki", "pingdin-obs-tempo", "pingdin-obs-node-exporter", "pingdin-obs-cadvisor"):
        assert name in floating or "@sha256:" in images[name], name
    # alert_bridge is built locally: nothing to pin, and it must have no image: key
    assert "image" not in pin.load_services(os.path.join(_ROOT, "docker-compose.yml"))["pingdin-alert-bridge"]


def test_floating_rule():
    for img in ("x/y:latest", "x/y:main-stable", "x/y", "reg.io/a/b"):
        assert pin.is_floating_tag(img), img
    for img in ("postgres:16", "caddy:2", "grafana/grafana:12.1.1", "reg.io:5000/a/b:1.2.3"):
        assert not pin.is_floating_tag(img), img


def test_main_prints_pasteable_line_and_skips_pinned_and_built_services():
    import io
    import tempfile

    import yaml

    compose = {"services": {
        "pinned": {"image": "postgres:16"},
        "floating": {"image": "prom/node-exporter:latest", "container_name": "fl"},
        "built": {"build": "."},
    }}
    with tempfile.NamedTemporaryFile("w", suffix=".yml", delete=False) as fh:
        yaml.safe_dump(compose, fh)
    buf, real = io.StringIO(), sys.stdout
    try:
        sys.stdout = buf
        with mock.patch.object(pin, "running_digest", return_value="prom/node-exporter@sha256:" + "b" * 64):
            rc = pin.main(["prog", fh.name])
    finally:
        sys.stdout = real
        os.unlink(fh.name)
    out = buf.getvalue()
    assert rc == 0 and "image: prom/node-exporter@sha256:" + "b" * 64 in out
    assert "pinned" not in out.replace("already pinned", "") and "built" not in out


def test_running_digest_degrades_to_none_without_docker():
    with mock.patch.object(pin.subprocess, "run", side_effect=FileNotFoundError):
        assert pin.running_digest("x") is None


def test_copy_is_identical_in_logic_to_the_platform_repo_copy_when_both_are_present():
    """Only meaningful in a checkout that has both repos side by side (the
    sandbox / a dev machine); skipped on a droplet, which has just one."""
    other = os.path.join(_ROOT, "..", "pingdin_platform", "scripts", "pin_image_digests.py")
    if not os.path.exists(other):
        return
    strip = lambda t: "\n".join(t.split('"""', 2)[2:])  # compare code, not the header docstring
    assert strip(open(other, encoding="utf-8").read()) == strip(open(os.path.join(_ROOT, "scripts", "pin_image_digests.py"), encoding="utf-8").read())



# ── --write (Obs journey 31: this copy had lost it; the platform copy gained it in its Journey 501) ─────────────────────────

def test_apply_pins_rewrites_only_the_floating_image_lines_and_keeps_everything_else():
    text = "services:\n  a:\n    image: prom/prometheus:latest   # metrics\n    restart: always\n  b:\n    image: grafana/grafana:12.1.1\n"
    new, done = pin.apply_pins(text, {"prom/prometheus:latest": "prom/prometheus@sha256:abc"})
    assert done == ["prom/prometheus:latest"]
    assert "image: prom/prometheus@sha256:abc   # metrics" in new and "image: grafana/grafana:12.1.1" in new and "restart: always" in new
    assert new.replace("prom/prometheus@sha256:abc", "prom/prometheus:latest") == text


def test_write_pins_the_running_digest_into_the_file_and_keeps_a_backup():
    import tempfile
    d = tempfile.mkdtemp()
    compose = os.path.join(d, "docker-compose.yml")
    open(compose, "w").write("services:\n  p:\n    image: prom/prometheus:latest\n    container_name: p\n  g:\n    image: grafana/grafana:12.1.1\n")
    with mock.patch.object(pin, "running_digest", return_value="prom/prometheus@sha256:feedbeef"):
        assert pin.main(["x", compose, "--write"]) == 0
    text = open(compose).read()
    assert "image: prom/prometheus@sha256:feedbeef" in text and "image: grafana/grafana:12.1.1" in text
    assert "image: prom/prometheus:latest" in open(compose + ".bak").read()


def test_the_two_copies_of_the_script_stay_identical_apart_from_their_header():
    """Keep the two copies identical (the docstring says so): this repo's copy must carry the --write that the platform's has."""
    src = open(os.path.join(_ROOT, "scripts", "pin_image_digests.py")).read()
    assert "def apply_pins(" in src and '"--write" in argv' in src

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
