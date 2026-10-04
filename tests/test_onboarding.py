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


HT = {"hindustantimes.com", "htdigital.in", "livehindustan.com"}


@pytest.mark.parametrize("hd", ["hindustantimes.com", "htdigital.in", "LiveHindustan.com"])
def test_every_ht_domain_can_connect(hd):
    result, store = _callback(hd, HT)
    assert result["email"].endswith(hd)
    store.assert_called_once()


@pytest.mark.parametrize("hd", ["gmail.com", None, "hindustantimes.com.evil.example"])
def test_other_accounts_are_refused_with_the_allowed_domains_named(hd):
    with pytest.raises(ValueError, match="@htdigital.in"):
        _callback(hd, HT)
