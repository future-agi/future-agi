"""Resolve the union of live split members inside one admitted dataset."""

from model_hub.models.dataset_splits import DatasetSplit, DatasetSplitMembership


def split_member_ids(dataset, split_ids):
    """Reject unavailable splits; never admit membership from another dataset."""
    ids = [str(split_id) for split_id in split_ids]
    if not ids or len(ids) > 100 or len(set(ids)) != len(ids):
        raise ValueError("Choose distinct splits from this dataset.")
    available = {
        str(split_id)
        for split_id in DatasetSplit.objects.filter(
            id__in=ids,
            dataset=dataset,
            dataset__organization_id=dataset.organization_id,
            dataset__workspace_id=dataset.workspace_id,
            dataset__deleted=False,
        ).values_list("id", flat=True)
    }
    if available != set(ids):
        raise ValueError("One or more splits are not available in this dataset.")
    members = DatasetSplitMembership.objects.filter(
        split_id__in=ids,
        split__dataset=dataset,
        row__dataset=dataset,
        row__deleted=False,
    ).values_list("row_id", flat=True)
    return {str(row_id) for row_id in members}
