# Relay

**Autonomous patient operations for outpatient physical therapy clinics.**

A PT clinic's front desk answers the phone, books the appointment, and produces a
sticky note. Relay answers the phone, verifies insurance, books the appointment,
and produces a complete structured medical record — so the next call is smarter
than the last one.

Built for the Medplum × YC Agentic Healthcare Hackathon.

---

## The problem

Outpatient PT clinics lose money in three places, all of them on the phone:

- **Calls go unanswered.** The front desk is with a patient; the phone rings out;
  that patient books somewhere else.
- **Eligibility gets skipped.** Checking coverage means 10–15 minutes on hold
  with a payer. Skip it and you get denied claims and surprise bills.
- **Context evaporates.** A returning patient's prior episode, their therapist,
  their cancellation history — none of it reaches the person answering.

The third one compounds. Every call starts from zero because the last call left
nothing behind but a note.

## What Relay does

A patient calls a real phone number. During the ring, caller ID resolves them
against the clinic's records. By the time the agent speaks, it already knows who
they are and what they were treated for last time.

Over the next few minutes it captures symptoms, screens for red flags, verifies
insurance against the payer, scores the available appointment slots, books one,
and writes **eight FHIR resources** into Medplum.

That record is the point. It is what makes the next call better.

---

## Architecture

The single most important design decision:

> **The LLM never writes to the record.** It fills in a structured object, and a
> deterministic engine decides what happens. That is how you get an autonomous
> agent a clinic will actually turn on.

```
   Twilio ──> FastAPI /twiml ──> WebSocket media stream (mu-law 8kHz)
                                        │
                                        v
                                    Pipecat
                                        │
                    Deepgram STT ──> Claude ──> Deepgram TTS
                                        │
                                        │  function call: update_call_state
                                        v
                                  CallState  (shared/state.py)
                                        │
                                        v
                        ┌───── decision engine (pure Python) ─────┐
                        │  1. red flags        — every turn, first │
                        │  2. confidence gates — per domain        │
                        │  3. slot scoring     — weighted sum      │
                        └──────────────────┬──────────────────────┘
                                           │  next_action
                                           v
                                    orchestrator
                                           │
                    ┌──────────────┬───────┴───────┬──────────────┐
                    v              v               v              v
                 Medplum         Stedi           Moss          Twilio
                 (FHIR)      (X12 270/271)    (retrieval)       (SMS)
```

### Why this shape

**`shared/state.py` is the contract.** The voice track produces a `CallState`;
the decision engine consumes it. Nothing else crosses the boundary. The
function-calling schema is generated from the same dataclasses, so the prompt and
the parser cannot drift apart.

**`engine/decision.py` has no I/O.** No network, no LLM, no clock unless you pass
one in. It is a pure function from state to action, and it is the thing that
decides whether a patient gets booked. If it ever needs a mock to test, something
has leaked in that does not belong.

**Confidence is scored per domain, and scored low when the model is inferring
rather than hearing.** Any domain below 0.7 raises a Medplum `Task` for human
review, and the agent says so on the call. That is the human-in-the-loop claim
made concrete rather than asserted.

---

## The safety branch

Red flags are evaluated **first, every turn**, before anything else — a hard
branch at the top of the engine, not a line buried in a system prompt.

The screen is for **cauda equina syndrome**: compression of the nerve roots below
the conus, presenting as bowel or bladder change, saddle anaesthesia, and
bilateral leg weakness. It is a surgical emergency measured in hours, and it is
the one thing an outpatient PT front desk must never book around.

When it fires, the agent stops cold, advises emergency care, writes a `stat`
Medplum Task, and **does not book**. No exceptions.

It is deliberately conservative — one flag is enough. A false positive costs a
human five minutes on the phone. A false negative costs a spinal cord.

```python
def test_emergency_beats_a_fully_bookable_call():
    """Everything else is perfect. It still must not book."""
```

---

## Patient matching

A date of birth spoken over an 8kHz phone line is the hardest thing in the call
to transcribe, and exact name+DOB matching turned a returning patient into a
stranger on the first live test — losing her history, her therapist, and the
entire reason the product is interesting.

So matching works the way a front desk actually does, strongest signal first:

| Tier | Signal | Confidence |
|---|---|---|
| 1 | **Caller ID** — resolved before the phone stops ringing | 0.90 |
| 2 | Name + date of birth | 0.95 |
| 3 | Name alone, only when unambiguous | 0.60 → raises a review Task |

