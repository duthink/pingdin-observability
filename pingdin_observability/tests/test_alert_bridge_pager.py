"""
Run: python3 tests/test_alert_bridge_pager.py   (or pytest)

BUILD_LOG Journey 25. A critical alert that starts firing also rings the phone (Pushover, emergency priority); nothing else does. The pure half (formatting.py) is run directly; the wiring in
main.py, the compose passthrough, the example env and the new disk/swap rules are checked as text.
"""
import os
import re
import sys

_ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(_ROOT, "alert_bridge"))
import formatting as f  # noqa: E402


def _alert(name="PostgresDown", severity="critical", status="firing"):
    return {"status": status, "labels": {"alertname": name, "severity": severity}, "annotations": {"summary": "db is down"}}


def test_only_a_firing_critical_alert_pages():
    assert f.should_page(_alert())
    assert not f.should_page(_alert(status="resolved"))
    assert not f.should_page(_alert(severity="warning"))
    assert not f.should_page(_alert(severity="none"))
    assert not f.should_page({"status": "firing", "labels": {}})
    assert not f.should_page(_alert(name="Watchdog"))


def test_the_page_uses_emergency_priority_like_the_platforms_own_pager():
    p = f.pager_payload("tok", "usr", _alert())
    assert p["priority"] == 2 and p["retry"] == 60 and p["expire"] == 1800
    assert p["title"] == "Pingdin: PostgresDown" and p["message"] == "db is down" and p["token"] == "tok" and p["user"] == "usr"
    long = _alert(); long["annotations"]["summary"] = "x" * 5000
    assert len(f.pager_payload("t", "u", long)["message"]) == 1000


def test_main_pages_after_telegram_never_raises_and_does_nothing_without_keys():
    src = open(os.path.join(_ROOT, "alert_bridge", "main.py"), encoding="utf-8").read()
    assert "if should_page(alert):" in src and src.index("_send_telegram(format_alertmanager_alert(alert))") < src.index("if should_page(alert):")
    body = src.split("def _send_pager", 1)[1].split("\n\n\n", 1)[0]
    assert "if not PUSHOVER_TOKEN or not PUSHOVER_USER:" in body and "except httpx.HTTPError" in body and "return False" in body
    assert "should_page," in src and "pager_payload," in src       # imported from the module the Dockerfile already copies


def test_the_compose_file_and_example_env_carry_the_two_pager_settings_without_values():
    compose = open(os.path.join(_ROOT, "docker-compose.yml"), encoding="utf-8").read()
    assert "ALERT_BRIDGE_PUSHOVER_TOKEN: ${ALERT_BRIDGE_PUSHOVER_TOKEN:-}" in compose and "ALERT_BRIDGE_PUSHOVER_USER: ${ALERT_BRIDGE_PUSHOVER_USER:-}" in compose
    env = open(os.path.join(_ROOT, ".env.example"), encoding="utf-8").read()
    assert re.search(r"^ALERT_BRIDGE_PUSHOVER_TOKEN=$", env, re.M) and re.search(r"^ALERT_BRIDGE_PUSHOVER_USER=$", env, re.M)


def test_disk_and_swap_alerts_exist_with_the_right_severity():
    rules = open(os.path.join(_ROOT, "prometheus", "alert_rules.yml"), encoding="utf-8").read()
    for name, sev in (("DiskAlmostFull", "critical"), ("DiskFillingFast", "critical"), ("SwapInUse", "warning")):
        block = rules.split(f"- alert: {name}", 1)[1].split("- alert:", 1)[0]
        assert f"severity: {sev}" in block, name
    assert "predict_linear(" in rules.split("- alert: DiskFillingFast", 1)[1].split("- alert:", 1)[0]
    swap = rules.split("- alert: SwapInUse", 1)[1].split("- alert:", 1)[0]               # Journey 26: swap that is only full of idle pages must not fire it
    assert "node_vmstat_pswpin" in swap and "node_vmstat_pswpout" in swap and "for: 15m" in swap


def test_the_routine_checks_explain_how_to_switch_on_and_test_the_pager():
    doc = open(os.path.join(_ROOT, "docs", "ROUTINE_CHECKS.md"), encoding="utf-8").read()
    assert "## The pager: critical alerts also ring the phone" in doc and "PagerTest" in doc and "ALERT_BRIDGE_PUSHOVER_TOKEN" in doc


if __name__ == "__main__":
    import traceback
    bad = 0
    for k, v in list(globals().items()):
        if k.startswith("test_"):
            try:
                v()
            except Exception:
                bad += 1; print("FAILED", k); traceback.print_exc()
    print("done,", bad, "failed")
