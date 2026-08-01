"""Hardcoded demo data — NOT Medplum. Exists so the full text-in -> Task ->
email flow can be demoed live today without waiting on services/medplum.py.

Every function here is a drop-in replacement for a stub already defined
elsewhere (patient_lookup, create_task, get_waitlist_candidates). Swap them
back for real Medplum calls in main.py's composition root once his client
exists — nothing in agentphone_router.py or cancellation_flow.py changes.

DEMO_PATIENT_PHONE is +17182337507, the number already used for the live
AgentPhone tests today. Text the AgentPhone number FROM that phone to play
"the patient" during the demo, or edit this constant.
"""

from __future__ import annotations

from datetime import datetime, timezone

DEMO_PATIENT_PHONE = "+17182337507"
DEMO_PATIENT_EMAIL = "relaycustomer28@gmail.com"

DEMO_PATIENT = {
    "id": "Patient/demo-1",
    "name": "Jordan Lee",
    "phone": DEMO_PATIENT_PHONE,
    "email": DEMO_PATIENT_EMAIL,
    "appointment": {
        "provider_name": "Dr. Chen",
        "when": "Tuesday at 2:00 PM",
    },
}

# (patient_id, email, ScoringInput kwargs). Separate inbox from
# DEMO_PATIENT_EMAIL so the two roles are visibly distinct during the demo —
# one screen showing the cancelling patient's exit survey, another showing
# the waitlisted patient's offer.
DEMO_WAITLIST_EMAIL = "sydneyeze.sia@gmail.com"

DEMO_WAITLIST = [
    (
        "Patient/demo-2",
        DEMO_WAITLIST_EMAIL,
        dict(
            severity=8,
            insurance_covered=True,
            copay=20.0,
            deductible_remaining=0.0,
            distance_miles=3.0,
            specialty_match=True,
            slot_available_soon=True,
        ),
    ),
]

# Keyed by provider name to match DEMO_PATIENT's appointment — real version
# is a Medplum Slot query for this provider/specialty.
DEMO_AVAILABLE_SLOTS = {
    "Dr. Chen": ["Wednesday at 10:00 AM", "Thursday at 3:30 PM", "Friday at 1:00 PM"],
}

_created_tasks: list[dict] = []


def demo_patient_lookup(phone: str) -> dict | None:
    if phone == DEMO_PATIENT_PHONE:
        return DEMO_PATIENT
    return None


def demo_create_task(
    *, phone: str, patient: dict | None, category: str, detail: str
) -> None:
    entry = {
        "phone": phone,
        "patient_name": patient["name"] if patient else None,
        "category": category,
        "detail": detail,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    _created_tasks.append(entry)
    print(f"[TASK] {category} — {entry['patient_name'] or phone}: {detail!r}")

    if category == "CANCELLATION" and patient:
        from services.cancellation_flow import handle_cancellation

        appt = patient["appointment"]
        handle_cancellation(
            cancelling_patient_email=patient["email"],
            freed_slot_provider=appt["provider_name"],
            freed_slot_when=appt["when"],
            clinic_name="Relay Physical Therapy",
        )
    elif category == "RESCHEDULE_REQUEST" and patient:
        from services.reschedule_flow import handle_reschedule

        appt = patient["appointment"]
        handle_reschedule(
            patient_email=patient["email"],
            provider_name=appt["provider_name"],
            clinic_name="Relay Physical Therapy",
        )


def demo_get_waitlist_candidates(*, specialty: str | None = None):
    from engine.patient_score import PatientHistory, ScoringInput

    return [
        (patient_id, email, ScoringInput(history=PatientHistory(), **inputs))
        for patient_id, email, inputs in DEMO_WAITLIST
    ]


def demo_get_available_slots(*, provider_name: str, specialty: str | None = None) -> list[str]:
    return DEMO_AVAILABLE_SLOTS.get(provider_name, [])


def get_created_tasks() -> list[dict]:
    return list(_created_tasks)
