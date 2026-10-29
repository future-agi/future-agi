"""Resolve soft-deleted F6 group URLs to an active group in the same project."""

from tracer.models.trace_error_analysis import TraceErrorGroup


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

    current = original
    visited = {original.pk}
    for _ in range(100):
        if not current.deleted:
            return {
                "requested_cluster_id": original.cluster_id,
                "resolved_cluster_id": current.cluster_id,
            }
        if current.redirect_to_id is None:
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
