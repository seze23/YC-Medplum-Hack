"""Pipecat pipeline for one inbound call.

--- The audio gotcha, corrected ------------------------------------------------

Twilio media streams are mu-law at 8000 Hz, not 16k PCM. Pipecat's
TwilioFrameSerializer already handles the mu-law <-> PCM conversion for you
(`ulaw_to_pcm` / `pcm_to_ulaw`) and will resample between Twilio's 8 kHz and
whatever rate the pipeline runs at.

So the thing that actually bites is not hand-configuring mu-law on Deepgram —
it's letting the sample rates disagree. We pin everything to 8000 Hz end to end:
the serializer, the transport, the STT, the TTS, and the VAD. No resampling
anywhere, lowest latency, nothing to get wrong. If you hear static or chipmunks,
one of those five numbers has drifted.

--- Architecture ---------------------------------------------------------------

The LLM speaks and fills a state object. It does not decide anything. Every
turn, the extracted state goes through the deterministic engine, and the engine's
answer is injected back as a DIRECTIVE the model must follow. An LLM that can be
talked out of the emergency branch is not a safety control.
"""

from __future__ import annotations

import asyncio
from typing import Any

import aiohttp
from loguru import logger
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import TTSSpeakFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.worker import PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
)
from pipecat.processors.audio.vad_processor import VADProcessor
from pipecat.serializers.twilio import TwilioFrameSerializer
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.anthropic.llm import AnthropicLLMService
from pipecat.services.deepgram.tts import DeepgramHttpTTSService
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)

from engine.decision import decide
from services.medplum import shared_client
from shared.config import (
    ANTHROPIC_API_KEY,
    DEEPGRAM_API_KEY,
    TWILIO_ACCOUNT_SID,
    TWILIO_AUTH_TOKEN,
    TWILIO_SAMPLE_RATE,
)
from shared.state import EXTRACTION_TOOL, CallState, apply_extraction, new_state
from voice.dashboard import publish, retire
from voice.orchestrator import CallOrchestrator
from voice.prompts import SYSTEM, context_block, directive

# The model's job here is narrow — fill a JSON schema from one turn of speech and
# emit a sentence or two. It decides nothing; the engine does. So this is a cheap
# knob to turn if latency needs work.
#
# Thinking stays ON. With thinking disabled this model can occasionally emit a
# tool call as plain text instead of a structured tool_use block, which for a
# function-calling extractor means the call silently never runs — no error, no
# warning, just an agent that quietly stops filling in the record. Exactly the
# bug you do not want to be chasing at 15:00.
LLM_MODEL = "claude-opus-5"

GREETING = (
    "Thanks for calling Bayview Physical Therapy, this is Relay. "
    "Can I start with your full name?"
)


