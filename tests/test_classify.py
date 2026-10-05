"""The classifier's client name and the rules in its instructions."""

from __future__ import annotations

import pytest

from app.pipeline import classify


@pytest.mark.parametrize("raw, clean", [
    ("Haleon (implied by email address and brands mentioned)", "Haleon"),
    ("Sheela Foam (Sleepwell)", "Sheela Foam (Sleepwell)"),
    ("YAS Islands", "YAS Islands"),
    ("  Liberty  ", "Liberty"),
    ("(not named in the email)", None),
    ("", None),
    (None, None),
])
def test_the_client_name_is_the_brand_alone(raw, clean):
    assert classify.clean_client_name(raw) == clean


def test_the_instructions_rule_out_invitations_and_rate_requests():
    decide = " ".join(classify._DECIDE_INSTRUCTION.split())
    assert "calendar invitation" in decide and "rate cards" in decide
    assert "judge this email alone" in decide
    assert "answers, delivers or updates a request the owner made" in decide
    assert "never an explanation" in " ".join(classify._EXTRACT_INSTRUCTION.split())
