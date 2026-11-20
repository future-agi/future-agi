"""Each case's lifecycle on a pair of versions, derived from its proof, its suite and archiving."""

import pytest
from django.core.cache import cache
from django.utils import timezone

from simulate.models import (
    HarnessEnvironmentVersion,
    HarnessSuiteMember,
    HarnessSuiteValidation,
    HarnessSuiteVersion,
    HostedHarnessScenario,
)
from simulate.services.harness_scenario_lifecycle import scenario_lifecycle
from simulate.services.harness_versioning.versions import record_proofs
from simulate.tests.test_harness_affected_scenarios import (
    LOOKUP_KEYS,
    PROVED,
    REFUND_KEYS,
    _suite,
    _ten,
)
from simulate.tests.test_harness_readiness import _environment
from simulate.tests.test_harness_readiness import (  # noqa: F401 - fixture
    storage as storage,
)

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("readiness_on")]
KEYS = REFUND_KEYS + LOOKUP_KEYS


def _proofs(environment, proofs, *, suite=None, version=None):
    HarnessSuiteValidation.no_workspace_objects.update_or_create(
        suite_version=suite or environment.suite_version,
        environment_version=version or environment.environment_version,
        defaults={"status": "partial", "proved_at": timezone.now(), "proofs": proofs},
    )
    cache.clear()


def _views(environment, *, suite=None, version=None):
    return scenario_lifecycle(
        environment,
        suite_version=suite or environment.suite_version,
        environment_version=version or environment.environment_version,
    )


def _copy(version, model, **changes):
    """Another version of the same piece, as Save or a rebuild would make."""
    fields = {
        field.name: getattr(version, field.name)
        for field in model._meta.concrete_fields
        if field.name not in ("id", "created_at", "updated_at")
    }
    fields.update(
        version_number=(version.version_number or 0) + 100,
        hash=version.hash[:-4] + "next",
    )
    fields.update(changes)
    return model._base_manager.create(**fields)


def _members_of(source, target, *, changed=None):
    for member in HarnessSuiteMember.no_workspace_objects.filter(suite_version=source):
        content = dict(member.content or {})
        if member.scenario_key == changed:
            content["instruction"] = "a new instruction"
        HarnessSuiteMember.no_workspace_objects.create(
            suite_version=target,
            scenario_id=member.scenario_id,
            scenario_key=member.scenario_key,
            number=member.number,
            content=content,
        )


def test_a_save_after_a_candidate_validation_keeps_every_unchanged_cases_proof(
    user, workspace, storage
):
    """A validated candidate proves only its changed case, and later than the default suite's validation: every other
    case still carries its proof into the saved suite, so none reads not validated after the save."""
    from datetime import timedelta

    from simulate.services.harness_versioning.versions import _carried_proofs

    environment = _environment(user, workspace, storage, "lc-carry-candidate")
    _suite(environment, _ten())
    _proofs(environment, dict.fromkeys(KEYS, PROVED))
    HarnessSuiteValidation.no_workspace_objects.filter(
        suite_version=environment.suite_version
    ).update(proved_at=timezone.now() - timedelta(minutes=5))
    candidate = _copy(
        environment.suite_version, HarnessSuiteVersion, status="candidate"
    )
    _members_of(environment.suite_version, candidate, changed=REFUND_KEYS[0])
    _proofs(environment, {REFUND_KEYS[0]: PROVED}, suite=candidate)
    paired = [
        (member.scenario, member.content)
        for member in HarnessSuiteMember.no_workspace_objects.filter(
            suite_version=candidate
        ).select_related("scenario")
    ]

    carried = _carried_proofs(environment.environment_version, paired)

    assert sorted(carried) == sorted(KEYS)


def test_a_case_keeps_the_default_agents_proof_and_only_the_last_three_others():
    from simulate.services.harness_versioning.versions import OTHER_AGENTS, other_agents

    previous = {
        "proved": True,
        "agent_version_id": "a5",
        OTHER_AGENTS: {
            one: {"agent_version_id": one} for one in ("a1", "a2", "a3", "a4")
        },
    }

    kept = other_agents(previous, {"agent_version_id": "a6"}, default="a1")[
        OTHER_AGENTS
    ]

    assert list(kept) == ["a1", "a3", "a4", "a5"]


def test_an_older_complete_proof_is_carried_past_a_newer_unreached_one(
    user, workspace, storage
):
    from datetime import timedelta

    from simulate.services.harness_versioning.versions import _carried_proofs

    environment = _environment(user, workspace, storage, "lc-carry-unreached")
    _suite(environment, _ten())
    _proofs(environment, dict.fromkeys(KEYS, PROVED))
    HarnessSuiteValidation.no_workspace_objects.filter(
        suite_version=environment.suite_version
    ).update(proved_at=timezone.now() - timedelta(minutes=5))
    candidate = _copy(
        environment.suite_version, HarnessSuiteVersion, status="candidate"
    )
    _members_of(environment.suite_version, candidate)
    unreached = {
        "format": 2,
        "proved": False,
        "blocked_reason": "validation_incomplete",
    }
    _proofs(environment, dict.fromkeys(KEYS, unreached), suite=candidate)
    paired = [
        (member.scenario, member.content)
        for member in HarnessSuiteMember.no_workspace_objects.filter(
            suite_version=candidate
        ).select_related("scenario")
    ]

    carried = _carried_proofs(environment.environment_version, paired)

    assert carried == dict.fromkeys(KEYS, PROVED)


def _removed_case(user, workspace, storage, name):
    """S1 held every case; S2, now the default, dropped CASE, and its row is removed."""
    from simulate.models import HostedHarnessJob

    environment = _environment(user, workspace, storage, name)
    _suite(environment, _ten())
    older = environment.suite_version
    case = REFUND_KEYS[0]
    kept = [one for one in older.documents["scenarios"] if one["scenario_key"] != case]
    newer = _copy(
        older, HarnessSuiteVersion, documents={**older.documents, "scenarios": kept}
    )
    _members_of(older, newer)
    HarnessSuiteMember.no_workspace_objects.filter(
        suite_version=newer, scenario_key=case
    ).delete()
    HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
        suite_version=newer
    )
    row = HostedHarnessScenario.no_workspace_objects.get(
        job=environment, scenario_key=case
    )
    HostedHarnessScenario.all_objects.filter(id=row.id).update(
        deleted=True, deleted_at=timezone.now()
    )
    environment.refresh_from_db()
    content = HarnessSuiteMember.no_workspace_objects.get(
        suite_version=older, scenario_key=case
    ).content
    return environment, older, row, {**content, "scenario_key": case}


