"""The eval set's deterministic metrics (evals/metrics.py), on recorded runs."""

from __future__ import annotations

import json

import pytest

pytest.importorskip("pandas", reason="needs the ADK eval extras (requirements-dev.txt)")

from google.adk.evaluation.eval_case import IntermediateData, Invocation  # noqa: E402
from google.adk.evaluation.eval_metrics import EvalMetric  # noqa: E402
from google.adk.evaluation.evaluator import EvalStatus  # noqa: E402
from google.genai import types  # noqa: E402

from evals import metrics  # noqa: E402

PILLARS = ["Haat Activation", "Kirana Mystery Shopper", "Print Domination", "Radio Storytelling"]


def _deck(eyebrows=PILLARS):
    return {"slides": [
        {"layout": "title", "heading": "x"},
        {"layout": "feature-grid", "cards": [{"title": t} for t in PILLARS]},
        *({"layout": "two-column", "eyebrow": e, "heading": "y"} for e in eyebrows),
        {"layout": "closing", "heading": "z"},
    ]}


def _run(*calls, reply="Done."):
    """An invocation from (tool name, args, response) triples."""
    uses, responses = [], []
    for i, (name, args, response) in enumerate(calls):
        uses.append(types.FunctionCall(id=f"c{i}", name=name, args=args))
        responses.append(types.FunctionResponse(id=f"c{i}", name=name, response=response))
    return Invocation(
        user_content=types.Content(role="user", parts=[types.Part(text="brief")]),
        final_response=types.Content(role="model", parts=[types.Part(text=reply)]),
        intermediate_data=IntermediateData(tool_uses=uses, tool_responses=responses),
    )


def _build(deck=None, ht_logo=True, generated=3, link=True):
    response = {"ht_logo": ht_logo, "images": {"generated": generated}}
    if link:
        response["link"] = "https://docs.google.com/presentation/d/x/edit"
    return ("build_solution_deck", {"deck_json": json.dumps(deck or _deck())}, response)


def _metric(name, threshold):
    return EvalMetric(metric_name=name, threshold=threshold)


def test_a_published_deck_with_logo_and_three_images_passes_the_gate():
    result = metrics.deck_quality_gate(_metric("deck_quality_gate", 1.0), [_run(_build())])
    assert result.overall_score == 1.0 and result.overall_eval_status == EvalStatus.PASSED


@pytest.mark.parametrize("build, score", [
    (_build(generated=2), 0.5),
    (_build(ht_logo=False, generated=0), 0.0),
    (_build(link=False), 0.0),  # rejected, never published
])
def test_the_gate_scores_what_is_missing(build, score):
    result = metrics.deck_quality_gate(_metric("deck_quality_gate", 1.0), [_run(build)])
    assert result.overall_score == score and result.overall_eval_status == EvalStatus.FAILED


def test_the_last_published_build_is_the_one_scored():
    run = _run(_build(link=False), _build(generated=4))
    assert metrics.deck_quality_gate(_metric("deck_quality_gate", 1.0), [run]).overall_score == 1.0


def test_a_competitor_link_anywhere_the_agent_cites_fails():
    search = ("search_web", {"query": "q"}, {"findings": [
        {"claim": "c", "sources": [{"url": "https://economictimes.indiatimes.com/x"}]}]})
    result = metrics.no_competitor_sources(_metric("no_competitor_sources", 1.0), [_run(search, _build())])
    assert result.overall_score == 0.0


def test_a_competitor_link_in_the_reply_fails():
    run = _run(_build(), reply="Per https://timesofindia.indiatimes.com/a the market grew.")
    assert metrics.no_competitor_sources(_metric("no_competitor_sources", 1.0), [run]).overall_score == 0.0


def test_a_page_read_with_fetch_url_is_not_held_against_the_agent():
    page = ("fetch_url", {"url": "https://client.in"}, {"text": "As seen on https://timesofindia.indiatimes.com/a"})
    run = _run(page, _build(), reply="Sources: https://www.afaqs.com/a")
    assert metrics.no_competitor_sources(_metric("no_competitor_sources", 1.0), [run]).overall_score == 1.0


def test_every_overview_card_reused_as_an_eyebrow_scores_one():
    result = metrics.component_names_consistent(_metric("component_names_consistent", 0.75), [_run(_build())])
    assert result.overall_score == 1.0


def test_renamed_components_lower_the_score():
    deck = _deck(eyebrows=["Haat Activation", "Kirana Mystery Shopper", "Pillar 3: Print", "Audio Blitz"])
    result = metrics.component_names_consistent(_metric("component_names_consistent", 0.75), [_run(_build(deck))])
    assert result.overall_score == 0.5 and result.overall_eval_status == EvalStatus.FAILED


def test_a_deck_with_no_overview_scores_zero():
    deck = {"slides": [{"layout": "title", "heading": "x"}, {"layout": "quote", "quote": "y"}]}
    assert metrics.component_names_consistent(
        _metric("component_names_consistent", 0.75), [_run(_build(deck))]).overall_score == 0.0



def test_the_threshold_is_read_the_way_adk_actually_passes_it():
    """The first real run failed: ADK sets criterion.threshold, not threshold."""
    from google.adk.evaluation.eval_config import EvalConfig, get_eval_metrics_from_config

    config = EvalConfig.model_validate({
        "criteria": {"component_names_consistent": 0.75},
        "custom_metrics": {"component_names_consistent": {
            "code_config": {"name": "evals.metrics.component_names_consistent"}}},
    })
    (metric,) = get_eval_metrics_from_config(config)
    deck = _deck(eyebrows=["Haat Activation", "Kirana Mystery Shopper", "Print Domination", "Audio"])
    result = metrics.component_names_consistent(metric, [_run(_build(deck))])
    assert result.overall_score == 0.75 and result.overall_eval_status == EvalStatus.PASSED
