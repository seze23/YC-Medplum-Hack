"""AgentPhone inbound webhook — SMS/iMessage side of the Patient Journey.

Confirmed live against the account: inbound works immediately, outbound is
blocked pending A2P 10DLC carrier registration. AgentPhone's own error says
registration takes "about 5 minutes" — that's the time to submit the form,
not the carrier review, which runs days regardless of vendor (same wall
Twilio SMS hit earlier in this project). Not worth waiting on for a
same-day demo.

So this router does not reply by text at all right now. Every inbound
message becomes a Task instead of an auto-reply — a human closes the loop.
That's not a workaround bolted on for this outage: it's the exact same
Human-In-The-Loop mechanism CONTEXT.md already specifies for low-confidence
cases ("create a Task, assign human review"), just triggered by a channel
limitation instead of a confidence score. send_sms/send_appointment_confirmation
/send_reminder in agentphone.py are untouched and correct — they're just
unused until registration clears, and this file goes back to calling them
at that point rather than create_task.

Two wrinkles, matching the notes in agentphone.py:

  1. Inbound payload is camelCase (`data.from`, `data.message`) — different
     convention from the outbound send call. Genuinely inconsistent on
     AgentPhone's side, not a bug here.
  2. SMS/iMessage events are fire-and-forget: AgentPhone wants a 200 and
     nothing else. (Only voice events need a JSON `{"text": ...}` body back —
     not this channel, that's Twilio -> Pipecat's problem.)

Wired today into main.py (a demo-only entrypoint — see its docstring) via:

    app.include_router(agentphone_router.router)

Once his real app exists, merge this router into it the same way.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Protocol

from fastapi import APIRouter, HTTPException, Request, Response

from services.agentphone import verify_signature

logger = logging.getLogger(__name__)

router = APIRouter()


class PatientLookup(Protocol):
    def __call__(self, phone: str) -> dict | None: ...


def _default_patient_lookup(phone: str) -> dict | None:
    """Stand-in until services/medplum.py exists.

    Real version: GET /fhir/R4/Patient?telecom=<phone>, return the first
    match as a dict, or None if nobody's found. Swap the module-level
    `patient_lookup` binding below once that call exists — don't edit the
    handler itself.
    """
    return None


patient_lookup: PatientLookup = _default_patient_lookup


class ProgressRecorder(Protocol):
    def __call__(self, phone: str, raw_reply: str) -> None: ...


def _default_record_progress_update(phone: str, raw_reply: str) -> None:
    """Stand-in until engine/scoring.py exposes an entry point.

    This is the seam that "revises the patient scoring system" — a reply to
    a 2-3 day follow-up check-in should eventually become an Observation in
    Medplum and feed the acceptance/follow-up-completion factors in
    engine/scoring.py. Deliberately not implementing that logic here: scoring
    changes belong in engine/, not in a webhook handler.
    """
    pass


record_progress_update: ProgressRecorder = _default_record_progress_update


class TaskCreator(Protocol):
    def __call__(
        self, *, phone: str, patient: dict | None, category: str, detail: str
    ) -> None: ...


def _default_create_task(
    *, phone: str, patient: dict | None, category: str, detail: str
) -> None:
    """Stand-in until services/medplum.py exists.

    Real version: POST a Medplum Task (status=requested), which is what
    makes it show up on the dashboard for a human to act on. This function
    is the entire reply mechanism right now — see the module docstring for
    why outbound text isn't an option today.
    """
    print(f"[TASK STUB] {category} from {phone} (patient={patient}): {detail!r}")


create_task: TaskCreator = _default_create_task


def _safe_record_progress_update(phone: str, raw_reply: str) -> None:
    """AgentPhone's SMS webhooks are fire-and-forget and want a 200
    regardless. record_progress_update can do real I/O once wired to
    Medplum, and I/O fails sometimes — that shouldn't turn into a 500 for an
    inbound text that was otherwise handled fine. Logged with full
    traceback (logger.exception), not silently dropped.
    """
    try:
        record_progress_update(phone, raw_reply)
    except Exception:
        logger.exception("record_progress_update failed for %s", phone)


def _safe_create_task(
    *, phone: str, patient: dict | None, category: str, detail: str
) -> None:
    """Same reasoning as _safe_record_progress_update. create_task is the
    one call in this router that can trigger real downstream I/O today (the
    demo's create_task fires two email sends for CANCELLATION) — a Gmail
    hiccup shouldn't cost the whole webhook response, or the Task record
    that was already appended before the email attempt.

    Deliberately scoped to just this call, not a blanket try/except around
    the whole handler — classification bugs (patient_lookup throwing, a
    genuine logic error) should still surface loudly during testing. Only
    the known-flaky downstream I/O gets swallowed-and-logged.
    """
    try:
        create_task(phone=phone, patient=patient, category=category, detail=detail)
    except Exception:
        logger.exception("create_task failed for %s (%s)", phone, category)


# Phones we've sent a follow-up check-in Task for and are waiting on a reply
# from. In-memory only — good enough for a single demo process. Once Medplum
# is wired in, replace with a real query ("does this patient have a completed
# Encounter in the last 3 days with no logged progress Observation yet?")
# rather than tracking it here at all.
_awaiting_progress_update: set[str] = set()


def mark_followup_sent(phone: str) -> None:
    """Call right after a follow-up check-in Task is created for this phone."""
    _awaiting_progress_update.add(phone)


# Deliberately minimal keyword matching for the demo. Anything that isn't one
# of these falls through to a generic inquiry Task rather than attempting to
# parse free text here — that's the LLM's job on the voice side, not this
# router's. Cancel and reschedule are split because they're different Medplum
# writes (Appointment.status=cancelled vs. a new booking search), not because
# the demo needs to distinguish them cosmetically.
# Checked before every other category, including a pending progress-update
# reply — same "safety overrides everything else" principle as
# engine/redflags.py on the voice side. Deliberately tight: broad words like
# "pain" or "worse" would false-positive constantly on a text channel with
# no clinical triage behind it, unlike the voice side's red-flag classifier.
_URGENT_KEYWORDS = {"urgent", "emergency", "911", "help"}

_CANCEL_KEYWORDS = {"cancel"}
_RESCHEDULE_KEYWORDS = {"reschedule", "change"}
_CONFIRM_KEYWORDS = {"confirm", "yes", "y"}

_WORD_RE = re.compile(r"[a-z']+")


def _words(text: str) -> set[str]:
    """Whole-word match, not substring. A naive `"y" in text.lower()` matches
    almost any sentence containing that letter ("my", "any", "yesterday") —
    caught by services/test_agentphone_router.py before this ever hit a demo.
    """
    return set(_WORD_RE.findall(text.lower()))


@router.post("/agentphone/webhook")
async def agentphone_webhook(request: Request) -> Response:
    raw_body = await request.body()

    secret = os.environ.get("AGENTPHONE_WEBHOOK_SECRET")
    if secret:
        ok = verify_signature(
            timestamp=request.headers.get("X-Webhook-Timestamp", ""),
            raw_body=raw_body,
            signature_header=request.headers.get("X-Webhook-Signature", ""),
            secret=secret,
        )
        if not ok:
            raise HTTPException(status_code=401, detail="bad signature")
    # No secret configured yet -> skip verification. Fine for local dev
    # against a tunnel only you know the URL of; not fine once this URL is
    # shared or public. Set AGENTPHONE_WEBHOOK_SECRET before the demo.

    payload = await request.json()

    if payload.get("event") != "agent.message":
        return Response(status_code=200)
    if payload.get("channel") not in ("sms", "imessage"):
        return Response(status_code=200)

    data = payload.get("data", {})
    from_number: str = data.get("from", "")
    message: str = (data.get("message") or "").strip()

    if from_number and message:
        _handle_inbound(from_number, message)

    return Response(status_code=200)


def _handle_inbound(from_number: str, message: str) -> None:
    """Classifies intent and creates a Task. Does not reply — see module
    docstring for why. A human (or, once registration clears, an automated
    text) does the actual outreach; this function's only job is making sure
    the right thing lands on the dashboard immediately.
    """
    patient = patient_lookup(from_number)

    # Absolute first check, before even a pending progress-update reply.
    # Fires whether or not patient_lookup found a match — an unknown number
    # saying something urgent must never be silently dropped just because
    # it doesn't match a known record.
    if _words(message) & _URGENT_KEYWORDS:
        _safe_create_task(
            phone=from_number,
            patient=patient,
            category="URGENT_CONCERN",
            detail=message,
        )
        return

    # Checked next — a follow-up reply is free text
    # ("shoulder's still tight but better"), not a keyword match, so it can't
    # go through the same branch as cancel/reschedule/confirm below.
    if from_number in _awaiting_progress_update:
        _awaiting_progress_update.discard(from_number)
        _safe_record_progress_update(from_number, message)
        _safe_create_task(
            phone=from_number,
            patient=patient,
            category="PROGRESS_UPDATE",
            detail=message,
        )
        return

    words = _words(message)

    if words & _CANCEL_KEYWORDS:
        category = "CANCELLATION"
        # This router never calls cancellation_flow/reschedule_flow/etc.
        # directly — create_task's implementation decides what a category
        # means. Today that's services/demo_fixtures.py's demo_create_task,
        # dispatching to the real flow files with fixture data. Once his
        # Medplum-backed create_task exists, it should mirror that same
        # dispatch-by-category — nothing here needs to change either way.
    elif words & _RESCHEDULE_KEYWORDS:
        category = "RESCHEDULE_REQUEST"
    elif words & _CONFIRM_KEYWORDS:
        category = "CONFIRMATION"
    else:
        category = "GENERAL_INQUIRY"

    _safe_create_task(phone=from_number, patient=patient, category=category, detail=message)
