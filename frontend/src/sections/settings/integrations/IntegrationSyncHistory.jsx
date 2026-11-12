import ErrorState from "src/components/error-state";
import { useMemo } from "react";
import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";
import StatusBadge from "src/components/status-badge/StatusBadge";
import { LoadingScreen } from "src/components/loading-screen";
import { AgGridReact } from "ag-grid-react";
import { useSyncLogs } from "src/api/integrations";
import { fDateTime } from "src/utils/format-time";
import { useAgTheme } from "src/hooks/use-ag-theme";

// eslint-disable-next-line react/prop-types
function StatusCellRenderer({ value }) {
  return <StatusBadge status={value} label={value ? undefined : "unknown"} />;
}

export default function IntegrationSyncHistory({ connectionId, isExport }) {
  const agTheme = useAgTheme();
  const { data, isLoading, isError, isRefetching, refetch } =
    useSyncLogs(connectionId);

  const columnDefs = useMemo(
    () => [
      {
        field: "started_at",
        headerName: "Time",
        flex: 1,
        valueFormatter: ({ value }) => (value ? fDateTime(value) : "—"),
      },
      // An export's sync counts the request logs it sent
      // (activities.py _sync_export_integration stores them as traces_fetched).
      ...(isExport
        ? [
            {
              field: "traces_fetched",
              headerName: "Request logs",
              minWidth: 150,
              valueFormatter: ({ value }) => value?.toLocaleString() || "0",
            },
          ]
        : [
            {
              field: "traces_fetched",
              headerName: "Traces",
              width: 100,
              valueFormatter: ({ value }) => value?.toLocaleString() || "0",
            },
            {
              field: "spans_synced",
              cellStyle: { fontVariantNumeric: "tabular-nums" },
              headerName: "Spans",
              width: 100,
              valueFormatter: ({ value }) => value?.toLocaleString() || "0",
            },
            {
              field: "scores_synced",
              cellStyle: { fontVariantNumeric: "tabular-nums" },
              headerName: "Scores",
              width: 100,
              valueFormatter: ({ value }) => value?.toLocaleString() || "0",
            },
          ]),
      {
        field: "status",
        headerName: "Status",
        width: 140,
        cellRenderer: StatusCellRenderer,
      },
      {
        field: "error_message",
        headerName: "Error",
        flex: 1,
        tooltipField: "error_message",
      },
    ],
    [isExport],
  );

  if (isLoading) {
    return <LoadingScreen variant="orbit" sx={{ height: 400 }} />;
  }

  if (isError) {
    return (
      <ErrorState
        role="alert"
        title="Failed to load sync history"
        detail="Check your connection and retry."
        onRetry={() => refetch()}
        retrying={isRefetching}
      />
    );
  }

  const rowData = Array.isArray(data) ? data : [];

  if (rowData.length === 0) {
    return (
      <Box py={4} textAlign="center">
        <Typography sx={{ typography: "s1", color: "text.secondary" }}>
          No sync history yet.
        </Typography>
      </Box>
    );
  }

  return (
    <Box sx={{ height: 400 }} className="ag-theme-quartz">
      <AgGridReact
        theme={agTheme}
        columnDefs={columnDefs}
        rowData={rowData}
        getRowId={({ data: row }) => row.id}
        domLayout="normal"
        pagination
        paginationPageSize={10}
        suppressCellFocus
        animateRows={false}
      />
    </Box>
  );
}

IntegrationSyncHistory.propTypes = {
  connectionId: PropTypes.string.isRequired,
  isExport: PropTypes.bool,
};
