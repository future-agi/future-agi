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

export function canDiscussQueueItem({
  canAnnotate,
  canReview,
  isBlockedAssignedToOther,
}) {
  return (canAnnotate || canReview) && !isBlockedAssignedToOther;
}

export function resolveAnnotationFooterProgress({ progress, isReviewMode }) {
  const userProgress = progress?.user_progress;
  const userCurrentPosition =
    userProgress?.current_position ?? userProgress?.currentPosition;

  if (
    !isReviewMode &&
    userProgress?.total > 0 &&
    Number.isFinite(Number(userCurrentPosition))
  ) {
    return {
      currentPosition: Number(userCurrentPosition),
      total: userProgress.total,
    };
  }

  return {
    currentPosition:
      progress?.current_position ?? progress?.currentPosition ?? 0,
    total: progress?.total ?? 0,
  };
}

export function annotationSubmitSuccessMessage({
  requiresReview,
  hasNextItem,
}) {
  const action = requiresReview ? "Submitted for review" : "Saved";
  return hasNextItem
    ? `${action}. Moved to next item.`
    : `${action}. No more items in this queue.`;
}

export function resolveCurrentDetailNavigation({
  detail,
  currentItemId,
  detailFetching,
  loadedScopeKey,
  currentScopeKey,
}) {
  if (
    !detail ||
    detailFetching ||
    !loadedScopeKey ||
    loadedScopeKey !== currentScopeKey
  ) {
    return { isCurrent: false, nextItemId: null, prevItemId: null };
  }

  const detailItemId = detail?.item?.id;
  if (
    detailItemId &&
    currentItemId &&
    String(detailItemId) !== String(currentItemId)
  ) {
    return { isCurrent: false, nextItemId: null, prevItemId: null };
  }

  return {
    isCurrent: true,
    nextItemId: detail?.next_item_id ?? detail?.nextItemId ?? null,
    prevItemId: detail?.prev_item_id ?? detail?.prevItemId ?? null,
  };
}

export function resolveSelectedAnnotatorScope({
  canReview,
  viewingAnnotatorId,
  currentUserId,
}) {
  const selectedAnnotatorId =
    viewingAnnotatorId && viewingAnnotatorId !== ALL_ANNOTATORS
      ? String(viewingAnnotatorId)
      : null;

  return {
    scopedAnnotatorId:
      canReview && selectedAnnotatorId ? selectedAnnotatorId : undefined,
    isViewingOtherAnnotator: Boolean(
      canReview &&
        selectedAnnotatorId &&
        currentUserId &&
        selectedAnnotatorId !== String(currentUserId),
    ),
  };
}

export function getSingleAssignedOtherAnnotatorId(
  assignedUsers,
  currentUserId,
) {
  const otherAssignedUsers = (assignedUsers || []).filter(
    (assignedUser) =>
      assignedUser?.id && String(assignedUser.id) !== String(currentUserId),
  );

  return otherAssignedUsers.length === 1
    ? String(otherAssignedUsers[0].id)
    : null;
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

export function annotationBelongsToCurrentUser(annotation, currentUserId) {
  if (!annotation || !currentUserId) return false;
  const annotatorId =
    annotation.annotator ??
    annotation.annotator_id ??
    annotation.annotatorId ??
    null;
  return annotatorId != null && String(annotatorId) === String(currentUserId);
}

export function canReviewCurrentQueueItem({
  item,
  annotations,
  currentUserId,
  isReviewMode,
}) {
  const submittedAnnotations = annotations || [];
  if (
    !isReviewMode ||
    item?.review_status !== "pending_review" ||
    submittedAnnotations.length === 0
  ) {
    return false;
  }
  return !submittedAnnotations.some((annotation) =>
    annotationBelongsToCurrentUser(annotation, currentUserId),
  );
}
