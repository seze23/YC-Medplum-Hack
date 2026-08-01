"""Thin REST client for AgentPhone — SMS and iMessage only.

Voice is Twilio's job, all the way down to Deepgram. AgentPhone never sees
audio here; it sends the text side of the Patient Journey (confirmations,
reminders) and receives text replies (reschedule requests, confirmations).

Docs: https://docs.agentphone.ai

Two things worth knowing before you touch this file:

  1. Case convention is genuinely inconsistent between their two APIs, not a
     typo on our end. Outbound `POST /v1/messages` is snake_case
     (`to_number`, `from_number`). The inbound webhook payload (see
     agentphone_router.py) is camelCase (`data.from`, `data.message`).
  2. The signed string for webhook verification is `"{timestamp}.{raw_body}"`
     — a literal period, no spaces — hashed with HMAC-SHA256 using the
     webhook secret. Confirmed against their own Python example, not guessed.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from typing import Any

import httpx

AGENTPHONE_BASE_URL = "https://api.agentphone.ai/v1"


def _api_key() -> str:
    key = os.environ.get("AGENTPHONE_API_KEY")
    if not key:
        raise RuntimeError("AGENTPHONE_API_KEY not set")
    return key


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_api_key()}",
        "Content-Type": "application/json",
    }


def send_sms(to_number: str, body: str) -> dict[str, Any]:
    """Send a single SMS. Returns AgentPhone's message resource (snake_case).

    `from_number` is optional on their side — omit it and the account's one
    provisioned number is used automatically. Set AGENTPHONE_FROM_NUMBER only
    if the account ever has more than one.
    """
    payload: dict[str, Any] = {"to_number": to_number, "body": body}
    from_number = os.environ.get("AGENTPHONE_FROM_NUMBER")
    if from_number:
        payload["from_number"] = from_number

    resp = httpx.post(
        f"{AGENTPHONE_BASE_URL}/messages",
        headers=_headers(),
        json=payload,
        timeout=10.0,
    )
    resp.raise_for_status()
    return resp.json()


def send_appointment_confirmation(
    to_number: str,
    *,
    provider_name: str,
    when: str,
    clinic_name: str = "the clinic",
) -> dict[str, Any]:
    """Fired once engine/decision.py reaches NextAction.BOOK."""
    body = (
        f"Confirmed: your PT appointment with {provider_name} at {clinic_name} "
        f"on {when}. Reply RESCHEDULE if you need to change it."
    )
    return send_sms(to_number, body)


def send_reminder(
    to_number: str,
    *,
    provider_name: str,
    when: str,
    clinic_name: str = "the clinic",
) -> dict[str, Any]:
    """Fired by whatever scheduling job runs the T-24h reminder pass."""
    body = (
        f"Reminder: your appointment with {provider_name} at {clinic_name} "
        f"is {when}. Reply RESCHEDULE if you need to change it."
    )
    return send_sms(to_number, body)


def send_followup_checkin(
    to_number: str,
    *,
    provider_name: str,
    visit_date: str,
    clinic_name: str = "the clinic",
) -> dict[str, Any]:
    """Sent 2-3 days after a completed appointment — this is the text-channel
    half of CONTEXT.md's "Progress assessment" step. Timing rule lives in
    services/followups.py; the reply gets captured in agentphone_router.py's
    follow-up handling, which is the seam that eventually revises the patient
    scoring in engine/scoring.py.
    """
    body = (
        f"Hi, checking in on how you're doing after your visit with "
        f"{provider_name} at {clinic_name} on {visit_date}. Any change in "
        f"your symptoms since then? Reply here and we'll pass it along."
    )
    return send_sms(to_number, body)


def verify_signature(
    *, timestamp: str, raw_body: bytes, signature_header: str, secret: str
) -> bool:
    """Validate X-Webhook-Signature against X-Webhook-Timestamp + raw body.

    Constant-time comparison — this gates whether an inbound request is
    trusted, so a naive `==` here would be a timing side-channel.
    """
    if not signature_header.startswith("sha256="):
        return False
    signed_string = f"{timestamp}.".encode() + raw_body
    expected = hmac.new(secret.encode(), signed_string, hashlib.sha256).hexdigest()
    return hmac.compare_digest(f"sha256={expected}", signature_header)
