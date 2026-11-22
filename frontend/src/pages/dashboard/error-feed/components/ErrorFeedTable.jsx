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
import ErrorTrendSparkline from "./ErrorTrendSparkline";
import {
  issueEpochStatus,
  issueKey,
  useIssueEpochs,
} from "src/api/errorFeed/epochFromIssue";
import EpochIssueIcon from "../epoch/EpochIssueIcon";
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

const SEVERITY_META = {
  critical: {
    label: "Critical",
    dot: "status.danger.solid",
    text: "status.danger.fg",
  },
  high: {
    label: "High",
    dot: "status.warning.fg",
    text: "text.primary",
  },
  medium: {
    label: "Medium",
    dot: "text.secondary",
    text: "text.secondary",
  },
  low: {
    label: "Low",
    dot: "text.disabled",
    text: "text.tertiary",
  },
};

const COLUMNS = [
  {
    id: "error",
    label: "Issue",
    sortable: false,
    width: "auto",
    minWidth: 340,
  },
  { id: "project", label: "Project", sortable: false, width: 152 },
  { id: "severity", label: "Severity", sortable: true, width: 96 },
  { id: "status", label: "Status", sortable: false, width: 120 },
  {
    id: "traceCount",
    label: "Events",
    sortable: true,
    width: 76,
    align: "right",
  },
  {
    id: "usersAffected",
    label: "Users",
    sortable: false,
    width: 66,
    align: "right",
  },
  { id: "trends", label: "Activity (7d)", sortable: false, width: 102 },
  {
    id: "lastSeen",
    label: "Last seen",
    sortable: true,
    width: 110,
    align: "right",
  },
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
        <Box sx={{ ...pulse, height: 14, width: 100 }} />
      </TableCell>
      <TableCell>
        <Box sx={{ ...pulse, height: 14, width: 64 }} />
      </TableCell>
      <TableCell>
        <Box sx={{ ...pulse, height: 20, width: 78, borderRadius: "4px" }} />
      </TableCell>
      <TableCell align="right">
        <Box sx={{ ...pulse, height: 14, width: 32, ml: "auto" }} />
      </TableCell>
      <TableCell align="right">
        <Box sx={{ ...pulse, height: 14, width: 28, ml: "auto" }} />
      </TableCell>
      <TableCell>
        <Box sx={{ ...pulse, height: 20, width: 76, borderRadius: "4px" }} />
      </TableCell>
      <TableCell align="right">
        <Box sx={{ ...pulse, height: 12, width: 72, ml: "auto" }} />
      </TableCell>
    </TableRow>
  );
}

