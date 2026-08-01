"""Parser tests against the fixture. No network.

When the real Stedi capture replaces the fixture, these tests are what tell you
whether the parser still finds the three numbers the agent says out loud.
"""

from __future__ import annotations

import json

from services.stedi import FIXTURE_PATH, parse


def _fixture():
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_fixture_parses_to_active_coverage():
    """The fixture is a real captured UnitedHealthcare 271, not a hand-written one."""
    assert parse(_fixture()).covered is True


def test_plan_wide_service_type_30_is_not_discarded():
    """Real 271s answer a PT query with plan-wide `30` lines.

    Accepting only `PT` threw away every benefit figure in a live response and
    made a covered patient look like they had no benefits at all.
    """
    e = parse(_fixture())
    assert e.deductible_remaining is not None


def test_deductible_uses_the_remaining_line():
    """timeQualifier 29 = Remaining. This plan's in-network deductible is met."""
    assert parse(_fixture()).deductible_remaining == 0.0


def test_zero_deductible_is_spoken_as_met_not_as_zero():
    """"$0 remaining" is correct and sounds like a bug. Say it in English."""
    spoken = parse(_fixture()).spoken()
    assert "already met" in spoken
    assert "$0" not in spoken


def test_out_of_network_figures_are_not_quoted():
    """The patient is coming to us, so in-network is the number that is true.

    This fixture carries an $1800 out-of-network remaining deductible; quoting
    it would alarm a patient whose real liability is nil.
    """
    assert parse(_fixture()).deductible_remaining != 1800.0


def test_synthetic_copay_and_referral_still_parse():
    """The captured plan has no copay line, so cover those branches directly."""
    payload = {
        "benefitsInformation": [
            {"code": "1", "serviceTypeCodes": ["PT"]},
            {
                "code": "B",
                "serviceTypeCodes": ["PT"],
                "benefitAmount": "40.00",
                "inPlanNetworkIndicator": "Yes",
                "authOrCertIndicator": "Y",
                "benefitsDateInformation": {"expiration": "2026-10-31"},
            },
            {
                "code": "C",
                "serviceTypeCodes": ["PT"],
                "benefitAmount": "310.00",
                "timeQualifierCode": "29",
                "inPlanNetworkIndicator": "Yes",
            },
        ]
    }
    e = parse(payload)
    assert e.copay == 40.0
    assert e.deductible_remaining == 310.0
    assert e.referral_required is True
    assert e.referral_valid_through == "2026-10-31"
    spoken = e.spoken()
    assert "$40" in spoken and "$310" in spoken


def test_no_benefits_means_not_covered():
    e = parse({"benefitsInformation": []})
    assert e.covered is False
    assert "not able to confirm" in e.spoken()


def test_missing_amounts_do_not_crash():
    payload = {
        "benefitsInformation": [
            {"code": "1", "serviceTypeCodes": ["PT"]},
            {"code": "B", "serviceTypeCodes": ["PT"], "benefitAmount": None},
        ]
    }
    e = parse(payload)
    assert e.covered is True
    assert e.copay is None
    # Still says something sane rather than "$None".
    assert "covered" in e.spoken().lower()
