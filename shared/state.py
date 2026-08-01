"""The state contract.

This is the only thing the voice track and the data track share. The extractor
produces a CallState; the decision engine consumes it. Nothing else crosses the
boundary.

Two rules that make the whole architecture work:

1. The LLM never writes to a service. It fills this object and nothing else.
2. Confidence is scored per domain, and scored LOW when the model is inferring
   rather than hearing. A domain below CONFIDENCE_THRESHOLD becomes a human
   review Task, spoken aloud on the call.

The function-calling schema lives at the bottom of this file so the prompt and
the parser cannot drift apart.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

# A domain below this is not trusted. It creates a Medplum Task for human review
# and the agent says so on the call.
CONFIDENCE_THRESHOLD = 0.7


class NextAction(str, Enum):
    """The only actions the engine can return. Deterministic, exhaustive."""

    COLLECT_IDENTITY = "collect_identity"
    COLLECT_SYMPTOMS = "collect_symptoms"
    VERIFY_INSURANCE = "verify_insurance"
    OFFER_SLOTS = "offer_slots"
    BOOK = "book"
    ESCALATE = "escalate"
    EMERGENCY_STOP = "emergency_stop"


@dataclass
class Identity:
    patient_id: str | None = None
    name: str = ""
    dob: str = ""  # ISO 8601, YYYY-MM-DD
    phone: str = ""
    is_returning: bool = False
    confidence: float = 0.0


@dataclass
class Symptoms:
    body_site: str = ""
    onset: str = ""
    severity: int = 0  # patient-reported, 0-10
    red_flags: list[str] = field(default_factory=list)
    specialty: str = ""
    confidence: float = 0.0


@dataclass
class Insurance:
    payer: str = ""
    member_id: str = ""
    # Filled by the Stedi call, not by the LLM. The agent speaks these aloud.
    covered: bool | None = None
    copay: float | None = None
    deductible_remaining: float | None = None
    referral_required: bool | None = None
    referral_valid_through: str | None = None
    confidence: float = 0.0


@dataclass
class Scheduling:
    preferred_days: list[str] = field(default_factory=list)
    preferred_time: str = ""  # morning | afternoon | evening | ""
    provider_pref: str | None = None  # Practitioner id, not a display name
    selected_slot_id: str | None = None
    confidence: float = 0.0


@dataclass
class CallState:
    identity: Identity = field(default_factory=Identity)
    symptoms: Symptoms = field(default_factory=Symptoms)
    insurance: Insurance = field(default_factory=Insurance)
    scheduling: Scheduling = field(default_factory=Scheduling)

    emergency: bool = False
    next_action: NextAction = NextAction.COLLECT_IDENTITY

    # Set by the confidence gate. Each entry becomes a Medplum Task.
    review_flags: list[str] = field(default_factory=list)

    # Populated by Moss before the LLM speaks. Read-only context, never written
    # back to the record — this is what makes the agent sound like an employee.
    retrieved_context: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["next_action"] = self.next_action.value
        return d

    def low_confidence_domains(self) -> list[str]:
        """Domains the engine should not act on without human review."""
        return [
            name
            for name, domain in self._domains()
            if domain.confidence < CONFIDENCE_THRESHOLD
        ]

    def _domains(self):
        return (
            ("identity", self.identity),
            ("symptoms", self.symptoms),
            ("insurance", self.insurance),
            ("scheduling", self.scheduling),
        )


def new_state() -> CallState:
    return CallState()


# --- LLM function-calling schema -------------------------------------------
#
# The extractor calls this one tool and nothing else. Every field is optional:
# the model fills in what it heard this turn and leaves the rest alone, so state
# accumulates across turns instead of being rebuilt each time.

_CONFIDENCE = {
    "type": "number",
    "minimum": 0.0,
    "maximum": 1.0,
    "description": (
        "How confident you are in THIS domain. Score above 0.9 only when the "
        "caller stated it clearly and you repeated it back. Score below 0.7 "
        "when you are inferring, guessing at a spelling, or heard it through "
        "noise. Under-confidence is cheap; over-confidence books the wrong "
        "appointment."
    ),
}

RED_FLAG_CODES = [
    "bowel_bladder_change",
    "saddle_numbness",
    "bilateral_leg_weakness",
    "progressive_neuro_deficit",
    "unexplained_weight_loss",
    "fever_with_back_pain",
    "recent_major_trauma",
]

EXTRACTION_TOOL = {
    "name": "update_call_state",
    "description": (
        "Record what you learned from the caller this turn. Only include fields "
        "you actually heard — omit everything else. Never invent a value to "
        "fill a field."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "identity": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "dob": {"type": "string", "description": "YYYY-MM-DD"},
                    "phone": {"type": "string"},
                    "confidence": _CONFIDENCE,
                },
                "additionalProperties": False,
            },
            "symptoms": {
                "type": "object",
                "properties": {
                    "body_site": {
                        "type": "string",
                        "description": "e.g. 'right shoulder', 'lower back'",
                    },
                    "onset": {
                        "type": "string",
                        "description": "e.g. '3 weeks ago', 'yesterday'",
                    },
                    "severity": {"type": "integer", "minimum": 0, "maximum": 10},
                    "red_flags": {
                        "type": "array",
                        "items": {"type": "string", "enum": RED_FLAG_CODES},
                        "description": (
                            "Report a flag if the caller describes it in ANY "
                            "words. They will not use clinical terms — "
                            "'I've been having accidents' is bowel_bladder_change, "
                            "'numb where I sit' is saddle_numbness, 'both legs "
                            "give out' is bilateral_leg_weakness."
                        ),
                    },
                    "specialty": {"type": "string"},
                    "confidence": _CONFIDENCE,
                },
                "additionalProperties": False,
            },
            "insurance": {
                "type": "object",
                "properties": {
                    "payer": {"type": "string"},
                    "member_id": {"type": "string"},
                    "confidence": _CONFIDENCE,
                },
                "additionalProperties": False,
            },
            "scheduling": {
                "type": "object",
                "properties": {
                    "preferred_days": {"type": "array", "items": {"type": "string"}},
                    "preferred_time": {
                        "type": "string",
                        "enum": ["morning", "afternoon", "evening"],
                    },
                    "provider_pref": {"type": "string"},
                    "confidence": _CONFIDENCE,
                },
                "additionalProperties": False,
            },
        },
        "additionalProperties": False,
    },
}


def apply_extraction(state: CallState, payload: dict[str, Any]) -> CallState:
    """Merge one tool call into the running state. Absent fields are untouched.

    Confidence takes the latest value rather than the max — if the caller
    corrects themselves and the model gets less sure, the gate should catch it.
    """
    for domain_name, domain in state._domains():
        incoming = payload.get(domain_name)
        if not incoming:
            continue
        for key, value in incoming.items():
            if value in (None, "", []):
                continue
            if hasattr(domain, key):
                setattr(domain, key, value)
    return state
