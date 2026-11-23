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
import { paths } from "src/routes/paths";
import { OBSERVE_PROJECTS } from "src/sections/simulate-v2/_mock/connectAgent";
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

const shortTypeOf = (type = "") =>
  type
    .replace(/Error$/, "")
    .replace(/([a-z])([A-Z])/g, "$1 $2")
    .trim();

/* Where the fix most likely belongs — said in the title's tooltip; the
   list has no Layer column or filter (Kartik's review, C10). */
const FIX_LAYER_LABEL = {
  prompt: "Prompt",
  orchestration: "Orchestration",
  tools: "Tools",
  guardrails: "Guardrails",
  data: "Data",
  memory: "Memory",
};

/* Kartik's review (C10): the issue's epoch reads in its own column, in
   words, right after its triage status — no icon after the name. */
const COLUMNS = [
  {
    id: "issue",
    label: "Issue",
    sortable: false,
    width: "auto",
    minWidth: 170,
  },
  { id: "severity", label: "Severity", sortable: true, width: 95 },
  { id: "status", label: "Status", sortable: false, width: 125 },
  { id: "epoch", label: "Epoch", sortable: false, width: 200, improving: true },
  { id: "traceCount", label: "Events", sortable: true, width: 75 },
  { id: "trends", label: "Trend (14d)", sortable: false, width: 90 },
  { id: "lastSeen", label: "Last seen", sortable: true, width: 100 },
];

const SAMPLE_PROJECT_IDS = new Set(OBSERVE_PROJECTS.map((p) => p.id));

/* Rows under their Observe project, in the order the sort puts them: a
   project comes where its first issue would. */
const groupByProject = (rows) => {
  const groups = new Map();
  rows.forEach((row) => {
    const key = row.project_id || "";
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(row);
  });
  return [...groups].map(([project, items]) => ({ project, items }));
};

/* "{Project} · N issues" above the project's rows. No band fill; the name
   is a link to the project, the count quiet beside it. */
