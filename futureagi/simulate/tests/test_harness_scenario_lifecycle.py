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
    readiness_on as readiness_on,
)
from simulate.tests.test_harness_readiness import (  # noqa: F401 - fixture
    storage as storage,
)

pytestmark = pytest.mark.django_db
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
        **changes,
    )
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
    other = _copy(environment.environment_version, HarnessEnvironmentVersion)
    _proofs(environment, dict.fromkeys(KEYS, PROVED), version=other)

    on_default = _views(environment)
    off_default = _views(environment, version=other)

    assert on_default[LOOKUP_KEYS[0]]["state"] == "archived"
    assert on_default[REFUND_KEYS[0]]["state"] == "active"
    assert off_default[REFUND_KEYS[0]]["state"] == "validated"
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


def test_legacy_survives_missing_control(user, workspace, storage):
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

    assert views[REFUND_KEYS[0]]["state"] == "kept_as_written"
    assert views[REFUND_KEYS[0]]["legacy"] is True
    assert views[REFUND_KEYS[0]]["warning"] == "legacy_no_negative_control"
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
    assert "never ran" in views[REFUND_KEYS[0]]["warning_text"]
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
        "Kept as written: only a judge grades this case"
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
        "refunded reads the tool calls, which this agent does not give the harness; it will read Not measured."
    )
    assert views[REFUND_KEYS[1]]["warning"] is None
    assert lifecycle.runnable(views[REFUND_KEYS[0]]) is True


def test_list_shows_validated_on_a_named_candidate_pair(user, workspace, storage):
    from simulate.services.harness_provider import _lifecycle_for_list

    environment = _environment(user, workspace, storage, "lc-candidate-list")
    _suite(environment, _ten())
    other = _copy(environment.environment_version, HarnessEnvironmentVersion)
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


@pytest.mark.parametrize(
    ("code", "why", "detail"),
    [
        (
            "policy_conflict",
            'its expected outcome contradicts a cited rule:\n  - rule:0 "Refund once": it refunds twice',
            'rule:0 "Refund once": it refunds twice',
        ),
        ("not_ready", "the order the case cites is missing", "the order the case cites is missing"),
    ],
)
def test_a_listed_reason_reads_by_its_first_item_not_its_heading(code, why, detail):
    from simulate.services.harness_scenario_lifecycle import REASON_TEXTS, reason_text

    assert reason_text(code, {"why": why}) == REASON_TEXTS[code].format(why=detail)
