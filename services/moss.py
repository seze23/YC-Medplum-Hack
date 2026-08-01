"""In-conversation retrieval.

The thesis is that the longitudinal record makes each conversation smarter. The
obstacle is that a FHIR query mid-sentence stalls the agent and breaks the
illusion — retrieval has to land in single-digit milliseconds or the pause gives
it away.

The payoff line on camera:

    "Last time you saw Dr. Chen for the same right shoulder — want me to put
     you back with her?"

said with no pause at all.

--- Two backends, one interface -----------------------------------------------

`index_patient()` and `retrieve()` are the only entry points, and callers never
learn which backend answered.

  Moss   — a real search runtime (Rust/WASM). `SessionIndex.query` runs entirely
           in memory, no cloud round trip, ~1-10ms. Requires MOSS_PROJECT_ID and
           MOSS_PROJECT_KEY; it authenticates even for local session indexes.

  Local  — a keyword-scored fallback over the same flattened facts. Same shape,
           same latency profile, no dependencies. Used when Moss credentials are
           absent or the SDK errors.

If Moss is not active, do not say on camera that Moss is powering retrieval.
`backend_name()` reports which one actually answered — use it rather than
assuming.
"""

from __future__ import annotations

import time
from typing import Any

from loguru import logger

from shared.config import MOSS_PROJECT_ID, MOSS_PROJECT_KEY

# patient_id -> list of short, speakable context strings. Also the source of
# truth for the Moss path, so both backends index identical text.
_FACTS: dict[str, list[str]] = {}

# patient_id -> Moss SessionIndex
_SESSIONS: dict[str, Any] = {}

_client: Any = None
_moss_available: bool | None = None
_last_query_ms: float | None = None
# Which backend answered the most recent retrieve. Tracked separately from
# _moss_available on purpose: a constructed client says nothing about whether
# indexing succeeded, and reporting "moss" because a client exists is how you
# end up claiming a latency number that came from the fallback.
_last_backend: str = "none"


def _moss_client() -> Any:
    """Construct the client once. Returns None when Moss is not configured."""
    global _client, _moss_available

    if _moss_available is False:
        return None
    if _client is not None:
        return _client

    if not (MOSS_PROJECT_ID and MOSS_PROJECT_KEY):
        _moss_available = False
        logger.info("Moss not configured — using local retrieval index.")
        return None

    try:
        from moss import MossClient

        _client = MossClient(MOSS_PROJECT_ID, MOSS_PROJECT_KEY)
        _moss_available = True
        logger.info("Moss client ready.")
        return _client
    except Exception as exc:  # noqa: BLE001
        _moss_available = False
        logger.warning(f"Moss unavailable ({type(exc).__name__}: {exc}) — local index.")
        return None


def backend_name() -> str:
    """Which backend actually answered the last retrieve: 'moss' or 'local'.

    Reports what happened, not what was configured. Say this one out loud.
    """
    return _last_backend


def last_query_ms() -> float | None:
    """Latency of the most recent retrieval. Worth showing when it is genuinely low."""
    return _last_query_ms


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

    _FACTS[patient_id] = facts

    client = _moss_client()
    if client is not None and facts:
        try:
            session = await client.session(f"patient-{patient_id}")
            # Every SessionIndex method is a coroutine, despite type hints that
            # say otherwise. Calling them without await returns a coroutine
            # object, which quietly has no `.docs` — so retrieval fell through
            # to the local index while still reporting backend="moss". The only
            # visible symptom was a suspiciously fast query time.
            from moss import DocumentInfo

            # DocumentInfo objects, not dicts — the Rust core rejects a plain
            # dict with "argument 'docs': 'dict' object is not an instance of
            # 'DocumentInfo'". That exception was being swallowed by the
            # fallback below, so retrieval ran on the local index while still
            # reporting backend="moss".
            await session.add_docs(
                [
                    DocumentInfo(id=str(i), text=fact)
                    for i, fact in enumerate(facts)
                ]
            )
            _SESSIONS[patient_id] = session
            logger.info(f"Moss indexed {len(facts)} facts for patient {patient_id}")
            return
        except Exception as exc:  # noqa: BLE001 - never break a call over retrieval
            logger.warning(f"Moss indexing failed ({exc}) — local index for this call.")

    logger.info(f"Indexed {len(facts)} facts for patient {patient_id} (local)")


async def retrieve(patient_id: str, *, query: str, limit: int = 3) -> list[str]:
    """Top facts for this moment in the conversation. Must be fast."""
    global _last_query_ms, _last_backend

    session = _SESSIONS.get(patient_id)
    if session is not None:
        try:
            started = time.perf_counter()
            result = await session.query(query, _query_options(limit))
            elapsed = (time.perf_counter() - started) * 1000
            hits = [doc.text for doc in getattr(result, "docs", [])]
            if hits:
                _last_query_ms = elapsed
                _last_backend = "moss"
                logger.debug(f"Moss retrieval {elapsed:.2f}ms, {len(hits)} hits")
                return hits[:limit]
            logger.warning("Moss returned no hits — falling back to local.")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Moss query failed ({exc}) — falling back to local.")

    started = time.perf_counter()
    hits = _local_retrieve(patient_id, query, limit)
    _last_query_ms = (time.perf_counter() - started) * 1000
    _last_backend = "local"
    return hits


def _query_options(limit: int) -> Any:
    try:
        from moss import QueryOptions

        return QueryOptions(top_k=limit)
    except Exception:  # noqa: BLE001
        return None


def _local_retrieve(patient_id: str, query: str, limit: int) -> list[str]:
    """Keyword overlap. Crude, deterministic, and effectively instant."""
    facts = _FACTS.get(patient_id, [])
    if not facts:
        return []

    terms = {t for t in query.lower().split() if len(t) > 2}
    if not terms:
        return facts[:limit]

    scored = sorted(facts, key=lambda fact: -sum(1 for t in terms if t in fact.lower()))
    return scored[:limit]


def clear() -> None:
    """Reset between demo takes."""
    _FACTS.clear()
    _SESSIONS.clear()


def _clinical_status(condition: dict[str, Any]) -> str:
    coding = (condition.get("clinicalStatus") or {}).get("coding") or [{}]
    return coding[0].get("code", "unknown")


def _practitioner_display(encounter: dict[str, Any]) -> str:
    for participant in encounter.get("participant", []):
        display = (participant.get("individual") or {}).get("display")
        if display:
            return display
    return ""
