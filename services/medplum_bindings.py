"""Real Medplum-backed create_task, bound in voice/server.py's startup.
patient_lookup stays bound to demo_fixtures.demo_patient_lookup — see below
for why that's a deliberate scope cut, not an oversight.

Two things this deliberately does NOT try to solve, given the time left
before submission:

1. Linking a real Medplum Patient id to the demo fixture patient. The
   fixture's "Patient/demo-1" is not a real Medplum resource id, so every
   Task written through here has patient=None on the FHIR side — the phone
   number and category are in the description text instead. It still shows
   up on the dashboard; it just isn't linked to a Patient resource yet.
2. Deriving email/appointment details from a real FHIR Patient resource —
   email lives under `telecom`, an appointment is a separate resource
   entirely, neither matches the flat dict shape cancellation_flow.py /
   reschedule_flow.py / etc. expect. Rather than build that FHIR-shape
   translation under time pressure, the email side-effects keep running
   through services.demo_fixtures.demo_create_task's fixture data. Real
   Task write + real dashboard visibility, proven email behavior — that
   split is deliberate, not a shortcut nobody noticed.
"""

from __future__ import annotations

import logging

from services.medplum import MedplumClient

logger = logging.getLogger(__name__)


async def medplum_create_task(
    *, phone: str, patient: dict | None, category: str, detail: str
) -> None:
    medplum = MedplumClient()
    try:
        priority = "stat" if category == "URGENT_CONCERN" else "routine"
        await medplum.write_task(
            None,
            description=f"[{category}] from {phone}: {detail}",
            priority=priority,
        )
    except Exception:
        # _safe_create_task (the caller) also catches, but logging here too
        # names which half failed — the Medplum write or the email dispatch
        # below — instead of one exception hiding which.
        logger.exception("Medplum write_task failed for %s (%s)", phone, category)
    finally:
        await medplum.aclose()

    # Same fixture-driven email dispatch as the standalone demo — see module
    # docstring for why this isn't sourced from the real `patient` argument.
    from services.demo_fixtures import demo_create_task

    await demo_create_task(phone=phone, patient=patient, category=category, detail=detail)
