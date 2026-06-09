export const ALL_ANNOTATORS = "all";

export const WORKSPACE_MODES = {
  ANNOTATE: "annotate",
  REVIEW: "review",
};

export function resolveAnnotationWorkspaceMode({
  requestedMode,
  canReview,
  canAnnotate,
}) {
  if (requestedMode === WORKSPACE_MODES.REVIEW && canReview) {
    return WORKSPACE_MODES.REVIEW;
  }
  if (requestedMode === WORKSPACE_MODES.ANNOTATE && canAnnotate) {
    return WORKSPACE_MODES.ANNOTATE;
  }
  if (canReview && !canAnnotate) {
    return WORKSPACE_MODES.REVIEW;
  }
  return WORKSPACE_MODES.ANNOTATE;
}

export function canOpenSubmissionWorkspace({
  itemCount,
  canViewSubmissions,
  queueStatus,
}) {
  return (
    itemCount > 0 &&
    canViewSubmissions &&
    (queueStatus === "active" || queueStatus === "completed")
  );
}

export function canUseCompletedNavigation({
  isReviewMode,
  canAnnotate,
  queueStatus,
}) {
  return !isReviewMode && canAnnotate && queueStatus === "active";
}

export function resolveQueueItemWorkspaceMode({
  item,
  canViewSubmissions,
  canAnnotate,
}) {
  if (
    canViewSubmissions &&
    (item?.review_status === "pending_review" ||
      item?.status === "completed" ||
      !canAnnotate)
  ) {
    return WORKSPACE_MODES.REVIEW;
  }
  if (canAnnotate) {
    return WORKSPACE_MODES.ANNOTATE;
  }
  return WORKSPACE_MODES.REVIEW;
}
