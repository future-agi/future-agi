import React, { useState, useMemo, useCallback } from "react";
import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";
import { format, isValid } from "date-fns";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip/CustomTooltip";
import { CallLogsStatus } from "src/sections/agents/CallLogs/CallLogsStatus";
import { useCallLogs } from "src/sections/agents/helper";
import DataTable from "./DataTable";

// ---------------------------------------------------------------------------
// Cell renderers — plain React, no AgGrid dependency
// ---------------------------------------------------------------------------

function CallSummaryCell({ row }) {
  const createdAt = row?.created_at ? new Date(row.created_at) : null;
  const formattedDate =
    createdAt && isValid(createdAt)
      ? format(createdAt, "MM/dd/yyyy, hh:mmaaa")
      : row?.created_at?.split("T")[0] ?? "-";
  const summary = row?.call_summary ?? "";

  return (
    <Box sx={{ display: "flex", flexDirection: "column", py: 1 }}>
      <Typography variant="caption" color="text.primary">
        {formattedDate}
      </Typography>
      <CustomTooltip show={!!summary} arrow title={summary}>
        <Typography
          variant="caption"
          color="text.secondary"
          noWrap
          sx={{ maxWidth: 400 }}
        >
          {summary || "-"}
        </Typography>
      </CustomTooltip>
    </Box>
  );
}

CallSummaryCell.propTypes = { row: PropTypes.object };

function ParticipantCell({ row }) {
  const name = row?.customer_name ?? "-";
  const isInbound = row?.call_type === "inbound";

  return (
    <Box sx={{ display: "flex", flexDirection: "column", py: 1 }}>
      <CustomTooltip show={!!name} arrow title={name}>
        <Typography variant="body2" fontWeight={500} noWrap>
          {name}
        </Typography>
      </CustomTooltip>
      <Typography variant="caption" color="text.primary">
        {isInbound ? "Inbound" : "Outbound"}
      </Typography>
    </Box>
  );
}

ParticipantCell.propTypes = { row: PropTypes.object };

function DurationCell({ row }) {
  const totalSeconds = Number(row?.duration_seconds) || 0;
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  const formatted = `${minutes}:${seconds.toString().padStart(2, "0")}`;

  return (
    <Box display="flex" alignItems="center" gap={0.5}>
      <Iconify
        icon="solar:clock-circle-linear"
        width={14}
        height={14}
        sx={{ color: "text.primary" }}
      />
      <Typography typography="s1">{formatted}</Typography>
    </Box>
  );
}

DurationCell.propTypes = { row: PropTypes.object };

function StatusCell({ row }) {
  return <CallLogsStatus value={row?.status} />;
}

StatusCell.propTypes = { row: PropTypes.object };

// ---------------------------------------------------------------------------
// Numeric formatters for system metrics
// ---------------------------------------------------------------------------
const formatPercent = (v) => {
  const n = v != null ? Number(v) : NaN;
  return Number.isFinite(n) ? `${n}%` : "-";
};

const formatMs = (v) => {
  const n = v != null ? Number(v) : NaN;
  return Number.isFinite(n) ? `${n}ms` : "-";
};

const formatInt = (v) => {
  const n = v != null ? Number(v) : NaN;
  return Number.isFinite(n) ? String(n) : "-";
};

// ---------------------------------------------------------------------------
// Column definitions
// ---------------------------------------------------------------------------
const BASE_COLUMNS = [
  {
    id: "call_summary",
    headerName: "Call Details",
    field: "call_summary",
    flex: 2,
    minWidth: 220,
    renderCell: ({ row }) => <CallSummaryCell row={row} />,
  },
  {
    id: "customer_name",
    headerName: "Participant",
    field: "customer_name",
    flex: 1,
    minWidth: 160,
    renderCell: ({ row }) => <ParticipantCell row={row} />,
  },
  {
    id: "duration_seconds",
    headerName: "Duration",
    field: "duration_seconds",
    flex: 1,
    minWidth: 120,
    renderCell: ({ row }) => <DurationCell row={row} />,
  },
  {
    id: "status",
    headerName: "Status",
    field: "status",
    flex: 1,
    minWidth: 120,
    renderCell: ({ row }) => <StatusCell row={row} />,
  },
];

