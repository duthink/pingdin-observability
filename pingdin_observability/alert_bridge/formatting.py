"""
alert_bridge/formatting.py — the pure, dependency-free half of alert_bridge
(BUILD_LOG Journey 8). No FastAPI, no httpx, no network: message text and
delivery-status decisions only, so every rule here is unit-testable with
nothing installed (main.py imports FastAPI/httpx and therefore cannot be
imported in a bare environment). One atomic unit per concern, reused by both
endpoints in main.py — same "small, independently testable" posture main.py's
own docstring states for the service as a whole.
"""

from __future__ import annotations

import hmac

WATCHDOG_ALERTNAME = "Watchdog"


def is_watchdog(alert: dict) -> bool:
    return (alert.get("labels") or {}).get("alertname") == WATCHDOG_ALERTNAME


def should_page(alert: dict) -> bool:
    """BUILD_LOG Journey 25 — a critical alert that has just STARTED firing also rings the phone (Pushover, emergency priority). Telegram is a silent chat message at 3 a.m.; when the
    main droplet is down it cannot page for itself, so this droplet has to. Resolved alerts, warnings and the always-on Watchdog never page."""
    labels = alert.get("labels") or {}
    return alert.get("status") == "firing" and labels.get("severity") == "critical" and not is_watchdog(alert)


def pager_payload(token: str, user: str, alert: dict) -> dict:
    """The Pushover form fields for one alert: emergency priority 2 (rings every 60 s until acknowledged, for up to 30 minutes), the same settings as the platform's own pager."""
    labels = alert.get("labels") or {}
    summary = (alert.get("annotations") or {}).get("summary", "(no summary)")
    name = labels.get("alertname", "unknown_alert")
    return {"token": token, "user": user, "title": f"Pingdin: {name}"[:250], "message": summary[:1000], "priority": 2, "retry": 60, "expire": 1800}


def format_alertmanager_alert(alert: dict) -> str:
    """One Alertmanager alert -> one Telegram message body.

    The Watchdog alert (prometheus/alert_rules.yml — always firing by design)
    gets its own wording: a red "FIRING" for something that is meant to fire
    forever would train the operator to ignore red messages."""
    status = alert.get("status", "unknown")
    labels = alert.get("labels") or {}
    annotations = alert.get("annotations") or {}
    alertname = labels.get("alertname", "unknown_alert")

    if is_watchdog(alert):
        if status == "firing":
            return ("\U0001F49A Alerting heartbeat — Prometheus, Alertmanager and this bridge are all working.\n"
                    "If this daily message stops arriving, alerting itself may be broken: "
                    "check Prometheus and Alertmanager directly.")
        return ("\U0001F6A8 Alerting heartbeat RESOLVED — Prometheus stopped firing its always-on "
                "Watchdog rule. Rule evaluation may be broken: check Prometheus (Status > Rules).")

    emoji = "\U0001F534" if status == "firing" else "\u2705"
    severity = labels.get("severity", "unknown")
    summary = annotations.get("summary", "(no summary)")
    instance = labels.get("instance", "")
    lines = [f"{emoji} [{status.upper()}] {alertname} ({severity})", summary]
    if instance:
        lines.append(f"instance: {instance}")
    return "\n".join(lines)


def format_owner_alert(body: dict) -> str:
    """pingdin_platform's capabilities/alerting/owner_alert.py payload
    ({subject, detail, context, source, timestamp}) -> Telegram text."""
    subject = body.get("subject", "(no subject)")
    detail = body.get("detail", "")
    context = body.get("context") or {}
    source = body.get("source") or "unknown-deployment"
    lines = [f"\u26A0\uFE0F [{source}] {subject}", detail]
    if context:
        lines.append("")
        lines.extend(f"{k}: {v}" for k, v in context.items())
    return "\n".join(lines)


def delivery_status_code(delivered: int, received: int) -> int:
    """HTTP status for a webhook batch. Alertmanager (and owner_alert.py's
    raise_for_status) only retry on a non-2xx response, so a batch where
    Telegram delivery failed MUST NOT answer 200 — before Journey 8 it did,
    and a Telegram outage silently dropped every alert in flight. 502 = the
    upstream (Telegram) failed; retry. An empty batch is a success."""
    return 200 if delivered >= received else 502


def bearer_token_matches(expected_token: str | None, authorization_header: str | None) -> bool:
    """True if no token is configured (open, Tailscale-bound endpoint — the
    pre-existing behaviour) or the header carries exactly `Bearer <token>`.
    Constant-time comparison: this is a credential check."""
    if not expected_token:
        return True
    if not authorization_header:
        return False
    return hmac.compare_digest(authorization_header.encode(), f"Bearer {expected_token}".encode())
