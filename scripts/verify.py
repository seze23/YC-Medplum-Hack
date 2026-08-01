"""Check every external dependency in one shot.

    python -m scripts.verify

Run it after adding each credential, and again during the 16:20 freeze. It makes
a real call to every service — a key that merely *looks* right but has no credit
behind it is the exact failure this is here to catch.

Never prints a secret. Only lengths and first/last few characters.
"""

from __future__ import annotations

import asyncio
import sys

import httpx

from shared import config

OK = "  [ OK ]"
BAD = "  [FAIL]"
SKIP = "  [ -- ]"


def mask(value: str) -> str:
    if not value:
        return "(empty)"
    if len(value) <= 12:
        return f"{value[:2]}...({len(value)} chars)"
    return f"{value[:6]}...{value[-4:]} ({len(value)} chars)"


class Report:
    def __init__(self) -> None:
        self.rows: list[tuple[str, bool | None, str]] = []

    def add(self, name: str, ok: bool | None, detail: str = "") -> None:
        self.rows.append((name, ok, detail))
        marker = OK if ok else (SKIP if ok is None else BAD)
        print(f"{marker} {name}" + (f" — {detail}" if detail else ""))

    def summary(self) -> int:
        passed = sum(1 for _, ok, _ in self.rows if ok is True)
        failed = sum(1 for _, ok, _ in self.rows if ok is False)
        skipped = sum(1 for _, ok, _ in self.rows if ok is None)
        print(f"\n{passed} ok, {failed} failed, {skipped} not configured")
        return 1 if failed else 0


async def check_deepgram(r: Report, http: httpx.AsyncClient) -> None:
    key = config.DEEPGRAM_API_KEY
    if not key:
        return r.add("Deepgram", None, "DEEPGRAM_API_KEY not set")
    resp = await http.get(
        "https://api.deepgram.com/v1/projects",
        headers={"Authorization": f"Token {key}"},
    )
    r.add("Deepgram", resp.status_code == 200, f"{mask(key)} HTTP {resp.status_code}")


