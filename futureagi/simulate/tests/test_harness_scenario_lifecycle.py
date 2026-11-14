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
        "active",
        True,
    )
    assert (views[LOOKUP_KEYS[0]]["state"], views[LOOKUP_KEYS[0]]["legacy"]) == (
        "active",
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
        and same[REFUND_KEYS[1]]["state"] == "validated"
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
        "active",
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
    _proofs(
        environment,
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
    )

    views = _views(environment)

    assert views[REFUND_KEYS[0]]["state"] == "active"
    assert views[REFUND_KEYS[0]]["legacy"] is True
    assert views[REFUND_KEYS[0]]["warning"] == "legacy_no_negative_control"
    assert views[REFUND_KEYS[1]]["state"] == "draft"
