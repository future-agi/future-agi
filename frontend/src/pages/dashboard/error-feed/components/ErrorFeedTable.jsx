import { useAgentImprovementFlag } from "src/sections/simulate-v2/_mock/featureFlags";
import React, { useCallback, useMemo } from "react";
import {
  Box,
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
import DataTablePagination from "src/components/data-table/DataTablePagination";
import IssueStatusChip from "./IssueStatusChip";
import ErrorStatusChip from "./ErrorStatusChip";
import ErrorSeverityBadge from "./ErrorSeverityBadge";
import ErrorTrendSparkline from "./ErrorTrendSparkline";
import { useErrorFeedList } from "src/api/errorFeed/mockAdapter";
import { useErrorFeedApiParams, useErrorFeedStore } from "../store";
import PropTypes from "prop-types";
import {
  clickable,
  focusRing,
} from "src/sections/simulate-v2/components/clickable";

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
    .replace(/([A-Z])/g, " $1")
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

// ── Fix layer chip ──────────────────────────────────────────────────────────
/* Where the fix most likely belongs. */
const FIX_LAYER_CONFIG = {
  prompt: {
    label: "Prompt",
    tip: "Most likely fixed in the agent's prompt or instructions.",
  },
  orchestration: {
    label: "Orchestration",
    tip: "Most likely fixed in how the agent's steps and tool calls are wired together.",
  },
  tools: {
    label: "Tools",
    tip: "Most likely fixed in a tool, or in how the agent calls it.",
  },
  guardrails: {
    label: "Guardrails",
    tip: "Most likely fixed in a check or guardrail around the agent.",
  },
  data: {
    label: "Data",
    tip: "Most likely fixed in the data the agent reads.",
  },
  memory: {
    label: "Memory",
    tip: "Most likely fixed in what the agent keeps between turns or hands over.",
  },
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
    <Tooltip arrow title={cfg.tip}>
      <Typography
        noWrap
        sx={{ fontSize: "12px", fontWeight: 500, color: "text.primary" }}
      >
        {cfg.label}
      </Typography>
    </Tooltip>
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
  { id: "status", label: "Status", sortable: false, width: 150 },
  { id: "traceCount", label: "Traces", sortable: true, width: 85 },
  { id: "usersAffected", label: "Users", sortable: false, width: 75 },
  { id: "fixLayer", label: "Layer", sortable: false, width: 120 },
  { id: "trends", label: "Trend (14d)", sortable: false, width: 130 },
  { id: "lastSeen", label: "Last seen", sortable: true, width: 120 },
];
/* With agent improvement on: the fix-status chip, in its own column beside
   the issue's triage status (RSI PRD: "a fix-status chip on the list"). */
const FIX_COLUMN = { id: "fix", label: "Fix", sortable: false, width: 170 };
const WITH_AGENT = [...COLUMNS.slice(0, 3), FIX_COLUMN, ...COLUMNS.slice(3)];

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
/* eslint-disable-next-line react/prop-types */
function SkeletonRow({ withAgent = false }) {
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
    <TableRow sx={{ height: 56 }}>
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
      {withAgent && (
        <TableCell>
          <Box sx={{ ...pulse, height: 14, width: 90 }} />
        </TableCell>
      )}
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
function EmptyState({ filtered, colSpan = 9 }) {
  return (
    <TableRow>
      <TableCell colSpan={colSpan} align="center" sx={{ py: 8 }}>
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
EmptyState.propTypes = { filtered: PropTypes.bool, colSpan: PropTypes.number };

// ── main component ─────────────────────────────────────────────────────────
export default function ErrorFeedTable({ selected, onSelect, onSelectAll }) {
  const theme = useTheme();
  const improving = useAgentImprovementFlag();
  const columns = improving ? WITH_AGENT : COLUMNS;
  const navigate = useNavigate();
  const { sortBy, sortDir, setSortBy, page, pageSize, setPage } =
    useErrorFeedStore();

  const apiParams = useErrorFeedApiParams();
  const { data, isLoading } = useErrorFeedList(apiParams);

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

  const rows = useMemo(() => data?.data ?? [], [data]);
  const totalCount = data?.total ?? 0;

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
              {columns.map((col) => (
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
                        ...focusRing,
                        cursor: "pointer",
                        userSelect: "none",
                        width: "fit-content",
                      }}
                      {...clickable(() => setSortBy(col.id))}
                    >
                      <Typography
                        fontSize={13}
                        fontWeight="fontWeightMedium"
                        color="text.secondary"
                      >
                        {col.label}
                      </Typography>
                      <SortIcon active={sortBy === col.id} dir={sortDir} />
                    </Stack>
                  ) : (
                    <Typography
                      fontSize={13}
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
            {isLoading ? (
              Array.from({ length: pageSize }).map((_, i) => (
                <SkeletonRow key={i} withAgent={improving} />
              ))
            ) : rows.length === 0 ? (
              <EmptyState filtered={isFiltered} colSpan={columns.length + 2} />
            ) : (
              rows.map((row) => {
                const isSelected = selected.includes(row.cluster_id);
                return (
                  <TableRow
                    key={row.cluster_id}
                    hover
                    selected={isSelected}
                    {...clickable(() => handleRowClick(row.cluster_id), {
                      role: "link",
                    })}
                    role={undefined}
                    sx={{
                      ...focusRing,
                      cursor: "pointer",
                      height: 56,
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
                          {/* The issue's id — how people find and refer to it. */}
                          <Typography
                            typography="s3"
                            color="text.secondary"
                            sx={{
                              fontFamily:
                                "ui-monospace, SFMono-Regular, Menlo, monospace",
                              fontFeatureSettings: "'tnum'",
                            }}
                          >
                            {row.cluster_id}
                          </Typography>
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
                      <ErrorSeverityBadge severity={row.severity} />
                    </TableCell>

                    {/* Status */}
                    <TableCell onClick={(e) => e.stopPropagation()}>
                      <ErrorStatusChip status={row.status} />
                    </TableCell>

                    {/* Fix */}
                    {improving && (
                      <TableCell>
                        {/* Opening the fix stops the row click itself; an empty cell opens the issue. */}
                        <IssueStatusChip issue={row} variant="text" />
                      </TableCell>
                    )}

                    {/* Traces */}
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

                    {/* Layer */}
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
      {totalCount > 0 && (
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
  selected: PropTypes.arrayOf(PropTypes.string),
  onSelect: PropTypes.func,
  onSelectAll: PropTypes.func,
};