const SYSTEM_METRIC_COLUMNS = [
  {
    id: "turn_count",
    headerName: "Turn Count",
    field: "turn_count",
    flex: 1,
    minWidth: 140,
    valueFormatter: formatInt,
  },
  {
    id: "agent_talk_percentage",
    headerName: "Agent Talk (%)",
    field: "agent_talk_percentage",
    flex: 1,
    minWidth: 170,
    valueFormatter: formatPercent,
  },
  {
    id: "avg_agent_latency_ms",
    headerName: "Agent Latency (ms)",
    field: "avg_agent_latency_ms",
    flex: 1,
    minWidth: 170,
    valueFormatter: formatMs,
  },
  {
    id: "user_wpm",
    headerName: "User WPM",
    field: "user_wpm",
    flex: 1,
    minWidth: 130,
    valueFormatter: formatInt,
  },
  {
    id: "bot_wpm",
    headerName: "Agent WPM",
    field: "bot_wpm",
    flex: 1,
    minWidth: 130,
    valueFormatter: formatInt,
  },
  {
    id: "user_interruption_count",
    headerName: "User Interruptions",
    field: "user_interruption_count",
    flex: 1,
    minWidth: 170,
    valueFormatter: formatInt,
  },
  {
    id: "ai_interruption_count",
    headerName: "Agent Interruptions",
    field: "ai_interruption_count",
    flex: 1,
    minWidth: 170,
    valueFormatter: formatInt,
  },
];

// ---------------------------------------------------------------------------
// VoiceCallsGrid — uses DataTable + useCallLogs to render voice call logs.
// ---------------------------------------------------------------------------
export default function VoiceCallsGrid({
  projectId,
  selectable = false,
  selectedIds,
  onSelectionChange,
  onRowClick,
  showSystemMetrics = true,
  enabled = true,
  sx,
}) {
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);

  const { data, isLoading } = useCallLogs({
    module: "project",
    id: projectId,
    page,
    pageLimit: pageSize,
    params: { project_id: projectId },
    enabled: enabled && !!projectId,
  });

  const rows = useMemo(() => data?.results || [], [data]);
  const totalPages = data?.total_pages || 1;

  const columns = useMemo(
    () =>
      showSystemMetrics
        ? [...BASE_COLUMNS, ...SYSTEM_METRIC_COLUMNS]
        : BASE_COLUMNS,
    [showSystemMetrics],
  );

  const handlePageChange = useCallback((newPage) => setPage(newPage), []);
  const handlePageSizeChange = useCallback((newSize) => {
    setPage(1);
    setPageSize(newSize);
  }, []);

  const getRowId = useCallback((row) => row.trace_id || row.id, []);

  return (
    <DataTable
      columns={columns}
      rows={rows}
      loading={isLoading}
      page={page}
      pageSize={pageSize}
      totalPages={totalPages}
      onPageChange={handlePageChange}
      onPageSizeChange={handlePageSizeChange}
      selectable={selectable}
      selectedIds={selectedIds}
      onSelectionChange={onSelectionChange}
      getRowId={getRowId}
      onRowClick={onRowClick}
      rowHeight={72}
      emptyTitle="No voice calls found"
      emptyDescription="There are no voice call logs for this project yet."
      sx={sx}
    />
  );
}

VoiceCallsGrid.propTypes = {
  projectId: PropTypes.string.isRequired,
  selectable: PropTypes.bool,
  selectedIds: PropTypes.instanceOf(Set),
  onSelectionChange: PropTypes.func,
  onRowClick: PropTypes.func,
  showSystemMetrics: PropTypes.bool,
  enabled: PropTypes.bool,
  sx: PropTypes.object,
};
