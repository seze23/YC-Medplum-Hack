"""What happens when a patient texts in wanting to reschedule. Mirrors
services/cancellation_flow.py's shape: a stub availability source (until
Medplum exists), an outbound email, resilience via try/except + logging.

One simplification versus cancellation: this is one email to one patient,
not a batch to multiple waitlist candidates, so there's no per-recipient
isolation to worry about — just "log and return nothing offered" if the
single send fails.

Not yet wired into agentphone_router.py's RESCHEDULE_REQUEST branch, same
reason as cancellation: it needs the patient's email and current provider,
both from Medplum. Wired in services/demo_fixtures.py for the demo.
"""

from __future__ import annotations

import logging
from typing import Protocol

from services.email import send_reschedule_options

logger = logging.getLogger(__name__)


class AvailabilitySource(Protocol):
    def __call__(
        self, *, provider_name: str, specialty: str | None = None
    ) -> list[str]:
        """Returns human-readable available time slots for this provider."""
        ...


def _default_get_available_slots(
    *, provider_name: str, specialty: str | None = None
) -> list[str]:
    """Stand-in until services/medplum.py exists — same pattern as
    get_waitlist_candidates in cancellation_flow.py. Real version queries
    Medplum Slot resources for this provider/specialty.
    """
    return []


get_available_slots: AvailabilitySource = _default_get_available_slots


def handle_reschedule(
    *,
    patient_email: str | None,
    provider_name: str,
    clinic_name: str = "the clinic",
    max_options: int = 3,
) -> list[str]:
    """Fires the reschedule-options email. Returns the options actually
    offered — empty if there was no email to send to, no availability to
    offer, or the send itself failed. Callers can tell "nothing to offer"
    and "the email broke" apart via the logs, but both return the same
    empty list, since neither should ever raise up into the webhook.
    """
    if not patient_email:
        logger.warning("handle_reschedule called with no patient email — skipping")
        return []

    options = get_available_slots(provider_name=provider_name)[:max_options]
    if not options:
        logger.warning(
            "No available slots found for %s — nothing to offer", provider_name
        )
        return []

    try:
        send_reschedule_options(
            patient_email,
            provider_name=provider_name,
            options=options,
            clinic_name=clinic_name,
        )
    except Exception:
        logger.exception("Reschedule options email failed to send to %s", patient_email)
        return []

    return options
