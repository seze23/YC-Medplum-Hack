from __future__ import annotations

from engine.patient_score import PatientHistory, ScoringInput, rank_waitlist, score_patient


def _input(**overrides) -> ScoringInput:
    defaults = dict(
        severity=5,
        insurance_covered=True,
        copay=20.0,
        deductible_remaining=0.0,
        distance_miles=5.0,
        specialty_match=True,
        slot_available_soon=True,
        history=PatientHistory(),
    )
    defaults.update(overrides)
    return ScoringInput(**defaults)


def test_score_is_bounded_zero_to_one():
    score = score_patient(_input())
    assert 0.0 <= score <= 1.0


def test_higher_severity_scores_higher_all_else_equal():
    low = score_patient(_input(severity=1))
    high = score_patient(_input(severity=9))
    assert high > low


def test_uncovered_insurance_scores_lower_than_covered():
    covered = score_patient(_input(insurance_covered=True, copay=0, deductible_remaining=0))
    uncovered = score_patient(_input(insurance_covered=False))
    assert covered > uncovered


def test_unresolved_insurance_is_neutral_not_penalized():
    unresolved = score_patient(_input(insurance_covered=None))
    uncovered = score_patient(_input(insurance_covered=False))
    covered = score_patient(_input(insurance_covered=True, copay=0, deductible_remaining=0))
    assert uncovered < unresolved < covered


def test_farther_distance_scores_lower():
    near = score_patient(_input(distance_miles=1))
    far = score_patient(_input(distance_miles=25))
    assert near > far


def test_new_patient_with_no_history_is_neutral_not_penalized():
    """A patient with zero history shouldn't score as if they're unreliable —
    that would unfairly bury every new patient at the bottom of the waitlist.
    """
    new_patient = score_patient(_input(history=PatientHistory()))
    reliable_patient = score_patient(
        _input(history=PatientHistory(accepted_offer_count=5))
    )
    unreliable_patient = score_patient(
        _input(history=PatientHistory(no_show_count=5))
    )
    assert unreliable_patient < new_patient < reliable_patient


def test_rank_waitlist_orders_highest_score_first():
    low = _input(severity=1, distance_miles=25)
    high = _input(severity=9, distance_miles=1)
    ranked = rank_waitlist([("low-patient", low), ("high-patient", high)])
    assert [pid for pid, _score in ranked] == ["high-patient", "low-patient"]
