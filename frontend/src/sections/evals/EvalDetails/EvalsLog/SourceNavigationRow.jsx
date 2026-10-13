import React, { forwardRef } from "react";
import PropTypes from "prop-types";
import { Box, Button, CircularProgress, Typography } from "@mui/material";

const COPY = {
  loading: "Checking source...",
  no_reference: "No trace or call reference was recorded for this log.",
  unsupported_source:
    "Trace or call navigation is not available for this log source.",
  unsupported_target:
    "This evaluation targets a session. Trace or call navigation is not available here.",
  incomplete_reference:
    "This log does not contain enough source information to open a trace or call.",
  invalid_reference: "This log's source reference is invalid.",
  ambiguous_reference: "This log's source cannot be identified uniquely.",
  unavailable: "The source is unavailable or you do not have access.",
  temporarily_unavailable: "Source details could not be loaded. Try again.",
  unsupported_backend: "Source navigation is not available on this server.",
};

const SourceNavigationRow = forwardRef(function SourceNavigationRow(
  { nav, isPending, isError, error, onActivate, onRetry, disabled = false },
  ref,
) {
  const statusCode = error?.statusCode ?? error?.response?.status;
  if (isError && statusCode === 401) return null;
  const state = isError
    ? [403, 404].includes(statusCode)
      ? "unavailable"
      : "temporarily_unavailable"
    : isPending
      ? "loading"
      : nav?.status;
  const ready =
    state === "ready" && ["trace", "voice_call"].includes(nav?.kind);
  const retryable = state === "temporarily_unavailable";

  return (
    <Box
      ref={ref}
      role="status"
      aria-live="polite"
      tabIndex={-1}
      sx={{ display: "flex", alignItems: "center", flexWrap: "wrap", gap: 1 }}
    >
      {state === "loading" && <CircularProgress size={14} aria-hidden="true" />}
      <Typography variant="body2" color="text.secondary">
        {ready ? "Source:" : COPY[state] || COPY.invalid_reference}
      </Typography>
      {(ready || retryable) && (
        <Button
          type="button"
          size="small"
          variant={ready ? "contained" : "outlined"}
          disabled={disabled}
          onClick={ready ? onActivate : onRetry}
        >
          {retryable
            ? "Retry"
            : nav.kind === "trace"
              ? "View trace"
              : "View call"}
        </Button>
      )}
    </Box>
  );
});

SourceNavigationRow.propTypes = {
  nav: PropTypes.object,
  isPending: PropTypes.bool,
  isError: PropTypes.bool,
  error: PropTypes.object,
  onActivate: PropTypes.func,
  onRetry: PropTypes.func,
  disabled: PropTypes.bool,
};

export default SourceNavigationRow;
