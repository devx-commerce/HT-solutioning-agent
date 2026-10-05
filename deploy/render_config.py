"""Check config.yaml, then write every service's settings from it.

    python -m deploy.render_config            # check, then write the files
    python -m deploy.render_config --check    # check only

Writes (all git-ignored, regenerated on every deploy):
    agents/solutioning_agent/.env   the agent's settings, shipped with it to Agent Engine
    deploy/out/pipeline.env.yaml    Cloud Run: the email pipeline
    deploy/out/onboarding.env.yaml  Cloud Run: the onboarding page
    deploy/out/deploy.env           names and ids for the deploy scripts

Every problem is reported at once, in plain words, and nothing is written
until the file is valid; a deploy stops here rather than half-applying a bad
setting.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "deploy" / "out"

_DOMAIN = re.compile(r"^[a-z0-9-]+(\.[a-z0-9-]+)+$")
_EMAIL = re.compile(r"^[^@\s]+@[a-z0-9-]+(\.[a-z0-9-]+)+$")
_DRIVE_ID = re.compile(r"^[-\w]{20,}$")
_CRON = re.compile(r"^(\S+\s+){4}\S+$")


def load(path: Path = ROOT / "config.yaml") -> dict:
    return yaml.safe_load(path.read_text())


def problems(cfg: dict) -> list[str]:
    """Everything wrong with the config, as sentences someone can act on."""
    out: list[str] = []
    s = (cfg or {}).get("settings") or {}
    i = (cfg or {}).get("infrastructure") or {}
    if not s or not i:
        return ["config.yaml must have both a 'settings' and an 'infrastructure' section."]

    def need(section, key, check, what):
        value = section.get(key)
        if value is None or value == "" or not check(value):
            out.append(f"{key}: {what} (found {value!r}).")

    need(s, "eval_summary_email", lambda v: bool(_EMAIL.match(str(v).lower())), "must be an email address")
    need(i, "agent_email", lambda v: bool(_EMAIL.match(str(v).lower())), "must be an email address")
    for key in ("onboarding_domains", "deck_reader_domains"):
        need(s, key, lambda v: isinstance(v, list) and v and all(_DOMAIN.match(str(d).lower()) for d in v),
             "must be a list of domains like hindustantimes.com, without @")
    need(s, "excluded_senders", lambda v: isinstance(v, list) and all(_EMAIL.match(str(a).lower()) for a in v),
         "must be a list of full email addresses (it may be empty: [])")
    need(s, "competitor_outlets",
         lambda v: isinstance(v, dict) and all(isinstance(d, list) and d and all(_DOMAIN.match(str(x).lower()) for x in d)
                                              for d in v.values()),
         "must map each publication's name to a list of its domains")
    need(s, "usage_report_schedule", lambda v: bool(_CRON.match(str(v))), "must be a cron schedule like \"0 9 * * 1\"")
    need(s, "sweep_schedule", lambda v: bool(_CRON.match(str(v))), "must be a cron schedule like \"*/30 * * * *\"")
    need(s, "new_inbox_lookback_hours", lambda v: type(v) is int and 0 <= v <= 168,
         "must be a whole number of hours from 0 to 168")
    need(s, "max_slides", lambda v: isinstance(v, int) and 7 <= v <= 30, "must be a whole number from 7 to 30")
    need(s, "min_images", lambda v: isinstance(v, int) and 0 <= v <= 10, "must be a whole number from 0 to 10")
    models = s.get("models") or {}
    for key in ("agent", "research", "classify", "images"):
        need(models, key, lambda v: str(v).startswith("gemini-"), f"models.{key} must be a Gemini model name")
    for key in ("deck_folder_id", "ht_assets_folder_id", "briefs_sheet_id"):
        need(s, key, lambda v: bool(_DRIVE_ID.match(str(v))), "must be a Drive or Sheets id, the long code in its URL")
    need(s, "past_decks_folder_ids", lambda v: isinstance(v, list) and v and all(_DRIVE_ID.match(str(x)) for x in v),
         "must be a list of Drive folder ids")
    evals = s.get("evals") or {}
    need(evals, "after_deploy", lambda v: v in ("smoke", "full", "none"), "evals.after_deploy must be smoke, full or none")
    need(evals, "smoke_cases", lambda v: isinstance(v, list) and v, "evals.smoke_cases must list at least one case")

    for key in ("project_id", "project_number", "region", "model_location", "image_location", "bigquery_dataset",
                "eval_dataset", "eval_deck_folder_id", "eval_results_bucket", "deck_images_bucket", "agent_engine_id",
                "past_decks_engine", "past_decks_location", "pubsub_topic", "scheduler_job"):
        need(i, key, lambda v: bool(str(v).strip()), "is required")
    need(i, "gemini_enterprise_agent_url",
         lambda v: str(v).startswith("https://vertexaisearch.cloud.google.com/") and "/r/agent/" in str(v),
         "must be the agent's Gemini Enterprise address, ending in /r/agent/<id>")
    need(i.get("services") or {}, "pipeline", bool, "services.pipeline is required")
    need(i.get("services") or {}, "onboarding", bool, "services.onboarding is required")
    need(i.get("services") or {}, "renderer", bool, "services.renderer is required")
    for key in ("agent_token", "oauth_client", "state_key", "youtube_key"):
        need(i.get("secrets") or {}, key, bool, f"secrets.{key} is required")
    return out


def _run_url(i: dict, service: str) -> str:
    """Cloud Run's stable per-service URL, known before the service is deployed."""
    return f"https://{service}-{i['project_number']}.{i['region']}.run.app"


