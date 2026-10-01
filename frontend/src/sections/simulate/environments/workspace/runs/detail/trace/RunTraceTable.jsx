import PropTypes from "prop-types";
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  Box,
  Stack,
  Button,
  Chip,
  FormControlLabel,
  Pagination,
  Switch,
  Typography,
} from "@mui/material";

import Iconify from "src/components/iconify";
import { FilterPanel } from "src/components/filter-panel";
import { useRunCalls } from "src/api/simulate-environments/runDetail";
import { AGENT_TYPES } from "src/sections/agents/constants";

import SectionCard from "../../../../components/SectionCard";
import EmptyState from "../../../../components/EmptyState";
import TraceTable from "./TraceTable";
import { TraceGroupByPicker, TraceColumnsPicker } from "./TracePickers";
import StatusFilterChips from "./StatusFilterChips";
import {
  CLOSED_GROUP_VIEW,
  GROUPINGS,
  VOICE_ONLY_COLUMNS,
  defaultTraceColumns,
} from "./traceTable.constants";

const STATUS_CHIP_API = {
  failing: "failed",
  errored: "error",
  inconclusive: "inconclusive",
  passing: "passed",
};
const STATUS_LABELS = {
  passed: "Passed",
  failed: "Failed",
  error: "Errored",
  inconclusive: "Not measured",
};

const filterButtonSx = {
  typography: "s2",
  fontWeight: "fontWeightBold",
  textTransform: "none",
  height: 32,
  color: "text.primary",
  borderColor: "divider",
  "&:hover": { borderColor: "text.disabled", bgcolor: "transparent" },
};

const PAGE_SIZE = 50;
// Room left under the table box for the pager row and the page's bottom gutter.
const BELOW_TABLE_PX = 88;
const PAGER_ROW_PX = 57;
const MIN_TABLE_PX = 360;

