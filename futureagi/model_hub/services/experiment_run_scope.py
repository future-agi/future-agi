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
