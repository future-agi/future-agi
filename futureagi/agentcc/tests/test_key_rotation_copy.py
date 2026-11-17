"""Exact activated response copy; gateway effects are synthetic fixtures only."""

import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from django.db import DatabaseError, transaction

from accounts.models.audit_log import AuditLog
from agentcc.tests.test_key_rotation_commit_boundary import request_fixture
from agentcc.tests.test_key_rotation_ownership import KEYS, isolated_gateway

pytestmark = pytest.mark.django_db(transaction=True)
ROWS = json.loads(
    Path(__file__).with_name("key_rotation_activation.json").read_text()
)["rows"]


def assert_exact(actual, row_id):
    row = ROWS[row_id]
    assert hashlib.sha256(row["afterExact"].encode()).hexdigest() == row["afterExact_sha256"]
    assert actual.encode("utf-8") == row["renderedExact"].encode("utf-8")


def test_rotation_refusal_matches_activated_keep_row(
    organization, workspace, isolated_gateway
):
    import uuid

    actor, old, client, payload = request_fixture(organization, workspace)
    response = client.post(
        KEYS, {**payload, "rotates_key_id": str(uuid.uuid4())}, format="json"
    )
    assert response.status_code == 403
    assert_exact(response.data["result"], "KEYS-ROTATE-3")


@pytest.mark.parametrize("revoke", [False, True])
def test_required_audit_refusal_matches_each_activated_keep_row(
    organization, workspace, isolated_gateway, revoke
):
    actor, old, client, payload = request_fixture(organization, workspace)
    with patch.object(AuditLog, "save", side_effect=DatabaseError("Fixture audit refusal")):
        response = client.post(
            f"{KEYS}{old.pk}/revoke/" if revoke else KEYS,
            {} if revoke else payload,
            format="json",
        )
    assert response.status_code == 503
    assert_exact(response.data["result"], "KEYS-ROTATE-6" if revoke else "KEYS-ROTATE-4")


@pytest.mark.parametrize("combined", [False, True])
@pytest.mark.parametrize("pending", ["ambient", "gateway-failure"])
def test_pending_warning_matches_the_two_joined_activated_fragments(
    organization, workspace, isolated_gateway, combined, pending
):
    from contextlib import nullcontext

    actor, old, client, payload = request_fixture(organization, workspace)
    if pending == "gateway-failure":
        isolated_gateway.revoke_key_confirmed.side_effect = RuntimeError("Fixture push failure")
    with transaction.atomic() if pending == "ambient" else nullcontext():
        response = client.post(
            KEYS if combined else f"{KEYS}{old.pk}/revoke/",
            {**payload, "revoke_rotated_key": True} if combined else {},
            format="json",
        )
        if pending == "ambient":
            assert response.status_code == 503
            assert_exact(response.data["result"], "KEYS-ROTATE-4" if combined else "KEYS-ROTATE-6")
            isolated_gateway.import_committed_key.assert_not_called()
            isolated_gateway.revoke_key_confirmed.assert_not_called()
            return
        assert response.status_code == (201 if combined else 200)
        result = response.data["result"]
        if combined:
            result = result["rotation"]
        assert result["gateway_revocation_confirmed"] is False
        assert_exact(result["warning"], "KEYS-ROTATE-5")
        assert "Fixture push failure" not in str(response.data)


def test_command_guard_matches_activated_row_14(organization, workspace, isolated_gateway):
    _, _, client, _ = request_fixture(organization, workspace)
    response = client.post(KEYS, {"name": "fixture", "revoke_rotated_key": True}, format="json")
    assert response.status_code == 403
    assert_exact(response.data["result"], "KEYS-ROTATE-14")
