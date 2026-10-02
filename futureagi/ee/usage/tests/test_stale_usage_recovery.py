"""Usage rows abandoned in ``processing`` are closed like a failed run.

Production (2026-09-28): 8 ``standalone_v2`` Protect rows in flight during the
18:08 UTC rollout stayed ``processing``; the usage views show them as running
forever. ``synthetic_dataset`` (908), ``simulate_tool_evaluation`` (4,494),
``run_prompt_gen`` and source-less resource rows are ``processing`` too, but
their code never closes them: those are finished, billed runs.

Only rows whose creator closes them are recovered. Production (2026-09-29): 76
``tracer`` rows come from the external-eval path, which bills at creation and
never closes its row, and 120 ``dataset_evaluation`` rows already hold their
eval result (the runner saves the result before the status). Both are left
alone and reported.
"""

import json
import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.management import call_command
from django.utils import timezone

from ee.usage.models.usage import APICallLog, APICallType
from ee.usage.services import stale_usage
from ee.usage.services.stale_usage import (
    STALE_AFTER_BY_SOURCE,
    close_stale_usage_row,
    recover_stale_usage_rows,
)
from ee.usage.utils.usage_entries import refund_cost_for_api_call
from model_hub.services import stale_work
from model_hub.services.stale_eval_recovery import DatasetEvalRecovery
from model_hub.services.stale_work import recoverable_sources
from model_hub.tasks.stale_work import recover_stale_work_activity
from model_hub.views.eval_runner import EvaluationRunner
from tfc.constants.api_calls import APICallStatusChoices, APICallTypeChoices
from tfc.utils.error_codes import get_error_message

pytestmark = [pytest.mark.requires_ee, pytest.mark.django_db]

PROCESSING = APICallStatusChoices.PROCESSING.value
ERROR = APICallStatusChoices.ERROR.value

# tracer/utils/eval.py: every Observe eval row names its custom eval config.
OBSERVE_EVAL_CONFIG = {
    "custom_eval_config_id": str(uuid.uuid4()),
    "mappings": {"input": "hi"},
    "source": "tracer",
}
# tracer/utils/external_eval.py: the external platform's config, billed at
# creation, never closed. Same ``tracer`` source.
EXTERNAL_EVAL_CONFIG = {
    "api_key": "<platform key>",
    "provider": "openai",
    "model": "gpt-4o",
    "is_futureagi_eval": False,
}


def _row(organization, *, source, age, config=None, deducted_cost=0):
    row = APICallLog.no_workspace_objects.create(
        organization=organization,
        status=PROCESSING,
        cost=Decimal("0.5"),
        deducted_cost=Decimal(deducted_cost),
        source=source,
        config=json.dumps(config or {"mappings": {"input": "hi"}}),
    )
    # created_at is auto_now_add; age the row the way time would.
    APICallLog.no_workspace_objects.filter(id=row.id).update(
        created_at=timezone.now() - age
    )
    row.refresh_from_db()
    return row


def _config(row):
    row.refresh_from_db()
    return json.loads(row.config) if isinstance(row.config, str) else row.config


def test_abandoned_row_is_closed_with_the_interrupted_reason(organization):
    row = _row(organization, source="standalone_v2", age=timedelta(hours=30))

    recovered = recover_stale_usage_rows(apply=True, limit=100).recovered

    assert [r.id for r in recovered] == [row.id]
    row.refresh_from_db()
    assert row.status == APICallStatusChoices.ERROR.value
    # Same shape and encoding the SDK path writes on failure.
    assert isinstance(row.config, str)
    assert _config(row) == {
        "mappings": {"input": "hi"},
        "output": {"output": None, "reason": get_error_message("RUN_INTERRUPTED")},
    }


@pytest.mark.parametrize("config", ["not json", "[1, 2]", "null", [1, 2]])
def test_a_config_that_is_not_a_json_object_is_kept_and_the_row_closes(
    organization, config
):
    """Such a row used to raise on every hourly tick and stay ``processing``."""
    row = _row(organization, source="standalone_v2", age=timedelta(hours=30))
    APICallLog.no_workspace_objects.filter(id=row.id).update(config=config)

    recovered = recover_stale_usage_rows(apply=True, limit=100).recovered

    assert [r.id for r in recovered] == [row.id]
    row.refresh_from_db()
    assert row.status == APICallStatusChoices.ERROR.value
    assert row.config == config


