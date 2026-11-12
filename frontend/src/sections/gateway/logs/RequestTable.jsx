import React, { useCallback, useEffect, useMemo, useState } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Alert from "@mui/material/Alert";
import Button from "@mui/material/Button";
import Typography from "@mui/material/Typography";
import { useTheme } from "@mui/material/styles";
import { TablePaginationCustom } from "src/components/table";
import Tooltip from "@mui/material/Tooltip";

import {
  GatewayEmptyState,
  GatewayGrid,
  GatewayStatusChip,
} from "../components/frame";
import { GatewayIcon } from "../shell/gateway-icons";
import {
  EMPTY,
  NO_PRICE,
  costTooltip,
  formatCostCell,
  isUnpriced,
} from "../utils/formatters";
import { REQUEST_TAG } from "../constants/requestTags";
import useRequestLogs from "./hooks/useRequestLogs";
import { BUILTIN_COLUMNS } from "./columns/columnModel";
import { MetadataValue } from "./columns/MetadataCell";
import { GatewayValue } from "./columns/GatewayCell";
import { exactLogTime, formatLogTime } from "./logDates";
import { formatMs, statusOf } from "./logStatus";
import { callEndOf } from "./callEnd";
import { NO_CHARGE, noChargeComputed } from "./logCost";
import { providerLabel } from "../utils/providerLabel";

// The Logs table: one dense row per request on the Future AGI grid (AG Grid
// with theme/ag-theme.js), newest first. Columns come from the column model
// (built-ins, Agent & billing, declared custom properties) and the Columns
// picker owns their order and visibility, so the grid keeps no column state
// of its own. Sorting and paging happen on the server: only the five fields
// the API can order by are sortable, and the grid never reorders a page.

// Built-in column key -> the API's ordering field.
const SORT_FIELDS = {
  startedAt: "started_at",
  statusCode: "status_code",
  latencyMs: "latency_ms",
  cost: "cost",
  totalTokens: "total_tokens",
};

const PAGE_SIZE_OPTIONS = [10, 25, 50, 100];
const DEFAULT_SORT = "-started_at";
const KEEP_PAGE_ORDER = () => 0;

// Filters that are not about which rows match.
const NOT_FILTERS = new Set(["page", "pageSize", "sort", "view"]);

const textSx = {
  display: "block",
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
};

function Text({ children, title, sx }) {
  const value =
    children === null || children === undefined || children === ""
      ? EMPTY
      : children;
  return (
    <Box component="span" title={title} sx={{ ...textSx, ...sx }}>
      {value}
    </Box>
  );
}

Text.propTypes = {
  children: PropTypes.node,
  title: PropTypes.string,
  sx: PropTypes.object,
};

function Flag({ icon, label, color }) {
  return (
    <Tooltip title={label} placement="top" arrow>
      <Box
        component="span"
        role="img"
        aria-label={label}
        sx={{ display: "inline-flex", color }}
      >
        <GatewayIcon name={icon} size={14} />
      </Box>
    </Tooltip>
  );
}

Flag.propTypes = {
  icon: PropTypes.string.isRequired,
  label: PropTypes.string.isRequired,
  color: PropTypes.string,
};

// A success is the usual row, so it reads as quiet digits; the chip is kept
// for what needs the eye: a 4xx, a 5xx, a guardrail block. A call that ended
// before its answer finished carries how it ended, in the drawer's words.
function StatusCell({ data }) {
  const status = statusOf(data);
  const callEnd = callEndOf(data);
  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "center",
        gap: 0.75,
        height: "100%",
      }}
    >
      {status.tone === "healthy" ? (
        <Box
          component="span"
          sx={{
            typography: "s2",
            color: "text.secondary",
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {status.label}
        </Box>
      ) : (
        <GatewayStatusChip
          tone={status.tone}
          label={status.label}
          showIcon={false}
        />
      )}
      {callEnd && (
        <Flag icon="alertCircle" label={callEnd.label} color="warning.dark" />
      )}
      {status.blocked && (
        <Flag
          icon="guardrails"
          label="Blocked by a guardrail"
          color="error.main"
        />
      )}
      {!status.blocked && data.guardrail_triggered && (
        <Flag
          icon="guardrails"
          label="Guardrail triggered"
          color="warning.dark"
        />
      )}
      {data.cache_hit && (
        <Flag icon="cache" label="Served from cache" color="text.secondary" />
      )}
      {data.fallback_used && (
        <Flag icon="fallbacks" label="Fallback used" color="text.secondary" />
      )}
    </Box>
  );
}

