"""Unit tests for the decision engine. Hand-written state, no mocks, no network.

If these pass, the safety claim holds regardless of what the LLM does.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from engine.decision import choose_slot, decide
from engine.redflags import evaluate
from engine.scoring import Slot, rank_slots
from shared.state import CallState, NextAction, new_state

NOW = datetime(2026, 8, 1, 9, 0, 0)


def _complete_state() -> CallState:
    """A caller who has given us everything, cleanly."""
    s = new_state()
    s.identity.name = "Maria Chen"
    s.identity.dob = "1979-04-12"
    s.identity.confidence = 0.95
    s.symptoms.body_site = "right shoulder"
    s.symptoms.onset = "3 weeks ago"
    s.symptoms.severity = 6
    s.symptoms.confidence = 0.9
    s.insurance.payer = "Blue Shield"
    s.insurance.member_id = "XYZ123"
    s.insurance.covered = True
    s.insurance.copay = 40.0
    s.insurance.confidence = 0.92
    return s


# --- the emergency branch ---------------------------------------------------


@pytest.mark.parametrize(
    "flag",
    ["bowel_bladder_change", "saddle_numbness", "bilateral_leg_weakness"],
)
def test_any_single_cauda_equina_flag_stops_the_call(flag):
    s = _complete_state()
    s.symptoms.red_flags = [flag]
    decide(s)
    assert s.emergency is True
    assert s.next_action is NextAction.EMERGENCY_STOP


def test_emergency_beats_a_fully_bookable_call():
    """Everything else is perfect. It still must not book."""
    s = _complete_state()
    s.scheduling.selected_slot_id = "slot-1"
    s.scheduling.confidence = 0.99
    s.symptoms.red_flags = ["saddle_numbness"]
    decide(s)
    assert s.next_action is NextAction.EMERGENCY_STOP
    assert s.next_action is not NextAction.BOOK


def test_emergency_fires_before_identity_is_known():
    """Red flags are evaluated first — we don't need a name to say 'go to A&E'."""
    s = new_state()
    s.symptoms.red_flags = ["bilateral_leg_weakness"]
    decide(s)
    assert s.emergency is True
    assert s.next_action is NextAction.EMERGENCY_STOP


def test_cauda_equina_advice_names_the_emergency_department():
    t = evaluate(["saddle_numbness", "bowel_bladder_change"])
    assert t.cauda_equina is True
    assert "emergency" in t.spoken_advice.lower()


def test_escalate_flags_do_not_trigger_the_emergency_script():
    s = _complete_state()
    s.symptoms.red_flags = ["unexplained_weight_loss"]
    decide(s)
    assert s.emergency is False
    assert s.next_action is NextAction.ESCALATE
    assert any(f.startswith("URGENT") for f in s.review_flags)


def test_emergency_subsumes_escalation():
    t = evaluate(["saddle_numbness", "fever_with_back_pain"])
    assert t.emergency is True
    assert t.escalate is False


def test_no_flags_is_not_an_emergency():
    t = evaluate([])
    assert t.emergency is False and t.escalate is False


# --- progressive collection -------------------------------------------------


def test_empty_state_asks_for_identity():
    assert decide(new_state()).next_action is NextAction.COLLECT_IDENTITY


def test_identity_without_dob_keeps_collecting_identity():
    s = new_state()
    s.identity.name = "Maria Chen"
    assert decide(s).next_action is NextAction.COLLECT_IDENTITY


def test_known_identity_moves_to_symptoms():
    s = new_state()
    s.identity.name = "Maria Chen"
    s.identity.dob = "1979-04-12"
    s.identity.confidence = 0.95
    assert decide(s).next_action is NextAction.COLLECT_SYMPTOMS


def test_collected_member_id_alone_does_not_count_as_verified():
    """Insurance is settled when Stedi answers, not when we hear a number."""
    s = _complete_state()
    s.insurance.covered = None
    assert decide(s).next_action is NextAction.VERIFY_INSURANCE


def test_verified_insurance_moves_to_slots():
    assert decide(_complete_state()).next_action is NextAction.OFFER_SLOTS


def test_selected_slot_books():
    s = _complete_state()
    s.scheduling.selected_slot_id = "slot-1"
    s.scheduling.confidence = 0.9
    assert decide(s).next_action is NextAction.BOOK


def test_decide_is_idempotent():
    s = _complete_state()
    first = decide(s).next_action
    assert decide(s).next_action is first


# --- the confidence gate ----------------------------------------------------


def test_low_confidence_on_a_settled_domain_raises_a_review_flag():
    s = _complete_state()
    s.identity.confidence = 0.4
    decide(s)
    assert any("REVIEW identity" in f for f in s.review_flags)


def test_low_confidence_does_not_stall_the_call():
    """It flags for a human and keeps going — it must not loop."""
    s = _complete_state()
    s.identity.confidence = 0.4
    decide(s)
    assert s.next_action is NextAction.OFFER_SLOTS


def test_unsettled_domains_are_not_flagged():
    """Scheduling hasn't happened yet, so its 0.0 confidence isn't a finding."""
    s = _complete_state()
    decide(s)
    assert not any("scheduling" in f for f in s.review_flags)


def test_clean_call_raises_no_flags():
    s = _complete_state()
    decide(s)
    assert s.review_flags == []


# --- slot scoring -----------------------------------------------------------


def _slot(sid, hours_out, practitioner="prac-chen", acceptance=0.5):
    return Slot(
        id=sid,
        start=NOW + timedelta(hours=hours_out),
        practitioner_id=practitioner,
        practitioner_name="Dr. Chen",
        historical_acceptance=acceptance,
    )


def test_prior_provider_outranks_a_slightly_earlier_stranger():
    """Continuity of care is the demo moment — it has to actually win."""
    s = _complete_state()
    s.scheduling.provider_pref = "prac-chen"
    slots = [
        _slot("early-stranger", 24, practitioner="prac-other"),
        _slot("chen", 30, practitioner="prac-chen"),
    ]
    assert choose_slot(s, slots, now=NOW).id == "chen"


def test_sooner_wins_when_all_else_is_equal():
    s = _complete_state()
    s.scheduling.provider_pref = None
    slots = [_slot("later", 72), _slot("sooner", 24)]
    assert choose_slot(s, slots, now=NOW).id == "sooner"


def test_past_slots_are_never_offered():
    s = _complete_state()
    slots = [_slot("yesterday", -24), _slot("tomorrow", 24)]
    ranked = rank_slots(slots, severity=5, provider_pref=None, now=NOW)
    assert [x[0].id for x in ranked] == ["tomorrow"]


def test_no_slots_returns_none_rather_than_raising():
    assert choose_slot(_complete_state(), [], now=NOW) is None


def test_higher_severity_raises_every_score():
    """Urgency is a real term in the sum, not decoration."""
    s = _slot("x", 24)
    from engine.scoring import score_slot

    mild = score_slot(s, severity=1, provider_pref=None, now=NOW)
    severe = score_slot(s, severity=9, provider_pref=None, now=NOW)
    assert severe > mild
