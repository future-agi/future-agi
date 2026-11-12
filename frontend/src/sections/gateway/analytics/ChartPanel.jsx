import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Skeleton from "@mui/material/Skeleton";
import Typography from "@mui/material/Typography";
import Alert from "@mui/material/Alert";
import Button from "@mui/material/Button";
import Stack from "@mui/material/Stack";

// One notice per endpoint, not one per chart. A failed first load must not
// expose zero-valued summaries or success-shaped empty panels. Same-query
// cached data may remain visible after a failed refresh, explicitly stale.
export function AnalyticsQueryState({
  title,
  error,
  hasData,
  isFetching,
  onRetry,
  children,
}) {
  return (
    <Stack spacing={1.5} sx={{ minWidth: 0 }}>
      {error && (
        <Alert
          severity={hasData ? "warning" : "error"}
          action={
            <Button
              color="inherit"
              size="small"
              disabled={isFetching}
              onClick={onRetry}
            >
              {isFetching ? "Retrying…" : "Retry"}
            </Button>
          }
        >
          <Typography component="div" typography="s1">
            {`${title} could not be ${hasData ? "refreshed" : "loaded"}`}
          </Typography>
          <Typography component="div" typography="s2">
            {hasData
              ? "Showing stale data from the last successful load."
              : "Try again to load this time range."}
          </Typography>
        </Alert>
      )}
      {(!error || hasData) && children}
    </Stack>
  );
}

AnalyticsQueryState.propTypes = {
  title: PropTypes.string.isRequired,
  error: PropTypes.any,
  hasData: PropTypes.bool,
  isFetching: PropTypes.bool,
  onRetry: PropTypes.func,
  children: PropTypes.node,
};

// One analytics panel: a bordered box with a quiet heading (and an optional
// control on the right, and an optional one-line caption under it), a
// skeleton while loading and one line when there is nothing to draw. Every
// chart and table on the Analytics page sits in one.
export default function ChartPanel({
  title,
  action,
  caption,
  loading = false,
  empty = false,
  emptyText = "No requests in this range.",
  height = 280,
  children,
}) {
  return (
    <Box
      component="section"
      aria-label={title}
      sx={{
        minWidth: 0,
        p: 1.5,
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        bgcolor: "background.paper",
      }}
    >
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          flexWrap: "wrap",
          gap: 1,
          mb: 1,
        }}
      >
        <Typography
          component="h2"
          typography="s1"
          fontWeight="fontWeightMedium"
        >
          {title}
        </Typography>
        {action}
      </Box>
      {caption && (
        <Typography
          typography="s2"
          color="text.secondary"
          sx={{ mt: -0.5, mb: 1 }}
        >
          {caption}
        </Typography>
      )}
      {loading && <Skeleton variant="rounded" height={height} />}
      {!loading && empty && (
        <Box
          sx={{
            height: Math.min(height, 160),
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
          }}
        >
          <Typography typography="s2" color="text.secondary">
            {emptyText}
          </Typography>
        </Box>
      )}
      {!loading && !empty && children}
    </Box>
  );
}

ChartPanel.propTypes = {
  title: PropTypes.string.isRequired,
  action: PropTypes.node,
  caption: PropTypes.node,
  loading: PropTypes.bool,
  empty: PropTypes.bool,
  emptyText: PropTypes.string,
  height: PropTypes.number,
  children: PropTypes.node,
};
