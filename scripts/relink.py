"""Re-point everything at the current ngrok tunnel.

    python -m scripts.relink

ngrok's free tier hands out a new hostname every time the agent restarts, and
when that happens three things go stale at once: PUBLIC_BASE_URL in .env, the
TwiML the server hands Twilio, and the voice webhook on the phone number. Fixing
those by hand takes a few minutes and is very easy to get half-right — which,
mid-demo, looks identical to the product being broken.

This does all three:

  1. Read the live tunnel URL from ngrok's local API
  2. Rewrite PUBLIC_BASE_URL in .env
  3. Update the Twilio number's voice webhook

Then restart the server so it picks up the new .env.
"""

from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

import httpx

from shared.config import (
    ROOT,
    TWILIO_ACCOUNT_SID,
    TWILIO_AUTH_TOKEN,
    TWILIO_PHONE_NUMBER,
)

NGROK_API = "http://127.0.0.1:4040/api/tunnels"
ENV_PATH: Path = ROOT / ".env"


async def tunnel_url(http: httpx.AsyncClient) -> str | None:
    try:
        resp = await http.get(NGROK_API, timeout=10)
    except Exception:
        return None
    tunnels = resp.json().get("tunnels", [])
    https = [t for t in tunnels if t.get("proto") == "https"]
    chosen = (https or tunnels or [None])[0]
    return chosen.get("public_url") if chosen else None


def rewrite_env(url: str) -> bool:
    if not ENV_PATH.exists():
        print(f"  .env not found at {ENV_PATH}")
        return False
    text = ENV_PATH.read_text(encoding="utf-8")
    new, count = re.subn(
        r"(?m)^PUBLIC_BASE_URL=.*$", f"PUBLIC_BASE_URL={url}", text
    )
    if count == 0:
        new = text.rstrip("\n") + f"\nPUBLIC_BASE_URL={url}\n"
    ENV_PATH.write_text(new, encoding="utf-8")
    return True


async def update_twilio(http: httpx.AsyncClient, url: str) -> bool:
    if not (TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_PHONE_NUMBER):
        print("  Twilio credentials incomplete — skipping webhook update")
        return False

    auth = (TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    root = f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}"

    resp = await http.get(f"{root}/IncomingPhoneNumbers.json", auth=auth, timeout=30)
    numbers = resp.json().get("incoming_phone_numbers", [])
    target = next(
        (n for n in numbers if n.get("phone_number") == TWILIO_PHONE_NUMBER), None
    )
    if not target:
        print(f"  {TWILIO_PHONE_NUMBER} not found on this Twilio account")
        return False

    upd = await http.post(
        f"{root}/IncomingPhoneNumbers/{target['sid']}.json",
        auth=auth,
        timeout=30,
        data={"VoiceUrl": f"{url}/twiml", "VoiceMethod": "POST"},
    )
    if upd.status_code != 200:
        print(f"  webhook update failed HTTP {upd.status_code}: {upd.text[:200]}")
        return False
    print(f"  webhook -> {upd.json().get('voice_url')}")
    return True


async def main() -> int:
    async with httpx.AsyncClient() as http:
        url = await tunnel_url(http)
        if not url:
            print("No ngrok tunnel found on 127.0.0.1:4040.")
            print("Start one first:  .\\run.ps1 tunnel")
            return 1

        print(f"Tunnel: {url}")
        rewrite_env(url)
        print(f"  .env PUBLIC_BASE_URL updated")
        await update_twilio(http, url)

        # Prove it end to end rather than trusting the writes.
        try:
            resp = await http.get(
                f"{url}/health",
                timeout=20,
                headers={"User-Agent": "TwilioProxy/1.1"},
            )
            reachable = resp.status_code == 200
        except Exception as exc:  # noqa: BLE001
            reachable = False
            print(f"  health check error: {type(exc).__name__}")

        if reachable:
            print("\nTunnel reaches the server. RESTART THE SERVER to load the new .env.")
        else:
            print(
                "\nTunnel is up but the server did not answer — start it with "
                ".\\run.ps1 serve"
            )
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