// The per-call table owns server-backed grouping, filtering, columns and paging
// for read-only execution results.
export default function RunTraceTable({
  executionId,
  onOpenCall,
  onQueryChange,
  initialFilters = {},
  activeCallId = null,
  activePage = null,
}) {
  const [groupBy, setGroupBy] = useState("goal");
  const [statusChip, setStatusChip] = useState("all");
  const [page, setPage] = useState(1);
  const [visibleColumns, setVisibleColumns] = useState(() =>
    defaultTraceColumns(),
  );
  const [filterAnchor, setFilterAnchor] = useState(null);
  // Which groups are open lives here, not in the table: a filter's loading
  // and empty states unmount the table, and its own state would go with it,
  // folding every group back up. Labels differ per axis, so each axis keeps
  // its own opened set and a change under one never touches another; Expand
  // all carries over.
  const [groupState, setGroupState] = useState({
    all: false,
    expandedByAxis: {},
  });
  const groupView = useMemo(
    () => ({
      all: groupState.all,
      expanded:
        groupState.expandedByAxis[groupBy] ?? CLOSED_GROUP_VIEW.expanded,
    }),
    [groupState, groupBy],
  );
  const setGroupView = useCallback(
    (update) =>
      setGroupState((prev) => {
        const current = {
          all: prev.all,
          expanded: prev.expandedByAxis[groupBy] ?? CLOSED_GROUP_VIEW.expanded,
        };
        const next = typeof update === "function" ? update(current) : update;
        // Collapse all closes every axis, not just the one on screen.
        if (next === CLOSED_GROUP_VIEW)
          return { all: false, expandedByAxis: {} };
        return {
          all: next.all,
          expandedByAxis: { ...prev.expandedByAxis, [groupBy]: next.expanded },
        };
      }),
    [groupBy],
  );
  const expandedForRef = useRef(null);
  const [filters, setFilters] = useState(initialFilters);

  const serverFilters = useMemo(() => {
    const next = {};
    if (filters.callExecutionId?.length)
      next.call_execution_id = filters.callExecutionId;
    if (filters.goal?.length) next.goal = filters.goal;
    if (filters.subGoal?.length) next.sub_goal = filters.subGoal;
    if (filters.status?.length) next.status = filters.status;
    if (filters.goal_outcome?.length)
      next.goal_outcome = filters.goal_outcome;
    if (statusChip !== "all") next.status = [STATUS_CHIP_API[statusChip]];
    return next;
  }, [filters, statusChip]);

  // A pager click opens the new page at its first row. Not on a drawer step:
  // there the open call's row scrolls itself into view. The table scrolls in
  // its own box, so its head and group rows can stay pinned.
  const scrollRef = useRef(null);
  const tableScrollRef = useRef(null);
  // The table box runs to the bottom of the window whatever the row count, so
  // the card never hugs a few rows. Measured, because the header above it
  // varies in height; re-measured on resize.
  const [boxHeight, setBoxHeight] = useState(null);
  useLayoutEffect(() => {
    const measure = () => {
      const top = scrollRef.current?.getBoundingClientRect().top;
      if (top == null) return;
      setBoxHeight(
        Math.max(MIN_TABLE_PX, window.innerHeight - top - BELOW_TABLE_PX),
      );
    };
    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, []);

  // Follow the drawer: when prev/next lands on a call on another page, show
  // that page. Keyed on the call too, so a manual page change doesn't stick.
  useEffect(() => {
    if (activePage) setPage(activePage);
  }, [activeCallId, activePage]);

  // Exactly what this table asks the list for. It's reported up so the call
  // drawer's prev/next read the same cache entry, in the same results order.
  const listQuery = useMemo(
    () => ({
      page,
      limit: PAGE_SIZE,
      search: "",
      filters: serverFilters,
      groupBy,
    }),
    [page, serverFilters, groupBy],
  );
  useEffect(() => {
    onQueryChange?.(listQuery);
  }, [listQuery, onQueryChange]);
  // Unmounting (another tab) takes the table's state with it.
  useEffect(() => () => onQueryChange?.(null), [onQueryChange]);

  const {
    tasks,
    columns,
    count = 0,
    groups = [],
    facets = {},
    totalPages = 1,
    agentType = null,
    runActive = false,
    isLoading,
    error,
  } = useRunCalls(executionId, listQuery);

  // A chat run has no voice metrics. Without the run's agent type, the calls
  // decide.
  const chatRun = agentType
    ? agentType === AGENT_TYPES.CHAT
    : tasks.length > 0 &&
      tasks.every((t) => t.simulationCallType === AGENT_TYPES.CHAT);
  const shownColumns = useMemo(
    () =>
      chatRun
        ? new Set([...visibleColumns].filter((k) => !VOICE_ONLY_COLUMNS.has(k)))
        : visibleColumns,
    [chatRun, visibleColumns],
  );

  // The eval columns to render come from the data-driven column descriptors.
  const evals = useMemo(
    () =>
      columns
        .filter((c) => c.group === "Evaluations")
        .map((c) => ({ id: c.key, name: c.label })),
    [columns],
  );
  const subGoalEvals = useMemo(
    () =>
      columns
        .filter((c) => c.group === "Sub-goal Results")
        .map((c) => ({ id: c.key, name: c.label })),
    [columns],
  );

  const goalOptions = useMemo(
    () => (facets.goal ?? []).map((item) => item.value),
    [facets.goal],
  );
  const subGoalOptions = useMemo(
    () => (facets.sub_goal ?? []).map((item) => item.value),
    [facets.sub_goal],
  );
  const filterFields = useMemo(
    () => [
      {
        value: "goal",
        label: "Goal",
        type: "enum",
        choices: goalOptions,
      },
      {
        value: "subGoal",
        label: "Sub goal",
        type: "enum",
        choices: subGoalOptions,
      },
      {
        value: "status",
        label: "Status",
        type: "enum",
        choices: Object.keys(STATUS_LABELS),
        choiceLabels: STATUS_LABELS,
      },
    ],
    [goalOptions, subGoalOptions],
  );
  // Calls handed over from a diagnosis issue; shown as their own chip rather than
  // a Filter-panel field, since the panel cannot express an id list.
  const affectedCalls = filters.callExecutionId?.length || 0;
  const filterCount = Object.values(filters).reduce(
    (a, v) => a + (v?.length || 0),
    0,
  );

  const statusCounts = useMemo(() => {
    const byStatus = Object.fromEntries(
      (facets.status ?? []).map((item) => [item.value, item.count]),
    );
    return {
      all: Object.values(byStatus).reduce((sum, count) => sum + count, 0),
      failing: byStatus.failed ?? 0,
      errored: byStatus.error ?? 0,
      mixed: 0,
      inconclusive: byStatus.inconclusive ?? 0,
      passing: byStatus.passed ?? 0,
    };
  }, [facets.status]);

  // The panel edits its own fields; the affected-calls scope stays until its chip
  // is dismissed.
  const { callExecutionId, ...panelFilters } = filters;
  const keepScope = (next) =>
    callExecutionId ? { ...next, callExecutionId } : next;
  const applyFilters = (result) => {
    if (!result) {
      setFilters(keepScope({}));
      return;
    }
    if (Array.isArray(result)) {
      const flat = {};
      result.forEach((r) => {
        const values = Array.isArray(r.value)
          ? r.value
          : r.value != null
            ? [r.value]
            : [];
        if (values.length)
          flat[r.field] = [...(flat[r.field] || []), ...values];
      });
      setFilters(keepScope(flat));
    } else {
      setFilters(keepScope(result));
    }
    setStatusChip("all");
  };

  // Like a "select all" box: on only while every group on screen is open,
  // however they were opened.
  const allOpen =
    groups.length > 0 &&
    groups.every((g) => groupView.all || groupView.expanded.has(g.label));

  const title = (
    <Stack direction="row" alignItems="center" spacing={1.25}>
      <TraceGroupByPicker
        value={groupBy}
        onChange={(value) => {
          setGroupBy(value);
          // The open call's group has to open again under the new axis.
          expandedForRef.current = null;
          setPage(1);
        }}
      />
      {groupBy && (
        <FormControlLabel
          control={
            <Switch
              size="small"
              checked={allOpen}
              onChange={() =>
                setGroupView((prev) =>
                  allOpen
                    ? CLOSED_GROUP_VIEW
                    : {
                        all: true,
                        expanded: new Set([
                          ...prev.expanded,
                          ...groups.map((g) => g.label),
                        ]),
                      },
                )
              }
            />
          }
          label="Expand all"
          sx={{
            ml: 0.5,
            mr: 0,
            ".MuiFormControlLabel-label": { typography: "s2" },
          }}
        />
      )}
      <Button
        size="small"
        variant="outlined"
        onClick={(e) => setFilterAnchor(e.currentTarget)}
        startIcon={
          <Iconify
            icon="mage:filter"
            width={15}
            sx={{ color: filterCount ? "primary.main" : "text.subtitle" }}
          />
        }
        endIcon={
          <Iconify
            icon="solar:alt-arrow-down-linear"
            width={12}
            sx={{ color: "text.subtitle" }}
          />
        }
        sx={filterButtonSx}
      >
        Filter
        {filterCount - affectedCalls > 0 && (
          <>
            <Box
              component="span"
              sx={{
                mx: 0.5,
                color: "text.subtitle",
                fontWeight: "fontWeightRegular",
              }}
            >
              ·
            </Box>
            <Box component="span" sx={{ color: "primary.main" }}>
              {filterCount - affectedCalls}
            </Box>
          </>
        )}
      </Button>
      {affectedCalls > 0 && (
        <Chip
          size="small"
          label={`${affectedCalls} affected call${affectedCalls === 1 ? "" : "s"}`}
          onDelete={() => {
            setFilters(({ callExecutionId: _ids, ...rest }) => rest);
            setPage(1);
          }}
          sx={{ typography: "s2", fontWeight: 600 }}
        />
      )}
    </Stack>
  );

  const action = (
    <Stack direction="row" alignItems="center" spacing={1.5}>
      <StatusFilterChips
        value={statusChip}
        counts={statusCounts}
        onChange={(value) => {
          setStatusChip(value);
          setFilters((current) => {
            if (!current.status) return current;
            const { status: _status, ...rest } = current;
            return rest;
          });
          setPage(1);
        }}
      />
      <TraceColumnsPicker
        value={visibleColumns}
        onChange={setVisibleColumns}
        hidden={chatRun ? VOICE_ONLY_COLUMNS : undefined}
      />
    </Stack>
  );
  const showPager = !isLoading && count > 0 && totalPages > 1;
  const rangeStart = count ? (page - 1) * PAGE_SIZE + 1 : 0;
  const rangeEnd = Math.min(page * PAGE_SIZE, count);

  return (
    <>
      <SectionCard title={title} action={action}>
        {/* A fixed-height box, like the Scenarios tab: the card keeps its size
            whatever the row count, and the pager below never moves. The table
            scrolls inside it, in its own box. */}
        <Box
          ref={scrollRef}
          sx={{
            // Without a pager, the box takes the pager's room too.
            height:
              boxHeight == null
                ? "calc(100dvh - 400px)"
                : boxHeight + (showPager ? 0 : PAGER_ROW_PX),
            overflowY: "auto",
          }}
        >
          {isLoading ? (
            <EmptyState icon="solar:hourglass-linear" title="Loading calls…" />
          ) : error ? (
            <EmptyState
              icon="solar:danger-triangle-linear"
              title="Couldn't load calls"
              body="Something went wrong loading this run's calls. Try again."
            />
          ) : tasks.length === 0 ? (
            <EmptyState
              icon="solar:filter-linear"
              title="No calls match that filter"
            />
          ) : (
            <TraceTable
              key={groupBy}
              columns={shownColumns}
              groups={groups}
              rows={groupBy ? null : tasks}
              evals={evals}
              subGoalEvals={subGoalEvals}
              groupView={groupView}
              onGroupViewChange={setGroupView}
              expandedForRef={expandedForRef}
              firstColumnLabel={
                groupBy
                  ? GROUPINGS.find((g) => g.id === groupBy)?.label
                  : undefined
              }
              onOpen={onOpenCall}
              activeCallId={activeCallId}
              scrollRef={tableScrollRef}
              runActive={runActive}
            />
          )}
        </Box>
        {showPager && (
          <Stack
            direction="row"
            alignItems="center"
            justifyContent="space-between"
            flexWrap="wrap"
            gap={1}
            sx={{
              px: 2,
              py: 1.5,
              borderTop: "1px solid",
              borderColor: "divider",
            }}
          >
            <Typography
              sx={{
                typography: "s3",
                color: "text.subtitle",
                fontVariantNumeric: "tabular-nums",
              }}
            >
              Showing {rangeStart.toLocaleString()}–{rangeEnd.toLocaleString()}{" "}
              of {count.toLocaleString()}
            </Typography>
            <Pagination
              size="small"
              shape="rounded"
              count={totalPages}
              page={page}
              onChange={(_, value) => {
                setPage(value);
                tableScrollRef.current?.scrollTo?.({ top: 0 });
              }}
              siblingCount={1}
              boundaryCount={1}
            />
          </Stack>
        )}
      </SectionCard>

      <FilterPanel
        anchorEl={filterAnchor}
        open={!!filterAnchor}
        onClose={() => setFilterAnchor(null)}
        filterFields={filterFields}
        currentFilters={panelFilters}
        onApply={(result) => {
          applyFilters(result);
          setPage(1);
        }}
        // The AI box isn't wired for run calls; Basic/Query cover the filters.
        showAiFilter={false}
        placement="bottom-start"
      />
    </>
  );
}
RunTraceTable.propTypes = {
  executionId: PropTypes.string,
  onOpenCall: PropTypes.func,
  onQueryChange: PropTypes.func,
  initialFilters: PropTypes.object,
  activeCallId: PropTypes.string,
  activePage: PropTypes.number,
};