def test_the_chat_reads_the_removed_cases_and_a_restore_takes_back_its_key_and_row(
    user, workspace, storage
):
    from simulate.services.harness_scenarios import plan_pairs
    from simulate.services.harness_versioning.versions import retired_keys
    from simulate.services.hosted_harness_conversation import removed_scenarios

    environment, older, row, document = _removed_case(
        user, workspace, storage, "lc-restore"
    )
    key = row.scenario_key
    restore = {
        **document,
        "restored_from": {"key": key, "suite_version": str(older.id)},
    }
    mismatched = {**document, "restored_from": {"key": "another-case"}}

    [removed] = removed_scenarios(environment)
    [restored] = plan_pairs(environment, [restore], exact=True)
    # A new case whose key happens to be the removed one's: never its row or history.
    [copied] = plan_pairs(
        environment, [{**document, "tests": "a different case"}], exact=True
    )
    [returned] = plan_pairs(environment, [document], exact=True)

    assert (removed["scenario_key"], removed["suite_version"]) == (key, str(older.id))
    assert (restored.row.id, restored.key, restored.number) == (row.id, key, row.number)
    assert copied.row is None and copied.key != key
    # The same case back exactly as it was, as a rebuild restores one: its row.
    assert returned.row.id == row.id
    assert retired_keys(environment, [restore]) == []
    assert retired_keys(environment, [mismatched]) == [key]


def test_a_new_case_never_takes_an_archived_or_removed_row_on_either_pairing_path(
    user, workspace, storage
):
    from simulate.services.harness_scenarios import plan_pairs

    environment, _older, row, document = _removed_case(
        user, workspace, storage, "lc-archived-guard"
    )
    archived = HostedHarnessScenario.no_workspace_objects.get(
        job=environment, scenario_key=REFUND_KEYS[1]
    )
    HostedHarnessScenario.all_objects.filter(id=archived.id).update(
        archived_at=timezone.now()
    )
    # Archived and no longer in the saved suite: its key now names nothing that runs.
    HarnessSuiteMember.no_workspace_objects.filter(
        suite_version=environment.suite_version, scenario_key=archived.scenario_key
    ).delete()
    new_case = {
        **document,
        "scenario_key": archived.scenario_key,
        "tests": "a different case",
    }
    removed_again = {**document, "tests": "a different case"}

    [on_archived] = plan_pairs(environment, [new_case], exact=True)
    [loose] = plan_pairs(environment, [removed_again], exact=False)

    assert on_archived.row is None
    assert loose.row is None or loose.row.id != row.id


def test_a_restore_with_changes_says_so_and_its_older_results_read_changed_test(
    user, workspace, storage
):
    from simulate.services.harness_versioning.review import _restore_summary
    from simulate.services.run_compare import changed_tests

    environment, older, row, document = _removed_case(
        user, workspace, storage, "lc-restore-changed"
    )
    key = row.scenario_key
    restore = {
        **document,
        "restored_from": {"key": key, "suite_version": str(older.id)},
    }

    def summary(value):
        change = {
            "kind": "scenario",
            "action": "added",
            "key": key,
            "patch": [{"op": "add", "path": "", "value": value}],
        }
        return _restore_summary(environment, change)

    restored_suite = _copy(
        environment.suite_version,
        HarnessSuiteVersion,
        version_number=300,
        hash=older.hash[:-8] + "restored",
    )
    HarnessSuiteMember.no_workspace_objects.create(
        suite_version=restored_suite,
        scenario_id=row.id,
        scenario_key=key,
        number=row.number,
        content={**document, "tests": "a new check"},
    )

    assert summary(restore) == f"Restore scenario {key}"
    assert (
        summary({**restore, "tests": "a new check"})
        == f"Restore scenario {key} with changes: tests"
    )
    assert summary({**document, "tests": "a new check"}) is None
    assert changed_tests(restored_suite.id, older.id, None, None, [key]) == {key}


def test_states_in_order(user, workspace, storage):
    environment = _environment(user, workspace, storage, "lc-order")
    _suite(environment, _ten())
    proofs = dict.fromkeys(KEYS, PROVED)
    proofs[LOOKUP_KEYS[1]] = {
        "format": 2,
        "holds": False,
        "proved": False,
        "ready": True,
        "solvable": False,
        "blocked_reason": "not_solvable",
        "why": "refunded never held",
    }
    _proofs(environment, proofs)
    HostedHarnessScenario.no_workspace_objects.filter(
        job=environment, scenario_key=LOOKUP_KEYS[0]
    ).update(archived_at=timezone.now(), archive_reason="old route")
    candidate = _copy(
        environment.environment_version, HarnessEnvironmentVersion, status="candidate"
    )
    _proofs(environment, dict.fromkeys(KEYS, PROVED), version=candidate)
    saved = _copy(
        environment.environment_version,
        HarnessEnvironmentVersion,
        version_number=(environment.environment_version.version_number or 0) + 200,
        hash=environment.environment_version.hash[:-5] + "saved",
    )
    _proofs(environment, dict.fromkeys(KEYS, PROVED), version=saved)

    on_default = _views(environment)
    on_candidate = _views(environment, version=candidate)
    on_saved = _views(environment, version=saved)

    assert on_default[LOOKUP_KEYS[0]]["state"] == "archived"
    assert on_default[REFUND_KEYS[0]]["state"] == "active"
    # Proved on versions not saved yet: Validated, not Active; on another saved version: Active.
    assert on_candidate[REFUND_KEYS[0]]["state"] == "validated"
    assert on_saved[REFUND_KEYS[0]]["state"] == "active"
    assert (
        on_default[LOOKUP_KEYS[1]]["state"],
        on_default[LOOKUP_KEYS[1]]["reason"],
    ) == ("draft", "not_solvable")
    assert "refunded never held" in on_default[LOOKUP_KEYS[1]]["reason_text"]


def test_draft_rows_read_in_draft(user, workspace, storage):
    from simulate.services.harness_provider import _with_lifecycle

    environment = _environment(user, workspace, storage, "lc-draft")
    _suite(environment, _ten())
    lifecycle = {"views": _views(environment), "archived_views": {}}

    added = _with_lifecycle(
        {"scenario_key": "brand-new", "draft_change": "added"}, lifecycle
    )
    changed = _with_lifecycle(
        {"scenario_key": REFUND_KEYS[0], "draft_change": "changed"}, lifecycle
    )
    unchanged = _with_lifecycle(
        {"scenario_key": REFUND_KEYS[1], "draft_change": "unchanged"}, lifecycle
    )

    assert added["lifecycle"]["reason"] == "in_draft"
    assert changed["lifecycle"]["reason"] == "in_draft"
    assert unchanged["lifecycle"]["state"] == "active"


