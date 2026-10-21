import React, { useEffect } from "react";
import PropTypes from "prop-types";
import { Alert, Box, Button, Stack, Typography } from "@mui/material";
import QueryResults from "./QueryResults";
import useSQLExecution from "./useSQLExecution";

export default function SQLQueryWidget({
  widget,
  refreshRequestId,
  globalDateRange,
  dashboardId,
  onQuerySettled,
}) {
  const config = widget.query_config;
  const { job, busy, error, run, cancel } = useSQLExecution(config.project_id);
  const start = globalDateRange?.start;
  const end = globalDateRange?.end;
  useEffect(() => {
    // The server loads the saved query again; widget JSON is never executable SQL.
    run(
      {
        saved_query_id: config.saved_query_id,
        ...(start && end ? { start, end } : {}),
      },
      true,
    );
  }, [run, config.saved_query_id, refreshRequestId, start, end]);
  useEffect(() => {
    if (busy || (!job && !error)) return;
    onQuerySettled?.({
      dashboardId,
      widgetId: widget.id,
      refreshRequestId,
      manualRefresh: Boolean(refreshRequestId),
      exact: job?.state === "succeeded" && !job.result?.truncated,
      pollingPaused: false,
      updatedAt: job?.created_at,
    });
  }, [
    busy,
    job,
    error,
    dashboardId,
    widget.id,
    refreshRequestId,
    onQuerySettled,
  ]);
  return (
    <Box sx={{ p: 1, height: "100%", overflow: "auto" }}>
      {error || job?.error ? (
        <Alert
          severity="error"
          action={
            <Button
              color="inherit"
              size="small"
              onClick={() =>
                run(
                  {
                    saved_query_id: config.saved_query_id,
                    ...(start && end ? { start, end } : {}),
                  },
                  true,
                )
              }
            >
              Retry
            </Button>
          }
        >
          {error || job.error}
        </Alert>
      ) : (
        <>
          <Stack direction="row" spacing={1} alignItems="center">
            <Typography variant="caption" role="status">
              {busy ? "Query running…" : job?.state}
            </Typography>
            {busy && (
              <Button size="small" onClick={cancel}>
                Cancel query
              </Button>
            )}
          </Stack>
          <QueryResults result={job?.result} chart={widget.chart_config} />
        </>
      )}
    </Box>
  );
}
SQLQueryWidget.propTypes = {
  widget: PropTypes.object.isRequired,
  refreshRequestId: PropTypes.number,
  globalDateRange: PropTypes.object,
  dashboardId: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
  onQuerySettled: PropTypes.func,
};
