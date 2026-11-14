"""Where a case's requirements come from, resolved on the environment and version in view."""

import uuid
from unittest.mock import patch

import pytest

from simulate.models import HarnessEnvironmentVersion, HostedHarnessConversationMessage
from simulate.services.harness_scenario_sources import (
    FILE_CHANGED,
    FILE_UNCHECKED,
    resolve,
)
from simulate.services.hosted_harness_conversation import enqueue_message
from simulate.tests.test_harness_readiness import _environment
from simulate.tests.test_harness_readiness import (  # noqa: F401 - fixture
    readiness_on as readiness_on,
)
from simulate.tests.test_harness_readiness import (  # noqa: F401 - fixture
    storage as storage,
)

pytestmark = pytest.mark.django_db


def _message(environment):
    enqueue_message(
        environment,
        content="refunds only within 14 days",
        client_request_id=f"src-{environment.id}",
    )
    return HostedHarnessConversationMessage.no_workspace_objects.filter(
        conversation__job=environment
    ).first()


def _one(environment, version, source, stored=None):
    return resolve(environment, version, {"k": [source]}, stored={"k": stored or []})[
        "k"
    ][0]


def test_chat_message_resolves_on_its_own_conversation_only(user, workspace, storage):
    mine = _environment(user, workspace, storage, "src-mine")
    other = _environment(user, workspace, storage, "src-other")
    message = _message(other)
    source = {"kind": "chat_message", "ref": f"chat:{message.id}"}

    assert _one(other, other.environment_version, source)["resolved"] is True
    assert _one(mine, mine.environment_version, source)["resolved"] is False


def test_evidence_case_is_unresolved_until_imports_exist(user, workspace, storage):
    environment = _environment(user, workspace, storage, "src-evidence")
    view = _one(
        environment,
        environment.environment_version,
        {"kind": "evidence_case", "ref": f"evidence:{uuid.uuid4()}"},
    )
    assert (view["resolved"], view["problem"]) == (
        False,
        "evidence imports not available",
    )


def test_rule_removed_in_a_later_version_is_unresolved(user, workspace, storage):
    environment = _environment(user, workspace, storage, "src-rule")
    version = environment.environment_version
    documents = dict(version.documents or {})
    documents["contract"] = {
        **(documents.get("contract") or {}),
        "hard_constraints": ["Refund at most once per order."],
    }
    HarnessEnvironmentVersion._base_manager.filter(id=version.id).update(
        documents=documents
    )
    version.refresh_from_db()
    source = {"kind": "rule", "ref": "rule:0", "quote": "at most once"}
    assert _one(environment, version, source)["resolved"] is True

    documents["contract"] = {
        **documents["contract"],
        "hard_constraints": ["Refund any order."],
    }
    HarnessEnvironmentVersion._base_manager.filter(id=version.id).update(
        documents=documents
    )
    version.refresh_from_db()
    assert _one(environment, version, source)["resolved"] is False


def test_list_reads_no_archive_for_file_sources(user, workspace, storage):
    environment = _environment(user, workspace, storage, "src-file")
    version = environment.environment_version
    documents = dict(version.documents or {})
    documents["file_digests"] = {"knowledge/policy.md": "abc123"}
    HarnessEnvironmentVersion._base_manager.filter(id=version.id).update(
        documents=documents
    )
    version.refresh_from_db()
    source = {"kind": "file", "ref": "file:policy.md", "quote": "14 days"}
    stored = [{"ref": "file:policy.md", "resolved": True, "matched_digest": "abc123"}]

    with patch(
        "simulate.services.harness_versioning.archives.stream_members",
        side_effect=AssertionError("the list must not read an archive"),
    ):
        assert _one(environment, version, source, stored)["resolved"] is True
        documents["file_digests"] = {"knowledge/policy.md": "def456"}
        HarnessEnvironmentVersion._base_manager.filter(id=version.id).update(
            documents=documents
        )
        version.refresh_from_db()
        changed = _one(environment, version, source, stored)

    assert (changed["resolved"], changed["problem"]) == (False, FILE_CHANGED)


def test_a_file_never_checked_reads_unchecked_not_changed(user, workspace, storage):
    environment = _environment(user, workspace, storage, "src-unchecked")
    version = environment.environment_version
    documents = dict(version.documents or {})
    documents["file_digests"] = {"knowledge/policy.md": "abc123"}
    HarnessEnvironmentVersion._base_manager.filter(id=version.id).update(
        documents=documents
    )
    version.refresh_from_db()
    source = {"kind": "file", "ref": "file:policy.md", "quote": "14 days"}

    unchecked = _one(environment, version, source)

    assert (unchecked["resolved"], unchecked["problem"]) == (False, FILE_UNCHECKED)


def test_requested_line_without_a_citing_case_is_uncovered(user, workspace, storage):
    from simulate.models import HarnessSuiteMember
    from simulate.services.harness_scenario_sources import requested_uncovered
    from simulate.tests.test_harness_affected_scenarios import REFUND_KEYS, _suite, _ten

    environment = _environment(user, workspace, storage, "src-requested")
    _suite(environment, _ten())
    enqueue_message(
        environment,
        content=(
            "Add one scenario for each requested case below.\n"
            "Requested case 1: A refund asked for after 30 days\n"
            "Requested case 2: A second refund on the same order"
        ),
        client_request_id=f"req-{environment.id}",
    )
    member = HarnessSuiteMember.no_workspace_objects.get(
        suite_version=environment.suite_version, scenario_key=REFUND_KEYS[0]
    )
    # Cited by its words, not by the message: case and punctuation do not matter.
    member.content = {
        **(member.content or {}),
        "sources": [
            {
                "kind": "requirement",
                "ref": "",
                "quote": "a refund asked for after 30 days.",
            }
        ],
    }
    member.save(update_fields=["content"])

    assert requested_uncovered(environment) == ["A second refund on the same order"]


def test_records_endpoint_reads_seed_rows_only(user, workspace, storage, auth_client):
    import json

    from simulate.services import harness_scenario_sources
    from simulate.tests.test_harness_affected_scenarios import REFUND_KEYS, _suite, _ten

    environment = _environment(user, workspace, storage, "src-records")
    _suite(environment, _ten())
    seeded = {
        "refills": {"RX-1001": {"drug": "statin"}, "RX-1002": {"drug": "insulin"}}
    }
    read: list[set[str]] = []

    def bodies(version, paths):
        read.append(set(paths))
        return {"state.json": json.dumps(seeded).encode()}

    with (
        patch.object(harness_scenario_sources, "file_bodies", bodies),
        patch("simulate.views.harness_environment.version_reads.require_versioning"),
    ):
        response = auth_client.get(
            f"/simulate/api/harness-environments/{environment.id}/scenarios/{REFUND_KEYS[0]}/records/",
            {"table": "refills"},
        )

    assert response.status_code == 200, response.content
    body = response.json()
    assert body["available"] is True
    assert [row["id"] for row in body["rows"]] == ["RX-1001", "RX-1002"]
    # The stored archive's seed is all it reads.
    assert read == [{"state.json"}]
    with patch.object(
        harness_scenario_sources, "file_bodies", lambda version, paths: {}
    ):
        assert harness_scenario_sources.seed_records(environment, "refills") == {
            "table": "refills",
            "rows": [],
            "available": False,
        }
