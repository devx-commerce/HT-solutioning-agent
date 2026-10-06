"""Shared test setup."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from agents.solutioning_agent.tools import visuals


@pytest.fixture(autouse=True)
def _offline_visuals():
    """Every deck build fetches logos and generates images; never for real here.

    agent.py loads the real .env, so without this a test building a deck would
    call Drive and Imagen. tests/test_visuals.py overrides this fixture to test
    the real functions with their network calls mocked instead.
    """
    with patch.object(visuals, "ht_logo", return_value=None), \
         patch.object(visuals, "client_logo", return_value=None), \
         patch.object(visuals, "generate_image", return_value=None):
        yield


@pytest.fixture(autouse=True)
def _as_in_ci(monkeypatch):
    """Tests run in the deploy before any .env is written; a local run reads
    the generated one. Pin the settings tests depend on to their defaults so
    both agree: the Drive connector for past-deck search (tests of the
    BigQuery index switch it on themselves) and no picture bucket."""
    from agents.solutioning_agent.tools import deck, research

    monkeypatch.setattr(research, "PAST_DECKS_SOURCE", "vertex")
    monkeypatch.setattr(deck, "DECK_IMAGES_BUCKET", "")