def test_legacy_version_validates_holds_and_certified_without_proof(
    user, workspace, storage
):
    environment = _environment(user, workspace, storage, "lc-legacy")
    _suite(environment, _ten())
    HarnessSuiteVersion._base_manager.filter(id=environment.suite_version_id).update(
        lifecycle_legacy=True
    )
    environment.refresh_from_db()
    # Format 1: one holds, the rest have no entry on a certified pairing.
    _proofs(environment, {REFUND_KEYS[0]: {"holds": True}})

    views = _views(environment)

    assert (views[REFUND_KEYS[0]]["state"], views[REFUND_KEYS[0]]["legacy"]) == (
        "kept_as_written",
        True,
    )
    assert (views[LOOKUP_KEYS[0]]["state"], views[LOOKUP_KEYS[0]]["legacy"]) == (
        "kept_as_written",
        True,
    )


def test_unchanged_content_inherits_legacy_on_same_environment(
    user, workspace, storage
):
    environment = _environment(user, workspace, storage, "lc-inherit")
    _suite(environment, _ten())
    legacy = environment.suite_version
    HarnessSuiteVersion._base_manager.filter(id=legacy.id).update(lifecycle_legacy=True)
    _proofs(environment, {key: {"holds": True} for key in KEYS})
    newer = _copy(legacy, HarnessSuiteVersion, lifecycle_legacy=False)
    _members_of(legacy, newer, changed=REFUND_KEYS[0])
    other = _copy(environment.environment_version, HarnessEnvironmentVersion)

    same = _views(environment, suite=newer)
    moved = _views(environment, suite=newer, version=other)

    assert (
        same[REFUND_KEYS[1]]["legacy"] is True
        and same[REFUND_KEYS[1]]["state"] == "kept_as_written"
    )
    assert same[REFUND_KEYS[0]]["state"] == "draft"
    assert moved[REFUND_KEYS[1]]["state"] == "draft"


def test_format_two_beats_legacy(user, workspace, storage):
    environment = _environment(user, workspace, storage, "lc-beats")
    _suite(environment, _ten(), proved=False)
    HarnessSuiteVersion._base_manager.filter(id=environment.suite_version_id).update(
        lifecycle_legacy=True
    )
    environment.refresh_from_db()
    _proofs(
        environment,
        {
            REFUND_KEYS[0]: {
                "format": 2,
                "holds": False,
                "proved": False,
                "ready": True,
                "solvable": False,
                "blocked_reason": "not_solvable",
            },
            REFUND_KEYS[1]: {
                "format": 2,
                "holds": False,
                "proved": False,
                "blocked_reason": "validation_incomplete",
            },
        },
    )

    views = _views(environment)

    assert views[REFUND_KEYS[0]]["state"] == "draft"
    assert (views[REFUND_KEYS[1]]["state"], views[REFUND_KEYS[1]]["legacy"]) == (
        "kept_as_written",
        True,
    )


def test_a_legacy_case_without_a_negative_control_reads_draft(user, workspace, storage):
    """A case written before controls were required is kept, recorded not proved: Draft with its reason, never
    Kept as written, so it is held out of runs until it has one."""
    environment = _environment(user, workspace, storage, "lc-control")
    _suite(environment, _ten(), proved=False)
    HarnessSuiteVersion._base_manager.filter(id=environment.suite_version_id).update(
        lifecycle_legacy=True
    )
    environment.refresh_from_db()
    _proofs(environment, {REFUND_KEYS[0]: {"holds": True}})
    views_before = _views(environment)
    assert views_before[REFUND_KEYS[0]]["legacy"] is True
    # The first certify writes through record_proofs, which replaces the old entry key by key.
    record_proofs(
        environment.suite_version,
        environment.environment_version,
        {
            REFUND_KEYS[0]: {
                "format": 2,
                "holds": True,
                "proved": False,
                "blocked_reason": "no_negative_control",
            },
            REFUND_KEYS[1]: {
                "format": 2,
                "holds": True,
                "proved": False,
                "blocked_reason": "answer_leaked",
            },
        },
        None,
    )
    cache.clear()

    views = _views(environment)

    assert (views[REFUND_KEYS[0]]["state"], views[REFUND_KEYS[0]]["reason"]) == (
        "draft",
        "no_negative_control",
    )
    assert "wrong-way example" in views[REFUND_KEYS[0]]["reason_text"]
    assert views[REFUND_KEYS[0]]["warning"] is None
    assert views[REFUND_KEYS[1]]["state"] == "draft"


def _with_rules_and_files(version, rules, digests):
    documents = dict(version.documents or {})
    documents["contract"] = {
        **(documents.get("contract") or {}),
        "hard_constraints": rules,
    }
    documents["file_digests"] = digests
    HarnessEnvironmentVersion._base_manager.filter(id=version.id).update(
        documents=documents
    )
    version.refresh_from_db()
    return version


def _cite(environment, sources_by_key):
    for key, sources in sources_by_key.items():
        member = HarnessSuiteMember.no_workspace_objects.get(
            suite_version=environment.suite_version, scenario_key=key
        )
        member.content = {**(member.content or {}), "sources": sources}
        member.save(update_fields=["content", "updated_at"])


def test_source_changed_rule_and_file(user, workspace, storage):
    environment = _environment(user, workspace, storage, "lc-sources")
    _suite(environment, _ten())
    parent = _with_rules_and_files(
        environment.environment_version,
        ["Refund at most once.", "Never refund after 14 days."],
        {"knowledge/policy.md": "aaa", "knowledge/other.md": "bbb"},
    )
    child = _copy(parent, HarnessEnvironmentVersion, parent=parent)
    # Rule 0 moved index only; rule 1 now reads 30 days; policy.md changed; other.md did not.
    _with_rules_and_files(
        child,
        ["A new first rule.", "Refund at most once.", "Never refund after 30 days."],
        {"knowledge/policy.md": "ccc", "knowledge/other.md": "bbb"},
    )
    _cite(
        environment,
        {
            REFUND_KEYS[0]: [
                {"kind": "rule", "ref": "rule:0", "quote": "at most once"}
            ],
            REFUND_KEYS[1]: [
                {"kind": "rule", "ref": "rule:1", "quote": "Never refund after 14 days"}
            ],
            REFUND_KEYS[2]: [
                {"kind": "file", "ref": "file:policy.md", "quote": "14 days"}
            ],
            REFUND_KEYS[3]: [
                {"kind": "file", "ref": "file:other.md", "quote": "anything"}
            ],
        },
    )

    views = _views(environment, version=child)

    flagged = {key for key, view in views.items() if view["source_changed"]}
    assert flagged == {REFUND_KEYS[1], REFUND_KEYS[2]}
    assert views[REFUND_KEYS[1]]["source_changed"] == ["rule:1"]
    assert views[REFUND_KEYS[2]]["state"] == "draft"


