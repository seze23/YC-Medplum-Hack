from __future__ import annotations

import services.urgent_escalation as flow_mod


def test_sends_to_staff_alert_email(monkeypatch):
    monkeypatch.setenv("STAFF_ALERT_EMAIL", "staff@example.com")
    sent = []
    monkeypatch.setattr(
        flow_mod, "send_urgent_alert", lambda to_email, **kw: sent.append((to_email, kw))
    )

    result = flow_mod.handle_urgent_concern(
        from_phone="+1111", patient_name="Jordan Lee", message="this is urgent"
    )

    assert result is True
    assert sent[0][0] == "staff@example.com"
    assert sent[0][1]["patient_name"] == "Jordan Lee"


def test_falls_back_to_smtp_username_if_no_staff_email(monkeypatch):
    monkeypatch.delenv("STAFF_ALERT_EMAIL", raising=False)
    monkeypatch.setenv("SMTP_USERNAME", "fallback@example.com")
    sent = []
    monkeypatch.setattr(
        flow_mod, "send_urgent_alert", lambda to_email, **kw: sent.append(to_email)
    )

    flow_mod.handle_urgent_concern(from_phone="+1111", patient_name=None, message="help")

    assert sent == ["fallback@example.com"]


def test_no_destination_configured_returns_false(monkeypatch):
    monkeypatch.delenv("STAFF_ALERT_EMAIL", raising=False)
    monkeypatch.delenv("SMTP_USERNAME", raising=False)

    result = flow_mod.handle_urgent_concern(
        from_phone="+1111", patient_name=None, message="help"
    )

    assert result is False


def test_fires_for_unknown_patient(monkeypatch):
    """The whole point: an unrecognized number saying something urgent must
    not be dropped just because patient_lookup found nothing."""
    monkeypatch.setenv("STAFF_ALERT_EMAIL", "staff@example.com")
    sent = []
    monkeypatch.setattr(
        flow_mod, "send_urgent_alert", lambda to_email, **kw: sent.append(kw)
    )

    result = flow_mod.handle_urgent_concern(
        from_phone="+19995551234", patient_name=None, message="emergency, please call"
    )

    assert result is True
    assert sent[0]["patient_name"] is None


def test_send_failure_returns_false(monkeypatch):
    monkeypatch.setenv("STAFF_ALERT_EMAIL", "staff@example.com")

    def raise_error(*a, **kw):
        raise RuntimeError("simulated SMTP failure")

    monkeypatch.setattr(flow_mod, "send_urgent_alert", raise_error)

    result = flow_mod.handle_urgent_concern(
        from_phone="+1111", patient_name=None, message="urgent"
    )

    assert result is False
