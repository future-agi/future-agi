"""
Tests for PR #747 — eval usage version tracking.

Two independent concerns:

1. Migration guard: 0112_eval_usage_version_backfill is a RunPython
   migration whose first act is `apps.get_model("usage", "APICallLog")`.
   In an OSS build where `ee.usage` is absent from INSTALLED_APPS, that
   call would raise LookupError and fail the migration.  The migration
   guards this with a try/except and returns early.  This test invokes
   the migration function with a fake `apps` registry that raises
   LookupError (simulating the absence of the usage app) and asserts that
   the function returns normally rather than propagating the error.

2. Pinned-child version tracking: when `execute_composite_children_sync`
   runs a child that has a `pinned_version` set on its
   `CompositeEvalChild` link, `_execute_child` calls `run_eval_func` with
   `resolved_version=link.pinned_version`.  Inside `run_eval_func`,
   `source_config["version_id"]` and `source_config["version_number"]`
   must be set from *that pinned version*, not from the template's
   default.  This test asserts that the `config` dict written to
   `log_and_deduct_cost_for_api_request` carries the pinned version's
   identity — verifying the "forward" path that prevents version drift
   in usage logs.
"""

import importlib
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from model_hub.models.choices import OwnerChoices
from model_hub.models.evals_metric import (
    CompositeEvalChild,
    EvalTemplate,
    EvalTemplateVersion,
)


# ---------------------------------------------------------------------------
# Test 1 — Migration guard: OSS build with ee.usage absent
# ---------------------------------------------------------------------------


class _FakeAppsNoUsage:
    """Minimal stand-in for Django's historical app registry.

    Raises LookupError for "usage" (simulates ee.usage being absent from
    INSTALLED_APPS), and unconditionally blows up for any other lookup so
    the test would fail loudly if the guard is bypassed.
    """

    def get_model(self, app_label, model_name=None):
        # Support both (app_label, model_name) and "app_label.ModelName" forms.
        if model_name is None:
            app_label, model_name = app_label.split(".")
        if app_label == "usage":
            raise LookupError(
                f"No installed app with label '{app_label}'"
            )
        # For any model_hub lookup, the guard must have already returned,
        # so this branch should never be reached.
        raise AssertionError(
            f"Migration touched {app_label}.{model_name} "
            "even though the usage guard should have returned early."
        )


def test_migration_0112_returns_early_when_usage_app_absent():
    """0111 migration must silently return when ee.usage is not installed.

    Simulates the OSS build by providing a fake app registry that raises
    LookupError for the 'usage' label.  If the guard in
    `backfill_apicalllog_version_info` is missing or broken, the function
    propagates LookupError and this test fails.
    """
    migration_module = importlib.import_module(
        "model_hub.migrations.0112_eval_usage_version_backfill"
    )
    func = migration_module.backfill_apicalllog_version_info

    fake_apps = _FakeAppsNoUsage()
    fake_schema_editor = SimpleNamespace()  # unused by the guard path

    # Must not raise — the LookupError must be caught and swallowed.
    result = func(fake_apps, fake_schema_editor)

    # RunPython functions return None on success; the early-return path
    # must also return None (not raise, not return a truthy sentinel).
    assert result is None


def test_migration_0112_noop_reverse_code_exists():
    """The reverse_code for 0111 must be migrations.RunPython.noop.

    This asserts the migration is safely reversible (it doesn't attempt
    to undo the backfill, which would be impossible to do correctly for
    historical data).
    """
    from django.db.migrations import RunPython

    migration_module = importlib.import_module(
        "model_hub.migrations.0112_eval_usage_version_backfill"
    )
    migration_class = migration_module.Migration

    run_python_ops = [
        op for op in migration_class.operations if isinstance(op, RunPython)
    ]
    assert len(run_python_ops) == 1, (
        f"Expected exactly one RunPython operation, found {len(run_python_ops)}"
    )
    op = run_python_ops[0]
    assert op.reverse_code is RunPython.noop, (
        "0111 reverse_code must be RunPython.noop so it can be safely un-applied"
    )


# ---------------------------------------------------------------------------
# Test 2 — Pinned-child version tracking
# ---------------------------------------------------------------------------
#
# Code path under test:
#
#   composite_execution._execute_child
#     └─ run_eval_func(... resolved_version=link.pinned_version)
#          └─ _tracked_version = kwargs.get("resolved_version")   # L222 evals.py
#             source_config["version_id"] = str(_tracked_version.id)  # L229
#             source_config["version_number"] = _tracked_version.version_number  # L230
#             log_and_deduct_cost_for_api_request(config=source_config, ...)  # L263
#
# The test intercepts `log_and_deduct_cost_for_api_request` (via monkeypatch
# on the evals module attribute, same technique as the existing billing test in
# test_composite_wiring_phase_b.py) and inspects the `config` dict it receives.
# A second "newer" version is created and marked as the template default, so
# that if the guard were bypassed we'd see the wrong version_id.


