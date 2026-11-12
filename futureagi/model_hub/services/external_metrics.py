"""External metrics: dataset metrics whose scores the caller computes (SRV-5).

The SDK scores trials in-process (local functions, trace checks, its own
judge calls, raw WER) and uploads those scores as ``score_refs`` on external
runs. Every score reference must name a dataset metric and the evaluator
version pinned at run creation, so each SDK score needs a real
``UserEvalMetric`` with a resolvable ``EvalTemplateVersion``.

An external metric is that pair with an ``EvalTemplate`` of ``eval_type``
``external``:

* it is created once and reused for the same (dataset, name, source_hash)
  and scoring definition (output type, direction, pass threshold); any change
  gives a new metric, so a run's pinned binding never moves under it;
* only the hash of the evaluator source is stored, never the source;
* the server never executes it. The metric model refuses any save that would
  queue or mark it as executing, and the evaluation engine refuses its
  template. On top of those two layers, the entry points that start or attach
  metrics for execution refuse it with a clear 400 (``external_execution_refusal``,
  ``external_template_refusal``), and the querysets that feed execution exclude
  it (``exclude_external``).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from django.db import transaction
from django.db.models import Exists, OuterRef, QuerySet

from accounts.models.user import User
from model_hub.models.choices import OwnerChoices, StatusType
from model_hub.models.develop_dataset import Dataset
from model_hub.models.evals_metric import (
    EXTERNAL_EVAL_TYPE,
    EvalTemplate,
    EvalTemplateVersion,
    UserEvalMetric,
)
from tfc.routers import force_primary

OUTPUT_TYPES = ("pass_fail", "percentage", "numeric")
# The platform's normalized output types; "numeric" (unbounded, e.g. latency
# or raw WER) has no platform equivalent and is recorded in the snapshot only.
_NORMALIZED = {"pass_fail": "pass_fail", "percentage": "percentage", "numeric": None}
_TEMPLATE_PREFIX = "external-"


class ExternalMetricError(ValueError):
    """Base class: the request to create or reuse an external metric failed."""


class ExternalMetricInvalid(ExternalMetricError):
    """The definition is invalid (HTTP 400)."""


class ExternalMetricConflict(ExternalMetricError):
    """The definition conflicts with an existing dataset metric (HTTP 409)."""


class ExternalMetricDatasetNotFound(ExternalMetricError):
    """The dataset disappeared while the request ran (HTTP 404)."""


class ExternalMetricExecutionError(RuntimeError):
    """A server path tried to execute a metric whose scores the caller owns."""


@dataclass(frozen=True)
class ExternalMetricDefinition:
    name: str
    output_type: str
    source_hash: str
    higher_is_better: bool = True
    pass_threshold: float | None = None

    def snapshot(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "output_type": self.output_type,
            "higher_is_better": self.higher_is_better,
            "pass_threshold": self.pass_threshold,
            "source_hash": self.source_hash,
        }

    def key(self, dataset_id: Any) -> str:
        payload = {"dataset_id": str(dataset_id), **self.snapshot()}
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ExternalMetricBinding:
    metric: UserEvalMetric
    version: EvalTemplateVersion
    created: bool


def is_external_template(template: EvalTemplate | None) -> bool:
    return getattr(template, "eval_type", None) == EXTERNAL_EVAL_TYPE


def _message(names: Iterable[str]) -> str:
    listed = ", ".join(sorted({str(name) for name in names}))
    return (
        f"External metric(s) {listed} are scored by the caller (the SDK) and are "
        "never executed by the server. Upload their scores as score_refs on an "
        "external experiment run instead."
    )


EXTERNAL_TEMPLATE_REFUSAL = (
    "External metrics are created through datasets/<dataset_id>/external-metrics/ "
    "and are never executed by the server. Upload their scores as score_refs on "
    "an external experiment run instead."
)


def external_template_refusal(template: EvalTemplate | None) -> str | None:
    """Return the refusal message when this template is an external metric's."""
    return EXTERNAL_TEMPLATE_REFUSAL if is_external_template(template) else None


def exclude_external(queryset: QuerySet) -> QuerySet:
    """Drop external metrics from a UserEvalMetric queryset that feeds execution."""
    return queryset.exclude(template__eval_type=EXTERNAL_EVAL_TYPE)


def refuse_external_execution(metrics: Iterable[UserEvalMetric]) -> None:
    """Raise when any metric is external. Call before executing metrics."""
    external = [m.name for m in metrics if is_external_template(m.template)]
    if external:
        raise ExternalMetricExecutionError(_message(external))


def external_execution_refusal(
    metric_ids: Iterable[Any], *, queryset: QuerySet | None = None
) -> str | None:
    """Return the refusal message when any of these metric ids is external.

    Pass the caller's scoped ``queryset`` so the message can only name metrics
    the caller can already see; without it every metric id is checked.
    """
    ids = [str(value) for value in metric_ids]
    if not ids:
        return None
    base = UserEvalMetric.all_objects if queryset is None else queryset
    names = list(
        base.filter(id__in=ids, template__eval_type=EXTERNAL_EVAL_TYPE).values_list(
            "name", flat=True
        )
    )
    return _message(names) if names else None


def external_metrics_queryset(dataset: Dataset) -> QuerySet:
    return (
        UserEvalMetric.no_workspace_objects.select_related("template", "pinned_version")
        .filter(
            dataset=dataset,
            deleted=False,
            template__deleted=False,
            template__eval_type=EXTERNAL_EVAL_TYPE,
        )
        .order_by("created_at", "id")
    )


