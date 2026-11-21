"""Compare an environment version with its parent, in the rules and files the cases cite."""

import uuid
from unittest.mock import patch

import pytest

from simulate.models import HarnessEnvironmentVersion, HarnessSuiteMember
from simulate.services import harness_scenario_actions
from simulate.tests.test_harness_affected_scenarios import REFUND_KEYS, _suite, _ten
from simulate.tests.test_harness_readiness import _environment
from simulate.tests.test_harness_readiness import (  # noqa: F401 - fixture
    storage as storage,
)
from simulate.tests.test_harness_scenario_lifecycle import (
    _cite,
    _copy,
    _with_rules_and_files,
)

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("readiness_on")]
BASE = "/simulate/api/harness-environments"


def _changed_pair(environment):
    parent = _with_rules_and_files(
        environment.environment_version,
        ["Never refund after 14 days."],
        {"knowledge/p.md": "a"},
    )
    child = _copy(parent, HarnessEnvironmentVersion, parent=parent)
    _with_rules_and_files(
        child, ["Never refund after 30 days."], {"knowledge/p.md": "b"}
    )
    return parent, child


def test_changes_with_cited_by(user, workspace, storage, auth_client):
    environment = _environment(user, workspace, storage, "sc-api")
    _suite(environment, _ten())
    parent, child = _changed_pair(environment)
    _cite(
        environment,
        {
            REFUND_KEYS[0]: [
                {"kind": "rule", "ref": "rule:0", "quote": "Never refund after 14 days"}
            ],
            REFUND_KEYS[1]: [
                {"kind": "rule", "ref": "rule:0", "quote": "Never refund after 14 days"}
            ],
            REFUND_KEYS[2]: [{"kind": "file", "ref": "file:p.md", "quote": "14"}],
        },
    )
    texts = {parent.id: {"p.md": "old text"}, child.id: {"p.md": "new text"}}
    with patch.object(
        harness_scenario_actions,
        "_file_texts",
        lambda version, paths: texts[version.id],
    ):
        response = auth_client.get(
            f"{BASE}/{environment.id}/versions/environment/{child.id}/source-changes/"
        )

    assert response.status_code == 200, response.content
    body = response.json()
    assert (body["from_version"], body["to_version"]) == (
        parent.version_number,
        child.version_number,
    )
    by_ref = {(one["kind"], one["ref"]): one for one in body["changes"]}
    rule = by_ref[("rule", "rule:0")]
    # The cited rule is gone; no new rule is guessed in its place from how it reads.
    assert (rule["before"], rule["after"]) == ("Never refund after 14 days.", None)
    assert sorted(rule["cited_by"]) == sorted(REFUND_KEYS[:2])
    assert by_ref[("file", "file:p.md")]["before"] == "old text"
    assert body["truncated"] is False


def test_a_rewritten_rule_compares_with_the_rule_the_harness_recorded_in_its_place(
    user, workspace, storage, auth_client
):
    from simulate.services.harness_versioning.review_items import rule_key

    environment = _environment(user, workspace, storage, "sc-replaced")
    _suite(environment, _ten())
    parent, child = _changed_pair(environment)
    old, new = "Never refund after 14 days.", "Never refund after 30 days."
    documents = dict(child.documents)
    documents["contract"] = {
        **documents["contract"],
        "amendment_records": [
            {
                "kind": "rule",
                "key": f"rule-{rule_key(new)}",
                "action": "changed",
                "previous": f"rule-{rule_key(old)}",
                "text": "rule changed",
            }
        ],
    }
    HarnessEnvironmentVersion._base_manager.filter(id=child.id).update(
        documents=documents
    )
    _cite(
        environment,
        {
            REFUND_KEYS[0]: [
                {"kind": "rule", "ref": "rule:0", "quote": "Never refund after 14 days"}
            ]
        },
    )

    with patch.object(
        harness_scenario_actions, "_file_texts", lambda version, paths: {}
    ):
        body = auth_client.get(
            f"{BASE}/{environment.id}/versions/environment/{child.id}/source-changes/"
        ).json()

    rule = next(one for one in body["changes"] if one["kind"] == "rule")
    assert (rule["before"], rule["after"]) == (old, new)


def test_changes_are_capped(user, workspace, storage):
    environment = _environment(user, workspace, storage, "sc-cap")
    _suite(environment, _ten())
    parent, child = _changed_pair(environment)
    _with_rules_and_files(child, [], {f"knowledge/f{n}.md": "new" for n in range(205)})
    member = HarnessSuiteMember.no_workspace_objects.filter(
        suite_version=environment.suite_version
    ).first()
    member.content = {
        **(member.content or {}),
        "sources": [
            {"kind": "file", "ref": f"file:f{n}.md", "quote": "x"} for n in range(205)
        ],
    }
    member.save(update_fields=["content", "updated_at"])

    with patch.object(
        harness_scenario_actions, "_file_texts", lambda version, paths: {}
    ):
        body = harness_scenario_actions.source_changes(environment, child.id)

    assert len(body["changes"]) == 200 and body["truncated"] is True


def test_unknown_version_404(user, workspace, storage, auth_client):
    environment = _environment(user, workspace, storage, "sc-404")
    other = _environment(user, workspace, storage, "sc-404-other")
    for version_id in (uuid.uuid4(), other.environment_version_id):
        response = auth_client.get(
            f"{BASE}/{environment.id}/versions/environment/{version_id}/source-changes/"
        )
        assert (response.status_code, response.json()["error"]) == (
            404,
            "version_not_found",
        )


def test_missing_digests_compare_by_text(user, workspace, storage):
    from simulate.services import harness_scenario_sources
    from simulate.services.harness_scenario_lifecycle import changed_refs

    environment = _environment(user, workspace, storage, "sc-no-digests")
    _suite(environment, _ten())
    parent = environment.environment_version
    child = _copy(parent, HarnessEnvironmentVersion, parent=parent)
    for version in (parent, child):
        documents = dict(version.documents or {})
        documents.pop("file_digests", None)
        HarnessEnvironmentVersion._base_manager.filter(id=version.id).update(
            documents=documents
        )
        version.refresh_from_db()
    contents = {
        REFUND_KEYS[0]: {"sources": [{"kind": "file", "ref": "file:same.md"}]},
        REFUND_KEYS[1]: {"sources": [{"kind": "file", "ref": "file:edited.md"}]},
        REFUND_KEYS[2]: {"sources": [{"kind": "file", "ref": "file:gone.md"}]},
    }
    bodies = {
        parent.id: {"same.md": b"14 days", "edited.md": b"14 days", "gone.md": b"x"},
        child.id: {"same.md": b"14 days", "edited.md": b"30 days"},
    }
    with patch.object(
        harness_scenario_sources,
        "read_files",
        lambda version, paths: (
            {p: b for p, b in bodies[version.id].items() if p in paths},
            True,
        ),
    ):
        changed = changed_refs(child, contents)

    assert REFUND_KEYS[0] not in changed
    assert changed[REFUND_KEYS[1]] == ["file:edited.md"]
    assert changed[REFUND_KEYS[2]] == ["file:gone.md"]
