"""Medplum FHIR client. System of record AND scheduling source of truth.

Using Medplum's Schedule/Slot resources instead of Google Calendar saves an hour
of OAuth setup and keeps the FHIR-first claim honest — the appointment book and
the medical record are the same system, which is the actual argument.

Auth is client-credentials: a machine-to-machine ClientApplication, no user
login, no redirect. Token is cached until ~60s before expiry.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from shared.config import (
    MEDPLUM_BASE_URL,
    MEDPLUM_CLIENT_ID,
    MEDPLUM_CLIENT_SECRET,
)
from shared.state import CallState


class MedplumError(RuntimeError):
    pass


# Everything this client writes is tagged, so the reset script can clear a demo
# take completely. Without it, patients created during a test call linger and
# make the next lookup ambiguous — two Maria Alvarezes on file means neither is
# matched and the returning-patient moment silently stops working.
RELAY_TAG_SYSTEM = "https://relay.health/tags"
RELAY_CALL_TAG = "RELAY_CALL"


_shared: "MedplumClient | None" = None


def shared_client() -> "MedplumClient":
    """One client for the whole process, so the OAuth token is cached.

    Constructing a fresh client per call meant a full client-credentials
    handshake on every inbound call — around 1.5s, spent before the agent could
    say anything. Worse, it pushed the caller-ID lookup past its timeout, so the
    caller stopped being recognised and the greeting lost their name.
    """
    global _shared
    if _shared is None:
        _shared = MedplumClient()
    return _shared


async def warmup() -> None:
    """Fetch and cache an access token before any call arrives."""
    from loguru import logger

    try:
        client = shared_client()
        await client._access_token()
        logger.info("Medplum token cached.")
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Medplum warmup failed ({exc}) — first call may be slow.")


class MedplumClient:
    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (base_url or MEDPLUM_BASE_URL).rstrip("/")
        self.fhir = f"{self.base_url}/fhir/R4"
        self._token: str | None = None
        self._expires_at: float = 0.0
        self._http = httpx.AsyncClient(timeout=20.0)

    async def aclose(self) -> None:
        await self._http.aclose()

    # --- auth ---------------------------------------------------------------

    async def _access_token(self) -> str:
        if self._token and time.time() < self._expires_at:
            return self._token

        if not (MEDPLUM_CLIENT_ID and MEDPLUM_CLIENT_SECRET):
            raise MedplumError(
                "MEDPLUM_CLIENT_ID / MEDPLUM_CLIENT_SECRET not set. "
                "Create a ClientApplication in Medplum -> Project Admin -> Clients."
            )

        resp = await self._http.post(
            f"{self.base_url}/oauth2/token",
            data={
                "grant_type": "client_credentials",
                "client_id": MEDPLUM_CLIENT_ID,
                "client_secret": MEDPLUM_CLIENT_SECRET,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if resp.status_code != 200:
            raise MedplumError(f"Medplum auth failed {resp.status_code}: {resp.text}")

        payload = resp.json()
        self._token = payload["access_token"]
        self._expires_at = time.time() + payload.get("expires_in", 3600) - 60
        return self._token

    async def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {await self._access_token()}",
            "Content-Type": "application/fhir+json",
        }

    # --- generic FHIR verbs -------------------------------------------------

    async def create(self, resource_type: str, body: dict[str, Any]) -> dict[str, Any]:
        # Stamp the call tag unless the caller already set its own (the seed
        # script tags with RELAY_SEED and must keep that).
        if "meta" not in body:
            body = {
                **body,
                "meta": {
                    "tag": [{"system": RELAY_TAG_SYSTEM, "code": RELAY_CALL_TAG}]
                },
            }
        resp = await self._http.post(
            f"{self.fhir}/{resource_type}", json=body, headers=await self._headers()
        )
        if resp.status_code not in (200, 201):
            raise MedplumError(
                f"create {resource_type} failed {resp.status_code}: {resp.text}"
            )
        return resp.json()

    async def update(
        self, resource_type: str, resource_id: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        body = {**body, "resourceType": resource_type, "id": resource_id}
        resp = await self._http.put(
            f"{self.fhir}/{resource_type}/{resource_id}",
            json=body,
            headers=await self._headers(),
        )
        if resp.status_code not in (200, 201):
            raise MedplumError(
                f"update {resource_type}/{resource_id} failed "
                f"{resp.status_code}: {resp.text}"
            )
        return resp.json()

    async def search(
        self, resource_type: str, params: dict[str, Any]
    ) -> list[dict[str, Any]]:
        resp = await self._http.get(
            f"{self.fhir}/{resource_type}", params=params, headers=await self._headers()
        )
        if resp.status_code != 200:
            raise MedplumError(
                f"search {resource_type} failed {resp.status_code}: {resp.text}"
            )
        return [e["resource"] for e in resp.json().get("entry", [])]

    async def delete(self, resource_type: str, resource_id: str) -> None:
        await self._http.delete(
            f"{self.fhir}/{resource_type}/{resource_id}", headers=await self._headers()
        )

    # --- identity -----------------------------------------------------------

    async def find_patient(
        self, name: str, dob: str, phone: str = ""
    ) -> tuple[dict[str, Any] | None, str, float]:
        """Find a returning patient. Returns (patient, how_matched, confidence).

        Exact name+DOB is the textbook match, and it is far too brittle here: a
        date of birth spoken over an 8kHz phone line is the hardest thing in the
        call to transcribe, and one wrong digit turns a returning patient into a
        stranger — losing their history, their therapist, and the entire reason
        this product is interesting.

        So we match the way a front desk actually does, strongest signal first:

          1. Caller ID. Real clinic systems pop the record before anyone speaks.
          2. Name + date of birth. The textbook match.
          3. Name alone, but only when it is unambiguous — one candidate and no
             more. Returned with reduced confidence so the gate raises a review
             Task and the agent says out loud that a human will confirm.

        Tier 3 is deliberately conservative. Two Alvarezes on file means we fall
        through to creating a new record rather than guessing which is which.
        """
        # 1. Caller ID.
        if phone:
            matches = await self.search("Patient", {"telecom": phone})
            if len(matches) == 1:
                return matches[0], "caller_id", 0.9

        if not name:
            return None, "none", 0.0
        family = name.strip().split()[-1]

        # 2. Name + DOB.
        if dob:
            matches = await self.search(
                "Patient", {"family": family, "birthdate": dob}
            )
            if matches:
                return matches[0], "name_dob", 0.95

        # 3. Name alone, only if unambiguous.
        matches = await self.search("Patient", {"family": family})
        if len(matches) == 1:
            return matches[0], "name_only", 0.6

        return None, "none", 0.0

    async def create_patient(self, state: CallState) -> dict[str, Any]:
        parts = state.identity.name.strip().split()
        given, family = (parts[:-1] or [""]), (parts[-1] if parts else "")
        body: dict[str, Any] = {
            "resourceType": "Patient",
            "name": [{"given": given, "family": family}],
            "birthDate": state.identity.dob,
        }
        if state.identity.phone:
            body["telecom"] = [
                {"system": "phone", "value": state.identity.phone, "use": "mobile"}
            ]
        return await self.create("Patient", body)

    async def patient_history(self, patient_id: str) -> dict[str, Any]:
        """Prior episodes and the therapist who saw them. Feeds Moss."""
        conditions = await self.search(
            "Condition", {"subject": f"Patient/{patient_id}", "_sort": "-_lastUpdated"}
        )
        encounters = await self.search(
            "Encounter", {"subject": f"Patient/{patient_id}", "_sort": "-date"}
        )
        appointments = await self.search(
            "Appointment", {"patient": f"Patient/{patient_id}", "_sort": "-date"}
        )
        return {
            "conditions": conditions,
            "encounters": encounters,
            "appointments": appointments,
        }

    # --- scheduling ---------------------------------------------------------

    async def free_slots(self, *, horizon_days: int = 7) -> list[dict[str, Any]]:
        now = datetime.now(timezone.utc)
        return await self.search(
            "Slot",
            {
                "status": "free",
                "start": f"ge{now.isoformat()}",
                "_sort": "start",
                "_count": "50",
            },
        )

    async def mark_slot_busy(self, slot_id: str) -> dict[str, Any]:
        slots = await self.search("Slot", {"_id": slot_id})
        if not slots:
            raise MedplumError(f"Slot {slot_id} not found")
        slot = slots[0]
        slot["status"] = "busy"
        return await self.update("Slot", slot_id, slot)

    # --- write-back ---------------------------------------------------------
    #
    # This is the artifact that separates Relay from an AI receptionist. Eight
    # resources, written from one phone call. Never cut.

    async def write_coverage(self, patient_id: str, state: CallState) -> dict[str, Any]:
        return await self.create(
            "Coverage",
            {
                "resourceType": "Coverage",
                "status": "active" if state.insurance.covered else "cancelled",
                "beneficiary": {"reference": f"Patient/{patient_id}"},
                "subscriberId": state.insurance.member_id,
                "payor": [{"display": state.insurance.payer}],
                "costToBeneficiary": (
                    [
                        {
                            "type": {"text": "copay"},
                            "valueMoney": {
                                "value": state.insurance.copay,
                                "currency": "USD",
                            },
                        }
                    ]
                    if state.insurance.copay is not None
                    else []
                ),
            },
        )

    async def write_condition(
        self, patient_id: str, state: CallState
    ) -> dict[str, Any]:
        return await self.create(
            "Condition",
            {
                "resourceType": "Condition",
                "subject": {"reference": f"Patient/{patient_id}"},
                "clinicalStatus": {
                    "coding": [
                        {
                            "system": "http://terminology.hl7.org/CodeSystem/condition-clinical",
                            "code": "active",
                        }
                    ]
                },
                "code": {"text": state.symptoms.body_site},
                "note": [
                    {
                        "text": (
                            f"Onset {state.symptoms.onset}. "
                            f"Patient-reported severity {state.symptoms.severity}/10. "
                            f"Red flags: {', '.join(state.symptoms.red_flags) or 'none'}."
                        )
                    }
                ],
                "recordedDate": datetime.now(timezone.utc).isoformat(),
            },
        )

    async def write_appointment(
        self,
        patient_id: str,
        *,
        slot_id: str,
        practitioner_id: str,
        start: datetime,
        minutes: int = 45,
    ) -> dict[str, Any]:
        return await self.create(
            "Appointment",
            {
                "resourceType": "Appointment",
                "status": "booked",
                "slot": [{"reference": f"Slot/{slot_id}"}],
                "start": start.isoformat(),
                "end": (start + timedelta(minutes=minutes)).isoformat(),
                "participant": [
                    {
                        "actor": {"reference": f"Patient/{patient_id}"},
                        "status": "accepted",
                    },
                    {
                        "actor": {"reference": f"Practitioner/{practitioner_id}"},
                        "status": "accepted",
                    },
                ],
            },
        )

    async def write_encounter(
        self, patient_id: str, state: CallState
    ) -> dict[str, Any]:
        return await self.create(
            "Encounter",
            {
                "resourceType": "Encounter",
                "status": "finished",
                "class": {
                    "system": "http://terminology.hl7.org/CodeSystem/v3-ActCode",
                    "code": "VR",
                    "display": "virtual",
                },
                "subject": {"reference": f"Patient/{patient_id}"},
                "reasonCode": [{"text": "Inbound scheduling call handled by Relay"}],
                "period": {"end": datetime.now(timezone.utc).isoformat()},
            },
        )

    async def write_communication(
        self, patient_id: str, *, content: str, medium: str
    ) -> dict[str, Any]:
        return await self.create(
            "Communication",
            {
                "resourceType": "Communication",
                "status": "completed",
                "subject": {"reference": f"Patient/{patient_id}"},
                "medium": [{"text": medium}],
                "sent": datetime.now(timezone.utc).isoformat(),
                "payload": [{"contentString": content}],
            },
        )

    async def write_task(
        self,
        patient_id: str | None,
        *,
        description: str,
        priority: str = "routine",
    ) -> dict[str, Any]:
        """Human-in-the-loop, made concrete. `priority='stat'` for emergencies."""
        body: dict[str, Any] = {
            "resourceType": "Task",
            "status": "requested",
            "intent": "order",
            "priority": priority,
            "description": description,
            "authoredOn": datetime.now(timezone.utc).isoformat(),
        }
        if patient_id:
            body["for"] = {"reference": f"Patient/{patient_id}"}
        return await self.create("Task", body)
