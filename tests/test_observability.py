"""Log lines, per-inbox sweep times, and when an inbox is marked for reconnecting."""

from __future__ import annotations

import importlib
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from google.auth.exceptions import RefreshError

from app import logs
from app.pipeline import storage


def test_an_event_is_one_json_line_cloud_logging_can_read(capsys):
    logs.event("brief.queued", client="Decathlon", subject="Brief:\nback to campus", dry_run=None)
    line = json.loads(capsys.readouterr().out)
    assert line["severity"] == "INFO" and line["event"] == "brief.queued"
    assert line["message"] == "brief.queued client=Decathlon subject=Brief: back to campus"
    assert "dry_run" not in line


def test_a_long_field_is_shortened_only_in_the_summary(capsys):
    logs.event("build.failed", "ERROR", agent_reply="x" * 300)
    line = json.loads(capsys.readouterr().out)
    assert len(line["agent_reply"]) == 300 and len(line["message"]) < 100


def _rows(*times):
    client = MagicMock()
    client.query.return_value.result.return_value = [{"last_swept_at": t} for t in times]
    return patch.object(storage, "_client", return_value=client), client


def test_an_inbox_reads_from_its_own_last_sweep():
    last = datetime(2026, 10, 5, 4, 15, tzinfo=timezone.utc)
    patched, client = _rows(last)
    with patched:
        assert storage.get_sweep_cutoff("a@hindustantimes.com") == last - storage.WATERMARK_OVERLAP
    param = client.query.call_args.kwargs["job_config"].query_parameters[0]
    assert param.value == "a@hindustantimes.com"


def test_a_new_inbox_is_read_back_the_configured_lookback():
    patched, _ = _rows()
    with patched:
        cutoff = storage.get_sweep_cutoff("new@htdigital.in")
    expected = datetime.now(timezone.utc) - storage.BOOTSTRAP_LOOKBACK
    assert abs((cutoff - expected).total_seconds()) < 5


@pytest.fixture
def main(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    import app.main as module
    return importlib.reload(module)


def _sweep(main, gmail_side_effect=None, sweep_side_effect=None):
    user = {"email": "a@hindustantimes.com", "gmail_secret": "s"}
    with patch.object(main, "active_users", return_value=[user]), \
         patch.object(main, "get_service_for_user", side_effect=gmail_side_effect), \
         patch.object(main.ingestion, "run_sweep_for_user", side_effect=sweep_side_effect), \
         patch.object(main, "mark_reauthorization_required") as mark, \
         patch.object(main, "send_reauth_prompt") as prompt:
        result = main.sweep()
    return result, mark, prompt


def test_a_refused_login_asks_the_person_to_reconnect(main):
    result, mark, prompt = _sweep(main, gmail_side_effect=RefreshError("invalid_grant"))
    mark.assert_called_once_with("a@hindustantimes.com")
    prompt.assert_called_once()
    assert result["results"][0]["error"] == "reauthorization_required"


def test_any_other_failure_is_retried_without_disconnecting_the_inbox(main):
    result, mark, prompt = _sweep(main, sweep_side_effect=RuntimeError("429 RESOURCE_EXHAUSTED"))
    mark.assert_not_called()
    prompt.assert_not_called()
    assert "retried" in result["results"][0]["error"]
