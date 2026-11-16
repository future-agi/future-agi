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
import {
  useErrorFeedList,
  useObserveProjectList,
} from "src/api/errorFeed/mockAdapter";
import { useErrorFeedApiParams, useErrorFeedStore } from "../store";
import PropTypes from "prop-types";
import { ROW_HEIGHT } from "src/theme/sizes";
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

/* The small tags under an issue's name, as production shows them (#64):
   an icon and a word on a quiet fill, so where it came from and what kind
   of error it is read at a glance. */
function RowTag({ icon, label, tip }) {
  return (
    <Tooltip arrow title={tip}>
      <Stack
        direction="row"
        alignItems="center"
        spacing={0.5}
        sx={{
          height: 20,
          px: 0.75,
          borderRadius: 0.5,
          bgcolor: "background.neutral",
          color: "text.secondary",
          maxWidth: 170,
          flexShrink: 0,
        }}
      >
        <Iconify icon={icon} width={12} sx={{ flexShrink: 0 }} />
        <Typography noWrap sx={{ typography: "s3", fontWeight: 500 }}>
          {label}
        </Typography>
      </Stack>
    </Tooltip>
  );
}
RowTag.propTypes = {
  icon: PropTypes.string,
  label: PropTypes.string,
  tip: PropTypes.string,
};

/* Scanner (Future AGI's own checks) or Eval (one of the customer's evals) —
   the backend's own source values. */
function SourceTag({ source }) {
  const evalSource = source === "eval";
  return (
    <RowTag
      icon="solar:tag-linear"
      label={evalSource ? "Eval" : "Scanner"}
      tip={
        evalSource
          ? "Source: Eval — found by one of your evals"
          : "Source: Scanner — found by Future AGI's trace scanner"
      }
    />
  );
}
SourceTag.propTypes = { source: PropTypes.string };

const shortTypeOf = (type = "") =>
  type
    .replace(/Error$/, "")
    .replace(/([a-z])([A-Z])/g, "$1 $2")
    .trim();

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
/* A fix shows as one icon after the issue's name, not a column (#63). */

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
  const columns = COLUMNS;
  const navigate = useNavigate();
  const { sortBy, sortDir, setSortBy, page, pageSize, setPage } =
    useErrorFeedStore();

  /* Each issue's Observe project, by name (#64). */
  const { data: projects } = useObserveProjectList();
  const projectName = useMemo(
    () => Object.fromEntries((projects || []).map((p) => [p.value, p.label])),
    [projects],
  );

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
            {isLoading ? (
              Array.from({ length: pageSize }).map((_, i) => (
                <SkeletonRow key={i} />
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
                        <Stack
                          direction="row"
                          alignItems="center"
                          spacing={0.75}
                          sx={{ minWidth: 0 }}
                        >
                          <Tooltip
                            title={`${row.error.name} · ${row.cluster_id}`}
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
                          {improving && (
                            <IssueStatusChip issue={row} variant="icon" />
                          )}
                        </Stack>
                        <Stack direction="row" alignItems="center" gap={0.75}>
                          <SourceTag source={row.source} />
                          {/* One tag (where it came from); the type and the
                              project as quiet text; the eval score on hover. */}
                          <Tooltip
                            arrow
                            placement="bottom-start"
                            title={[
                              `Error type: ${shortTypeOf(row.error.type)}`,
                              projectName[row.project_id] &&
                                `Project: ${projectName[row.project_id]}`,
                              row.eval_score != null &&
                                `Eval score: ${row.eval_score.toFixed(2)}`,
                            ]
                              .filter(Boolean)
                              .join(" · ")}
                          >
                            <Typography
                              noWrap
                              sx={{
                                typography: "s3",
                                color: "text.tertiary",
                                minWidth: 0,
                              }}
                            >
                              {[
                                shortTypeOf(row.error.type),
                                projectName[row.project_id],
                              ]
                                .filter(Boolean)
                                .join(" · ")}
                            </Typography>
                          </Tooltip>
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
