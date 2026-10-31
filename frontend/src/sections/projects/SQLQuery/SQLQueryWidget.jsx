import React, { useEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import { Alert, Box, Button, Stack, Typography } from "@mui/material";
import QueryResults from "./QueryResults";
import useSQLExecution from "./useSQLExecution";
import { getQueryPresentation } from "./queryPresentation";

export default function SQLQueryWidget({
  widget,
  refreshRequestId,
  globalDateRange,
  dashboardId,
  onQuerySettled,
}) {
  const config = widget.query_config;
  const { job, busy, error, waitingForAdmission, run, cancel } =
    useSQLExecution(config.project_id, {
      retryAdmission: true,
      cancelOnUnmount: true,
    });
  const [windowError, setWindowError] = useState("");
  const cancelRef = useRef(cancel);
  cancelRef.current = cancel;
  const start = globalDateRange?.start;
  const end = globalDateRange?.end;
  useEffect(() => {
    if (start && end) {
      const duration = Date.parse(end) - Date.parse(start);
      if (
        !Number.isFinite(duration) ||
        duration <= 0 ||
        duration > 31 * 24 * 60 * 60 * 1000
      ) {
        setWindowError(
          "SQL dashboard queries support windows up to 31 days. Narrow the dashboard date range or open the saved query.",
        );
        cancelRef.current();
        if (refreshRequestId) {
          onQuerySettled?.({
            dashboardId,
            widgetId: widget.id,
            refreshRequestId,
            manualRefresh: true,
            exact: false,
            pollingPaused: true,
            updatedAt: undefined,
          });
        }
        return;
      }
    }
    setWindowError("");
    // The server loads the saved query again; widget JSON is never executable SQL.
    run(
      {
        saved_query_id: config.saved_query_id,
        ...(start && end ? { start, end } : {}),
      },
      true,
    );
  }, [
    run,
    config.saved_query_id,
    refreshRequestId,
    start,
    end,
    dashboardId,
    widget.id,
    onQuerySettled,
  ]);
  useEffect(() => {
    if (busy || windowError || (!job && !error)) return;
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
    windowError,
  ]);
  const presentation = getQueryPresentation({
    state: job?.state,
    busy,
    waitingForAdmission,
  });
  return (
    <Box sx={{ p: 1, height: "100%", overflow: "auto" }}>
      {windowError ? (
        <Alert severity="warning">{windowError}</Alert>
      ) : error || job?.error ? (
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
              {presentation
                ? `${presentation.title}${busy ? "…" : ""}`
                : job?.state}
            </Typography>
            {busy && (
              <Button size="small" onClick={cancel}>
                Cancel query
              </Button>
            )}
          </Stack>
          <QueryResults
            result={job?.result}
            chart={widget.chart_config}
            presentation={presentation}
            showEditorGuidance={false}
          />
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
