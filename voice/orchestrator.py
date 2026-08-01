"""The merge layer: turns `next_action` into real side effects.

The decision engine says *what* should happen. This says *how*, by calling
Medplum, Stedi, Moss and Twilio. It is the only place in the codebase that both
reads state and writes to the outside world.

Everything here is idempotent. A voice call produces a lot of partial turns, and
the extractor will happily re-report the same member ID three times — running
the eligibility check three times, or booking three appointments, is exactly the
failure a live demo surfaces. Each step guards on the state it would produce.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from loguru import logger

from engine.decision import choose_slot, decide
from engine.scoring import Slot
from services import moss
from services.medplum import MedplumClient
from services.confirmation import confirmation_text
from services.stedi import check_eligibility
from shared.config import STEDI_TEST_PAYER_ID
from shared.state import CallState, NextAction


class CallOrchestrator:
    """One per call. Holds the side-effect state the engine deliberately lacks."""

    def __init__(self, medplum: MedplumClient) -> None:
        self.medplum = medplum
        self.patient_id: str | None = None
        self.booked_appointment_id: str | None = None
        self.escalation_task_id: str | None = None
        self._eligibility_checked = False
        self._history_loaded = False
        self._slot_cache: dict[str, dict[str, Any]] = {}
        self._practitioner_names: dict[str, str] = {}

    async def advance(self, state: CallState) -> CallState:
        """Run the side effect for the current action, then re-decide.

        Called after every extraction. Safe to call repeatedly.
        """
        decide(state)

        try:
            if state.next_action is NextAction.EMERGENCY_STOP:
                await self._handle_emergency(state)
            elif state.next_action is NextAction.ESCALATE:
                await self._handle_escalation(state)
            elif state.next_action is NextAction.COLLECT_SYMPTOMS:
                # Identity is settled by the time we get here — load history so
                # Moss can surface the prior episode before the agent speaks.
                await self._resolve_patient(state)
            elif state.next_action is NextAction.VERIFY_INSURANCE:
                await self._verify_insurance(state)
            elif state.next_action is NextAction.OFFER_SLOTS:
                await self._offer_slot(state)
            elif state.next_action is NextAction.BOOK:
                await self._book(state)
        except Exception as exc:  # noqa: BLE001
            # A failed side effect must not kill the call. Flag it and let the
            # conversation continue; a human picks it up from the Task list.
            logger.exception(f"Orchestrator step failed: {exc}")
            state.review_flags.append(f"SYSTEM: {state.next_action.value} failed: {exc}")

        return decide(state)

    async def preload_by_caller_id(self, state: CallState) -> bool:
        """Resolve the caller from their number before a word is spoken.

        Run this at connect, not mid-conversation. Waiting until identity is
        "settled" is circular — settling identity needs a name and a date of
        birth, and the whole point of caller ID is that we already know who this
        is. A real clinic system pops the record while the phone is still
        ringing; this is that.

        Returns True when the caller was recognised.
        """
        if not state.identity.phone:
            return False

        patient, how, confidence = await self.medplum.find_patient(
            "", "", state.identity.phone
        )
        if not patient or how != "caller_id":
            return False

        self.patient_id = patient["id"]
        state.identity.patient_id = patient["id"]
        state.identity.is_returning = True
        state.identity.name = _patient_name(patient)
        state.identity.dob = patient.get("birthDate", "")
        # Caller ID is strong but not proof of who is holding the phone, so the
        # agent still confirms verbally. 0.9 sits above the review threshold.
        state.identity.confidence = confidence

        history = await self.medplum.patient_history(patient["id"])
        await moss.index_patient(patient["id"], history)
        state.retrieved_context = await moss.retrieve(
            patient["id"], query="prior care"
        )
        prior = _last_practitioner(history)
        if prior:
            state.scheduling.provider_pref = prior

        self._history_loaded = True
        logger.info(
            f"Caller ID resolved {state.identity.name} ({patient['id']}), "
            f"prior provider {prior}"
        )
        return True

    # --- identity + history -------------------------------------------------

    async def _resolve_patient(self, state: CallState) -> None:
        if self._history_loaded:
            return

        existing, how, match_confidence = await self.medplum.find_patient(
            state.identity.name, state.identity.dob, state.identity.phone
        )
        if existing:
            # A shaky match must not silently inherit someone else's history.
            # Cap identity confidence at how sure the *match* was, so the gate
            # raises a review Task and the agent says so on the call.
            state.identity.confidence = min(state.identity.confidence, match_confidence)
            if how == "name_only":
                # Not prefixed "REVIEW " on purpose — decide() regenerates those
                # from confidence each turn and would drop this one.
                state.review_flags.append(
                    "IDENTITY MATCH: matched on name alone, date of birth did "
                    "not match — confirm before the appointment"
                )
            logger.info(f"Matched patient {existing['id']} via {how} ({match_confidence})")
            self.patient_id = existing["id"]
            state.identity.patient_id = existing["id"]
            state.identity.is_returning = True

            history = await self.medplum.patient_history(existing["id"])
            await moss.index_patient(existing["id"], history)
            state.retrieved_context = await moss.retrieve(
                existing["id"], query=state.symptoms.body_site or "prior care"
            )

            # Continuity of care: default to whoever they saw last, unless they
            # ask for someone else. This is what makes the agent sound like an
            # employee rather than a booking form.
            prior = _last_practitioner(history)
            if prior and state.scheduling.provider_pref is None:
                state.scheduling.provider_pref = prior
            logger.info(f"Returning patient {existing['id']}, prior provider {prior}")
        else:
            created = await self.medplum.create_patient(state)
            self.patient_id = created["id"]
            state.identity.patient_id = created["id"]
            state.identity.is_returning = False
            logger.info(f"New patient {created['id']}")

        self._history_loaded = True

    # --- insurance ----------------------------------------------------------

    async def _verify_insurance(self, state: CallState) -> None:
        if self._eligibility_checked:
            return
        if not (state.insurance.payer and state.insurance.member_id):
            return  # still collecting; the directive asks for the details

        self._eligibility_checked = True
        parts = state.identity.name.strip().split()
        result = await check_eligibility(
            payer_id=STEDI_TEST_PAYER_ID or state.insurance.payer,
            member_id=state.insurance.member_id,
            first_name=parts[0] if parts else "",
            last_name=parts[-1] if len(parts) > 1 else "",
            dob=state.identity.dob,
            external_patient_id=self.patient_id or "",
        )

        state.insurance.covered = result.covered
        state.insurance.copay = result.copay
        state.insurance.deductible_remaining = result.deductible_remaining
        state.insurance.referral_required = result.referral_required
        state.insurance.referral_valid_through = result.referral_valid_through
        logger.info(f"Eligibility: {result.spoken()}")

        if not result.covered:
            # The agent tells the caller "I'll flag it for our billing team".
            # Without this the system quietly did not, and the patient arrives
            # to an unverified claim. An agent that promises something the
            # system does not do is worse than one that says nothing.
            state.review_flags.append(
                f"BILLING: coverage not confirmed for member "
                f"{state.insurance.member_id or '(none given)'} "
                f"({state.insurance.payer or 'unknown payer'}) — verify before the visit"
            )

    # --- scheduling ---------------------------------------------------------

    async def _offer_slot(self, state: CallState) -> None:
        if state.scheduling.selected_slot_id:
            return

        fhir_slots = await self.medplum.free_slots()
        if not fhir_slots:
            state.review_flags.append("SYSTEM: no free slots available")
            return

        await self._load_practitioner_names()
        engine_slots: list[Slot] = []
        for raw in fhir_slots:
            self._slot_cache[raw["id"]] = raw
            practitioner_id = await self._practitioner_for_slot(raw)
            engine_slots.append(
                Slot(
                    id=raw["id"],
                    start=_parse_dt(raw["start"]),
                    practitioner_id=practitioner_id,
                    practitioner_name=self._practitioner_names.get(
                        practitioner_id, "your therapist"
                    ),
                )
            )

        best = choose_slot(state, engine_slots)
        if best is None:
            state.review_flags.append("SYSTEM: no bookable slot after scoring")
            return

        state.scheduling.selected_slot_id = best.id
        state.retrieved_context.append(
            f"Offering: {best.start.strftime('%A %d %B at %I:%M %p').lstrip('0')} "
            f"with {best.practitioner_name}."
        )
        logger.info(f"Offering slot {best.id} with {best.practitioner_name}")

    # --- booking + write-back ----------------------------------------------

    async def _book(self, state: CallState) -> None:
        if self.booked_appointment_id or not self.patient_id:
            return

        slot_id = state.scheduling.selected_slot_id
        raw = self._slot_cache.get(slot_id)
        if not raw:
            state.review_flags.append(f"SYSTEM: slot {slot_id} not in cache")
            return

        start = _parse_dt(raw["start"])
        practitioner_id = await self._practitioner_for_slot(raw)

        # The artifact. Eight resources from one phone call — this is the part
        # that separates Relay from an AI receptionist, and it never gets cut.
        await self.medplum.write_condition(self.patient_id, state)
        await self.medplum.write_coverage(self.patient_id, state)
        appointment = await self.medplum.write_appointment(
            self.patient_id,
            slot_id=slot_id,
            practitioner_id=practitioner_id,
            start=start,
        )
        await self.medplum.mark_slot_busy(slot_id)
        await self.medplum.write_encounter(self.patient_id, state)

        self.booked_appointment_id = appointment["id"]

        # Recorded as a verbal confirmation, because that is what it is. SMS was
        # cut: US carriers reject unregistered A2P 10DLC traffic (error 30034)
        # and registration takes days. The Communication resource still captures
        # exactly what the patient was told, which is the part that belongs in
        # the record.
        body = confirmation_text(
            patient_name=state.identity.name,
            start=start,
            practitioner_name=self._practitioner_names.get(
                practitioner_id, "your therapist"
            ),
            copay=state.insurance.copay,
        )
        await self.medplum.write_communication(
            self.patient_id, content=body, medium="Verbal (telephone)"
        )

        # Confidence gate, made concrete: one Task per untrusted domain.
        for flag in state.review_flags:
            await self.medplum.write_task(self.patient_id, description=flag)

        logger.info(f"Booked appointment {appointment['id']} for {self.patient_id}")

    # --- safety paths -------------------------------------------------------

    async def _handle_emergency(self, state: CallState) -> None:
        if self.escalation_task_id:
            return
        task = await self.medplum.write_task(
            self.patient_id,
            description=(
                "EMERGENCY — possible cauda equina. Caller advised to seek "
                "emergency care. No appointment booked. "
                + "; ".join(state.review_flags)
            ),
            priority="stat",
        )
        self.escalation_task_id = task["id"]
        logger.warning(f"Urgent Task {task['id']} written")

    async def _handle_escalation(self, state: CallState) -> None:
        if self.escalation_task_id:
            return
        task = await self.medplum.write_task(
            self.patient_id,
            description="Clinical review before booking. " + "; ".join(state.review_flags),
            priority="urgent",
        )
        self.escalation_task_id = task["id"]

    # --- helpers ------------------------------------------------------------

    async def _load_practitioner_names(self) -> None:
        if self._practitioner_names:
            return
        for p in await self.medplum.search("Practitioner", {"_count": "50"}):
            self._practitioner_names[p["id"]] = _display_name(p)

    async def _practitioner_for_slot(self, raw_slot: dict[str, Any]) -> str:
        """Slot -> Schedule -> Practitioner. Cached; the roster is tiny."""
        ref = (raw_slot.get("schedule") or {}).get("reference", "")
        schedule_id = ref.split("/")[-1] if ref else ""
        if not schedule_id:
            return ""
        schedules = await self.medplum.search("Schedule", {"_id": schedule_id})
        if not schedules:
            return ""
        for actor in schedules[0].get("actor", []):
            actor_ref = actor.get("reference", "")
            if actor_ref.startswith("Practitioner/"):
                return actor_ref.split("/")[-1]
        return ""


def _patient_name(patient: dict[str, Any]) -> str:
    names = patient.get("name") or [{}]
    n = names[0]
    given = " ".join(n.get("given") or [])
    return f"{given} {n.get('family', '')}".strip()


def _display_name(practitioner: dict[str, Any]) -> str:
    names = practitioner.get("name") or [{}]
    n = names[0]
    prefix = " ".join(n.get("prefix") or [])
    given = " ".join(n.get("given") or [])
    family = n.get("family", "")
    return " ".join(p for p in (prefix, given, family) if p).strip() or "your therapist"


def _last_practitioner(history: dict[str, Any]) -> str | None:
    for encounter in history.get("encounters", []):
        for participant in encounter.get("participant", []):
            ref = (participant.get("individual") or {}).get("reference", "")
            if ref.startswith("Practitioner/"):
                return ref.split("/")[-1]
    return None


def _parse_dt(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
