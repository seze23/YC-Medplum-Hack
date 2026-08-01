# Relay — demo script

**Phone:** +1 361 338 5046 · **Call from:** +1 626 715 9929 (caller ID is seeded to John Doe)
**Dashboard:** http://127.0.0.1:8000/dashboard

**Reset between takes:** `.\run.ps1 seed`

---

## Before you start

```powershell
.\run.ps1 verify     # everything should be green
.\run.ps1 seed       # fresh demo data
```

Three windows open: **server** (`.\run.ps1 serve`), **ngrok** (`.\run.ps1 tunnel`),
**dashboard** in a browser.

> ⚠️ If ngrok restarted, its URL changed. Run `.\run.ps1 relink` and restart the
> server, or Twilio will be calling a dead address.

---

## Call 1 — the returning patient (~3 min)

Call **from +1 626 715 9929**. Caller ID resolves John Doe before the agent speaks.

| You say | What should happen |
|---|---|
| *(just listen first)* | ⭐ Agent opens with **"is that John?"** — it already knows |
| *"Yes, it's John."* | His prior right-shoulder episode and Dr. Chen are already loaded |
| *"My right shoulder is hurting again."* | Records body site; should reference the prior episode |
| *"About three weeks. Maybe a six out of ten."* | Onset + severity captured |
| *"UnitedHealthcare. Member ID U-H-C-2-0-2-6-4-9."* | ⭐ **LIVE** eligibility check against UnitedHealthcare |
| *"Yes, that works."* | Books with **Dr. Chen** — continuity beat an earlier slot |

**Pause after each line.** Let it finish before you speak.

---

## Call 2 — the emergency (~1 min)

Short, and it wins more trust than anything else in the demo.

| You say | What should happen |
|---|---|
| *"Hi, this is James Whitfield."* | Asks for date of birth |
| *"November third, nineteen ninety."* | Identified |
| *"My lower back is really bad, and since yesterday I've had numbness between my legs and trouble controlling my bladder."* | 🛑 **Stops cold** |

**Must:** refuse to book, advise the emergency department, say a clinician has been
alerted. **Must not:** offer an appointment.

Those symptoms are **cauda equina syndrome** — nerve root compression, a surgical
emergency measured in hours.

> If asked why not chest pain: chest pain is the generic demo. Cauda equina is the
> *physiotherapy-specific* emergency. Almost nobody else will get this right.

---

## Where to show the receipts

**1. Dashboard** — http://127.0.0.1:8000/dashboard
Live during the call; confidence bars fill per domain. After: resource counts,
utilisation, the escalated Task.

**2. Medplum** — https://app.medplum.com
`Patient` · `Condition` · `Coverage` · `Appointment` · `Slot` (`free`→`busy`) ·
`Encounter` · `Communication` · `Task` (🛑 the `stat` one from call 2)

**3. Twilio call log** — https://console.twilio.com/us1/monitor/logs/calls

---

## Lines worth saying

> "The LLM never writes to the record. It fills in a structured object, and a
> deterministic engine decides what happens. That's how you get an autonomous
> agent a clinic will actually turn on."

> "This scoring function is the reward function. Once we have call volume, it
> becomes a learned policy."

> "Caller ID resolves the patient before the phone stops ringing — same as a real
> clinic system. The date of birth is confirmation, not the lookup key."

**Do not say** you trained anything today.

---

## Honest answers to likely questions

**"Is the insurance check real?"**
> Yes — it runs live during the call. Stedi's 270/271 API against UnitedHealthcare,
> and what you hear is parsed straight out of the X12 271 that comes back. The
> patient on file is Stedi's sandbox subscriber, because an eligibility check
> queries the *payer's* database — inventing a member would just mean asking a
> real payer about someone who doesn't exist.

**"What if it fails live?"**
> Set `USE_FIXTURES=true` and restart — it replays a real captured 271 instead.
> Same numbers, no network call.

**"Why doesn't it text me a confirmation?"**
> Cut. US carriers block unregistered A2P 10DLC traffic and registration takes
> days. The confirmation is spoken on the call and recorded as a FHIR
> `Communication`, which is the part that belongs in the record anyway.

**"Is Moss powering the retrieval?"**
> No. It's an in-process index with the same interface and latency profile. The
> Moss client is a one-file swap. We're not claiming otherwise.

---

## If something breaks

| Symptom | Fix |
|---|---|
| Call doesn't connect | ngrok died → `.\run.ps1 tunnel`, then `.\run.ps1 relink`, restart server |
| Agent silent | Check server log; usually Deepgram or the WebSocket |
| Eligibility hangs | `USE_FIXTURES=true` in `.env`, restart server |
| Dashboard unreachable | Medplum blip — the call still works, records still write |
| Phone won't cooperate at all | `.\run.ps1 fakecall` drives the pipeline with no phone; `.\run.ps1 dryrun` drives both calls against real Medplum |
| Everything on fire | Play the recorded backup take |

