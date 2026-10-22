"""
alert_bridge/main.py — the one place an alert becomes a Telegram
message, for BOTH of this platform's alert sources:

1. POST /alertmanager — Prometheus Alertmanager's own webhook_config
   payload shape ({status, alerts: [...], groupLabels, commonAnnotations,
   ...}), fired by prometheus/alert_rules.yml's infra/service rules
   (PostgresDown, HighCPUUsage, etc.) via alertmanager/alertmanager.yml.

2. POST /owner-alert — the EXACT payload shape pingdin_platform's own
   capabilities/alerting/owner_alert.py already POSTs when
   OWNER_ALERT_WEBHOOK_URL is set: {subject, detail, context, source,
   timestamp}, optionally with an `Authorization: Bearer <token>`
   header. This endpoint is what OWNER_ALERT_WEBHOOK_URL should point
   at once it's set on the main droplet's own .env — Edge-side
   application failures (dashboard lens/widget errors, dead-lettered
   events) land in the SAME Telegram chat as infra alerts, one place,
   not two.

Deliberately one small standalone service, not folded into Grafana or
Alertmanager itself — matches this repo's own "atomic, independently
testable" posture, and keeps the Telegram bot token in exactly one
container's own env, nowhere else.

NEVER RAISES past its own request handler — same law
capabilities/alerting/owner_alert.py already states for itself. A
failure to reach Telegram must return a clean error to the caller
(Alertmanager/Edge will retry), never crash this process.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from formatting import (
    bearer_token_matches,
    delivery_status_code,
    format_alertmanager_alert,
    format_owner_alert,
)

logging.basicConfig(
    level=os.environ.get("PINGDIN_LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("alert_bridge")

app = FastAPI(title="pingdin-alert-bridge")

# BUILD_LOG Journey 8 — this service's OWN /metrics, so Prometheus can tell when
# the alert pipeline's last hop is down (prometheus.yml's `alert-bridge` job,
# alert_rules.yml's AlertBridgeDown). Fails open, like pingdin_platform's
# shared/metrics.py: a missing library must never stop alerts being delivered.
try:
    from prometheus_fastapi_instrumentator import Instrumentator

    Instrumentator(excluded_handlers=["/health", "/metrics"]).instrument(app).expose(
        app, endpoint="/metrics", include_in_schema=False,
    )
except ImportError:  # pragma: no cover - exercised only in a bare environment
    logging.getLogger("alert_bridge").warning(
        "prometheus-fastapi-instrumentator not installed; /metrics disabled"
    )

TELEGRAM_BOT_TOKEN = os.environ.get("ALERT_BRIDGE_TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("ALERT_BRIDGE_TELEGRAM_CHAT_ID")
# Matches OWNER_ALERT_WEBHOOK_TOKEN's own convention in
# capabilities/alerting/owner_alert.py — this service is the receiving
# end of that exact bearer-token check, not a new scheme.
OWNER_ALERT_WEBHOOK_TOKEN = os.environ.get("OWNER_ALERT_WEBHOOK_TOKEN")

TELEGRAM_API_TIMEOUT = 15.0


def _send_telegram(text: str) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.error("ALERT_BRIDGE_TELEGRAM_BOT_TOKEN/_CHAT_ID not set — cannot deliver: %s", text)
        return False
    try:
        resp = httpx.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text},
            timeout=TELEGRAM_API_TIMEOUT,
        )
        resp.raise_for_status()
        return True
    except httpx.HTTPError as e:
        logger.error("Telegram send failed: %s", e)
        return False


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/alertmanager")
async def alertmanager_webhook(request: Request):
    """Alertmanager's own payload shape (v4 webhook_config):
    https://prometheus.io/docs/alerting/latest/configuration/#webhook_config
    Iterates every alert in the batch — Alertmanager already groups
    related alerts together (alertmanager.yml's own group_by), so a
    batch is usually one incident, occasionally a few.
    """
    body = await request.json()
    alerts = body.get("alerts", [])
    if not alerts:
        return {"delivered": 0}

    delivered = 0
    for alert in alerts:
        if _send_telegram(format_alertmanager_alert(alert)):
            delivered += 1

    # Non-2xx when Telegram failed, so Alertmanager retries the batch instead
    # of treating it as delivered (see formatting.delivery_status_code).
    return JSONResponse(
        {"delivered": delivered, "received": len(alerts)},
        status_code=delivery_status_code(delivered, len(alerts)),
    )


@app.post("/owner-alert")
async def owner_alert_webhook(request: Request, authorization: str | None = Header(default=None)):
    """The receiving end of pingdin_platform's own
    capabilities/alerting/owner_alert.py `_send_webhook()` — same
    payload shape, same optional bearer-token check. Point
    OWNER_ALERT_WEBHOOK_URL at this endpoint (and OWNER_ALERT_WEBHOOK_TOKEN
    at the same value as this service's own OWNER_ALERT_WEBHOOK_TOKEN
    env var) to route Edge-side application alerts into this same
    Telegram chat.
    """
    if not bearer_token_matches(OWNER_ALERT_WEBHOOK_TOKEN, authorization):
        raise HTTPException(status_code=401, detail="invalid or missing bearer token")

    body = await request.json()
    delivered = _send_telegram(format_owner_alert(body))
    # 502 on failure so owner_alert.py's raise_for_status sees it (it logs; the
    # alert is not silently reported as sent).
    return JSONResponse({"delivered": delivered}, status_code=200 if delivered else 502)
