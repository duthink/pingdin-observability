"""
Run: python3 tests/test_alert_bridge_formatting.py   (or pytest)

BUILD_LOG Journey 8. alert_bridge/formatting.py is the pure half of the
bridge — importable with nothing installed (main.py needs FastAPI/httpx).
Pins the defects found reading the service: delivery failure answered HTTP 200
(Alertmanager only retries on non-2xx, so a Telegram outage silently dropped
alerts), a non-constant-time credential comparison, and the always-firing
Watchdog being rendered as a red incident.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "alert_bridge"))
import formatting as f  # noqa: E402


def test_failed_delivery_is_not_a_2xx_so_senders_retry():
    assert f.delivery_status_code(delivered=0, received=1) == 502
    assert f.delivery_status_code(delivered=1, received=3) == 502  # partial: retry the batch
    assert f.delivery_status_code(delivered=3, received=3) == 200
    assert f.delivery_status_code(delivered=0, received=0) == 200  # empty batch is not a failure


def test_bearer_token_check():
    assert f.bearer_token_matches(None, None)  # unconfigured: open, as before
    assert f.bearer_token_matches("", "anything")
    assert f.bearer_token_matches("s3cret", "Bearer s3cret")
    assert not f.bearer_token_matches("s3cret", None)
    assert not f.bearer_token_matches("s3cret", "Bearer wrong")
    assert not f.bearer_token_matches("s3cret", "s3cret")  # missing scheme
    assert not f.bearer_token_matches("s3cret", "Bearer s3cret ")  # exact match only


def test_regular_alert_text():
    txt = f.format_alertmanager_alert({
        "status": "firing",
        "labels": {"alertname": "PostgresDown", "severity": "critical", "instance": "db:9187"},
        "annotations": {"summary": "pingdin-db is not responding"},
    })
    assert txt.splitlines()[0].endswith("[FIRING] PostgresDown (critical)")
    assert "pingdin-db is not responding" in txt and "instance: db:9187" in txt


def test_resolved_alert_uses_the_resolved_marker():
    txt = f.format_alertmanager_alert({"status": "resolved", "labels": {"alertname": "X", "severity": "warning"}})
    assert "[RESOLVED] X (warning)" in txt and "(no summary)" in txt


def test_alert_with_missing_labels_and_annotations_does_not_raise():
    f.format_alertmanager_alert({})
    f.format_alertmanager_alert({"labels": None, "annotations": None})


def test_watchdog_is_a_green_heartbeat_not_a_red_incident():
    firing = f.format_alertmanager_alert({"status": "firing", "labels": {"alertname": "Watchdog", "severity": "none"}})
    assert "heartbeat" in firing.lower() and "FIRING" not in firing and "\U0001F534" not in firing
    resolved = f.format_alertmanager_alert({"status": "resolved", "labels": {"alertname": "Watchdog"}})
    assert "RESOLVED" in resolved and "\U0001F6A8" in resolved  # a Watchdog that STOPS is the emergency


def test_owner_alert_text_matches_the_platform_payload_shape():
    txt = f.format_owner_alert({"subject": "lens fetch failed", "detail": "boom",
                                "context": {"tenant_id": "t1"}, "source": "pingdin-managed"})
    assert txt.splitlines()[0].endswith("[pingdin-managed] lens fetch failed")
    assert "boom" in txt and "tenant_id: t1" in txt
    assert "[unknown-deployment]" in f.format_owner_alert({"subject": "s"})  # source optional
    f.format_owner_alert({})  # never raises



def test_main_py_wires_the_status_logic_into_both_handlers():
    """main.py cannot be imported here (FastAPI/httpx absent), so read its
    source: each webhook handler must answer through JSONResponse with a
    computed status, never a bare dict (which FastAPI turns into 200 — the
    original bug). The one allowed bare dict is the empty-batch early return."""
    import ast

    src = open(os.path.join(os.path.dirname(__file__), "..", "alert_bridge", "main.py"), encoding="utf-8").read()
    tree = ast.parse(src)
    handlers = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef)}
    assert {"alertmanager_webhook", "owner_alert_webhook"} <= set(handlers)
    for name, fn in handlers.items():
        rets = [r for r in ast.walk(fn) if isinstance(r, ast.Return)]
        bare = [r for r in rets if isinstance(r.value, ast.Dict)]
        limit = 1 if name == "alertmanager_webhook" else 0  # empty-batch early return
        assert len(bare) <= limit, f"{name} returns a bare dict (implicit HTTP 200) on a delivery path"
        assert any(isinstance(r.value, ast.Call) and getattr(r.value.func, "id", "") == "JSONResponse" for r in rets), name
    assert "delivery_status_code(" in src

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
