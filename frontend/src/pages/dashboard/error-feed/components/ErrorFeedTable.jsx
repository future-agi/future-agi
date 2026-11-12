import React, { useCallback, useMemo } from "react";
import {
  Alert,
  Box,
  Button,
  ButtonBase,
  Checkbox,
  IconButton,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Tooltip,
  Typography,
  useTheme,
} from "@mui/material";
import { Link as RouterLink, useNavigate } from "react-router-dom";
import { formatDistanceToNowStrict } from "date-fns";
import Iconify from "src/components/iconify";
import DataTablePagination from "src/components/data-table/DataTablePagination";
import EmptyState from "src/components/empty-state";
import ErrorState from "src/components/error-state";
import { paths } from "src/routes/paths";
import ErrorStatusChip from "./ErrorStatusChip";
import ErrorSeverityBadge from "./ErrorSeverityBadge";
import ErrorTrendSparkline from "./ErrorTrendSparkline";
import { useErrorFeedList } from "src/api/errorFeed/error-feed";
import { useErrorFeedApiParams, useErrorFeedStore } from "../store";
import PropTypes from "prop-types";
import { ROW_HEIGHT } from "src/theme/sizes";

// ── helpers ────────────────────────────────────────────────────────────────
const fmt = (n) => {
  if (n == null) return "—";
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return String(n);
};

const humanizeTime = (iso) => {
  if (!iso) return "—";
  try {
    return `${formatDistanceToNowStrict(new Date(iso))} ago`;
  } catch {
    return "—";
  }
};

function ErrorTypeTag({ type }) {
  const shortType = type
    .replace(/Error$/, "")
    // Only split camelCase boundaries (lowercase→uppercase). The old
    // `/([A-Z])/g` inserted a space before every capital, which is a no-op
    // on already-spaced strings like "Tool Selection Errors" (HTML collapses
    // the doubled space) but visibly breaks acronyms with no existing space,
    // e.g. "KB Groundedness" -> "K B Groundedness".
    .replace(/([a-z])([A-Z])/g, "$1 $2")
    .trim();
  return (
    <Tooltip arrow title={`Error type: ${shortType}`}>
      <Typography
        noWrap
        sx={{ fontSize: "12px", color: "text.tertiary", maxWidth: 140 }}
      >
        {shortType}
      </Typography>
    </Tooltip>
  );
}

ErrorTypeTag.propTypes = {
  type: PropTypes.string,
};

// Tells the row apart at a glance: a cluster is either Future AGI's built-in
// scanner or one of the customer's own eval tasks. The label carries the
// difference. Mirrors the backend's own source values
// (ClusterSource.SCANNER / .EVAL) rather than a separate display name.
function SourceTag({ source }) {
  const label = source === "eval" ? "Eval" : "Scanner";
  return (
    <Tooltip arrow title={`Source: ${label}`}>
      <Typography
        noWrap
        sx={{ fontSize: "12px", color: "text.tertiary", maxWidth: 140 }}
      >
        {label}
      </Typography>
    </Tooltip>
  );
}

SourceTag.propTypes = {
  source: PropTypes.string,
};

// ── Fix layer chip ──────────────────────────────────────────────────────────
const FIX_LAYER_CONFIG = {
  prompt: { label: "Prompt" },
  orchestration: { label: "Orchestration" },
  tools: { label: "Tools" },
  guardrails: { label: "Guardrails" },
  data: { label: "Data" },
  memory: { label: "Memory" },
};

function FixLayerChip({ layer }) {
  const theme = useTheme();
  const cfg = FIX_LAYER_CONFIG[layer];
  if (!cfg)
    return (
      <span style={{ color: theme.palette.text.disabled, fontSize: 12 }}>
        —
      </span>
    );
  return (
    <Typography
      noWrap
      sx={{ fontSize: "12px", fontWeight: 500, color: "text.primary" }}
    >
      {cfg.label}
    </Typography>
  );
}
FixLayerChip.propTypes = { layer: PropTypes.string };

