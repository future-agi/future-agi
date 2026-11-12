import PropTypes from "prop-types";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  useNavigate,
  useOutletContext,
  useSearchParams,
} from "react-router-dom";
import {
  Box,
  Button,
  FormControlLabel,
  Pagination,
  Stack,
  Switch,
  Typography,
} from "@mui/material";

import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { FilterPanel } from "src/components/filter-panel";
import { paths } from "src/routes/paths";
import {
  MAX_COMPARE_RUNS,
  useCompareRuns,
} from "src/api/simulate-environments/compareRuns";
import { useEnvironmentRuns } from "src/api/simulate-environments/runs";

import SectionCard from "../../../components/SectionCard";
import EmptyState from "../../../components/EmptyState";
import { COMPARE_COPY } from "../../workspace.constants";
import {
  TraceColumnsPicker,
  TraceGroupByPicker,
} from "../detail/trace/TracePickers";
import {
  VOICE_ONLY_COLUMNS,
  compactHiddenSx,
  isChatRun,
  toolbarButtonSx,
} from "../detail/trace/traceTable.constants";
import {
  buildTraceFilterFields,
  countFilterValues,
  panelResultToFilters,
  statusChipOf,
  toServerFilters,
  withStatusChip,
} from "../detail/trace/traceFilters";
import StatusFilterChips from "../detail/trace/StatusFilterChips";
import CallDrawer from "../detail/CallDrawer";
import CompareRunPills from "./CompareRunPills";
import CompareScoreChart from "./CompareScoreChart";
import CompareTable from "./CompareTable";
import CompareRowDrawer from "./CompareRowDrawer";
import {
  COMPARE_COLUMNS,
  MIN_COMPARE_RUNS,
  ROW_LEAD_COLUMNS,
  defaultCompareColumns,
  parseRunIds,
} from "./compare.constants";

// Enough runs for the Add run menu to offer every recent finished run.
const ADD_RUN_PAGE_SIZE = 50;
// The page box's padding, top and bottom.
const PAGE_PADDING_PX = 32;

/**
 * Runs compared scenario by scenario, at
 * `environments/:envId/compare?runs=a,b,c` (the first is the baseline). The
 * toolbar, columns and cells are the run detail calls table's.
 */
