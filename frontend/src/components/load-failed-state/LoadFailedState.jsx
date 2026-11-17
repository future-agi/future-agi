import React from "react";
import PropTypes from "prop-types";
import Button from "@mui/material/Button";
import Paper from "@mui/material/Paper";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import Iconify from "src/components/iconify";

// What a page shows when the data it needs failed to load: say so, offer a
// retry, and show no default values in its place (a $0 or "Free plan" drawn
// from a failed request reads as a fact).
export default function LoadFailedState({
  title,
  detail = "The request failed. Try again in a moment.",
  onRetry,
  retrying = false,
  sx,
}) {
  return (
    <Paper
      variant="outlined"
      role="alert"
      data-testid="load-failed-state"
      sx={{ p: 3, borderRadius: 2, borderStyle: "dashed", ...sx }}
    >
      <Stack alignItems="center" spacing={1.5} sx={{ textAlign: "center" }}>
        <Iconify
          icon="mdi:cloud-alert-outline"
          width={36}
          sx={{ color: "text.secondary" }}
        />
        <Typography variant="subtitle1" fontWeight={600}>
          {title}
        </Typography>
        <Typography variant="body2" color="text.secondary">
          {detail}
        </Typography>
        {onRetry && (
          <Button
            variant="outlined"
            color="inherit"
            size="small"
            onClick={() => onRetry()}
            disabled={retrying}
          >
            {retrying ? "Retrying…" : "Retry"}
          </Button>
        )}
      </Stack>
    </Paper>
  );
}

LoadFailedState.propTypes = {
  title: PropTypes.string.isRequired,
  detail: PropTypes.string,
  onRetry: PropTypes.func,
  retrying: PropTypes.bool,
  sx: PropTypes.object,
};
