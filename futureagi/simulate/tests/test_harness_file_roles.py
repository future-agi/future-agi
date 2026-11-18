"""Each attached file's role: a policy, seed records, or evaluator-only, kept in the draft as
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


def test_a_role_path_is_read_as_the_harness_spells_it():
    """Surrounding spaces and a leading `./` are not part of the path, on both sides."""
    files = {
        harness_actor_rules.KNOWLEDGE_ROLES: json.dumps(
            {" knowledge/a.md ": "policy", "./knowledge/b.md": "evaluator_only"}
        ).encode()
    }

    assert harness_actor_rules.file_roles(files) == {
        "knowledge/a.md": "policy",
        "knowledge/b.md": "evaluator_only",
    }


def _key_cases():
    import json
    from pathlib import Path

    fixture = Path(__file__).parent / "fixtures" / "knowledge_role_keys.json"
    return [
        (one["key"], one["names"])
        for one in json.loads(fixture.read_text(encoding="utf-8"))["cases"]
    ]


@pytest.mark.parametrize("key, names", _key_cases())
def test_a_roles_key_names_the_file_alk_reads_it_as(key, names):
    """The shared fixture agent-learning-kit tests its reader against: both read every key the same way."""
    from simulate.services.harness_actor_rules import knowledge_role_key

    assert knowledge_role_key(key) == names


def test_a_dot_slash_evaluator_only_key_is_hidden_and_the_sealed_rule_is_the_one_applied():
    import json

    from simulate.services.harness_actor_rules import KNOWLEDGE_ROLE_KEYS, actor_rules

    files = {
        "knowledge/roles.json": json.dumps(
            {"./knowledge/answers.md": "evaluator_only"}
        ).encode(),
        "knowledge/answers.md": b"the answers",
    }
    assert "answers.md" in actor_rules(files)["evaluator_only"]["files"]
    # A release that sealed another rule is read by its rule, not the platform's copy.
    sealed = {**KNOWLEDGE_ROLE_KEYS, "drop_prefix": ""}
    files["actor_rules.json"] = json.dumps(
        {"knowledge_roles": sealed, "evaluator_only": {"files": []}}
    ).encode()
    assert "answers.md" not in actor_rules(files)["evaluator_only"]["files"]