function ProjectGroupRow({ name, count, colSpan, onOpen }) {
  return (
    <TableRow>
      <TableCell
        colSpan={colSpan}
        sx={{
          height: ROW_HEIGHT.default,
          py: 0,
          px: 1.5,
          borderBottom: "1px solid",
          borderColor: "divider",
        }}
      >
        <Stack direction="row" alignItems="baseline" gap={0.75}>
          <Tooltip
            describeChild
            arrow
            placement="top-start"
            title="Open this project in Tracing"
          >
            <Typography
              component="span"
              {...clickable(onOpen, { role: "link" })}
              sx={{
                ...focusRing,
                typography: "s2",
                fontWeight: 500,
                color: "text.primary",
                cursor: "pointer",
                "&:hover": { textDecoration: "underline" },
              }}
            >
              {name}
            </Typography>
          </Tooltip>
          <Typography
            component="span"
            sx={{
              typography: "s3",
              color: "text.tertiary",
              fontVariantNumeric: "tabular-nums",
            }}
          >
            {`· ${count} ${count === 1 ? "issue" : "issues"}`}
          </Typography>
        </Stack>
      </TableCell>
    </TableRow>
  );
}
ProjectGroupRow.propTypes = {
  name: PropTypes.string,
  count: PropTypes.number,
  colSpan: PropTypes.number,
  onOpen: PropTypes.func,
};

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
function SkeletonRow({ withEpoch = false }) {
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
    <TableRow sx={{ height: ROW_HEIGHT.default }}>
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
      {withEpoch && (
        <TableCell>
          <Box sx={{ ...pulse, height: 14, width: 140 }} />
        </TableCell>
      )}
      <TableCell>
        <Box sx={{ ...pulse, height: 14, width: 40 }} />
      </TableCell>
      <TableCell>
        <Box sx={{ ...pulse, height: 28, width: "100%" }} />
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
              ? "No issues match your filters"
              : "No issues — everything looks good!"}
          </Typography>
          <Typography typography="s2" color="text.secondary">
            {filtered
              ? "Try adjusting your search or filter criteria."
              : "Issues Future AGI finds in your traces appear here."}
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
  const columns = useMemo(
    () => COLUMNS.filter((c) => !c.improving || improving),
    [improving],
  );
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

  /* Grouped by project by default (Kartik's review, C10). One project
     filtered in still gets its group row — the name says where you are. */
  const groups = useMemo(() => groupByProject(rows), [rows]);
  /* The project's page in Tracing. The prototype's sample projects have no
     page there, so they open the Tracing list. */
  const openProject = useCallback(
    (projectId) =>
      navigate(
        projectId && !SAMPLE_PROJECT_IDS.has(projectId)
          ? `${paths.dashboard.observe}/${projectId}/llm-tracing`
          : paths.dashboard.observe,
      ),
    [navigate],
  );

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
                <SkeletonRow key={i} withEpoch={improving} />
              ))
            ) : rows.length === 0 ? (
              <EmptyState filtered={isFiltered} colSpan={columns.length + 2} />
            ) : (
              groups.map(({ project, items }) => [
                <ProjectGroupRow
                  key={`project:${project}`}
                  name={projectName[project] || project || "No project"}
                  count={items.length}
                  colSpan={columns.length + 2}
                  onOpen={() => openProject(project)}
                />,
                ...items.map((row) => {
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
                        height: ROW_HEIGHT.default,
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

                      {/* Issue: its name only; type, source and score on
                          hover. maxWidth 0 lets a long name truncate instead
                          of pushing the table wider than the page. */}
                      <TableCell sx={{ maxWidth: 0 }}>
                        <Tooltip
                          arrow
                          placement="top-start"
                          title={[
                            row.error.name,
                            row.cluster_id,
                            shortTypeOf(row.error.type),
                            row.source === "eval"
                              ? "Found by an eval"
                              : "Found by the scanner",
                            row.users_affected != null &&
                              `${fmt(row.users_affected)} users`,
                            FIX_LAYER_LABEL[row.fix_layer] &&
                              `Fix layer: ${FIX_LAYER_LABEL[row.fix_layer]}`,
                            row.eval_score != null &&
                              `Eval score ${row.eval_score.toFixed(2)}`,
                          ]
                            .filter(Boolean)
                            .join(" · ")}
                        >
                          <Typography
                            typography="s2"
                            fontWeight="fontWeightMedium"
                            color="text.primary"
                            noWrap
                            sx={{ display: "block" }}
                          >
                            {row.error.name}
                          </Typography>
                        </Tooltip>
                      </TableCell>

                      {/* Severity */}
                      <TableCell>
                        <ErrorSeverityBadge severity={row.severity} />
                      </TableCell>

                      {/* Status */}
                      <TableCell onClick={(e) => e.stopPropagation()}>
                        <ErrorStatusChip status={row.status} />
                      </TableCell>

                      {/* Epoch: which epoch works on it, and where it stands */}
                      {improving && (
                        <TableCell sx={{ maxWidth: 200 }}>
                          <IssueStatusChip issue={row} variant="epoch" />
                        </TableCell>
                      )}

                      {/* Events: the issue's trace count */}
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

                      {/* Trend sparkline */}
                      <TableCell sx={{ p: "0 8px !important" }}>
                        <ErrorTrendSparkline
                          data={row.trends}
                          severity={row.severity}
                        />
                      </TableCell>

                      {/* Last seen */}
                      <TableCell>
                        <Typography
                          typography="s3"
                          color="text.disabled"
                          noWrap
                        >
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
                            <Iconify
                              icon="solar:arrow-right-linear"
                              width={16}
                            />
                          </IconButton>
                        </Tooltip>
                      </TableCell>
                    </TableRow>
                  );
                }),
              ])
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
