"""
Proactive anomaly alerts (scripts/alerts.py).

The dashboard is something you have to go and look at. This module flips that around:
when a complaint theme spikes past its normal range, it pushes an immediate alert to a
team chat (Slack or Discord). That "tell me before it gets worse" behaviour is the
decision-oriented angle that makes this more than a passive dashboard.

It reuses the SAME anomaly detector the dashboard uses (insights.detect_anomalies), so
there's one definition of "a spike", not two that can drift apart.

Design:
  - Webhook URL comes from the env var ALERT_WEBHOOK_URL. No URL set -> no-op (handy
    for local runs and CI).
  - The send is wrapped in retries, because a flaky webhook shouldn't lose an alert.
  - The sender is injectable, so tests verify the logic with zero network calls.
  - Slack and Discord both accept a simple {"text"/"content": "..."} JSON POST.

Usage:
    ALERT_WEBHOOK_URL=https://hooks.slack.com/... python scripts/alerts.py
"""
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from scripts.database import get_connection, init_db
from scripts.insights import detect_anomalies
from scripts.retry import with_retries


def format_alert(anomaly: dict) -> str:
    """Turn one anomaly dict into a human, chat-ready line."""
    jump = ""
    if anomaly.get("baseline"):
        # How big is the spike vs the recent normal? (e.g. "+217%"). Only show it when
        # it's an actual increase — anomalies always are, but guard against odd inputs.
        pct = (anomaly["count"] - anomaly["baseline"]) / anomaly["baseline"] * 100
        if pct > 0:
            jump = f"  (+{pct:.0f}% vs normal)"
    return (f":rotating_light: {anomaly['brand']} — '{anomaly['theme']}' complaints spiked to "
            f"{anomaly['count']} in {anomaly['week']}{jump}. Recent normal ~{anomaly.get('baseline','?')}.")


def _default_sender(url: str, message: str) -> None:
    """POST the message to a Slack/Discord-style webhook. Retried on transient errors."""
    import httpx

    @with_retries(max_attempts=3, base_delay=1.0, exceptions=(httpx.HTTPError,))
    def _post():
        # "text" is Slack's field; "content" is Discord's. Sending both is harmless and
        # makes the same payload work on either platform.
        resp = httpx.post(url, json={"text": message, "content": message}, timeout=10.0)
        resp.raise_for_status()

    _post()


def check_and_alert(conn, sender=None, webhook_url=None) -> list:
    """Find anomalies and dispatch one alert each. Returns the messages it sent.

    `sender(url, message)` is injectable for tests. With no sender and no configured
    webhook URL, it detects and formats but sends nothing (a safe dry run).
    """
    url = webhook_url or os.getenv("ALERT_WEBHOOK_URL")
    send = sender or (_default_sender if url else None)

    messages = []
    for anomaly in detect_anomalies(conn):
        message = format_alert(anomaly)
        messages.append(message)
        if send:
            try:
                send(url, message)
            except Exception as exc:  # one bad send shouldn't sink the rest
                print(f"  [alert] failed to send: {exc}")
    return messages


def main():
    conn = get_connection()
    init_db(conn)
    messages = check_and_alert(conn)
    conn.close()

    if not messages:
        print("No anomalies to alert on. All quiet.")
        return
    configured = bool(os.getenv("ALERT_WEBHOOK_URL"))
    where = "sent to webhook" if configured else "DRY RUN (set ALERT_WEBHOOK_URL to send)"
    print(f"{len(messages)} alert(s) — {where}:\n")
    for m in messages:
        print(" ", m)


if __name__ == "__main__":
    main()
