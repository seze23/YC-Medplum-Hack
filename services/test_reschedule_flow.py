from __future__ import annotations

import services.reschedule_flow as flow_mod


def test_handle_reschedule_sends_available_options(monkeypatch):
    monkeypatch.setattr(
        flow_mod, "get_available_slots", lambda **kw: ["Wed 10am", "Thu 3pm", "Fri 1pm"]
    )
    sent = []
    monkeypatch.setattr(
        flow_mod,
        "send_reschedule_options",
        lambda to_email, **kw: sent.append((to_email, kw["options"])),
    )

    offered = flow_mod.handle_reschedule(
        patient_email="patient@example.com", provider_name="Dr. Chen"
    )

    assert offered == ["Wed 10am", "Thu 3pm", "Fri 1pm"]
    assert sent == [("patient@example.com", ["Wed 10am", "Thu 3pm", "Fri 1pm"])]


def test_respects_max_options(monkeypatch):
    monkeypatch.setattr(
        flow_mod, "get_available_slots", lambda **kw: ["a", "b", "c", "d", "e"]
    )
    monkeypatch.setattr(flow_mod, "send_reschedule_options", lambda *a, **kw: None)

    offered = flow_mod.handle_reschedule(
        patient_email="patient@example.com", provider_name="Dr. Chen", max_options=2
    )

    assert offered == ["a", "b"]


def test_no_email_skips_without_raising():
    offered = flow_mod.handle_reschedule(patient_email=None, provider_name="Dr. Chen")
    assert offered == []


def test_no_availability_returns_empty(monkeypatch):
    monkeypatch.setattr(flow_mod, "get_available_slots", lambda **kw: [])
    sent = []
    monkeypatch.setattr(
        flow_mod, "send_reschedule_options", lambda *a, **kw: sent.append(1)
    )

    offered = flow_mod.handle_reschedule(
        patient_email="patient@example.com", provider_name="Dr. Chen"
    )

    assert offered == []
    assert sent == []  # never even attempted a send with nothing to offer


def test_send_failure_is_caught_and_returns_empty(monkeypatch):
    monkeypatch.setattr(flow_mod, "get_available_slots", lambda **kw: ["Wed 10am"])

    def raise_error(*a, **kw):
        raise RuntimeError("simulated SMTP failure")

    monkeypatch.setattr(flow_mod, "send_reschedule_options", raise_error)

    offered = flow_mod.handle_reschedule(
        patient_email="patient@example.com", provider_name="Dr. Chen"
    )

    assert offered == []  # did not raise, and correctly reports nothing offered