def test_counts_call_only_a_run_proof_proved():
    from simulate.services.harness_scenario_lifecycle import lifecycle_counts

    def view(state, warning=None, legacy=False):
        return {"state": state, "warning": warning, "legacy": legacy}

    counts = lifecycle_counts(
        [
            view("active"),
            view("active", warning="evidence_not_captured"),
            view("kept_as_written", warning="solution_assumed"),
            view("kept_as_written", warning="legacy_no_negative_control", legacy=True),
            view("kept_as_written", legacy=True),
            view("draft"),
            view("archived"),
        ]
    )

    assert (counts["proved"], counts["kept_as_written"], counts["not_proved"]) == (
        2,
        3,
        1,
    )
    assert (counts["active"], counts["archived"]) == (2, 1)


def test_draft_rows_show_their_own_sources(user, workspace, storage):
    from simulate.services.harness_provider import _with_lifecycle

    environment = _environment(user, workspace, storage, "lc-draft-sources")
    _suite(environment, _ten())
    quote = "issues exactly one full refund"
    lifecycle = {
        "views": _views(environment),
        "archived_views": {},
        "resolve_sources": lambda by_key: {
            key: [{"kind": "requirement", "quote": quote, "resolved": True}]
            for key in by_key
        },
    }

    added = _with_lifecycle(
        {"scenario_key": "brand-new", "draft_change": "added"},
        lifecycle,
        draft_sources=[{"kind": "requirement", "quote": quote}],
    )

    assert added["lifecycle"]["reason"] == "in_draft"
    assert added["lifecycle"]["sources"][0]["quote"] == quote


ASSUMED = {
    "format": 2,
    "holds": True,
    "proved": False,
    "assumed": ["refund_order"],
    "solution_steps": 2,
    "kept_as_written": True,
    "blocked_reason": "solution_assumed",
}


def test_assumed_solution_reads_kept_as_written_not_active(user, workspace, storage):
    environment = _environment(user, workspace, storage, "lc-kept")
    _suite(environment, _ten())
    proofs = dict.fromkeys(KEYS, PROVED)
    proofs[REFUND_KEYS[0]] = ASSUMED
    _proofs(environment, proofs)

    views = _views(environment)

    assert (views[REFUND_KEYS[0]]["state"], views[REFUND_KEYS[0]]["warning"]) == (
        "kept_as_written",
        "solution_assumed",
    )
    assert "could not be run here" in views[REFUND_KEYS[0]]["warning_text"]
    assert views[REFUND_KEYS[1]]["state"] == "active"


def test_legacy_proof_reads_kept_as_written(user, workspace, storage):
    environment = _environment(user, workspace, storage, "lc-kept-legacy")
    _suite(environment, _ten())
    proofs = dict.fromkeys(KEYS, PROVED)
    proofs[REFUND_KEYS[0]] = {"holds": True}
    _proofs(environment, proofs)

    view = _views(environment)[REFUND_KEYS[0]]

    assert (view["state"], view["legacy"]) == ("kept_as_written", True)


def test_judged_only_reads_kept_as_written_with_its_text(user, workspace, storage):
    environment = _environment(user, workspace, storage, "lc-judged")
    _suite(environment, _ten())
    proofs = dict.fromkeys(KEYS, PROVED)
    proofs[REFUND_KEYS[0]] = {
        **ASSUMED,
        "assumed": [],
        "judged_only": True,
        "blocked_reason": "judged_only",
    }
    _proofs(environment, proofs)

    view = _views(environment)[REFUND_KEYS[0]]

    assert (view["state"], view["warning"]) == ("kept_as_written", "judged_only")
    assert view["warning_text"].startswith(
        "Kept as written: only an AI judge grades this case"
    )


def test_policy_conflict_reads_draft_with_the_why(user, workspace, storage):
    environment = _environment(user, workspace, storage, "lc-conflict")
    _suite(environment, _ten())
    proofs = dict.fromkeys(KEYS, PROVED)
    proofs[REFUND_KEYS[0]] = {
        "format": 2,
        "holds": True,
        "proved": False,
        "blocked_reason": "policy_conflict",
        "why": "its expected outcome contradicts a cited rule:\n  - rule:0: the rule allows 14 days",
    }
    _proofs(environment, proofs)

    view = _views(environment)[REFUND_KEYS[0]]

    assert (view["state"], view["reason"]) == ("draft", "policy_conflict")
    assert view["reason_text"].startswith(
        "Its expected outcome contradicts a cited rule"
    )


def test_uncaptured_evidence_warns(user, workspace, storage, monkeypatch):
    from simulate.services import harness_scenario_lifecycle as lifecycle

    environment = _environment(user, workspace, storage, "lc-evidence")
    _suite(environment, _ten())
    proofs = dict.fromkeys(KEYS, PROVED)
    proofs[REFUND_KEYS[0]] = {**PROVED, "evidence": {"refunded": ["tool_calls"]}}
    proofs[REFUND_KEYS[1]] = {**PROVED, "evidence": {"stock_left": ["world_state"]}}
    _proofs(environment, proofs)
    monkeypatch.setattr(
        "simulate.services.harness_readiness.report.resolve_pairing",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "simulate.services.harness_readiness.report.readiness_report",
        lambda pairing, **kwargs: {
            "rows": [
                {"id": "tool_routing", "status": "blocked"},
                {"id": "state", "status": "supported"},
            ]
        },
    )

    views = _views(environment)

    assert (views[REFUND_KEYS[0]]["state"], views[REFUND_KEYS[0]]["warning"]) == (
        "active",
        "evidence_not_captured",
    )
    assert views[REFUND_KEYS[0]]["warning_text"] == (
        "refunded reads the tool calls, which this agent does not share with the test; it will read Not measured."
    )
    assert views[REFUND_KEYS[1]]["warning"] is None
    assert lifecycle.runnable(views[REFUND_KEYS[0]]) is True


