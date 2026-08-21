import { Alert, Box, Button, CircularProgress, TextField } from "@mui/material";
import PropTypes from "prop-types";
import React, { useCallback, useRef, useState } from "react";

/** One explicit gesture advances at most one cursor-backed attribute page. */
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
}) => {
  const activeRequestRef = useRef(null);
  const [pendingAction, setPendingAction] = useState(null);
  const loading = isFetchingNextPage || pendingAction !== null;

  const runOneRequest = useCallback((action, requestAction) => {
    if (!requestAction || activeRequestRef.current) return;
    const request = Promise.resolve(requestAction());
    activeRequestRef.current = request;
    setPendingAction(action);
    const clearRequest = () => {
      if (activeRequestRef.current === request) {
        activeRequestRef.current = null;
        setPendingAction(null);
      }
    };
    request.then(clearRequest, clearRequest);
  }, []);

  const hasWarning =
    isError ||
    isExactSearchError ||
    isExactSearchDegraded ||
    isFetchNextPageError ||
    cursorRetryExhausted;

  if (
    !showSearch &&
    (!showLoadMore || !hasNextPage) &&
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
        {showLoadMore && (hasNextPage || pendingAction === "load") && (
          <Button
            size="small"
            disabled={loading}
            onClick={() => runOneRequest("load", onLoadMore)}
          >
            {pendingAction === "load" || isFetchingNextPage ? (
              <>
                <CircularProgress size={14} sx={{ mr: 0.75 }} />
                Loading attributes…
              </>
            ) : isExactSearchDegraded ? (
              "Continue retained properties"
            ) : isFetchNextPageError ? (
              "Retry properties"
            ) : search.trim() ? (
              "Continue searching"
            ) : (
              "Load more attributes"
            )}
          </Button>
        )}
      </Box>
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
};

export default AttributeInventoryControls;
