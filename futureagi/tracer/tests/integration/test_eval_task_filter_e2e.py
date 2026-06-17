"""For every FilterCase: seed the corpus, create an EvalTask with the matching
filter and target type, run process_eval_task inline (engine stubbed), and assert
EvalLogger count == matched_rows × num_evals, with the right target_type set."""
import pytest
from django.db import transaction

from tracer.models.eval_task import EvalTask, EvalTaskStatus, RowType, RunType
from tracer.models.observation_span import EvalLogger, EvalTargetType
from tracer.tests.integration._filter_matrix import all_cases_for, FilterCase
from tracer.tests.integration._seed import SeededRow
from tracer.utils.eval_tasks import process_eval_task

pytestmark = [pytest.mark.integration, pytest.mark.slow, pytest.mark.django_db]

_TARGET_TYPE_TO_ROW_TYPE = {
    "spans": RowType.SPANS,
    "traces": RowType.TRACES,
    "sessions": RowType.SESSIONS,
    "voiceCalls": RowType.VOICE_CALLS,
}

_ROW_TYPE_TO_EVAL_TARGET = {
    RowType.SPANS: EvalTargetType.SPAN,
    RowType.TRACES: EvalTargetType.TRACE,
    RowType.SESSIONS: EvalTargetType.SESSION,
    RowType.VOICE_CALLS: EvalTargetType.SPAN,
}


_PLACEHOLDER_EVAL_CFG = "00000000-0000-0000-0000-000000000001"
_PLACEHOLDER_LABEL = "00000000-0000-0000-0000-000000000002"
_PLACEHOLDER_CHOICE_CFG = "00000000-0000-0000-0000-000000000003"
_ALL_CASES = all_cases_for(
    _PLACEHOLDER_EVAL_CFG, _PLACEHOLDER_LABEL, _PLACEHOLDER_CHOICE_CFG
)


def _rebind_case(
    case: FilterCase,
    eval_config_id: str,
    label_id: str,
    choice_eval_config_id: str,
) -> FilterCase:
    new_col_id = case.column_id
    if case.col_type == "EVAL_METRIC":
        if case.column_id == _PLACEHOLDER_EVAL_CFG:
            new_col_id = eval_config_id
        elif case.column_id == _PLACEHOLDER_CHOICE_CFG:
            new_col_id = choice_eval_config_id
    if case.col_type == "ANNOTATION" and case.column_id == _PLACEHOLDER_LABEL:
        new_col_id = label_id
    if new_col_id == case.column_id:
        return case
    return FilterCase(
        case_id=case.case_id,
        target_type=case.target_type,
        col_type=case.col_type,
        filter_type=case.filter_type,
        filter_op=case.filter_op,
        column_id=new_col_id,
        filter_value=case.filter_value,
        expected_predicate=case.expected_predicate,
    )


def _expected_unit_count(case: FilterCase, seeded: list[SeededRow]) -> int:
    if case.target_type in ("spans", "voiceCalls"):
        return sum(1 for r in seeded if case.expected_predicate(r))
    if case.target_type == "traces":
        return len({r.trace_id for r in seeded if case.expected_predicate(r)})
    if case.target_type == "sessions":
        return len({r.session_id for r in seeded if case.expected_predicate(r)})
    raise AssertionError(case.target_type)


@pytest.mark.parametrize("case", _ALL_CASES, ids=lambda c: c.case_id)
def test_eval_task_creates_correct_logger_count(
    case,
    seeded_corpus,
    voice_corpus,
    custom_eval_config_factory,
    stub_run_eval,
    inline_temporal,
    stub_cost_log,
):
    # voiceCalls → voice-only project; everything else → mixed corpus.
    corpus = voice_corpus if case.target_type == "voiceCalls" else seeded_corpus
    project = corpus.project
    seeded = corpus.rows
    case = _rebind_case(
        case,
        corpus.eval_config_id,
        corpus.annotation_label_id,
        corpus.choice_eval_config_id,
    )
    expected_units = _expected_unit_count(case, seeded)

    eval_a = custom_eval_config_factory(project=project)
    eval_b = custom_eval_config_factory(project=project)
    # Include project_id so the runner scopes the queryset.
    filters_dict = {"project_id": str(project.id), **case.to_filter_dict()}
    task = EvalTask.objects.create(
        project=project,
        filters=filters_dict,
        row_type=_TARGET_TYPE_TO_ROW_TYPE[case.target_type],
        run_type=RunType.HISTORICAL,
        sampling_rate=100.0,  # percentage in [0, 100], not a fraction
        spans_limit=10_000,
        status=EvalTaskStatus.PENDING,
    )
    task.evals.add(eval_a, eval_b)

    # ``._original_func`` bypasses the Temporal activity wrapper; savepoint
    # isolates parser exceptions from poisoning the outer test transaction.
    try:
        with transaction.atomic():
            process_eval_task._original_func(str(task.id))
    except Exception as exc:
        raise AssertionError(
            f"{case.case_id}: runner raised {type(exc).__name__}: {exc}"
        ) from exc

    actual = EvalLogger.objects.filter(eval_task_id=str(task.id)).count()
    expected_loggers = expected_units * 2  # two evals attached
    assert actual == expected_loggers, (
        f"{case.case_id}: row_type={task.row_type} expected {expected_loggers} "
        f"({expected_units} units × 2 evals), got {actual}"
    )

    if expected_loggers > 0:
        sample = EvalLogger.objects.filter(eval_task_id=str(task.id)).first()
        assert sample.target_type == _ROW_TYPE_TO_EVAL_TARGET[task.row_type]
