"""Drive both demo calls through the real stack, without a phone.

    python -m scripts.dry_run

fake_call.py proves the voice layer. This proves everything behind it: real
Medplum writes, real slot booking, a real SMS, the emergency branch. It walks
the state object exactly as the extractor would and lets the orchestrator do the
rest.

Run it before a demo. If this passes, the only thing left that can fail is the
phone line.
"""

from __future__ import annotations

import asyncio
import sys

from services.medplum import MedplumClient
from shared.config import DEMO_CALLER_NUMBER, STEDI_TEST_MEMBER_ID
from shared.state import NextAction, new_state
from voice.orchestrator import CallOrchestrator


def _hr(title: str) -> None:
    print(f"\n{'=' * 62}\n{title}\n{'=' * 62}")


async def booking_call(medplum: MedplumClient) -> bool:
    _hr("CALL 1 — returning patient, full booking")
    orch = CallOrchestrator(medplum)
    state = new_state()
    state.identity.phone = DEMO_CALLER_NUMBER

    if await orch.preload_by_caller_id(state):
        print(f"  caller ID    -> {state.identity.name} (returning)")
        print(f"  prior provider -> {state.scheduling.provider_pref}")
        for fact in state.retrieved_context:
            print(f"  history      -> {fact}")
    else:
        print("  caller ID    -> NOT RECOGNISED (demo moment will not land)")

    state.symptoms.body_site = "right shoulder"
    state.symptoms.onset = "3 weeks ago"
    state.symptoms.severity = 6
    state.symptoms.confidence = 0.9
    await orch.advance(state)
    print(f"  after symptoms -> {state.next_action.value}")

    # The member ID Stedi's sandbox payer actually recognises. An invented one
    # comes back AAA 72 "Invalid/Missing Subscriber ID" — correct behaviour from
    # a payer that has never heard of the member.
    state.insurance.payer = "UnitedHealthcare"
    state.insurance.member_id = STEDI_TEST_MEMBER_ID
    state.insurance.confidence = 0.92
    await orch.advance(state)
    print(f"  eligibility  -> covered={state.insurance.covered} "
          f"copay=${state.insurance.copay} deductible=${state.insurance.deductible_remaining}")

    await orch.advance(state)
    print(f"  slot chosen  -> {state.scheduling.selected_slot_id}")
    for fact in state.retrieved_context[-1:]:
        print(f"  offering     -> {fact}")

    state.scheduling.confidence = 0.9
    await orch.advance(state)
    print(f"  appointment  -> {orch.booked_appointment_id}")
    print(f"  final action -> {state.next_action.value}")
    if state.review_flags:
        for flag in state.review_flags:
            print(f"  flag         -> {flag}")

    ok = orch.booked_appointment_id is not None
    print(f"\n  RESULT: {'BOOKED' if ok else 'DID NOT BOOK'}")
    return ok


async def emergency_call(medplum: MedplumClient) -> bool:
    _hr("CALL 2 — cauda equina red flags, must NOT book")
    orch = CallOrchestrator(medplum)
    state = new_state()
    state.identity.name = "James Whitfield"
    state.identity.dob = "1990-11-03"
    state.identity.confidence = 0.9
    state.symptoms.body_site = "lower back"
    state.symptoms.onset = "yesterday"
    state.symptoms.severity = 8
    state.symptoms.red_flags = ["saddle_numbness", "bowel_bladder_change"]
    state.symptoms.confidence = 0.9

    await orch.advance(state)

    print(f"  emergency    -> {state.emergency}")
    print(f"  action       -> {state.next_action.value}")
    print(f"  appointment  -> {orch.booked_appointment_id}")
    print(f"  urgent task  -> {orch.escalation_task_id}")
    for flag in state.review_flags:
        print(f"  flag         -> {flag}")

    from engine.redflags import evaluate

    print(f"\n  AGENT SAYS: {evaluate(state.symptoms.red_flags).spoken_advice}")

    ok = (
        state.emergency
        and state.next_action is NextAction.EMERGENCY_STOP
        and orch.booked_appointment_id is None
        and orch.escalation_task_id is not None
    )
    print(f"\n  RESULT: {'CORRECTLY REFUSED TO BOOK' if ok else 'SAFETY FAILURE'}")
    return ok


async def main() -> int:
    medplum = MedplumClient()
    try:
        booked = await booking_call(medplum)
        refused = await emergency_call(medplum)

        _hr("SUMMARY")
        print(f"  booking call  : {'PASS' if booked else 'FAIL'}")
        print(f"  emergency call: {'PASS' if refused else 'FAIL'}")
        print("\n  Reset before demoing:  .\\run.ps1 seed")
        return 0 if (booked and refused) else 1
    finally:
        await medplum.aclose()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
