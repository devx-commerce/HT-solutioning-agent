"""config.yaml: the shipped file is valid, and a mistake stops the deploy with a reason."""

from __future__ import annotations

import copy

import pytest

from deploy import render_config as rc

CFG = rc.load()


def test_the_shipped_config_is_valid():
    assert rc.problems(CFG) == []


@pytest.mark.parametrize("path, value, says", [
    (("settings", "eval_summary_email"), "sales.agent", "eval_summary_email: must be an email address"),
    (("settings", "onboarding_domains"), ["@hindustantimes.com"], "without @"),
    (("settings", "excluded_senders"), ["payroll"], "full email addresses"),
    (("settings", "max_slides"), 50, "from 7 to 30"),
    (("settings", "min_images"), "3", "from 0 to 10"),
    (("settings", "sweep_schedule"), "every 30 minutes", "cron schedule"),
    (("settings", "usage_report_schedule"), "mondays", "cron schedule"),
    (("settings", "new_inbox_lookback_hours"), 2.5, "from 0 to 168"),
    (("settings", "deck_folder_id"), "https://drive.google.com/x", "Drive or Sheets id"),
    (("settings", "competitor_outlets"), {"TOI": "indiatimes.com"}, "list of its domains"),
    (("infrastructure", "agent_engine_id"), "", "agent_engine_id: is required"),
])
def test_a_mistake_is_explained_not_deployed(path, value, says):
    cfg = copy.deepcopy(CFG)
    cfg[path[0]][path[1]] = value
    found = rc.problems(cfg)
    assert any(says in p for p in found), found


def test_every_mistake_is_reported_at_once():
    cfg = copy.deepcopy(CFG)
    cfg["settings"]["max_slides"] = 1
    cfg["settings"]["eval_summary_email"] = "x"
    assert len(rc.problems(cfg)) == 2


def test_services_get_the_settings_the_code_reads():
    pipeline, agent = rc.pipeline_env(CFG), rc.agent_env(CFG, "yt")
    assert pipeline["ALLOWED_ONBOARD_DOMAIN"] == "hindustantimes.com,htdigital.in"
    assert pipeline["AGENT_EMAIL"] == "sales.agent@hindustantimes.com"
    assert pipeline["AGENT_ENGINE_RESOURCE"].endswith("/reasoningEngines/1985844993356464128")
    assert agent["RENDER_URL"] == "https://solutioning-agent-renderer-296974829876.us-central1.run.app"
    assert "STATE_SIGNING_KEY" not in pipeline  # a secret, mounted by the deploy, never a plain value
    assert rc.onboarding_env(CFG)["PUBLIC_ROUTES_ONLY"] == "true"


def test_the_defaults_in_code_match_the_shipped_config():
    """A setting left out of the deploy must not change behaviour."""
    from agents.solutioning_agent.tools import master_deck, source_policy
    from app.pipeline import ingestion

    s = CFG["settings"]
    assert list(ingestion._DEFAULT_EXCLUDED_SENDERS) == s["excluded_senders"]
    assert {k: list(v) for k, v in source_policy._DEFAULT_OUTLETS.items()} == s["competitor_outlets"]
    assert (master_deck.MAX_SLIDES, master_deck.MIN_IMAGES) == (s["max_slides"], s["min_images"])
    from app.pipeline import storage
    assert storage.BOOTSTRAP_LOOKBACK.total_seconds() == s["new_inbox_lookback_hours"] * 3600