Tier 3 is deliberately cautious: two Alvarezes on file means we create a new
record rather than guess which is which.

---

## Slot scoring

A weighted sum over four signals — deliberately **not** a learned policy:

| Signal | Weight |
|---|---|
| Urgency (severity, escalation) | 0.35 |
| Provider match (continuity of care) | 0.25 |
| Historical acceptance | 0.20 |
| Time to appointment | 0.20 |

This is the reward function. Once there is call volume, it becomes a learned
policy. Nothing here was trained.

Continuity of care is weighted heavily enough to beat a slightly earlier opening
with a stranger — which is usually what the patient actually wants.

---

## FHIR resources written

One phone call, eight resources. This is the artifact that separates Relay from
an AI receptionist.

| Resource | Written when |
|---|---|
| `Patient` | Identity resolved or created |
| `Coverage` | Eligibility returns |
| `Condition` | Symptoms captured |
| `Appointment` | Slot booked |
| `Slot` | Status flipped to `busy` |
| `Encounter` | Call completes |
| `Communication` | SMS body + call record |
| `Task` | Low confidence, or emergency escalation |

---

## Stack

| Layer | Tool |
|---|---|
| Telephony | Twilio Voice (media streams, mu-law 8kHz) |
| Orchestration | Pipecat |
| Speech | Deepgram `nova-2-phonecall` STT + Aura TTS |
| Reasoning | Claude Opus 5 (function calling only — never prose to a service) |
| Records + scheduling | Medplum (FHIR R4) |
| Eligibility | Stedi (X12 270/271) |
| Retrieval | Moss (Rust/WASM session index, in-process) |
| Surface | FastAPI + zero-build HTML dashboard |

Medplum `Schedule`/`Slot` resources are the scheduling source of truth rather
than an external calendar, which keeps the appointment book and the medical
record in the same system.

---

## Running it

```powershell
.\run.ps1 test       # unit tests, no credentials needed
.\run.ps1 verify     # check every credential against the real service
.\run.ps1 seed       # reset + seed demo data in Medplum
.\run.ps1 serve      # FastAPI on :8000
.\run.ps1 tunnel     # ngrok
.\run.ps1 relink     # ngrok URL changed -> fix .env + Twilio webhook
.\run.ps1 fakecall   # simulate a Twilio call, no phone needed
.\run.ps1 dryrun     # drive both demo calls against real Medplum
```

Copy `.env.example` to `.env` and fill it in. `verify` will tell you what is
missing and make a real call to each service — a key with no credit behind it
shows as a failure, not a pass.

### Fixture discipline

Every external response is captured to `fixtures/` on first success, and
`USE_FIXTURES=true` replays them. If a sandbox falls over mid-demo, one env var
keeps everything working. The capture is guarded: an eligibility *error* is still
HTTP 200, and overwriting a good fixture with one that says "not covered" is a
failure you would only discover on stage.

---

## Honest status

What is real, and what is not:

| | Status |
|---|---|
| Voice pipeline | **Working.** Verified end to end — TwiML, media stream, STT, LLM, TTS. |
| Decision engine + safety branch | **Working.** 46 unit tests. |
| Caller ID matching | **Working.** Resolves a returning patient before she speaks. |
| Medplum write-back | **Working.** Real appointments, real resources. |
| Stedi eligibility | **Working.** Correct endpoint and auth; genuine X12 271 responses. Replays a captured real response during the demo so a mis-heard member ID cannot break the flow. |
| SMS confirmation | **Blocked.** Code works; US carriers reject unregistered A2P 10DLC traffic (error 30034). Registration takes days. |
| Moss retrieval | **Working.** Live session index. Measured median **4.8ms**, p95 7.8ms over 50 queries — genuinely sub-10ms, so the prior episode lands mid-sentence with no perceptible pause. Falls back to a local keyword index if credentials are absent. |

Nothing here was trained today. The scoring function is hand-written, and it is
described as a reward function precisely because that is what it would become.

---

## What is next

- Waitlist backfill when a slot cancels — the scoring function already ranks
  candidates
- Outbound follow-up calls after a missed appointment
- No-show prediction feeding the urgency term
- Travel time and clinic utilisation as scoring signals
- Learned policy over the scoring weights, once there is call volume


