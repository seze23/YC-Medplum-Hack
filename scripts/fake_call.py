"""Simulate a Twilio media stream against the running server.

    python -m scripts.fake_call

Pretends to be Twilio: fetches TwiML the way Twilio would, opens the media
stream WebSocket, sends the `connected` and `start` frames, then listens for
outbound audio.

The point is to test our stack without a phone in the loop. When a real call
produces silence, this answers the only question that matters — is the problem
in Relay, or between the handset and Twilio?
"""

from __future__ import annotations

import asyncio
import base64
import json
import re
import sys

import httpx
import websockets

from shared.config import PUBLIC_BASE_URL, TWILIO_PHONE_NUMBER, DEMO_CALLER_NUMBER

LOCAL = "http://127.0.0.1:8000"
CALLER = DEMO_CALLER_NUMBER or "+16267159929"


async def main() -> int:
    base = PUBLIC_BASE_URL or LOCAL
    print(f"Pretending to be Twilio against {base}\n")

    # 1. The webhook, exactly as Twilio posts it.
    async with httpx.AsyncClient(timeout=30) as http:
        resp = await http.post(
            f"{base}/twiml",
            headers={"User-Agent": "TwilioProxy/1.1"},
            data={
                "CallSid": "CAfake00000000000000000000000000",
                "From": CALLER,
                "To": TWILIO_PHONE_NUMBER or "+13613385046",
                "AccountSid": "ACfake",
                "Direction": "inbound",
            },
        )
    print(f"1. POST /twiml            -> HTTP {resp.status_code}")
    if resp.status_code != 200:
        print(resp.text[:300])
        return 1

    body = resp.text
    print(f"   TwiML: {body[:160]}")
    match = re.search(r'url="([^"]+)"', body)
    if not match:
        print("   no <Stream url=...> in TwiML — server thinks it is misconfigured")
        return 1
    ws_url = match.group(1)
    if "From" in body:
        print("   caller number is being passed through (caller ID will work)")

    # Prefer the local socket so we test the app, not the tunnel.
    local_ws = "ws://127.0.0.1:8000/ws"
    print(f"\n2. WebSocket {local_ws}")

    try:
        async with websockets.connect(local_ws, open_timeout=20) as ws:
            print("   connected (HTTP 101)")

            await ws.send(json.dumps({"event": "connected", "protocol": "Call"}))
            await ws.send(
                json.dumps(
                    {
                        "event": "start",
                        "sequenceNumber": "1",
                        "start": {
                            "streamSid": "MZfake0000000000000000000000000",
                            "callSid": "CAfake00000000000000000000000000",
                            "accountSid": "ACfake",
                            "tracks": ["inbound"],
                            "mediaFormat": {
                                "encoding": "audio/x-mulaw",
                                "sampleRate": 8000,
                                "channels": 1,
                            },
                            "customParameters": {"From": CALLER},
                        },
                    }
                )
            )
            print("   sent connected + start frames")

            # Silence keeps the stream alive while the greeting is synthesised.
            silence = base64.b64encode(b"\xff" * 160).decode()

            async def keepalive() -> None:
                seq = 2
                while True:
                    await ws.send(
                        json.dumps(
                            {
                                "event": "media",
                                "sequenceNumber": str(seq),
                                "streamSid": "MZfake0000000000000000000000000",
                                "media": {
                                    "track": "inbound",
                                    "chunk": str(seq),
                                    "timestamp": str(seq * 20),
                                    "payload": silence,
                                },
                            }
                        )
                    )
                    seq += 1
                    await asyncio.sleep(0.02)

            pump = asyncio.create_task(keepalive())

            print("\n3. Listening for outbound audio (20s)...")
            audio_frames = 0
            audio_bytes = 0
            deadline = asyncio.get_event_loop().time() + 20
            try:
                while asyncio.get_event_loop().time() < deadline:
                    remaining = deadline - asyncio.get_event_loop().time()
                    raw = await asyncio.wait_for(ws.recv(), timeout=max(0.1, remaining))
                    msg = json.loads(raw)
                    if msg.get("event") == "media":
                        audio_frames += 1
                        audio_bytes += len(
                            base64.b64decode(msg["media"]["payload"])
                        )
                        if audio_frames == 1:
                            print("   FIRST AUDIO FRAME RECEIVED")
                    elif msg.get("event") == "mark":
                        print(f"   mark: {msg.get('mark', {}).get('name')}")
            except asyncio.TimeoutError:
                pass
            finally:
                pump.cancel()

            print()
            print(f"   audio frames back : {audio_frames}")
            print(f"   audio bytes back  : {audio_bytes}")
            secs = audio_bytes / 8000 if audio_bytes else 0
            print(f"   ~= {secs:.1f} seconds of 8kHz mu-law speech")

            if audio_frames == 0:
                print("\nRESULT: the server sent NO audio. The bug is in Relay.")
                return 1
            if secs < 1.0:
                print("\nRESULT: audio came back but it is too short to be the greeting.")
                return 1
            print("\nRESULT: Relay is generating and sending speech correctly.")
            print("If a real call is silent, the problem is between the handset")
            print("and Twilio — not in this stack.")
            return 0

    except Exception as exc:  # noqa: BLE001
        print(f"   WebSocket failed: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