// At a 1024 px window the list column is 792 px wide (sidebar 200, 16 px
// gutters). The other headers need about 598 px unwrapped, so the Error
// column's floor (and the table's) must leave the whole header in view: at
// 340 / 900 px the header ran past the edge, cut at "Trend (1". The Error
// column is `width: "auto"`, so on wider windows it still takes the spare
// space; issue names ellipsize with a tooltip as before, and rows that need
// more room scroll sideways in the container below.
const ERROR_COLUMN_MIN_WIDTH = 180;
const TABLE_MIN_WIDTH = 760;

const COLUMNS = [
  {
    id: "error",
    label: "Error",
    sortable: false,
    width: "auto",
    minWidth: ERROR_COLUMN_MIN_WIDTH,
  },
  { id: "severity", label: "Severity", sortable: true, width: 100 },
  { id: "status", label: "Status", sortable: false, width: 118 },
  { id: "traceCount", label: "Events", sortable: true, width: 85 },
  { id: "usersAffected", label: "Users", sortable: false, width: 75 },
  { id: "fixLayer", label: "Fix Layer", sortable: false, width: 120 },
  { id: "trends", label: "Trend (14d)", sortable: false, width: 130 },
  { id: "lastSeen", label: "Last seen", sortable: true, width: 120 },
];

function SortIcon({ active, dir }) {
  if (!active)
    return (
      <Iconify
        icon="solar:chevrons-up-down-linear"
        width={14}
        sx={{ opacity: 0.3, ml: 0.25 }}
      />
    );
  return (
    <Iconify
      icon={dir === "asc" ? "solar:arrow-up-linear" : "solar:arrow-down-linear"}
      width={14}
      sx={{ ml: 0.25, color: "primary.main" }}
    />
  );
}
SortIcon.propTypes = { active: PropTypes.bool, dir: PropTypes.string };

// Keyboard focus ring for the list's own controls (sort headers, issue
// links), matching DataTablePagination's.
const focusRing = {
  outline: (t) =>
    `2px solid ${t.palette.border?.focus || t.palette.primary.main}`,
  outlineOffset: 2,
};

// ── skeleton rows ──────────────────────────────────────────────────────────
function SkeletonRow() {
  const pulse = {
    bgcolor: "action.selected",
    borderRadius: "4px",
    animation: "pulse 1.4s ease-in-out infinite",
    "@keyframes pulse": {
      "0%,100%": { opacity: 1 },
      "50%": { opacity: 0.4 },
    },
  };
  return (
    <TableRow sx={{ height: ROW_HEIGHT.twoLine }}>
      <TableCell padding="checkbox">
        <Checkbox disabled size="small" />
      </TableCell>
      <TableCell>
        <Box sx={{ ...pulse, height: 14, width: "85%" }} />
      </TableCell>
      <TableCell>
        <Box sx={{ ...pulse, height: 14, width: 60 }} />
      </TableCell>
      <TableCell>
        <Box sx={{ ...pulse, height: 20, width: 70, borderRadius: "4px" }} />
      </TableCell>
      <TableCell>
        <Box sx={{ ...pulse, height: 14, width: 40 }} />
      </TableCell>
      <TableCell>
        <Box sx={{ ...pulse, height: 14, width: 35 }} />
      </TableCell>
      <TableCell>
        <Box sx={{ ...pulse, height: 14, width: 35 }} />
      </TableCell>
      <TableCell>
        <Box sx={{ ...pulse, height: 20, width: 90, borderRadius: "4px" }} />
      </TableCell>
      <TableCell>
        <Box sx={{ ...pulse, height: 36, width: "100%" }} />
      </TableCell>
      <TableCell>
        <Box sx={{ ...pulse, height: 12, width: 80 }} />
      </TableCell>
    </TableRow>
  );
}

// ── empty and load-failed states ───────────────────────────────────────────
// Checkbox column + data columns + actions column.
const COLUMN_COUNT = COLUMNS.length + 2;

const ERROR_FEED_DOCS_URL = "https://docs.futureagi.com/docs/error-feed";

// An empty list only means "no errors in this window", never "all healthy":
// name the window the list covers ("1" is the 24-hour option).
const emptyTitle = (timeRange) => {
  const days = Number(timeRange);
  if (!days) return "No errors yet";
  if (days === 1) return "No errors in the last 24 hours";
  return `No errors in the last ${days} days`;
};

