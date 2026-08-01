"""Continuous patient scoring — deterministic, history-informed. Not Q-learning:
there's no call volume yet to learn a policy from. Same principle as
engine/scoring.py's rank_slots() already applies here — this is the reward
function a future learned policy would optimize, not the policy itself. Say
that if asked; don't claim this is trained.

Recomputed any time new data lands about a patient (a call, a text reply, an
eligibility check) rather than in a batch job — there's no reason to hold a
stale score. Feeds three things:

  1. The admin/practitioner triage view — who needs attention.
  2. Waitlist backfill ordering — who gets offered a freed slot first
     (see services/cancellation_flow.py).
  3. Outbound targeting — worth an email, or a known no-show risk.

Deliberately separate from rank_slots() in this file: that scores SLOTS for
one call. This scores PATIENTS, persistently, across calls.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PatientHistory:
    """Everything about a patient that predates the current interaction."""

    no_show_count: int = 0
    accepted_offer_count: int = 0
    declined_offer_count: int = 0


@dataclass
class ScoringInput:
    severity: int  # 0-10, from CallState.symptoms
    insurance_covered: bool | None  # from Stedi; None = not yet resolved
    copay: float | None
    deductible_remaining: float | None
    distance_miles: float | None
    specialty_match: bool
    slot_available_soon: bool
    history: PatientHistory


# Sums to 1.0. Adjust here, not by hand-tweaking component functions — keeps
# the "why did patient X outrank patient Y" question answerable by reading
# one table instead of tracing five functions.
WEIGHTS = {
    "severity": 0.25,
    "insurance": 0.20,
    "distance": 0.15,
    "specialty_match": 0.15,
    "availability": 0.10,
    "history": 0.15,
}


def _severity_component(severity: int) -> float:
    return max(0.0, min(severity, 10)) / 10.0


def _insurance_component(
    covered: bool | None, copay: float | None, deductible_remaining: float | None
) -> float:
    if covered is None:
        return 0.5  # unresolved, not yet penalized
    if not covered:
        return 0.0
    # Less remaining financial friction => more likely the patient actually
    # completes the visit => higher score.
    friction = (copay or 0.0) + (deductible_remaining or 0.0)
    return max(0.0, 1.0 - min(friction, 500.0) / 500.0)


def _distance_component(distance_miles: float | None) -> float:
    if distance_miles is None:
        return 0.5
    return max(0.0, 1.0 - min(distance_miles, 30.0) / 30.0)


def _history_component(history: PatientHistory) -> float:
    total = history.accepted_offer_count + history.declined_offer_count + history.no_show_count
    if total == 0:
        return 0.5  # no history yet — neutral, not penalized for being new
    reliability = history.accepted_offer_count / total
    no_show_penalty = history.no_show_count / total
    return max(0.0, min(1.0, reliability - no_show_penalty))


def score_patient(inp: ScoringInput) -> float:
    """0.0-1.0, higher = higher priority. Pure — no I/O, no LLM, no clock."""
    components = {
        "severity": _severity_component(inp.severity),
        "insurance": _insurance_component(
            inp.insurance_covered, inp.copay, inp.deductible_remaining
        ),
        "distance": _distance_component(inp.distance_miles),
        "specialty_match": 1.0 if inp.specialty_match else 0.0,
        "availability": 1.0 if inp.slot_available_soon else 0.0,
        "history": _history_component(inp.history),
    }
    return sum(WEIGHTS[name] * value for name, value in components.items())


def rank_waitlist(candidates: list[tuple[str, ScoringInput]]) -> list[tuple[str, float]]:
    """(patient_id, score) sorted highest first. Used to pick who gets
    offered a freed slot after a cancellation."""
    scored = [(patient_id, score_patient(inp)) for patient_id, inp in candidates]
    return sorted(scored, key=lambda pair: pair[1], reverse=True)
