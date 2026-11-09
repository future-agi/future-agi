"""Grant fragments are removed at the diagnostic persistence boundary."""

import gzip
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from django.db import transaction
from simulate.services.hosted_harness_diagnostics import forget_attempt_redaction_values
from simulate.services.hosted_harness_gateway import HostedHarnessGateway

from accounts.services.service_grants import issue_person_grant

pytestmark = pytest.mark.django_db


def issued_attempt(user, workspace):
    attempt_id, agent_id, job_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    with transaction.atomic():
        token, claims, _ = issue_person_grant(
            kind="sandbox",
            org=user.organization_id,
            user=user.pk,
            workspace=workspace.pk,
            sub=f"attempt:{agent_id}:{attempt_id}",
            ttl=600,
        )
    attempt = SimpleNamespace(
        id=attempt_id,
        job_id=job_id,
        job=SimpleNamespace(organization_id=user.organization_id),
        attempt_number=1,
        provider_ref="synthetic-sandbox",
        snapshot_name="test-only",
        diagnostics_captured_at=None,
        save=MagicMock(),
    )
    return attempt, token, "fit_" + claims["id"]


def test_new_worker_redacts_all_fragments_before_storage(user, workspace):
    attempt, token, public_id = issued_attempt(user, workspace)
    fragments = [token, *token.split(".")[1:], public_id]
    output = "\n".join(fragments)
    sandbox = MagicMock()
    sandbox.process.get_session_command_logs.return_value = SimpleNamespace(
        output=output
    )
    sandbox.process.exec.return_value = SimpleNamespace(result=output)
    stored = []
    storage = MagicMock()
    storage.put_object.side_effect = lambda **kwargs: stored.append(
        gzip.decompress(kwargs["data"].getvalue()).decode()
    )
    forget_attempt_redaction_values(attempt.id)
    with (
        patch(
            "simulate.services.hosted_harness_gateway.PlatformSecretResolver"
        ) as resolver,
        patch(
            "simulate.services.hosted_harness_gateway.resolve_platform_simulator_secrets",
            return_value={},
        ),
        patch(
            "simulate.services.hosted_harness_diagnostics.get_storage_client",
            return_value=storage,
        ),
    ):
        resolver.return_value.resolve.return_value = {}
        result = HostedHarnessGateway._capture_diagnostics(
            attempt,
            sandbox,
            command_id="test-command",
            command=SimpleNamespace(status="done", exit_code=0),
            final=True,
        )
    assert result is not None and len(stored) == 1
    for fragment in fragments:
        assert fragment not in stored[0]
    assert "[REDACTED]" in stored[0]


def test_missing_derivation_key_persists_no_diagnostics(user, workspace, settings):
    attempt, _, _ = issued_attempt(user, workspace)
    settings.API_SECRET_PEPPER = ""
    sandbox = MagicMock()
    forget_attempt_redaction_values(attempt.id)
    with patch(
        "simulate.services.hosted_harness_diagnostics.get_storage_client"
    ) as storage:
        assert (
            HostedHarnessGateway._capture_diagnostics(attempt, sandbox, final=True)
            is None
        )
    storage.assert_not_called()
    sandbox.process.exec.assert_not_called()
    assert attempt.diagnostics_error.startswith("redaction:")
