"""Red flag triage. Runs first, every turn, before anything else.

Cauda equina syndrome is the correct PT-specific emergency: compression of the
nerve roots below the conus. It presents as bowel or bladder change, saddle
anaesthesia, and bilateral leg weakness. It is a surgical emergency measured in
hours, and it is the single thing an outpatient PT front desk must never book
around.

Two tiers:

  EMERGENCY  — stop the call, advise emergency care, write an urgent Task.
               No booking. No exceptions.
  ESCALATE   — do not book automatically. Hand to a human, same day.

Deliberately conservative: one flag is enough. A false positive costs a human
five minutes on the phone. A false negative costs a spinal cord.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Any one of these stops the call cold.
EMERGENCY_FLAGS: dict[str, str] = {
    "bowel_bladder_change": "New bowel or bladder dysfunction",
    "saddle_numbness": "Saddle anaesthesia",
    "bilateral_leg_weakness": "Bilateral lower limb weakness",
    "progressive_neuro_deficit": "Progressive neurological deficit",
}

# Serious, but the right answer is a human callback rather than the ED.
ESCALATE_FLAGS: dict[str, str] = {
    "fever_with_back_pain": "Fever with spinal pain — infection screen",
    "unexplained_weight_loss": "Unexplained weight loss — malignancy screen",
    "recent_major_trauma": "Recent major trauma — fracture screen",
}

CAUDA_EQUINA_SET = {
    "bowel_bladder_change",
    "saddle_numbness",
    "bilateral_leg_weakness",
}


@dataclass
class Triage:
    emergency: bool = False
    escalate: bool = False
    reasons: list[str] = field(default_factory=list)
    # True when the pattern is specifically cauda equina, which changes the
    # script: "go to the emergency department now", not "call your GP today".
    cauda_equina: bool = False

    @property
    def spoken_advice(self) -> str:
        if self.cauda_equina:
            return (
                "The symptoms you're describing need to be assessed today in an "
                "emergency department, not in physical therapy. Please go to your "
                "nearest emergency room, or call 911 if you can't get there "
                "safely. I'm not going to book you an appointment — this needs "
                "to be seen sooner than that. I've flagged this for our clinical "
                "team right now."
            )
        if self.emergency:
            return (
                "What you're describing needs urgent medical assessment before "
                "physical therapy. Please seek emergency care today. I've flagged "
                "this for our clinical team."
            )
        return (
            "I want one of our clinicians to review this before we book anything. "
            "Someone will call you back today."
        )


def evaluate(red_flags: list[str]) -> Triage:
    """Pure. No I/O, no LLM, no network. Called on every turn."""
    triage = Triage()

    for flag in red_flags:
        if flag in EMERGENCY_FLAGS:
            triage.emergency = True
            triage.reasons.append(EMERGENCY_FLAGS[flag])
        elif flag in ESCALATE_FLAGS:
            triage.escalate = True
            triage.reasons.append(ESCALATE_FLAGS[flag])

    if any(f in CAUDA_EQUINA_SET for f in red_flags):
        triage.cauda_equina = True

    # Emergency subsumes escalation — don't report both.
    if triage.emergency:
        triage.escalate = False

    return triage