def listable_external_metrics(dataset: Dataset) -> QuerySet:
    """External metrics that resolve to a version, for paging the list.

    ``resolve_for_metric`` falls back from a deleted pin to the template's
    live default, so a metric resolves exactly when its template has a live
    version. Filtering here keeps ``has_more`` true to the rows returned.
    """
    return external_metrics_queryset(dataset).filter(
        Exists(
            EvalTemplateVersion.objects.filter(
                eval_template_id=OuterRef("template_id"), deleted=False
            )
        )
    )


def resolved_version(metric: UserEvalMetric) -> EvalTemplateVersion | None:
    return EvalTemplateVersion.objects.resolve_for_metric(metric)


def metric_view(metric: UserEvalMetric, version: EvalTemplateVersion) -> dict:
    snapshot = (version.config_snapshot or {}).get(EXTERNAL_EVAL_TYPE, {})
    return {
        "metric_id": metric.id,
        "evaluator_version_id": version.id,
        "template_id": metric.template_id,
        "version_number": version.version_number,
        "dataset_id": metric.dataset_id,
        "name": metric.name,
        "output_type": snapshot.get("output_type"),
        "higher_is_better": snapshot.get("higher_is_better"),
        "pass_threshold": snapshot.get("pass_threshold"),
        "source_hash": snapshot.get("source_hash"),
        "created_at": metric.created_at,
    }


def _validate(definition: ExternalMetricDefinition) -> None:
    if definition.output_type not in OUTPUT_TYPES:
        raise ExternalMetricInvalid(f"output_type must be one of {OUTPUT_TYPES}")
    threshold = definition.pass_threshold
    if (
        threshold is not None
        and definition.output_type != "numeric"
        and not 0.0 <= threshold <= 1.0
    ):
        raise ExternalMetricInvalid(
            "pass_threshold must be between 0 and 1 for pass_fail and percentage"
        )
    digest = definition.source_hash
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise ExternalMetricInvalid("source_hash must be a lowercase sha256 hex digest")


@force_primary()
@transaction.atomic
def create_or_reuse_external_metric(
    *, dataset: Dataset, definition: ExternalMetricDefinition, user: User | None
) -> ExternalMetricBinding:
    """Return the dataset's external metric for this definition, creating it once.

    The dataset row is locked so concurrent identical requests return one
    metric. A non-external metric with the same name on the dataset is a
    conflict (scores would be ambiguous in the UI).
    """
    _validate(definition)
    locked = (
        Dataset.no_workspace_objects.select_for_update()
        .filter(pk=dataset.pk, deleted=False)
        .first()
    )
    if locked is None:
        raise ExternalMetricDatasetNotFound("dataset was not found")
    key = definition.key(locked.id)
    template_name = f"{_TEMPLATE_PREFIX}{key[:40]}"
    snapshot = {EXTERNAL_EVAL_TYPE: {**definition.snapshot(), "key": key}}

    existing = (
        external_metrics_queryset(locked).filter(template__name=template_name).first()
    )
    if existing is not None:
        version = resolved_version(existing)
        if version is not None and not version.deleted:
            return ExternalMetricBinding(existing, version, created=False)

    if (
        UserEvalMetric.no_workspace_objects.filter(
            dataset=locked, deleted=False, name=definition.name
        )
        .exclude(template__eval_type=EXTERNAL_EVAL_TYPE)
        .exists()
    ):
        raise ExternalMetricConflict(
            f"the dataset already has a metric named {definition.name!r} that is "
            "not an external metric"
        )

    template = EvalTemplate.no_workspace_objects.filter(
        name=template_name,
        organization=locked.organization,
        eval_type=EXTERNAL_EVAL_TYPE,
        deleted=False,
    ).first()
    normalized = _NORMALIZED[definition.output_type]
    if template is None:
        template = EvalTemplate(
            name=template_name,
            description=(
                f"External metric '{definition.name[:200]}': scored by the SDK, "
                "never executed by the server."
            ),
            organization=locked.organization,
            workspace=locked.workspace,
            owner=OwnerChoices.USER.value,
            eval_type=EXTERNAL_EVAL_TYPE,
            eval_tags=[EXTERNAL_EVAL_TYPE],
            config=snapshot,
            criteria="",
            choices=[],
            model=None,
            visible_ui=False,
            proxy_agi=False,
            allow_edit=False,
            allow_copy=False,
            output_type_normalized=normalized,
            pass_threshold=definition.pass_threshold,
            choice_scores=None,
            aggregation_enabled=False,
        )
        template.save()
    version = EvalTemplateVersion.objects.get_default(template)
    if version is None:
        version = EvalTemplateVersion.objects.create_version(
            template,
            config_snapshot=snapshot,
            user=user,
            organization=locked.organization,
            workspace=locked.workspace,
            output_type_normalized=normalized,
            pass_threshold=definition.pass_threshold,
            choice_scores=None,
            error_localizer_enabled=False,
            eval_tags=[EXTERNAL_EVAL_TYPE],
        )
    if existing is not None:
        existing.pinned_version = version
        existing.save(update_fields=["pinned_version", "updated_at"])
        return ExternalMetricBinding(existing, version, created=False)
    metric = UserEvalMetric(
        name=definition.name,
        organization=locked.organization,
        workspace=locked.workspace,
        template=template,
        dataset=locked,
        config=snapshot,
        status=StatusType.INACTIVE.value,
        show_in_sidebar=False,
        user=user,
        model=None,
        pinned_version=version,
    )
    metric.save()
    return ExternalMetricBinding(metric, version, created=True)
