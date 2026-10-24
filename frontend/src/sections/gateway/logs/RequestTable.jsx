import React, { useCallback, useMemo } from "react";
import PropTypes from "prop-types";
import {
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TableSortLabel,
  Typography,
  Skeleton,
  Stack,
  Tooltip,
  Alert,
  Button,
} from "@mui/material";
import { TablePaginationCustom } from "src/components/table";
import Section from "src/components/section";
import Iconify from "src/components/iconify";
import StatusBadge from "src/components/status-badge/StatusBadge";
import { statusColors } from "src/utils/runStatus";
import useRequestLogs from "./hooks/useRequestLogs";
import { formatCost } from "../utils/formatters";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const COLUMNS = [
  { id: "startedAt", label: "Timestamp", width: 180, sortable: true },
  { id: "model", label: "Model", width: 140, sortable: false },
  { id: "provider", label: "Provider", width: 120, sortable: false },
  { id: "statusCode", label: "Status", width: 80, sortable: true },
  { id: "latencyMs", label: "Latency", width: 100, sortable: true },
  { id: "cost", label: "Cost", width: 100, sortable: true },
  { id: "totalTokens", label: "Tokens", width: 120, sortable: true },
  { id: "sessionId", label: "Session ID", width: 130, sortable: false },
];

const PAGE_SIZE_OPTIONS = [10, 25, 50, 100];

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const CHIP_ROLE = {
  success: "success",
  warning: "warning",
  error: "danger",
  info: "info",
  primary: "discovery",
  secondary: "discovery",
  default: "neutral",
};

function getStatusChipColor(code) {
  if (code === 246) return "warning"; // guardrail warn
  if (code === 446) return "error"; // guardrail block
  if (code >= 200 && code < 300) return "success";
  if (code >= 400 && code < 500) return "error";
  if (code >= 500 && code < 600) return "error";
  return "default";
}

function getLatencyColor(ms) {
  if (ms < 500) return "success.dark";
  if (ms <= 2000) return "warning.dark";
  return "error.main";
}

function formatTimestamp(iso) {
  if (!iso) return "N/A";
  try {
    const d = new Date(iso);
    return d.toLocaleString(undefined, {
      month: "short",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    });
  } catch {
    return "N/A";
  }
}

// ---------------------------------------------------------------------------
// Memoised row component
// ---------------------------------------------------------------------------

