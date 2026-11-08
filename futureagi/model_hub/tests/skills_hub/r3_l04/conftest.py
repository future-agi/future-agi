"""Synthetic workspace fixtures for L4 isolation tests."""

from uuid import uuid4

import pytest

from accounts.models.workspace import Workspace


@pytest.fixture
def native_configuration():
    from simulate.services.skills_context_adapter import validate_native_configuration

    digest = "sha256:" + "a" * 64
    return validate_native_configuration(
        {
            "code_commit": "a" * 40,
            "code_digest": digest,
            "entrypoint": "synthetic:run",
            "image_digest": digest,
            "runtime_version": "python-3.11.11",
            "packages": [
                {"name": "synthetic-app", "version": "1.0.0", "sha256": "b" * 64}
            ],
            "package_lock_digest": digest,
            "model": {
                "model_id": "synthetic-model",
                "temperature": 0,
                "max_output_tokens": 1024,
            },
            "connection_id": str(uuid4()),
            "connection_revision": "1",
            "tools_digest": digest,
            "retrieval_fixture_digest": digest,
            "state_schema": "synthetic-checkpoint-v1",
            "checkpoint_digest": digest,
            "side_effect_policy": "fixture_only",
            "reset_entrypoint": "synthetic:reset",
            "context_mount": "/fi_context/",
            "observable_boundaries": [
                "model_request",
                "root_delivery",
                "checkpoint",
                "child_selection",
            ],
        }
    )


@pytest.fixture
def other_workspace(organization, user):
    return Workspace.no_workspace_objects.create(
        organization=organization, name="Other synthetic workspace", created_by=user
    )
