"""The reply /work gets back from Agent Engine is the agent's answer, not a dump."""

from app.pipeline.agent_client import final_reply

LINK = "https://docs.google.com/presentation/d/abc123/edit?usp=drivesdk"


def _text(t):
    return {"content": {"role": "model", "parts": [{"text": t}]}, "author": "solutioning_agent"}


def _call(name):
    return {"content": {"role": "model", "parts": [{"function_call": {"name": name, "args": {}}}]}}


def _resp(name, response):
    return {"content": {"role": "user", "parts": [{"function_response": {"name": name, "response": response}}]}}


def test_only_the_text_after_the_last_tool_call_is_returned():
    events = [
        _text("I'll search past decks first."),
        _call("search_past_decks"), _resp("search_past_decks", {"results": []}),
        _call("build_solution_deck"), _resp("build_solution_deck", {"link": LINK}),
        _text("### Findings\n"), _text(f"* Deck: [open]({LINK})"),
    ]
    reply = final_reply(events)
    assert reply == f"### Findings\n* Deck: [open]({LINK})"
    assert "search past decks first" not in reply and "function_call" not in reply and "{" not in reply


def test_a_deck_link_the_answer_left_out_is_appended():
    events = [_call("build_solution_deck"), _resp("build_solution_deck", {"link": LINK}), _text("Done.")]
    assert final_reply(events) == f"Done.\n\nDeck: {LINK}"


def test_no_deck_tool_means_no_link_is_invented():
    events = [_call("search_web"), _resp("search_web", {"findings": []}), _text("I need more detail.")]
    assert final_reply(events) == "I need more detail."


def test_tolerates_events_without_content():
    assert final_reply([{}, {"content": None}, _text("ok")]) == "ok"