// ── empty state ────────────────────────────────────────────────────────────
function EmptyState({ filtered }) {
  return (
    <TableRow>
      <TableCell colSpan={10} align="center" sx={{ py: 8 }}>
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
  // Each visible issue's latest epoch, in one read for the page.
  const { jobs: epochs } = useIssueEpochs(rows.map((r) => r.cluster_id));
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
                /* The product's DataTable header: no fill, a quiet title. */
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
                  align={col.align || "left"}
                  sx={{ width: col.width, minWidth: col.minWidth }}
                >
                  {col.sortable ? (
                    <Stack
                      direction="row"
                      alignItems="center"
                      justifyContent={
                        col.align === "right" ? "flex-end" : "flex-start"
                      }
                      gap={0.25}
                      sx={{
                        cursor: "pointer",
                        userSelect: "none",
                        ml: col.align === "right" ? "auto" : 0,
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
              <TableCell sx={{ width: 36 }} />
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

                    {/* Issue: crisp title on line 1, plain-English cause sentence on line 2 */}
                    <TableCell>
                      <Stack
                        direction="column"
                        gap={0.35}
                        justifyContent="center"
                      >
                        <Stack direction="row" alignItems="center" gap={0.75}>
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
                                maxWidth: 480,
                                display: "block",
                              }}
                            >
                              {row.error.name}
                            </Typography>
                          </Tooltip>
                          <EpochIssueIcon
                            status={issueEpochStatus(
                              epochs[issueKey(row.project_id, row.cluster_id)],
                            )}
                          />
                        </Stack>
                        <Stack
                          direction="row"
                          alignItems="center"
                          gap={0.75}
                          sx={{ minWidth: 0 }}
                        >
                          {row.root_cause_preview ? (
                            <Typography
                              typography="s3"
                              color="text.tertiary"
                              noWrap
                              sx={{ maxWidth: 480 }}
                            >
                              {row.root_cause_preview}
                            </Typography>
                          ) : (
                            <>
                              <SourceTag source={row.source} />
                              <ErrorTypeTag type={row.error.type} />
                            </>
                          )}
                        </Stack>
                      </Stack>
                    </TableCell>

                    {/* Project (identical name across list & detail) */}
                    <TableCell>
                      <Tooltip title={row.project || "—"} arrow>
                        <Typography
                          typography="s3"
                          color="text.secondary"
                          noWrap
                          sx={{ maxWidth: 144, display: "block" }}
                        >
                          {row.project || "—"}
                        </Typography>
                      </Tooltip>
                    </TableCell>

                    {/* Readable sortable Severity column */}
                    <TableCell>
                      {(() => {
                        const sev =
                          SEVERITY_META[row.severity] || SEVERITY_META.medium;
                        return (
                          <Stack direction="row" alignItems="center" gap={0.75}>
                            <Box
                              sx={{
                                width: 7,
                                height: 7,
                                borderRadius: "50%",
                                flexShrink: 0,
                                bgcolor: sev.dot,
                              }}
                            />
                            <Typography
                              typography="s3"
                              fontWeight={500}
                              color={sev.text}
                            >
                              {sev.label}
                            </Typography>
                          </Stack>
                        );
                      })()}
                    </TableCell>

                    {/* Status */}
                    <TableCell onClick={(e) => e.stopPropagation()}>
                      <ErrorStatusChip status={row.status} />
                    </TableCell>

                    {/* Events */}
                    <TableCell align="right">
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
                    <TableCell align="right">
                      <Typography
                        typography="s2_1"
                        color="text.secondary"
                        sx={{ fontFeatureSettings: "'tnum'" }}
                      >
                        {fmt(row.users_affected)}
                      </Typography>
                    </TableCell>

                    {/* Discrete 7d daily activity sparkline */}
                    <TableCell>
                      <ErrorTrendSparkline
                        data={row.trends}
                        days={7}
                        totalEvents={row.trace_count}
                      />
                    </TableCell>

                    {/* Last seen */}
                    <TableCell align="right">
                      <Typography typography="s3" color="text.secondary" noWrap>
                        {row.lastSeenHuman ?? humanizeTime(row.last_seen)}
                      </Typography>
                    </TableCell>

                    {/* Actions */}
                    <TableCell
                      onClick={(e) => e.stopPropagation()}
                      sx={{ pr: 1 }}
                    >
                      <Tooltip title="View details" arrow>
                        <IconButton
                          size="small"
                          aria-label={`Open ${row.error.name}`}
                          sx={{
                            color: "text.tertiary",
                            "&:hover": { color: "text.primary" },
                          }}
                          onClick={() => handleRowClick(row.cluster_id)}
                        >
                          <Iconify icon="solar:arrow-right-linear" width={15} />
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

      {/* ── Pagination (full-width top divider; inner controls clear bottom-right FAB) ── */}
      {pageState === FEED_PAGE_STATE.FEED && totalCount > 0 && (
        <Box
          sx={{
            borderTop: "1px solid",
            borderColor: "divider",
            pr: 7,
            "& > *": { borderTop: "none !important" },
          }}
        >
          <DataTablePagination
            page={page}
            pageSize={pageSize}
            total={totalCount}
            onPageChange={setPage}
            onPageSizeChange={(size) =>
              useErrorFeedStore.setState({ pageSize: size, page: 0 })
            }
          />
        </Box>
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
