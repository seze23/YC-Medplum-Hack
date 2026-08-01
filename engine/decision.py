"""The decision engine.

A pure function from state to next action. No I/O, no LLM, no network, no
clock unless you pass one in. That is the whole point: the model fills in a
structured object, and *this* decides what actually happens to a patient.

It should be the most boring file in the repo. If it ever needs a mock to test,
something has leaked in that doesn't belong.

Order is load-bearing:

  1. Red flags. Always. Before anything else, every turn.
  2. Progressive collection — identity, symptoms, insurance, scheduling.
  3. Confidence gate, applied to whatever has been settled so far.
"""

from __future__ import annotations

from datetime import datetime

from engine.redflags import Triage, evaluate
from engine.scoring import Slot, rank_slots
from shared.state import CONFIDENCE_THRESHOLD, CallState, NextAction


def decide(state: CallState) -> CallState:
    """Set state.next_action, state.emergency and state.review_flags.

    Mutates and returns the same object so callers can chain. Idempotent:
    calling it twice on unchanged state gives the same answer.
    """
    triage = evaluate(state.symptoms.red_flags)

    # 1. Emergency branch. Hard stop at the top — not a line buried in a prompt.
    if triage.emergency:
        state.emergency = True
        state.next_action = NextAction.EMERGENCY_STOP
        state.review_flags = [f"EMERGENCY: {r}" for r in triage.reasons]
        return state

    state.emergency = False
    # Confidence flags are recomputed each turn, but flags raised elsewhere —
    # a failed Medplum call, an urgent triage note — are not ours to discard.
    # decide() is called after every extraction, so overwriting the list here
    # silently loses them.
    state.review_flags = _dedupe(_preserved(state) + _confidence_flags(state))

    # 2. Progressive collection.
    if not _identity_settled(state):
        state.next_action = NextAction.COLLECT_IDENTITY
    elif not _symptoms_settled(state):
        state.next_action = NextAction.COLLECT_SYMPTOMS
    elif triage.escalate:
        # Serious but not an emergency: a human calls back, we don't auto-book.
        state.review_flags = _dedupe(
            [f"URGENT: {r}" for r in triage.reasons] + state.review_flags
        )
        state.next_action = NextAction.ESCALATE
    elif not _insurance_settled(state):
        state.next_action = NextAction.VERIFY_INSURANCE
    elif state.scheduling.selected_slot_id is None:
        state.next_action = NextAction.OFFER_SLOTS
    else:
        state.next_action = NextAction.BOOK

    return state


def choose_slot(
    state: CallState, slots: list[Slot], *, now: datetime | None = None
) -> Slot | None:
    """Top-ranked bookable slot, or None if there is nothing to offer."""
    ranked = rank_slots(
        slots,
        severity=state.symptoms.severity,
        provider_pref=state.scheduling.provider_pref,
        escalate=evaluate(state.symptoms.red_flags).escalate,
        now=now,
    )
    return ranked[0][0] if ranked else None


# --- completeness -----------------------------------------------------------
#
# Completeness gates the *flow*; confidence gates *trust*. Keeping them separate
# is what stops the agent looping forever on a domain the model is stubbornly
# unsure about — it moves on, and says out loud that a human will check.


def _identity_settled(state: CallState) -> bool:
    return bool(state.identity.name and state.identity.dob)


def _symptoms_settled(state: CallState) -> bool:
    return bool(state.symptoms.body_site and state.symptoms.onset)


def _insurance_settled(state: CallState) -> bool:
    # Settled means Stedi has actually answered, not merely that we collected
    # a member ID. `covered` stays None until the eligibility call returns.
    return state.insurance.covered is not None


def _preserved(state: CallState) -> list[str]:
    """Flags the engine did not raise and must not drop."""
    return [f for f in state.review_flags if not f.startswith("REVIEW ")]


def _dedupe(flags: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for flag in flags:
        if flag not in seen:
            seen.add(flag)
            out.append(flag)
    return out


def _confidence_flags(state: CallState) -> list[str]:
    """One flag per settled-but-untrusted domain. Each becomes a Medplum Task."""
    settled = {
        "identity": _identity_settled(state),
        "symptoms": _symptoms_settled(state),
        "insurance": _insurance_settled(state),
        "scheduling": state.scheduling.selected_slot_id is not None,
    }
    flags = []
    for name, domain in state._domains():
        if settled[name] and domain.confidence < CONFIDENCE_THRESHOLD:
            flags.append(
                f"REVIEW {name}: confidence {domain.confidence:.2f} "
                f"below {CONFIDENCE_THRESHOLD}"
            )
    return flags
