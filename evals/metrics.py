"""Deterministic checks the LLM judge can't be trusted with, as ADK custom metrics.

Registered in evals/test_config.json under `custom_metrics`. Each reads the
tool calls ADK recorded for the run, so no extra logging is needed:

* deck_quality_gate          a deck was published with HT's logo on the cover
                             and at least three generated images;
* no_competitor_sources      no competitor publication's link anywhere in the
                             research results, the deck or the reply;
* component_names_consistent every card on the deck's overview slide
                             reappears, word for word, as the eyebrow of a
                             later slide.

Each scores 1.0 or a fraction per invocation; an invocation that built no deck
scores 0 on the deck checks.
"""

from __future__ import annotations

import json
import re

from google.adk.evaluation.eval_case import get_all_tool_calls_with_responses
from google.adk.evaluation.eval_metrics import EvalMetric
from google.adk.evaluation.evaluator import EvalStatus, EvaluationResult, PerInvocationResult

from agents.solutioning_agent.tools import master_deck, source_policy

_URL = re.compile(r"https?://[^\s\"'<>)\]]+")


def _built_deck(invocation) -> tuple[dict | None, dict | None]:
    """The last deck build that published: (deck as passed in, the tool's response)."""
    found = (None, None)
    for call, response in get_all_tool_calls_with_responses(invocation.intermediate_data):
        if call.name != "build_solution_deck":
            continue
        result = (response.response if response else None) or {}
        if "link" not in result:
            continue
        try:
            deck = json.loads((call.args or {}).get("deck_json") or "", strict=False)
        except (TypeError, ValueError):
            deck = None
        found = (deck, result)
    return found


def _threshold(eval_metric: EvalMetric) -> float:
    """ADK hands custom metrics their threshold in the criterion, not the metric."""
    if eval_metric.threshold is not None:
        return eval_metric.threshold
    criterion = getattr(eval_metric, "criterion", None)
    return getattr(criterion, "threshold", None) if getattr(criterion, "threshold", None) is not None else 1.0


def _result(eval_metric: EvalMetric, invocations, expected, scores: list[float]) -> EvaluationResult:
    threshold = _threshold(eval_metric)
    per = [
        PerInvocationResult(
            actual_invocation=inv,
            expected_invocation=(expected[i] if expected and i < len(expected) else None),
            score=score,
            eval_status=EvalStatus.PASSED if score >= threshold else EvalStatus.FAILED,
        )
        for i, (inv, score) in enumerate(zip(invocations, scores))
    ]
    if not per:
        return EvaluationResult(overall_eval_status=EvalStatus.NOT_EVALUATED)
    overall = sum(scores) / len(scores)
    return EvaluationResult(
        overall_score=overall,
        overall_eval_status=EvalStatus.PASSED if overall >= threshold else EvalStatus.FAILED,
        per_invocation_results=per,
    )


def deck_quality_gate(eval_metric, actual_invocations, expected_invocations=None, conversation_scenario=None):
    scores = []
    for inv in actual_invocations:
        _, result = _built_deck(inv)
        if not result:
            scores.append(0.0)
            continue
        images = (result.get("images") or {}).get("generated", 0)
        checks = [bool(result.get("ht_logo")), images >= master_deck.MIN_IMAGES]
        scores.append(sum(checks) / len(checks))
    return _result(eval_metric, actual_invocations, expected_invocations, scores)


def _text_of(value) -> str:
    return json.dumps(value, ensure_ascii=False, default=str) if not isinstance(value, str) else value


def no_competitor_sources(eval_metric, actual_invocations, expected_invocations=None, conversation_scenario=None):
    scores = []
    for inv in actual_invocations:
        # What the agent cites, not every page it read: a client's own site
        # (fetch_url) may well link to a TOI story without it being used.
        haystack = [_text_of(inv.final_response.model_dump() if inv.final_response else "")]
        for call, response in get_all_tool_calls_with_responses(inv.intermediate_data):
            if call.name in ("build_solution_deck", "update_deck"):
                haystack.append(_text_of(call.args or {}))
            elif call.name in ("search_web", "search_past_decks", "search_youtube") and response:
                haystack.append(_text_of((response.response or {}).get("findings") or []))
                haystack.append(_text_of((response.response or {}).get("results") or []))
        urls = {u for text in haystack for u in _URL.findall(text)}
        scores.append(0.0 if any(source_policy.is_blocked(u) for u in urls) else 1.0)
    return _result(eval_metric, actual_invocations, expected_invocations, scores)


def _norm(text) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().lower()


def component_names_consistent(eval_metric, actual_invocations, expected_invocations=None, conversation_scenario=None):
    """The overview is the first feature-grid whose cards later become eyebrows."""
    scores = []
    for inv in actual_invocations:
        deck, _ = _built_deck(inv)
        slides = (deck or {}).get("slides") or []
        score = 0.0
        for i, slide in enumerate(slides):
            if not isinstance(slide, dict) or slide.get("layout") != "feature-grid":
                continue
            titles = [_norm(c.get("title")) for c in slide.get("cards") or [] if isinstance(c, dict)]
            later = {_norm(s.get("eyebrow")) for s in slides[i + 1:] if isinstance(s, dict)}
            hits = [t for t in titles if t and t in later]
            if titles and len(hits) >= 2:
                score = len(hits) / len(titles)
                break
        scores.append(score)
    return _result(eval_metric, actual_invocations, expected_invocations, scores)
