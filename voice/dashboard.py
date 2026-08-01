"""Ops dashboard data.

The browser cannot hold Medplum client credentials, so the dashboard polls this
endpoint and the server does the FHIR queries. That also means no CORS setup and
no second server to keep alive during the demo.

Every number here is read back out of Medplum rather than kept in memory. Ugly
and live beats polished and faked — a judge who asks "is that real?" can be shown
the same figures in the Medplum console.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from services.medplum import MedplumClient

# What a completed outpatient PT evaluation is worth. Used for the recovered
# revenue tick. State the assumption out loud rather than hiding it.
VISIT_VALUE_USD = 120

# Live per-call state, so the dashboard can show confidence per domain while the
# call is still happening. In memory on purpose: this is the only thing on the
# board that isn't read back out of Medplum, because it does not exist there yet.
ACTIVE_CALLS: dict[str, dict[str, Any]] = {}


def publish(call_id: str, state: Any) -> None:
    """Called after every extraction. Cheap, synchronous, never raises."""
    try:
        payload = state.to_dict()
        ACTIVE_CALLS[call_id] = {
            "call_id": call_id,
            "name": payload["identity"]["name"],
            "next_action": payload["next_action"],
            "emergency": payload["emergency"],
            "confidence": {
                domain: round(payload[domain]["confidence"], 2)
                for domain in ("identity", "symptoms", "insurance", "scheduling")
            },
            "review_flags": payload["review_flags"],
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception:  # noqa: BLE001 - a dashboard must never break a call
        pass


def retire(call_id: str) -> None:
    ACTIVE_CALLS.pop(call_id, None)


async def snapshot(medplum: MedplumClient) -> dict[str, Any]:
    """One poll. Everything the dashboard renders."""
    counts: dict[str, int] = {}
    for resource_type in (
        "Patient",
        "Coverage",
        "Condition",
        "Appointment",
        "Encounter",
        "Communication",
        "Task",
    ):
        counts[resource_type] = len(
            await medplum.search(resource_type, {"_count": "200"})
        )

    slots = await medplum.search("Slot", {"_count": "200"})
    busy = sum(1 for s in slots if s.get("status") == "busy")
    total = len(slots)

    tasks = await medplum.search("Task", {"status": "requested", "_count": "50"})
    appointments = await medplum.search(
        "Appointment", {"status": "booked", "_count": "200"}
    )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "active_calls": list(ACTIVE_CALLS.values()),
        "resources": counts,
        "resources_total": sum(counts.values()),
        "utilization": {
            "busy": busy,
            "total": total,
            "percent": round(100 * busy / total) if total else 0,
        },
        "revenue_recovered": len(appointments) * VISIT_VALUE_USD,
        "appointments_booked": len(appointments),
        "open_tasks": [
            {
                "id": t.get("id"),
                "priority": t.get("priority", "routine"),
                "description": t.get("description", ""),
                "authored": t.get("authoredOn", ""),
            }
            for t in sorted(
                tasks,
                key=lambda t: {"stat": 0, "urgent": 1}.get(t.get("priority"), 2),
            )
        ],
    }
