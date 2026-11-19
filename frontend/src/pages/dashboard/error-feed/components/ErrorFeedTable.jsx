import React, { useCallback, useMemo } from "react";
import {
  Alert,
  Box,
  Button,
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
import { useNavigate } from "react-router-dom";
import { formatDistanceToNowStrict } from "date-fns";
import Iconify from "src/components/iconify";
import { RouterLink } from "src/routes/components";
import { paths } from "src/routes/paths";
import DataTablePagination from "src/components/data-table/DataTablePagination";
import ErrorStatusChip from "./ErrorStatusChip";
import ErrorSeverityBadge from "./ErrorSeverityBadge";
import ErrorTrendSparkline from "./ErrorTrendSparkline";
import { FEED_PAGE_STATE } from "../feedPageState";
import { useErrorFeedStore } from "../store";
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

const COLUMNS = [
  {
    id: "error",
    label: "Error",
    sortable: false,
    width: "auto",
    minWidth: 340,
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

// ── empty state ────────────────────────────────────────────────────────────
function EmptyState({ filtered }) {
  return (
    <TableRow>
      <TableCell colSpan={9} align="center" sx={{ py: 8 }}>
        <Stack alignItems="center" gap={1.5}>
          <Box
            sx={{
              width: 48,
              height: 48,
              borderRadius: "50%",
              bgcolor: "action.hover",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
            }}
          >
            <Iconify
              icon="solar:check-circle-linear"
              width={24}
              sx={{ color: "text.disabled" }}
            />
          </Box>
          <Typography
            typography="m3"
            color="text.primary"
            fontWeight="fontWeightMedium"
          >
            {filtered
              ? "No errors match your filters"
              : "No errors — everything looks good!"}
          </Typography>
          <Typography typography="s2" color="text.secondary">
            {filtered
              ? "Try adjusting your search or filter criteria."
              : "Errors captured by Future AGI will appear here."}
          </Typography>
        </Stack>
      </TableCell>
    </TableRow>
  );
}
EmptyState.propTypes = { filtered: PropTypes.bool };

// ── no Observe projects (TH-8209) ──────────────────────────────────────────
// Shown only when the Observe catalog is empty AND the feed itself returned
// nothing for the current scope. Reuses the table's in-table block treatment
// and the existing Observe route; it is not an error.
const NO_PROJECTS_TITLE = "No Observe projects in this workspace.";
const NO_PROJECTS_BODY =
  "Create an Observe project and connect your application to start collecting traces.";

function NoProjectsState() {
  return (
    <TableRow>
      <TableCell colSpan={10} align="center" sx={{ py: 8 }}>
        <Stack alignItems="center" gap={1.5} role="status">
          <Box
            sx={{
              width: 48,
              height: 48,
              borderRadius: "50%",
              bgcolor: "action.hover",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
            }}
          >
            <Iconify
              icon="mdi:folder-plus-outline"
              width={24}
              sx={{ color: "text.disabled" }}
            />
          </Box>
          <Typography
            typography="m3"
            color="text.primary"
            fontWeight="fontWeightMedium"
          >
            {NO_PROJECTS_TITLE}
          </Typography>
          <Typography typography="s2" color="text.secondary">
            {NO_PROJECTS_BODY}
          </Typography>
          <Stack direction="row" gap={1} sx={{ mt: 0.5 }}>
            <Button
              variant="contained"
              size="small"
              component={RouterLink}
              href={paths.dashboard.observe}
            >
              Open Observe
            </Button>
            <Button
              variant="outlined"
              size="small"
              component="a"
              href="https://docs.futureagi.com/docs/error-feed"
              target="_blank"
              rel="noreferrer"
            >
              Error Feed docs
            </Button>
          </Stack>
        </Stack>
      </TableCell>
    </TableRow>
  );
}

// ── failed read (TH-8209) ──────────────────────────────────────────────────
// A failed list or catalog read must never render success copy. Same Alert +
// Retry pattern as the Observe page's tab error state.
const FEED_FAILED_TITLE = "Couldn't load the Error Feed";

function FeedErrorState({ onRetry }) {
  return (
    <TableRow>
      <TableCell colSpan={10} sx={{ py: 4, px: 3 }}>
        <Alert
          severity="error"
          role="alert"
          action={
            <Button color="inherit" size="small" onClick={onRetry}>
              Retry
            </Button>
          }
        >
          {FEED_FAILED_TITLE}
        </Alert>
      </TableCell>
    </TableRow>
  );
}
FeedErrorState.propTypes = { onRetry: PropTypes.func };

// ── main component ─────────────────────────────────────────────────────────
export default function ErrorFeedTable({
  rows: rowsProp,
  totalCount = 0,
  isLoading = false,
  pageState = FEED_PAGE_STATE.FEED,
  onRetry,
  selected,
  onSelect,
  onSelectAll,
}) {
  const theme = useTheme();
  const navigate = useNavigate();
  const { sortBy, sortDir, setSortBy, page, pageSize, setPage } =
    useErrorFeedStore();

  const isFiltered = useErrorFeedStore(
    (s) =>
      !!(
        s.searchQuery ||
        s.selectedProject ||
        s.selectedEnvironment ||
        s.selectedStatus ||
        s.selectedSeverity ||
        s.selectedErrorType
      ),
  );

  const rows = useMemo(() => rowsProp ?? [], [rowsProp]);
  const showError = pageState === FEED_PAGE_STATE.ERROR;
  const showNoProjects = pageState === FEED_PAGE_STATE.NO_PROJECTS;
  const showSkeleton =
    !showError && (isLoading || pageState === FEED_PAGE_STATE.LOADING);

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
      <TableContainer
        sx={{
          flex: 1,
          overflowX: "auto",
          overflowY: "auto",
        }}
      >
        <Table stickyHeader size="small" sx={{ minWidth: 900 }}>
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
                  indeterminate={
                    selected.length > 0 && selected.length < rows.length
                  }
                  checked={rows.length > 0 && selected.length === rows.length}
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
                  sx={{ width: col.width, minWidth: col.minWidth }}
                >
                  {col.sortable ? (
                    <Stack
                      direction="row"
                      alignItems="center"
                      gap={0.25}
                      sx={{
                        cursor: "pointer",
                        userSelect: "none",
                        width: "fit-content",
                      }}
                      onClick={() => setSortBy(col.id)}
                    >
                      <Typography
                        typography="s3"
                        fontWeight="fontWeightMedium"
                        color="text.secondary"
                      >
                        {col.label}
                      </Typography>
                      <SortIcon active={sortBy === col.id} dir={sortDir} />
                    </Stack>
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
            {showError ? (
              <FeedErrorState onRetry={onRetry} />
            ) : showSkeleton ? (
              Array.from({ length: pageSize }).map((_, i) => (
                <SkeletonRow key={i} />
              ))
            ) : showNoProjects ? (
              <NoProjectsState />
            ) : rows.length === 0 ? (
              <EmptyState filtered={isFiltered} />
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
                        <Tooltip
                          title={row.error.name}
                          placement="top-start"
                          arrow
                        >
                          <Typography
                            typography="s2_1"
                            fontWeight="fontWeightMedium"
                            color="text.primary"
                            sx={{
                              overflow: "hidden",
                              textOverflow: "ellipsis",
                              whiteSpace: "nowrap",
                              maxWidth: 420,
                              display: "block",
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
                      <Tooltip title="View details" arrow>
                        <IconButton
                          size="small"
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
      {pageState === FEED_PAGE_STATE.FEED && totalCount > 0 && (
        <DataTablePagination
          page={page}
          pageSize={pageSize}
          total={totalCount}
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
  rows: PropTypes.arrayOf(PropTypes.object),
  totalCount: PropTypes.number,
  isLoading: PropTypes.bool,
  pageState: PropTypes.oneOf(Object.values(FEED_PAGE_STATE)),
  onRetry: PropTypes.func,
  selected: PropTypes.arrayOf(PropTypes.string),
  onSelect: PropTypes.func,
  onSelectAll: PropTypes.func,
};