function StateRow({ children }) {
  return (
    <TableRow>
      <TableCell colSpan={COLUMN_COUNT}>{children}</TableCell>
    </TableRow>
  );
}
StateRow.propTypes = { children: PropTypes.node };

// ── main component ─────────────────────────────────────────────────────────
export default function ErrorFeedTable({ selected, onSelect, onSelectAll }) {
  const theme = useTheme();
  const navigate = useNavigate();
  const { sortBy, sortDir, setSortBy, page, pageSize, setPage } =
    useErrorFeedStore();

  const apiParams = useErrorFeedApiParams();
  const { data, isLoading, isError, isPaused, isPlaceholderData, refetch } =
    useErrorFeedList(apiParams);
  // Nothing to show because the request failed, or is waiting for a
  // connection (React Query pauses offline instead of failing). Either way
  // the list is unknown, so it must not read as "no errors".
  const loadFailed = !data && (isError || isPaused);

  const timeRange = useErrorFeedStore((s) => s.timeRange);
  const clearFilters = useErrorFeedStore((s) => s.clearFilters);
  const isFiltered = useErrorFeedStore(
    (s) =>
      !!(
        s.searchQuery ||
        s.selectedProject ||
        s.selectedEnvironment ||
        s.selectedStatus ||
        s.selectedSeverity ||
        s.selectedErrorType ||
        s.selectedFixLayer ||
        s.selectedSource
      ),
  );

  const rows = useMemo(() => data?.data ?? [], [data]);
  const totalCount = data?.total ?? 0;
  // The header checkbox reflects the rows on screen, not the size of the
  // selection.
  const visibleSelected = rows.filter((r) =>
    selected.includes(r.cluster_id),
  ).length;
  // While a new search, filter, sort or page loads, the previous rows stay on
  // screen. Skeletons only stand in when there are no rows to keep, so an
  // earlier empty result never reads as the answer to the new query.
  const showSkeleton = isLoading || (isPlaceholderData && rows.length === 0);
  // Those kept rows belong to the previous query, and the view has already
  // dropped the selection for it. A row ticked now would stay selected after
  // the new rows replace it, so selection waits for them.
  const selectionLocked = Boolean(isPlaceholderData);
  // Offline, React Query holds the request for the new search, filter, sort
  // or page until the connection is back, and the previous query's rows stay
  // on screen meanwhile. Say so: otherwise they read as the answer to the new
  // query, under a "Loading page…" that never ends.
  const waitingForConnection = Boolean(isPlaceholderData && isPaused);

  const handleRowClick = useCallback(
    (clusterId) => {
      navigate(`/dashboard/error-feed/${clusterId}`);
    },
    [navigate],
  );

  const hoverBg = theme.palette.action.hover;

  const selectedBg = theme.palette.action.selected;

  return (
    <>
      {waitingForConnection && (
        <Alert severity="warning" role="status" sx={{ flexShrink: 0 }}>
          {rows.length > 0
            ? "You're offline. These results are from before your last change and will update when you reconnect."
            : "You're offline. Results will load when you reconnect."}
        </Alert>
      )}
      <TableContainer
        sx={{
          flex: 1,
          overflowX: "auto",
          overflowY: "auto",
        }}
      >
        <Table stickyHeader size="small" sx={{ minWidth: TABLE_MIN_WIDTH }}>
          {/* ── header ── */}
          <TableHead>
            <TableRow
              sx={{
                /* The product's DataTable header: no fill, a quiet 13px title. */
                "& .MuiTableCell-head": {
                  bgcolor: "background.paper",
                  /* The theme paints table heads with a grey gradient — off. */
                  backgroundImage: "none",
                  /* A sticky cell's border isn't painted in a collapsed table —
                     the divider under the header is drawn as a shadow. */
                  borderBottom: "none",
                  boxShadow: (t) => `inset 0 -1px 0 ${t.palette.divider}`,
                  py: 1.25,
                  px: 1.5,
                  whiteSpace: "nowrap",
                },
              }}
            >
              <TableCell padding="checkbox" sx={{ width: 40 }}>
                <Checkbox
                  size="small"
                  disabled={selectionLocked}
                  indeterminate={
                    visibleSelected > 0 && visibleSelected < rows.length
                  }
                  checked={rows.length > 0 && visibleSelected === rows.length}
                  onChange={(e) =>
                    onSelectAll(
                      e.target.checked,
                      rows.map((r) => r.cluster_id),
                    )
                  }
                />
              </TableCell>
              {COLUMNS.map((col) => (
                <TableCell
                  key={col.id}
                  // Sets aria-sort on the sorted column's header.
                  sortDirection={
                    col.sortable && sortBy === col.id ? sortDir : false
                  }
                  sx={{ width: col.width, minWidth: col.minWidth }}
                >
                  {col.sortable ? (
                    <ButtonBase
                      disableRipple
                      onClick={() => setSortBy(col.id)}
                      sx={{
                        gap: 0.25,
                        borderRadius: "4px",
                        "&.Mui-focusVisible": focusRing,
                      }}
                    >
                      <Typography
                        component="span"
                        typography="s3"
                        fontWeight="fontWeightMedium"
                        color="text.secondary"
                      >
                        {col.label}
                      </Typography>
                      <SortIcon active={sortBy === col.id} dir={sortDir} />
                    </ButtonBase>
                  ) : (
                    <Typography
                      typography="s3"
                      fontWeight="fontWeightMedium"
                      color="text.secondary"
                    >
                      {col.label}
                    </Typography>
                  )}
                </TableCell>
              ))}
              <TableCell sx={{ width: 40 }} />
            </TableRow>
          </TableHead>

          {/* ── body ── */}
          <TableBody>
            {loadFailed ? (
              <StateRow>
                <ErrorState
                  role="alert"
                  title="Couldn't load the Error Feed"
                  detail="Try again. If it keeps failing, check your connection."
                  onRetry={() => refetch()}
                />
              </StateRow>
            ) : showSkeleton ? (
              Array.from({ length: pageSize }).map((_, i) => (
                <SkeletonRow key={i} />
              ))
            ) : rows.length === 0 ? (
              <StateRow>
                {isFiltered ? (
                  <EmptyState
                    variant="no-results"
                    title="No errors match your filters"
                    description="Try a different search or filter."
                    onClearFilters={clearFilters}
                  />
                ) : (
                  <EmptyState
                    title={emptyTitle(timeRange)}
                    description="Errors appear once a tracing project sends traces and the scanner or an eval task runs."
                    action={
                      <Button
                        component={RouterLink}
                        to={paths.dashboard.observe}
                        size="small"
                        variant="contained"
                        color="primary"
                      >
                        Open Tracing
                      </Button>
                    }
                    docsHref={ERROR_FEED_DOCS_URL}
                  />
                )}
              </StateRow>
            ) : (
              rows.map((row) => {
                const isSelected = selected.includes(row.cluster_id);
                return (
                  <TableRow
                    key={row.cluster_id}
                    hover
                    selected={isSelected}
                    onClick={() => handleRowClick(row.cluster_id)}
                    sx={{
                      cursor: "pointer",
                      height: ROW_HEIGHT.twoLine,
                      "&.MuiTableRow-hover:hover": { bgcolor: hoverBg },
                      "&.Mui-selected": { bgcolor: selectedBg },
                      "&.Mui-selected:hover": { bgcolor: selectedBg },
                      "& .MuiTableCell-body": {
                        borderBottom: "1px solid",
                        borderColor: "divider",
                        px: 1.5,
                        py: 0,
                      },
                    }}
                  >
                    {/* Checkbox */}
                    <TableCell
                      padding="checkbox"
                      onClick={(e) => e.stopPropagation()}
                    >
                      <Checkbox
                        size="small"
                        disabled={selectionLocked}
                        checked={isSelected}
                        onChange={(e) =>
                          onSelect(row.cluster_id, e.target.checked)
                        }
                      />
                    </TableCell>

                    {/* Error name + type */}
                    <TableCell>
                      <Stack
                        direction="column"
                        gap={0.5}
                        justifyContent="center"
                      >
                        {/* A real link, so the issue opens from the keyboard
                            too. The row's click would open it a second time,
                            so the link keeps its click to itself. */}
                        <Tooltip
                          title={row.error.name}
                          placement="top-start"
                          arrow
                        >
                          <Typography
                            component={RouterLink}
                            to={`/dashboard/error-feed/${row.cluster_id}`}
                            onClick={(e) => e.stopPropagation()}
                            typography="s2_1"
                            fontWeight="fontWeightMedium"
                            color="text.primary"
                            sx={{
                              overflow: "hidden",
                              textOverflow: "ellipsis",
                              whiteSpace: "nowrap",
                              maxWidth: 420,
                              display: "block",
                              textDecoration: "none",
                              "&:focus-visible": focusRing,
                            }}
                          >
                            {row.error.name}
                          </Typography>
                        </Tooltip>
                        <Stack direction="row" alignItems="center" gap={0.75}>
                          <SourceTag source={row.source} />
                          <ErrorTypeTag type={row.error.type} />
                          {row.eval_score != null && (
                            <Typography
                              typography="s3"
                              color="text.disabled"
                              sx={{ fontFeatureSettings: "'tnum'" }}
                            >
                              eval: {row.eval_score.toFixed(2)}
                            </Typography>
                          )}
                        </Stack>
                      </Stack>
                    </TableCell>

                    {/* Severity */}
                    <TableCell>
                      <ErrorSeverityBadge
                        severity={row.severity}
                        assessmentStatus={row.severity_assessment_status}
                        source={row.severity_source}
                        reason={row.severity_reason}
                      />
                    </TableCell>

                    {/* Status */}
                    <TableCell onClick={(e) => e.stopPropagation()}>
                      <ErrorStatusChip status={row.status} />
                    </TableCell>

                    {/* Events */}
                    <TableCell>
                      <Typography
                        typography="s2_1"
                        fontWeight="fontWeightMedium"
                        color="text.primary"
                        sx={{ fontFeatureSettings: "'tnum'" }}
                      >
                        {fmt(row.trace_count)}
                      </Typography>
                    </TableCell>

                    {/* Users */}
                    <TableCell>
                      <Typography
                        typography="s2_1"
                        color="text.secondary"
                        sx={{ fontFeatureSettings: "'tnum'" }}
                      >
                        {fmt(row.users_affected)}
                      </Typography>
                    </TableCell>

                    {/* Fix Layer */}
                    <TableCell>
                      <FixLayerChip layer={row.fix_layer} />
                    </TableCell>

                    {/* Trend sparkline */}
                    <TableCell sx={{ p: "0 8px !important" }}>
                      <ErrorTrendSparkline
                        data={row.trends}
                        severity={row.severity}
                      />
                    </TableCell>

                    {/* Last seen */}
                    <TableCell>
                      <Typography typography="s3" color="text.disabled" noWrap>
                        {row.lastSeenHuman ?? humanizeTime(row.last_seen)}
                      </Typography>
                    </TableCell>

                    {/* Actions */}
                    <TableCell onClick={(e) => e.stopPropagation()}>
                      {/* Hover-only shortcut. Keyboard users open the issue
                          from its name link, so focus never lands here while
                          it is invisible. */}
                      <Tooltip title="View details" arrow>
                        <IconButton
                          size="small"
                          tabIndex={-1}
                          sx={{
                            opacity: 0,
                            transition: "opacity 0.15s",
                            ".MuiTableRow-root:hover &": { opacity: 1 },
                          }}
                          onClick={() => handleRowClick(row.cluster_id)}
                        >
                          <Iconify icon="solar:arrow-right-linear" width={16} />
                        </IconButton>
                      </Tooltip>
                    </TableCell>
                  </TableRow>
                );
              })
            )}
          </TableBody>
        </Table>
      </TableContainer>

      {/* ── Pagination ── */}
      {totalCount > 0 && (
        <DataTablePagination
          page={page}
          pageSize={pageSize}
          total={totalCount}
          loading={isPlaceholderData && !isPaused}
          onPageChange={setPage}
          onPageSizeChange={(size) =>
            useErrorFeedStore.setState({ pageSize: size, page: 0 })
          }
        />
      )}
    </>
  );
}

ErrorFeedTable.propTypes = {
  selected: PropTypes.arrayOf(PropTypes.string),
  onSelect: PropTypes.func,
  onSelectAll: PropTypes.func,
};