async def run_call(
    websocket: Any,
    *,
    stream_sid: str,
    call_sid: str | None,
    caller_number: str = "",
) -> None:
    """Own one call from connect to hangup."""
    state = new_state()
    if caller_number:
        state.identity.phone = caller_number

    # Shared, already-authenticated client — a per-call handshake cost ~1.5s of
    # dead air before the agent could speak.
    medplum = shared_client()
    orchestrator = CallOrchestrator(medplum)

    # Resolve the caller from their number while the line is still connecting.
    # If we know them, the agent opens by name and their history is already
    # loaded — which is the whole difference between this and a phone tree.
    greeting = GREETING
    try:
        # Hard ceiling on how long the caller waits in silence. Recognising them
        # is worth a beat, but not an unbounded one — if Medplum is slow we open
        # with the generic greeting and ask for their name like any other call.
        if await asyncio.wait_for(
            orchestrator.preload_by_caller_id(state), timeout=4.0
        ):
            first_name = state.identity.name.split()[0]
            greeting = (
                f"Thanks for calling Bayview Physical Therapy — is that "
                f"{first_name}?"
            )
            decide(state)
    except asyncio.TimeoutError:
        logger.warning("Caller ID preload timed out — opening with generic greeting.")
    except Exception as exc:  # noqa: BLE001 - never block a call on a lookup
        logger.warning(f"Caller ID preload failed: {exc}")

    serializer = TwilioFrameSerializer(
        stream_sid=stream_sid,
        call_sid=call_sid,
        account_sid=TWILIO_ACCOUNT_SID or None,
        auth_token=TWILIO_AUTH_TOKEN or None,
        params=TwilioFrameSerializer.InputParams(
            twilio_sample_rate=TWILIO_SAMPLE_RATE,
            # Pin the pipeline to Twilio's rate so nothing resamples.
            sample_rate=TWILIO_SAMPLE_RATE,
            # Only hang up automatically if we actually have credentials.
            auto_hang_up=bool(call_sid and TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN),
        ),
    )

    transport = FastAPIWebsocketTransport(
        websocket=websocket,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            audio_in_sample_rate=TWILIO_SAMPLE_RATE,
            audio_out_sample_rate=TWILIO_SAMPLE_RATE,
            add_wav_header=False,  # Twilio wants raw payloads
            serializer=serializer,
        ),
    )

    stt = DeepgramSTTService(
        api_key=DEEPGRAM_API_KEY,
        # nova-3 is trained on wideband audio and mangles 8kHz phone lines —
        # "This is Maria Alvarez" came back as "Pieces Maria Alvarez". The
        # -phonecall models are trained on exactly this narrowband material.
        model="nova-2-phonecall",
        language="en-US",
        sample_rate=TWILIO_SAMPLE_RATE,
        encoding="linear16",
        interim_results=True,
        smart_format=True,
        # Dates of birth and member IDs are the two things we cannot afford to
        # get wrong, and both are spoken as digits.
        numerals=True,
        # Give the caller a beat before deciding they've finished a sentence.
        utterance_end_ms=1000,
    )

    # HTTP TTS, not the WebSocket variant. The streaming service kept truncating
    # utterances to ~300ms and logging "unable to append audio to context: no
    # context ID provided" — the greeting was generated, marked as spoken, and
    # never actually reached the caller. HTTP has no context handshake to get
    # wrong, and for one- or two-sentence replies the latency cost is small.
    http_session = aiohttp.ClientSession()
    tts = DeepgramHttpTTSService(
        api_key=DEEPGRAM_API_KEY,
        aiohttp_session=http_session,
        sample_rate=TWILIO_SAMPLE_RATE,
        encoding="linear16",
    )

    llm = AnthropicLLMService(api_key=ANTHROPIC_API_KEY, model=LLM_MODEL)

    tools = ToolsSchema(
        standard_tools=[
            FunctionSchema(
                name=EXTRACTION_TOOL["name"],
                description=EXTRACTION_TOOL["description"],
                properties=EXTRACTION_TOOL["input_schema"]["properties"],
                required=[],
            )
        ]
    )

    opening_context = context_block(state)
    context = LLMContext(
        messages=[
            {"role": "system", "content": SYSTEM},
            # Everything caller ID already told us — prior episodes, the
            # therapist they saw last — in front of the model before it speaks.
            *(
                [{"role": "system", "content": opening_context}]
                if opening_context
                else []
            ),
            {"role": "system", "content": directive(decide(state))},
            # GREETING is deliberately NOT seeded here. It is spoken via a
            # TTSSpeakFrame on connect, and the assistant aggregator writes it
            # into the context itself — seeding it too puts it in twice.
        ],
        tools=tools,
    )
    aggregators = LLMContextAggregatorPair(context)

    async def on_extraction(params: Any) -> None:
        """The one tool the model can call. It writes to state, nothing else."""
        payload = getattr(params, "arguments", None) or {}
        logger.debug(f"extraction: {payload}")

        apply_extraction(state, payload)

        # decide() runs red flags first, then the orchestrator performs the side
        # effect for that action (Medplum lookup, Stedi check, booking, Task)
        # and re-decides. Everything in there is idempotent, so a chatty
        # extractor repeating itself cannot double-book.
        await orchestrator.advance(state)

        # Feed the engine's decision back as the next instruction. This is the
        # loop that keeps the model on rails.
        instruction = directive(state)
        extra = context_block(state)
        if extra:
            instruction = f"{extra}\n\n{instruction}"

        context.add_message({"role": "system", "content": instruction})
        publish(call_sid or stream_sid, state)

        if state.emergency:
            logger.warning(f"EMERGENCY on call {call_sid}: {state.review_flags}")

        await params.result_callback({"acknowledged": True})

    llm.register_function(EXTRACTION_TOOL["name"], on_extraction)

    pipeline = Pipeline(
        [
            transport.input(),
            VADProcessor(
                vad_analyzer=SileroVADAnalyzer(
                    sample_rate=TWILIO_SAMPLE_RATE,
                    # Phone lines are noisy and the defaults treat every crackle
                    # as the caller talking, which fires an interruption and
                    # truncates the agent mid-sentence (its first reply came out
                    # as the single word "I want"). Higher thresholds and a
                    # longer stop window make it wait for an actual pause.
                    # Tuned against real calls, not defaults.
                    #
                    # stop_secs=0.2 (pipecat's default) chopped callers into
                    # sub-half-second fragments — VAD start/stop every ~350ms —
                    # and every fragment restarted the turn, so Deepgram never
                    # saw a complete utterance and returned no transcript at
                    # all. Transcription worked at 0.7-0.8. pipecat warns that a
                    # longer stop window degrades its turn-latency calibration;
                    # that is a real cost and worth paying, because the
                    # alternative is an agent that hears nothing.
                    #
                    # The higher confidence and min_volume keep line noise from
                    # registering as speech, which is what caused the churn.
                    params=VADParams(
                        confidence=0.8,
                        start_secs=0.25,
                        stop_secs=0.7,
                        min_volume=0.75,
                    ),
                )
            ),
            stt,
            aggregators.user(),
            llm,
            tts,
            transport.output(),
            aggregators.assistant(),
        ]
    )

    worker = PipelineWorker(pipeline, conversation_id=call_sid or stream_sid)
    runner = PipelineRunner(handle_sigint=False)

    # The agent has to speak first. Without this the pipeline sits waiting for
    # the caller to talk while the caller waits for the clinic to say hello, and
    # the call dies in mutual silence. Putting the greeting in the LLM context is
    # not enough — nothing pushes it into the pipeline until a turn completes.
    @transport.event_handler("on_client_connected")
    async def _greet(_transport, _client):  # noqa: ANN001
        logger.info(f"Client connected on {call_sid} — greeting")
        await worker.queue_frames([TTSSpeakFrame(greeting)])

    logger.info(f"Pipeline starting for call {call_sid}")
    try:
        await runner.run(worker)
    finally:
        retire(call_sid or stream_sid)
        # medplum is process-shared — do not close it here, the next call needs
        # its cached token.
        await http_session.close()
        logger.info(
            f"Call {call_sid} ended. next_action={state.next_action.value} "
            f"emergency={state.emergency} flags={state.review_flags} "
            f"appointment={orchestrator.booked_appointment_id}"
        )
