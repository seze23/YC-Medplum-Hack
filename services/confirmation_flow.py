"""What happens when a patient texts back a confirmation. Same shape as
referral_flow.py: one email, one patient, log-and-return-False on failure
rather than raising.
"""

from __future__ import annotations

import logging

from services.email import send_confirmation_ack

logger = logging.getLogger(__name__)


def handle_confirmation(
    patient_email: str | None,
    *,
    provider_name: str,
    when: str,
    clinic_name: str = "the clinic",
) -> bool:
    if not patient_email:
        logger.warning("handle_confirmation called with no patient email")
        return False

    try:
        send_confirmation_ack(
            patient_email, provider_name=provider_name, when=when, clinic_name=clinic_name
        )
    except Exception:
        logger.exception("Confirmation ack email failed to send to %s", patient_email)
        return False

    return True
