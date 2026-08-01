from __future__ import annotations

import services.cancellation_flow as flow_mod
from engine.patient_score import PatientHistory, ScoringInput


def _candidate(patient_id: str, email: str, *, severity: int) -> tuple[str, str, ScoringInput]:
    return (
        patient_id,
        email,
        ScoringInput(
            severity=severity,
            insurance_covered=True,
            copay=0,
            deductible_remaining=0,
            distance_miles=5,
            specialty_match=True,
            slot_available_soon=True,
            history=PatientHistory(),
        ),
    )


def test_handle_cancellation_emails_top_n_waitlist_candidates(monkeypatch):
    candidates = [
        _candidate("p1", "low@example.com", severity=2),
        _candidate("p2", "high@example.com", severity=9),
        _candidate("p3", "mid@example.com", severity=5),
    ]
    monkeypatch.setattr(
        flow_mod, "get_waitlist_candidates", lambda **kw: candidates
    )

    offer_calls = []
    monkeypatch.setattr(
        flow_mod,
        "send_waitlist_offer",
        lambda to_email, **kw: offer_calls.append(to_email),
    )
    survey_calls = []
    monkeypatch.setattr(
        flow_mod,
        "send_cancellation_exit_survey",
        lambda to_email, **kw: survey_calls.append(to_email),
    )

    offered = flow_mod.handle_cancellation(
        cancelling_patient_email="cancelled@example.com",
        freed_slot_provider="Dr. Chen",
        freed_slot_when="Tuesday at 2pm",
        top_n=2,
    )

    assert offered == ["high@example.com", "mid@example.com"]
    assert survey_calls == ["cancelled@example.com"]


def test_no_exit_survey_when_email_unknown(monkeypatch):
    monkeypatch.setattr(flow_mod, "get_waitlist_candidates", lambda **kw: [])
    survey_calls = []
    monkeypatch.setattr(
        flow_mod,
        "send_cancellation_exit_survey",
        lambda to_email, **kw: survey_calls.append(to_email),
    )

    flow_mod.handle_cancellation(
        cancelling_patient_email=None,
        freed_slot_provider="Dr. Chen",
        freed_slot_when="Tuesday at 2pm",
    )

    assert survey_calls == []


def test_empty_waitlist_offers_nothing(monkeypatch):
    monkeypatch.setattr(flow_mod, "get_waitlist_candidates", lambda **kw: [])
    monkeypatch.setattr(flow_mod, "send_cancellation_exit_survey", lambda *a, **kw: None)

    offered = flow_mod.handle_cancellation(
        cancelling_patient_email="cancelled@example.com",
        freed_slot_provider="Dr. Chen",
        freed_slot_when="Tuesday at 2pm",
    )

    assert offered == []


def test_one_failed_waitlist_send_does_not_block_the_others(monkeypatch):
    candidates = [
        _candidate("p1", "ok1@example.com", severity=9),
        _candidate("p2", "fails@example.com", severity=8),
        _candidate("p3", "ok2@example.com", severity=7),
    ]
    monkeypatch.setattr(flow_mod, "get_waitlist_candidates", lambda **kw: candidates)
    monkeypatch.setattr(flow_mod, "send_cancellation_exit_survey", lambda *a, **kw: None)

    def flaky_send(to_email, **kw):
        if to_email == "fails@example.com":
            raise RuntimeError("simulated SMTP failure")

    monkeypatch.setattr(flow_mod, "send_waitlist_offer", flaky_send)

    offered = flow_mod.handle_cancellation(
        cancelling_patient_email="cancelled@example.com",
        freed_slot_provider="Dr. Chen",
        freed_slot_when="Tuesday at 2pm",
        top_n=3,
    )

    # fails@ is silently skipped -- ok1@ and ok2@ still went through, and the
    # function itself did not raise.
    assert offered == ["ok1@example.com", "ok2@example.com"]


def test_failed_exit_survey_does_not_block_waitlist_offers(monkeypatch):
    candidates = [_candidate("p1", "ok@example.com", severity=9)]
    monkeypatch.setattr(flow_mod, "get_waitlist_candidates", lambda **kw: candidates)

    def raise_error(*a, **kw):
        raise RuntimeError("simulated SMTP failure")

    monkeypatch.setattr(flow_mod, "send_cancellation_exit_survey", raise_error)
    # Isolating what this test actually proves: the waitlist offer succeeds
    # on its own merits. Without this mock it falls through to the real SMTP
    # client and fails for an unrelated reason (no .env loaded in this
    # process) — caught the same way, but that's a different bug than the
    # one this test is checking for.
    offer_calls = []
    monkeypatch.setattr(
        flow_mod, "send_waitlist_offer", lambda to_email, **kw: offer_calls.append(to_email)
    )

    offered = flow_mod.handle_cancellation(
        cancelling_patient_email="cancelled@example.com",
        freed_slot_provider="Dr. Chen",
        freed_slot_when="Tuesday at 2pm",
    )

    # The exit survey blew up, but the waitlist offer -- arguably the more
    # important half, since it's what actually fills the freed slot -- still
    # went out.
    assert offered == ["ok@example.com"]
