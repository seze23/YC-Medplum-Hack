"""Builds the appointment confirmation the patient is given.

This used to be an SMS. It is not any more: US carriers reject application-to-
person traffic from unregistered 10-digit numbers (Twilio error 30034), and A2P
10DLC registration takes days rather than minutes. Rather than demo a message
that silently never arrives, the confirmation is spoken on the call and recorded
as a FHIR `Communication`.

The text still lives here because the record should show exactly what the
patient was told — that is the part a clinic cares about, and it is unchanged by
the delivery channel.
"""

from __future__ import annotations

from datetime import datetime


def confirmation_text(
    *,
    patient_name: str,
    start: datetime,
    practitioner_name: str,
    copay: float | None,
    clinic: str = "Bayview Physical Therapy",
) -> str:
    first = patient_name.strip().split()[0] if patient_name.strip() else "there"
    when = start.strftime("%a %d %b at %I:%M %p").replace(" 0", " ")
    lines = [
        f"{first}, you're booked at {clinic}.",
        f"{when} with {practitioner_name}.",
    ]
    if copay is not None:
        lines.append(f"Copay: ${copay:.0f}.")
    return " ".join(lines)