def test_row_younger_than_its_source_age_is_live_and_untouched(organization):
    live = _row(organization, source="standalone_v2", age=timedelta(hours=23))

    assert recover_stale_usage_rows(apply=True, limit=100).recovered == []

    live.refresh_from_db()
    assert live.status == PROCESSING


def test_older_than_cannot_shorten_a_source_age(organization):
    live = _row(
        organization,
        source="tracer",
        age=timedelta(hours=2),
        config=OBSERVE_EVAL_CONFIG,
    )

    assert (
        recover_stale_usage_rows(
            apply=True, older_than=timedelta(hours=1), limit=100
        ).recovered
        == []
    )
    live.refresh_from_db()
    assert live.status == PROCESSING


@pytest.mark.parametrize(
    "source",
    ["synthetic_dataset", "simulate_tool_evaluation", "run_prompt_gen", None],
)
def test_rows_their_code_never_closes_are_finished_work(organization, source):
    finished = _row(organization, source=source, age=timedelta(days=5))

    assert recover_stale_usage_rows(apply=True, limit=100).recovered == []

    finished.refresh_from_db()
    assert finished.status == PROCESSING


def test_dry_run_changes_nothing(organization):
    row = _row(organization, source="eval_playground", age=timedelta(days=2))

    listed = recover_stale_usage_rows(apply=False, limit=100).recovered

    assert [r.id for r in listed] == [row.id]
    row.refresh_from_db()
    assert row.status == PROCESSING
    assert "output" not in _config(row)


def test_row_closed_meanwhile_keeps_its_outcome(organization):
    row = _row(organization, source="standalone_v2", age=timedelta(hours=30))
    APICallLog.no_workspace_objects.filter(id=row.id).update(
        status=APICallStatusChoices.SUCCESS.value
    )

    assert close_stale_usage_row(row, now=timezone.now(), owes_refund=False) is None

    row.refresh_from_db()
    assert row.status == APICallStatusChoices.SUCCESS.value


def _refunds(row):
    return APICallLog.no_workspace_objects.filter(refund_parent_id=str(row.id))


@pytest.mark.parametrize("deducted_cost", [0, "0.5"])
def test_billing_matches_the_eval_runner_error_path(organization, deducted_cost):
    """No charge was taken for a postpaid row, so nothing is refunded; a legacy
    wallet row gets back exactly what the runner's own error path returns."""
    APICallType.objects.get_or_create(name=APICallTypeChoices.WALLET_REFUND.value)
    abandoned = _row(
        organization,
        source="dataset_evaluation",
        age=timedelta(hours=30),
        deducted_cost=deducted_cost,
    )
    failed = _row(
        organization,
        source="dataset_evaluation",
        age=timedelta(minutes=1),
        deducted_cost=deducted_cost,
    )
    runner = EvaluationRunner.__new__(EvaluationRunner)
    runner.user_eval_metric_id = "metric"
    runner._handle_api_call_status(failed, "error")

    recover_stale_usage_rows(apply=True, sources=["dataset_evaluation"], limit=100)
    recover_stale_usage_rows(apply=True, sources=["dataset_evaluation"], limit=100)

    abandoned.refresh_from_db()
    assert abandoned.status == failed.status == APICallStatusChoices.ERROR.value
    assert [r.cost for r in _refunds(abandoned)] == [r.cost for r in _refunds(failed)]
    assert _refunds(abandoned).count() == (1 if Decimal(deducted_cost) else 0)