def test_list_shows_validated_on_a_named_candidate_pair(user, workspace, storage):
    from simulate.services.harness_provider import _lifecycle_for_list

    environment = _environment(user, workspace, storage, "lc-candidate-list")
    _suite(environment, _ten())
    other = _copy(
        environment.environment_version, HarnessEnvironmentVersion, status="candidate"
    )
    _proofs(environment, dict.fromkeys(KEYS, PROVED), version=other)
    _proofs(environment, dict.fromkeys(KEYS, PROVED))

    default = _lifecycle_for_list(environment, {})
    candidate = _lifecycle_for_list(
        environment,
        {
            "suite_version_id": str(environment.suite_version_id),
            "environment_version_id": str(other.id),
        },
    )

    assert default["views"][REFUND_KEYS[0]]["state"] == "active"
    assert candidate["views"][REFUND_KEYS[0]]["state"] == "validated"


def test_must_pass_failures_name_only_failed_must_pass_cases():
    from types import SimpleNamespace
    from unittest.mock import patch

    from simulate.services import harness_scenario_lifecycle as lifecycle

    members = [
        ("refund-a", {"name": "Refund A", "must_pass": True}),
        ("refund-b", {"name": "Refund B", "must_pass": True}),
        ("refund-c", {"name": "Refund C"}),
    ]
    calls = [
        {
            "hosted_harness_receipt": {"scenario_key": "refund-a"},
            "harness_outcome_status": "failed",
        },
        {
            "hosted_harness_receipt": {"scenario_key": "refund-b"},
            "harness_outcome_status": "passed",
        },
        {
            "hosted_harness_receipt": {"scenario_key": "refund-c"},
            "harness_outcome_status": "failed",
        },
    ]

    class _Rows:
        def __init__(self, rows):
            self.rows = rows

        def filter(self, **kwargs):
            return self

        def values_list(self, *fields, flat=False):
            return self.rows

    execution = SimpleNamespace(
        hosted_harness_job=SimpleNamespace(suite_version_id="s1")
    )
    with (
        patch.object(
            lifecycle.HarnessSuiteMember, "no_workspace_objects", _Rows(members)
        ),
        patch("simulate.models.CallExecution.objects", _Rows(calls)),
    ):
        assert lifecycle.must_pass_failures(execution) == ["Refund A"]
    assert lifecycle.must_pass_failures(SimpleNamespace(hosted_harness_job=None)) == []


def _workflow_view(state, outcome="change", controls=()):
    return {"state": state, "proof": {"outcome": outcome, "controls": list(controls)}}


def test_workflow_proof_names_the_missing_refusal():
    from simulate.services.harness_scenario_lifecycle import workflow_proof

    contents = {
        "a": {"use_case": "refunds", "must_pass": True},
        "b": {"use_case": "refunds"},
        "c": {"use_case": "refunds"},
        "d": {"use_case": "lookups"},
    }
    views = {
        "a": _workflow_view("active", controls=[{"failed": True, "assumed": []}]),
        # Kept as written never counts as a proved success.
        "b": _workflow_view("kept_as_written"),
        "c": _workflow_view("draft", outcome="no_change"),
        "d": _workflow_view("active", outcome="no_change"),
    }

    rows = workflow_proof(views, contents)

    assert rows == [
        {
            "workflow": "refunds",
            "success": 1,
            "refusal": 0,
            "wrong_result": 1,
            "missing": ["refusal"],
        }
    ]
    # With no must-pass case, every use case is a workflow.
    contents["a"]["must_pass"] = False
    assert [row["workflow"] for row in workflow_proof(views, contents)] == [
        "lookups",
        "refunds",
    ]


def test_unrun_control_is_not_a_wrong_result():
    from simulate.services.harness_scenario_lifecycle import workflow_proof

    contents = {"a": {"use_case": "refunds"}}
    for controls in (
        [{"failed": None, "assumed": []}],
        [{"failed": True, "assumed": ["refund"]}],
        [{"failed": False, "assumed": []}],
    ):
        row = workflow_proof(
            {"a": _workflow_view("active", controls=controls)}, contents
        )[0]
        assert row["wrong_result"] == 0 and "wrong_result" in row["missing"]


def test_apply_failed_reads_draft_with_reason(user, workspace, storage):
    from simulate.services.harness_scenario_lifecycle import runnable
    from simulate.services.hosted_harness_conversation import record_failed_changes

    environment = _environment(user, workspace, storage, "lc-apply-failed")
    _suite(environment, _ten())
    _proofs(environment, dict.fromkeys(KEYS, PROVED))
    record_failed_changes(
        environment,
        [
            {
                "scenario_key": REFUND_KEYS[0],
                "reason": "check refunded_once: invalid syntax\nTraceback",
            }
        ],
    )
    environment.refresh_from_db()

    views = _views(environment)

    assert (views[REFUND_KEYS[0]]["state"], views[REFUND_KEYS[0]]["reason"]) == (
        "draft",
        "apply_failed",
    )
    assert views[REFUND_KEYS[0]]["reason_text"] == (
        "This change could not be applied: check refunded_once: invalid syntax. "
        "Fix it in the builder chat or discard it."
    )
    # Only that case is held back; the rest stay runnable.
    assert runnable(views[REFUND_KEYS[0]]) is False
    assert all(runnable(views[key]) for key in KEYS if key != REFUND_KEYS[0])
    # The next save that applies it clears it.
    record_failed_changes(environment, [])
    environment.refresh_from_db()
    assert _views(environment)[REFUND_KEYS[0]]["state"] == "active"


def test_an_archived_case_with_a_failed_change_stays_archived(user, workspace, storage):
    from django.utils import timezone

    from simulate.models import HostedHarnessScenario
    from simulate.services.hosted_harness_conversation import record_failed_changes

    environment = _environment(user, workspace, storage, "lc-apply-failed-archived")
    _suite(environment, _ten())
    _proofs(environment, dict.fromkeys(KEYS, PROVED))
    HostedHarnessScenario.no_workspace_objects.filter(
        job=environment, scenario_key=REFUND_KEYS[1]
    ).update(archived_at=timezone.now(), archive_reason="retired route")
    record_failed_changes(
        environment, [{"scenario_key": REFUND_KEYS[1], "reason": "bad check"}]
    )
    environment.refresh_from_db()

    view = _views(environment)[REFUND_KEYS[1]]

    assert (view["state"], view["reason"]) == ("archived", "archived")


