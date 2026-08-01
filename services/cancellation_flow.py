"""What happens when a patient cancels via text. Ties together:

  - engine/patient_score.py's ranking (pure)
  - services/email.py's sends (I/O)

Two emails go out per cancellation:

  1. To the patient who cancelled — the exit survey (why'd you cancel).
  2. To the top-ranked waitlisted patients — the freed-slot offer.

Not yet wired into agentphone_router.py's CANCELLATION branch — that needs
the cancelling patient's email and the freed appointment's provider/time,
both of which come from Medplum, which doesn't exist here yet. Wire
`handle_cancellation` in once `patient_lookup` in agentphone_router.py
returns something real.
"""

from __future__ import annotations

import logging
from typing import Protocol

from engine.patient_score import ScoringInput, rank_waitlist
from services.email import send_cancellation_exit_survey, send_waitlist_offer

logger = logging.getLogger(__name__)


class WaitlistSource(Protocol):
    def __call__(
        self, *, specialty: str | None = None
    ) -> list[tuple[str, str, ScoringInput]]:
        """Returns (patient_id, email, ScoringInput) for everyone waitlisted."""
        ...


def _default_get_waitlist_candidates(
    *, specialty: str | None = None
) -> list[tuple[str, str, ScoringInput]]:
    """Stand-in until services/medplum.py exists — same pattern as
    patient_lookup in agentphone_router.py. Real version queries Medplum for
    patients with an open waitlist flag, filtered by specialty if given.
    """
    return []


get_waitlist_candidates: WaitlistSource = _default_get_waitlist_candidates


def handle_cancellation(
    *,
    cancelling_patient_email: str | None,
    freed_slot_provider: str,
    freed_slot_when: str,
    specialty: str | None = None,
    clinic_name: str = "the clinic",
    hold_minutes: int = 60,
    top_n: int = 3,
) -> list[str]:
    """Fires both outbound flows. Returns the emails actually offered the
    slot (the top_n highest-scored waitlist candidates that succeeded) —
    useful for tests and for logging what happened without re-deriving it
    from side effects.

    Each send is isolated: the exit survey and every waitlist offer are
    independent emails to independent people. A Gmail hiccup on one
    shouldn't cost every other patient their shot at the freed slot, and a
    failed exit survey shouldn't cancel the waitlist offers that follow it.
    Failures are logged with full traceback, not silently dropped.
    """
    if cancelling_patient_email:
        try:
            send_cancellation_exit_survey(cancelling_patient_email, clinic_name=clinic_name)
        except Exception:
            logger.exception("Exit survey failed to send to %s", cancelling_patient_email)

    candidates = get_waitlist_candidates(specialty=specialty)
    ranked = rank_waitlist([(patient_id, inp) for patient_id, _email, inp in candidates])
    email_by_id = {patient_id: email for patient_id, email, _inp in candidates}

    offered = []
    for patient_id, _score in ranked[:top_n]:
        email = email_by_id[patient_id]
        try:
            send_waitlist_offer(
                email,
                provider_name=freed_slot_provider,
                when=freed_slot_when,
                clinic_name=clinic_name,
                hold_minutes=hold_minutes,
            )
            offered.append(email)
        except Exception:
            logger.exception("Waitlist offer failed to send to %s", email)
    return offered