const RequestRow = React.memo(function RequestRow({ log, onClick }) {
  const startedAt = log.started_at;
  const latencyMs = log.latency_ms;
  const guardrailTriggered = log.guardrail_triggered;
  const fallbackUsed = log.fallback_used;
  const isGuardrailBlock = log.status_code === 446;
  const isGuardrailWarn = log.status_code === 246;
  const isError =
    !isGuardrailBlock &&
    !isGuardrailWarn &&
    (log.is_error || (log.status_code && log.status_code >= 400));

  const isErrorRow = isError || isGuardrailBlock;
  const isWarnRow = isGuardrailWarn;

  return (
    <TableRow
      hover
      onClick={() => onClick(log.id)}
      sx={(theme) => ({
        cursor: "pointer",
        ...(isErrorRow && {
          boxShadow: `inset 3px 0 0 0 ${statusColors(theme, "danger").solid}`,
          "& > td": {
            backgroundColor: `${statusColors(theme, "danger").bg} !important`,
          },
        }),
        ...(isWarnRow && {
          boxShadow: `inset 3px 0 0 0 ${statusColors(theme, "warning").solid}`,
          "& > td": {
            backgroundColor: `${statusColors(theme, "warning").bg} !important`,
          },
        }),
      })}
    >
      {/* Timestamp */}
      <TableCell sx={{ whiteSpace: "nowrap" }}>
        <Typography variant="body2">{formatTimestamp(startedAt)}</Typography>
      </TableCell>

      {/* Model */}
      <TableCell>
        <Typography variant="body2" noWrap>
          {log.model || "-"}
        </Typography>
      </TableCell>

      {/* Provider */}
      <TableCell>
        <Typography variant="body2" noWrap>
          {log.provider || "-"}
        </Typography>
      </TableCell>

      {/* Status */}
      <TableCell>
        <StatusBadge
          status={log.status_code}
          label={String(log.status_code ?? "-")}
          role={CHIP_ROLE[getStatusChipColor(log.status_code)]}
        />
      </TableCell>

      {/* Latency */}
      <TableCell>
        <Typography
          variant="body2"
          sx={{
            color: latencyMs != null ? getLatencyColor(latencyMs) : undefined,
            fontWeight: 500,
          }}
        >
          {latencyMs != null ? `${latencyMs}ms` : "-"}
        </Typography>
      </TableCell>

      {/* Cost */}
      <TableCell sx={{ fontVariantNumeric: "tabular-nums" }}>
        <Typography variant="body2">{formatCost(log.cost)}</Typography>
      </TableCell>

      {/* Tokens */}
      <TableCell>
        <Tooltip
          title={`Total: ${log.total_tokens ?? 0}`}
          placement="top"
          arrow
        >
          <Typography variant="body2">
            {log.input_tokens ?? 0} / {log.output_tokens ?? 0}
          </Typography>
        </Tooltip>
      </TableCell>

      {/* Session ID */}
      <TableCell>
        <Stack direction="row" spacing={0.5} alignItems="center">
          <Typography variant="body2" noWrap sx={{ maxWidth: 100 }}>
            {log.session_id || "-"}
          </Typography>

          {/* Flag icons */}
          {log.cache_hit && (
            <Tooltip title="Cache Hit" arrow>
              <Iconify
                icon="solar:refresh-linear"
                width={16}
                sx={{ color: "info.main" }}
              />
            </Tooltip>
          )}
          {guardrailTriggered && (
            <Tooltip title="Guardrail Triggered" arrow>
              <Iconify
                icon="solar:shield-linear"
                width={16}
                sx={{ color: "warning.dark" }}
              />
            </Tooltip>
          )}
          {fallbackUsed && (
            <Tooltip title="Fallback Used" arrow>
              <Iconify
                icon="solar:transfer-horizontal-linear"
                width={16}
                sx={{ color: "secondary.main" }}
              />
            </Tooltip>
          )}
        </Stack>
      </TableCell>
    </TableRow>
  );
});

RequestRow.propTypes = {
  log: PropTypes.object.isRequired,
  onClick: PropTypes.func.isRequired,
};

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

