# CONTEXT.md

# Relay
## Autonomous Patient Operations Platform

Version: 1.0

---

# Mission

Relay is an autonomous patient operations platform built specifically for outpatient Physical Therapy and Orthopedic clinics.

Relay is **not** an AI receptionist.

Relay behaves like an experienced patient operations employee capable of managing the complete operational lifecycle surrounding a patient's care while keeping clinicians in control.

The system continuously improves clinic efficiency by reducing administrative burden, increasing appointment utilization, and maintaining a longitudinal patient profile built on FHIR resources inside Medplum.

Every interaction contributes to a richer understanding of the patient and improves future operational decisions.

---

# Problem Statement

Physical therapy clinics spend an enormous amount of administrative effort performing repetitive work including:

- answering phone calls
- collecting patient demographics
- collecting medical history
- verifying insurance
- validating referrals
- scheduling appointments
- rescheduling appointments
- reminding patients
- following up after appointments
- documenting conversations
- handling cancellations
- managing waitlists

These repetitive workflows consume valuable staff time while contributing little clinical value.

Meanwhile, clinics suffer from:

- high cancellation rates
- recurring scheduling conflicts
- no-shows
- fragmented documentation
- incomplete patient profiles
- manual insurance verification
- inconsistent follow-up
- underutilized appointment capacity

Relay automates these operational workflows while ensuring every action becomes structured healthcare data.

---

# Product Vision

Every clinic should have an autonomous operations employee available 24/7.

Relay continuously performs administrative work before, during, and after every patient encounter.

Rather than replacing clinicians, Relay maximizes clinician efficiency by ensuring providers spend less time coordinating care and more time delivering care.

---

# North Star Metric

Appointment Utilization %

Everything in the platform should improve this metric.

Secondary metrics include:

- insurance verification rate
- cancellation recovery rate
- filled waitlist appointments
- average intake duration
- average scheduling duration
- administrative hours saved
- no-show reduction
- patient satisfaction
- follow-up completion rate

---

# Product Philosophy

The system is built around one principle:

> Every conversation updates the longitudinal patient record.

Nothing is stateless.

Nothing is temporary.

Every interaction contributes to future decision making.

---

# Design Principles

## Safety First

Patient safety always overrides optimization.

Emergency symptoms terminate automation and trigger escalation.

---

## Human-In-The-Loop

Automation exists to reduce workload.

Not eliminate human oversight.

Whenever confidence falls below thresholds:

Create a Task.

Never hallucinate.

---

## FHIR First

Medplum is the canonical source of truth.

The LLM is never the database.

Every meaningful event becomes structured FHIR resources.

---

## Deterministic Execution

The LLM reasons.

Services execute.

The workflow engine makes deterministic decisions using structured outputs.

---

## Observable System

Every decision should be explainable.

Every action should be logged.

Every workflow should be reproducible.

---

# High-Level Architecture

Inbound Phone Call

↓

Pipecat

↓

Deepgram Streaming STT

↓

LLM Reasoning

↓

Structured JSON Extraction

↓

Decision Engine

↓

Workflow Executor

↓

Medplum

↓

Google Calendar

↓

STEDI

↓

SMS

↓

Dashboard

---

# Sponsor Integrations

## Pipecat

Primary orchestration framework.

Responsibilities

- conversation orchestration
- streaming pipeline
- voice state management
- interruptions
- latency optimization
- workflow transitions
- agent lifecycle

Pipecat coordinates every conversation.

---

## Deepgram

Speech infrastructure.

Responsibilities

- speech-to-text
- text-to-speech
- streaming transcription
- timestamps
- speaker segmentation

---

## Medplum

Healthcare backend.

System of record.

Responsibilities

- Patient resources
- Appointment resources
- QuestionnaireResponse
- Observation
- Encounter
- Communication
- Task
- CarePlan
- Goal
- Practitioner
- Organization
- Provenance
- DocumentReference

Every workflow ultimately updates Medplum.

---

## STEDI

Insurance infrastructure.

Responsibilities

- eligibility verification
- payer lookup
- coverage validation
- deductible
- copay
- referral requirements

Insurance is verified before appointments whenever possible.

---

## Google Calendar

Scheduling source of truth.

Responsibilities

- provider calendars
- room availability
- scheduling conflicts
- rescheduling
- recurring appointments

---

## Moss

Clinic operations analytics.

Rather than participating directly in patient conversations, Moss powers operational intelligence.

Responsibilities

- recovered revenue estimation
- appointment utilization analytics
- administrative cost savings
- operational KPI dashboard
- cancellation impact
- recovered capacity
- financial reporting

Example Metrics

Recovered Revenue Today

Admin Hours Saved

Recovered Appointment Slots

Cancellation Cost

Insurance Approval Rate

Utilization %

---

# Patient Journey

Patient calls clinic

↓

Identity

↓

Insurance verification

↓

Referral verification

↓

Collect demographics

↓

Collect symptoms

↓

Determine urgency

↓

Determine PT specialty

↓

Provider matching

↓

Calendar optimization

↓

Appointment scheduling

↓

SMS confirmation

↓

Reminder

↓

Appointment

↓

Follow-up call

↓

Progress assessment

↓

FHIR updates

↓

Patient profile strengthened

---

# Emergency Workflow

Potential emergency detected

↓

Immediately advise:

Call 911 or go to the nearest emergency department.

↓

End automation

↓

Create Medplum Task

↓

Notify clinic

↓

Human follow-up

Safety always overrides scheduling.

---

# Human Escalation

Relay intentionally escalates uncertainty.

Confidence is evaluated separately for:

Identity

Insurance

Referral

Symptoms

Scheduling

FHIR Mapping

If any confidence falls below threshold:

Create Task

Assign human review

Continue only after validation

---

# Decision Engine

Relay separates reasoning from execution.

Conversation

↓

Structured JSON

↓

Decision Engine

↓

Recommended Action

↓

Execution

LLMs never directly modify clinic systems.

---

# Reinforcement Learning

The Decision Engine continuously improves scheduling policies.

Optimization objectives:

Increase appointment utilization

Reduce no-shows

Reduce cancellations

Improve patient satisfaction

Increase same-day fill rate

State includes:

distance

travel time

patient flexibility

historical acceptance

historical no-show

provider preference

insurance

referral status

urgency

availability

The RL policy never overrides safety.

---

# Patient Profile

Every patient maintains a continuously evolving operational profile.

Identity

Insurance

Referral

Preferred therapist

Preferred clinic

Transportation estimate

Travel distance

Availability model

Communication preferences

Pain history

Mobility history

Cancellation history

No-show probability

Acceptance probability

Follow-up history

Goals

Current workflow

Outstanding tasks

Risk flags

Confidence scores

Last interaction

Next recommended action

---

# Patient Operations Brief

Every completed workflow generates an operational summary.

Includes:

Patient

Insurance

Provider Match

Symptoms

Urgency

Referral Status

Appointment

Outstanding Tasks

Follow-up Schedule

Operational Notes

AI Confidence

Human Review Requirements

This brief becomes the primary handoff artifact for clinicians and staff.

---

# Success Criteria

Judges should conclude:

"Relay behaves like an autonomous patient operations employee that continuously improves clinic efficiency while maintaining safe human oversight and creating high-quality structured healthcare data."

