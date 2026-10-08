"""Readable brief references: how a client becomes a label, and matching."""

from __future__ import annotations

import pytest

from agents.solutioning_agent.tools import brief_refs


@pytest.mark.parametrize("client, label", [
    ("Tata Sampann", "Tata Sampann"),
    ("The Great Rocksport India Private Limited", "The Great Rocksport India"),
    ("India Gate", "India Gate"),
    ("Sheela Foam (Sleepwell)", "Sheela Foam (Sleepwell)"),
    ("Haleon (implied by email address and brands mentioned)", "Haleon"),
    ("Senco Gold & Diamonds", "Senco Gold & Diamonds"),
    ("", "Unnamed"),
])
def test_the_client_part_of_a_reference(client, label):
    assert brief_refs.client_label(client) == label


def test_a_reference_matches_however_it_is_typed():
    assert {brief_refs.slug(v) for v in ("Tata Sampann 3", "tata-sampann-3", "TATA  SAMPANN 3", " tata sampann 3 ")} == {"tata-sampann-3"}


@pytest.mark.parametrize("a, b, same", [
    ("Senco Gold", "Senco Gold & Diamonds", False), ("Liberty", "Liberty Shoes", False),
    ("Tata", "Tata Sampann", False), ("YAS Islands", "Yas Island", True), ("Yas Island", "Yas Island Abu Dhabi", False),
    ("The Great Rocksport India Pvt. Ltd.", "Great Rocksport India", True), ("FlixBus", "Flixbus", True), ("Sheela Foam (Sleepwell)", "Sleepwell", True), ("ITA (Italian Trade Agency)", "Italian Trade Agency", True),
    ("India Gate", "Air India", False),
    ("Tata Sampann", "Tata Motors", False), ("Mother Dairy", "Mother Dairy", True), ("Decathlon", "Rapido", False),
])
def test_one_client_named_a_little_differently_is_one_series(a, b, same):
    assert brief_refs.same_client(a, b) is same
