from __future__ import annotations

import services.referral_flow as flow_mod


def test_sends_and_returns_true(monkeypatch):
    sent = []
    monkeypatch.setattr(
        flow_mod, "send_referral_request", lambda to_email, **kw: sent.append(to_email)
    )

    result = flow_mod.handle_referral_required(
        "patient@example.com", provider_name="Dr. Chen"
    )

    assert result is True
    assert sent == ["patient@example.com"]


def test_no_email_returns_false_without_raising():
    result = flow_mod.handle_referral_required(None, provider_name="Dr. Chen")
    assert result is False


def test_send_failure_returns_false_without_raising(monkeypatch):
    def raise_error(*a, **kw):
        raise RuntimeError("simulated SMTP failure")

    monkeypatch.setattr(flow_mod, "send_referral_request", raise_error)

    result = flow_mod.handle_referral_required(
        "patient@example.com", provider_name="Dr. Chen"
    )

    assert result is False
