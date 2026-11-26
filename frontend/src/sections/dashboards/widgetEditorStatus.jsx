import React from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Box,
  Button,
  CircularProgress,
  Stack,
  Typography,
} from "@mui/material";

export function WidgetPreviewStatus({ state, onRetry }) {
  if (state === "loading") {
    return <CircularProgress size={24} aria-label="Loading preview" />;
  }
  if (state === "preparing") {
    return (
      <Stack alignItems="center" spacing={1} role="status">
        <CircularProgress size={24} />
        <Typography variant="body2" color="text.secondary">
          Preparing data…
        </Typography>
      </Stack>
    );
  }
  if (state === "failed") {
    return (
      <Alert
        severity="warning"
        action={
          <Button color="inherit" size="small" onClick={onRetry}>
            Retry
          </Button>
        }
      >
        Data could not be prepared. Try again or narrow the time range.
      </Alert>
    );
  }
  return null;
}

WidgetPreviewStatus.propTypes = {
  state: PropTypes.oneOf(["loading", "preparing", "failed", "ready"])
    .isRequired,
  onRetry: PropTypes.func,
};

export function WidgetEditorLoadFailure({ kind, onRetry, onBack }) {
  const isMissing = kind === "missing";
  return (
    <Box sx={{ p: 3 }}>
      <Alert
        severity={isMissing ? "warning" : "error"}
        action={
          <Stack direction="row" spacing={1}>
            {!isMissing && (
              <Button color="inherit" size="small" onClick={onRetry}>
                Retry
              </Button>
            )}
            <Button color="inherit" size="small" onClick={onBack}>
              Back to dashboard
            </Button>
          </Stack>
        }
      >
        {isMissing
          ? "This widget is no longer available."
          : "The widget could not be loaded."}
      </Alert>
    </Box>
  );
}

WidgetEditorLoadFailure.propTypes = {
  kind: PropTypes.oneOf(["error", "missing"]).isRequired,
  onRetry: PropTypes.func,
  onBack: PropTypes.func.isRequired,
};
