import React from "react";
import PropTypes from "prop-types";
import Alert from "@mui/material/Alert";
import Button from "@mui/material/Button";

export default function ToolInventoryNotice({ known, loading, error, fetching, onRetry }) {
  if (known && !error) return null;
  return (
    <Alert
      severity={error ? "warning" : "info"}
      action={onRetry && (error || !loading) ? (
        <Button aria-label="Retry tool inventory" disabled={fetching} onClick={onRetry}>
          Retry
        </Button>
      ) : undefined}
    >
      {error
        ? known
          ? "Tool inventory is stale. The refresh failed; candidate counts and previews use the last loaded tools."
          : "Tool inventory is unavailable. Candidate counts are unknown; retry to load tools."
        : "Tool inventory has not loaded yet. Candidate counts are unknown."}
      {" This does not change the configured tools or run a tool."}
    </Alert>
  );
}

ToolInventoryNotice.propTypes = {
  known: PropTypes.bool.isRequired,
  loading: PropTypes.bool,
  error: PropTypes.bool,
  fetching: PropTypes.bool,
  onRetry: PropTypes.func,
};
