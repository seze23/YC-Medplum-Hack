"""_handle_inbound's classification logic, now that it doesn't make a network
call. Same spirit as engine/test_decision.py: given text, assert the right
category and the right hooks got called — nothing about HTTP or FastAPI.
"""

from __future__ import annotations

import services.agentphone_router as router_mod


def _reset():
    router_mod._awaiting_progress_update.clear()
    router_mod.patient_lookup = router_mod._default_patient_lookup
    router_mod.record_progress_update = router_mod._default_record_progress_update
    router_mod.create_task = router_mod._default_create_task


def test_urgent_keyword_creates_urgent_task(monkeypatch):
    _reset()
    calls = []
    monkeypatch.setattr(router_mod, "create_task", lambda **kw: calls.append(kw))

    router_mod._handle_inbound("+1111", "this is urgent please help")

    assert calls[0]["category"] == "URGENT_CONCERN"


def test_urgent_overrides_pending_progress_update(monkeypatch):
    """Safety-first ordering: even mid-followup-conversation, an urgent
    word takes priority over the progress-update branch."""
    _reset()
    router_mod.mark_followup_sent("+1111")
    calls = []
    recorded = []
    monkeypatch.setattr(router_mod, "create_task", lambda **kw: calls.append(kw))
    monkeypatch.setattr(
        router_mod, "record_progress_update", lambda phone, reply: recorded.append(reply)
    )

    router_mod._handle_inbound("+1111", "emergency, something is wrong")

    assert calls[0]["category"] == "URGENT_CONCERN"
    assert recorded == []  # never reached the progress-update branch


def test_urgent_fires_for_unknown_patient(monkeypatch):
    _reset()  # patient_lookup default returns None for any number
    calls = []
    monkeypatch.setattr(router_mod, "create_task", lambda **kw: calls.append(kw))

    router_mod._handle_inbound("+19995551234", "911 help")

    assert calls[0]["category"] == "URGENT_CONCERN"
    assert calls[0]["patient"] is None


def test_cancel_keyword_creates_cancellation_task(monkeypatch):
    _reset()
    calls = []
    monkeypatch.setattr(router_mod, "create_task", lambda **kw: calls.append(kw))

    router_mod._handle_inbound("+1111", "I need to cancel my appointment")

    assert len(calls) == 1
    assert calls[0]["category"] == "CANCELLATION"
    assert calls[0]["phone"] == "+1111"


def test_reschedule_keyword_is_distinct_from_cancel(monkeypatch):
    _reset()
    calls = []
    monkeypatch.setattr(router_mod, "create_task", lambda **kw: calls.append(kw))

    router_mod._handle_inbound("+1111", "can we reschedule to Friday")

    assert calls[0]["category"] == "RESCHEDULE_REQUEST"


def test_confirm_keyword(monkeypatch):
    _reset()
    calls = []
    monkeypatch.setattr(router_mod, "create_task", lambda **kw: calls.append(kw))

    router_mod._handle_inbound("+1111", "yes")

    assert calls[0]["category"] == "CONFIRMATION"


def test_unrecognized_text_is_general_inquiry(monkeypatch):
    _reset()
    calls = []
    monkeypatch.setattr(router_mod, "create_task", lambda **kw: calls.append(kw))

    router_mod._handle_inbound("+1111", "does my insurance cover this")

    assert calls[0]["category"] == "GENERAL_INQUIRY"


def test_pending_progress_update_takes_priority_over_keywords(monkeypatch):
    """A patient replying to a follow-up check-in with 'yes I'm cancelling PT
    entirely' should be treated as a progress update, not a cancellation —
    it's answering a different question than the keyword branches assume.
    """
    _reset()
    router_mod.mark_followup_sent("+1111")

    calls = []
    recorded = []
    monkeypatch.setattr(router_mod, "create_task", lambda **kw: calls.append(kw))
    monkeypatch.setattr(
        router_mod, "record_progress_update", lambda phone, reply: recorded.append(reply)
    )

    router_mod._handle_inbound("+1111", "cancel, I'm feeling much better")

    assert calls[0]["category"] == "PROGRESS_UPDATE"
    assert recorded == ["cancel, I'm feeling much better"]
    assert "+1111" not in router_mod._awaiting_progress_update  # consumed, not re-armed


def test_patient_lookup_result_is_passed_through():
    _reset()
    router_mod.patient_lookup = lambda phone: {"id": "Patient/123"}
    calls = []
    router_mod.create_task = lambda **kw: calls.append(kw)

    router_mod._handle_inbound("+1111", "cancel")

    assert calls[0]["patient"] == {"id": "Patient/123"}


def test_create_task_failure_does_not_propagate(monkeypatch):
    """The actual claim: a downstream failure (email send, future Medplum
    write) inside create_task must not blow up _handle_inbound. If this
    raises, the test fails on the exception itself -- no assertion needed
    beyond "this call completes."
    """
    _reset()

    def boom(**kw):
        raise RuntimeError("simulated failure")

    monkeypatch.setattr(router_mod, "create_task", boom)
    router_mod._handle_inbound("+1111", "cancel")  # must not raise


def test_record_progress_update_failure_does_not_propagate(monkeypatch):
    _reset()
    router_mod.mark_followup_sent("+1111")

    def boom(phone, reply):
        raise RuntimeError("simulated failure")

    monkeypatch.setattr(router_mod, "record_progress_update", boom)
    monkeypatch.setattr(router_mod, "create_task", lambda **kw: None)

    router_mod._handle_inbound("+1111", "feeling better")  # must not raise


def test_webhook_returns_200_even_when_create_task_raises(monkeypatch):
    """End-to-end proof at the actual HTTP layer, not just the handler
    function -- this is what AgentPhone sees. A downstream failure must
    still come back as 200, since SMS webhooks are fire-and-forget and a
    non-200 could trigger AgentPhone retries we don't want.
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    _reset()
    monkeypatch.delenv("AGENTPHONE_WEBHOOK_SECRET", raising=False)
    monkeypatch.setattr(
        router_mod,
        "create_task",
        lambda **kw: (_ for _ in ()).throw(RuntimeError("simulated failure")),
    )

    app = FastAPI()
    app.include_router(router_mod.router)
    client = TestClient(app)

    resp = client.post(
        "/agentphone/webhook",
        json={
            "event": "agent.message",
            "channel": "sms",
            "data": {"from": "+1111", "to": "+2222", "message": "cancel"},
        },
    )

    assert resp.status_code == 200