def test_a_proof_blocked_on_a_source_names_that_source(user, workspace, storage):
    environment = _environment(user, workspace, storage, "lc-source-proof")
    _suite(environment, _ten())
    _with_rules_and_files(environment.environment_version, ["Refund at most once."], {})
    quote = "Identify the caller by full name and date of birth."
    _cite(environment, {REFUND_KEYS[0]: [{"kind": "rule", "ref": "", "quote": quote}]})
    _proofs(
        environment,
        {
            **dict.fromkeys(KEYS, PROVED),
            REFUND_KEYS[0]: {
                **PROVED,
                "proved": False,
                "blocked_reason": "source_unresolved",
            },
            REFUND_KEYS[1]: {
                **PROVED,
                "proved": False,
                "blocked_reason": "source_unresolved",
            },
        },
    )

    views = _views(environment)

    assert views[REFUND_KEYS[0]]["reason"] == "source_unresolved"
    assert views[REFUND_KEYS[0]]["reason_text"].startswith(
        "A cited source is not in this environment: rule Identify the caller"
    )
    # No source the platform can point at: the text says so without empty blanks.
    assert (
        views[REFUND_KEYS[1]]["reason_text"]
        == "A cited source is not in this environment."
    )


def test_a_case_kept_for_a_judged_policy_conflict_reads_kept_as_written_with_the_conflict(
    user, workspace, storage
):
    environment = _environment(user, workspace, storage, "lc-judged-conflict")
    _suite(environment, _ten())
    why = (
        "a model judged that its expected outcome may contradict a cited rule (a judgment, not a proof):\n"
        '  - possible conflict with rule:0 "Refund at most once": it refunds twice\n\n'
        "Read the rule against the case. Left as it is, the case is kept as written with this warning."
    )
    kept = {
        **PROVED,
        "proved": False,
        "holds": True,
        "kept_as_written": True,
        "blocked_reason": "policy_conflict",
        "why": why,
    }
    _proofs(environment, {**dict.fromkeys(KEYS, PROVED), REFUND_KEYS[0]: kept})

    view = _views(environment)[REFUND_KEYS[0]]

    assert (view["state"], view["warning"], view["warning_text"]) == (
        "kept_as_written",
        "policy_conflict",
        "Kept as written: a model judged its expected outcome may contradict a cited rule "
        '(possible conflict with rule:0 "Refund at most once": it refunds twice); a judgment, not a proof.',
    )


@pytest.mark.parametrize(
    ("note", "text"),
    [
        (
            "memorize: recorded, not run (no world effect)",
            "memorize: recorded, not run (no world effect)",
        ),
        (None, "memorize, note: recorded, not run (no world effect)."),
    ],
)
def test_a_proof_that_recorded_steps_instead_of_running_them_warns_and_keeps_its_state(
    user, workspace, storage, note, text
):
    environment = _environment(user, workspace, storage, f"lc-recorded-{bool(note)}")
    _suite(environment, _ten())
    recorded = {**PROVED, "recorded": ["memorize", "note"][: 1 if note else 2]}
    if note:
        recorded["recorded_note"] = note
    _proofs(environment, {**dict.fromkeys(KEYS, PROVED), REFUND_KEYS[0]: recorded})

    views = _views(environment)

    assert views[REFUND_KEYS[0]]["state"] == views[REFUND_KEYS[1]]["state"] == "active"
    assert (
        views[REFUND_KEYS[0]]["warning"],
        views[REFUND_KEYS[0]]["warning_text"],
    ) == (
        "recorded_not_run",
        text,
    )
    assert views[REFUND_KEYS[1]]["warning"] is None


def test_a_case_citing_the_agent_s_own_source_reads_by_the_harness_verdict(
    user, workspace, storage
):
    """An agent's own source file is not among the environment's files: the harness, which has it, decides the
    citation, and a citation nothing resolved names the file once, never `file file:`."""
    environment = _environment(user, workspace, storage, "lc-agent-source")
    _suite(environment, _ten())
    _with_rules_and_files(environment.environment_version, [], {})
    cited = {"kind": "file", "ref": "file:agent/app.py", "quote": "def issue_refund"}
    _cite(environment, {key: [cited] for key in REFUND_KEYS[:2]})
    read = {"kind": "file", "ref": "file:agent/app.py", "problem": ""}
    _proofs(
        environment,
        {
            **dict.fromkeys(KEYS, PROVED),
            REFUND_KEYS[0]: {
                **PROVED,
                "sources": [
                    {**read, "resolved": True, "matched_digest": "sha256:" + "a" * 64}
                ],
            },
            REFUND_KEYS[1]: {
                **PROVED,
                "sources": [
                    {**read, "resolved": None, "problem": "not in the authoring tree"}
                ],
            },
        },
    )

    views = _views(environment)

    assert views[REFUND_KEYS[0]]["state"] == "active"
    assert views[REFUND_KEYS[0]]["sources"][0]["resolved"] is True
    assert (views[REFUND_KEYS[1]]["reason"], views[REFUND_KEYS[1]]["reason_text"]) == (
        "source_unresolved",
        "A cited source is not in this environment: file agent/app.py.",
    )


@pytest.mark.parametrize(
    ("code", "why", "detail"),
    [
        (
            "policy_conflict",
            'its expected outcome contradicts a cited rule:\n  - rule:0 "Refund once": it refunds twice',
            'rule:0 "Refund once": it refunds twice',
        ),
        (
            "not_ready",
            "the order the case cites is missing",
            "the order the case cites is missing",
        ),
    ],
)
def test_a_listed_reason_reads_by_its_first_item_not_its_heading(code, why, detail):
    from simulate.services.harness_scenario_lifecycle import REASON_TEXTS, reason_text

    assert reason_text(code, {"why": why}) == REASON_TEXTS[code].format(why=detail)


def test_a_candidate_proved_before_save_keeps_its_proofs_and_reads_validated(
    user, workspace, storage
):
    from simulate.tests.test_harness_affected_scenarios import LOOKUP, REFUND, _doc

    environment = _environment(user, workspace, storage, "lc-candidate")
    _suite(environment, _ten())
    saved = HarnessSuiteVersion.no_workspace_objects.get(
        id=environment.suite_version_id
    )
    added = _doc("partial-refund", solution=(LOOKUP, REFUND), sub_goals=("refunded",))
    candidate = _copy(
        saved,
        HarnessSuiteVersion,
        status="candidate",
        documents={**saved.documents, "scenarios": [*_ten(), added]},
    )

    # The validation's proofs name the scenario folders, as ALK writes them.
    proofs = {"partial_refund": PROVED, **dict.fromkeys(KEYS, PROVED)}
    validation = record_proofs(candidate, environment.environment_version, proofs, None)

    assert validation.proofs["partial-refund"]["proved"] is True
    assert validation.status == "passed"
    assert (
        _views(environment, suite=candidate)["partial-refund"]["state"] == "validated"
    )
    assert not HostedHarnessScenario.no_workspace_objects.filter(
        job=environment, scenario_key="partial-refund"
    ).exists()


