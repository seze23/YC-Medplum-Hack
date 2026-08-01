"""In-conversation retrieval.

The thesis is that the longitudinal record makes each conversation smarter. The
obstacle is that a FHIR query mid-sentence stalls the agent and breaks the
illusion. Retrieval has to land in single-digit milliseconds or the pause gives
it away.

The payoff line on camera:

    "Last time you saw Dr. Chen for the same right shoulder — want me to put
     you back with her?"

said with no pause at all.

--- Status ---------------------------------------------------------------------

`MOSS_API_KEY` is unset and the Moss API surface is not wired yet. Until the
docs land, this module runs a local in-memory index that hits the same latency
bar and demos identically. The public interface below (`index_patient`,
`retrieve`) is what the Moss client will implement, so swapping it in is a
change to this file only — no caller changes.

Do not claim on camera that Moss is powering retrieval until the client is
actually wired. The local path is a legitimate fallback; describing it as
something it isn't is the kind of thing that unravels under a judge's follow-up.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from shared.config import MOSS_API_KEY

# patient_id -> list of short, speakable context strings.
_INDEX: dict[str, list[str]] = {}


async def index_patient(patient_id: str, history: dict[str, Any]) -> None:
    """Flatten a patient's FHIR history into retrievable one-liners."""
    facts: list[str] = []

    for condition in history.get("conditions", []):
        text = (condition.get("code") or {}).get("text", "")
        status = _clinical_status(condition)
        when = (condition.get("recordedDate") or "")[:10]
        if text:
            facts.append(f"Prior episode: {text} ({status}, recorded {when}).")

    for encounter in history.get("encounters", []):
        reason = ", ".join(
            r.get("text", "") for r in encounter.get("reasonCode", []) if r.get("text")
        )
        practitioner = _practitioner_display(encounter)
        period = encounter.get("period") or {}
        span = f"{(period.get('start') or '')[:10]} to {(period.get('end') or '')[:10]}"
        if reason:
            facts.append(
                f"Seen for {reason}"
                + (f" by {practitioner}" if practitioner else "")
                + f" ({span})."
            )

    appointments = history.get("appointments", [])
    cancelled = sum(1 for a in appointments if a.get("status") == "cancelled")
    if cancelled:
        facts.append(f"Cancellation history: {cancelled} cancelled appointment(s).")

    _INDEX[patient_id] = facts
    logger.info(f"Indexed {len(facts)} facts for patient {patient_id}")


async def retrieve(patient_id: str, *, query: str, limit: int = 3) -> list[str]:
    """Top facts for this moment in the conversation. Must be fast."""
    if MOSS_API_KEY:
        # TODO: real Moss client goes here once the API surface is known.
        # Keep the same signature so nothing upstream changes.
        pass

    facts = _INDEX.get(patient_id, [])
    if not facts:
        return []

    terms = {t for t in query.lower().split() if len(t) > 2}
    if not terms:
        return facts[:limit]

    scored = sorted(
        facts,
        key=lambda fact: -sum(1 for t in terms if t in fact.lower()),
    )
    return scored[:limit]


def clear() -> None:
    """Reset between demo takes."""
    _INDEX.clear()


def _clinical_status(condition: dict[str, Any]) -> str:
    coding = (condition.get("clinicalStatus") or {}).get("coding") or [{}]
    return coding[0].get("code", "unknown")


def _practitioner_display(encounter: dict[str, Any]) -> str:
    for participant in encounter.get("participant", []):
        display = (participant.get("individual") or {}).get("display")
        if display:
            return display
    return ""