StatusCell.propTypes = { data: PropTypes.object.isRequired };

const BUILTIN_RENDERERS = {
  startedAt: ({ data }) => (
    <Text
      title={exactLogTime(data.started_at) || undefined}
      sx={{ fontVariantNumeric: "tabular-nums" }}
    >
      {formatLogTime(data.started_at)}
    </Text>
  ),
  model: ({ data }) => <Text title={data.model}>{data.model}</Text>,
  provider: ({ data }) => {
    const label = providerLabel(data.provider, data.billing_mode);
    return <Text title={label || undefined}>{label}</Text>;
  },
  application: ({ data }) => (
    <Text>{data.metadata?.[REQUEST_TAG.APPLICATION]}</Text>
  ),
  service: ({ data }) => <Text>{data.metadata?.[REQUEST_TAG.SERVICE]}</Text>,
  statusCode: StatusCell,
  latencyMs: ({ data }) => (
    <Text sx={{ fontVariantNumeric: "tabular-nums" }}>
      {formatMs(data.latency_ms)}
    </Text>
  ),
  // A request the gateway found no price for is unknown, not free; one no
  // charge was computed for (refused before it was priced) is not $0.00
  // either (logCost.js).
  cost: ({ data }) =>
    isUnpriced(data) ? (
      <Text title={NO_PRICE}>{EMPTY}</Text>
    ) : noChargeComputed(data) ? (
      <Text title={NO_CHARGE}>{EMPTY}</Text>
    ) : (
      <Text
        title={costTooltip(data.cost) ?? undefined}
        sx={{ fontVariantNumeric: "tabular-nums" }}
      >
        {formatCostCell(data.cost)}
      </Text>
    ),
  totalTokens: ({ data }) => (
    <Text
      title={`Total ${data.total_tokens ?? 0} tokens`}
      sx={{ fontVariantNumeric: "tabular-nums" }}
    >
      {`${data.input_tokens ?? 0} / ${data.output_tokens ?? 0}`}
    </Text>
  ),
  sessionId: ({ data }) => (
    <Text title={data.session_id}>{data.session_id}</Text>
  ),
};

// The grid's own minimum width per built-in (the column model's widths size
// the old table's cells) and its share of the spare room: model ids are long,
// application and service are often empty. Each minimum holds its header (the
// label, 32 px of padding and, on a sortable column, the 24 px sort arrow;
// this grid has no menu button) and its widest value, such as a timestamp or
// "$0.000999"; the ten add up to 1,184 px, so the table fits at 1440 px
// (logs/__tests__/RequestTable.fit.test.jsx). A timestamp keeps its
// milliseconds at 1280 and 1024 px (__tests__/RequestTable.timeFit.test.jsx);
// a long model id ends in an ellipsis there, its cell's title holding it.
const GRID_MIN_WIDTH = {
  startedAt: 168,
  model: 164,
  provider: 96,
  application: 110,
  service: 88,
  statusCode: 112,
  latencyMs: 112,
  cost: 120,
  totalTokens: 108,
  sessionId: 106,
};
// Provider takes a little more of the spare room: a managed request reads
// "Future AGI managed", which fits in full at 1440
// px with the default columns; at its minimum the cell's title holds it.
const FLEX = { model: 2.4, startedAt: 1.2, provider: 1.3 };

// The Columns picker owns which columns show (with saved views); AG Grid's own
// Choose Columns, reachable by right-clicking a header, would hide columns
// behind it. The header menu keeps its other items.
const withoutColumnChooser = ({ defaultItems }) =>
  defaultItems.filter((item) => item !== "columnChooser");

