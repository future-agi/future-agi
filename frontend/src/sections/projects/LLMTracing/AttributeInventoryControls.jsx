import { Alert, Box, Button, CircularProgress, TextField } from "@mui/material";
import PropTypes from "prop-types";
import React, { useCallback, useEffect, useRef, useState } from "react";

const ATTRIBUTE_PAGINATION_THRESHOLD_PX = 120;
const ATTRIBUTE_PAGINATION_ROOT_MARGIN = `0px 0px ${ATTRIBUTE_PAGINATION_THRESHOLD_PX}px 0px`;
const ACTIVE_REQUESTS = new WeakMap();

/** One viewport-end gesture advances at most one cursor-backed attribute page. */
const AttributeInventoryControls = ({
  search = "",
  onSearchChange,
  hasNextPage,
  isFetchingNextPage,
  onLoadMore,
  isError = false,
  isExactSearchError = false,
  isExactSearchDegraded = false,
  isFetchNextPageError = false,
  cursorRetryExhausted = false,
  canRetry = false,
  onRetry,
  showSearch = true,
  showLoadMore = true,
  searchLabel = "Search attributes",
  scrollContainerRef,
}) => {
  const activeRequestRef = useRef(null);
  const bottomEdgeLatchedRef = useRef(false);
  const paginationSentinelRef = useRef(null);
  const paginationStateRef = useRef(null);
  const [pendingAction, setPendingAction] = useState(null);
  const loading = isFetchingNextPage || pendingAction !== null;

  const runOneRequest = useCallback((action, requestAction) => {
    if (
      typeof requestAction !== "function" ||
      activeRequestRef.current ||
      ACTIVE_REQUESTS.has(requestAction)
    ) {
      return false;
    }

    let requestResult;
    try {
      requestResult = requestAction();
    } catch (_error) {
      return false;
    }
    const request = Promise.resolve(requestResult);
    ACTIVE_REQUESTS.set(requestAction, request);
    activeRequestRef.current = request;
    setPendingAction(action);
    const clearRequest = () => {
      if (ACTIVE_REQUESTS.get(requestAction) === request) {
        ACTIVE_REQUESTS.delete(requestAction);
      }
      if (activeRequestRef.current === request) {
        activeRequestRef.current = null;
        setPendingAction(null);
      }
    };
    request.then(clearRequest, clearRequest);
    return true;
  }, []);

  paginationStateRef.current = {
    canAutoLoad:
      showLoadMore &&
      hasNextPage &&
      !loading &&
      !isError &&
      !isExactSearchError &&
      !isFetchNextPageError &&
      !cursorRetryExhausted,
    onLoadMore,
  };

  const requestNextPage = useCallback(() => {
    const paginationState = paginationStateRef.current;
    if (!paginationState?.canAutoLoad) return false;
    return runOneRequest("load", paginationState.onLoadMore);
  }, [runOneRequest]);

  const handleNearViewportEnd = useCallback(
    (isNearEnd) => {
      if (!isNearEnd) {
        bottomEdgeLatchedRef.current = false;
        return;
      }
      if (bottomEdgeLatchedRef.current) return;
      if (requestNextPage()) bottomEdgeLatchedRef.current = true;
    },
    [requestNextPage],
  );

  useEffect(() => {
    bottomEdgeLatchedRef.current = false;
  }, [search]);

  useEffect(() => {
    if (!showLoadMore) return undefined;

    const scrollContainer = scrollContainerRef?.current;
    if (scrollContainer) {
      const handleScroll = () => {
        const remaining =
          scrollContainer.scrollHeight -
          scrollContainer.scrollTop -
          scrollContainer.clientHeight;
        handleNearViewportEnd(remaining <= ATTRIBUTE_PAGINATION_THRESHOLD_PX);
      };
      scrollContainer.addEventListener("scroll", handleScroll, {
        passive: true,
      });
      return () => scrollContainer.removeEventListener("scroll", handleScroll);
    }

    const sentinel = paginationSentinelRef.current;
    if (!sentinel || typeof IntersectionObserver !== "function") {
      return undefined;
    }
    const observer = new IntersectionObserver(
      (entries) => {
        const entry = entries[0];
        handleNearViewportEnd(
          Boolean(entry?.isIntersecting || entry?.intersectionRatio > 0),
        );
      },
      { rootMargin: ATTRIBUTE_PAGINATION_ROOT_MARGIN },
    );
    observer.observe(sentinel);
    return () => observer.disconnect();
  }, [
    cursorRetryExhausted,
    handleNearViewportEnd,
    hasNextPage,
    scrollContainerRef,
    showLoadMore,
  ]);

  const hasWarning =
    isError ||
    isExactSearchError ||
    isExactSearchDegraded ||
    isFetchNextPageError ||
    cursorRetryExhausted;
  const hasAutomaticPagination =
    showLoadMore &&
    !cursorRetryExhausted &&
    (hasNextPage || pendingAction === "load");
  const canRetryNextPage =
    showLoadMore &&
    hasNextPage &&
    isFetchNextPageError &&
    !cursorRetryExhausted &&
    typeof onLoadMore === "function";

  if (
    !showSearch &&
    !hasAutomaticPagination &&
    !canRetry &&
    !loading &&
    !hasWarning
  ) {
    return null;
  }

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 1, mt: 1 }}>
      {hasWarning && (
        <Alert severity="warning" sx={{ py: 0 }}>
          {cursorRetryExhausted
            ? "Attribute pagination stopped safely. Loaded properties remain available."
            : isError
              ? "Properties could not be loaded. Retry this page."
              : isExactSearchError
                ? "Exact property search could not be loaded. Retry this search."
                : isExactSearchDegraded
                  ? "Exact property search stopped. Continue through retained properties."
                  : "The next property page failed. Loaded properties remain available."}
        </Alert>
      )}
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          justifyContent: "flex-end",
          gap: 1,
        }}
      >
        {showSearch && (
          <TextField
            size="small"
            label={searchLabel}
            value={search}
            onChange={(event) => onSearchChange?.(event.target.value)}
            sx={{ minWidth: 220 }}
          />
        )}
        {(canRetry || pendingAction === "retry") && (
          <Button
            size="small"
            disabled={loading}
            onClick={() => runOneRequest("retry", onRetry)}
          >
            {pendingAction === "retry" ? (
              <>
                <CircularProgress size={14} sx={{ mr: 0.75 }} />
                Retrying properties…
              </>
            ) : (
              "Retry properties"
            )}
          </Button>
        )}
        {(canRetryNextPage || pendingAction === "page-retry") && (
          <Button
            size="small"
            disabled={loading}
            onClick={() => runOneRequest("page-retry", onLoadMore)}
          >
            {pendingAction === "page-retry" ? (
              <>
                <CircularProgress size={14} sx={{ mr: 0.75 }} />
                Retrying properties…
              </>
            ) : (
              "Retry next property page"
            )}
          </Button>
        )}
        {showLoadMore &&
          loading &&
          pendingAction !== "retry" &&
          pendingAction !== "page-retry" && (
            <Box
              role="status"
              aria-live="polite"
              sx={{ display: "flex", alignItems: "center", gap: 0.75 }}
            >
              <CircularProgress size={14} />
              Loading more properties…
            </Box>
          )}
      </Box>
      {hasAutomaticPagination && (
        <Box
          ref={paginationSentinelRef}
          aria-hidden="true"
          data-attribute-pagination-sentinel=""
          sx={{ width: "100%", height: 1 }}
        />
      )}
    </Box>
  );
};

AttributeInventoryControls.propTypes = {
  search: PropTypes.string,
  onSearchChange: PropTypes.func,
  hasNextPage: PropTypes.bool,
  isFetchingNextPage: PropTypes.bool,
  onLoadMore: PropTypes.func,
  isError: PropTypes.bool,
  isExactSearchError: PropTypes.bool,
  isExactSearchDegraded: PropTypes.bool,
  isFetchNextPageError: PropTypes.bool,
  cursorRetryExhausted: PropTypes.bool,
  canRetry: PropTypes.bool,
  onRetry: PropTypes.func,
  showSearch: PropTypes.bool,
  showLoadMore: PropTypes.bool,
  searchLabel: PropTypes.string,
  scrollContainerRef: PropTypes.shape({ current: PropTypes.any }),
};

export default AttributeInventoryControls;
