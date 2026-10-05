"""Who may connect an inbox: any domain in ALLOWED_ONBOARD_DOMAIN, nobody else."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.auth import gmail_oauth


def _callback(hd: str | None, domains: set[str]):
    claims = {"email": f"am@{hd or 'gmail.com'}", "email_verified": True}
    if hd:
        claims["hd"] = hd
    resp = MagicMock(status_code=200)
    resp.json.return_value = {"refresh_token": "r", "id_token": "t"}
    with patch.object(gmail_oauth, "ALLOWED_DOMAINS", domains), \
         patch.object(gmail_oauth, "_verify_state", return_value="https://x/cb"), \
         patch.object(gmail_oauth, "_client_material", return_value={"client_id": "c", "client_secret": "s"}), \
         patch.object(gmail_oauth.requests, "post", return_value=resp), \
         patch.object(gmail_oauth.google_id_token, "verify_oauth2_token", return_value=claims), \
         patch.object(gmail_oauth, "_store_refresh_token") as store, \
         patch.object(gmail_oauth, "_upsert_user"):
        return gmail_oauth.handle_callback("code", "state"), store


HT = {"hindustantimes.com", "htdigital.in"}


@pytest.mark.parametrize("hd", ["hindustantimes.com", "htdigital.in", "HTDigital.in"])
def test_every_ht_domain_can_connect(hd):
    result, store = _callback(hd, HT)
    assert result["email"].endswith(hd)
    store.assert_called_once()


@pytest.mark.parametrize("hd", ["gmail.com", None, "livehindustan.com", "hindustantimes.com.evil.example"])
def test_other_accounts_are_refused_with_the_allowed_domains_named(hd):
    with pytest.raises(ValueError, match="@htdigital.in"):
        _callback(hd, HT)


def _secret_client(bindings, set_error=None):
    client = MagicMock()
    client.get_iam_policy.return_value.bindings = bindings
    if set_error:
        client.set_iam_policy.side_effect = set_error
    return client


def _binding(role, member):
    b = MagicMock(role=role, members=[member])
    return b


@patch.object(gmail_oauth.requests, "get")
def test_a_failed_access_grant_never_fails_the_connection(get):
    get.return_value.text = "runner@p.iam.gserviceaccount.com"
    client = _secret_client(MagicMock(), set_error=PermissionError("setIamPolicy denied"))
    gmail_oauth._grant_pipeline_access(client, "projects/p/secrets/gmail-x")  # does not raise


@patch.object(gmail_oauth.requests, "get")
def test_an_existing_grant_is_left_alone(get):
    get.return_value.text = "runner@p.iam.gserviceaccount.com"
    client = _secret_client([_binding("roles/secretmanager.secretAccessor", "serviceAccount:runner@p.iam.gserviceaccount.com")])
    gmail_oauth._grant_pipeline_access(client, "projects/p/secrets/gmail-x")
    client.set_iam_policy.assert_not_called()