def _secret_path(i: dict, name: str) -> str:
    return f"projects/{i['project_number']}/secrets/{name}/versions/latest"


def _secret_value(i: dict, name: str) -> str:
    from google.cloud import secretmanager

    client = secretmanager.SecretManagerServiceClient()
    path = f"projects/{i['project_id']}/secrets/{name}/versions/latest"
    return client.access_secret_version(name=path).payload.data.decode("utf-8").strip()


def agent_env(cfg: dict, youtube_key: str) -> dict[str, str]:
    s, i = cfg["settings"], cfg["infrastructure"]
    return {
        "GOOGLE_CLOUD_PROJECT": i["project_id"],
        "GOOGLE_CLOUD_LOCATION": i["region"],
        "GOOGLE_GENAI_USE_VERTEXAI": "TRUE",
        "MODEL_LOCATION": i["model_location"],
        "IMAGE_LOCATION": i["image_location"],
        "BQ_DATASET": i["bigquery_dataset"],
        "OAUTH_TOKEN_SECRET": _secret_path(i, i["secrets"]["agent_token"]),
        "GEMINI_MODEL": s["models"]["agent"],
        "RESEARCH_MODEL": s["models"]["research"],
        "IMAGE_MODEL": s["models"]["images"],
        "RENDER_URL": _run_url(i, i["services"]["renderer"]),
        "DECK_FOLDER_ID": s["deck_folder_id"],
        "DECK_READER_DOMAIN": ",".join(s["deck_reader_domains"]),
        "PAST_DECKS_FOLDER_ID": ",".join(s["past_decks_folder_ids"]),
        "PAST_DECKS_ENGINE": i["past_decks_engine"],
        "PAST_DECKS_LOCATION": i["past_decks_location"],
        "HT_ASSETS_FOLDER_ID": s["ht_assets_folder_id"],
        "COMPETITOR_OUTLETS": json.dumps(s["competitor_outlets"], ensure_ascii=False),
        "DECK_IMAGES_BUCKET": i["deck_images_bucket"],
        "MAX_SLIDES": str(s["max_slides"]),
        "MIN_IMAGES": str(s["min_images"]),
        "YOUTUBE_API_KEY": youtube_key,
    }


def pipeline_env(cfg: dict) -> dict[str, str]:
    s, i = cfg["settings"], cfg["infrastructure"]
    return {
        **_shared_service_env(cfg),
        "SERVICE_URL": _run_url(i, i["services"]["pipeline"]),
        "BRIEFS_SHEET_ID": s["briefs_sheet_id"],
        "CLASSIFY_MODEL": s["models"]["classify"],
        "BUILD_WORK_TOPIC": i["pubsub_topic"],
        "AGENT_EMAIL": i["agent_email"],
        "AGENT_ENGINE_RESOURCE":
            f"projects/{i['project_number']}/locations/{i['region']}/reasoningEngines/{i['agent_engine_id']}",
        "EXCLUDED_SENDERS": ",".join(s["excluded_senders"]),
        "NEW_INBOX_LOOKBACK_HOURS": str(s["new_inbox_lookback_hours"]),
        # Never one person's browser account (/u/1/): each reader opens it in their own default.
        "GE_AGENT_URL": re.sub(r"/u/\d+/", "/", i["gemini_enterprise_agent_url"].split("/session/")[0]).rstrip("/"),
    }