// The ten columns' minimums (1,184 px) fit the grid at 1440 px only: at 1280
// and 1024 px the columns run past the grid's right edge. From tablet width
// up, Timestamp, which every layout keeps first (columnModel's locked
// column), stays pinned at the left while the rest scroll, at a fixed width
// (a pinned column with flex takes a share of the whole grid); the grid
// shades the right edge while columns lie past it. A phone pins nothing: a
// pinned column would take most of its little room. null unpins: AG Grid
// keeps a pin a column def leaves undefined.
const PINNED_TIME = {
  pinned: "left",
  width: GRID_MIN_WIDTH.startedAt,
  flex: 0,
};

// Whether the window is a phone's (narrower than md), from the window's own
// width, following a resize.
function usePhoneWidth() {
  const theme = useTheme();
  const below = theme.breakpoints.values.md;
  const read = useCallback(
    () => typeof window !== "undefined" && window.innerWidth < below,
    [below],
  );
  const [phone, setPhone] = useState(read);
  useEffect(() => {
    const onResize = () => setPhone(read());
    onResize();
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [read]);
  return phone;
}

function toColDef(col, sort, { pinTime = false } = {}) {
  const base = {
    colId: col.id,
    headerName: col.label,
    minWidth: (col.kind === "builtin" && GRID_MIN_WIDTH[col.key]) || col.width,
    flex: FLEX[col.key] || 1,
    sortable: false,
    suppressHeaderMenuButton: true,
    ...(col.kind === "builtin" &&
      col.key === "startedAt" &&
      (pinTime ? PINNED_TIME : { pinned: null })),
  };
  if (col.kind === "metadata") {
    return {
      ...base,
      flex: 0,
      width: col.width,
      // eGridCell: the keyboard focuses the grid cell, and the value's
      // tooltip follows it.
      cellRenderer: ({ data, eGridCell }) => (
        <MetadataValue
          metadata={data.metadata}
          name={col.name}
          cell={eGridCell}
        />
      ),
    };
  }
  if (col.kind === "gateway") {
    return {
      ...base,
      cellRenderer: ({ data }) => <GatewayValue column={col.key} log={data} />,
    };
  }
  const field = SORT_FIELDS[col.key];
  const active = field && sort.replace(/^-/, "") === field;
  return {
    ...base,
    cellRenderer: BUILTIN_RENDERERS[col.key],
    ...(field && {
      sortable: true,
      comparator: KEEP_PAGE_ORDER,
      sortingOrder: ["desc", "asc"],
      sort: active ? (sort.startsWith("-") ? "desc" : "asc") : undefined,
    }),
  };
}

function hasFilters(filters) {
  return Object.entries(filters).some(
    ([key, value]) => !NOT_FILTERS.has(key) && value !== "" && value != null,
  );
}

const RequestTable = ({
  filters,
  setFilter,
  setFilters,
  onSelectLog,
  onClearFilters,
  columns = BUILTIN_COLUMNS,
  refetchInterval,
  onGridReady,
  onResults,
}) => {
  const page = parseInt(filters.page, 10) || 1;
  const pageSize = parseInt(filters.pageSize, 10) || 25;
  const sort = filters.sort || DEFAULT_SORT;

  const { data, isLoading, isFetching, isPlaceholderData, error, refetch } =
    useRequestLogs({
      filters,
      page,
      pageSize,
      refetchInterval,
    });

  const results = data?.result?.results ?? data?.results;
  const totalCount = data?.result?.count ?? data?.count ?? 0;

  // KEEP_PAGE_ORDER means these rows are also the visible grid order. Bind
  // them to their query: keepPreviousData must never enable traversal of a
  // new filter/sort/page using the old page's rows.
  const resultsKey = JSON.stringify(filters);
  const resultsReady = Boolean(
    Array.isArray(results) &&
      !isLoading &&
      !isFetching &&
      !isPlaceholderData &&
      !error,
  );
  useEffect(() => {
    onResults?.(results, { queryKey: resultsKey, ready: resultsReady });
  }, [onResults, results, resultsKey, resultsReady]);

  const phone = usePhoneWidth();
  const columnDefs = useMemo(
    () => columns.map((col) => toColDef(col, sort, { pinTime: !phone })),
    [columns, sort, phone],
  );

  // The grid tells us which column the user sorted; the server does the rest.
  const handleSortChanged = useCallback(
    (event) => {
      const state = event.api.getColumnState().find((c) => c.sort);
      const key = state && columns.find((c) => c.id === state.colId)?.key;
      const field = key && SORT_FIELDS[key];
      const next = field
        ? `${state.sort === "desc" ? "-" : ""}${field}`
        : DEFAULT_SORT;
      if (next === sort) return;
      setFilters({ ...filters, sort: next, page: "1" });
    },
    [columns, filters, setFilters, sort],
  );

  const handleRowClicked = useCallback(
    (event) => {
      // A link inside the row (a run id) opens its own page.
      if (event.event?.target?.closest?.("a, button")) return;
      onSelectLog(event.data.id);
    },
    [onSelectLog],
  );

  if (error && !results) {
    return (
      <GatewayEmptyState
        icon="alertCircle"
        title="Request logs could not be loaded"
        description={error.message || "The gateway API did not answer."}
        actionLabel="Retry"
        onAction={() => refetch()}
      />
    );
  }

  const filtered = hasFilters(filters);

  return (
    <Box
      sx={{
        flex: 1,
        minHeight: 0,
        display: "flex",
        flexDirection: "column",
      }}
    >
      {(isFetching || isPlaceholderData) && (
        <Typography
          role="status"
          typography="s2"
          color="text.secondary"
          sx={{ py: 1 }}
        >
          Updating requests…
          {isPlaceholderData
            ? " Showing previous results until the new query completes."
            : ""}
        </Typography>
      )}
      {error && results && (
        <Alert
          severity="warning"
          action={
            <Button
              size="small"
              color="inherit"
              disabled={isFetching}
              onClick={() => refetch()}
            >
              Retry
            </Button>
          }
        >
          Request logs could not be refreshed. Showing stale data from the last
          successful load.
        </Alert>
      )}
      <GatewayGrid
        rowData={isLoading ? undefined : results || []}
        loading={isLoading}
        columnDefs={columnDefs}
        getRowId={(params) => String(params.data.id)}
        onRowClicked={handleRowClicked}
        onGridReady={onGridReady}
        onSortChanged={handleSortChanged}
        getMainMenuItems={withoutColumnChooser}
        rowHeight={32}
        headerHeight={36}
        suppressMovableColumns
        scrollCue
        rowStyle={{ cursor: "pointer" }}
        emptyState={
          filtered
            ? {
                variant: "no-results",
                title: "No matching requests",
                description:
                  "No request matches these filters. Clear them, or widen the time range.",
                actionLabel: "Clear filters",
                onAction: onClearFilters,
              }
            : {
                icon: "logs",
                title: "No requests yet",
                description:
                  "Every request sent through the gateway shows up here, newest first, within a few seconds.",
              }
        }
      />
      {results && results.length > 0 && (
        <TablePaginationCustom
          count={totalCount}
          page={Math.max(0, page - 1)}
          rowsPerPage={pageSize}
          rowsPerPageOptions={PAGE_SIZE_OPTIONS}
          onPageChange={(_event, next) => setFilter("page", String(next + 1))}
          onRowsPerPageChange={(event) =>
            setFilters({
              ...filters,
              pageSize: String(event.target.value),
              page: "1",
            })
          }
          sx={{ flexShrink: 0, borderTop: 0 }}
        />
      )}
    </Box>
  );
};

RequestTable.propTypes = {
  filters: PropTypes.object.isRequired,
  setFilter: PropTypes.func.isRequired,
  setFilters: PropTypes.func.isRequired,
  onSelectLog: PropTypes.func.isRequired,
  onClearFilters: PropTypes.func,
  columns: PropTypes.arrayOf(PropTypes.object),
  refetchInterval: PropTypes.oneOfType([PropTypes.number, PropTypes.bool]),
  onGridReady: PropTypes.func,
  // Called with the rows the table lists, each time they change.
  onResults: PropTypes.func,
};

export { SORT_FIELDS };
export default RequestTable;