@pytest.fixture
def child_eval(db, organization, workspace):
    return EvalTemplate.no_workspace_objects.create(
        name="pinning-child",
        organization=organization,
        workspace=workspace,
        owner=OwnerChoices.USER.value,
        config={
            "output": "score",
            "eval_type_id": "DeterministicEvaluator",
        },
        output_type_normalized="percentage",
        pass_threshold=0.5,
    )


@pytest.fixture
def pinned_version(db, child_eval, user, organization, workspace):
    """Version 1 — this is the version the CompositeEvalChild will be pinned to."""
    return EvalTemplateVersion.objects.create_version(
        eval_template=child_eval,
        config_snapshot={"criteria": "v1 criteria"},
        criteria="v1 criteria",
        model="turing_large",
        user=user,
        organization=organization,
        workspace=workspace,
    )


@pytest.fixture
def default_version(db, child_eval, pinned_version, user, organization, workspace):
    """Version 2 — created after v1 and promoted to default.

    When the composite runs with the child pinned to v1, run_eval_func must
    use v1 (not this newer default).  Its presence is what makes the
    test meaningful: if the guard were absent, source_config would carry
    v2's identity instead.
    """
    v2 = EvalTemplateVersion.objects.create_version(
        eval_template=child_eval,
        config_snapshot={"criteria": "v2 criteria — the default"},
        criteria="v2 criteria — the default",
        model="turing_large",
        user=user,
        organization=organization,
        workspace=workspace,
    )
    # Promote v2 to default; demote v1.
    EvalTemplateVersion.objects.filter(eval_template=child_eval).exclude(
        id=v2.id
    ).update(is_default=False)
    EvalTemplateVersion.objects.filter(id=v2.id).update(is_default=True)
    return v2


@pytest.fixture
def composite_with_pinned_child(
    db, organization, workspace, child_eval, pinned_version
):
    parent = EvalTemplate.no_workspace_objects.create(
        name="composite-pinning-parent",
        organization=organization,
        workspace=workspace,
        owner=OwnerChoices.USER.value,
        template_type="composite",
        aggregation_enabled=True,
        aggregation_function="weighted_avg",
        config={},
    )
    CompositeEvalChild.objects.create(
        parent=parent,
        child=child_eval,
        order=0,
        weight=1.0,
        pinned_version=pinned_version,
    )
    return parent


def _make_fake_eval_instance():
    """Return a FakeEvalInstance with the minimum interface run_eval_func needs."""

    class FakeEvalInstance:
        cost = {"total_cost": 0}
        token_usage = {}

        def run(self, **_kwargs):
            return SimpleNamespace(
                eval_results=[
                    {
                        "data": {"input": "hello"},
                        "failure": None,
                        "reason": "ok",
                        "runtime": 0.01,
                        "model": "turing_large",
                        "metrics": None,
                        "metadata": None,
                    }
                ]
            )

    return FakeEvalInstance()


def _install_run_eval_func_patches(monkeypatch, captured_configs, log_id="log-001"):
    """Install all the monkeypatches required to run run_eval_func in a unit test.

    Mirrors the setup used by test_composite_wiring_phase_b.py's billing test
    so both test files use the same faking strategy.

    Returns the fake log row so callers can inspect it if needed.
    """
    import model_hub.views.utils.evals as evals_module
    from tfc.constants.api_calls import APICallStatusChoices

    def _fake_log_and_deduct(organization, api_call_type, config=None, **_kw):
        captured_configs.append(dict(config) if config else {})
        return SimpleNamespace(
            log_id=log_id,
            config=json.dumps(config or {}),
            status=APICallStatusChoices.PROCESSING.value,
            input_token_count=0,
            save=MagicMock(),
        )

    # log_and_deduct_cost_for_api_request is a module-level name in evals.py
    # (assigned via `try: from ee.usage... except ImportError: ... = None`).
    # Patching on the module object is the canonical approach — see the
    # existing billing test in test_composite_wiring_phase_b.py (line 374).
    monkeypatch.setattr(
        evals_module,
        "log_and_deduct_cost_for_api_request",
        _fake_log_and_deduct,
    )

    # check_usage is imported locally inside run_eval_func each call, so we
    # patch it on the source module so the local `from ... import` picks up the mock.
    monkeypatch.setattr(
        "ee.usage.services.metering.check_usage",
        lambda *_args, **_kwargs: SimpleNamespace(allowed=True),
    )

    # Suppress the eval engine, field mapping, output formatting, and input
    # validation — all irrelevant to version tracking.
    monkeypatch.setattr(
        "model_hub.views.utils.evals.EvaluationRunner._create_eval_instance",
        lambda *_args, **_kwargs: _make_fake_eval_instance(),
    )
    monkeypatch.setattr(
        "model_hub.views.utils.evals.EvaluationRunner.map_fields",
        lambda *_args, **_kwargs: {"input": "hello"},
    )
    monkeypatch.setattr(
        "model_hub.views.utils.evals.EvaluationRunner.format_output",
        lambda *_args, **_kwargs: 0.9,
    )
    # validate_eval_inputs is imported locally inside run_eval_func; patch on
    # the source module so the local import gets the stub.
    monkeypatch.setattr(
        "model_hub.utils.eval_input_validation.validate_eval_inputs",
        lambda template, run_kwargs: (None, run_kwargs),
    )