export function RunCompare({ env, envState }) {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const runIds = useMemo(
    () => parseRunIds(params.get("runs"), MAX_COMPARE_RUNS),
    [params],
  );
  const [groupBy, setGroupBy] = useState("goal");
  const [filters, setFilters] = useState({});
  const [filterAnchor, setFilterAnchor] = useState(null);
  const [page, setPage] = useState(1);
  const [visibleColumns, setVisibleColumns] = useState(defaultCompareColumns);
  const [hiddenEvals, setHiddenEvals] = useState(() => new Set());
  const [showDiff, setShowDiff] = useState(false);
  const [chartEvalId, setChartEvalId] = useState(null);
  const [openRowId, setOpenRowId] = useState(null);
  const [openCall, setOpenCall] = useState(null);
  // The card is capped at the page's visible height, so the table scrolls in
  // its own box: its head stays pinned and its scrollbar and pager in reach.
  const [scroller, setScroller] = useState(null);
  const [cardMaxHeight, setCardMaxHeight] = useState(null);
  useEffect(() => {
    if (!scroller) return undefined;
    const fit = () => setCardMaxHeight(scroller.clientHeight - PAGE_PADDING_PX);
    fit();
    const ro =
      typeof ResizeObserver !== "undefined" ? new ResizeObserver(fit) : null;
    ro?.observe(scroller);
    return () => ro?.disconnect();
  }, [scroller]);

  const serverFilters = useMemo(() => toServerFilters(filters), [filters]);
  const { data, error, isPending, isError, isFetching, isPlaceholderData } =
    useCompareRuns(env.id, runIds, {
      page,
      filters: serverFilters,
      groupBy,
    });
  // A failed refetch keeps the comparison it already has on screen.
  const failed = isError && !data;

  const { runs: recentRuns = [] } = useEnvironmentRuns(env, envState, {
    page: 0,
    pageSize: ADD_RUN_PAGE_SIZE,
  });
  const candidates = recentRuns.filter(
    (r) =>
      r.executionId &&
      r.runState === "finished" &&
      !runIds.includes(r.executionId),
  );

  const setRuns = (ids) => {
    const next = new URLSearchParams(params);
    next.set("runs", ids.join(","));
    setParams(next, { replace: true });
    setPage(1);
  };
  const back = () =>
    navigate(
      paths.dashboard.simulate.environments.workspaceTab(env.id, "runs"),
    );

  // A refused comparison has no runs: the URL's, as the runs list knows them,
  // so the refused one can still be named and removed. Kept apart so a runs
  // list poll leaves a loaded comparison's runs, and its table, alone.
  const refusedRuns = useMemo(() => {
    if (!failed) return [];
    return runIds.map((id, i) => {
      const known = recentRuns.find((r) => r.executionId === id);
      return {
        id,
        colorIndex: i,
        ordinal: known?.ordinal ?? null,
        isBaseline: i === 0,
        agentLabel: known?.agentVersion ?? null,
        environmentLabel: null,
        passRate: known?.passRate == null ? null : Math.round(known.passRate),
      };
    });
  }, [failed, runIds, recentRuns]);
  // Each run's colour is its place in the comparison.
  const loadedRuns = useMemo(
    () => data?.runs?.map((run, i) => ({ ...run, colorIndex: i })) ?? null,
    [data],
  );
  const runs = loadedRuns ?? refusedRuns;
  // The run the server refused, as a refused comparison names it.
  const refused = failed
    ? runs.find((r) => r.id === error?.execution_id) ?? null
    : null;
  const columns = useMemo(() => data?.columns ?? [], [data]);
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
  const shownEvals = useMemo(
    () => evals.filter((e) => !hiddenEvals.has(e.id)),
    [evals, hiddenEvals],
  );
  // Every catalog eval and sub-goal has bands; only one some run scored is
  // worth charting.
  const chartEvals = [...evals, ...subGoalEvals].filter((e) =>
    runs.some((run) => run.histograms?.[e.id]?.some((n) => n > 0)),
  );
  const chartEval =
    chartEvals.find((e) => e.id === chartEvalId)?.id ?? chartEvals[0]?.id;

  // A chat comparison has no voice metrics; Scenario is the row's own column.
  const chatOnly =
    runs.length > 0 &&
    runs.every((run) =>
      isChatRun(
        run.agentType,
        (data?.rows ?? []).flatMap((row) => row.calls[run.id] ?? []),
      ),
    );
  const hiddenColumns = useMemo(
    () =>
      new Set([...ROW_LEAD_COLUMNS, ...(chatOnly ? VOICE_ONLY_COLUMNS : [])]),
    [chatOnly],
  );
  const shownColumns = useMemo(
    () => new Set([...visibleColumns].filter((k) => !hiddenColumns.has(k))),
    [visibleColumns, hiddenColumns],
  );

  // Each facet, not the object: a missing one defaults to a new object per render.
  const {
    goal: goalFacet,
    sub_goal: subGoalFacet,
    persona: personaFacet,
    age: ageFacet,
    attack: attackFacet,
    task: taskFacet,
  } = data?.facets ?? {};
  const levelLabels = data?.levelLabels;
  const filterFields = useMemo(
    () =>
      buildTraceFilterFields(
        {
          goal: goalFacet,
          sub_goal: subGoalFacet,
          persona: personaFacet,
          age: ageFacet,
          attack: attackFacet,
          task: taskFacet,
        },
        levelLabels,
      ),
    [
      goalFacet,
      subGoalFacet,
      personaFacet,
      ageFacet,
      attackFacet,
      taskFacet,
      levelLabels,
    ],
  );
  const filterCount = countFilterValues(filters);
  // Counted under every filter but status, over every run's calls.
  const statusCounts = useMemo(() => {
    const counts = { all: 0 };
    runs.forEach((run) =>
      Object.entries(run.statusCounts ?? {}).forEach(([key, n]) => {
        counts[key] = (counts[key] ?? 0) + n;
        counts.all += n;
      }),
    );
    return counts;
  }, [runs]);

  // A refetch can leave fewer pages than the one shown: go to the last.
  useEffect(() => {
    if (data && !isPlaceholderData && page > data.totalPages) {
      setPage(data.totalPages);
    }
  }, [data, isPlaceholderData, page, setPage]);

  const openRowFor = useCallback((row) => setOpenRowId(row.rowId), []);
  const rows = data?.rows ?? [];
  const openRow = rows.find((r) => r.rowId === openRowId) ?? null;
  const totalPages = data?.totalPages ?? 1;

  if (runIds.length < MIN_COMPARE_RUNS) {
    return (
      <Box sx={{ p: 2 }}>
        <EmptyState
          icon="solar:chart-2-linear"
          title={COMPARE_COPY.tooFew}
          action={
            <Button variant="outlined" size="small" onClick={back}>
              {COMPARE_COPY.back}
            </Button>
          }
        />
      </Box>
    );
  }

  const toolbar = (
    <Stack direction="row" alignItems="center" flexWrap="wrap" gap={1.25}>
      <TraceGroupByPicker
        value={groupBy}
        onChange={(value) => {
          setGroupBy(value);
          setPage(1);
        }}
      />
      <CustomTooltip
        show
        size="small"
        arrow
        title={filterCount ? `Filter · ${filterCount}` : "Filter"}
      >
        <Button
          size="small"
          variant="outlined"
          onClick={(e) => setFilterAnchor(e.currentTarget)}
          startIcon={
            <Iconify
              icon="mage:filter"
              width={15}
              sx={{ color: filterCount ? "text.primary" : "text.subtitle" }}
            />
          }
          endIcon={
            <Iconify
              icon="solar:alt-arrow-down-linear"
              width={12}
              sx={{ color: "text.subtitle" }}
            />
          }
          sx={toolbarButtonSx}
        >
          <Box component="span" sx={compactHiddenSx}>
            Filter
          </Box>
          {filterCount > 0 && (
            <Box component="span" sx={{ ml: 0.5 }}>
              {`· ${filterCount}`}
            </Box>
          )}
        </Button>
      </CustomTooltip>
      <TraceColumnsPicker
        value={visibleColumns}
        onChange={setVisibleColumns}
        hidden={hiddenColumns}
        evals={evals}
        hiddenEvals={hiddenEvals}
        onHiddenEvalsChange={setHiddenEvals}
        columns={COMPARE_COLUMNS}
        defaults={defaultCompareColumns}
      />
    </Stack>
  );

  let body;
  if (isPending) {
    body = (
      <EmptyState icon="solar:hourglass-linear" title={COMPARE_COPY.loading} />
    );
  } else if (failed) {
    const left = runIds.filter((id) => id !== refused?.id);
    body = refused ? (
      <EmptyState
        icon="solar:danger-triangle-linear"
        title={COMPARE_COPY.refused(refused.ordinal)}
        body={error?.message ?? COMPARE_COPY.errorBody}
        action={
          left.length >= MIN_COMPARE_RUNS ? (
            <Button
              variant="outlined"
              size="small"
              onClick={() => setRuns(left)}
            >
              {COMPARE_COPY.removeRefused(refused.ordinal)}
            </Button>
          ) : null
        }
      />
    ) : (
      <EmptyState
        icon="solar:danger-triangle-linear"
        title={COMPARE_COPY.error}
        body={COMPARE_COPY.errorBody}
      />
    );
  } else if (!rows.length) {
    body = <EmptyState icon="solar:filter-linear" title={COMPARE_COPY.empty} />;
  } else {
    body = (
      <Box
        sx={{ minHeight: 0, overflow: "auto", opacity: isFetching ? 0.6 : 1 }}
      >
        <CompareTable
          runs={runs}
          rows={rows}
          // A response still loading for a new grouping has no groups yet.
          groups={groupBy && data.groups.length ? data.groups : null}
          columns={shownColumns}
          evals={shownEvals}
          subGoalEvals={subGoalEvals}
          showDiff={showDiff}
          onOpenRow={openRowFor}
        />
      </Box>
    );
  }

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        minHeight: 0,
      }}
    >
      <Stack
        spacing={1.25}
        sx={{
          px: 3,
          py: 2,
          borderBottom: "1px solid",
          borderColor: "divider",
          flexShrink: 0,
        }}
      >
        <Stack direction="row" alignItems="center" spacing={2}>
          <Button
            size="small"
            variant="outlined"
            onClick={back}
            startIcon={
              <Iconify icon="solar:alt-arrow-left-linear" width={15} />
            }
          >
            {COMPARE_COPY.back}
          </Button>
          <Box minWidth={0} flex={1}>
            <Typography noWrap sx={{ typography: "m1", fontWeight: 600 }}>
              {env.name}
            </Typography>
            <Typography
              noWrap
              sx={{ typography: "s2", color: "text.subtitle" }}
            >
              {data
                ? COMPARE_COPY.subtitle(
                    runs.length,
                    data.count,
                    data.changedCount,
                  )
                : COMPARE_COPY.heading}
            </Typography>
          </Box>
          {toolbar}
        </Stack>
        <CompareRunPills
          runs={runs}
          candidates={candidates}
          // From the URL: a loading comparison still shows the previous runs.
          canRemove={runIds.length > MIN_COMPARE_RUNS}
          full={runIds.length >= MAX_COMPARE_RUNS}
          onRemove={(id) => setRuns(runIds.filter((r) => r !== id))}
          onAdd={(id) => setRuns([...runIds, id])}
        />
      </Stack>

      <Box ref={setScroller} sx={{ flex: 1, minHeight: 0, overflow: "auto" }}>
        <Stack spacing={2} sx={{ p: 2 }}>
          {data && runs.length > 0 && (
            <CompareScoreChart
              runs={runs}
              evals={chartEvals}
              evalId={chartEval}
              onEvalChange={setChartEvalId}
            />
          )}
          <SectionCard
            sx={{
              display: "flex",
              flexDirection: "column",
              ...(cardMaxHeight && { maxHeight: cardMaxHeight }),
            }}
            title={COMPARE_COPY.tableTitle}
            subtitle={
              data
                ? COMPARE_COPY.tableSubtitle(
                    data.changedCount,
                    data.count,
                    rows.length,
                  )
                : null
            }
            action={
              <Stack direction="row" alignItems="center" spacing={1.5}>
                <StatusFilterChips
                  value={statusChipOf(filters)}
                  counts={statusCounts}
                  onChange={(value) => {
                    setFilters((current) => withStatusChip(current, value));
                    setPage(1);
                  }}
                />
                <Stack direction="row" alignItems="center" spacing={0.5}>
                  <FormControlLabel
                    control={
                      <Switch
                        size="small"
                        checked={showDiff}
                        onChange={(e) => setShowDiff(e.target.checked)}
                      />
                    }
                    label={COMPARE_COPY.showDiff}
                    sx={{
                      mr: 0,
                      ".MuiFormControlLabel-label": { typography: "s2" },
                    }}
                  />
                  <CustomTooltip
                    show
                    arrow
                    size="small"
                    title={COMPARE_COPY.showDiffHint}
                  >
                    <Box sx={{ display: "flex", color: "text.subtitle" }}>
                      <Iconify icon="solar:info-circle-linear" width={14} />
                    </Box>
                  </CustomTooltip>
                </Stack>
              </Stack>
            }
          >
            {body}
            {!isPending && !failed && totalPages > 1 && (
              <Stack
                direction="row"
                justifyContent="flex-end"
                sx={{
                  px: 2,
                  py: 1.5,
                  borderTop: "1px solid",
                  borderColor: "divider",
                }}
              >
                <Pagination
                  size="small"
                  shape="rounded"
                  count={totalPages}
                  page={page}
                  onChange={(_, value) => setPage(value)}
                />
              </Stack>
            )}
          </SectionCard>
        </Stack>
      </Box>

      <FilterPanel
        anchorEl={filterAnchor}
        open={!!filterAnchor}
        onClose={() => setFilterAnchor(null)}
        filterFields={filterFields}
        currentFilters={filters}
        onApply={(result) => {
          setFilters(panelResultToFilters(result));
          setPage(1);
        }}
        showAiFilter={false}
        placement="bottom-start"
      />
      <CompareRowDrawer
        row={openRow}
        runs={runs}
        onClose={() => setOpenRowId(null)}
        onOpenCall={(task, run) => {
          setOpenRowId(null);
          setOpenCall({ task, run });
        }}
      />
      <CallDrawer
        task={openCall?.task ?? null}
        executionId={openCall?.run.id ?? null}
        agentType={openCall?.run.agentType}
        onClose={() => setOpenCall(null)}
      />
    </Box>
  );
}
RunCompare.propTypes = {
  env: PropTypes.shape({ id: PropTypes.string, name: PropTypes.string })
    .isRequired,
  envState: PropTypes.object,
};

// The route element: the environment comes from the workspace's Outlet.
export default function WorkspaceRunCompare() {
  const { env, envState } = useOutletContext() || {};
  if (!env) return null;
  return <RunCompare env={env} envState={envState} />;
}
