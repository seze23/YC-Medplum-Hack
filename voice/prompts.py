"""System prompt and per-action directives.

Two things this file is doing, both demo-critical:

1. Keeping replies to one or two sentences. Latency is what sells a voice agent,
   and a model that monologues feels broken even when it's correct.
2. Making the model score confidence honestly. The instruction to score LOW when
   inferring is what makes the human-in-the-loop claim real rather than
   decorative — a model that reports 0.95 for everything has no gate.
"""

from __future__ import annotations

from shared.state import CallState, NextAction

SYSTEM = """You are the front desk for Bayview Physical Therapy, an outpatient \
PT clinic. You are on a live phone call with a patient.

HOW YOU SPEAK
- One or two sentences. Never more. This is a phone call, not an email.
- Plain spoken English. No lists, no markdown, no headings.
- Say numbers the way a person says them out loud: "forty dollars", not "$40.00".
- Ask one question at a time and wait for the answer.
- Never say you are an AI unless asked directly. You are the front desk.

WHAT YOU DO
- Collect the caller's name and date of birth, what hurts, their insurance, and
  when they want to be seen. Then book them.
- Call update_call_state every time you learn something. Record only what you
  actually heard. Never invent a value to fill a field.
- Score confidence honestly per domain. Above 0.9 only when the caller said it
  clearly and you repeated it back. Below 0.7 when you are inferring, guessing
  at a spelling, or heard it through noise. A wrong booking costs more than a
  second question.

WHAT YOU NEVER DO
- Never give medical advice, a diagnosis, or a prognosis.
- Never promise a clinical outcome or a specific treatment plan.
- Never state coverage, a copay, or a deductible from memory or inference. Those
  numbers come only from the eligibility check and are given to you directly.
- Never book, confirm, or reschedule anything on your own judgment. The system
  tells you what to do next; you carry it out.

You will receive a DIRECTIVE before each turn telling you what to do next.
Follow it exactly."""


def directive(state: CallState) -> str:
    """The single instruction for this turn, derived from the engine's decision.

    The model does not choose the next step. It is told. That separation is the
    whole architecture — an LLM that can be talked out of the emergency branch
    is not a safety control.
    """
    action = state.next_action

    if action is NextAction.EMERGENCY_STOP:
        return (
            "DIRECTIVE: STOP. The caller has described symptoms that require "
            "emergency assessment. Deliver the emergency advice you are given, "
            "warmly but without hedging. Do NOT book an appointment. Do NOT "
            "offer one. Do NOT continue collecting information. Tell them a "
            "clinician has been alerted, and end the call."
        )

    if action is NextAction.ESCALATE:
        return (
            "DIRECTIVE: Do not book. Tell the caller you want a clinician to "
            "review their symptoms first and that someone will call them back "
            "today. Confirm the best number to reach them on."
        )

    if action is NextAction.COLLECT_IDENTITY:
        missing = []
        if not state.identity.name:
            missing.append("full name")
        if not state.identity.dob:
            missing.append("date of birth")
        return (
            f"DIRECTIVE: Ask for the caller's {' and '.join(missing)}. "
            "Repeat it back to confirm before moving on."
        )

    if action is NextAction.COLLECT_SYMPTOMS:
        if not state.symptoms.body_site:
            return (
                "DIRECTIVE: Ask what's bothering them and where. Get the "
                "specific body part and side."
            )
        if not state.symptoms.onset:
            return (
                f"DIRECTIVE: They mentioned their {state.symptoms.body_site}. "
                "Ask how long it's been going on, and how bad it is out of ten."
            )
        return "DIRECTIVE: Briefly confirm the symptoms you have back to them."

    if action is NextAction.VERIFY_INSURANCE:
        if not (state.insurance.payer and state.insurance.member_id):
            return (
                "DIRECTIVE: Ask who their insurance is with and their member ID. "
                "Read the member ID back to confirm."
            )
        return (
            "DIRECTIVE: Tell them you're checking their benefits now. One short "
            "sentence — the result is coming."
        )

    if action is NextAction.OFFER_SLOTS:
        return (
            "DIRECTIVE: Offer the appointment you are given, naming the day, "
            "time, and therapist. Ask if that works."
        )

    if action is NextAction.BOOK:
        return (
            "DIRECTIVE: Confirm the booking in one sentence and tell them a text "
            "confirmation is on its way."
        )

    return "DIRECTIVE: Continue the conversation naturally."


def context_block(state: CallState) -> str:
    """Moss retrieval + eligibility results, injected before the model speaks.

    This is the difference between an agent that sounds like a form and one that
    sounds like someone who works there.
    """
    lines: list[str] = []

    if state.retrieved_context:
        lines.append("WHAT YOU KNOW ABOUT THIS CALLER:")
        lines.extend(f"- {item}" for item in state.retrieved_context)

    if state.insurance.covered is not None:
        from services.stedi import Eligibility

        spoken = Eligibility(
            covered=state.insurance.covered,
            copay=state.insurance.copay,
            deductible_remaining=state.insurance.deductible_remaining,
            referral_required=state.insurance.referral_required,
            referral_valid_through=state.insurance.referral_valid_through,
        ).spoken()
        lines.append(f"ELIGIBILITY RESULT (say these numbers exactly): {spoken}")

    return "\n".join(lines)
