"""Re-runnable Medplum seed. Run it between demo takes.

    python -m scripts.seed_medplum          # reset, then seed
    python -m scripts.seed_medplum --reset  # reset only

Every resource this writes carries the RELAY_SEED tag, so --reset removes
exactly what the script created and nothing a judge or teammate added by hand.

Doing this by hand at 17:45 is misery. That is the entire reason this file
exists.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, time, timedelta, timezone
from typing import Any

from services.medplum import MedplumClient
from shared.config import DEMO_CALLER_NUMBER

SEED_SYSTEM = "https://relay.health/tags"
SEED_CODE = "RELAY_SEED"

TAG = {"meta": {"tag": [{"system": SEED_SYSTEM, "code": SEED_CODE}]}}

# Deleted in this order so nothing is left pointing at a missing parent.
RESOURCE_TYPES = [
    "Appointment",
    "Encounter",
    "Condition",
    "Coverage",
    "Communication",
    "Task",
    "Slot",
    "Schedule",
    "Patient",
    "Practitioner",
]


def _tagged(body: dict[str, Any]) -> dict[str, Any]:
    return {**body, **TAG}


async def reset(client: MedplumClient) -> None:
    """Clear both the seeded fixtures and anything previous calls wrote.

    Both tags matter. RELAY_SEED is this script's own data; RELAY_CALL is what
    the agent created during a take. Leaving the latter behind is what makes the
    second run of a demo behave differently from the first.
    """
    print("Resetting seeded resources...")
    for resource_type in RESOURCE_TYPES:
        deleted = 0
        for code in (SEED_CODE, "RELAY_CALL"):
            found = await client.search(
                resource_type, {"_tag": f"{SEED_SYSTEM}|{code}", "_count": "200"}
            )
            for resource in found:
                await client.delete(resource_type, resource["id"])
            deleted += len(found)
        if deleted:
            print(f"  deleted {deleted:>3} {resource_type}")
    print("Reset complete.\n")


async def seed(client: MedplumClient) -> None:
    print("Seeding...")

    # --- practitioners ------------------------------------------------------
    # Two of them, so provider-match scoring has something to actually beat.
    chen = await client.create(
        "Practitioner",
        _tagged(
            {
                "resourceType": "Practitioner",
                "name": [{"given": ["Sarah"], "family": "Chen", "prefix": ["Dr."]}],
                "qualification": [{"code": {"text": "Physical Therapist, DPT"}}],
            }
        ),
    )
    reed = await client.create(
        "Practitioner",
        _tagged(
            {
                "resourceType": "Practitioner",
                "name": [{"given": ["Marcus"], "family": "Reed", "prefix": ["Dr."]}],
                "qualification": [{"code": {"text": "Physical Therapist, DPT"}}],
            }
        ),
    )
    print(f"  Practitioner Dr. Chen  {chen['id']}")
    print(f"  Practitioner Dr. Reed  {reed['id']}")

    # --- patients -----------------------------------------------------------
    # The returning patient. His history is what gets surfaced mid-sentence:
    # same right shoulder, same therapist. That line is the demo.
    #
    # Deliberately Stedi's own test subscriber rather than an invented patient.
    #
    # An eligibility check queries the PAYER's database, not ours. Inventing
    # "Maria Alvarez, member XYZ123456789" meant Stedi's test payer correctly
    # answered "never heard of her" (AAA 75 / 71 / 73) — not a bug, just a
    # patient who does not exist on their side. Seeding the clinic record with
    # the identity their sandbox actually knows makes the check genuinely live.
    #
    # Values taken verbatim from a captured 271: John Doe, UHC202649,
    # UnitedHealthcare (payer 87726), born 1976-02-14.
    maria = await client.create(
        "Patient",
        _tagged(
            {
                "resourceType": "Patient",
                "name": [{"given": ["John"], "family": "Doe"}],
                "birthDate": "1976-02-14",
                "gender": "male",
                # The number you demo from, so caller ID resolves him on ring.
                "telecom": [
                    {
                        "system": "phone",
                        "value": DEMO_CALLER_NUMBER or "+14155550142",
                        "use": "mobile",
                    }
                ],
            }
        ),
    )
    james = await client.create(
        "Patient",
        _tagged(
            {
                "resourceType": "Patient",
                "name": [{"given": ["James"], "family": "Whitfield"}],
                "birthDate": "1990-11-03",
                "telecom": [
                    {"system": "phone", "value": "+14155550178", "use": "mobile"}
                ],
            }
        ),
    )
    print(f"  Patient John Doe (returning)  {maria['id']}")
    print(f"  Patient James Whitfield (new)      {james['id']}")

    # --- John's prior episode ---------------------------------------------
    six_months_ago = datetime.now(timezone.utc) - timedelta(days=180)

    await client.create(
        "Condition",
        _tagged(
            {
                "resourceType": "Condition",
                "subject": {"reference": f"Patient/{maria['id']}"},
                "clinicalStatus": {
                    "coding": [
                        {
                            "system": "http://terminology.hl7.org/CodeSystem/condition-clinical",
                            "code": "resolved",
                        }
                    ]
                },
                "code": {"text": "right shoulder impingement"},
                "recordedDate": six_months_ago.isoformat(),
                "note": [{"text": "Completed 8 sessions. Discharged, full ROM."}],
            }
        ),
    )
    await client.create(
        "Encounter",
        _tagged(
            {
                "resourceType": "Encounter",
                "status": "finished",
                "class": {
                    "system": "http://terminology.hl7.org/CodeSystem/v3-ActCode",
                    "code": "AMB",
                    "display": "ambulatory",
                },
                "subject": {"reference": f"Patient/{maria['id']}"},
                # `display` matters as much as the reference here. Retrieval
                # flattens this Encounter into a one-line fact, and without a
                # name that line reads "seen for right shoulder impingement"
                # instead of "seen for right shoulder impingement by Dr. Sarah
                # Chen" — which is the whole point of surfacing it mid-call.
                "participant": [
                    {
                        "individual": {
                            "reference": f"Practitioner/{chen['id']}",
                            "display": "Dr. Sarah Chen",
                        }
                    }
                ],
                "reasonCode": [{"text": "right shoulder impingement"}],
                "period": {
                    "start": six_months_ago.isoformat(),
                    "end": (six_months_ago + timedelta(days=42)).isoformat(),
                },
            }
        ),
    )
    print("  John's prior episode: right shoulder, 8 sessions with Dr. Chen")

    # --- schedule + slots ---------------------------------------------------
    slot_count = 0
    for practitioner, label in ((chen, "Chen"), (reed, "Reed")):
        schedule = await client.create(
            "Schedule",
            _tagged(
                {
                    "resourceType": "Schedule",
                    "active": True,
                    "actor": [{"reference": f"Practitioner/{practitioner['id']}"}],
                    "comment": f"Dr. {label} outpatient PT",
                }
            ),
        )
        for slot_start in _slot_times():
            await client.create(
                "Slot",
                _tagged(
                    {
                        "resourceType": "Slot",
                        "schedule": {"reference": f"Schedule/{schedule['id']}"},
                        "status": "free",
                        "start": slot_start.isoformat(),
                        "end": (slot_start + timedelta(minutes=45)).isoformat(),
                    }
                ),
            )
            slot_count += 1

    print(f"  {slot_count} free slots across today and tomorrow\n")
    print("Seed complete. Demo identities:")
    print("  Returning: John Doe, 1976-02-14, member UHC202649  (prior right shoulder, Dr. Chen)")
    print("  New:       James Whitfield, 1990-11-03")


def _slot_times() -> list[datetime]:
    """Slots in LOCAL time, always including some later today.

    Two things this has to get right, both of which bit us on the first run:

    1. Local time, not UTC. Seeding 10:00 UTC while demoing in California means
       the agent cheerfully offers a 3 AM physiotherapy appointment.
    2. Same-day availability regardless of when you run it. Fixed clinic hours
       (10:00/13:00/15:00) are all in the past by mid-afternoon, so a 15:00 seed
       produces a tomorrow-only demo and the "we can see you today" line dies.

    So: today's slots are generated relative to now, and tomorrow's use real
    clinic hours.
    """
    now = datetime.now().astimezone()  # local tz, tz-aware
    out: list[datetime] = []

    # Later today — next clean half-hour, then every 90 minutes.
    first = (now + timedelta(minutes=45)).replace(second=0, microsecond=0)
    first = first.replace(minute=0 if first.minute < 30 else 30)
    for i in range(3):
        out.append(first + timedelta(minutes=90 * i))

    # Tomorrow — normal clinic hours.
    tomorrow = (now + timedelta(days=1)).date()
    for hour in (9, 11, 14):
        out.append(
            datetime.combine(tomorrow, time(hour), tzinfo=now.tzinfo)
        )

    return out


async def main() -> None:
    parser = argparse.ArgumentParser(description="Seed Medplum for the Relay demo.")
    parser.add_argument(
        "--reset", action="store_true", help="Delete seeded resources and stop."
    )
    args = parser.parse_args()

    client = MedplumClient()
    try:
        await reset(client)
        if not args.reset:
            await seed(client)
    finally:
        await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())

