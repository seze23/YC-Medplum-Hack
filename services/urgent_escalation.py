"""Immediate staff notification for texts flagged urgent. Text-channel
parallel to engine/redflags.py's emergency handling on the voice side — same
"safety overrides everything else" principle, checked first in
agentphone_router.py's classification, same as red flags are checked first
in engine/decision.py.

Fires regardless of whether patient_lookup found a match — an unknown
number saying something urgent should never be silently dropped just
because it doesn't match a known patient record.
"""

from __future__ import annotations

import logging
import os

from services.email import send_urgent_alert

logger = logging.getLogger(__name__)


def handle_urgent_concern(
    *, from_phone: str, patient_name: str | None, message: str
) -> bool:
    staff_email = os.environ.get("STAFF_ALERT_EMAIL") or os.environ.get("SMTP_USERNAME")
    if not staff_email:
        logger.error(
            "No STAFF_ALERT_EMAIL or SMTP_USERNAME configured — urgent "
            "message from %s was NOT escalated: %r",
            from_phone,
            message,
        )
        return False

    try:
        send_urgent_alert(
            staff_email,
            from_phone=from_phone,
            patient_name=patient_name,
            message=message,
        )
    except Exception:
        logger.exception("Urgent alert email failed to send for %s", from_phone)
        return False

    return True
