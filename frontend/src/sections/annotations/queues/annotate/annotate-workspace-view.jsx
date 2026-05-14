import React, {
  useState,
  useCallback,
  useEffect,
  useRef,
  useReducer,
  useMemo,
} from "react";
import { useParams, useNavigate, useSearchParams } from "react-router-dom";
import {
  Box,
  Alert,
  Button,
  CircularProgress,
  Stack,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { useSnackbar } from "notistack";
import axios from "src/utils/axios";
import { useAuthContext } from "src/auth/hooks";
import { useSocket } from "src/hooks/use-socket";
import { useQueryClient } from "@tanstack/react-query";
import {
  annotateKeys,
  useAnnotateDetail,
  useAnnotationQueueDetail,
  useNextItem,
  useSubmitAnnotations,
  useCompleteItem,
  useSkipItem,
  useQueueProgress,
  useReviewItem,
  useAssignQueueItems,
  useItemDiscussion,
} from "src/api/annotation-queues/annotation-queues";
import AnnotateHeader from "./annotate-header";
import AnnotateFooter from "./annotate-footer";
import ContentPanel from "./content-panel";
import LabelPanel from "./label-panel";
import AnnotationComparisonPanel from "./annotation-comparison-panel";
import { CollaborationDrawer } from "./discussion-panel";
import ItemAssignmentPanel from "./item-assignment-panel";
import {
  ALL_ANNOTATORS,
  WORKSPACE_MODES,
  canUseCompletedNavigation,
  resolveAnnotationWorkspaceMode,
} from "./annotation-view-mode";
import useKeyboardShortcuts from "./use-keyboard-shortcuts";
import { QUEUE_ROLES, hasQueueRole, isQueueAnnotatorRole } from "../constants";

const MAX_HISTORY = 50;

function historyReducer(state, action) {
  switch (action.type) {
    case "init": {
      return { history: [action.id], index: 0, currentItemId: action.id };
    }
    case "push": {
      const next = [...state.history.slice(0, state.index + 1), action.id];
      if (next.length > MAX_HISTORY) next.shift();
      const newIndex = next.length - 1;
      return { history: next, index: newIndex, currentItemId: action.id };
    }
    case "prev": {
      if (state.index <= 0) return state;
      const prevIdx = state.index - 1;
      return {
        ...state,
        index: prevIdx,
        currentItemId: state.history[prevIdx],
      };
    }
    case "next": {
      if (state.index >= state.history.length - 1) return state;
      const nextIdx = state.index + 1;
      return {
        ...state,
        index: nextIdx,
        currentItemId: state.history[nextIdx],
      };
    }
    case "clear": {
      return { history: [], index: -1, currentItemId: null };
    }
    default:
      return state;
  }
}

function normalizeReviewPayload(payload) {
  if (payload && typeof payload === "object") {
    return {
      notes: payload.notes || "",
      labelComments: payload.labelComments || [],
    };
  }
  return { notes: payload || "", labelComments: [] };
}

function isDiscussionComment(comment) {
  return comment?.action === "comment";
}

function isOpenReviewStatus(status) {
  return status === "open" || status === "reopened";
}

function isBlockingReviewFeedback(comment) {
  return comment?.blocking || comment?.action === "request_changes";
}

function isOpenBlockingReviewFeedback(comment) {
  return (
    isBlockingReviewFeedback(comment) &&
    (!comment?.thread_status || isOpenReviewStatus(comment.thread_status))
  );
}

function shortId(value) {
  if (!value) return "";
  const text = String(value);
  return text.length > 8 ? text.slice(0, 8) : text;
}

function itemContextLabel(item, itemId) {
  const sourceType =
    item?.source_type || item?.sourceType || item?.source || item?.type;
  const typeLabel = sourceType
    ? String(sourceType).replaceAll("_", " ").toLowerCase()
    : "item";
  return `${typeLabel} ${shortId(item?.id || itemId)}`;
}

function commentScopeKey(labelId, targetAnnotatorId) {
  if (labelId && targetAnnotatorId) return `${labelId}:${targetAnnotatorId}`;
  if (labelId) return `label:${labelId}`;
  return "item";
}

function queueMembershipForUser(queueDetail, currentUserId) {
  if (!queueDetail || !currentUserId) return null;
  if (
    Array.isArray(queueDetail.viewer_roles) &&
    queueDetail.viewer_roles.length > 0
  ) {
    return {
      role: queueDetail.viewer_role,
      roles: queueDetail.viewer_roles,
    };
  }
  return (queueDetail.annotators || []).find(
    (a) => String(a.user_id) === String(currentUserId),
  );
}

function queueRoleAccess(membership) {
  return {
    canReview:
      hasQueueRole(membership, QUEUE_ROLES.REVIEWER) ||
      hasQueueRole(membership, QUEUE_ROLES.MANAGER),
    canAnnotate:
      hasQueueRole(membership, QUEUE_ROLES.ANNOTATOR) ||
      hasQueueRole(membership, QUEUE_ROLES.MANAGER),
  };
}

function itemAssignedToOther({ queueDetail, detail, currentUserId }) {
  const assignedUsers = detail?.item?.assigned_users || [];
  const hasAssignments = assignedUsers.length > 0;
  const isAssignedToMe = assignedUsers.some(
    (a) => String(a.id) === String(currentUserId),
  );
  return (
    queueDetail?.auto_assign === false && hasAssignments && !isAssignedToMe
  );
}

function isLockedForReview({
  detail,
  requiresReview,
  isReviewMode,
  currentUserId,
}) {
  if (
    isReviewMode ||
    !requiresReview ||
    detail?.item?.review_status !== "pending_review"
  ) {
    return false;
  }

  const currentUserHasAnnotations = (detail?.annotations || []).some(
    (annotation) => String(annotation?.annotator) === String(currentUserId),
  );
  const hasOpenReworkForCurrentUser = (detail?.review_comments || []).some(
    (comment) => {
      const isOpen =
        !comment?.thread_status ||
        ["open", "reopened"].includes(comment.thread_status);
      const targetsCurrentUser =
        !comment?.target_annotator_id ||
        String(comment.target_annotator_id) === String(currentUserId);
      return (
        comment?.action === "request_changes" &&
        comment?.blocking !== false &&
        isOpen &&
        targetsCurrentUser
      );
    },
  );

  return currentUserHasAnnotations && !hasOpenReworkForCurrentUser;
}

export default function AnnotateWorkspaceView() {
  const { queueId } = useParams();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const { user } = useAuthContext();
  const queryClient = useQueryClient();
  const { enqueueSnackbar } = useSnackbar();
  const { addMessageListener } = useSocket();
  const initialItemId = searchParams.get("itemId");

  const [navState, dispatch] = useReducer(historyReducer, {
    history: initialItemId ? [initialItemId] : [],
    index: initialItemId ? 0 : -1,
    currentItemId: initialItemId || null,
  });
  const { history: itemHistory, index: historyIndex, currentItemId } = navState;
  const [commentsOpen, setCommentsOpen] = useState(false);
  const [focusedCommentScope, setFocusedCommentScope] = useState(null);
  const focusTimeoutRef = useRef(null);

  useEffect(
    () => () => {
      window.clearTimeout(focusTimeoutRef.current);
    },
    [],
  );

  const { data: progress } = useQueueProgress(queueId, {
    staleTime: 0,
    refetchOnMount: "always",
    refetchOnWindowFocus: "always",
  });
  const {
    data: queueDetail,
    isFetching: isQueueDetailFetching,
    refetch: refetchQueueDetail,
  } = useAnnotationQueueDetail(queueId, {
    staleTime: 0,
    refetchOnMount: "always",
    refetchOnWindowFocus: "always",
    refetchInterval: 15000,
  });
  const currentUserId = String(
    user?.id ||
      user?.pk ||
      user?.user_id ||
      user?.userId ||
      (typeof window !== "undefined"
        ? window.sessionStorage.getItem("currentUserId")
        : "") ||
      "",
  );

  const myQueueMembership = useMemo(
    () => (user ? queueMembershipForUser(queueDetail, currentUserId) : null),
    [queueDetail, user, currentUserId],
  );

  const { canReview, canAnnotate } = queueRoleAccess(myQueueMembership);
  const canManageAssignments = hasQueueRole(
    myQueueMembership,
    QUEUE_ROLES.MANAGER,
  );
  const canDiscuss = canAnnotate || canReview;
  const requiresReview = queueDetail?.requires_review === true;
  const requestedMode = searchParams.get("mode");
  const workspaceMode = resolveAnnotationWorkspaceMode({
    requestedMode,
    canReview,
    canAnnotate,
  });
  const isReviewWorkspaceMode = workspaceMode === WORKSPACE_MODES.REVIEW;
  const canBrowseCompletedItems = canUseCompletedNavigation({
    isReviewMode: isReviewWorkspaceMode,
    canAnnotate,
    queueStatus: queueDetail?.status,
  });
  const includeCompletedItems =
    canBrowseCompletedItems && searchParams.get("includeCompleted") === "true";

  useEffect(() => {
    if (!queueDetail || canBrowseCompletedItems) return;
    if (searchParams.get("includeCompleted") !== "true") return;

    const nextParams = new URLSearchParams(searchParams);
    nextParams.delete("includeCompleted");
    setSearchParams(nextParams, { replace: true });
  }, [canBrowseCompletedItems, queueDetail, searchParams, setSearchParams]);
  const nextItemModeFilters = useMemo(
    () =>
      isReviewWorkspaceMode
        ? {
            viewMode: "review",
            ...(requiresReview
              ? { reviewStatus: "pending_review" }
              : { includeCompleted: true }),
          }
        : requiresReview
          ? {
              excludeReviewStatus: "pending_review",
              includeCompleted: includeCompletedItems,
            }
          : { includeCompleted: includeCompletedItems },
    [isReviewWorkspaceMode, requiresReview, includeCompletedItems],
  );
  const navigationModeParams = useMemo(
    () =>
      isReviewWorkspaceMode
        ? {
            view_mode: "review",
            ...(requiresReview
              ? { review_status: "pending_review" }
              : { include_completed: true }),
          }
        : requiresReview
          ? {
              exclude_review_status: "pending_review",
              ...(includeCompletedItems ? { include_completed: true } : {}),
            }
          : includeCompletedItems
            ? { include_completed: true }
            : {},
    [isReviewWorkspaceMode, requiresReview, includeCompletedItems],
  );
  const openOnlyNavigationParams = useMemo(
    () =>
      !isReviewWorkspaceMode && requiresReview
        ? { exclude_review_status: "pending_review" }
        : {},
    [isReviewWorkspaceMode, requiresReview],
  );
  const isResolvingInitialItem = !initialItemId && !currentItemId;

  // Fetch next item on mount (only if no specific item was requested).
  const {
    data: nextItemData,
    isLoading: nextLoading,
    isFetching: nextFetching,
    isFetchedAfterMount: nextFetchedAfterMount,
  } = useNextItem(queueId, {
    ...nextItemModeFilters,
    enabled: isResolvingInitialItem && !!queueDetail,
  });
  const isInitialItemLoading =
    isResolvingInitialItem &&
    (nextLoading || nextFetching || !nextFetchedAfterMount);

  // Set initial item from next-item query.
  useEffect(() => {
    if (
      isResolvingInitialItem &&
      nextFetchedAfterMount &&
      !nextFetching &&
      nextItemData?.id
    ) {
      dispatch({ type: "init", id: nextItemData.id });
    }
  }, [
    isResolvingInitialItem,
    nextFetchedAfterMount,
    nextFetching,
    nextItemData?.id,
  ]);

  const queueAnnotators = useMemo(
    () => (queueDetail?.annotators || []).filter(isQueueAnnotatorRole),
    [queueDetail?.annotators],
  );
  const queueMembers = useMemo(
    () => queueDetail?.annotators || [],
    [queueDetail?.annotators],
  );

  const [viewingAnnotatorId, setViewingAnnotatorId] = useState(null);

  useEffect(() => {
    if (!queueDetail) return;
    if (
      isReviewWorkspaceMode &&
      canReview &&
      currentUserId &&
      !viewingAnnotatorId
    ) {
      setViewingAnnotatorId(ALL_ANNOTATORS);
    } else if (!isReviewWorkspaceMode && viewingAnnotatorId !== null) {
      setViewingAnnotatorId(null);
    }
  }, [
    isReviewWorkspaceMode,
    canReview,
    currentUserId,
    queueDetail,
    viewingAnnotatorId,
  ]);

  const isViewingAllAnnotators =
    isReviewWorkspaceMode && viewingAnnotatorId === ALL_ANNOTATORS;
  const scopedAnnotatorId =
    isReviewWorkspaceMode && !isViewingAllAnnotators
      ? viewingAnnotatorId
      : undefined;
  const isViewingOtherAnnotator =
    isReviewWorkspaceMode &&
    !!viewingAnnotatorId &&
    !isViewingAllAnnotators &&
    String(viewingAnnotatorId) !== currentUserId;
  const detailEnabled =
    !!queueId &&
    !!currentItemId &&
    !!queueDetail &&
    (!isReviewWorkspaceMode || !!viewingAnnotatorId);

  // Reviewers/managers default to a comparison view. When a single annotator
  // is selected, request that annotator only so values never merge into one
  // editable form.
  const {
    data: detail,
    isLoading: detailLoading,
    isFetching: detailFetching,
    error: detailError,
    refetch: refetchAnnotateDetail,
  } = useAnnotateDetail(queueId, currentItemId, {
    annotatorId: scopedAnnotatorId,
    includeCompleted: includeCompletedItems,
    viewMode: isReviewWorkspaceMode ? "review" : undefined,
    reviewStatus:
      isReviewWorkspaceMode && requiresReview ? "pending_review" : undefined,
    excludeReviewStatus:
      !isReviewWorkspaceMode && requiresReview ? "pending_review" : undefined,
    enabled: detailEnabled,
    staleTime: 0,
    refetchOnMount: "always",
    refetchOnWindowFocus: "always",
  });
  const { data: liveDiscussion } = useItemDiscussion(queueId, currentItemId, {
    enabled: detailEnabled,
    refetchInterval: commentsOpen ? 3000 : 10000,
    refetchIntervalInBackground: false,
  });

  useEffect(() => {
    if (!addMessageListener || !queueId || !currentItemId) {
      return undefined;
    }

    return addMessageListener((message) => {
      if (message?.type !== "annotation_discussion_updated") {
        return;
      }
      const data = message.data || {};
      if (
        String(data.queue_id) !== String(queueId) ||
        String(data.item_id) !== String(currentItemId)
      ) {
        return;
      }
      queryClient.invalidateQueries({
        queryKey: annotateKeys.discussion(queueId, currentItemId),
      });
    });
  }, [addMessageListener, currentItemId, queueId, queryClient]);

  const lastLoadedAnnotatorIdRef = useRef(null);
  useEffect(() => {
    if (detail && !detailFetching) {
      lastLoadedAnnotatorIdRef.current = scopedAnnotatorId || null;
    }
  }, [detail, detailFetching, scopedAnnotatorId]);

  const isAnnotatorSwitchPending =
    detailFetching &&
    !!scopedAnnotatorId &&
    !!lastLoadedAnnotatorIdRef.current &&
    String(lastLoadedAnnotatorIdRef.current) !== String(scopedAnnotatorId);

  // Prefetch adjacent items for instant navigation
  useEffect(() => {
    if (!detail || !queueId) return;
    const nextId = detail.next_item_id;
    const prevId = detail.prev_item_id;
    const prefetchParams = {
      ...(scopedAnnotatorId ? { annotator_id: scopedAnnotatorId } : {}),
      ...navigationModeParams,
    };
    const requestOptions = Object.keys(prefetchParams).length
      ? { params: prefetchParams }
      : undefined;
    const detailFilters = Object.keys(navigationModeParams).length
      ? navigationModeParams
      : undefined;
    if (nextId) {
      queryClient.prefetchQuery({
        queryKey: annotateKeys.detail(
          queueId,
          nextId,
          scopedAnnotatorId,
          detailFilters,
        ),
        queryFn: () =>
          axios.get(
            `/model-hub/annotation-queues/${queueId}/items/${nextId}/annotate-detail/`,
            requestOptions,
          ),
        staleTime: 1000 * 60 * 2,
      });
    }
    if (prevId) {
      queryClient.prefetchQuery({
        queryKey: annotateKeys.detail(
          queueId,
          prevId,
          scopedAnnotatorId,
          detailFilters,
        ),
        queryFn: () =>
          axios.get(
            `/model-hub/annotation-queues/${queueId}/items/${prevId}/annotate-detail/`,
            requestOptions,
          ),
        staleTime: 1000 * 60 * 2,
      });
    }
  }, [
    detail,
    queueId,
    queryClient,
    scopedAnnotatorId,
    includeCompletedItems,
    navigationModeParams,
  ]);

  const labelPanelRef = useRef(null);
  const isDirtyRef = useRef(false);
  const handleDirtyChange = useCallback((dirty) => {
    isDirtyRef.current = dirty;
  }, []);
  const confirmDiscardUnsaved = useCallback((message) => {
    if (!isDirtyRef.current) return true;
    const canDiscard = window.confirm(
      message || "You have unsaved changes. Continue anyway?",
    );
    if (canDiscard) isDirtyRef.current = false;
    return canDiscard;
  }, []);

  const { mutate: submitAnnotations, isPending: isSubmitting } =
    useSubmitAnnotations();
  const { mutate: completeItem, isPending: isCompleting } = useCompleteItem();
  const { mutate: skipItem, isPending: isSkipping } = useSkipItem();
  const { mutate: reviewItem, isPending: isReviewing } = useReviewItem();
  const { mutate: assignItems, isPending: isAssigningItem } =
    useAssignQueueItems();

  const isPendingReview = detail?.item?.review_status === "pending_review";
  const hasSubmittedAnnotations = (detail?.annotations || []).length > 0;
  const isCurrentItemCompleted = detail?.item?.status === "completed";
  const completedByCurrentUser =
    !isReviewWorkspaceMode &&
    isCurrentItemCompleted &&
    (detail?.annotations || []).length > 0;
  const showReviewActions =
    isReviewWorkspaceMode && isPendingReview && hasSubmittedAnnotations;
  const isAnnotateLockedForReview = isLockedForReview({
    detail,
    requiresReview,
    isReviewMode: isReviewWorkspaceMode,
    currentUserId,
  });

  // Item is explicitly assigned to someone else (only blocks in manual-assignment mode)
  const assignedUsers = detail?.item?.assigned_users || [];
  const hasAssignments = assignedUsers.length > 0;
  const isAssignedToMe = assignedUsers.some(
    (a) => String(a.id) === currentUserId,
  );
  const isManualAssignment = queueDetail?.auto_assign === false;
  const assignedToName =
    hasAssignments && !isAssignedToMe
      ? assignedUsers
          .map((a) => a.name)
          .filter(Boolean)
          .join(", ") || "other annotators"
      : null;
  // Unassigned manual items are claimable. Only explicitly assigned-to-other
  // items should become read-only/blocked in the annotate workspace.
  const cannotAnnotate = itemAssignedToOther({
    queueDetail,
    detail,
    currentUserId,
  });
  // Reviewers/managers see assigned-to-other items in read-only mode (when
  // not actively reviewing). Other members are fully blocked.
  const isViewOnlyForReviewer =
    !isReviewWorkspaceMode && canReview && cannotAnnotate;
  const isBlockedAssignedToOther =
    !isReviewWorkspaceMode && !canReview && cannotAnnotate;
  // Backwards-compatible flag passed to header for disabling Skip.
  const isAssignedToOther = !isReviewWorkspaceMode && cannotAnnotate;
  const effectiveQueueStatus = detail?.queue?.status || queueDetail?.status;
  const canUseCompletedControls = canUseCompletedNavigation({
    isReviewMode: isReviewWorkspaceMode,
    canAnnotate,
    queueStatus: effectiveQueueStatus,
  });
  const labelPanelReadOnly =
    !canAnnotate ||
    isViewOnlyForReviewer ||
    isViewingOtherAnnotator ||
    isAnnotatorSwitchPending ||
    isAnnotateLockedForReview;
  const labelPanelReadOnlyReason = isAnnotatorSwitchPending
    ? "Loading selected annotator..."
    : isAnnotateLockedForReview
      ? "This item is waiting for review. It can be edited after a reviewer requests changes."
      : isViewingOtherAnnotator
        ? "Viewing another annotator's submissions (read only)"
        : isViewOnlyForReviewer
          ? `Assigned to ${assignedToName || "another annotator"} — view only`
          : !canAnnotate
            ? "You do not have annotator access for this queue"
            : null;

  const isSubmittingRef = useRef(false);
  const [isChangingCompletedVisibility, setIsChangingCompletedVisibility] =
    useState(false);

  const handleViewingAnnotatorChange = useCallback(
    (id) => {
      const nextAnnotatorId = id || ALL_ANNOTATORS;
      if (String(nextAnnotatorId) === String(viewingAnnotatorId || "")) {
        return;
      }
      if (
        !confirmDiscardUnsaved(
          "You have unsaved changes. Switch annotator anyway?",
        )
      ) {
        return;
      }
      setViewingAnnotatorId(nextAnnotatorId);
    },
    [viewingAnnotatorId, confirmDiscardUnsaved],
  );

  const handleAssignCurrentItem = useCallback(
    ({ itemIds, userId, userIds, action }) => {
      assignItems({
        queueId,
        itemIds,
        userId,
        userIds,
        action,
        assignees: queueAnnotators,
      });
    },
    [assignItems, queueAnnotators, queueId],
  );

  const handleWorkspaceModeChange = useCallback(
    (_, nextMode) => {
      if (!nextMode || nextMode === workspaceMode) return;
      if (
        !confirmDiscardUnsaved("You have unsaved changes. Switch mode anyway?")
      ) {
        return;
      }
      const nextParams = new URLSearchParams(searchParams);
      nextParams.set("mode", nextMode);
      nextParams.delete("itemId");
      if (nextMode === WORKSPACE_MODES.REVIEW) {
        nextParams.delete("includeCompleted");
      }
      setSearchParams(nextParams, { replace: true });
      isDirtyRef.current = false;
      dispatch({ type: "clear" });
    },
    [workspaceMode, searchParams, setSearchParams, confirmDiscardUnsaved],
  );

  const handleIncludeCompletedChange = useCallback(
    async (event) => {
      if (
        !canUseCompletedControls ||
        isAssignedToOther ||
        isAnnotateLockedForReview ||
        isAnnotatorSwitchPending
      ) {
        return;
      }

      if (
        !confirmDiscardUnsaved(
          "You have unsaved changes. Change navigation mode anyway?",
        )
      ) {
        return;
      }

      setIsChangingCompletedVisibility(true);
      try {
        const [freshQueueResult, freshDetailResult] = await Promise.all([
          refetchQueueDetail(),
          detailEnabled
            ? refetchAnnotateDetail()
            : Promise.resolve({ data: detail }),
        ]);
        const freshQueueDetail = freshQueueResult?.data || queueDetail;
        const freshDetail = freshDetailResult?.data || detail;
        const freshMembership = queueMembershipForUser(
          freshQueueDetail,
          currentUserId,
        );
        const { canReview: freshCanReview, canAnnotate: freshCanAnnotate } =
          queueRoleAccess(freshMembership);
        const freshMode = resolveAnnotationWorkspaceMode({
          requestedMode,
          canReview: freshCanReview,
          canAnnotate: freshCanAnnotate,
        });
        const freshIsReviewMode = freshMode === WORKSPACE_MODES.REVIEW;
        const freshQueueStatus =
          freshDetail?.queue?.status || freshQueueDetail?.status;
        const freshCanBrowseCompleted = canUseCompletedNavigation({
          isReviewMode: freshIsReviewMode,
          canAnnotate: freshCanAnnotate,
          queueStatus: freshQueueStatus,
        });
        const freshAssignedToOther = itemAssignedToOther({
          queueDetail: freshQueueDetail,
          detail: freshDetail,
          currentUserId,
        });
        const freshLockedForReview = isLockedForReview({
          detail: freshDetail,
          requiresReview: freshQueueDetail?.requires_review === true,
          isReviewMode: freshIsReviewMode,
          currentUserId,
        });

        if (
          !freshCanBrowseCompleted ||
          freshAssignedToOther ||
          freshLockedForReview
        ) {
          const refreshedParams = new URLSearchParams(searchParams);
          refreshedParams.delete("includeCompleted");
          setSearchParams(refreshedParams, { replace: true });
          enqueueSnackbar("Queue access changed. Refreshed navigation state.", {
            variant: "info",
          });
          return;
        }
      } catch (err) {
        enqueueSnackbar(
          err?.response?.data?.detail ||
            err?.message ||
            "Couldn't refresh queue access before changing completed visibility.",
          { variant: "error" },
        );
        return;
      } finally {
        setIsChangingCompletedVisibility(false);
      }

      const nextIncludeCompleted = event.target.checked;
      const nextParams = new URLSearchParams(searchParams);
      if (nextIncludeCompleted) {
        nextParams.set("includeCompleted", "true");
      } else {
        nextParams.delete("includeCompleted");
      }
      if (currentItemId) {
        nextParams.set("itemId", currentItemId);
      } else {
        nextParams.delete("itemId");
      }
      setSearchParams(nextParams, { replace: true });
      isDirtyRef.current = false;

      if (nextIncludeCompleted || !currentItemId) {
        return;
      }

      // Leaving "Show completed" should also leave the all-items browsing
      // history. Otherwise a completed item visited while the toggle was on
      // remains reachable through local Previous/Next even though the mode now
      // says completed work is hidden.
      if (detail?.item?.status !== "completed") {
        dispatch({ type: "init", id: currentItemId });
        return;
      }

      setIsChangingCompletedVisibility(true);
      try {
        const nextRes = await axios.get(
          `/model-hub/annotation-queues/${queueId}/items/next-item/`,
          {
            params: {
              exclude: currentItemId,
              ...openOnlyNavigationParams,
            },
          },
        );
        const nextItem =
          nextRes?.data?.data?.item ||
          nextRes?.data?.result?.item ||
          nextRes?.data?.item;
        if (nextItem?.id) {
          const itemParams = new URLSearchParams(nextParams);
          itemParams.set("itemId", nextItem.id);
          setSearchParams(itemParams, { replace: true });
          dispatch({ type: "init", id: nextItem.id });
          return;
        }

        const prevRes = await axios.get(
          `/model-hub/annotation-queues/${queueId}/items/next-item/`,
          {
            params: {
              before: currentItemId,
              ...openOnlyNavigationParams,
            },
          },
        );
        const prevItem =
          prevRes?.data?.data?.item ||
          prevRes?.data?.result?.item ||
          prevRes?.data?.item;
        if (prevItem?.id) {
          const itemParams = new URLSearchParams(nextParams);
          itemParams.set("itemId", prevItem.id);
          setSearchParams(itemParams, { replace: true });
          dispatch({ type: "init", id: prevItem.id });
        } else {
          const emptyParams = new URLSearchParams(nextParams);
          emptyParams.delete("itemId");
          setSearchParams(emptyParams, { replace: true });
          dispatch({ type: "clear" });
        }
      } catch (err) {
        enqueueSnackbar(
          err?.response?.data?.detail ||
            err?.message ||
            "Couldn't update completed item visibility.",
          { variant: "error" },
        );
      } finally {
        setIsChangingCompletedVisibility(false);
      }
    },
    [
      canUseCompletedControls,
      isAssignedToOther,
      isAnnotateLockedForReview,
      isAnnotatorSwitchPending,
      refetchQueueDetail,
      detailEnabled,
      refetchAnnotateDetail,
      detail,
      queueDetail,
      currentUserId,
      requestedMode,
      searchParams,
      setSearchParams,
      confirmDiscardUnsaved,
      currentItemId,
      detail?.item?.status,
      queueId,
      openOnlyNavigationParams,
      enqueueSnackbar,
    ],
  );

  useEffect(() => {
    if (!queueDetail || canUseCompletedControls) return;
    if (searchParams.get("includeCompleted") !== "true") return;

    const nextParams = new URLSearchParams(searchParams);
    nextParams.delete("includeCompleted");
    setSearchParams(nextParams, { replace: true });
  }, [canUseCompletedControls, queueDetail, searchParams, setSearchParams]);

  const handleSubmitAndNext = useCallback(
    ({ annotations, notes, itemNotes }) => {
      if (!currentItemId || isSubmittingRef.current) return;
      isSubmittingRef.current = true;

      // First submit, then complete
      submitAnnotations(
        { queueId, itemId: currentItemId, annotations, notes, itemNotes },
        {
          onSuccess: () => {
            completeItem(
              {
                queueId,
                itemId: currentItemId,
                exclude: itemHistory.join(","),
                excludeReviewStatus: requiresReview
                  ? "pending_review"
                  : undefined,
                includeCompleted: includeCompletedItems,
              },
              {
                onSuccess: (data) => {
                  isSubmittingRef.current = false;
                  const result = data?.data?.result || data?.data;
                  const nextItem = result?.nextItem || result?.next_item;
                  if (nextItem?.id) {
                    dispatch({ type: "push", id: nextItem.id });
                  } else {
                    enqueueSnackbar("Saved. No more items in this queue.", {
                      variant: "success",
                    });
                  }
                },
                onError: () => {
                  isSubmittingRef.current = false;
                },
              },
            );
          },
          onError: () => {
            isSubmittingRef.current = false;
          },
        },
      );
    },
    [
      queueId,
      currentItemId,
      itemHistory,
      submitAnnotations,
      completeItem,
      requiresReview,
      includeCompletedItems,
      enqueueSnackbar,
    ],
  );

  const handleSkip = useCallback(() => {
    if (!currentItemId || isAnnotateLockedForReview) return;
    skipItem(
      {
        queueId,
        itemId: currentItemId,
        exclude: itemHistory.join(","),
        excludeReviewStatus: requiresReview ? "pending_review" : undefined,
        includeCompleted: includeCompletedItems,
      },
      {
        onSuccess: (data) => {
          const result = data?.data?.result || data?.data;
          const nextItem = result?.nextItem || result?.next_item;
          if (nextItem?.id) {
            dispatch({ type: "push", id: nextItem.id });
          } else {
            dispatch({ type: "clear" });
          }
        },
      },
    );
  }, [
    queueId,
    currentItemId,
    itemHistory,
    skipItem,
    requiresReview,
    isAnnotateLockedForReview,
    includeCompletedItems,
  ]);

  const handleReviewSuccess = useCallback(
    (data) => {
      isDirtyRef.current = false;
      const result = data?.data?.result || data?.data;
      const nextItem = result?.nextItem || result?.next_item;
      if (nextItem?.id) {
        dispatch({ type: "push", id: nextItem.id });
      } else {
        navigate(`/dashboard/annotations/queues/${queueId}`);
      }
    },
    [navigate, queueId],
  );

  const handleApprove = useCallback(
    (payload) => {
      const { notes, labelComments } = normalizeReviewPayload(payload);
      reviewItem(
        {
          queueId,
          itemId: currentItemId,
          action: "approve",
          notes,
          labelComments,
        },
        {
          onSuccess: handleReviewSuccess,
        },
      );
    },
    [queueId, currentItemId, reviewItem, handleReviewSuccess],
  );

  const handleReject = useCallback(
    (payload) => {
      const { notes, labelComments } = normalizeReviewPayload(payload);
      reviewItem(
        {
          queueId,
          itemId: currentItemId,
          action: "request_changes",
          notes,
          labelComments,
        },
        {
          onSuccess: handleReviewSuccess,
        },
      );
    },
    [queueId, currentItemId, reviewItem, handleReviewSuccess],
  );

  const handleBack = useCallback(() => {
    if (!confirmDiscardUnsaved("You have unsaved changes. Leave anyway?"))
      return;
    navigate(`/dashboard/annotations/queues/${queueId}`);
  }, [navigate, queueId, confirmDiscardUnsaved]);

  const [isFetchingPrev, setIsFetchingPrev] = useState(false);

  const handlePrev = useCallback(async () => {
    if (!confirmDiscardUnsaved("You have unsaved changes. Load another item?"))
      return;
    if (historyIndex > 0) {
      dispatch({ type: "prev" });
      return;
    }
    // Fetch previous item by order from the API
    if (isFetchingPrev || !currentItemId) return;
    setIsFetchingPrev(true);
    try {
      const res = await axios.get(
        `/model-hub/annotation-queues/${queueId}/items/next-item/`,
        { params: { before: currentItemId, ...navigationModeParams } },
      );
      const prevItem =
        res?.data?.data?.item || res?.data?.result?.item || res?.data?.item;
      if (prevItem?.id) {
        dispatch({ type: "init", id: prevItem.id });
      }
    } catch (err) {
      // Aborts (from rapid Prev/Next clicks) are expected — only surface
      // real failures so the user knows the action didn't go through.
      if (err?.name !== "CanceledError" && err?.code !== "ERR_CANCELED") {
        enqueueSnackbar(
          err?.response?.data?.detail ||
            err?.message ||
            "Couldn't load previous item.",
          { variant: "error" },
        );
      }
    } finally {
      setIsFetchingPrev(false);
    }
  }, [
    historyIndex,
    currentItemId,
    queueId,
    isFetchingPrev,
    enqueueSnackbar,
    navigationModeParams,
    confirmDiscardUnsaved,
  ]);

  const [isFetchingNext, setIsFetchingNext] = useState(false);
  const nextAbortRef = useRef(null);

  // Cleanup abort controller on unmount
  useEffect(() => () => nextAbortRef.current?.abort(), []);

  const handleNext = useCallback(async () => {
    if (!confirmDiscardUnsaved("You have unsaved changes. Load another item?"))
      return;
    // If there are forward items in history, navigate to them
    if (historyIndex < itemHistory.length - 1) {
      dispatch({ type: "next" });
      return;
    }

    if (detail?.next_item_id) {
      dispatch({ type: "push", id: detail.next_item_id });
      return;
    }

    if (isFetchingNext) return;
    setIsFetchingNext(true);
    nextAbortRef.current?.abort();
    const controller = new AbortController();
    nextAbortRef.current = controller;
    try {
      const res = await axios.get(
        `/model-hub/annotation-queues/${queueId}/items/next-item/`,
        {
          params: { exclude: itemHistory.join(","), ...navigationModeParams },
          signal: controller.signal,
        },
      );
      const nextItem =
        res?.data?.data?.item || res?.data?.result?.item || res?.data?.item;
      if (nextItem?.id) {
        dispatch({ type: "push", id: nextItem.id });
      }
    } catch (err) {
      // Aborts (from rapid Prev/Next clicks) are expected and silent.
      // 404 means "no more items" — also expected, button just stays
      // disabled. Any other error gets surfaced so the user isn't left
      // wondering why nothing happened.
      const status = err?.response?.status;
      const isAbort =
        err?.name === "CanceledError" || err?.code === "ERR_CANCELED";
      if (!isAbort && status !== 404) {
        enqueueSnackbar(
          err?.response?.data?.detail ||
            err?.message ||
            "Couldn't load next item.",
          { variant: "error" },
        );
      }
    } finally {
      setIsFetchingNext(false);
    }
  }, [
    historyIndex,
    itemHistory,
    queueId,
    isFetchingNext,
    enqueueSnackbar,
    detail,
    navigationModeParams,
    confirmDiscardUnsaved,
  ]);

  const handleKeyboardSubmit = useCallback(() => {
    if (
      isViewOnlyForReviewer ||
      isBlockedAssignedToOther ||
      isViewingAllAnnotators ||
      isViewingOtherAnnotator ||
      isAnnotatorSwitchPending ||
      isAnnotateLockedForReview
    )
      return;
    labelPanelRef.current?.submit();
  }, [
    isViewOnlyForReviewer,
    isBlockedAssignedToOther,
    isViewingAllAnnotators,
    isViewingOtherAnnotator,
    isAnnotatorSwitchPending,
    isAnnotateLockedForReview,
  ]);

  useKeyboardShortcuts({
    onSubmit: handleKeyboardSubmit,
    onSkip: handleSkip,
    onPrev: handlePrev,
    onNext: handleNext,
    onEscape: handleBack,
  });

  const isInitialDetailLoading =
    detailEnabled && !detail && (detailLoading || detailFetching);
  const isWaitingForAnnotatorSelection =
    !!queueDetail && isReviewWorkspaceMode && !viewingAnnotatorId;
  const reviewComments =
    liveDiscussion?.review_comments || detail?.review_comments || [];
  const reviewThreads =
    liveDiscussion?.review_threads || detail?.review_threads || [];
  const decisionReviewComments = reviewComments.filter(
    (comment) => !isDiscussionComment(comment),
  );
  const activeDiscussionCount =
    reviewThreads.filter(
      (thread) => thread?.action === "comment" && thread?.status !== "resolved",
    ).length ||
    reviewComments.filter(
      (comment) =>
        isDiscussionComment(comment) &&
        (!comment?.thread_status || comment.thread_status !== "resolved"),
    ).length;
  const openBlockingFeedbackCount = decisionReviewComments.filter(
    isOpenBlockingReviewFeedback,
  ).length;
  const addressedFeedbackCount = decisionReviewComments.filter(
    (comment) =>
      isBlockingReviewFeedback(comment) &&
      comment?.thread_status === "addressed",
  ).length;
  const resolvedFeedbackCount = decisionReviewComments.filter(
    (comment) =>
      isBlockingReviewFeedback(comment) &&
      comment?.thread_status === "resolved",
  ).length;
  const commentBadgeCount = activeDiscussionCount + openBlockingFeedbackCount;

  const handleFocusCommentScope = useCallback(
    ({ labelId, targetAnnotatorId } = {}) => {
      const nextScope = commentScopeKey(labelId, targetAnnotatorId);
      setFocusedCommentScope(nextScope);

      const selector = targetAnnotatorId
        ? `[data-review-score-key="${labelId}:${targetAnnotatorId}"]`
        : labelId
          ? `[data-review-label-id="${labelId}"]`
          : '[data-review-item-summary="true"]';

      const scrollToScope = () => {
        const target = document.querySelector(selector);
        if (typeof target?.scrollIntoView === "function") {
          target.scrollIntoView({ block: "center", behavior: "smooth" });
        }
      };
      if (typeof window.requestAnimationFrame === "function") {
        window.requestAnimationFrame(scrollToScope);
      } else {
        window.setTimeout(scrollToScope, 0);
      }

      window.clearTimeout(focusTimeoutRef.current);
      focusTimeoutRef.current = window.setTimeout(
        () => setFocusedCommentScope(null),
        2400,
      );
    },
    [],
  );

  // Loading state
  if (
    isInitialItemLoading ||
    !queueDetail ||
    isWaitingForAnnotatorSelection ||
    isInitialDetailLoading
  ) {
    return (
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          height: "80vh",
        }}
      >
        <CircularProgress />
      </Box>
    );
  }

  // Reservation conflict
  if (
    detailError?.statusCode === 400 ||
    detailError?.response?.status === 400
  ) {
    const msg =
      detailError?.detail ||
      detailError?.response?.data?.detail ||
      "This item is currently reserved by another annotator.";
    return (
      <Box
        sx={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          height: "80vh",
          gap: 2,
        }}
      >
        <Iconify icon="mingcute:lock-fill" width={64} color="warning.main" />
        <Typography variant="h5">Item Reserved</Typography>
        <Alert severity="warning" sx={{ maxWidth: 480 }}>
          {msg}
        </Alert>
        <Button variant="outlined" onClick={handleSkip}>
          Skip to Next Item
        </Button>
        <Button onClick={handleBack}>Back to Queue</Button>
      </Box>
    );
  }

  // Queue not active — block annotation
  const queueStatus = effectiveQueueStatus;
  const completedToggleDisabled =
    isChangingCompletedVisibility ||
    isQueueDetailFetching ||
    detailFetching ||
    !detail ||
    !canUseCompletedControls ||
    isAssignedToOther ||
    isAnnotateLockedForReview ||
    isAnnotatorSwitchPending;
  const canResumeCompletedSkipped =
    queueStatus === "completed" && detail?.item?.status === "skipped";
  const canEditCompletedItem =
    queueStatus === "completed" && detail?.item?.status === "completed";
  if (
    queueStatus &&
    queueStatus !== "active" &&
    !canResumeCompletedSkipped &&
    !canEditCompletedItem
  ) {
    return (
      <Box
        sx={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          height: "80vh",
          gap: 2,
        }}
      >
        <Iconify icon="mingcute:lock-fill" width={64} color="warning.main" />
        <Typography variant="h5">Queue Not Active</Typography>
        <Typography color="text.secondary">
          This queue is currently <strong>{queueStatus}</strong>. Annotations
          can only be submitted when the queue is active.
        </Typography>
        <Button variant="outlined" color="primary" onClick={handleBack}>
          Back to Queue
        </Button>
      </Box>
    );
  }

  // No items / all done
  if (!currentItemId && !isInitialItemLoading) {
    const userProgress = progress?.user_progress;
    const hasUserProgress = userProgress && userProgress.total > 0;
    const skippedCount = hasUserProgress
      ? userProgress.skipped ?? 0
      : progress?.skipped ?? 0;
    const completedCount = hasUserProgress
      ? userProgress.completed ?? 0
      : progress?.completed ?? 0;
    const totalCount = hasUserProgress
      ? userProgress.total ?? 0
      : progress?.total ?? 0;

    return (
      <Box
        sx={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          height: "80vh",
          gap: 2,
        }}
      >
        <Iconify
          icon="eva:checkmark-circle-2-fill"
          width={64}
          color="success.main"
        />
        <Typography variant="h5">All Done!</Typography>
        <Typography color="text.secondary">
          {isReviewWorkspaceMode
            ? "No items are waiting for review."
            : completedCount > 0 &&
              `${completedCount} of ${totalCount} items completed.`}
          {!isReviewWorkspaceMode &&
            completedCount === 0 &&
            "No more pending items in this queue."}
        </Typography>

        {skippedCount > 0 && (
          <Alert severity="info" sx={{ maxWidth: 480 }}>
            You have {skippedCount} skipped{" "}
            {skippedCount === 1 ? "item" : "items"}. You can review them from
            the queue items list.
          </Alert>
        )}

        <Button variant="outlined" onClick={handleBack} sx={{ mt: 1 }}>
          Back to Queue
        </Button>
      </Box>
    );
  }

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        height: "calc(100vh - 64px)",
      }}
    >
      <AnnotateHeader
        queueName={detail?.queue?.name}
        progress={detail?.progress}
        onBack={handleBack}
        onSkip={handleSkip}
        isSkipping={isSkipping}
        isReviewMode={isReviewWorkspaceMode}
        isAssignedToOther={isAssignedToOther}
        isSkipDisabled={isAnnotateLockedForReview}
        showCompletedToggle={canUseCompletedControls}
        includeCompleted={includeCompletedItems}
        onIncludeCompletedChange={handleIncludeCompletedChange}
        completedToggleDisabled={completedToggleDisabled}
        isItemCompleted={isCurrentItemCompleted}
        completedByCurrentUser={completedByCurrentUser}
        onOpenComments={() => setCommentsOpen(true)}
        commentsDisabled={!currentItemId || !detail || !canDiscuss}
        commentBadgeCount={commentBadgeCount}
        activeCommentCount={activeDiscussionCount}
        openFeedbackCount={openBlockingFeedbackCount}
        addressedFeedbackCount={addressedFeedbackCount}
        resolvedFeedbackCount={resolvedFeedbackCount}
      />

      <Box
        sx={{
          display: "flex",
          flex: 1,
          overflow: "hidden",
          minWidth: 0,
          flexDirection: { xs: "column", md: "row" },
        }}
      >
        {/* Left: Content */}
        <Box
          sx={{
            flex: 1,
            minWidth: 0,
            borderRight: { xs: 0, md: 1 },
            borderBottom: { xs: 1, md: 0 },
            borderColor: "divider",
            overflow: "auto",
          }}
        >
          <ContentPanel item={detail?.item} />
        </Box>

        {/* Right: Labels or Review */}
        <Box
          sx={{
            flex: isReviewWorkspaceMode
              ? { xs: "1 1 auto", md: "0 1 clamp(440px, 52vw, 820px)" }
              : { xs: "1 1 auto", md: "0 0 400px" },
            width: isReviewWorkspaceMode
              ? { xs: "100%", md: "clamp(440px, 52vw, 820px)" }
              : { xs: "100%", md: 400 },
            minWidth: 0,
            maxWidth: isReviewWorkspaceMode
              ? { xs: "100%", md: 820 }
              : { xs: "100%", md: 440 },
            overflow: "auto",
            bgcolor: isReviewWorkspaceMode
              ? "background.default"
              : "background.paper",
          }}
        >
          {canReview && canAnnotate && (
            <Box sx={{ p: 1.5, pb: 0 }}>
              <Typography
                variant="caption"
                fontWeight={700}
                color="text.secondary"
                sx={{ display: "block", mb: 0.75 }}
              >
                Workspace action
              </Typography>
              <ToggleButtonGroup
                exclusive
                fullWidth
                size="small"
                value={workspaceMode}
                onChange={handleWorkspaceModeChange}
                aria-label="annotation workspace mode"
              >
                <ToggleButton value={WORKSPACE_MODES.ANNOTATE}>
                  Annotate my answers
                </ToggleButton>
                <ToggleButton value={WORKSPACE_MODES.REVIEW}>
                  {requiresReview ? "Review submissions" : "View submissions"}
                </ToggleButton>
              </ToggleButtonGroup>
            </Box>
          )}
          {!isReviewWorkspaceMode && isManualAssignment && detail?.item && (
            <ItemAssignmentPanel
              item={detail.item}
              annotators={queueAnnotators}
              currentUserId={currentUserId}
              canAnnotate={canAnnotate}
              canManageAssignments={canManageAssignments}
              onAssign={handleAssignCurrentItem}
              isPending={isAssigningItem}
            />
          )}
          {isReviewWorkspaceMode ? (
            <>
              {requiresReview && !isPendingReview && (
                <Alert severity="info" sx={{ m: 1.5, mb: 0 }}>
                  This item is not waiting for review. Review actions appear on
                  items submitted for review.
                </Alert>
              )}
              <AnnotationComparisonPanel
                item={detail?.item}
                annotations={detail?.annotations || []}
                labels={detail?.labels || []}
                spanNotes={detail?.span_notes || []}
                annotators={queueAnnotators}
                currentUserId={currentUserId}
                viewingAnnotatorId={viewingAnnotatorId}
                onViewingAnnotatorChange={handleViewingAnnotatorChange}
                onApprove={handleApprove}
                onReject={handleReject}
                onDirtyChange={handleDirtyChange}
                isPending={isReviewing}
                reviewStatus={detail?.item?.review_status}
                reviewNotes={detail?.item?.review_notes || ""}
                reviewComments={reviewComments}
                queueId={queueId}
                itemId={currentItemId}
                showReviewActions={showReviewActions}
                focusedCommentScope={focusedCommentScope}
              />
            </>
          ) : isBlockedAssignedToOther ? (
            <Stack
              alignItems="center"
              justifyContent="center"
              spacing={2}
              sx={{ height: "100%", p: 3, textAlign: "center" }}
            >
              <Iconify
                icon="mingcute:lock-fill"
                width={48}
                color="text.disabled"
              />
              <Typography variant="subtitle1" color="text.secondary">
                Assigned to {assignedToName || "another annotator"}
              </Typography>
              <Typography variant="body2" color="text.disabled">
                This item is assigned to someone else. You cannot annotate it.
              </Typography>
              <Button
                variant="outlined"
                color="primary"
                size="small"
                onClick={handleNext}
                disabled={isFetchingNext}
              >
                Skip to Next Item
              </Button>
            </Stack>
          ) : isViewingAllAnnotators ? (
            <AnnotationComparisonPanel
              item={detail?.item}
              labels={detail?.labels || []}
              annotations={detail?.annotations || []}
              spanNotes={detail?.span_notes || []}
              annotators={queueAnnotators}
              currentUserId={currentUserId}
              viewingAnnotatorId={viewingAnnotatorId}
              onViewingAnnotatorChange={handleViewingAnnotatorChange}
              onDirtyChange={handleDirtyChange}
              reviewStatus={detail?.item?.review_status}
              reviewNotes={detail?.item?.review_notes || ""}
              reviewComments={reviewComments}
              queueId={queueId}
              itemId={currentItemId}
              focusedCommentScope={focusedCommentScope}
            />
          ) : (
            <LabelPanel
              ref={labelPanelRef}
              labels={detail?.labels || []}
              annotations={detail?.annotations || []}
              initialItemNotes={detail?.existing_notes || ""}
              reviewFeedback={detail?.item?.review_notes || ""}
              reviewComments={detail?.review_comments || []}
              instructions={detail?.queue?.instructions}
              onSubmit={handleSubmitAndNext}
              isPending={isSubmitting || isCompleting}
              queueId={queueId}
              itemId={currentItemId}
              detailItemId={detail?.item?.id}
              onDirtyChange={handleDirtyChange}
              readOnly={labelPanelReadOnly}
              readOnlyReason={labelPanelReadOnlyReason}
              annotators={null}
              viewingAnnotatorId={viewingAnnotatorId}
              currentUserId={currentUserId}
              isAnnotatorSwitchPending={isAnnotatorSwitchPending}
              onViewingAnnotatorChange={handleViewingAnnotatorChange}
              focusedCommentScope={focusedCommentScope}
              submitLabel={
                isCurrentItemCompleted
                  ? "Update & Next"
                  : requiresReview
                    ? "Submit for Review"
                    : "Submit & Next"
              }
            />
          )}
        </Box>
      </Box>

      {!isReviewWorkspaceMode && (
        <AnnotateFooter
          currentPosition={
            detail?.progress?.currentPosition ||
            detail?.progress?.current_position ||
            0
          }
          total={detail?.progress?.total || 0}
          onPrev={handlePrev}
          onNext={handleNext}
          hasPrev={historyIndex > 0 || Boolean(detail?.prev_item_id)}
          hasNext={
            historyIndex < itemHistory.length - 1 ||
            Boolean(detail?.next_item_id)
          }
          isLoadingNext={isFetchingNext}
        />
      )}

      <CollaborationDrawer
        open={commentsOpen}
        onClose={() => setCommentsOpen(false)}
        queueId={queueId}
        itemId={currentItemId}
        itemLabel={itemContextLabel(detail?.item, currentItemId)}
        labels={detail?.labels || []}
        members={queueMembers}
        comments={reviewComments}
        threads={reviewThreads}
        canTargetMembers={canReview}
        canComment={canDiscuss}
        onFocusScope={handleFocusCommentScope}
      />
    </Box>
  );
}
