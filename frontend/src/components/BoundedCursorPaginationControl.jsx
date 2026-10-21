import {
  Box,
  Button,
  CircularProgress,
  Stack,
  Typography,
} from "@mui/material";
import PropTypes from "prop-types";
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";

import { PROPERTY_PICKER_PREFETCH_MARGIN_PX } from "src/config/runtime_limits";

/**
 * Advance cursor-backed lists once per distinct continuation.
 *
 * A channel is either a catalog page or a retained-attribute page. Multiple
 * channels may advance together. A newly published continuation may load while
 * the sentinel remains visible when autoAdvanceWhileVisible is enabled, but a
 * continuation already attempted by this mounted picker can never be replayed
 * automatically. Search surfaces disable that follow-on behavior so an empty
 * exact lookup cannot drain a retained window without another viewport-entry
 * gesture. Failed channels are retried only by the explicit retry control.
 * Changing resetKey starts a new logical cursor chain and makes its
 * continuations eligible again.
 */
const BoundedCursorPaginationControl = ({
  channels,
  rootRef,
  resetKey,
  autoAdvanceWhileVisible = true,
  requireUserAdvanceGesture = false,
  loadingLabel = "Loading more…",
  retryLabel = "Retry loading properties",
  errorMessage,
  testId,
  markerProps,
}) => {
  const sentinelRef = useRef(null);
  const channelsRef = useRef(channels);
  const activeRequestRef = useRef(null);
  const isIntersectingRef = useRef(false);
  const visibleEntryConsumedRef = useRef(false);
  const userAdvanceGestureRef = useRef(!requireUserAdvanceGesture);
  const attemptedContinuationsRef = useRef(new Set());
  const [isRequestPending, setIsRequestPending] = useState(false);

  channelsRef.current = channels;
  const pageAvailable = channels.some(
    ({ hasNextPage, loadNextPage }) => hasNextPage && loadNextPage,
  );
  const hasRetryableError = channels.some(
    ({ error, loadNextPage }) => error && loadNextPage,
  );
  // A transport promise can outlive the authoritative channel state (for
  // example, React Query publishes a terminal page before an adapter promise
  // settles). Once no channel can continue, do not keep a stale local promise
  // rendering an endless "Loading more" sentinel.
  const loading =
    channels.some(({ isFetching }) => isFetching) ||
    (isRequestPending && pageAvailable);
  const shouldRender = pageAvailable || hasRetryableError || loading;
  const loadingRef = useRef(loading);
  loadingRef.current = loading;
  const continuationSignature = channels
    .map(({ channelKey, hasNextPage, continuationKey }, index) => {
      const channelIdentity = channelKey || `channel-${index}`;
      return hasNextPage &&
        continuationKey !== null &&
        continuationKey !== undefined
        ? `${channelIdentity}:${typeof continuationKey}:${String(continuationKey)}`
        : null;
    })
    .filter(Boolean)
    .join("|");

  const runOneBoundedPage = useCallback((retryOnly = false) => {
    if (loadingRef.current || activeRequestRef.current) return false;

    const actions = channelsRef.current
      .map((channel, index) => {
        const {
          channelKey,
          continuationKey,
          error,
          hasNextPage,
          loadNextPage,
        } = channel;
        if (!loadNextPage || !(retryOnly ? error : hasNextPage)) return null;
        const channelIdentity = channelKey || `channel-${index}`;
        const attemptKey =
          continuationKey === null || continuationKey === undefined
            ? null
            : `${channelIdentity}:${typeof continuationKey}:${String(continuationKey)}`;
        if (
          !retryOnly &&
          (!attemptKey || attemptedContinuationsRef.current.has(attemptKey))
        ) {
          return null;
        }
        return { attemptKey, loadNextPage };
      })
      .filter(Boolean);
    if (actions.length === 0) return false;

    const asyncResults = [];
    actions.forEach(({ attemptKey, loadNextPage }) => {
      if (attemptKey) {
        attemptedContinuationsRef.current.add(attemptKey);
      }
      try {
        const result = loadNextPage();
        if (result && typeof result.then === "function") {
          asyncResults.push(result);
        }
      } catch (error) {
        asyncResults.push(Promise.reject(error));
      }
    });

    // React Query page loaders return promises, so keep the sentinel visibly
    // busy until those requests settle. Some consumers and unit-test doubles
    // synchronously enqueue a request and return nothing; avoid a transient
    // local-state render in that case because the consumer's own fetching
    // state is authoritative.
    if (asyncResults.length === 0) return true;

    setIsRequestPending(true);
    const request = Promise.allSettled(asyncResults);
    activeRequestRef.current = request;
    const clearRequest = () => {
      if (activeRequestRef.current === request) {
        activeRequestRef.current = null;
        setIsRequestPending(false);
      }
    };
    request.then(clearRequest, clearRequest);
    return true;
  }, []);

  useLayoutEffect(() => {
    attemptedContinuationsRef.current.clear();
    visibleEntryConsumedRef.current = false;
    userAdvanceGestureRef.current = !requireUserAdvanceGesture;
    activeRequestRef.current = null;
    setIsRequestPending(false);
  }, [requireUserAdvanceGesture, resetKey]);

  useLayoutEffect(() => {
    if (requireUserAdvanceGesture) {
      // A settled page publishes a new continuation while a short list may
      // leave the sentinel visible. Make that new cursor eligible, but keep
      // the gesture token consumed so it cannot auto-drain the next page.
      visibleEntryConsumedRef.current = false;
    }
  }, [continuationSignature, requireUserAdvanceGesture]);

  const loadAtVisibleEnd = useCallback(() => {
    if (hasRetryableError || loadingRef.current) {
      return;
    }
    runOneBoundedPage();
  }, [hasRetryableError, runOneBoundedPage]);

  useLayoutEffect(() => {
    if (!requireUserAdvanceGesture || !shouldRender) return undefined;

    const sentinel = sentinelRef.current;
    const root =
      rootRef?.current ||
      sentinel?.closest?.('[role="listbox"]') ||
      sentinel?.parentElement;
    if (!root?.addEventListener) return undefined;

    const recordAdvanceGesture = (event) => {
      if (event?.type === "wheel" && Number(event.deltaY) <= 0) return;
      if (
        loadingRef.current ||
        activeRequestRef.current ||
        visibleEntryConsumedRef.current ||
        hasRetryableError
      ) {
        return;
      }
      userAdvanceGestureRef.current = true;
      if (isIntersectingRef.current) {
        visibleEntryConsumedRef.current = true;
        userAdvanceGestureRef.current = false;
        loadAtVisibleEnd();
      }
    };

    root.addEventListener("scroll", recordAdvanceGesture, { passive: true });
    root.addEventListener("wheel", recordAdvanceGesture, { passive: true });
    return () => {
      root.removeEventListener("scroll", recordAdvanceGesture);
      root.removeEventListener("wheel", recordAdvanceGesture);
    };
  }, [
    hasRetryableError,
    loadAtVisibleEnd,
    requireUserAdvanceGesture,
    rootRef,
    shouldRender,
  ]);

  useLayoutEffect(() => {
    const sentinel = sentinelRef.current;
    if (
      !shouldRender ||
      !sentinel ||
      typeof IntersectionObserver !== "function"
    ) {
      return undefined;
    }
    const root =
      rootRef?.current ||
      sentinel.closest?.('[role="listbox"]') ||
      sentinel.parentElement;
    const observer = new IntersectionObserver(
      ([entry]) => {
        const isIntersecting = Boolean(entry?.isIntersecting);
        isIntersectingRef.current = isIntersecting;
        if (!isIntersecting) {
          visibleEntryConsumedRef.current = false;
          return;
        }
        if (requireUserAdvanceGesture && !userAdvanceGestureRef.current) {
          return;
        }
        if (!autoAdvanceWhileVisible && visibleEntryConsumedRef.current) {
          return;
        }
        visibleEntryConsumedRef.current = true;
        if (requireUserAdvanceGesture) {
          userAdvanceGestureRef.current = false;
        }
        loadAtVisibleEnd();
      },
      {
        root,
        rootMargin: `0px 0px ${PROPERTY_PICKER_PREFETCH_MARGIN_PX}px 0px`,
        threshold: 0,
      },
    );
    observer.observe(sentinel);
    return () => observer.disconnect();
  }, [
    autoAdvanceWhileVisible,
    loadAtVisibleEnd,
    requireUserAdvanceGesture,
    rootRef,
    shouldRender,
  ]);

  useEffect(() => {
    if (
      autoAdvanceWhileVisible &&
      !loading &&
      isIntersectingRef.current &&
      (!requireUserAdvanceGesture || userAdvanceGestureRef.current) &&
      !hasRetryableError
    ) {
      loadAtVisibleEnd();
    }
  }, [
    continuationSignature,
    autoAdvanceWhileVisible,
    hasRetryableError,
    loadAtVisibleEnd,
    loading,
    requireUserAdvanceGesture,
    resetKey,
  ]);

  if (!shouldRender) return null;

  return (
    <Box
      ref={sentinelRef}
      data-testid={testId}
      {...markerProps}
      sx={{ py: 0.75, textAlign: "center" }}
    >
      {loading ? (
        <Box
          role="status"
          sx={{
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            gap: 0.75,
          }}
        >
          <CircularProgress size={14} />
          <Typography sx={{ fontSize: 11, color: "text.secondary" }}>
            {loadingLabel}
          </Typography>
        </Box>
      ) : hasRetryableError ? (
        <Stack spacing={0.5} alignItems="center">
          {errorMessage && (
            <Typography role="alert" variant="caption" color="warning.main">
              {errorMessage}
            </Typography>
          )}
          <Button
            size="small"
            onClick={() => runOneBoundedPage(true)}
            sx={{ fontSize: 11 }}
          >
            {retryLabel}
          </Button>
        </Stack>
      ) : null}
    </Box>
  );
};

BoundedCursorPaginationControl.propTypes = {
  channels: PropTypes.arrayOf(
    PropTypes.shape({
      channelKey: PropTypes.string,
      hasNextPage: PropTypes.bool,
      continuationKey: PropTypes.oneOfType([
        PropTypes.string,
        PropTypes.number,
      ]),
      isFetching: PropTypes.bool,
      error: PropTypes.bool,
      loadNextPage: PropTypes.func,
    }),
  ).isRequired,
  rootRef: PropTypes.shape({ current: PropTypes.any }),
  resetKey: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
  autoAdvanceWhileVisible: PropTypes.bool,
  requireUserAdvanceGesture: PropTypes.bool,
  loadingLabel: PropTypes.string,
  retryLabel: PropTypes.string,
  errorMessage: PropTypes.string,
  testId: PropTypes.string,
  markerProps: PropTypes.object,
};

export default BoundedCursorPaginationControl;