def onboarding_env(cfg: dict) -> dict[str, str]:
    i = cfg["infrastructure"]
    return {
        **_shared_service_env(cfg),
        "SERVICE_URL": _run_url(i, i["services"]["onboarding"]),
        "PUBLIC_ROUTES_ONLY": "true",
    }


def _shared_service_env(cfg: dict) -> dict[str, str]:
    s, i = cfg["settings"], cfg["infrastructure"]
    return {
        "GOOGLE_CLOUD_PROJECT": i["project_id"],
        "GOOGLE_CLOUD_LOCATION": i["region"],
        "BQ_DATASET": i["bigquery_dataset"],
        "OAUTH_TOKEN_SECRET": _secret_path(i, i["secrets"]["agent_token"]),
        "OAUTH_CLIENT_SECRET": _secret_path(i, i["secrets"]["oauth_client"]),
        "ALLOWED_ONBOARD_DOMAIN": ",".join(s["onboarding_domains"]),
    }


def deploy_env(cfg: dict) -> dict[str, str]:
    """Plain names and ids for deploy/*.sh and the Cloud Build steps."""
    s, i = cfg["settings"], cfg["infrastructure"]
    return {
        "PROJECT_ID": i["project_id"],
        "PROJECT_NUMBER": i["project_number"],
        "REGION": i["region"],
        "BQ_DATASET": i["bigquery_dataset"],
        "EVAL_DATASET": i["eval_dataset"],
        "EVAL_DECK_FOLDER_ID": i["eval_deck_folder_id"],
        "EVAL_RESULTS_BUCKET": i["eval_results_bucket"],
        "DECK_IMAGES_BUCKET": i["deck_images_bucket"],
        "AGENT_ENGINE_ID": i["agent_engine_id"],
        "PIPELINE_SERVICE": i["services"]["pipeline"],
        "ONBOARDING_SERVICE": i["services"]["onboarding"],
        "RENDERER_SERVICE": i["services"]["renderer"],
        "RENDER_URL": _run_url(i, i["services"]["renderer"]),
        "PIPELINE_URL": _run_url(i, i["services"]["pipeline"]),
        "PUBSUB_TOPIC": i["pubsub_topic"],
        "SCHEDULER_JOB": i["scheduler_job"],
        "SWEEP_SCHEDULE": s["sweep_schedule"],
        "STATE_KEY_SECRET": i["secrets"]["state_key"],
        "SECRET_NAMES": " ".join(i["secrets"].values()),
        "EVAL_SUMMARY_EMAIL": s["eval_summary_email"],
        "USAGE_REPORT_SCHEDULE": s["usage_report_schedule"],
        "EVALS_AFTER_DEPLOY": s["evals"]["after_deploy"],
        "SMOKE_CASES": ",".join(s["evals"]["smoke_cases"]),
    }


def _write_env_file(path: Path, values: dict[str, str]) -> None:
    path.write_text("".join(f"{k}={v}\n" for k, v in values.items()))


def _write_yaml(path: Path, values: dict[str, str]) -> None:
    path.write_text(yaml.safe_dump(values, default_flow_style=False, allow_unicode=True))


def main() -> None:
    cfg = load()
    found = problems(cfg)
    if found:
        print("config.yaml has problems; nothing was deployed:\n" + "\n".join(f"  - {p}" for p in found))
        sys.exit(1)
    if "--check" in sys.argv:
        print("config.yaml is valid.")
        return
    i = cfg["infrastructure"]
    OUT.mkdir(parents=True, exist_ok=True)
    _write_env_file(ROOT / "agents" / "solutioning_agent" / ".env",
                    agent_env(cfg, _secret_value(i, i["secrets"]["youtube_key"])))
    _write_yaml(OUT / "pipeline.env.yaml", pipeline_env(cfg))
    _write_yaml(OUT / "onboarding.env.yaml", onboarding_env(cfg))
    _write_env_file(OUT / "deploy.env", {k: f'"{v}"' for k, v in deploy_env(cfg).items()})
    print("config.yaml is valid; settings written for the agent, both Cloud Run services and the deploy.")


if __name__ == "__main__":
    main()
