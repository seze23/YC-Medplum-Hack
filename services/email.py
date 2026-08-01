"""Generic SMTP client. Works with a Gmail App Password, SendGrid's SMTP
relay, Mailgun's SMTP relay, or anything else that speaks SMTP — deliberately
not tied to one vendor's REST API, so switching providers later is an env var
change (SMTP_HOST/PORT), not a code change.

This exists because outbound AgentPhone/Twilio SMS is blocked pending A2P
10DLC registration (see agentphone_router.py). Email is the outbound channel
until that clears.
"""

from __future__ import annotations

import os
import smtplib
from email.mime.text import MIMEText
from email.utils import formataddr


def _send(to_email: str, subject: str, body: str) -> None:
    host = os.environ["SMTP_HOST"]
    port = int(os.environ.get("SMTP_PORT", "587"))
    username = os.environ["SMTP_USERNAME"]
    password = os.environ["SMTP_APP_PASSWORD"]
    from_name = os.environ.get("SMTP_FROM_NAME", "Relay")

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = formataddr((from_name, username))
    msg["To"] = to_email

    with smtplib.SMTP(host, port) as server:
        server.starttls()
        server.login(username, password)
        server.send_message(msg)


def send_waitlist_offer(
    to_email: str,
    *,
    provider_name: str,
    when: str,
    clinic_name: str = "the clinic",
    hold_minutes: int = 60,
) -> None:
    """Sent to a highly-ranked waitlisted patient right after a cancellation
    frees a slot. hold_minutes should match the 45-90 min window this needs
    to go out within — that timing is the caller's job, not this function's.
    """
    subject = f"A spot just opened up at {clinic_name}"
    body = (
        f"Hi,\n\nA spot with {provider_name} on {when} just opened up. Based "
        f"on your waitlist request, we wanted to offer it to you first.\n\n"
        f"Reply within {hold_minutes} minutes to claim it, or it goes to the "
        f"next person on the list.\n\n— {clinic_name}"
    )
    _send(to_email, subject, body)


def send_reschedule_options(
    to_email: str,
    *,
    provider_name: str,
    options: list[str],
    clinic_name: str = "the clinic",
) -> None:
    """Sent when a patient texts in wanting to reschedule. `options` is a
    list of human-readable time slots ("Wednesday at 10:00 AM") — Medplum
    Slot resources once real, fixture strings for the demo.
    """
    bullet_list = "\n".join(f"- {opt}" for opt in options)
    subject = f"New times available with {provider_name}"
    body = (
        f"Hi,\n\nHere are the next available times with {provider_name} at "
        f"{clinic_name}:\n\n{bullet_list}\n\n"
        f"Reply with the one that works best and we'll get you booked.\n\n"
        f"— {clinic_name}"
    )
    _send(to_email, subject, body)


def send_urgent_alert(
    to_email: str, *, from_phone: str, patient_name: str | None, message: str
) -> None:
    """Sent to clinic STAFF, not the patient — the text-channel parallel to
    engine/redflags.py's emergency handling on the voice side. A generic
    Task is easy to miss in a list; this is meant to interrupt someone.
    """
    subject = f"URGENT: message from {patient_name or from_phone} needs review"
    body = (
        f"A patient texted something flagged urgent and needs a human to "
        f"look at this now, not whenever the Task queue gets checked.\n\n"
        f"From: {patient_name or 'Unknown patient'} ({from_phone})\n"
        f"Message: {message!r}\n\n"
        f"This is a staff notification only — not a substitute for "
        f"911/emergency services if this is a medical emergency."
    )
    _send(to_email, subject, body)


def send_confirmation_ack(
    to_email: str, *, provider_name: str, when: str, clinic_name: str = "the clinic"
) -> None:
    """Sent when a patient texts back a confirmation. Same recipient as the
    exit survey — one email address per patient, one identity across every
    flow they touch, not a different inbox per feature.
    """
    subject = f"You're confirmed with {provider_name}"
    body = (
        f"Hi,\n\nThanks for confirming — you're all set for your visit "
        f"with {provider_name} at {clinic_name} on {when}. See you then!\n\n"
        f"— {clinic_name}"
    )
    _send(to_email, subject, body)


def send_referral_request(
    to_email: str, *, provider_name: str, clinic_name: str = "the clinic"
) -> None:
    """Sent when Stedi eligibility comes back with referral_required=True and
    no referral on file (shared/state.py's Insurance.referral_required /
    referral_valid_through). Catching this before the visit, not after,
    is the whole point — avoids a denied claim surprising the patient later.
    """
    subject = f"A referral is needed before your visit with {provider_name}"
    body = (
        f"Hi,\n\nYour insurance requires a referral for your upcoming visit "
        f"with {provider_name} at {clinic_name}, and we don't have one on "
        f"file yet.\n\nPlease ask your primary care provider to send a "
        f"referral to {clinic_name} as soon as possible so your visit isn't "
        f"delayed.\n\n— {clinic_name}"
    )
    _send(to_email, subject, body)


def send_cancellation_exit_survey(to_email: str, *, clinic_name: str = "the clinic") -> None:
    """Sent to the patient who cancelled. Qualitative, not structured —
    the point is collecting a reason, not filling a form field."""
    subject = "Quick question about your cancelled appointment"
    body = (
        f"Hi,\n\nWe noticed you cancelled your upcoming appointment. We'd "
        f"love to understand why, so we can make {clinic_name} easier to "
        f"work with:\n\n"
        f"- Was the time or location inconvenient?\n"
        f"- Was cost or insurance a factor?\n"
        f"- Anything else we should know?\n\n"
        f"Just reply to this email — takes 30 seconds and helps a lot.\n\n"
        f"— {clinic_name}"
    )
    _send(to_email, subject, body)