def test_a_row_already_refunded_is_not_refunded_again(organization):
    """A legacy wallet row that already has a refund recorded is closed
    without a second one."""
    APICallType.objects.get_or_create(name=APICallTypeChoices.WALLET_REFUND.value)
    row = _row(
        organization,
        source="experiment",
        age=timedelta(hours=30),
        deducted_cost="0.5",
    )
    refund_cost_for_api_call(row)

    (listed,) = recover_stale_usage_rows(apply=False, limit=100).recovered
    (closed,) = recover_stale_usage_rows(apply=True, limit=100).recovered

    assert listed.refunded is closed.refunded is False
    row.refresh_from_db()
    assert row.status == APICallStatusChoices.ERROR.value
    assert _refunds(row).count() == 1


def test_the_usage_app_registers_every_source_for_recovery():
    assert set(STALE_AFTER_BY_SOURCE) <= set(recoverable_sources())


def test_sources_that_never_refund_on_error_are_not_refunded(organization):
    """The SDK path closes a failed row without a refund, so recovery does too,
    even for a legacy row that took a wallet deduction."""
    APICallType.objects.get_or_create(name=APICallTypeChoices.WALLET_REFUND.value)
    legacy = _row(
        organization,
        source="standalone_v2",
        age=timedelta(days=90),
        deducted_cost="0.5",
    )

    (listed,) = recover_stale_usage_rows(apply=False, limit=100).recovered
    (closed,) = recover_stale_usage_rows(apply=True, limit=100).recovered

    assert listed.refunded is closed.refunded is False
    assert not _refunds(legacy).exists()


def test_a_preview_run_is_closed_without_a_refund(organization):
    """Dataset eval previews (process_eval_for_single_row) log under
    dataset_evaluation with preview set, and their error path never refunds,
    so recovery does not either."""
    APICallType.objects.get_or_create(name=APICallTypeChoices.WALLET_REFUND.value)
    preview = _row(
        organization,
        source="dataset_evaluation",
        age=timedelta(days=3),
        config={"mappings": {"input": "hi"}, "preview": True},
        deducted_cost="0.5",
    )

    (listed,) = recover_stale_usage_rows(apply=False, limit=100).recovered
    (closed,) = recover_stale_usage_rows(apply=True, limit=100).recovered

    assert listed.refunded is closed.refunded is False
    preview.refresh_from_db()
    assert preview.status == APICallStatusChoices.ERROR.value
    assert not _refunds(preview).exists()


def test_command_dry_run_changes_nothing_then_apply_closes(organization, capsys):
    row = _row(organization, source="standalone_v2", age=timedelta(hours=30))

    _row(
        organization,
        source="standalone_v2",
        age=timedelta(days=5),
        config={"output": {"output": 0.9, "reason": "Relevant."}},
    )

    call_command("recover_stale_work", "--source", "standalone_v2")

    out = capsys.readouterr().out
    assert "dry run, nothing written" in out
    assert f"standalone_v2 {organization.id}: 1, 1" in out
    assert f"holds_result standalone_v2 {organization.id}: 1" in out
    row.refresh_from_db()
    assert row.status == PROCESSING

    call_command("recover_stale_work", "--source", "standalone_v2", "--apply")

    assert "Stale work (applied): 1 recovered" in capsys.readouterr().out
    row.refresh_from_db()
    assert row.status == APICallStatusChoices.ERROR.value


def test_a_tracer_row_from_the_external_eval_path_is_never_closed(organization):
    """``external_eval.py`` writes ``source="tracer"`` too, emits its usage event
    at once and never closes the row: 24 h later it is finished, billed work."""
    external = _row(
        organization,
        source="tracer",
        age=timedelta(days=5),
        config=EXTERNAL_EVAL_CONFIG,
    )
    observe = _row(
        organization, source="tracer", age=timedelta(days=5), config=OBSERVE_EVAL_CONFIG
    )

    recover_stale_usage_rows(apply=True, limit=100)

    external.refresh_from_db()
    observe.refresh_from_db()
    assert (external.status, _config(external)) == (PROCESSING, EXTERNAL_EVAL_CONFIG)
    assert observe.status == ERROR
    report = recover_stale_usage_rows(apply=False, limit=100)
    assert report.recovered == []
    assert [(r.id, r.reason) for r in report.excluded] == [
        (external.id, "creator_never_closes")
    ]


