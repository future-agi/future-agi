"""Resolve soft-deleted F6 group URLs to an active group in the same project."""

from tracer.models.trace_error_analysis import TraceErrorGroup

MAX_REDIRECT_HOPS = 99  # The original resolver examined 100 rows, including the source.


def resolve_issue_redirect(cluster_id: str, project_ids: list[str]) -> dict | None:
    original = (
        TraceErrorGroup.all_objects.filter(
            cluster_id=cluster_id,
            project_id__in=project_ids,
            target_type="error_feed",
        )
        .order_by("deleted")
        .first()
    )
    if original is None:
        return None

    current = resolve_issue_row(original)
    if current is None:
        return None
    return {
        "requested_cluster_id": original.cluster_id,
        "resolved_cluster_id": current.cluster_id,
    }


def resolve_issue_row(original: TraceErrorGroup) -> TraceErrorGroup | None:
    current = original
    visited = {original.pk}
    for hops in range(MAX_REDIRECT_HOPS + 1):
        if not current.deleted:
            return current
        if hops == MAX_REDIRECT_HOPS or current.redirect_to_id is None:
            return None
        current = TraceErrorGroup.all_objects.filter(
            pk=current.redirect_to_id,
            project_id=original.project_id,
            target_type="error_feed",
        ).first()
        if current is None or current.pk in visited:
            return None
        visited.add(current.pk)
    return None
