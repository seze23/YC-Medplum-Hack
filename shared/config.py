"""Environment loading. One place, so nothing reads os.environ directly."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "fixtures"

load_dotenv(ROOT / ".env")


def _get(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


def _require(key: str) -> str:
    value = _get(key)
    if not value:
        raise RuntimeError(
            f"{key} is not set. Copy .env.example to .env and fill it in."
        )
    return value


# The single most important flag in the repo. At 16:00 every team is hammering
# the same sandboxes; flipping this replays fixtures/ instead of calling out,
# and the demo is unaffected.
USE_FIXTURES = _get("USE_FIXTURES", "false").lower() in ("1", "true", "yes")

TWILIO_ACCOUNT_SID = _get("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = _get("TWILIO_AUTH_TOKEN")
TWILIO_PHONE_NUMBER = _get("TWILIO_PHONE_NUMBER")
DEEPGRAM_API_KEY = _get("DEEPGRAM_API_KEY")
PUBLIC_BASE_URL = _get("PUBLIC_BASE_URL").rstrip("/")

ANTHROPIC_API_KEY = _get("ANTHROPIC_API_KEY")
# Kept so the LLM provider can be swapped back without re-fetching a key.
OPENAI_API_KEY = _get("OPENAI_API_KEY")

MEDPLUM_BASE_URL = _get("MEDPLUM_BASE_URL", "https://api.medplum.com").rstrip("/")
MEDPLUM_CLIENT_ID = _get("MEDPLUM_CLIENT_ID")
MEDPLUM_CLIENT_SECRET = _get("MEDPLUM_CLIENT_SECRET")

STEDI_API_KEY = _get("STEDI_API_KEY")
# Defaulted for the same reason as DEMO_CALLER_NUMBER below — these went missing
# from .env repeatedly, and the only symptom was the eligibility check quietly
# querying an empty payer. 87726 is UnitedHealthcare, one of Stedi's four mock
# payers; UHC202649 is the subscriber their sandbox actually recognises.
STEDI_TEST_PAYER_ID = _get("STEDI_TEST_PAYER_ID") or "87726"
STEDI_TEST_MEMBER_ID = _get("STEDI_TEST_MEMBER_ID") or "UHC202649"

MOSS_API_KEY = _get("MOSS_API_KEY")

# The phone you demo from. Seeded onto the returning patient's record so caller
# ID resolves her instantly — which is what a real clinic system does, and it
# keeps the "last time you saw Dr. Chen" moment off the critical path of
# transcribing a date of birth over a phone line.
#
# Defaulted rather than left blank on purpose. This value went missing from .env
# three separate times during the build (an editor window holding a stale copy
# will do it), and each time the only symptom was the returning-patient demo
# quietly failing to recognise the caller. A hard default means the centrepiece
# still works even if .env gets clobbered again.
DEMO_CALLER_NUMBER = _get("DEMO_CALLER_NUMBER") or "+16267159929"

# Twilio media streams are mu-law at 8kHz. Not 16k PCM. Set explicitly on BOTH
# the STT input and the TTS output or you get silence, static, or chipmunks.
# This constant exists so the value is stated once and never guessed at.
TWILIO_SAMPLE_RATE = 8000
TWILIO_ENCODING = "mulaw"

require = _require