@pytest.mark.parametrize(
    "output",
    [
        {"output": ["Need More Details"], "reason": "Critical information is missing."},
        {"output": None, "reason": "The answer stays on topic."},
    ],
)
def test_a_row_holding_its_eval_result_is_untouched(organization, output):
    """The runner saves the result, then the status in a second write; a row
    left between the two holds a finished eval, not an interrupted one."""
    APICallType.objects.get_or_create(name=APICallTypeChoices.WALLET_REFUND.value)
    config = {"mappings": {"input": "hi"}, "output": output}
    finished = _row(
        organization,
        source="dataset_evaluation",
        age=timedelta(days=5),
        config=config,
        deducted_cost="0.01",
    )

    recover_stale_usage_rows(apply=True, limit=100)

    finished.refresh_from_db()
    assert (finished.status, _config(finished)) == (PROCESSING, config)
    assert not _refunds(finished).exists()
    report = recover_stale_usage_rows(apply=False, limit=100)
    assert [(r.id, r.reason) for r in report.excluded] == [
        (finished.id, "holds_result")
    ]


def test_an_output_type_in_the_config_is_not_a_result(organization):
    """run_eval_func copies the template config, whose ``output`` names the
    output type ("score"); that row is still abandoned work."""
    row = _row(
        organization,
        source="simulate",
        age=timedelta(days=5),
        config={"output": "score", "mappings": {"input": "hi"}},
    )

    recover_stale_usage_rows(apply=True, limit=100)

    row.refresh_from_db()
    assert row.status == ERROR


def test_excluded_rows_do_not_hold_back_eligible_ones(organization, monkeypatch):
    """Excluded rows stay ``processing``, so a batch of the oldest rows must
    page past them, or the rows behind them would never be reached."""
    monkeypatch.setattr(stale_usage, "_SCAN_PAGE", 1, raising=False)
    excluded = _row(
        organization,
        source="tracer",
        age=timedelta(days=6),
        config=EXTERNAL_EVAL_CONFIG,
    )
    abandoned = _row(organization, source="standalone_v2", age=timedelta(days=5))
    # Same start time: the page boundary falls between the two rows.
    APICallLog.no_workspace_objects.filter(id=abandoned.id).update(
        created_at=excluded.created_at
    )

    recover_stale_usage_rows(apply=True, limit=1)

    excluded.refresh_from_db()
    abandoned.refresh_from_db()
    assert (excluded.status, abandoned.status) == (PROCESSING, ERROR)


@pytest.mark.parametrize("apply", [False, True], ids=["report_only", "apply"])
def test_the_schedule_changes_nothing_until_recovery_is_switched_on(
    organization, settings, monkeypatch, apply
):
    """``STALE_WORK_RECOVERY_APPLY`` is off by default: the hourly tick then only
    reports. On, it closes eligible rows and nothing else."""
    settings.STALE_WORK_RECOVERY_APPLY = apply
    monkeypatch.setattr(
        stale_work,
        "recover_stale_dataset_evals",
        lambda **_: DatasetEvalRecovery(recovered=[]),
    )
    abandoned = _row(organization, source="standalone_v2", age=timedelta(hours=30))
    external = _row(
        organization,
        source="tracer",
        age=timedelta(days=5),
        config=EXTERNAL_EVAL_CONFIG,
    )
    finished = _row(
        organization,
        source="dataset_evaluation",
        age=timedelta(days=5),
        config={"output": {"output": "Passed", "reason": "Grounded."}},
    )

    result = recover_stale_work_activity._original_func()

    for row in (abandoned, external, finished):
        row.refresh_from_db()
    assert abandoned.status == (ERROR if apply else PROCESSING)
    assert (external.status, finished.status) == (PROCESSING, PROCESSING)
    org = str(organization.id)
    assert result == {
        "mode": "apply" if apply else "report_only",
        "recovered" if apply else "would_recover": {"standalone_v2": {org: 1}},
        "excluded": {
            "creator_never_closes": {"tracer": {org: 1}},
            "holds_result": {"dataset_evaluation": {org: 1}},
        },
        "skipped": {},
    }
