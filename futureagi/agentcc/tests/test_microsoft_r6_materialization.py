"""R6 direct materialization REDs; real custody and authority, synthetic values.

No authority gate is replaced; SQL exclusions have negative controls.
"""
from datetime import timedelta
from unittest.mock import Mock

import pytest
from django.utils import timezone

from accounts.models.auth_token import AuthToken, AuthTokenType
from agentcc.tests import test_microsoft_connect_gate as gate
from agentcc.tests.test_microsoft_identity_exclusion import FILE_ID
from agentcc.tests.test_microsoft_secret_chokepoint import identities, url_server

pytestmark = pytest.mark.django_db
rig = gate.rig


@pytest.mark.parametrize("client_id", [gate.PLATFORM_ID, FILE_ID])
@pytest.mark.parametrize("supplied_access", [False, True])
def test_direct_served_refuses_platform_row_before_any_materialization(
    rig, identities, monkeypatch, client_id, supplied_access,
):
    org, user, config, fake = rig
    url_server(config)
    row = gate.token_row(org, user, server="alias")
    row.oauth_client_id = client_id
    row.save(update_fields=["oauth_client_id"])
    selected = gate.Credential.objects.defer("encrypted_secret", "encrypted_refresh").get(pk=row.pk)
    deferred = selected.get_deferred_fields()
    assert {"encrypted_secret", "encrypted_refresh"} <= deferred
    access = gate.oauth.Sealed("fixture-already-sealed-access") if supplied_access else None
    with gate.no_effects(monkeypatch, fake):
        answer = gate.tokens._served(
            selected, "refreshed" if supplied_access else "ok",
            timezone.now() + timedelta(hours=1), access=access,
        )
        assert answer.outcome == "needs_reauth"
        assert answer.access is None
        assert "access_token" not in answer.body()
        assert selected.get_deferred_fields() == deferred
    row.refresh_from_db()
    assert row.last_used_at is None
    assert row.status == "active" and row.version == 1


@pytest.mark.parametrize("supplied_access", [False, True])
def test_direct_served_non_microsoft_control_still_serves(
    rig, identities, monkeypatch, supplied_access,
):
    org, user, _, fake = rig
    row = gate.token_row(org, user, server="gmail")
    row.oauth_client_id = "fixture-distinct-google-client"
    row.save(update_fields=["oauth_client_id"])
    decrypt = Mock(wraps=gate.tokens.decrypt_token)
    monkeypatch.setattr(gate.tokens, "decrypt_token", decrypt)
    expires = timezone.now() + timedelta(hours=1)
    access = gate.oauth.Sealed("fixture-refreshed-access") if supplied_access else None
    before = len(fake.requests)
    answer = gate.tokens._served(
        row, "refreshed" if supplied_access else "ok", expires, access=access,
    )
    assert answer.outcome == ("refreshed" if supplied_access else "ok")
    assert answer.body()["access_token"] == (
        "fixture-refreshed-access" if supplied_access else "fixture-access"
    )
    assert answer.expires_at == expires and answer.version == row.version
    assert decrypt.call_count == (0 if supplied_access else 1)
    assert len(fake.requests) == before
    row.refresh_from_db()
    assert row.last_used_at is not None
    assert row.status == "active" and row.version == 1


def test_permitted_shared_row_still_requires_config_reference(rig, identities, monkeypatch):
    org, user, _, _ = rig
    row = gate.token_row(org, user, server="gmail", shared=True)
    row.oauth_client_id = "fixture-distinct-google-client"
    row.save(update_fields=["oauth_client_id"])
    decrypt = Mock(wraps=gate.tokens.decrypt_token)
    monkeypatch.setattr(gate.tokens, "decrypt_token", decrypt)
    answer = gate.tokens.shared_token(str(org.pk), "gmail")
    assert answer.outcome == "no_credential"
    assert answer.access is None
    decrypt.assert_not_called()
    row.refresh_from_db()
    assert row.last_used_at is None


def test_no_effects_accepts_only_fixture_session_insert_and_authenticated_touch(rig, monkeypatch):
    _, user, _, fake = rig
    with gate.no_effects(monkeypatch, fake):
        browser = gate.bearer(user)
        response = browser.post("/agentcc/connectors/oauth-clients/", {
            "client_group": "microsoft", "client_id": "fixture-new-client",
            "client_secret": gate.SECRET,
        }, format="json")
        assert response.status_code == 409
        assert response.json()["code"] == gate.CODE


@pytest.mark.parametrize("operation", ["insert", "touch", "other-column", "delete"])
def test_no_effects_captures_other_session_writes(rig, monkeypatch, operation):
    _, user, _, fake = rig
    token = AuthToken.objects.create(user=user, auth_type=AuthTokenType.ACCESS,
                                    last_used_at=timezone.now())
    with pytest.raises(AssertionError):
        with gate.no_effects(monkeypatch, fake):
            if operation == "insert":
                AuthToken.objects.create(user=user, auth_type=AuthTokenType.ACCESS,
                                         last_used_at=timezone.now())
            elif operation == "touch":
                # Identical columns outside bearer authentication are not exempt.
                AuthToken.objects.filter(pk=token.pk).update(last_used_at=timezone.now())
            elif operation == "other-column":
                AuthToken.objects.filter(pk=token.pk).update(
                    last_used_at=timezone.now(), is_active=False,
                )
            else:
                AuthToken.objects.filter(pk=token.pk).delete()


@pytest.mark.parametrize("field", ["last_used_at", "encrypted_secret", "encrypted_refresh"])
def test_no_effects_never_exempts_custody_table_writes(rig, monkeypatch, field):
    org, user, _, fake = rig
    row = gate.token_row(org, user)
    value = timezone.now() if field == "last_used_at" else "fixture-unused-ciphertext"
    with pytest.raises(AssertionError):
        with gate.no_effects(monkeypatch, fake):
            gate.Credential.objects.filter(pk=row.pk).update(**{field: value})