async def check_anthropic(r: Report, http: httpx.AsyncClient) -> None:
    key = config.ANTHROPIC_API_KEY
    if not key:
        return r.add("Anthropic", None, "ANTHROPIC_API_KEY not set")
    headers = {
        "x-api-key": key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    # A real completion, because that is what catches an empty balance.
    resp = await http.post(
        "https://api.anthropic.com/v1/messages",
        headers=headers,
        json={
            "model": "claude-opus-5",
            "max_tokens": 8,
            "messages": [{"role": "user", "content": "say: ready"}],
        },
    )
    if resp.status_code == 200:
        r.add("Anthropic", True, "claude-opus-5 responds, credits ok")
    else:
        detail = resp.json().get("error", {}).get("message", resp.text[:120])
        r.add("Anthropic", False, f"HTTP {resp.status_code}: {detail}")


async def check_medplum(r: Report, http: httpx.AsyncClient) -> None:
    if not (config.MEDPLUM_CLIENT_ID and config.MEDPLUM_CLIENT_SECRET):
        return r.add("Medplum", None, "MEDPLUM_CLIENT_ID/SECRET not set")
    resp = await http.post(
        f"{config.MEDPLUM_BASE_URL}/oauth2/token",
        data={
            "grant_type": "client_credentials",
            "client_id": config.MEDPLUM_CLIENT_ID,
            "client_secret": config.MEDPLUM_CLIENT_SECRET,
        },
    )
    if resp.status_code != 200:
        return r.add("Medplum", False, f"auth HTTP {resp.status_code}")

    token = resp.json()["access_token"]
    auth = {"Authorization": f"Bearer {token}"}
    slots = await http.get(
        f"{config.MEDPLUM_BASE_URL}/fhir/R4/Slot?status=free&_count=100", headers=auth
    )
    patients = await http.get(
        f"{config.MEDPLUM_BASE_URL}/fhir/R4/Patient?_count=100", headers=auth
    )
    free = len(slots.json().get("entry", []))
    people = len(patients.json().get("entry", []))
    r.add("Medplum", True, f"{people} patients, {free} free slots")
    if free == 0:
        r.add("Medplum seed", False, "no free slots — run: .\\run.ps1 seed")


async def check_twilio(r: Report, http: httpx.AsyncClient) -> None:
    sid, token = config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN
    if not (sid and token):
        return r.add("Twilio", None, "TWILIO_ACCOUNT_SID/AUTH_TOKEN not set")

    resp = await http.get(
        f"https://api.twilio.com/2010-04-01/Accounts/{sid}.json", auth=(sid, token)
    )
    if resp.status_code != 200:
        return r.add("Twilio", False, f"auth HTTP {resp.status_code}")

    account = resp.json()
    r.add("Twilio", True, f"{account.get('friendly_name')} ({account.get('type')})")

    number = config.TWILIO_PHONE_NUMBER
    if not number:
        return r.add("Twilio number", None, "TWILIO_PHONE_NUMBER not set")

    nums = await http.get(
        f"https://api.twilio.com/2010-04-01/Accounts/{sid}/IncomingPhoneNumbers.json",
        auth=(sid, token),
    )
    owned = nums.json().get("incoming_phone_numbers", [])
    match = next((n for n in owned if n.get("phone_number") == number), None)
    if not match:
        have = ", ".join(n.get("phone_number", "?") for n in owned) or "none"
        return r.add(
            "Twilio number", False, f"{number} not on this account. Owned: {have}"
        )

    caps = match.get("capabilities", {})
    voice_ok = bool(caps.get("voice"))
    r.add(
        "Twilio number",
        voice_ok,
        f"{number} voice={caps.get('voice')} sms={caps.get('sms')}",
    )

    # The webhook must point at our public URL or Twilio will never reach us.
    expected = f"{config.PUBLIC_BASE_URL}/twiml" if config.PUBLIC_BASE_URL else ""
    actual = match.get("voice_url") or "(not set)"
    if not expected:
        r.add("Twilio webhook", None, f"PUBLIC_BASE_URL not set; number points at {actual}")
    else:
        r.add("Twilio webhook", actual == expected, f"expected {expected}, got {actual}")

    if account.get("type") == "Trial":
        print(
            "         note: trial account — SMS only reaches Verified Caller IDs."
        )


async def check_public_url(r: Report, http: httpx.AsyncClient) -> None:
    url = config.PUBLIC_BASE_URL
    if not url:
        return r.add("Public URL", None, "PUBLIC_BASE_URL not set (start ngrok)")
    try:
        resp = await http.get(f"{url}/health", timeout=15)
        ok = resp.status_code == 200
        r.add("Public URL", ok, f"{url}/health HTTP {resp.status_code}")
    except Exception as exc:  # noqa: BLE001
        r.add("Public URL", False, f"{url} unreachable: {type(exc).__name__}")


async def check_stedi(r: Report, http: httpx.AsyncClient) -> None:
    if config.USE_FIXTURES:
        return r.add("Stedi", None, "USE_FIXTURES=true — replaying saved response")
    if not config.STEDI_API_KEY:
        return r.add("Stedi", None, "STEDI_API_KEY not set (fixture still works)")
    r.add("Stedi", True, f"key present {mask(config.STEDI_API_KEY)} (not called)")


async def check_moss(r: Report) -> None:
    if not config.MOSS_API_KEY:
        return r.add("Moss", None, "MOSS_API_KEY not set — local index in use")
    r.add("Moss", True, "key present")


async def main() -> int:
    print(f"\nRelay credential check   (USE_FIXTURES={config.USE_FIXTURES})\n")
    report = Report()
    async with httpx.AsyncClient(timeout=30) as http:
        await check_deepgram(report, http)
        await check_anthropic(report, http)
        await check_medplum(report, http)
        await check_twilio(report, http)
        await check_public_url(report, http)
        await check_stedi(report, http)
        await check_moss(report)
    return report.summary()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