def test_a_legacy_suite_kept_as_written_is_not_warned_as_never_proved(
    user, workspace, storage
):
    from simulate.models import HostedHarnessJob
    from simulate.services.harness_versioning.run_warnings import run_warnings
    from simulate.services.hosted_harness import resolve_run_pins

    environment = _environment(user, workspace, storage, "lc-legacy-warning")
    _suite(environment, _ten())
    legacy = environment.suite_version
    HarnessSuiteVersion._base_manager.filter(id=legacy.id).update(lifecycle_legacy=True)
    _proofs(environment, {key: {"holds": True} for key in KEYS})
    newer = _copy(legacy, HarnessSuiteVersion, lifecycle_legacy=False)
    _members_of(legacy, newer)
    HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
        suite_version=newer
    )
    environment.refresh_from_db()

    warnings = {
        one["code"]: one["message"]
        for one in run_warnings(environment, resolve_run_pins(environment))
    }

    assert "suite_unproved" not in warnings
    assert (
        "0 proved, 10 kept as written, 0 not proved" in warnings["suite_partly_proved"]
    )


def _on_agent(environment, monkeypatch, tools):
    """A second agent version paired with the environment, its tools as read when asked (``None``: never read)."""
    from simulate.services import agent_setup
    from simulate.tests.test_harness_versioning_picks import _second_agent

    agent = _second_agent(environment)
    monkeypatch.setattr(
        agent_setup,
        "stored_agent",
        lambda version_id, **_: (
            {"tools": [{"name": one} for one in tools]}
            if tools is not None and str(version_id) == str(agent.id)
            else None
        ),
    )
    return agent


def _views_with(environment, agent):
    return scenario_lifecycle(
        environment,
        suite_version=environment.suite_version,
        environment_version=environment.environment_version,
        agent_version_id=agent.id,
    )


def test_a_case_whose_reference_needs_a_tool_the_agent_dropped_is_held_until_it_comes_back(
    user, workspace, storage, monkeypatch
):
    from simulate.services.harness_readiness import selection
    from simulate.services.harness_readiness.report import resolve_pairing

    environment = _environment(user, workspace, storage, "tool-dropped")
    _suite(environment, _ten())
    _proofs(environment, dict.fromkeys(REFUND_KEYS + LOOKUP_KEYS, PROVED))
    tools = ["lookup_order"]
    agent = _on_agent(environment, monkeypatch, tools)

    held = _views_with(environment, agent)
    plan = selection.select_for_run(
        resolve_pairing(environment, agent_version_id=agent.id),
        keys=[REFUND_KEYS[0], LOOKUP_KEYS[0]],
        trials=1,
    )
    tools.append("issue_refund")
    back = _views_with(environment, agent)

    assert (held[REFUND_KEYS[0]]["state"], held[REFUND_KEYS[0]]["reason"]) == (
        "draft",
        "tool_not_on_agent",
    )
    assert held[REFUND_KEYS[0]]["reason_text"] == (
        f"Needs issue_refund, which agent version v{agent.version_number} doesn't have."
    )
    assert held[LOOKUP_KEYS[0]]["state"] == "active"
    assert plan.view["held_back"]["by"]["tool_not_on_agent"] == 1
    assert "Needs issue_refund" in plan.view["reasons"]["tool_not_on_agent"]
    assert back[REFUND_KEYS[0]]["state"] == "active"


def test_a_case_proved_by_a_check_that_never_read_the_agent_is_not_active(
    user, workspace, storage, monkeypatch
):
    """Proved by a check of this agent version that never read its tools: unknown, never Active. A proof whose check
    read them, one another agent's check made, or one that names no check, is read as before."""
    import uuid

    from simulate.models import HostedHarnessJob

    environment = _environment(user, workspace, storage, "tools-unread")
    _suite(environment, _ten())
    agent = _on_agent(environment, monkeypatch, None)
    check = HostedHarnessJob.no_workspace_objects.get(id=environment.id)
    check.pk = check.id = uuid.uuid4()
    check.run_id = uuid.uuid4()
    check.idempotency_key = f"check-{check.id}"
    check.environment_id = environment.id
    check.agent_version_id = agent.id
    check.payload = {
        **(check.payload or {}),
        "metadata": {"certify_only": True, "agent_reading": "before_certify"},
    }
    check.save()
    _proofs(
        environment,
        {
            REFUND_KEYS[0]: {**PROVED, "job_id": None},
            REFUND_KEYS[1]: {**PROVED, "job_id": str(environment.id)},
            **dict.fromkeys(LOOKUP_KEYS, {**PROVED, "job_id": str(check.id)}),
        },
    )
    read_by_check = _views_with(environment, agent)
    HostedHarnessJob.no_workspace_objects.filter(id=check.id).update(
        payload={**check.payload, "metadata": {"certify_only": True}}
    )

    views = _views_with(environment, agent)

    assert read_by_check[LOOKUP_KEYS[0]]["state"] == "active"
    assert (views[LOOKUP_KEYS[0]]["state"], views[LOOKUP_KEYS[0]]["reason"]) == (
        "draft",
        "tools_unread",
    )
    assert views[REFUND_KEYS[0]]["state"] == "active"
    assert views[REFUND_KEYS[1]]["state"] == "active"
    assert _views(environment)[LOOKUP_KEYS[0]]["state"] == "active"


def test_legacy_cases_a_validate_would_hold_for_no_negative_control_are_counted():
    from simulate.services.harness_scenario_lifecycle import lifecycle_counts

    control = [{"name": "wrong_order", "why": "w", "steps": [], "must_fail": []}]
    views = [
        {"state": "kept_as_written", "legacy": True, "negative_controls": []},
        {"state": "kept_as_written", "legacy": True, "negative_controls": control},
        {"state": "kept_as_written", "legacy": False, "negative_controls": []},
        {"state": "active", "legacy": False, "negative_controls": []},
    ]

    counts = lifecycle_counts(views)

    assert counts["legacy_without_control"] == 1


