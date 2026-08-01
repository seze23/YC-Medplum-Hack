"""Demo entrypoint. Mounts the AgentPhone webhook and swaps its Medplum
stubs for hardcoded fixture data (services/demo_fixtures.py) so the full
text-in -> Task -> email flow works live today without waiting on his
Medplum client or main app.

Once his real app + services/medplum.py exist, this file's job is done:
merge agentphone_router into his app, and change the three assignments below
from demo_fixtures functions to real Medplum-backed ones. Nothing in
agentphone_router.py or cancellation_flow.py needs to change either way —
that's the whole point of building them against Protocols instead of direct
imports.

Run: uvicorn main:app --reload --port 8000
"""

from __future__ import annotations

import services.agentphone_router as agentphone_router
import services.cancellation_flow as cancellation_flow
import services.reschedule_flow as reschedule_flow
from fastapi import FastAPI
from services.demo_fixtures import (
    demo_create_task,
    demo_get_available_slots,
    demo_get_waitlist_candidates,
    demo_patient_lookup,
    get_created_tasks,
)

# Composition root: the one place stubs get swapped for real implementations.
# Everything else only knows about the Protocol, not which side is live.
agentphone_router.patient_lookup = demo_patient_lookup
agentphone_router.create_task = demo_create_task
cancellation_flow.get_waitlist_candidates = demo_get_waitlist_candidates
reschedule_flow.get_available_slots = demo_get_available_slots

app = FastAPI(title="Relay (demo)")
app.include_router(agentphone_router.router)


@app.get("/tasks")
def list_tasks():
    """Visible proof of what the agent did — point the demo screen here if
    a real dashboard isn't ready in time."""
    return {"tasks": get_created_tasks()}


@app.get("/health")
def health():
    return {"status": "ok"}
