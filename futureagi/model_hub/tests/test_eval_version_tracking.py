"""
Regression tests for eval-usage version tracking.

Three independent concerns:

1. Migration guard: 0115_eval_usage_version_backfill is a RunPython
   migration whose first act is `apps.get_model("usage", "APICallLog")`.
   In an OSS build where `ee.usage` is absent from INSTALLED_APPS, that
   call would raise LookupError and fail the migration.  The migration
   guards this with a try/except and returns early.

2. Pinned-child version tracking: when `execute_composite_children_sync`
   runs a child that has a `pinned_version` set on its
   `CompositeEvalChild` link, `_execute_child` calls `run_eval_func` with
   `resolved_version=link.pinned_version`.  Inside `run_eval_func`,
   `source_config["version_id"]` and `source_config["version_number"]`
   must be set from *that pinned version*, not from the template's
   default.

3. `EvalTemplateVersion.objects.resolve_for_metric` — the single
   authoritative pin rule used by every stamping site.
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

MIGRATION_MODULE = "model_hub.migrations.0115_eval_usage_version_backfill"


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


def test_migration_returns_early_when_usage_app_absent():
    """The backfill must silently return when ee.usage is not installed.

    Simulates the OSS build by providing a fake app registry that raises
    LookupError for the 'usage' label.  If the guard in
    `backfill_apicalllog_version_info` is missing or broken, the function
    propagates LookupError and this test fails.
    """
    migration_module = importlib.import_module(MIGRATION_MODULE)
    func = migration_module.backfill_apicalllog_version_info

    fake_apps = _FakeAppsNoUsage()
    fake_schema_editor = SimpleNamespace()  # unused by the guard path

    # Must not raise — the LookupError must be caught and swallowed.
    result = func(fake_apps, fake_schema_editor)

    # RunPython functions return None on success; the early-return path
    # must also return None (not raise, not return a truthy sentinel).
    assert result is None


def test_migration_noop_reverse_code_exists():
    """The reverse_code must be migrations.RunPython.noop.

    This asserts the migration is safely reversible (it doesn't attempt
    to undo the backfill, which would be impossible to do correctly for
    historical data).
    """
    from django.db.migrations import RunPython

    migration_module = importlib.import_module(MIGRATION_MODULE)
    migration_class = migration_module.Migration

    run_python_ops = [
        op for op in migration_class.operations if isinstance(op, RunPython)
    ]
    assert len(run_python_ops) == 1, (
        f"Expected exactly one RunPython operation, found {len(run_python_ops)}"
    )
    op = run_python_ops[0]
    assert op.reverse_code is RunPython.noop, (
        "reverse_code must be RunPython.noop so it can be safely un-applied"
    )


# ---------------------------------------------------------------------------
# Test 2 — Pinned-child version tracking
# ---------------------------------------------------------------------------
#
# Code path under test:
#
#   composite_execution._execute_child
#     └─ run_eval_func(... resolved_version=link.pinned_version)
#          └─ tracked_version = kwargs.get("resolved_version")
#             source_config["version_id"] = str(tracked_version.id)
#             source_config["version_number"] = tracked_version.version_number
#             log_and_deduct_cost_for_api_request(config=source_config, ...)
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
    monkeypatch.setattr(
        evals_module,
        "log_and_deduct_cost_for_api_request",
        _fake_log_and_deduct,
    )

    # check_usage is imported locally inside run_eval_func each call, so we
    # patch it on the source module so the local `from ... import` picks up
    # the mock.
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
        lambda template, run_kwargs, **_kw: (None, run_kwargs),
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
        # default.  If the resolved_version guard in run_eval_func were absent
        # or broken, these would contain v2's values.
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

        Complementary test: confirms the get_default fallback in run_eval_func
        works and source_config carries v2 (the current default) when the
        child link has no pin.
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


# ── Eval usage response shape contract ───────────────────────────────────────

class TestEvalUsageResponseContract:
    """Verify the EvalUsageStatsView response shape.

    Each row is a typed cell-wrapped object: ``row_id`` is a plain string,
    known columns (``score``, ``result``, ``input`` …) are ``{cell_value:
    <typed>}`` wrappers, and dynamic per-eval ``input_var_<name>`` columns
    pass through via ``_ExtraFieldsMixin`` + ``additionalProperties: True``.
    """

    def test_eval_usage_table_accepts_typed_row_shape(self):
        import uuid

        from model_hub.serializers.contracts import EvalUsageStatsResponseSerializer

        payload = {
            "status": True,
            "result": {
                "template_id": str(uuid.uuid4()),
                "is_composite": False,
                "stats": {
                    "total_runs": 10,
                    "runs_period": 7,
                    "success_count": 7,
                    "error_count": 3,
                    "pass_rate": 0.7,
                },
                "chart": [],
                "table": [
                    {
                        "row_id": "abc-123",
                        "score": {"cell_value": 0.9},
                        "result": {"cell_value": "Passed"},
                        "input": {"cell_value": "hello world"},
                        "version": {"cell_value": "2"},
                        # Dynamic per-eval column — user-controlled key.
                        "input_var_response": {"cell_value": "my response"},
                    }
                ],
                "logs": {
                    "total": 1,
                    "page": 0,
                    "page_size": 25,
                },
            },
        }

        serializer = EvalUsageStatsResponseSerializer(data=payload)
        assert serializer.is_valid(), (
            f"EvalUsageStatsResponse rejected valid payload: {serializer.errors}"
        )

    def test_eval_usage_table_row_preserves_dynamic_keys_on_output(self):
        """_ExtraFieldsMixin.to_representation must copy dynamic ``input_var_<name>``
        cells through ``.data`` — without it DRF strips undeclared keys at
        serialize time and the FE grid renders empty rows even though the
        swagger contract says extras are allowed."""
        from model_hub.serializers.contracts import EvalUsageTableRowSerializer

        row = {
            "row_id": "abc-123",
            "score": {"cell_value": 0.9},
            "result": {"cell_value": "Passed"},
            "input_var_response": {"cell_value": "my response"},
            "input_var_prompt": {"cell_value": "my prompt"},
        }

        out = EvalUsageTableRowSerializer(instance=row).data

        assert out["row_id"] == "abc-123"
        assert out["score"] == {"cell_value": 0.9}
        assert out["result"] == {"cell_value": "Passed"}
        # The mixin-provided output-side passthrough — this is the behavior
        # that would otherwise produce empty rows on the live endpoint.
        assert out["input_var_response"] == {"cell_value": "my response"}
        assert out["input_var_prompt"] == {"cell_value": "my prompt"}


# ── Direct unit tests for resolve_for_metric / get_default ───────────────────

@pytest.mark.django_db
class TestResolveForMetric:
    """Unit tests for EvalTemplateVersion.objects.resolve_for_metric covering
    all branches: pin wins, deleted pin falls back, no pin → default,
    no default → highest version_number."""

    def _setup(self, organization, workspace):
        """Create a fresh template + UEM for each test."""
        template = EvalTemplate.no_workspace_objects.create(
            name=f"ver-test-{id(self)}",
            organization=organization,
            workspace=workspace,
            owner=OwnerChoices.USER.value,
            criteria="test criteria",
            model="turing_large",
            config={"output": "score", "eval_type_id": "DeterministicEvaluator"},
        )
        from model_hub.models.choices import DatasetSourceChoices
        from model_hub.models.develop_dataset import Dataset
        dataset = Dataset.objects.create(
            name=f"ver-test-ds-{id(self)}",
            organization=organization,
            workspace=workspace,
            source=DatasetSourceChoices.BUILD.value,
        )
        from model_hub.models.evals_metric import UserEvalMetric
        uem = UserEvalMetric.objects.create(
            name=f"uem-{id(self)}",
            dataset=dataset,
            organization=organization,
            workspace=workspace,
            template=template,
            status="NotStarted",
        )
        return template, uem

    def test_pin_returns_pin_not_default(self, db, organization, workspace, user):
        """Pinned non-deleted version is returned instead of the default."""
        template, uem = self._setup(organization, workspace)
        v1 = EvalTemplateVersion.objects.create(
            eval_template=template, version_number=1, is_default=False,
            config_snapshot={"criteria": "v1"},
        )
        EvalTemplateVersion.objects.create(
            eval_template=template, version_number=2, is_default=True,
            config_snapshot={"criteria": "v2"},
        )
        uem.pinned_version = v1
        uem.save(update_fields=["pinned_version"])
        uem.refresh_from_db()

        result = EvalTemplateVersion.objects.resolve_for_metric(uem)
        assert result is not None
        assert result.id == v1.id, "Pinned v1 should win over default v2"

    def test_deleted_pin_falls_back_to_default(self, db, organization, workspace, user):
        """Soft-deleted pinned version falls back to the active default."""
        template, uem = self._setup(organization, workspace)
        v1 = EvalTemplateVersion.objects.create(
            eval_template=template, version_number=1, is_default=False,
            config_snapshot={"criteria": "v1"}, deleted=True,
        )
        v2 = EvalTemplateVersion.objects.create(
            eval_template=template, version_number=2, is_default=True,
            config_snapshot={"criteria": "v2"},
        )
        uem.pinned_version = v1
        uem.save(update_fields=["pinned_version"])
        uem.refresh_from_db()

        result = EvalTemplateVersion.objects.resolve_for_metric(uem)
        assert result is not None
        assert result.id == v2.id, "Deleted pin should fall back to default v2"

    def test_no_pin_returns_default(self, db, organization, workspace, user):
        """No pin set → returns the default version."""
        template, uem = self._setup(organization, workspace)
        EvalTemplateVersion.objects.create(
            eval_template=template, version_number=1, is_default=False,
            config_snapshot={"criteria": "v1"},
        )
        v2 = EvalTemplateVersion.objects.create(
            eval_template=template, version_number=2, is_default=True,
            config_snapshot={"criteria": "v2"},
        )

        result = EvalTemplateVersion.objects.resolve_for_metric(uem)
        assert result is not None
        assert result.id == v2.id, "No pin → default v2"

    def test_no_default_marked_returns_highest_version(
        self, db, organization, workspace, user
    ):
        """When no version is marked default, highest version_number wins.

        This mirrors the FE picker fallback (EvalDetailPage) — both sides
        must agree on which version is 'current' when no flag is set.
        """
        template, uem = self._setup(organization, workspace)
        EvalTemplateVersion.objects.create(
            eval_template=template, version_number=1, is_default=False,
            config_snapshot={"criteria": "v1"},
        )
        v3 = EvalTemplateVersion.objects.create(
            eval_template=template, version_number=3, is_default=False,
            config_snapshot={"criteria": "v3"},
        )

        result = EvalTemplateVersion.objects.resolve_for_metric(uem)
        assert result is not None
        assert result.id == v3.id, "No default → highest version_number (v3)"

    def test_tracer_stamp_uses_template_default(
        self, db, organization, workspace, user
    ):
        """_stamp_eval_version (tracer) writes the template default into
        source_config — there is no FK path from CustomEvalConfig to
        UserEvalMetric so a per-metric pin cannot apply on tracer paths."""
        from tracer.utils.eval import _stamp_eval_version

        template, _ = self._setup(organization, workspace)
        v2 = EvalTemplateVersion.objects.create(
            eval_template=template, version_number=2, is_default=True,
            config_snapshot={"criteria": "v2"},
        )

        source_config = {}
        _stamp_eval_version(source_config, template)
        assert source_config.get("version_id") == str(v2.id)
        assert source_config.get("version_number") == 2