const RequestTable = ({ filters, setFilter, setFilters, onSelectLog }) => {
  // Pagination state derived from filters (URL params)
  const page = parseInt(filters.page, 10) || 1;
  const pageSize = parseInt(filters.pageSize, 10) || 25;
  const sort = filters.sort || "-started_at";

  const { data, isLoading, error, refetch } = useRequestLogs({
    filters,
    page,
    pageSize,
  });

  const results = data?.result?.results ?? data?.results ?? [];
  const totalCount = data?.result?.count ?? data?.count ?? 0;

  // --- Sort handler ---------------------------------------------------------
  const handleSort = useCallback(
    (columnId) => {
      const fieldMap = {
        startedAt: "started_at",
        statusCode: "status_code",
        latencyMs: "latency_ms",
        cost: "cost",
        totalTokens: "total_tokens",
      };
      const apiField = fieldMap[columnId] || columnId;
      const currentField = sort.replace(/^-/, "");
      const isDesc = sort.startsWith("-");

      const newSort =
        currentField === apiField
          ? isDesc
            ? apiField
            : `-${apiField}`
          : `-${apiField}`;

      setFilters({ ...filters, sort: newSort, page: "1" });
    },
    [sort, filters, setFilters],
  );

  // --- Pagination handlers --------------------------------------------------
  const handlePageChange = useCallback(
    (_event, newPage) => {
      setFilter("page", String(newPage + 1)); // MUI is 0-indexed
    },
    [setFilter],
  );

  const handleRowsPerPageChange = useCallback(
    (event) => {
      setFilters({
        ...filters,
        pageSize: String(event.target.value),
        page: "1",
      });
    },
    [filters, setFilters],
  );

  // --- Current sort state for header indicators -----------------------------
  const sortField = sort.replace(/^-/, "");
  const sortDir = sort.startsWith("-") ? "desc" : "asc";

  const fieldMap = useMemo(
    () => ({
      startedAt: "started_at",
      statusCode: "status_code",
      latencyMs: "latency_ms",
      cost: "cost",
      totalTokens: "total_tokens",
    }),
    [],
  );

  // =========================================================================
  // Render
  // =========================================================================

  return (
    <Section variant="panel" sx={{ p: 0, overflow: "hidden" }}>
      {/* Error state */}
      {error && (
        <Alert
          severity="error"
          action={
            <Button color="inherit" size="small" onClick={() => refetch()}>
              Retry
            </Button>
          }
        >
          Failed to load request logs: {error.message || "Unknown error"}
        </Alert>
      )}

      <TableContainer sx={{ maxHeight: "calc(100vh - 360px)" }}>
        <Table stickyHeader size="small">
          {/* ---- Header ---- */}
          <TableHead>
            <TableRow>
              {COLUMNS.map((col) => (
                <TableCell
                  key={col.id}
                  sx={{ width: col.width, fontWeight: 600 }}
                >
                  {col.sortable ? (
                    <TableSortLabel
                      active={sortField === (fieldMap[col.id] || col.id)}
                      direction={
                        sortField === (fieldMap[col.id] || col.id)
                          ? sortDir
                          : "desc"
                      }
                      onClick={() => handleSort(col.id)}
                    >
                      {col.label}
                    </TableSortLabel>
                  ) : (
                    col.label
                  )}
                </TableCell>
              ))}
            </TableRow>
          </TableHead>

          <TableBody>
            {/* ---- Loading skeleton ---- */}
            {isLoading &&
              Array.from({ length: 10 }).map((_, idx) => (
                <TableRow key={`skeleton-${idx}`}>
                  {COLUMNS.map((col) => (
                    <TableCell key={col.id}>
                      {col.id === "statusCode" ? (
                        <Skeleton
                          variant="rectangular"
                          width={40}
                          height={20}
                          sx={{ borderRadius: 1 }}
                        />
                      ) : (
                        <Skeleton variant="text" width="80%" />
                      )}
                    </TableCell>
                  ))}
                </TableRow>
              ))}

            {/* ---- Data rows ---- */}
            {!isLoading &&
              results.length > 0 &&
              results.map((log) => (
                <RequestRow key={log.id} log={log} onClick={onSelectLog} />
              ))}

            {/* ---- Empty state ---- */}
            {!isLoading && !error && results.length === 0 && (
              <TableRow>
                <TableCell colSpan={COLUMNS.length}>
                  <Stack alignItems="center" spacing={1.5} py={6}>
                    <Iconify
                      icon="solar:magnifier-close-linear"
                      width={48}
                      sx={{ color: "text.disabled" }}
                    />
                    <Typography variant="h6" color="text.secondary">
                      No requests found
                    </Typography>
                    <Typography variant="body2" color="text.secondary">
                      Try adjusting your filters or check that your gateway is
                      forwarding logs.
                    </Typography>
                  </Stack>
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </TableContainer>

      {/* ---- Pagination ---- */}
      <TablePaginationCustom
        count={totalCount}
        page={page - 1}
        rowsPerPage={pageSize}
        rowsPerPageOptions={PAGE_SIZE_OPTIONS}
        onPageChange={handlePageChange}
        onRowsPerPageChange={handleRowsPerPageChange}
      />
    </Section>
  );
};

RequestTable.propTypes = {
  filters: PropTypes.object.isRequired,
  setFilter: PropTypes.func.isRequired,
  setFilters: PropTypes.func.isRequired,
  onSelectLog: PropTypes.func.isRequired,
};

export default RequestTable;
