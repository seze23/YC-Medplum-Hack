"""Slot scoring.

A weighted sum over four signals. This is deliberately not a learned policy —
you cannot train one in an afternoon, and pretending otherwise gets you
cross-examined.

What it *is*: the reward function. Every term here is something you would
optimise against once you have call volume. Say that out loud; don't claim you
trained anything today.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

WEIGHTS = {
    "urgency": 0.35,  # sicker patients get seen sooner
    "provider_match": 0.25,  # continuity of care beats a random opening
    "historical_acceptance": 0.20,  # slots this patient has actually taken before
    "time_to_appointment": 0.20,  # sooner is better, all else equal
}


@dataclass
class Slot:
    """A Medplum Slot, flattened to what scoring needs."""

    id: str
    start: datetime
    practitioner_id: str
    practitioner_name: str
    # Fraction of offers at this weekday/time-of-day this patient has accepted
    # historically. 0.5 is the neutral prior for a new patient.
    historical_acceptance: float = 0.5


def urgency_score(severity: int, escalate: bool) -> float:
    """0-1. Severity is patient-reported 0-10."""
    base = min(max(severity, 0), 10) / 10.0
    if escalate:
        base = max(base, 0.9)
    return base


def provider_match_score(slot: Slot, provider_pref: str | None) -> float:
    if provider_pref is None:
        return 0.5  # no preference expressed — neutral, not penalised
    return 1.0 if slot.practitioner_id == provider_pref else 0.0


def time_to_appointment_score(slot: Slot, now: datetime) -> float:
    """1.0 for right now, decaying to 0.0 at seven days out."""
    hours_out = (slot.start - now).total_seconds() / 3600.0
    if hours_out <= 0:
        return 0.0  # already passed; filtered upstream, belt and braces
    horizon = 24.0 * 7
    return max(0.0, 1.0 - (hours_out / horizon))


def score_slot(
    slot: Slot,
    *,
    severity: int,
    provider_pref: str | None,
    escalate: bool = False,
    now: datetime | None = None,
) -> float:
    now = now or datetime.now(slot.start.tzinfo)
    terms = {
        "urgency": urgency_score(severity, escalate),
        "provider_match": provider_match_score(slot, provider_pref),
        "historical_acceptance": min(max(slot.historical_acceptance, 0.0), 1.0),
        "time_to_appointment": time_to_appointment_score(slot, now),
    }
    return sum(WEIGHTS[k] * v for k, v in terms.items())


def rank_slots(
    slots: list[Slot],
    *,
    severity: int,
    provider_pref: str | None,
    escalate: bool = False,
    now: datetime | None = None,
) -> list[tuple[Slot, float]]:
    """Best first. Ties broken by earliest start so the order is stable."""
    # Match the slots' awareness. Medplum returns tz-aware timestamps, and a
    # bare datetime.now() here raises "can't subtract offset-naive and
    # offset-aware datetimes" the moment it meets real data.
    if now is None:
        tzinfo = slots[0].start.tzinfo if slots else None
        now = datetime.now(tzinfo)
    scored = [
        (
            s,
            score_slot(
                s,
                severity=severity,
                provider_pref=provider_pref,
                escalate=escalate,
                now=now,
            ),
        )
        for s in slots
        if (s.start - now).total_seconds() > 0
    ]
    scored.sort(key=lambda pair: (-pair[1], pair[0].start))
    return scored
