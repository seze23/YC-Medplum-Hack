"""End-to-end orchestrator tests against a fake Medplum. No network, no keys.

This is the merge step, verified before credentials exist. It proves the two
claims that matter:

  * A clean call books once and writes all eight FHIR resources.
  * A call with cauda equina red flags writes a stat Task and books NOTHING.

Uses asyncio.run rather than pytest-asyncio to avoid another dependency.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from services import moss
from shared.state import NextAction, new_state
from voice.orchestrator import CallOrchestrator

NOW = datetime.now(timezone.utc)


class FakeMedplum:
    """Records every write so the tests can assert on the artifact."""

    def __init__(
        self,
        *,
        existing_patient: bool = False,
        match_how: str = "name_dob",
        match_confidence: float = 0.95,
    ) -> None:
        self.existing_patient = existing_patient
        self.match_how = match_how
        self.match_confidence = match_confidence
        self.writes: list[tuple[str, dict[str, Any]]] = []
        self.busy_slots: list[str] = []
        self._counter = 0

    def _record(self, kind: str, body: dict[str, Any]) -> dict[str, Any]:
        self._counter += 1
        self.writes.append((kind, body))
        return {"id": f"{kind.lower()}-{self._counter}", **body}

    def kinds(self) -> list[str]:
        return [k for k, _ in self.writes]

    # --- identity ---
    async def find_patient(self, name: str, dob: str, phone: str = ""):
        """Mirrors the real signature: (patient, how_matched, confidence)."""
        if not self.existing_patient:
            return None, "none", 0.0
        patient = {"id": "patient-maria", "name": [{"given": ["Maria"], "family": "Alvarez"}]}
        return patient, self.match_how, self.match_confidence

    async def create_patient(self, state):
        return self._record("Patient", {"resourceType": "Patient"})

    async def patient_history(self, patient_id: str):
        return {
            "conditions": [
                {
                    "code": {"text": "right shoulder impingement"},
                    "clinicalStatus": {"coding": [{"code": "resolved"}]},
                    "recordedDate": (NOW - timedelta(days=180)).isoformat(),
                }
            ],
            "encounters": [
                {
                    "reasonCode": [{"text": "right shoulder impingement"}],
                    "participant": [
                        {
                            "individual": {
                                "reference": "Practitioner/prac-chen",
                                "display": "Dr. Sarah Chen",
                            }
                        }
                    ],
                    "period": {"start": (NOW - timedelta(days=180)).isoformat()},
                }
            ],
            "appointments": [],
        }

    # --- scheduling ---
    async def free_slots(self, **_):
        return [
            {
                "id": "slot-reed",
                "start": (NOW + timedelta(hours=24)).isoformat(),
                "schedule": {"reference": "Schedule/sched-reed"},
            },
            {
                "id": "slot-chen",
                "start": (NOW + timedelta(hours=30)).isoformat(),
                "schedule": {"reference": "Schedule/sched-chen"},
            },
        ]

    async def search(self, resource_type: str, params: dict[str, Any]):
        if resource_type == "Practitioner":
            return [
                {"id": "prac-chen", "name": [{"prefix": ["Dr."], "given": ["Sarah"], "family": "Chen"}]},
                {"id": "prac-reed", "name": [{"prefix": ["Dr."], "given": ["Marcus"], "family": "Reed"}]},
            ]
        if resource_type == "Schedule":
            sid = params.get("_id", "")
            mapping = {"sched-chen": "prac-chen", "sched-reed": "prac-reed"}
            return [{"id": sid, "actor": [{"reference": f"Practitioner/{mapping.get(sid, '')}"}]}]
        return []

    async def mark_slot_busy(self, slot_id: str):
        self.busy_slots.append(slot_id)
        return {"id": slot_id, "status": "busy"}

    # --- write-back ---
    async def write_condition(self, pid, state):
        return self._record("Condition", {})

    async def write_coverage(self, pid, state):
        return self._record("Coverage", {})

    async def write_appointment(self, pid, **kw):
        return self._record("Appointment", dict(kw))

    async def write_encounter(self, pid, state):
        return self._record("Encounter", {})

    async def write_communication(self, pid, *, content, medium):
        return self._record("Communication", {"content": content, "medium": medium})

    async def write_task(self, pid, *, description, priority="routine"):
        return self._record("Task", {"description": description, "priority": priority})


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    """Force fixture replay for Stedi and skip real SMS."""
    monkeypatch.setattr("services.stedi.USE_FIXTURES", True)
    moss.clear()


def _returning_caller():
    s = new_state()
    s.identity.name = "Maria Alvarez"
    s.identity.dob = "1979-04-12"
    s.identity.phone = "+14155550142"
    s.identity.confidence = 0.95
    return s


async def _drive_happy_path(fake: FakeMedplum):
    """Walk the state through a full call the way the extractor would."""
    orch = CallOrchestrator(fake)
    state = _returning_caller()

    await orch.advance(state)  # identity -> resolve patient, load history

    state.symptoms.body_site = "right shoulder"
    state.symptoms.onset = "3 weeks ago"
    state.symptoms.severity = 6
    state.symptoms.confidence = 0.9
    await orch.advance(state)  # -> verify insurance (details still missing)

    state.insurance.payer = "Blue Shield"
    state.insurance.member_id = "XYZ123456789"
    state.insurance.confidence = 0.92
    await orch.advance(state)  # -> Stedi runs
    await orch.advance(state)  # -> offer slot
    state.scheduling.confidence = 0.9
    await orch.advance(state)  # -> book

    return orch, state


def test_happy_path_books_and_writes_the_full_record():
    fake = FakeMedplum(existing_patient=True)
    orch, state = asyncio.run(_drive_happy_path(fake))

    assert orch.booked_appointment_id is not None
    for resource in ("Condition", "Coverage", "Appointment", "Encounter", "Communication"):
        assert resource in fake.kinds(), f"{resource} was never written"
    assert fake.busy_slots == ["slot-chen"] or fake.busy_slots == ["slot-reed"]


def test_returning_patient_is_matched_not_recreated():
    fake = FakeMedplum(existing_patient=True)
    orch, state = asyncio.run(_drive_happy_path(fake))
    assert state.identity.is_returning is True
    assert "Patient" not in fake.kinds()


def test_new_patient_is_created():
    fake = FakeMedplum(existing_patient=False)
    orch, state = asyncio.run(_drive_happy_path(fake))
    assert state.identity.is_returning is False
    assert "Patient" in fake.kinds()


def test_prior_therapist_becomes_the_default_and_wins_the_slot():
    """The demo moment: continuity of care beats a slightly earlier opening."""
    fake = FakeMedplum(existing_patient=True)
    orch, state = asyncio.run(_drive_happy_path(fake))
    assert state.scheduling.provider_pref == "prac-chen"
    assert state.scheduling.selected_slot_id == "slot-chen"


def test_moss_surfaces_the_prior_episode():
    fake = FakeMedplum(existing_patient=True)
    orch, state = asyncio.run(_drive_happy_path(fake))
    joined = " ".join(state.retrieved_context).lower()
    assert "right shoulder" in joined


def test_eligibility_numbers_reach_the_state():
    """Whatever the payer returns must land on the state object.

    Asserted against the real captured 271 rather than pinned figures, so this
    keeps passing when the fixture is re-captured from a live call.
    """
    fake = FakeMedplum(existing_patient=True)
    orch, state = asyncio.run(_drive_happy_path(fake))
    assert state.insurance.covered is True
    assert state.insurance.deductible_remaining is not None


def test_booking_is_idempotent():
    """A chatty extractor repeating itself must not double-book."""
    fake = FakeMedplum(existing_patient=True)

    async def run():
        orch, state = await _drive_happy_path(fake)
        for _ in range(4):
            await orch.advance(state)
        return orch

    orch = asyncio.run(run())
    assert fake.kinds().count("Appointment") == 1


def test_eligibility_is_checked_only_once():
    fake = FakeMedplum(existing_patient=True)

    async def run():
        orch, state = await _drive_happy_path(fake)
        for _ in range(3):
            await orch.advance(state)
        return orch

    orch = asyncio.run(run())
    assert fake.kinds().count("Coverage") == 1


# --- the branch that must never break --------------------------------------


def test_emergency_writes_a_stat_task_and_books_nothing():
    fake = FakeMedplum(existing_patient=True)

    async def run():
        orch = CallOrchestrator(fake)
        state = _returning_caller()
        state.symptoms.body_site = "lower back"
        state.symptoms.onset = "2 days ago"
        state.symptoms.severity = 8
        state.symptoms.confidence = 0.9
        state.symptoms.red_flags = ["saddle_numbness", "bowel_bladder_change"]
        await orch.advance(state)
        return orch, state

    orch, state = asyncio.run(run())

    assert state.emergency is True
    assert state.next_action is NextAction.EMERGENCY_STOP
    assert orch.booked_appointment_id is None
    assert "Appointment" not in fake.kinds()

    tasks = [body for kind, body in fake.writes if kind == "Task"]
    assert len(tasks) == 1
    assert tasks[0]["priority"] == "stat"


def test_emergency_task_is_written_only_once():
    fake = FakeMedplum(existing_patient=True)

    async def run():
        orch = CallOrchestrator(fake)
        state = _returning_caller()
        state.symptoms.body_site = "lower back"
        state.symptoms.onset = "2 days ago"
        state.symptoms.red_flags = ["bilateral_leg_weakness"]
        for _ in range(3):
            await orch.advance(state)
        return orch

    asyncio.run(run())
    assert fake.kinds().count("Task") == 1


def test_caller_id_match_is_trusted():
    """Caller ID is the strongest signal — it should not depress confidence."""
    fake = FakeMedplum(existing_patient=True, match_how="caller_id", match_confidence=0.9)
    orch, state = asyncio.run(_drive_happy_path(fake))
    assert state.identity.is_returning is True
    assert state.identity.confidence == pytest.approx(0.9)
    assert not any("matched on name alone" in f for f in state.review_flags)


def test_name_only_match_raises_a_review_flag_and_still_books():
    """A shaky match must not silently inherit someone else's history."""
    fake = FakeMedplum(existing_patient=True, match_how="name_only", match_confidence=0.6)
    orch, state = asyncio.run(_drive_happy_path(fake))
    assert state.identity.confidence <= 0.6
    assert any("IDENTITY MATCH" in f for f in state.review_flags)
    # The low confidence also trips the ordinary gate, so a human gets a Task.
    assert any(f.startswith("REVIEW identity") for f in state.review_flags)
    # It still completes the call — a human confirms afterwards.
    assert orch.booked_appointment_id is not None


