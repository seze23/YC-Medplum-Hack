"""What happens when Stedi eligibility comes back with a referral required
and none on file. Same resilience shape as reschedule_flow.py: one email,
one patient, log-and-return-False on failure rather than raising.

Not triggered by inbound text at all — this fires from the insurance-check
result (his Stedi integration), not agentphone_router.py. Call
handle_referral_required directly once his eligibility check returns
referral_required=True with no referral_valid_through on file
(shared/state.py's Insurance dataclass already has both fields).
"""

from __future__ import annotations

import logging

from services.email import send_referral_request

logger = logging.getLogger(__name__)


def handle_referral_required(
    patient_email: str | None,
    *,
    provider_name: str,
    clinic_name: str = "the clinic",
) -> bool:
    """Returns True if the email actually sent. False for "no email to send
    to" and "the send itself failed" alike — callers that need to tell those
    apart should check the logs, not this return value.
    """
    if not patient_email:
        logger.warning("handle_referral_required called with no patient email")
        return False

    try:
        send_referral_request(patient_email, provider_name=provider_name, clinic_name=clinic_name)
    except Exception:
        logger.exception("Referral request email failed to send to %s", patient_email)
        return False

    return True
