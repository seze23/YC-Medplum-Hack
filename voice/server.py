"""FastAPI server: Twilio voice webhook + media-stream WebSocket.

Run it:

    uvicorn voice.server:app --host 0.0.0.0 --port 8000

Then in another shell:

    ngrok http 8000

Put the ngrok https URL in PUBLIC_BASE_URL, and set the Twilio number's
"A call comes in" webhook to  <PUBLIC_BASE_URL>/twiml  (HTTP POST).

The ngrok URL changes every restart. That is why it lives in an env var and not
in this file.
"""

from __future__ import annotations

import json

from html import escape
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import HTMLResponse, JSONResponse, Response
from loguru import logger

import services.agentphone_router as agentphone_router
from services.demo_fixtures import demo_patient_lookup
from services.medplum import MedplumClient
from services.medplum_bindings import medplum_create_task
from shared.config import PUBLIC_BASE_URL
from voice.dashboard import snapshot
from voice.pipeline import run_call

# Text-channel side of the Patient Journey (AgentPhone SMS webhook). Voice
# above is the call path; this is inbound text -> Task, with the same
# Human-In-The-Loop principle -- see services/agentphone_router.py's module
# docstring for why outbound text itself isn't wired yet (A2P 10DLC).
#
# patient_lookup stays on the fixture binding rather than a real Medplum
# lookup -- see services/medplum_bindings.py's docstring for why. create_task
# is real: every inbound text becomes an actual Medplum Task, which is what
# makes it show up in /api/dashboard's open_tasks below.
agentphone_router.patient_lookup = demo_patient_lookup
agentphone_router.create_task = medplum_create_task

app = FastAPI(title="Relay")
app.include_router(agentphone_router.router)

DASHBOARD_HTML = Path(__file__).resolve().parent.parent / "dashboard" / "index.html"


@app.get("/health")
async def health() -> JSONResponse:
    """Smoke check before you point a real phone number at this."""
    return JSONResponse(
        {
            "ok": True,
            "public_base_url": PUBLIC_BASE_URL or "(not set)",
            "websocket_url": _ws_url() or "(set PUBLIC_BASE_URL first)",
        }
    )


@app.post("/twiml")
@app.get("/twiml")
async def twiml(request: Request) -> Response:
    """Twilio hits this when a call arrives. We hand back a media stream.

    <Connect><Stream> is bidirectional and blocks the call until the socket
    closes — which is what we want. <Start><Stream> would fork a copy of the
    audio and let Twilio carry on to the next verb; you'd hear silence.
    """
    # Twilio POSTs the caller's number here as `From`, but it does NOT forward it
    # to the media stream — <Stream> only carries parameters you declare. Without
    # passing it through explicitly, caller ID never reaches the pipeline and the
    # strongest patient-matching signal we have is silently unavailable.
    caller = ""
    try:
        form = await request.form()
        caller = str(form.get("From") or "")
    except Exception:  # noqa: BLE001 - GET probes have no form body
        caller = request.query_params.get("From", "")

    ws_url = _ws_url()
    if not ws_url:
        logger.error("PUBLIC_BASE_URL is not set — Twilio cannot reach the socket.")
        return Response(
            content=(
                '<?xml version="1.0" encoding="UTF-8"?>'
                "<Response><Say>Configuration error. Goodbye.</Say></Response>"
            ),
            media_type="application/xml",
        )

    logger.info(f"Incoming call from {caller or '(unknown)'} -> streaming to {ws_url}")
    param = (
        f'<Parameter name="From" value="{escape(caller, quote=True)}" />' if caller else ""
    )
    return Response(
        content=(
            '<?xml version="1.0" encoding="UTF-8"?>'
            "<Response>"
            f'<Connect><Stream url="{ws_url}">{param}</Stream></Connect>'
            "</Response>"
        ),
        media_type="application/xml",
    )


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    """Twilio media stream.

    Twilio sends two JSON frames before any audio: `connected`, then `start`.
    The `start` frame carries streamSid and callSid, and the serializer needs
    both. Read them before building the pipeline — not after.
    """
    await websocket.accept()

    try:
        # Frame 1: {"event": "connected", ...}
        await websocket.receive_text()
        # Frame 2: {"event": "start", "start": {"streamSid": ..., "callSid": ...}}
        start_raw = await websocket.receive_text()
    except Exception as exc:  # noqa: BLE001 - the call just went away
        logger.warning(f"Socket closed before the start frame arrived: {exc}")
        return

    start = json.loads(start_raw).get("start", {})
    stream_sid = start.get("streamSid")
    call_sid = start.get("callSid")
    caller = (start.get("customParameters") or {}).get("From", "")

    if not stream_sid:
        logger.error(f"No streamSid in start frame: {start_raw[:200]}")
        await websocket.close()
        return

    logger.info(f"Call {call_sid} connected (stream {stream_sid})")
    await run_call(
        websocket, stream_sid=stream_sid, call_sid=call_sid, caller_number=caller
    )


@app.get("/api/dashboard")
async def dashboard_data() -> JSONResponse:
    """Polled every 2s by the dashboard. Reads straight back out of Medplum."""
    medplum = MedplumClient()
    try:
        return JSONResponse(await snapshot(medplum))
    except Exception as exc:  # noqa: BLE001 - show the error on the board
        logger.error(f"Dashboard snapshot failed: {exc}")
        return JSONResponse({"error": str(exc)}, status_code=200)
    finally:
        await medplum.aclose()


@app.get("/dashboard")
async def dashboard_page() -> HTMLResponse:
    if not DASHBOARD_HTML.exists():
        return HTMLResponse("<h1>dashboard/index.html is missing</h1>", status_code=404)
    return HTMLResponse(DASHBOARD_HTML.read_text(encoding="utf-8"))


@app.get("/")
async def index() -> HTMLResponse:
    """Browser-mic fallback lives here if Twilio isn't cooperating by 12:30."""
    return HTMLResponse(
        "<h1>Relay</h1>"
        "<p>Autonomous patient operations for outpatient PT.</p>"
        f"<p>Point your Twilio number's voice webhook at "
        f"<code>{PUBLIC_BASE_URL or '(PUBLIC_BASE_URL unset)'}/twiml</code></p>"
    )


def _ws_url() -> str:
    if not PUBLIC_BASE_URL:
        return ""
    return PUBLIC_BASE_URL.replace("https://", "wss://").replace("http://", "ws://") + "/ws"
