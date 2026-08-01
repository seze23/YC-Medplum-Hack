"""Stedi real-time eligibility (X12 270/271).

The point of this module in the demo is one sentence spoken out loud:

    "You're covered — your copay is forty dollars, you have three hundred and
     ten left on your deductible, and your referral is valid through October."

Almost nobody else in the room will have an agent say a real dollar figure.

--- Fixture discipline -------------------------------------------------------

`fixtures/stedi_eligibility.json` is the parser's contract. The workflow is:

  1. Get ONE successful live call.
  2. Immediately run this module with --capture to write the raw response
     to the fixture.
  3. Write/adjust the parser against the fixture, not against the live API.

Then `USE_FIXTURES=true` replays it. If Stedi's sandbox falls over at 16:00 —
and it might, every team is hitting it — you flip one env var and the demo is
unaffected. The parser below is written against the documented 271 shape and is
deliberately defensive; replace the fixture with a real capture and re-check the
three numbers before demoing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from loguru import logger

from shared.config import (
    FIXTURES,
    STEDI_API_KEY,
    USE_FIXTURES,
)

ELIGIBILITY_URL = (
    "https://healthcare.us.stedi.com/2024-04-01/change/medicalnetwork/eligibility/v3"
)

FIXTURE_PATH: Path = FIXTURES / "stedi_eligibility.json"

# X12 EB01 eligibility/benefit codes we care about.
ACTIVE_COVERAGE = "1"
CO_PAYMENT = "B"
DEDUCTIBLE = "C"
# EB06 time qualifier: 29 = Remaining.
REMAINING = "29"
# EB03 service type. PT is physical therapy specifically; 30 is "Health Benefit
# Plan Coverage", the plan-wide line. Real 271s frequently answer a PT query
# with plan-level 30 lines and no PT-specific ones — treating only PT as
# relevant silently discarded every benefit figure in a live response.
PHYSICAL_THERAPY = "PT"
PLAN_WIDE = "30"
RELEVANT_SERVICE_TYPES = {PHYSICAL_THERAPY, PLAN_WIDE}


@dataclass
class Eligibility:
    covered: bool = False
    copay: float | None = None
    deductible_remaining: float | None = None
    referral_required: bool | None = None
    referral_valid_through: str | None = None
    plan_name: str = ""
    raw: dict[str, Any] | None = None

    def spoken(self) -> str:
        """What the agent actually says. Numbers, not adjectives."""
        if not self.covered:
            return (
                "I'm not able to confirm active coverage for that member ID. "
                "I'll flag it for our billing team and they'll call you — we can "
                "still get you booked."
            )
        parts = ["Good news — you're covered."]
        if self.copay is not None:
            parts.append(f"Your copay is ${self.copay:.0f} per visit.")
        if self.deductible_remaining is not None:
            if self.deductible_remaining <= 0:
                # "$0 remaining" is technically right and sounds like an error.
                parts.append("Your deductible is already met for the year.")
            else:
                parts.append(
                    f"You have ${self.deductible_remaining:.0f} remaining on your "
                    "deductible."
                )
        if self.referral_required is False:
            parts.append("No referral needed.")
        elif self.referral_required and self.referral_valid_through:
            parts.append(f"Your referral is valid through {self.referral_valid_through}.")
        return " ".join(parts)


async def check_eligibility(
    *,
    payer_id: str,
    member_id: str,
    first_name: str,
    last_name: str,
    dob: str,
    external_patient_id: str = "",
) -> Eligibility:
    """Live call, or fixture replay when USE_FIXTURES is set or no key exists."""
    if USE_FIXTURES:
        return parse(_load_fixture())

    if not STEDI_API_KEY:
        # Fall back rather than raise. A missing key used to throw here, the
        # orchestrator swallowed it, and the call stalled forever on
        # verify_insurance without ever reaching a booking. A demo that quietly
        # never books is worse than one that says where its numbers came from.
        logger.warning(
            "STEDI_API_KEY not set — replaying fixtures/stedi_eligibility.json. "
            "The copay and deductible spoken on this call are FIXTURE values."
        )
        return parse(_load_fixture())

    body: dict[str, Any] = {
        # No controlNumber — it is deprecated and Stedi generates its own.
        "tradingPartnerServiceId": payer_id,
        "provider": {
            "organizationName": "Relay Physical Therapy",
            "npi": "1999999984",
        },
        # The member ID is the authoritative key, so when we have one it is sent
        # alone. Adding a name and date of birth alongside it makes the payer
        # match on all three, and any mismatch — a middle name it stores that we
        # don't, a transcription slip on a digit — comes back as AAA 71/73 and
        # reads to the caller as "not covered" rather than as a bad request.
        # Names and DOB are the fallback for when no member ID was captured.
        "subscriber": (
            {"memberId": member_id}
            if member_id
            else {
                k: v
                for k, v in {
                    "firstName": first_name,
                    "lastName": last_name,
                    "dateOfBirth": dob.replace("-", "") if dob else "",
                }.items()
                if v
            }
        ),
        "encounter": {"serviceTypeCodes": [PHYSICAL_THERAPY]},
    }

    # Stedi has two test paths. A Test-type API key with a real mock payer ID
    # (Aetna / Cigna / UnitedHealthcare / CMS — e.g. 87726) is the usual one.
    # Sandbox accounts instead use the literal payer "STEDI" plus a stediTest
    # flag. Supporting both means whichever the console hands you just works.
    if payer_id.upper() == "STEDI":
        body["stediTest"] = True

    # Stedi recommends this so a check can be correlated back to a patient
    # later. Sending the Medplum Patient id keeps the eligibility trail joined
    # up with the record we write.
    if external_patient_id:
        body["externalPatientId"] = external_patient_id[:36]

    async with httpx.AsyncClient(timeout=25.0) as http:
        resp = await http.post(
            ELIGIBILITY_URL,
            json=body,
            headers={
                "Authorization": STEDI_API_KEY,
                "Content-Type": "application/json",
            },
        )

    if resp.status_code != 200:
        raise RuntimeError(f"Stedi eligibility failed {resp.status_code}: {resp.text}")

    payload = resp.json()
    _capture(payload)  # first success writes the fixture, always
    return parse(payload)


def parse(payload: dict[str, Any]) -> Eligibility:
    """Pure. Unit-testable against the fixture with no network."""
    result = Eligibility(raw=payload)

    benefits = payload.get("benefitsInformation") or []
    result.plan_name = (payload.get("planInformation") or {}).get("planNumber", "")

    for entry in benefits:
        code = entry.get("code")
        service_types = entry.get("serviceTypeCodes") or []
        amount = _money(entry.get("benefitAmount"))

        # Trust a figure that is PT-specific, plan-wide, or unscoped. A copay
        # for, say, chiropractic is not the number to say out loud.
        relevant = not service_types or bool(
            RELEVANT_SERVICE_TYPES.intersection(service_types)
        )
        # Payers return in- and out-of-network figures side by side. The patient
        # is coming to us, so the in-network number is the one that is true for
        # them — quoting the out-of-network deductible would be alarming and wrong.
        in_network = entry.get("inPlanNetworkIndicator") in (None, "Yes", "Not Applicable")

        if code == ACTIVE_COVERAGE:
            result.covered = True
        elif code == CO_PAYMENT and relevant and in_network and amount is not None:
            # Prefer a PT-specific figure over a later plan-wide one.
            if result.copay is None or PHYSICAL_THERAPY in service_types:
                result.copay = amount
        elif code == DEDUCTIBLE and relevant and in_network and amount is not None:
            if entry.get("timeQualifierCode") == REMAINING:
                # First in-network "remaining" line wins; later ones are
                # usually the same figure restated per tier.
                if result.deductible_remaining is None:
                    result.deductible_remaining = amount

        if "authOrCertIndicator" in entry:
            # Y here means prior auth / referral is required.
            result.referral_required = str(entry["authOrCertIndicator"]).upper() == "Y"

        dates = entry.get("benefitsDateInformation") or {}
        if dates.get("expiration"):
            result.referral_valid_through = dates["expiration"]

    return result


def _money(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _load_fixture() -> dict[str, Any]:
    if not FIXTURE_PATH.exists():
        raise RuntimeError(
            f"USE_FIXTURES is on but {FIXTURE_PATH} does not exist. "
            "Run one live call first, or check the file into fixtures/."
        )
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _capture(payload: dict[str, Any]) -> None:
    """Save a *useful* response as the replay fixture.

    Guarded deliberately. An eligibility error — wrong date of birth, unknown
    member — still comes back as HTTP 200 with an `errors` array and no
    benefits. Saving that overwrites a known-good fixture with one that makes
    the agent announce "I can't confirm coverage" for the rest of the day, and
    you find out on stage. Only overwrite when there is something to replay.
    """
    if not payload.get("benefitsInformation"):
        logger.warning(
            "Stedi returned no benefitsInformation "
            f"(errors: {payload.get('errors')}) — keeping the existing fixture."
        )
        return
    FIXTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    logger.info(f"Captured live Stedi response to {FIXTURE_PATH}")
