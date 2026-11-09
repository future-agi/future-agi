"""Dataset eval defaults for new experiments, and the eval list's scoring view.

``set_experiment_defaults`` stores which of a dataset's evals new experiments
preselect ("Use in new experiments"). ``eval_scoring_views`` describes, for
each dataset eval, the version that runs and the pass rule a run applies, so
the eval list can say so (``get_evals_list``).

Tenancy: every read and write here is filtered by the caller's organization
and the dataset, never by thread-local workspace context alone.
"""

from __future__ import annotations

from collections.abc import Iterable
from uuid import UUID

from evaluations.engine.instance import resolve_pass_threshold
from model_hub.models.evals_metric import EvalTemplateVersion, UserEvalMetric


class ExperimentDefaultsError(ValueError):
    """The requested defaults name an eval this dataset does not have."""


def _dataset_evals(*, dataset, organization):
    return UserEvalMetric.no_workspace_objects.filter(
        dataset=dataset,
        organization=organization,
        show_in_sidebar=True,
        dataset__deleted=False,
    )


def set_experiment_defaults(*, dataset, organization, eval_ids: Iterable) -> list[str]:
    """Make ``eval_ids`` the dataset's experiment defaults; ``[]`` clears them.

    The list is the whole set: evals not in it stop being defaults. An id that
    is not one of this dataset's evals in this organization refuses the whole
    request and changes nothing.
    """
    if organization is None or str(dataset.organization_id) != str(organization.id):
        raise ExperimentDefaultsError("Dataset not found")

    wanted: list[str] = []
    for value in eval_ids or []:
        try:
            key = str(UUID(str(value)))
        except (TypeError, ValueError, AttributeError):
            raise ExperimentDefaultsError(f"Not an eval id: {value}") from None
        if key not in wanted:
            wanted.append(key)

    evals = _dataset_evals(dataset=dataset, organization=organization)
    found = {str(pk) for pk in evals.filter(id__in=wanted).values_list("id", flat=True)}
    unknown = [key for key in wanted if key not in found]
    if unknown:
        raise ExperimentDefaultsError(
            "These evals are not on this dataset: " + ", ".join(unknown)
        )

    evals.filter(id__in=wanted).update(is_experiment_default=True)
    evals.exclude(id__in=wanted).filter(is_experiment_default=True).update(
        is_experiment_default=False
    )
    return wanted


def _label(number):
    return f"v{number}" if number else None


def eval_scoring_views(user_evals: Iterable) -> dict:
    """Per user eval id: the version that runs and the pass rule a run applies.

    - ``version`` / ``version_number``: the version a dataset run uses (a live
      pin, else the template's default, else its newest), as
      ``EvalTemplateVersionManager.resolve_for_metric`` picks it.
    - ``pinned_version_number``: the pinned version's number, or None.
    - ``latest_version`` / ``latest_version_number``: the template's newest
      live version.
    - ``pass_threshold``: the threshold a run applies
      (``resolve_pass_threshold`` with the binding config and that version).
    - ``higher_is_better``: always True. A run fails a score below the
      threshold (``agentic_eval.core.utils.llm_payloads``); there is no
      lower-is-better rule to report.
    """
    user_evals = list(user_evals)
    template_ids = {ue.template_id for ue in user_evals if ue.template_id}
    pinned_ids = {ue.pinned_version_id for ue in user_evals if ue.pinned_version_id}

    live = list(
        EvalTemplateVersion.objects.filter(
            eval_template_id__in=template_ids, deleted=False
        ).only(
            "id", "eval_template_id", "version_number", "is_default", "pass_threshold"
        )
    )
    pinned = {
        v.id: v
        for v in EvalTemplateVersion.objects.filter(id__in=pinned_ids).only(
            "id", "eval_template_id", "version_number", "deleted", "pass_threshold"
        )
    }
    newest: dict = {}
    default: dict = {}
    for version in live:
        key = version.eval_template_id
        if key not in newest or version.version_number > newest[key].version_number:
            newest[key] = version
        if version.is_default:
            default[key] = version

    views: dict = {}
    for ue in user_evals:
        pin = pinned.get(ue.pinned_version_id) if ue.pinned_version_id else None
        if pin is not None and pin.deleted:
            pin_live = None
        else:
            pin_live = pin
        runs = pin_live or default.get(ue.template_id) or newest.get(ue.template_id)
        latest = newest.get(ue.template_id)
        threshold = None
        if ue.template is not None:
            threshold = resolve_pass_threshold(ue.template, ue.config or {}, runs)
        views[ue.id] = {
            "version": _label(runs.version_number if runs else None),
            "version_number": runs.version_number if runs else None,
            "pinned_version_number": pin.version_number if pin is not None else None,
            "latest_version": _label(latest.version_number if latest else None),
            "latest_version_number": latest.version_number if latest else None,
            "pass_threshold": threshold,
            "higher_is_better": True,
        }
    return views
