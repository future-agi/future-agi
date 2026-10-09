import React, { useCallback, useMemo } from "react";
import PropTypes from "prop-types";
import {
  Card,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TablePagination,
  TableSortLabel,
  Typography,
  Chip,
  Skeleton,
  Stack,
  Tooltip,
  Alert,
  Button,
} from "@mui/material";
import Iconify from "src/components/iconify";
import useRequestLogs from "./hooks/useRequestLogs";
import { formatCost } from "../utils/formatters";
import { REQUEST_TAG } from "../constants/requestTags";
import { BUILTIN_COLUMNS } from "./columns/columnModel";
import MetadataCell from "./columns/MetadataCell";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

// Column definitions live in ./columns/columnModel. With no `columns` prop the
// table renders the ten built-ins in their original order (TH-7041, R43).

const PAGE_SIZE_OPTIONS = [10, 25, 50, 100];

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

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

/** Built-in cell renderers keyed by the built-in column key; bodies unchanged. */
function renderBuiltinCell(colKey, log, ctx) {
  switch (colKey) {
    case "startedAt":
      return (
        <TableCell sx={{ whiteSpace: "nowrap" }}>
          <Typography variant="body2">
            {formatTimestamp(log.started_at)}
          </Typography>
        </TableCell>
      );
    case "model":
      return (
        <TableCell>
          <Typography variant="body2" noWrap>
            {log.model || "-"}
          </Typography>
        </TableCell>
      );
    case "provider":
      return (
        <TableCell>
          <Typography variant="body2" noWrap>
            {log.provider || "-"}
          </Typography>
        </TableCell>
      );
    case "application":
      return (
        <TableCell>
          <Typography variant="body2" noWrap>
            {log.metadata?.[REQUEST_TAG.APPLICATION] || "-"}
          </Typography>
        </TableCell>
      );
    case "service":
      return (
        <TableCell>
          <Typography variant="body2" noWrap>
            {log.metadata?.[REQUEST_TAG.SERVICE] || "-"}
          </Typography>
        </TableCell>
      );
    case "statusCode":
      return (
        <TableCell>
          <Chip
            label={log.status_code ?? "-"}
            size="small"
            variant="outlined"
            color={getStatusChipColor(log.status_code)}
          />
        </TableCell>
      );
    case "latencyMs":
      return (
        <TableCell>
          <Typography
            variant="body2"
            sx={{
              color:
                ctx.latencyMs != null
                  ? getLatencyColor(ctx.latencyMs)
                  : undefined,
              fontWeight: 500,
            }}
          >
            {ctx.latencyMs != null ? `${ctx.latencyMs}ms` : "-"}
          </Typography>
        </TableCell>
      );
    case "cost":
      return (
        <TableCell>
          <Typography variant="body2">{formatCost(log.cost)}</Typography>
        </TableCell>
      );
    case "totalTokens":
      return (
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
      );
    case "sessionId":
      return (
        <TableCell>
          <Stack direction="row" spacing={0.5} alignItems="center">
            <Typography variant="body2" noWrap sx={{ maxWidth: 100 }}>
              {log.session_id || "-"}
            </Typography>

            {/* Flag icons */}
            {log.cache_hit && (
              <Tooltip title="Cache Hit" arrow>
                <Iconify
                  icon="mdi:cached"
                  width={16}
                  sx={{ color: "info.main" }}
                />
              </Tooltip>
            )}
            {ctx.guardrailTriggered && (
              <Tooltip title="Guardrail Triggered" arrow>
                <Iconify
                  icon="mdi:shield-outline"
                  width={16}
                  sx={{ color: "warning.dark" }}
                />
              </Tooltip>
            )}
            {ctx.fallbackUsed && (
              <Tooltip title="Fallback Used" arrow>
                <Iconify
                  icon="mdi:swap-horizontal"
                  width={16}
                  sx={{ color: "secondary.main" }}
                />
              </Tooltip>
            )}
          </Stack>
        </TableCell>
      );
    default:
      return <TableCell>-</TableCell>;
  }
}