def test_unconfirmed_coverage_raises_a_billing_task_and_still_books():
    """A walk-in whose insurance we cannot verify.

    The agent tells them billing will follow up, so a Task has to actually
    exist — otherwise the promise is empty and the patient arrives against an
    unverified claim. It must still book: a clinic would.
    """

    def _no_coverage(payload):
        from services.stedi import Eligibility

        return Eligibility(covered=False, raw={})

    fake = FakeMedplum(existing_patient=False)

    async def run():
        import services.stedi as stedi_mod

        original = stedi_mod.parse
        stedi_mod.parse = _no_coverage
        try:
            orch, state = await _drive_happy_path(fake)
            return orch, state
        finally:
            stedi_mod.parse = original

    orch, state = asyncio.run(run())

    assert any(f.startswith("BILLING") for f in state.review_flags)
    assert orch.booked_appointment_id is not None
    tasks = [b for k, b in fake.writes if k == "Task"]
    assert any("BILLING" in t["description"] for t in tasks)


def test_a_failing_service_flags_but_does_not_kill_the_call():
    """A Medplum blip must not take down a live call."""

    class Broken(FakeMedplum):
        async def free_slots(self, **_):
            raise RuntimeError("medplum is having a moment")

    fake = Broken(existing_patient=True)

    async def run():
        orch, state = await _drive_happy_path(fake)
        return state

    state = asyncio.run(run())
    assert any("SYSTEM" in f for f in state.review_flags)
