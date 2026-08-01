from __future__ import annotations

from datetime import datetime

from services.followups import CompletedVisit, due_for_followup, send_due_followups


def test_one_day_too_soon():
    assert not due_for_followup(datetime(2026, 1, 1), now=datetime(2026, 1, 2))


def test_two_days_is_due():
    assert due_for_followup(datetime(2026, 1, 1), now=datetime(2026, 1, 3))


def test_three_days_is_due():
    assert due_for_followup(datetime(2026, 1, 1), now=datetime(2026, 1, 4))


def test_four_days_too_late():
    assert not due_for_followup(datetime(2026, 1, 1), now=datetime(2026, 1, 5))


def test_send_due_followups_skips_not_due_and_already_sent(monkeypatch):
    sent_to = []
    monkeypatch.setattr(
        "services.agentphone_router.create_task",
        lambda **kw: sent_to.append(kw["phone"]),
    )
    monkeypatch.setattr(
        "services.agentphone_router.mark_followup_sent", lambda phone: None
    )

    visits = [
        CompletedVisit(phone="+1111", provider_name="Dr. A", visit_date=datetime(2026, 1, 1)),
        CompletedVisit(
            phone="+2222",
            provider_name="Dr. B",
            visit_date=datetime(2026, 1, 1),
            followup_sent=True,
        ),
        CompletedVisit(phone="+3333", provider_name="Dr. C", visit_date=datetime(2026, 1, 3)),
    ]

    sent = send_due_followups(visits, now=datetime(2026, 1, 3))

    assert [v.phone for v in sent] == ["+1111"]
    assert sent_to == ["+1111"]
    assert visits[0].followup_sent is True
