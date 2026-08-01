"""When to flag the post-appointment check-in.

`due_for_followup` is pure — no I/O, no Medplum, no clock unless you pass one
in — same discipline as engine/decision.py. It lives here rather than in
engine/ only because he's actively pushing to that package right now and this
doesn't need to block on his structure; it's a reasonable move into engine/
later if that ends up being the cleaner home.

send_due_followups creates a Task rather than sending a text — outbound
AgentPhone is blocked pending A2P 10DLC registration (see
agentphone_router.py's module docstring for the full reasoning). A human
does the check-in call/text for now; swap the create_task call for
services.agentphone.send_followup_checkin once registration clears, nothing
else here needs to change.

This module doesn't fetch appointments itself. Whatever eventually reads
completed appointments out of Medplum should build a list of CompletedVisit
and hand it to send_due_followups — the Medplum query is his side, the timing
rule and the Task creation are this file's.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

FOLLOWUP_WINDOW = (timedelta(days=2), timedelta(days=3))


@dataclass
class CompletedVisit:
    phone: str
    provider_name: str
    visit_date: datetime
    clinic_name: str = "the clinic"
    followup_sent: bool = False


def due_for_followup(visit_date: datetime, *, now: datetime | None = None) -> bool:
    """True when `now` falls 2-3 days after the visit, inclusive."""
    now = now or datetime.now(visit_date.tzinfo)
    elapsed = now - visit_date
    return FOLLOWUP_WINDOW[0] <= elapsed <= FOLLOWUP_WINDOW[1]


def send_due_followups(
    visits: list[CompletedVisit], *, now: datetime | None = None
) -> list[CompletedVisit]:
    """Flags every visit that's due for a check-in and hasn't gotten one.

    Creates a Task per visit rather than texting — see module docstring.
    Mutates and returns the same list (marks followup_sent), mirroring
    engine/decision.py's mutate-and-return pattern. Imports are local so
    tests can monkeypatch the Task creation without touching module-load
    order.

    Uses _safe_create_task, not the raw create_task binding — one visit's
    Task-creation failure (a bad downstream email, once this dispatches to
    a real flow) must not abort every other visit still waiting in the
    batch. Caught during a full-codebase review: this was calling the
    unprotected create_task directly, unlike every other caller.
    """
    from services.agentphone_router import _safe_create_task, mark_followup_sent

    sent = []
    for visit in visits:
        if visit.followup_sent:
            continue
        if due_for_followup(visit.visit_date, now=now):
            _safe_create_task(
                phone=visit.phone,
                patient=None,
                category="FOLLOWUP_DUE",
                detail=(
                    f"2-3 day check-in due: {visit.provider_name}, "
                    f"visit on {visit.visit_date.strftime('%B %d')} "
                    f"at {visit.clinic_name}."
                ),
            )
            mark_followup_sent(visit.phone)
            visit.followup_sent = True
            sent.append(visit)
    return sent