@pytest.mark.django_db
class TestPinnedChildVersionTracking:
    """run_eval_func records the pinned version's identity in source_config."""

    def test_pinned_version_recorded_in_source_config_not_default(
        self,
        composite_with_pinned_child,
        child_eval,
        pinned_version,
        default_version,
        organization,
        workspace,
        monkeypatch,
    ):
        """The APICallLog config must carry the *pinned* version, not the template default.

        Setup:
        - child_eval has two versions: v1 (pinned on the link) and v2 (default).
        - CompositeEvalChild.pinned_version = v1.
        - _execute_child passes resolved_version=v1 to run_eval_func.

        Assert:
        - source_config["version_id"] == str(pinned_version.id)   (v1, not v2)
        - source_config["version_number"] == pinned_version.version_number
        """
        # Sanity: v2 is the current template default.
        current_default = EvalTemplateVersion.objects.get_default(child_eval)
        assert current_default.id == default_version.id, (
            "Fixture precondition: v2 must be the template default before running"
        )
        assert pinned_version.version_number < default_version.version_number, (
            "Fixture precondition: pinned version must be older (lower number) than default"
        )

        captured_configs: list[dict] = []
        _install_run_eval_func_patches(monkeypatch, captured_configs)

        from model_hub.utils.composite_execution import execute_composite_children_sync

        links = list(
            CompositeEvalChild.objects.filter(
                parent=composite_with_pinned_child
            ).select_related("child", "pinned_version")
        )
        assert len(links) == 1
        assert links[0].pinned_version_id == pinned_version.id

        with patch(
            "model_hub.utils.composite_execution._log_composite_usage",
            return_value=None,
        ):
            outcome = execute_composite_children_sync(
                parent=composite_with_pinned_child,
                child_links=links,
                mapping={"input": "hello"},
                config={},
                org=organization,
                workspace=workspace,
            )

        # The child must have completed without error.
        assert len(outcome.child_results) == 1
        assert outcome.child_results[0].status == "completed", (
            f"Child failed unexpectedly: {outcome.child_results[0].error}"
        )

        # Exactly one call to log_and_deduct (one child ran).
        assert len(captured_configs) == 1, (
            f"Expected 1 usage-log call, got {len(captured_configs)}"
        )

        recorded = captured_configs[0]

        # KEY ASSERTION: source_config must carry the pinned version, not the
        # default.  If the guard in run_eval_func (lines 222-230 of evals.py)
        # were absent or broken, these would contain v2's values.
        assert recorded.get("version_id") == str(pinned_version.id), (
            f"Expected version_id={pinned_version.id!r} (pinned v1), "
            f"got {recorded.get('version_id')!r} — "
            f"default v2 id={default_version.id!r}"
        )
        assert recorded.get("version_number") == pinned_version.version_number, (
            f"Expected version_number={pinned_version.version_number} (pinned v1), "
            f"got {recorded.get('version_number')} — "
            f"default v2 version_number={default_version.version_number}"
        )

    def test_unpinned_child_falls_back_to_template_default(
        self,
        db,
        organization,
        workspace,
        child_eval,
        pinned_version,
        default_version,
        monkeypatch,
    ):
        """Without a pinned version, run_eval_func must fall back to the template default.

        Complementary test to the one above: confirms the fallback branch at
        lines 224-226 of evals.py works and source_config carries v2 (the
        current default) when the child link has no pin.
        """
        parent = EvalTemplate.no_workspace_objects.create(
            name="composite-unpinned-parent",
            organization=organization,
            workspace=workspace,
            owner=OwnerChoices.USER.value,
            template_type="composite",
            aggregation_enabled=True,
            aggregation_function="weighted_avg",
            config={},
        )
        CompositeEvalChild.objects.create(
            parent=parent,
            child=child_eval,
            order=0,
            weight=1.0,
            pinned_version=None,  # no pin → must use template default
        )

        captured_configs: list[dict] = []
        _install_run_eval_func_patches(
            monkeypatch, captured_configs, log_id="log-002"
        )

        from model_hub.utils.composite_execution import execute_composite_children_sync

        links = list(
            CompositeEvalChild.objects.filter(
                parent=parent
            ).select_related("child", "pinned_version")
        )
        assert len(links) == 1
        assert links[0].pinned_version is None, (
            "Fixture precondition: link must have no pinned_version"
        )

        with patch(
            "model_hub.utils.composite_execution._log_composite_usage",
            return_value=None,
        ):
            execute_composite_children_sync(
                parent=parent,
                child_links=links,
                mapping={"input": "hello"},
                config={},
                org=organization,
                workspace=workspace,
            )

        assert len(captured_configs) == 1
        recorded = captured_configs[0]

        # Must fall back to the current default (v2), not the pinned v1.
        assert recorded.get("version_id") == str(default_version.id), (
            f"Expected default version_id={default_version.id!r} (v2), "
            f"got {recorded.get('version_id')!r}"
        )
        assert recorded.get("version_number") == default_version.version_number, (
            f"Expected default version_number={default_version.version_number} (v2), "
            f"got {recorded.get('version_number')}"
        )
