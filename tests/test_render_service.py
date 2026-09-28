"""Rendering over HTTP: the agent must be able to tell a bad deck from an outage.

If "the renderer is warming up" came back as "your deck is invalid", the agent
would rewrite a deck that was fine, possibly several times.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
import requests

from agents.solutioning_agent.tools import deck as deck_tools

DECK = json.dumps({"type": "deck", "slides": []})


def _resp(status, body=b"", js=None):
    r = MagicMock(status_code=status, content=body, text=json.dumps(js) if js else "")
    r.json.return_value = js or {}
    return r


@pytest.fixture(autouse=True)
def service(monkeypatch):
    monkeypatch.setattr(deck_tools, "RENDER_URL", "https://renderer.example.run.app")
    with patch.object(deck_tools, "_id_token_headers", return_value={"Authorization": "Bearer t"}), \
         patch.object(deck_tools.time, "sleep") as sleep:
        yield sleep


def test_a_rendered_deck_comes_back_as_bytes():
    with patch.object(deck_tools.requests, "post", return_value=_resp(200, b"PK-pptx")) as post:
        assert deck_tools._render_pptx(DECK) == b"PK-pptx"
    assert post.call_args.args[0] == "https://renderer.example.run.app/render"
    assert post.call_args.kwargs["headers"]["Authorization"] == "Bearer t"


def test_an_invalid_deck_is_not_retried_and_names_the_field(service):
    bad = _resp(422, js={"error": "invalid", "details": ["/slides/2/number must be string"]})
    with patch.object(deck_tools.requests, "post", return_value=bad) as post:
        with pytest.raises(deck_tools.DeckRenderError, match="/slides/2/number must be string"):
            deck_tools._render_pptx(DECK)
    assert post.call_count == 1
    service.assert_not_called()


@pytest.mark.parametrize("first", [
    _resp(503, js={"error": "unavailable"}),
    _resp(500, js={"error": "render_failed", "message": "boom"}),
    _resp(429),
    requests.ConnectionError("cold start"),
    requests.Timeout("slow"),
])
def test_an_outage_is_retried_then_succeeds(first, service):
    with patch.object(deck_tools.requests, "post", side_effect=[first, _resp(200, b"PK")]) as post:
        assert deck_tools._render_pptx(DECK) == b"PK"
    assert post.call_count == 2
    service.assert_called_once_with(deck_tools._RENDER_RETRY_WAITS[0])


def test_a_persistent_outage_gives_up_after_a_few_attempts(service):
    with patch.object(deck_tools.requests, "post", side_effect=requests.ConnectionError("down")) as post:
        with pytest.raises(deck_tools.RendererUnavailable, match="after 4 attempts"):
            deck_tools._render_pptx(DECK)
    assert post.call_count == 4
    assert [c.args[0] for c in service.call_args_list] == list(deck_tools._RENDER_RETRY_WAITS)


def test_the_tool_tells_the_agent_not_to_change_the_deck_on_an_outage():
    from tests.test_master_deck import _deck

    with patch.object(deck_tools.requests, "post", side_effect=requests.ConnectionError("down")), \
         patch.object(deck_tools, "_upload_pptx") as upload:
        result = deck_tools.build_solution_deck(json.dumps(_deck()), "Acme", "b1")
    assert "The deck itself was NOT rejected: do not change it" in result["error"]
    assert "problems" not in result
    upload.assert_not_called()


def test_the_tool_passes_the_renderers_reason_through_for_an_invalid_deck():
    from tests.test_master_deck import _deck

    bad = _resp(422, js={"error": "invalid", "details": ["/slides/4/steps/3 must have required property 'title'"]})
    with patch.object(deck_tools.requests, "post", return_value=bad), \
         patch.object(deck_tools, "_upload_pptx") as upload:
        result = deck_tools.build_solution_deck(json.dumps(_deck()), "Acme", "b1")
    assert result["error"].startswith("Deck was not valid, nothing was published")
    assert "/slides/4/steps/3 must have required property 'title'" in result["error"]
    upload.assert_not_called()
