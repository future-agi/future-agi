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
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import axios from "src/utils/axios";
import { useAuthContext } from "src/auth/hooks";
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
} from "src/api/annotation-queues/annotation-queues";
import AnnotateHeader from "./annotate-header";
import AnnotateFooter from "./annotate-footer";
import ContentPanel from "./content-panel";
import LabelPanel from "./label-panel";
import ReviewPanel from "./review-panel";
import useKeyboardShortcuts from "./use-keyboard-shortcuts";
import { QUEUE_ROLES } from "../constants";

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

export default function AnnotateWorkspaceView() {
  const { queueId } = useParams();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const { user } = useAuthContext();
  const queryClient = useQueryClient();
  const initialItemId = searchParams.get("itemId");

  const [navState, dispatch] = useReducer(historyReducer, {
    history: initialItemId ? [initialItemId] : [],
    index: initialItemId ? 0 : -1,
    currentItemId: initialItemId || null,
  });
  const { history: itemHistory, index: historyIndex, currentItemId } = navState;

  // Fetch next item on mount (only if no specific item was requested)
  const { data: nextItemData, isLoading: nextLoading } = useNextItem(queueId, {
    enabled: !currentItemId,
  });

  // Set initial item from next-item query
  useEffect(() => {
    if (nextItemData?.id && !currentItemId) {
      dispatch({ type: "init", id: nextItemData.id });
    }
  }, [nextItemData, currentItemId]);

  // Fetch annotate detail for current item
  const {
    data: detail,
    isLoading: detailLoading,
    error: detailError,
  } = useAnnotateDetail(queueId, currentItemId);

  const { data: progress } = useQueueProgress(queueId);
  const { data: queueDetail } = useAnnotationQueueDetail(queueId);

  const myQueueRole = useMemo(() => {
    if (!queueDetail?.annotators || !user) return null;
    const currentUser = queueDetail.annotators.find(
      (a) => a.user_id === (user.id || user.pk),
    );
    return currentUser?.role || null;
  }, [queueDetail, user]);

  const canReview =
    myQueueRole === QUEUE_ROLES.REVIEWER || myQueueRole === QUEUE_ROLES.MANAGER;

  // Prefetch adjacent items for instant navigation
  useEffect(() => {
    if (!detail || !queueId) return;
    const nextId = detail.next_item_id;
    const prevId = detail.prev_item_id;
    if (nextId) {
      queryClient.prefetchQuery({
        queryKey: annotateKeys.detail(queueId, nextId),
        queryFn: () =>
          axios.get(
            `/model-hub/annotation-queues/${queueId}/items/${nextId}/annotate-detail/`,
          ),
        staleTime: 1000 * 60 * 2,
      });
    }
    if (prevId) {
      queryClient.prefetchQuery({
        queryKey: annotateKeys.detail(queueId, prevId),
        queryFn: () =>
          axios.get(
            `/model-hub/annotation-queues/${queueId}/items/${prevId}/annotate-detail/`,
          ),
        staleTime: 1000 * 60 * 2,
      });
    }
  }, [detail, queueId, queryClient]);

  const labelPanelRef = useRef(null);
  const isDirtyRef = useRef(false);
  const handleDirtyChange = useCallback((dirty) => {
    isDirtyRef.current = dirty;
  }, []);

  const { mutate: submitAnnotations, isPending: isSubmitting } =
    useSubmitAnnotations();
  const { mutate: completeItem, isPending: isCompleting } = useCompleteItem();
  const { mutate: skipItem, isPending: isSkipping } = useSkipItem();
  const { mutate: reviewItem, isPending: isReviewing } = useReviewItem();

  const requiresReview = queueDetail?.requires_review === true;
  const isPendingReview = detail?.item?.review_status === "pending_review";
  const isReviewMode = isPendingReview && canReview && requiresReview;

  // Item is explicitly assigned to someone else (only blocks in manual-assignment mode)
  const assignedUsers = detail?.item?.assigned_users || [];
  const currentUserId = String(user?.id || user?.pk || "");
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
  // In manual-assignment queues, only explicitly assigned users may annotate.
  // Auto-assign queues implicitly assign everyone, so they never block.
  const cannotAnnotate = isManualAssignment && !isAssignedToMe;
  // Reviewers/managers see assigned-to-other items in read-only mode (when
  // not actively reviewing). Other members are fully blocked.
  const isViewOnlyForReviewer = canReview && cannotAnnotate && !isReviewMode;
  const isBlockedAssignedToOther = !canReview && cannotAnnotate;
  // Backwards-compatible flag passed to header for disabling Skip.
  const isAssignedToOther = cannotAnnotate && !isReviewMode;

  const isSubmittingRef = useRef(false);

  const handleSubmitAndNext = useCallback(
    ({ annotations, notes }) => {
      if (!currentItemId || isSubmittingRef.current) return;
      isSubmittingRef.current = true;

      // First submit, then complete
      submitAnnotations(
        { queueId, itemId: currentItemId, annotations, notes },
        {
          onSuccess: () => {
            completeItem(
              {
                queueId,
                itemId: currentItemId,
                exclude: itemHistory.join(","),
              },
              {
                onSuccess: (data) => {
                  isSubmittingRef.current = false;
                  const result = data?.data?.result || data?.data;
                  const nextItem = result?.nextItem || result?.next_item;
                  if (nextItem?.id) {
                    dispatch({ type: "push", id: nextItem.id });
                  } else {
                    dispatch({ type: "clear" });
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
    [queueId, currentItemId, itemHistory, submitAnnotations, completeItem],
  );

  const handleSkip = useCallback(() => {
    if (!currentItemId) return;
    skipItem(
      { queueId, itemId: currentItemId, exclude: itemHistory.join(",") },
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
  }, [queueId, currentItemId, itemHistory, skipItem]);

  const handleApprove = useCallback(
    (notes) => {
      reviewItem(
        { queueId, itemId: currentItemId, action: "approve", notes },
        {
          onSuccess: () => navigate(`/dashboard/annotations/queues/${queueId}`),
        },
      );
    },
    [queueId, currentItemId, reviewItem, navigate],
  );

  const handleReject = useCallback(
    (notes) => {
      reviewItem(
        { queueId, itemId: currentItemId, action: "reject", notes },
        {
          onSuccess: () => navigate(`/dashboard/annotations/queues/${queueId}`),
        },
      );
    },
    [queueId, currentItemId, reviewItem, navigate],
  );

  const handleBack = useCallback(() => {
    if (isDirtyRef.current) {
      if (!window.confirm("You have unsaved annotations. Leave anyway?"))
        return;
    }
    navigate(`/dashboard/annotations/queues/${queueId}`);
  }, [navigate, queueId]);

  const [isFetchingPrev, setIsFetchingPrev] = useState(false);

  const handlePrev = useCallback(async () => {
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
        { params: { before: currentItemId } },
      );
      const prevItem =
        res?.data?.data?.item || res?.data?.result?.item || res?.data?.item;
      if (prevItem?.id) {
        dispatch({ type: "init", id: prevItem.id });
      }
    } catch {
      // silently ignore
    } finally {
      setIsFetchingPrev(false);
    }
  }, [historyIndex, currentItemId, queueId, isFetchingPrev]);

  const [isFetchingNext, setIsFetchingNext] = useState(false);
  const nextAbortRef = useRef(null);

  // Cleanup abort controller on unmount
  useEffect(() => () => nextAbortRef.current?.abort(), []);

  const handleNext = useCallback(async () => {
    // If there are forward items in history, navigate to them
    if (historyIndex < itemHistory.length - 1) {
      dispatch({ type: "next" });
      return;
    }

    // detail.next_item_id is status-agnostic — works for view-only managers.
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
          params: { exclude: itemHistory.join(",") },
          signal: controller.signal,
        },
      );
      const nextItem =
        res?.data?.data?.item || res?.data?.result?.item || res?.data?.item;
      if (nextItem?.id) {
        dispatch({ type: "push", id: nextItem.id });
      }
    } catch {
      // silently ignore — button will stay disabled if no more items
    } finally {
      setIsFetchingNext(false);
    }
  }, [historyIndex, itemHistory, queueId, isFetchingNext, detail]);

  const handleKeyboardSubmit = useCallback(() => {
    if (isViewOnlyForReviewer || isBlockedAssignedToOther) return;
    labelPanelRef.current?.submit();
  }, [isViewOnlyForReviewer, isBlockedAssignedToOther]);

  useKeyboardShortcuts({
    onSubmit: handleKeyboardSubmit,
    onSkip: handleSkip,
    onPrev: handlePrev,
    onNext: handleNext,
    onEscape: handleBack,
  });

  // Loading state
  if (nextLoading || (detailLoading && !detail)) {
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
  const queueStatus = detail?.queue?.status;
  if (queueStatus && queueStatus !== "active") {
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
  if (!currentItemId && !nextLoading) {
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
          {completedCount > 0 &&
            `${completedCount} of ${totalCount} items completed.`}
          {completedCount === 0 && "No more pending items in this queue."}
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
        isReviewMode={isReviewMode}
        isAssignedToOther={isAssignedToOther}
      />

      <Box sx={{ display: "flex", flex: 1, overflow: "hidden" }}>
        {/* Left: Content */}
        <Box
          sx={{
            flex: 1,
            borderRight: 1,
            borderColor: "divider",
            overflow: "auto",
          }}
        >
          <ContentPanel item={detail?.item} />
        </Box>

        {/* Right: Labels or Review */}
        <Box sx={{ width: 400, minWidth: 360, overflow: "auto" }}>
          {isReviewMode ? (
            <ReviewPanel
              annotations={detail?.annotations || []}
              labels={detail?.labels || []}
              onApprove={handleApprove}
              onReject={handleReject}
              isPending={isReviewing}
              reviewStatus={detail?.item?.review_status}
              itemId={currentItemId}
            />
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
          ) : (
            <LabelPanel
              ref={labelPanelRef}
              labels={detail?.labels || []}
              annotations={detail?.annotations || []}
              instructions={detail?.queue?.instructions}
              onSubmit={handleSubmitAndNext}
              isPending={isSubmitting || isCompleting}
              queueId={queueId}
              itemId={currentItemId}
              onDirtyChange={handleDirtyChange}
              readOnly={isViewOnlyForReviewer}
              readOnlyReason={
                isViewOnlyForReviewer
                  ? `Assigned to ${assignedToName || "another annotator"} — view only`
                  : null
              }
            />
          )}
        </Box>
      </Box>

      {!isReviewMode && (
        <AnnotateFooter
          currentPosition={
            detail?.progress?.currentPosition ||
            detail?.progress?.current_position ||
            0
          }
          total={detail?.progress?.total || 0}
          onPrev={handlePrev}
          onNext={handleNext}
          hasPrev={
            historyIndex > 0 ||
            (detail?.progress?.currentPosition ||
              detail?.progress?.current_position ||
              0) > 1
          }
          hasNext={
            historyIndex < itemHistory.length - 1 ||
            (detail?.progress?.currentPosition ||
              detail?.progress?.current_position ||
              0) < (detail?.progress?.total || 0)
          }
          isLoadingNext={isFetchingNext}
        />
      )}
    </Box>
  );
}
