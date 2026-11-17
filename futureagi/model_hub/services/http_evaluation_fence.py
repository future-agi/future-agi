"""Admission-aware commit boundaries for classic evaluations of HTTP output.

An evaluator may outlive Stop and a new whole-run admission. Never hold a DB
lock while evaluating; recheck the immutable execution identity at each write.
Absent HTTP context deliberately preserves the existing prompt/agent behavior.
"""
from contextlib import contextmanager
from functools import wraps


class HttpEvaluationStopped(ValueError):
    """The HTTP evaluation no longer owns these result cells."""


@contextmanager
def http_evaluation_write(context):
    if context is None:
        yield
        return

    from django.db import transaction
    from model_hub.models.choices import StatusType
    from model_hub.services.experiment_submit import _locked_native_admission
    from model_hub.services.http_experiment_source import _http_execution
    from tfc.routers import force_primary

    required = {"experiment_id", "source_id", "admission_id", "dataset_id", "column_id"}
    if not isinstance(context, dict) or not required <= context.keys() or any(not context[key] for key in required):
        raise HttpEvaluationStopped("HTTP evaluation identity is incomplete.")
    with force_primary(), transaction.atomic():
        # Shared ordering with request admission and Stop: source dataset,
        # experiment, snapshot. Hold these locks through the actual commit.
        experiment, snapshot, state = _locked_native_admission(context["experiment_id"])
        if (snapshot is None or experiment.status != StatusType.RUNNING.value
                or state.get("state") != "accepted"
                or state.get("admission_id") != context["admission_id"]):
            raise HttpEvaluationStopped("HTTP evaluation was stopped or superseded.")
        try:
            _, _, source, _, column = _http_execution(
                context["experiment_id"], context["source_id"], context["admission_id"],
            )
        except ValueError as exc:
            raise HttpEvaluationStopped("HTTP evaluation source is no longer current.") from exc
        if (str(snapshot.pk) != str(context["dataset_id"])
                or str(column.pk) != str(context["column_id"])
                or source.legacy_prompt_config["_http_execution"].get("phase") != "running"):
            raise HttpEvaluationStopped("HTTP evaluation does not own this source output.")
        yield


def validate_http_eval_input(context, experiment_id, dataset_id, column_id, row_ids, metric_ids):
    if context is None:
        return
    for key, value in (("experiment_id", experiment_id), ("dataset_id", dataset_id), ("column_id", column_id)):
        if str(context.get(key)) != str(value):
            raise HttpEvaluationStopped("HTTP evaluation input does not match its execution.")
    with http_evaluation_write(context):
        from model_hub.models.experiments import ExperimentsTable
        from model_hub.services.http_experiment_source import _http_execution, _http_rows
        experiment, _, source, snapshot, _ = _http_execution(
            experiment_id, context["source_id"], context["admission_id"],
        )
        admitted_rows = {str(link.snapshot_row_id) for link in _http_rows(snapshot, source)}
        if not row_ids or not set(map(str, row_ids)) <= admitted_rows:
            raise HttpEvaluationStopped("HTTP evaluation rows are outside this snapshot.")
        # The scoped experiment relation, not a global metric lookup, is authority.
        metric_ids = set(map(str, metric_ids))
        admitted_metrics = set(map(str, ExperimentsTable.no_workspace_objects.get(
            pk=experiment.pk,
        ).user_eval_template_ids.filter(deleted=False).values_list("pk", flat=True)))
        if not metric_ids <= admitted_metrics:
            raise HttpEvaluationStopped("HTTP evaluations are outside this experiment.")


def fence_http_evaluation_write(function):
    @wraps(function)
    def guarded(self, *args, **kwargs):
        with http_evaluation_write(self.source_configs.get("http_context")):
            return function(self, *args, **kwargs)
    return guarded
