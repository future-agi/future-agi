"""Admission-only evaluator equivalence; local pins remain authorization keys."""

from collections import Counter
from copy import copy

from evaluations.run_settings import run_settings
from model_hub.services.experiment_run_writes import RunWriteError, _digest
from model_hub.services.persisted_run_comparison import _bindings


def _identity(metric, version):
    from model_hub.services.native_run_binding import native_evaluator_definition

    # Compare run settings, not credentials or references held in credential
    # fields. Copies preserve the original objects and local integrity hashes.
    metric = copy(metric)
    metric.config = run_settings(metric.config)
    metric.template = copy(metric.template)
    metric.template.config = run_settings(metric.template.config)
    version = copy(version)
    version.config_snapshot = run_settings(version.config_snapshot)
    return {
        "schema": 1,
        "template_id": str(metric.template_id),
        "evaluator_version_id": str(version.id),
        "version_number": version.version_number,
        "definition_hash": native_evaluator_definition(metric, version),
        "settings_hash": _digest(metric.config),
        "model_hash": _digest(metric.model),
        "explicit_pin": metric.pinned_version_id == version.id and not version.deleted,
    }


def _seal(pin, comparison):
    local = {key: value for key, value in pin.items() if key != "comparison"}
    settings = local.get("binding_config")
    if isinstance(settings, dict) and set(settings) != {"sha256"}:
        local["binding_config"] = run_settings(settings)
    return _digest({"local_pin": local, "comparison": comparison})


def with_comparison_identity(pin, metric, version, dataset):
    """Called only after the writer authorizes the local metric and version.

    A snapshot gets a source alias only for ONE exactly equivalent authorized
    source binding, resolved at admission. No name/row-order joins, mutable
    read-time inference, or acceptance of snapshot metrics by external writers.
    Missing or ambiguous matches keep their local key and cannot align with a
    source metric. Every execution-affecting field still participates in equality.
    The writer must finalize the complete set before persisting any of these pins.
    """
    from django.db.models import Q

    from model_hub.models.evals_metric import EvalTemplateVersion, UserEvalMetric

    identity = _identity(metric, version)
    key = str(metric.id)
    if metric.dataset_id != dataset.id:
        matches = []
        candidates = UserEvalMetric.no_workspace_objects.select_related(
            "template", "pinned_version"
        ).filter(
            Q(organization_id=dataset.organization_id)
            | Q(organization_id__isnull=True),
            Q(workspace_id=dataset.workspace_id) | Q(workspace_id__isnull=True),
            dataset=dataset,
            template_id=metric.template_id,
            deleted=False,
        )
        for source in candidates:
            resolved = EvalTemplateVersion.objects.resolve_for_metric(source)
            if (
                resolved is not None
                and resolved.id == version.id
                and _identity(source, resolved) == identity
            ):
                matches.append(str(source.id))
                if len(matches) > 1:
                    break
        if len(matches) == 1:
            key = matches[0]
    return {**pin, "comparison": {"metric_id": key, "identity": identity}}


def finalize_comparison_identities(pins):
    """Seal new pins only after resolving aliases across the full admitted set.

    Aliases target source metrics only. Colliding snapshot aliases fall back to
    their own local IDs, which cannot collide with a source alias. Replayed pins
    never enter this helper; reads use the saved mapping without live inference.
    """
    counts = Counter(pin["comparison"]["metric_id"] for pin in pins)
    result = []
    for pin in pins:
        comparison = dict(pin["comparison"])
        if counts[comparison["metric_id"]] > 1:
            comparison["metric_id"] = pin["metric_id"]
        result.append(
            {
                **pin,
                "comparison": {**comparison, "seal": _seal(pin, comparison)},
            }
        )
    return result


def _comparison(pin):
    saved = pin.get("comparison")
    if not isinstance(saved, dict) or set(saved) != {"metric_id", "identity", "seal"}:
        return None
    comparison = {key: saved[key] for key in ("metric_id", "identity")}
    if saved["seal"] != _seal(pin, comparison):
        return None
    return comparison


def comparison_keys(run):
    """Use only admitted aliases; unknown historical evidence stays literal."""
    keys = {}
    for local, pin in _bindings(run).items():
        saved = _comparison(pin)
        keys[local] = saved["metric_id"] if saved else local
    if len(set(keys.values())) != len(keys):
        raise RunWriteError("evaluator comparison mapping is ambiguous")
    return keys


def comparison_signature(run):
    keys = comparison_keys(run)
    result = {}
    for local, pin in _bindings(run).items():
        saved = _comparison(pin)
        result[keys[local]] = saved["identity"] if saved else pin
    return result


def comparison_trial(item, number, run, bindings):
    """Validate ALL local score references before mapping any display key."""
    from model_hub.services.results_grid import trial_record

    trial = trial_record(item, number, run, _bindings(run))
    keys = comparison_keys(run)
    trial["scores"] = {keys[key]: value for key, value in trial["scores"].items()}
    return trial


def project_selection(selection, runs):
    """Rows are already locally validated. Never rewrite items, pins or receipts."""
    for run in runs:
        rid = str(run.id)
        keys = comparison_keys(run)
        selection.bindings[rid] = {
            keys[key]: pin for key, pin in selection.bindings[rid].items()
        }
        for row in selection.rows:
            cell = row["results"].get(rid)
            if cell is None:
                continue
            for scored in [cell, *cell["trials_detail"]]:
                scored["scores"] = {
                    keys[key]: value for key, value in scored["scores"].items()
                }