const RequestRow = React.memo(function RequestRow({ log, columns, onClick }) {
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
  const ctx = { latencyMs, guardrailTriggered, fallbackUsed };

  return (
    <TableRow
      hover
      onClick={() => onClick(log.id)}
      sx={{
        cursor: "pointer",
        ...(isErrorRow && {
          boxShadow: "inset 3px 0 0 0 #FF5630",
          "& > td": {
            backgroundColor: "rgba(255, 86, 48, 0.14) !important",
          },
          "&:hover > td": {
            backgroundColor: "rgba(255, 86, 48, 0.22) !important",
          },
        }),
        ...(isWarnRow && {
          boxShadow: "inset 3px 0 0 0 #FFAB00",
          "& > td": {
            backgroundColor: "rgba(255, 171, 0, 0.12) !important",
          },
          "&:hover > td": {
            backgroundColor: "rgba(255, 171, 0, 0.20) !important",
          },
        }),
      }}
    >
      {columns.map((col) =>
        col.kind === "metadata" ? (
          <MetadataCell key={col.id} metadata={log.metadata} name={col.name} />
        ) : (
          <React.Fragment key={col.id}>
            {renderBuiltinCell(col.key, log, ctx)}
          </React.Fragment>
        ),
      )}
    </TableRow>
  );
});

RequestRow.propTypes = {
  log: PropTypes.object.isRequired,
  columns: PropTypes.arrayOf(PropTypes.object).isRequired,
  onClick: PropTypes.func.isRequired,
};

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

const RequestTable = ({
  filters,
  setFilter,
  setFilters,
  onSelectLog,
  columns = BUILTIN_COLUMNS,
}) => {
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
    <Card>
      {/* Error state */}
      {error && (
        <Alert
          severity="error"
          action={
            <Button color="inherit" size="small" onClick={() => refetch()}>
              Retry
            </Button>
          }
          sx={{ borderRadius: 0 }}
        >
          Failed to load request logs: {error.message || "Unknown error"}
        </Alert>
      )}

      <TableContainer sx={{ maxHeight: "calc(100vh - 360px)" }}>
        <Table stickyHeader size="small">
          {/* ---- Header ---- */}
          <TableHead>
            <TableRow>
              {columns.map((col) => (
                <TableCell
                  key={col.id}
                  sx={{ width: col.width, fontWeight: 600 }}
                  data-column={col.id}
                >
                  {col.sortable ? (
                    <TableSortLabel
                      active={sortField === (fieldMap[col.key] || col.key)}
                      direction={
                        sortField === (fieldMap[col.key] || col.key)
                          ? sortDir
                          : "desc"
                      }
                      onClick={() => handleSort(col.key)}
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
                  {columns.map((col) => (
                    <TableCell key={col.id}>
                      {col.key === "statusCode" ? (
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
                <RequestRow
                  key={log.id}
                  log={log}
                  columns={columns}
                  onClick={onSelectLog}
                />
              ))}

            {/* ---- Empty state ---- */}
            {!isLoading && !error && results.length === 0 && (
              <TableRow>
                <TableCell colSpan={columns.length}>
                  <Stack alignItems="center" spacing={1.5} py={6}>
                    <Iconify
                      icon="mdi:magnify-remove-outline"
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
      <TablePagination
        component="div"
        count={totalCount}
        page={page - 1}
        rowsPerPage={pageSize}
        rowsPerPageOptions={PAGE_SIZE_OPTIONS}
        onPageChange={handlePageChange}
        onRowsPerPageChange={handleRowsPerPageChange}
      />
    </Card>
  );
};

RequestTable.propTypes = {
  filters: PropTypes.object.isRequired,
  setFilter: PropTypes.func.isRequired,
  setFilters: PropTypes.func.isRequired,
  onSelectLog: PropTypes.func.isRequired,
  columns: PropTypes.arrayOf(PropTypes.object),
};

export default RequestTable;
