"""Resolve experiment scope before writes and recheck it before snapshotting."""

import hashlib

from model_hub.models.develop_dataset import Row
from model_hub.services.experiment_split_scope import split_member_ids


def prepare_run_scope(dataset, data, organization, workspace):
    """Return a stable, audited selection from the caller's dataset only.

    The caller admits the dataset through _scoped_dataset_queryset, including
    legacy NULL-workspace datasets visible in the default workspace. Do not
    narrow that admission to workspace equality here. Rows still must belong
    to this exact dataset and organization.

    A digest ordering avoids dependence on database ordering or Python PRNG
    versions. The selected source ids are persisted, so retries never resample.
    Metadata is stored independently and must not turn a plain create into a
    row-scoped create.
    """
    if dataset.organization_id != organization.id:
        raise ValueError("Dataset not found in organization.")
    if not any(key in data for key in ("row_ids", "sample", "split_ids")):
        return {}

    rows = Row.objects.filter(
        dataset=dataset,
        dataset__organization=organization,
        dataset__workspace_id=dataset.workspace_id,
        deleted=False,
    ).order_by("order", "id")
    source_ids = [str(row_id) for row_id in rows.values_list("id", flat=True)]
    requested = data.get("row_ids")
    if requested is not None:
        requested = [str(row_id) for row_id in requested]
        if not requested or len(set(requested)) != len(requested):
            raise ValueError("Choose distinct rows from this dataset.")
        if not set(requested).issubset(source_ids):
            raise ValueError("One or more rows are not available in this dataset.")
        requested_set = set(requested)
        eligible = [row_id for row_id in source_ids if row_id in requested_set]
    else:
        eligible = source_ids

    # Splits form a union; picked rows intersect it, then sampling runs last.
    split_ids = data.get("split_ids")
    if split_ids is not None:
        split_ids = [str(split_id) for split_id in split_ids]
        members = split_member_ids(dataset, split_ids)
        eligible = [row_id for row_id in eligible if row_id in members]
        if not eligible:
            raise ValueError("No live rows match the selected splits and rows.")

    sample = data.get("sample")
    selected = eligible
    if sample:
        if sample["size"] > len(eligible):
            raise ValueError("Sample size exceeds the number of eligible rows.")
        seed = sample["seed"]
        ranked = sorted(
            eligible,
            key=lambda row_id: hashlib.sha256(f"{seed}:{row_id}".encode()).digest(),
        )
        chosen = set(ranked[: sample["size"]])
        selected = [row_id for row_id in eligible if row_id in chosen]

    return {
        **({"split_ids": split_ids} if split_ids is not None else {}),
        "row_ids": requested,
        "sample": sample,
        "selected_row_ids": selected,
        "source_count": len(source_ids),
        "eligible_count": len(eligible),
        "selected_count": len(selected),
    }


def snapshot_source_rows(source_dataset, experiment):
    """Resolve rows before creating the snapshot; never widen a missing scope."""
    if experiment.dataset_id != source_dataset.id:
        raise ValueError("Snapshot dataset does not match the experiment.")
    rows = Row.objects.filter(
        dataset=source_dataset,
        dataset__organization_id=source_dataset.organization_id,
        dataset__workspace_id=source_dataset.workspace_id,
        deleted=False,
    ).order_by("order", "id")
    scope = getattr(experiment, "run_scope", None) or {}
    if scope:
        selected = scope.get("selected_row_ids")
        if not isinstance(selected, list):
            raise ValueError("Experiment row scope is invalid.")
        if "split_ids" in scope:
            members = split_member_ids(source_dataset, scope["split_ids"])
            if not set(selected).issubset(members):
                raise ValueError(
                    "Selected split membership changed before snapshotting."
                )
        selected_rows = list(
            rows.filter(id__in=selected).select_for_update(of=("self",))
        )
        if len(selected_rows) != len(selected):
            raise ValueError("One or more selected rows are no longer available.")
        return selected_rows
    return rows.iterator(chunk_size=5000)


def run_settings_writes(experiment, data):
    """What an edit's run settings would store, by field; nothing is written.

    {"run_metadata": ..., "run_policy": ...}, each only when it differs from
    what the experiment stores now; {} when the edit sends no setting or sends
    each one as it is stored. A cleared setting (error_policy None, limits {})
    returns to the default. Metadata replaces the person's keys and keeps the
    server's reserved ones.
    """
    from model_hub.services.run_metadata_public import RESERVED_METADATA_KEYS

    writes = {}
    if "metadata" in data:
        stored = experiment.run_metadata
        if not isinstance(stored, dict):
            stored = {}
        metadata = {
            **dict(data["metadata"]),
            **{
                key: value
                for key, value in stored.items()
                if key in RESERVED_METADATA_KEYS
            },
        }
        if metadata != stored:
            writes["run_metadata"] = metadata
    if "error_policy" in data or "limits" in data:
        stored = dict(experiment.run_policy or {})
        policy = dict(stored)
        for key in ("error_policy", "limits"):
            if key not in data:
                continue
            value = data[key]
            if value:
                policy[key] = dict(value) if key == "limits" else value
            else:
                policy.pop(key, None)
        if policy != stored:
            writes["run_policy"] = policy
    return writes


def run_settings_change(experiment, data):
    """Whether an edit would change the run settings the experiment stores.

    A setting sent as it is stored changes nothing (a page may send every
    setting on every edit), so it is no reason to refuse the edit.
    """
    return bool(run_settings_writes(experiment, data))


def apply_run_settings(experiment, data):
    """Save an edit's run settings on the locked experiment, for its next run.

    Call it before a run is staged: a run is admitted with the policy stored
    then (native_run_binding pins its hash). Finished runs keep what they ran
    with: each failed row's result cell records its own error policy and each
    row budget its own limits. What is stored is run_settings_writes'. The
    rows (run_scope) are never touched. Returns whether anything changed.
    """
    writes = run_settings_writes(experiment, data)
    for field, value in writes.items():
        setattr(experiment, field, value)
    if writes:
        experiment.save(update_fields=list(writes))
    return bool(writes)
