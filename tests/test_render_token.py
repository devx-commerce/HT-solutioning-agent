"""The renderer's identity token, where the metadata server won't issue one."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from agents.solutioning_agent.tools import deck as deck_tools

URL = "https://renderer.example.run.app"


def test_the_token_comes_from_the_metadata_server_when_it_can(monkeypatch):
    monkeypatch.setattr(deck_tools, "RENDER_URL", URL)
    with patch("google.oauth2.id_token.fetch_id_token", return_value="meta") as fetch:
        assert deck_tools._id_token_headers() == {"Authorization": "Bearer meta"}
    assert fetch.call_args.args[1] == URL


def test_inside_cloud_build_the_token_is_minted_for_the_same_account(monkeypatch):
    monkeypatch.setattr(deck_tools, "RENDER_URL", URL)
    source = MagicMock(service_account_email="runner@project.iam.gserviceaccount.com")
    id_creds = MagicMock(token="minted")
    with patch("google.oauth2.id_token.fetch_id_token", side_effect=Exception("please provide a user-specified service account")), \
         patch("google.auth.default", return_value=(source, "project")), \
         patch("google.auth.impersonated_credentials.Credentials") as signer, \
         patch("google.auth.impersonated_credentials.IDTokenCredentials", return_value=id_creds) as minted:
        assert deck_tools._id_token_headers() == {"Authorization": "Bearer minted"}
    assert signer.call_args.kwargs["target_principal"] == "runner@project.iam.gserviceaccount.com"
    assert minted.call_args.kwargs["target_audience"] == URL


def test_a_local_renderer_needs_no_token(monkeypatch):
    monkeypatch.setattr(deck_tools, "RENDER_URL", "http://localhost:8080")
    assert deck_tools._id_token_headers() == {}
