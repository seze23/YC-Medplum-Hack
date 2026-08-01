# Relay — 90-Second Pitch Script

*Read time: ~90 seconds at a normal pace. Cut the bracketed lines first if you need to trim to 60.*

---

## Problem (20s)

Outpatient PT and ortho clinics run their entire front desk on phone calls and sticky notes.

Calls go unanswered while staff are with a patient — that patient books somewhere else. Insurance verification means 10 to 15 minutes on hold with a payer, so it gets skipped, and skipped eligibility means denied claims and surprise bills. And when a patient does get booked, none of their history — prior episode, their therapist, why they cancelled last time — makes it to whoever answers the phone next. Every call starts from zero.

## Product (15s)

Relay is an autonomous patient operations employee, not an AI receptionist. It answers the phone, texts, and runs the administrative lifecycle around a patient's care — before, during, and after the appointment — while a human stays in control of anything it isn't sure about.

## Solution (45s)

A patient calls. Caller ID resolves them before the agent even speaks — it already knows their last episode and their therapist. Deepgram and Claude run the conversation over Twilio and Pipecat; Moss pulls that prior history into the reply in under 5 milliseconds, so it lands mid-sentence.

The agent screens for red flags — cauda equina syndrome, a surgical emergency — before anything else, every turn. If it fires, the call stops cold, a `stat` task alerts staff, and nothing gets booked. Otherwise it verifies insurance live against the payer through Stedi, checks for a required referral, scores open slots on urgency and continuity of care, and books.

But here's the part that matters: the LLM never touches the record. It fills in a structured object; a deterministic engine decides what happens next. That's what makes a clinic actually willing to turn this on.

After the call, AgentPhone sends the confirmation by text, and the same channel handles what comes next — a reschedule request, a cancellation that instantly re-offers the slot to the top-ranked waitlisted patient, a missed follow-up check-in, an urgent reply that pages staff directly. Every one of those becomes structured FHIR data in Medplum — Patient, Coverage, Condition, Appointment, Task — so the next call, or the next text, starts smarter than the last one.

## Close (10s)

One phone call becomes eight FHIR resources and a clinic that runs itself a little more each day. That's Relay.
