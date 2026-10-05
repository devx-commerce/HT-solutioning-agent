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
    text = classify._CLASSIFY_INSTRUCTION
    assert "calendar invitations" in text and "rate cards" in text
    assert "Never an explanation" in text
