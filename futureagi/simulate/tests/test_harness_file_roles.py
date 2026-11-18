"""Each attached file's role (TH-8919): a policy, seed records, or evaluator-only, kept in the draft as
`knowledge/roles.json` for the harness to seal. An evaluator-only file is never offered to an actor."""

from __future__ import annotations

import json

import pytest

from simulate.services import harness_actor_rules
from simulate.services.harness_versioning import drafts
from simulate.tests.test_harness_actor_settings import _upload
from simulate.tests.test_harness_actors import BASE, MANAGER, _add, _built
from simulate.tests.test_harness_versioning_backfill import (  # noqa: F401 - fixture
    storage as storage,
)

pytestmark = pytest.mark.django_db
POLICY = "knowledge/refund policy.md"
ANSWERS = "knowledge/answers.md"


@pytest.fixture(autouse=True)
def versioning_on(settings):
    settings.HARNESS_VERSIONING_ENABLED = True
    settings.HARNESS_BUNDLE_CERTIFY_ENABLED = False


def _roles(environment):
    raw = drafts.edit_sides(environment)["environment"].get(
        harness_actor_rules.KNOWLEDGE_ROLES
    )
    return json.loads(raw) if raw else {}


def _set(auth_client, environment, path, role, **extra):
    return auth_client.post(
        f"{BASE}/{environment.id}/files/role/",
        {"path": path, "role": role, **extra},
        format="json",
    )


def test_an_evaluator_only_upload_is_kept_from_every_actor(
    user, workspace, storage, auth_client
):
    environment = _built(user, workspace, "roles-upload")

    upload = _upload(
        auth_client, environment, "answers.md", b"Refund 40.", role="evaluator_only"
    )

    assert upload.status_code == 200, upload.content
    sources = upload.json()["knowledge_sources"]
    assert ANSWERS not in sources["files"]
    assert sources["roles"] == {ANSWERS: "evaluator_only"}
    assert _roles(environment) == {ANSWERS: "evaluator_only"}
    # Giving it to an actor is refused, as any evaluator-only file is.
    reader = {
        **MANAGER,
        "knowledge": [{"kind": "files", "paths": [ANSWERS]}],
    }
    refused = _add(auth_client, environment, reader)
    assert refused.status_code == 400, refused.content


def test_a_role_set_after_attaching_is_kept_and_read_back(
    user, workspace, storage, auth_client
):
    environment = _built(user, workspace, "roles-set")
    assert (
        _upload(auth_client, environment, "refund policy.md", b"30 days").status_code
        == 200
    )

    policy = _set(auth_client, environment, POLICY, "policy")
    seed = _set(auth_client, environment, POLICY, "seed_records")

    assert policy.status_code == 200, policy.content
    assert seed.json()["knowledge_sources"]["roles"] == {POLICY: "seed_records"}
    assert POLICY in seed.json()["knowledge_sources"]["files"]
    assert _roles(environment) == {POLICY: "seed_records"}


def test_a_file_an_actor_reads_is_not_made_evaluator_only(
    user, workspace, storage, auth_client
):
    environment = _built(user, workspace, "roles-in-use")
    assert (
        _upload(auth_client, environment, "refund policy.md", b"30 days").status_code
        == 200
    )
    reader = {**MANAGER, "knowledge": [{"kind": "files", "paths": [POLICY]}]}
    assert _add(auth_client, environment, reader).status_code == 200

    refused = _set(auth_client, environment, POLICY, "evaluator_only")

    assert refused.status_code == 409, refused.content
    assert refused.json()["error"] == "file_in_use"
    assert refused.json()["readers"] == [MANAGER["name"]]
    assert _roles(environment) == {}


@pytest.mark.parametrize(
    "path", ["knowledge/missing.md", "contract.json", "knowledge/roles.json"]
)
def test_a_role_for_a_file_the_environment_does_not_attach_is_refused(
    user, workspace, storage, auth_client, path
):
    environment = _built(user, workspace, f"roles-missing-{len(path)}")

    refused = _set(auth_client, environment, path, "policy")

    assert refused.status_code == 404, refused.content
    assert refused.json()["error"] == "file_not_found"


def test_an_unknown_or_unreadable_roles_file_hides_or_ignores_as_the_harness_does():
    files = {
        harness_actor_rules.KNOWLEDGE_ROLES: json.dumps(
            {
                "knowledge/a.md": "secret_thing",
                "../x.md": "policy",
                "knowledge/b.md": "policy",
            }
        ).encode()
    }

    assert harness_actor_rules.file_roles(files) == {
        "knowledge/a.md": "evaluator_only",
        "knowledge/b.md": "policy",
    }
    assert (
        harness_actor_rules.file_roles({harness_actor_rules.KNOWLEDGE_ROLES: b"{no"})
        == {}
    )
    assert "a.md" in harness_actor_rules.actor_rules(files)["evaluator_only"]["files"]