def test_cases_kept_without_a_control_are_counted_from_current_and_legacy_proofs(
    user, workspace, storage
):
    from simulate.services.harness_scenario_lifecycle import lifecycle_counts

    environment = _environment(user, workspace, storage, "lc-no-control")
    _suite(environment, _ten())
    proofs = dict.fromkeys(KEYS, PROVED)
    proofs[REFUND_KEYS[0]] = {
        **ASSUMED,
        "assumed": [],
        "blocked_reason": "no_negative_control",
    }
    proofs[REFUND_KEYS[1]] = {"holds": True}
    proofs[REFUND_KEYS[2]] = ASSUMED
    _proofs(environment, proofs)

    counts = lifecycle_counts(_views(environment).values())

    assert (counts["kept_as_written"], counts["legacy_without_control"]) == (3, 2)
    assert counts["kept_as_written"] == 3


def _refused(code, *items, **fields):
    """A proof the harness refused for ``code``, its reason written as it writes one: a line, then `- item` lines."""
    why = "the case cannot be proved:" + "".join(f"\n  - {one}" for one in items)
    return {
        **PROVED,
        "proved": False,
        "holds": False,
        "refused": True,
        "blocked_reason": code,
        "why": why,
        **fields,
    }


GAP = __import__("re").compile(r"\s{2,}|\s[,.;:]|\{\w*\}|\(\s*\)|''|\"\"|\bv\b(?!\w)")


def test_every_reason_a_proof_gives_renders_with_nothing_left_blank(
    user, workspace, storage
):
    """Each reason the harness can give a case, read through the lifecycle and a real run's selection: no blank
    name, no doubled space, no placeholder left in the words a person reads."""
    from simulate.services.harness_readiness import selection
    from simulate.services.harness_readiness.report import resolve_pairing
    from simulate.tests.test_harness_affected_scenarios import _doc

    environment = _environment(user, workspace, storage, "reason-gaps")
    entries = {
        "not_ready": _refused("not_ready", "the world was not ready"),
        "not_solvable": _refused("not_solvable", "lookup_order: not done"),
        "broken_check": _refused("broken_check", "refunded: raised KeyError"),
        "weak_check": _refused("weak_check", "refunded passes without a refund"),
        "credits_failed_attempt": _refused(
            "credits_failed_attempt", "a failed refund is credited"
        ),
        "too_strict": _refused("too_strict", "a second lookup is refused"),
        "unchecked": _refused("unchecked", "the refund amount"),
        "policy_conflict": _refused(
            "policy_conflict", "refunds over 30 days are not allowed"
        ),
        "no_negative_control": _refused("no_negative_control"),
        "vacuous": _refused("vacuous"),
        "tool_not_on_agent": _refused("tool_not_on_agent", "issue_refund"),
        "control_misses_case_goal": _refused(
            "control_misses_case_goal", "refunded", "refund_amount_matches"
        ),
        "negative_control_passed": _refused(
            "negative_control_passed",
            controls=[
                {
                    "name": "refunds_twice",
                    "why": "refunds twice",
                    "held": ["refunded"],
                    "failed": False,
                    "ran": True,
                }
            ],
        ),
        "answer_leaked": _refused(
            "answer_leaked",
            leaks=[
                {
                    "who": "caller",
                    "field": "persona",
                    "excerpt": "40",
                    "grader_field": "tests",
                }
            ],
        ),
    }
    _suite(environment, [_doc(code, solution=("lookup_order",)) for code in entries])
    _proofs(environment, entries)

    views = _views(environment)
    plan = selection.select_for_run(
        resolve_pairing(environment), keys=list(entries), trials=1
    )

    for code in entries:
        assert views[code]["reason"] == code, (code, views[code]["reason"])
        text = views[code]["reason_text"] or ""
        assert text and not GAP.search(text), (code, text)
    for row, text in plan.view["reasons"].items():
        assert not GAP.search(text), (row, text)
    assert views["tool_not_on_agent"]["reason_text"] == (
        f"Needs issue_refund, which agent version {_label(environment)} doesn't have."
    )
    assert views["control_misses_case_goal"]["reason_text"] == (
        "Its wrong-way examples only fail checks every scenario shares; add one that fails this case's own goal: "
        "refunded, refund_amount_matches."
    )
    assert "control_misses_case_goal" in plan.held
    assert plan.view["held_back"]["by"]["proof_failed"] >= 1
    # A proof that names no goal still reads as a whole sentence.
    from simulate.services.harness_scenario_lifecycle import reason_text

    bare = reason_text("control_misses_case_goal", {"why": "the controls miss it"})
    assert bare.endswith("own goal.") and not GAP.search(bare)


def test_a_wrong_way_that_makes_a_call_no_check_reads_names_that_call():
    from simulate.services.harness_scenario_lifecycle import reason_text

    control = {
        "name": "refunds_anyway",
        "why": "refunds an order past 30 days",
        "held": ["order_looked_up"],
    }
    control.update(failed=False, ran=True)
    said = reason_text(
        "negative_control_passed",
        {"controls": [{**control, "unmatched": ["issue_refund"]}]},
    )
    assert said == (
        "The checks pass a wrong way of handling it: refunds_anyway (refunds an order past 30 days). It calls "
        "issue_refund, which no check reads: add a check that fails when that happens."
    )
    # A proof saved before controls named their calls reads as it always did.
    assert reason_text("negative_control_passed", {"controls": [control]}) == (
        "The checks pass a wrong way of handling it: refunds_anyway (refunds an order past 30 days). "
        "Tighten order_looked_up."
    )


def _label(environment):
    from simulate.services.harness_versioning.read import _label as label

    return label(environment.agent_version.version_number) or "this one"


@pytest.mark.parametrize(
    "fields, named",
    [
        ({"missing_tools": ["issue_refund", "void_order"]}, "issue_refund, void_order"),
        # A proof from before the harness wrote the list: the items of its reason.
        ({}, "issue_refund"),
    ],
    ids=["listed", "from-the-reason"],
)
def test_a_refused_case_names_the_tools_the_agent_lacks(
    user, workspace, storage, fields, named
):
    environment = _environment(user, workspace, storage, f"missing-{len(fields)}")
    _suite(environment, _ten())
    _proofs(
        environment,
        {LOOKUP_KEYS[0]: _refused("tool_not_on_agent", "issue_refund", **fields)},
    )

    view = _views(environment)[LOOKUP_KEYS[0]]

    assert view["reason_text"] == (
        f"Needs {named}, which agent version {_label(environment)} doesn't have."
    )
