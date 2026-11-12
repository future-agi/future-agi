import React, {
  useEffect,
  useState,
  forwardRef,
  useCallback,
  useImperativeHandle,
  useMemo,
  useRef,
} from "react";
import PropTypes from "prop-types";
import { Box, IconButton, useMediaQuery } from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { alpha } from "@mui/material/styles";
import { AgGridReact } from "ag-grid-react";
import { useAgThemeWith } from "src/hooks/use-ag-theme";
import { AG_THEME_OVERRIDES } from "src/theme/ag-theme";
import { isGhostBaseline, LeaderboardContext } from "./cellContext";
import EvalCell from "./EvalCell";
import EvalHeader from "./EvalHeader";
import RunStatusCell from "./RunStatusCell";
import {
  CreatedByCell,
  CreatedCell,
  DatasetCell,
  DescriptionCell,
  ErrorsCell,
  ExamplesCell,
  HumanCell,
  MetaCell,
  TargetCell,
  TokensCell,
  TrialsCell,
  WinRateCell,
} from "./LeaderboardCells";
import {
  COLUMN_WIDTH,
  GROUP_BY_COLUMN,
  META_PREFIX,
  NAME_COLUMN,
  ROW_HEIGHT,
  columnOfGroup,
  nameWidthFor,
  evalSortingOrder,
  nullsLastComparator,
  widthOfColumn,
} from "./leaderboardColumns";
import useLeaderboardRoom from "./useLeaderboardRoom";
// HO results R7 (the interface contract): run metrics as leaderboard columns.
import {
  RUN_METRICS_GROUP,
  runMetricColDef,
} from "src/sections/common/DerivedMetrics/leaderboardRunMetrics";

// R4 (lane Rb): the default set is sparser (run + 4 numbers), so the
// frozen name column may grow further before Updated takes the rest.
const NAME_MAX_WIDTH = 640;
import {
  CostPerTaskCell,
  LatencyP99SummaryCell,
  SuccessRateCell,
  TimePerTaskCell,
  TotalCostCell,
} from "./LeaderboardSummaryCells";
import SummaryHeader from "./SummaryHeader";
import NameCellWithActions from "./RowHoverActions";
import RunRowMenuHost from "./RunRowMenuHost";
import { PassKCell, UpdatedCell, WhereRanCell } from "./R4Cells";
import { successFrom, updatedOf } from "./leaderboardVersions";
import { SELECTION_GUTTER_VAR } from "./SelectionBar";
import { costPerTask } from "./leaderboardHeadline";
import { lowSampleNote } from "./metricStripModel";
import { getRunStatus } from "src/sections/common/Runs/runStatus";
import { LOW_SAMPLE_N } from "src/sections/common/Metrics/metricFormat";
import {
  ContextMenu,
  copyIdItem,
  headerMenuItems,
  isMenuKey,
  openColumnsMenu,
  pinBaselineItem,
  toAgMenuItems,
  useAgColumnShaping,
  useContextMenu,
  useHoverSelection,
} from "src/sections/common/ux/oneview";
import { fmtCount } from "src/sections/common/format/fmt";
import { useRunConfigs } from "src/api/develop/r2";
import { useLocation } from "react-router-dom";
import useQuickTests, {
  QUICK_TESTS_PARAM,
} from "src/sections/experiment-detail/iterate/useQuickTests";
import {
  iterationGroupOf,
  joinLineage,
  liftIterationsOf,
  nestIterations,
  nestSorted,
} from "src/sections/experiment-detail/iterate/lineageModel";
import { withKeptCounts } from "./attemptsModel";
import {
  aggValueOf,
  compareSelection,
  compareUrl,
  costOf,
  errorsOf,
  experimentUrl,
  experimentRunsOf,
  groupOf,
  latencyP50,
  latencyP99,
  rankPool,
  tokensOf,
} from "./leaderboardModel";
import {
  sideBySideDisabledReason,
  sideBySideHref,
  sideBySideLabel,
} from "./sideBySide";
import {
  canRerunHere,
  outputModalityOf,
} from "src/sections/common/Runs/runKind";
import { suppressGridTyping } from "src/sections/common/ux/gridKeys";
import { openRerunWithChanges } from "src/sections/common/RunSources/rerunBus";
import { canRunOf, deleteTip, editTip, runTip } from "./leaderboardAccess";

// The experiment grid's theme (AG_THEME_OVERRIDES) with the DataTable's
// 13 px headers; rows are two lines (chip + config), like DataTable rows.
// One view: 10 px cell padding (was 15) so the default columns fit 1280.
const THEME_PARAMS = {
  ...AG_THEME_OVERRIDES.noHeaderBorder,
  headerFontSize: "13px",
  fontSize: "13px",
  cellHorizontalPadding: 10,
};
const HEADER_HEIGHT = 40;
// Custom headers carry G's ⋮ (HeaderMenuButton); AG's default header shows
// its own menu button, which opens the same items (getMainMenuItems).
// R4 (D25): printable keys and Enter are shortcuts, never "start typing".
const DEFAULT_COL_DEF = {
  sortable: true,
  suppressHeaderMenuButton: false,
  suppressKeyboardEvent: suppressGridTyping,
  // the interface contract C2: every column keeps '—' last in both directions.
  comparator: nullsLastComparator,
};

/**
 * the interface contract C5 (AG-09): a headline cell (Success, Cost per task, Time per task)
 * of a finished run that rests on fewer than LOW_SAMPLE_N scored rows
 * carries `n=20` with `Low sample · n=20` on hover, like the eval cells.
 */
function withLowSample(Cell) {
  const Wrapped = (params) => {
    const run = params?.node?.group ? null : params?.data;
    const status = run ? getRunStatus(run) : null;
    const note =
      run && !status?.active && !status?.waiting ? lowSampleNote(run) : null;
    if (!note) return <Cell {...params} />;
    return (
      <Box
        data-testid="lb-low-sample"
        sx={{ position: "relative", width: "100%", height: "100%" }}
      >
        <Cell {...params} />
        {/* A corner tag, so the number keeps its full width. */}
        <CustomTooltip
          show
          size="small"
          placement="top"
          arrow
          title={`${note}: fewer than ${LOW_SAMPLE_N} rows, so treat this number as rough.`}
        >
          <Box
            component="span"
            aria-label={note}
            sx={{
              position: "absolute",
              top: 2,
              left: 0,
              fontSize: "10px",
              lineHeight: "14px",
              color: "warning.dark",
              bgcolor: (t) => alpha(t.palette.warning.main, 0.12),
              borderRadius: "2px",
              px: 0.5,
            }}
          >
            {note.replace(/^Low sample · /, "")}
          </Box>
        </CustomTooltip>
      </Box>
    );
  };
  Wrapped.displayName = `WithLowSample(${Cell.displayName || Cell.name || "Cell"})`;
  return Wrapped;
}

// the interface contract C2: the value a headline cell SHOWS is the value it sorts by: a
// run with no reported cost shows '—' (never $0), so it sorts as blank.
const shownCostPerTask = (run) =>
  Number(run?.metrics?.cost_usd) > 0 ? costPerTask(run) : null;

/**
 * the interface contract C2: a group row's headline value, as its cell shows it: the best
 * FINISHED run's (rankPool), blank while nothing finished ('Best pending'),
 * so a pending group sorts with the blanks.
 */
function shownGroupBest(valueOf, pick) {
  return (params) => {
    const leaves = (params.rowNode?.allLeafChildren ?? [])
      .map((n) => n.data)
      .filter(Boolean);
    const values = rankPool(leaves)
      .finished.map((r) => valueOf(r, params))
      .filter((v) => typeof v === "number" && Number.isFinite(v));
    if (!values.length) return null;
    return pick === "max" ? Math.max(...values) : Math.min(...values);
  };
}

// '—' (no value) sorts last in both directions (AG flips the result when
// descending, so the null side flips with it).
const nullsLast = nullsLastComparator;

const STATIC_COLUMNS = {
  // R4 (lane Rb, MUST 7): Updated is a default; pass^k and Where it ran
  // are one click away in Columns.
  updated: {
    width: COLUMN_WIDTH.updated,
    minWidth: 112,
    // The last default column takes what the frozen name leaves, so a wide
    // window never ends in a blank strip.
    flex: 1,
    cellRenderer: UpdatedCell,
    valueGetter: (p) => updatedOf(p.data),
  },
  pass_k: {
    width: 120,
    minWidth: 110,
    cellRenderer: PassKCell,
    headerComponent: SummaryHeader,
    headerComponentParams: {
      tip: "pass^k: share of tasks where every try passed (k = tries per task). A run that set how each row's tries count shows that, with its 95% interval.",
    },
    valueGetter: (p) =>
      p.context?.numbers?.byRun?.[p.data?.run_id]?.passK?.rate ?? null,
    comparator: nullsLast,
  },
  where_ran: {
    width: 200,
    cellRenderer: WhereRanCell,
  },
  target: {
    width: COLUMN_WIDTH.target,
    minWidth: 92,
    cellRenderer: TargetCell,
    valueGetter: (p) => p.data?.target_type,
  },
  dataset: {
    width: 230,
    cellRenderer: DatasetCell,
    valueGetter: (p) => p.data?.dataset_version?.number,
    aggFunc: "max",
  },
  // A2-08: fixed 132 px (the chip; progress stacks under it).
  status: {
    width: COLUMN_WIDTH.status,
    minWidth: 120,
    cellRenderer: RunStatusCell,
    // the capability contract: sort, group and copy by the run's real outcome.
    valueGetter: (p) => (p.data ? getRunStatus(p.data).key : undefined),
  },
  // R3 headline columns: min widths fit value + delta (`74 s +64 s`,
  // `$0.21 +1,809%`); the Experiment column shrinks first.
  success_rate: {
    width: COLUMN_WIDTH.success_rate,
    minWidth: 124,
    cellRenderer: withLowSample(SuccessRateCell),
    headerComponent: SummaryHeader,
    headerComponentParams: {
      termKey: "successRate",
      tip: "Passed tries ÷ tries that ran. Errored tries count as fails.",
    },
    valueGetter: (p) =>
      p.data ? successFrom(p.context?.numbers, p.data)?.rate ?? null : null,
    aggFunc: shownGroupBest(
      (r, p) => successFrom(p.context?.numbers, r)?.rate ?? null,
      "max",
    ),
    comparator: nullsLast,
  },
  cost_per_task: {
    width: COLUMN_WIDTH.cost_per_task,
    minWidth: 112,
    cellRenderer: withLowSample(CostPerTaskCell),
    headerComponent: SummaryHeader,
    headerComponentParams: {
      termKey: "costPerTask",
      tip: "Total cost ÷ tasks done. Lower is better.",
    },
    valueGetter: (p) => (p.data ? shownCostPerTask(p.data) : null),
    aggFunc: shownGroupBest(shownCostPerTask, "min"),
    comparator: nullsLast,
  },
  latency_p50: {
    width: COLUMN_WIDTH.latency_p50,
    minWidth: 92,
    cellRenderer: withLowSample(TimePerTaskCell),
    headerComponent: SummaryHeader,
    headerComponentParams: {
      termKey: "timePerTask",
      tip: "Median (p50) wall time of one task. Lower is better.",
    },
    valueGetter: (p) => (p.data ? latencyP50(p.data) : null),
    aggFunc: shownGroupBest(latencyP50, "min"),
    comparator: nullsLast,
  },
  latency_p99: {
    width: 140,
    minWidth: 128,
    cellRenderer: LatencyP99SummaryCell,
    headerComponent: SummaryHeader,
    headerComponentParams: { tip: "Slowest 1% of tasks. Lower is better." },
    valueGetter: (p) => (p.data ? latencyP99(p.data) : null),
    aggFunc: "min",
    comparator: nullsLast,
  },
  cost: {
    width: 128,
    minWidth: 120,
    cellRenderer: TotalCostCell,
    headerComponent: SummaryHeader,
    headerComponentParams: {
      termKey: "totalCost",
      tip: "Everything the run spent. Group rows add up their runs.",
    },
    valueGetter: (p) => (p.data ? costOf(p.data) : null),
    aggFunc: "sum",
    comparator: nullsLast,
  },
  tokens: {
    width: 110,
    cellRenderer: TokensCell,
    valueGetter: (p) => (p.data ? tokensOf(p.data) : null),
    aggFunc: "sum",
    comparator: nullsLast,
  },
  errors: {
    width: 100,
    cellRenderer: ErrorsCell,
    valueGetter: (p) => (p.data ? errorsOf(p.data) : null),
    aggFunc: "sum",
    comparator: nullsLast,
  },
  trials: {
    width: 140,
    cellRenderer: TrialsCell,
    valueGetter: (p) => p.data?.trials,
    aggFunc: "max",
  },
  examples: {
    width: 120,
    cellRenderer: ExamplesCell,
    valueGetter: (p) => p.data?.examples,
    aggFunc: "max",
  },
  win_rate: {
    width: 150,
    cellRenderer: WinRateCell,
    valueGetter: (p) => p.data?.metrics?.preference?.win_rate ?? null,
    aggFunc: "max",
    comparator: nullsLast,
  },
  created_by: {
    width: 170,
    cellRenderer: CreatedByCell,
    valueGetter: (p) => p.data?.created_by?.name,
  },
  created: {
    width: 130,
    cellRenderer: CreatedCell,
    valueGetter: (p) => (p.data ? Date.parse(p.data.created_at) : null),
    aggFunc: "max",
    sort: "desc",
  },
  description: {
    width: 240,
    cellRenderer: DescriptionCell,
    valueGetter: (p) => p.data?.experiment_description,
  },
};

const isClickOnControl = (event) =>
  !!event?.target?.closest?.(
    "a,button,input,[role=button],[data-row-link],.ag-selection-checkbox,.ag-group-contracted,.ag-group-expanded,.ag-checkbox-input-wrapper,.MuiPopover-root,.MuiPopper-root",
  );

const AUTO_COLUMN = "ag-Grid-AutoColumn";
// Cells use G's ContextMenu (onCellContextMenu); AG's own cell menu stays
// empty. (`suppressContextMenu` would also kill the header right-click.)
const NO_AG_CONTEXT_MENU = () => [];
const SELECTION_WIDTH = 40;
// the interface contract §9 (M6): edit and run doors read the tab's access gates and tips.

/**
 * The row's right-click menu (A2-12): the hover actions (Open · Side by
 * side · Pin as baseline) + Copy run id · Rerun · Delete; group rows get
 * the experiment's. Items for G's ContextMenu.
 */
function rowMenuItems(node, data, ctx) {
  if (!node || isGhostBaseline(node, data, ctx)) return [];
  if (node.group) {
    const runs = (node.allLeafChildren ?? [])
      .map((n) => n.data)
      .filter(Boolean);
    const first = runs[0];
    if (!first || ctx.group !== "experiment") return [];
    const compare = compareSelection(runs, ctx.baselineId, {
      pinnedId: ctx.pinnedRunId,
      candId: ctx.headlineCandId,
    });
    return [
      {
        id: "open",
        label: "Open experiment",
        icon: "mdi:open-in-app",
        shortcut: "open",
        href: experimentUrl(ctx.datasetId, first.experiment_id, {
          runs: experimentRunsOf(ctx.allRuns, first.experiment_id),
        }),
      },
      {
        id: "compare",
        label: `Compare ${runs.length} runs`,
        icon: "mdi:compare-horizontal",
        disabled: !!compare.error,
        reason: compare.error ?? undefined,
        href: compare.error
          ? undefined
          : compareUrl(ctx.datasetId, { ...compare, from: ctx.fromUrl }),
      },
      { divider: true },
      copyIdItem(first.experiment_id, "experiment"),
      // dataset integration FE-0: only the runs Future AGI can start again itself.
      ...(runs.some(canRerunHere)
        ? [
            {
              id: "rerun",
              label: "Rerun all runs",
              icon: "mdi:refresh",
              disabled: !canRunOf(ctx),
              reason: canRunOf(ctx) ? undefined : runTip(ctx),
              run: () =>
                ctx.requestRerun({
                  mode: "all",
                  runs: runs.filter(canRerunHere),
                }),
            },
          ]
        : []),
      {
        id: "delete",
        label: "Delete experiment",
        icon: "mdi:trash-can-outline",
        danger: true,
        disabled: !ctx.canDelete,
        reason: ctx.canDelete ? undefined : deleteTip(ctx),
        run: () =>
          ctx.requestDelete({
            ids: [first.experiment_id],
            runCount: runs.length,
            name: first.experiment_name,
          }),
      },
    ];
  }
  if (!data) return [];
  const side = sideBySideHref(data, ctx);
  const isPinned = data.run_id === ctx.pinnedRunId;
  const pin = pinBaselineItem(
    { label: data.label, isBaseline: isPinned },
    ctx.canEdit ? () => ctx.pinBaseline(data.run_id) : null,
  );
  const external = data.target_type === "external";
  const runCount = ctx.runsPerExperiment?.[data.experiment_id] ?? 1;
  return [
    {
      id: "open",
      label: "Open",
      icon: "mdi:open-in-app",
      shortcut: "open",
      href: experimentUrl(ctx.datasetId, data.experiment_id, {
        runId: data.run_id,
      }),
    },
    {
      id: "side",
      label: sideBySideLabel(data),
      icon: "mdi:view-split-vertical",
      href: side ?? undefined,
      disabled: !side,
      reason: sideBySideDisabledReason(data, ctx) ?? undefined,
    },
    {
      ...pin,
      label: isPinned
        ? `${data.label} is the pinned baseline`
        : `Make ${data.label} the baseline`,
      reason: ctx.canEdit ? undefined : editTip(ctx),
    },
    { divider: true },
    copyIdItem(data.run_id, "run"),
    // dataset integration FE-0: no Rerun for a run whose only path was a generated
    // runner or a live connection (code, harness, voice, upload).
    ...(canRerunHere(data)
      ? [
          {
            id: "rerun",
            label: "Rerun",
            icon: "mdi:refresh",
            disabled: !canRunOf(ctx) || external,
            reason: !canRunOf(ctx)
              ? runTip(ctx)
              : external
                ? "External runs rerun from your CI"
                : undefined,
            run: () => ctx.requestRerun({ mode: "all", runs: [data] }),
          },
          {
            // R4 (lane Rb): Ra's dialog (model, prompt, recipe, rows), ⇧R.
            id: "rerunChanges",
            label: "Rerun with changes…",
            icon: "mdi:tune-variant",
            shortcut: "rerunWithChanges",
            disabled: !canRunOf(ctx),
            reason: canRunOf(ctx) ? undefined : runTip(ctx),
            run: () => openRerunWithChanges({ runId: data.run_id }),
          },
        ]
      : []),
    {
      id: "delete",
      label: runCount > 1 ? `Delete experiment (${runCount} runs)` : "Delete",
      icon: "mdi:trash-can-outline",
      danger: true,
      disabled: !ctx.canDelete,
      reason: ctx.canDelete ? undefined : deleteTip(ctx),
      run: () =>
        ctx.requestDelete({
          ids: [data.experiment_id],
          runCount,
          name: data.experiment_name,
        }),
    },
  ];
}

/**
 * The leaderboard grid: AG Grid client side, runs grouped by a hidden
 * group column (§3.7), the baseline run pinned on top as a read-only copy.
 * `context` is mutated in place (AG Grid reads it once) and the cells are
 * refreshed when it changes.
 *
 * One view (A2-06…A2-12): the columns are G's layout (`useColumnLayout`,
 * applied through `useAgColumnShaping`): hide / show / freeze / move /
 * resize are the user's and remembered; the header menu (right-click, ⋮,
 * Shift+F10) is G's; the Experiment column is locked and frozen by default
 * (the auto group column stands in for it while grouped); checkboxes show on
 * hover; a row's right-click opens G's ContextMenu.
 */
const LeaderboardGrid = forwardRef(
  (
    {
      runs,
      catalog,
      layout,
      group,
      onGroup,
      context,
      onSelectionChange,
      onOpenRow,
    },
    ref,
  ) => {
    const agTheme = useAgThemeWith(THEME_PARAMS);
    const gridRef = useRef(null);
    const wrapRef = useRef(null);
    const ctxRef = useRef({});
    const [rowMenu, setRowMenu] = useState(null);
    const closeRowMenu = useCallback(() => setRowMenu(null), []);
    Object.assign(ctxRef.current, context);
    const hover = useHoverSelection(gridRef);
    const cm = useContextMenu();

    // EL#28 / EL#29: J/K move a visible row focus (no AG Grid cell focus),
    // X selects it, Enter opens, B pins, E folds its group.
    const focusRef = useRef(null);
    const displayed = () => {
      const api = gridRef.current?.api;
      if (!api) return [];
      const out = [];
      for (let i = 0; i < api.getDisplayedRowCount(); i += 1) {
        const n = api.getDisplayedRowAtIndex(i);
        // EL#12: J/K skip the ghost baseline row (it has no actions).
        if (n && !isGhostBaseline(n, n.data, ctxRef.current)) out.push(n);
      }
      return out;
    };
    const focusedNode = () => {
      const id = focusRef.current;
      return id ? displayed().find((n) => n.id === id) ?? null : null;
    };
    const setFocus = (node) => {
      const api = gridRef.current?.api;
      const prev = focusedNode();
      focusRef.current = node?.id ?? null;
      ctxRef.current.kbFocusId = focusRef.current;
      const nodes = [prev, node].filter(Boolean);
      if (nodes.length) api?.redrawRows({ rowNodes: nodes });
      if (node) api?.ensureNodeVisible(node);
    };

    useImperativeHandle(ref, () => ({
      kb: {
        // EL#35: Group menu's Expand all / Collapse all.
        expandAll: () => gridRef.current?.api?.expandAll(),
        collapseAll: () => gridRef.current?.api?.collapseAll(),
        move: (dir) => {
          const rows = displayed();
          if (!rows.length) return;
          const at = rows.findIndex((n) => n.id === focusRef.current);
          const next =
            at < 0
              ? rows[dir > 0 ? 0 : rows.length - 1]
              : rows[Math.min(rows.length - 1, Math.max(0, at + dir))];
          setFocus(next);
        },
        toggleSelect: () => {
          const n = focusedNode();
          if (n && !n.rowPinned) n.setSelected(!n.isSelected());
        },
        open: () => {
          const n = focusedNode();
          if (!n) return;
          if (n.group) {
            const first = n.allLeafChildren?.[0]?.data;
            if (first) onOpenRow(first, { experimentOnly: true });
          } else if (n.data) onOpenRow(n.data, {});
        },
        pin: () => {
          const n = focusedNode();
          if (n?.data && !n.group && ctxRef.current.canEdit)
            ctxRef.current.pinBaseline?.(n.data.run_id);
        },
        // R4 (lane Rb): ⇧R on the focused run (Ra's dialog).
        rerunChanges: () => {
          const n = focusedNode();
          if (
            n?.data &&
            !n.group &&
            ctxRef.current.canEdit &&
            canRerunHere(n.data)
          )
            openRerunWithChanges({ runId: n.data.run_id });
        },
        toggleGroup: () => {
          const n = focusedNode();
          if (!n) return;
          if (n.group) n.setExpanded(!n.expanded);
          else if (n.parent?.group && n.parent.level >= 0) {
            n.parent.setExpanded(false);
            setFocus(n.parent);
          }
        },
      },
      // the interface contract C2: the Columns menu host anchors to the grid header.
      headerElement: () =>
        wrapRef.current?.querySelector(".ag-header") ?? wrapRef.current,
      clearSelection: () => gridRef.current?.api?.deselectAll(),
      toggleSelection: (runId) => {
        const node = gridRef.current?.api?.getRowNode(runId);
        node?.setSelected(!node.isSelected());
      },
      scrollToRun: (runId) => {
        const api = gridRef.current?.api;
        const node = api?.getRowNode(runId);
        if (!node) return;
        if (node.parent && !node.parent.expanded) node.parent.setExpanded(true);
        api.ensureNodeVisible(node, "middle");
        api.flashCells({ rowNodes: [node] });
      },
    }));

    // A2-06: the name column is frozen by default (300 px, 260 under
    // 1366 px, min 220) and the user can unfreeze it from the header menu;
    // unfrozen, it takes the slack (AG Grid never flexes a pinned column).
    const narrow = useMediaQuery("(max-width:1365.95px)", { noSsr: true });
    const frozenWidth = nameWidthFor(narrow ? 1280 : 1920);
    const nameCol = layout.columns.find((c) => c.id === NAME_COLUMN);
    const nameFrozen = nameCol ? nameCol.pinned === "left" : true;
    const nameUserWidth = nameCol?.width ?? null;
    // A frozen column can't flex, so on a wide window it starts wider by
    // whatever the shown columns leave free (≤ 440 px): names stay whole
    // instead of a blank strip at the right. The user's own width wins.
    const room = useLeaderboardRoom();
    const used = layout.columns
      .filter((c) => !c.hidden && c.id !== NAME_COLUMN && c.pinned !== "left")
      .reduce((sum, c) => sum + (c.width ?? widthOfColumn(c)), 0);
    const slack = room ? Math.max(0, room - used - 8) : 0;
    const frozenDefault = Math.min(
      NAME_MAX_WIDTH,
      frozenWidth + Math.floor(slack / 20) * 20,
    );
    const nameSize = useMemo(
      () =>
        nameFrozen || nameUserWidth
          ? {
              width: nameUserWidth ?? frozenDefault,
              minWidth: 220,
              flex: null,
            }
          : { flex: 1, minWidth: 260 },
      [nameFrozen, nameUserWidth, frozenDefault],
    );
    // Initial visibility only (AG ignores `initialHide` after creation);
    // the layout owns hide / pin / order through applyColumnState.
    const hiddenRef = useRef(new Set());
    hiddenRef.current = new Set(layout.hiddenIds);

    const columnDefs = useMemo(() => {
      const defs = [
        {
          colId: "__group",
          field: "__group_key",
          rowGroup: group !== "none",
          hide: true,
          suppressColumnsToolPanel: true,
        },
      ];
      catalog.forEach((col) => {
        const base = {
          colId: col.id,
          headerName: col.label,
          // IT-R3 G2 P3: the statistic's rule on hover.
          ...(col.description && col.group === "Summary"
            ? { headerTooltip: col.description }
            : {}),
          initialHide: hiddenRef.current.has(col.id),
          sortable: col.sortable !== false,
          resizable: true,
        };
        if (col.id === NAME_COLUMN) {
          // While grouped, the auto group column is the name column.
          if (group !== "none") return;
          defs.push({
            ...base,
            ...nameSize,
            suppressMovable: true,
            suppressHeaderMenuButton: false,
            cellRenderer: NameCellWithActions,
            valueGetter: (p) => p.data?.label,
          });
        } else if (STATIC_COLUMNS[col.id]) {
          defs.push({ ...base, ...STATIC_COLUMNS[col.id] });
        } else if (col.group === "Evals") {
          const def = col.def;
          defs.push({
            ...base,
            width: COLUMN_WIDTH.eval,
            minWidth: 128,
            headerComponent: EvalHeader,
            headerComponentParams: { def },
            cellRenderer: EvalCell,
            cellRendererParams: { def },
            valueGetter: (p) =>
              p.data ? aggValueOf(p.data, def, context.agg) : null,
            aggFunc: def.higher_is_better === false ? "min" : "max",
            comparator: nullsLast,
            // Review 2 (S9): the first click puts the best runs first.
            sortingOrder: evalSortingOrder(def),
          });
        } else if (col.group === "Human") {
          defs.push({
            ...base,
            width: 130,
            cellRenderer: HumanCell,
            cellRendererParams: { def: col.def },
            valueGetter: (p) => p.data?.metrics?.human?.[col.id]?.avg ?? null,
            aggFunc: "max",
            comparator: nullsLast,
          });
        } else if (col.group === RUN_METRICS_GROUP) {
          defs.push({
            ...runMetricColDef(col, base),
            comparator: nullsLastComparator,
          });
        } else if (col.id.startsWith(META_PREFIX)) {
          const key = col.id.slice(META_PREFIX.length);
          defs.push({
            ...base,
            width: 170,
            cellRenderer: MetaCell,
            cellRendererParams: { metaKey: key },
            valueGetter: (p) => p.data?.metadata?.[key],
          });
        }
      });
      return defs;
    }, [catalog, group, context.agg, nameSize]);

    const autoGroupColumnDef = useMemo(
      () => ({
        headerName: "Experiment",
        ...nameSize,
        pinned: nameFrozen ? "left" : null,
        suppressMovable: true,
        suppressHeaderMenuButton: false,
        sortable: false,
        cellRendererParams: {
          innerRenderer: NameCellWithActions,
          suppressCount: true,
        },
      }),
      [nameSize, nameFrozen],
    );

    // G's header menu: `Group by` maps a column to its `group=` value.
    const groupColumn = columnOfGroup(group);
    const handleGroup = useCallback(
      (colId) => onGroup?.(colId ? GROUP_BY_COLUMN[colId] ?? "none" : "none"),
      [onGroup],
    );
    const shaping = useAgColumnShaping({
      gridRef,
      layout,
      group: groupColumn,
      onGroup: handleGroup,
    });
    const liveLayout = useRef(layout);
    liveLayout.current = layout;
    const liveGroup = useRef(groupColumn);
    liveGroup.current = groupColumn;
    // The auto group column IS the Experiment column: same menu.
    const getMainMenuItems = useCallback(
      (params) => {
        if (params.column?.getColId?.() !== AUTO_COLUMN)
          return shaping.getMainMenuItems(params);
        return toAgMenuItems(
          headerMenuItems(liveLayout.current, NAME_COLUMN, {
            group: liveGroup.current,
            onGroup: handleGroup,
          }),
        );
      },
      [shaping, handleGroup],
    );
    // Resizing the auto group column = resizing the Experiment column.
    const onColumnResized = useCallback(
      (e) => {
        shaping.onColumnResized(e);
        const autoCol = (e.columns ?? (e.column ? [e.column] : [])).find(
          (c) => c.getColId?.() === AUTO_COLUMN,
        );
        if (autoCol && e.finished !== false && e.source === "uiColumnResized")
          liveLayout.current.setWidth(
            NAME_COLUMN,
            Math.round(autoCol.getActualWidth()),
          );
      },
      [shaping],
    );

    // R2 (L2): iterations nest under their root run (collapsed by default)
    // when grouping by experiment or not at all; `group=iteration` groups
    // by root. Leaderboard runs come without lineage, so R1 joins it in.
    const lineage = useRunConfigs(context.datasetId);
    const [expanded, setExpanded] = useState(() => new Set());
    const nesting = group === "experiment" || group === "none";
    // `quick_tests=1` (header link) shows the unlisted quick tests too.
    const { search } = useLocation();
    const showQuick =
      new URLSearchParams(search).get(QUICK_TESTS_PARAM) === "1";
    const quick = useQuickTests(showQuick ? context.datasetId : null);
    const joined = useMemo(() => {
      const extra = showQuick
        ? (quick.data ?? []).filter(
            (q) => !runs.some((r) => r.run_id === q.run_id),
          )
        : [];
      return joinLineage([...runs, ...extra], lineage.data?.results);
    }, [runs, lineage.data, showQuick, quick.data]);
    // Roots get `__iterations` / `__latest`; collapsed iterations are hidden
    // by an external filter (not dropped), so group counts stay whole. The
    // pinned baseline's own row is hidden and its pinned copy has no toggle,
    // so its iterations (TS #2F, #2G) list as plain runs instead.
    const pinnedBaseId = context.baselineRun?.run_id ?? null;
    const rowData = useMemo(() => {
      const flat = liftIterationsOf(joined, pinnedBaseId);
      // L7c g5 (kept:nesting-words): a root counts every later run of a
      // config nested under it as a kept run, not an iteration.
      const list = nesting
        ? withKeptCounts(nestIterations(flat, { has: () => true }), flat)
        : flat;
      return list.map((r) => {
        const g =
          group === "iteration"
            ? iterationGroupOf(r, joined)
            : groupOf(r, group);
        return { ...r, __group_key: g.key, __group_label: g.label };
      });
    }, [joined, nesting, group, pinnedBaseId]);
    // IT-R2 G2 P8: a dataset where some run made calls is a voice dataset.
    const voiceDataset = useMemo(
      () => runs.some((r) => outputModalityOf(r) === "voice"),
      [runs],
    );
    const rootIds = useMemo(
      () => new Set(joined.map((r) => r.run_id)),
      [joined],
    );
    // IT-R1 G2 P2: the baseline's natural row is hidden (it sits pinned on
    // top; its group header says `baseline ↑`). A filter, not a drop, so
    // group counts stay whole.
    const baselineId = context.baselineRun?.run_id ?? null;
    const isExternalFilterPresent = useCallback(
      () => nesting || Boolean(baselineId),
      [nesting, baselineId],
    );
    const doesExternalFilterPass = useCallback(
      (node) => {
        const d = node.data;
        if (d && !node.rowPinned && baselineId && d.run_id === baselineId)
          return false;
        if (!nesting) return true;
        if (!d || d.__depth !== 1 || !rootIds.has(d.__root)) return true;
        return expanded.has(d.__root);
      },
      [rootIds, expanded, nesting, baselineId],
    );
    useEffect(() => {
      gridRef.current?.api?.onFilterChanged();
    }, [expanded, nesting, rootIds, baselineId]);
    const postSortRows = useCallback(
      (p) => {
        if (nesting || group === "iteration") {
          const order = nestSorted(p.nodes, (n) => n.data);
          p.nodes.splice(0, p.nodes.length, ...order);
        }
        // IT-R2 G2 P8: on a voice dataset, experiments that ran calls group
        // first; text-only and spoken-prompt experiments follow (their group
        // row says so with a chip). Stable: the column sort (newest first by
        // default) holds inside each part.
        if (!voiceDataset || group !== "experiment") return;
        const calls = (n) =>
          Boolean(n.group) &&
          (n.allLeafChildren ?? []).some(
            (c) => outputModalityOf(c.data) === "voice",
          );
        if (!p.nodes.some(calls)) return;
        const head = p.nodes.filter(calls);
        const tail = p.nodes.filter((n) => !calls(n));
        p.nodes.splice(0, p.nodes.length, ...head, ...tail);
      },
      [nesting, group, voiceDataset],
    );
    const primaryDef = catalog.find((c) => c.group === "Evals")?.def ?? null;
    const iter = useMemo(
      () => ({
        enabled: nesting,
        expanded,
        primaryDef,
        toggle: (rootId) =>
          setExpanded((prev) => {
            const next = new Set(prev);
            if (next.has(rootId)) next.delete(rootId);
            else next.add(rootId);
            return next;
          }),
      }),
      [nesting, expanded, primaryDef],
    );
    const cellContext = useMemo(
      () => ({ ...context, iter, rowMenu, openRowMenu: setRowMenu }),
      [context, iter, rowMenu],
    );
    ctxRef.current.iter = iter;

    // EL#12: a new baseline moves the ghost; row classes need a redraw.
    const baselineRunId = context.baselineRun?.run_id ?? null;
    useEffect(() => {
      gridRef.current?.api?.redrawRows();
    }, [baselineRunId]);

    // EL#14: when columns continue off-screen, the right edge fades and a
    // chevron scrolls to the next one.
    const [moreRight, setMoreRight] = useState(0);
    const checkMoreRight = useCallback(() => {
      const api = gridRef.current?.api;
      if (!api || api.isDestroyed?.()) return;
      const range = api.getHorizontalPixelRange();
      const hidden = api
        .getAllDisplayedColumns()
        .filter(
          (c) =>
            !c.getPinned() &&
            c.getLeft() + c.getActualWidth() > range.right + 2,
        ).length;
      setMoreRight((prev) => (prev === hidden ? prev : hidden));
    }, []);
    const scrollToNextColumn = useCallback(() => {
      const api = gridRef.current?.api;
      if (!api) return;
      const range = api.getHorizontalPixelRange();
      const next = api
        .getAllDisplayedColumns()
        .find(
          (c) =>
            !c.getPinned() &&
            c.getLeft() + c.getActualWidth() > range.right + 2,
        );
      if (next) api.ensureColumnVisible(next, "end");
      setTimeout(checkMoreRight, 50);
    }, [checkMoreRight]);

    const pinnedTopRowData = useMemo(
      () =>
        context.baselineRun ? [{ ...context.baselineRun, __pinned: true }] : [],
      [context.baselineRun],
    );

    // IT-R4 G2 P7: keyboard use turns the row focus ring on; a pointer
    // press turns it off (`:focus-visible` for the J/K row).
    const [kbOn, setKbOn] = useState(false);
    useEffect(() => {
      const onKey = (e) => {
        if (e.metaKey || e.ctrlKey || e.altKey) return;
        if (e.target?.closest?.("input,textarea,[contenteditable=true]"))
          return;
        setKbOn(true);
      };
      const onPointer = () => setKbOn(false);
      window.addEventListener("keydown", onKey, true);
      window.addEventListener("pointerdown", onPointer, true);
      return () => {
        window.removeEventListener("keydown", onKey, true);
        window.removeEventListener("pointerdown", onPointer, true);
      };
    }, []);

    const rowSelection = useMemo(
      () => ({
        mode: "multiRow",
        groupSelects: "descendants",
        checkboxes: true,
        headerCheckbox: true,
        enableClickSelection: false,
        isRowSelectable: (node) =>
          !node.rowPinned && !isGhostBaseline(node, node.data, ctxRef.current),
      }),
      [],
    );

    const handleSelection = useCallback(
      (e) => {
        hover.onSelectionChanged(e);
        const selected = e.api
          .getSelectedNodes()
          .filter((n) => !n.group && n.data && !n.rowPinned)
          .map((n) => n.data);
        onSelectionChange(selected);
      },
      [onSelectionChange, hover],
    );

    // A2-12: right-click a row = the row's menu (G's ContextMenu, never
    // AG Grid's own); Shift+F10 / the ContextMenu key on the J/K row.
    const onCellContextMenu = useCallback(
      (e) => {
        const items = rowMenuItems(e.node, e.data, ctxRef.current);
        if (items.length && e.event) cm.open(e.event, items);
      },
      [cm],
    );
    useEffect(() => {
      const onKey = (e) => {
        if (!isMenuKey(e) || !focusRef.current) return;
        if (
          document.activeElement?.closest?.(
            "input,textarea,[contenteditable=true]",
          )
        )
          return;
        const node = focusedNode();
        if (!node) return;
        const items = rowMenuItems(node, node.data, ctxRef.current);
        const el = document.querySelector(
          `.ag-center-cols-container .ag-row[row-id="${CSS.escape(node.id)}"]`,
        );
        if (!items.length || !el) return;
        e.preventDefault();
        e.stopPropagation();
        cm.open(el, items);
      };
      document.addEventListener("keydown", onKey);
      return () => document.removeEventListener("keydown", onKey);
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [cm]);

    // Clicks on buttons, links and checkboxes inside a row are theirs, not
    // the row's. Decide in the capture phase, while the DOM is intact (a
    // menu opening re-renders the icon that was clicked).
    const controlClickRef = useRef(null);
    const handleRowClicked = useCallback(
      (e) => {
        if (e.event && e.event === controlClickRef.current) return;
        if (isClickOnControl(e.event)) return;
        if (e.node.group) {
          if (ctxRef.current.group !== "experiment") {
            e.node.setExpanded(!e.node.expanded);
            return;
          }
          const first = e.node.allLeafChildren?.[0]?.data;
          if (first) onOpenRow(first, { experimentOnly: true });
          return;
        }
        // EL#12: the ghost row jumps to the pinned baseline instead.
        if (isGhostBaseline(e.node, e.data, ctxRef.current)) {
          const top = e.api.getPinnedTopRow(0);
          e.api.ensureIndexVisible(0);
          if (top) e.api.flashCells({ rowNodes: [top] });
          return;
        }
        if (e.data) onOpenRow(e.data, {});
      },
      [onOpenRow],
    );

    return (
      <Box
        ref={wrapRef}
        className={[hover.className, kbOn ? "lb-kb-on" : null]
          .filter(Boolean)
          .join(" ")}
        // the interface contract C2: a header right-click opens the Columns menu (the page's
        // one persistent host), never the browser's menu.
        onContextMenuCapture={(e) => {
          const head = e.target?.closest?.(".ag-header");
          if (!head || e.target.closest(".ag-header-select-all")) return;
          if (!openColumnsMenu(layout.key)) return;
          e.preventDefault();
          e.stopPropagation();
        }}
        onClickCapture={(e) => {
          controlClickRef.current = isClickOnControl(e.nativeEvent)
            ? e.nativeEvent
            : null;
        }}
        sx={(theme) => ({
          flex: 1,
          minHeight: 0,
          position: "relative",
          // Critic r3 #5: while the floating selection bar shows, the grid
          // ends above it (SelectionBar publishes its height + 16 px).
          pb: `var(${SELECTION_GUTTER_VAR}, 0px)`,
          "& .ag-row": { cursor: "pointer" },
          // IT-R1 G2 P2: `Compare any runs…` points at the checkboxes.
          ...(context.compareAny
            ? {
                "& .ag-selection-checkbox .ag-checkbox-input-wrapper, & .lb-baseline-tick":
                  {
                    outline: `2px solid ${theme.palette.primary.main}`,
                    outlineOffset: 2,
                    borderRadius: "4px",
                    bgcolor: alpha(theme.palette.primary.main, 0.12),
                  },
              }
            : {}),
          // Row hover / keyboard focus reveal the row's quick actions.
          // They open in their own space (the name ellipsises before them).
          "& .ag-row-hover .lb-hover-actions, &.lb-kb-on .lb-kb-focus .lb-hover-actions, & .lb-hover-actions:focus-within, & .lb-hover-actions:has([aria-expanded='true'])":
            {
              opacity: 1,
              pointerEvents: "auto",
              maxWidth: 400,
              pl: 1,
              pr: 0.5,
            },
          // R4 hc-10: at 1440 and wider, Open and Side by side are always
          // visible (icons with labels); narrower windows show them on hover.
          // The pinned baseline row keeps its name, chips and `vs #1B` line
          // whole: its actions open on hover only (the line is a link).
          "@media (min-width: 1440px)": {
            "& .lb-hover-actions:not(.lb-hover-base)": {
              opacity: 1,
              pointerEvents: "auto",
              maxWidth: 400,
              pl: 1,
              pr: 0.5,
            },
          },
          // IT-R4 G2 P7: the focus ring shows only after keyboard use
          // (the `:focus-visible` rule), never on load or after a click.
          "&.lb-kb-on .ag-pinned-left-cols-container .lb-kb-focus, &.lb-kb-on .ag-center-cols-container .lb-kb-focus":
            {
              boxShadow: `inset 0 0 0 2px ${alpha(theme.palette.primary.main, 0.6)}`,
            },
          // Numeric headers sit over right-aligned numbers.
          "& .ag-header-cell-comp-wrapper": { width: "100%" },
          // The column menu (⋮, ours and AG's) shows on header hover / focus
          // only, so the header row reads as labels.
          "& .ag-header-cell:hover .fa-header-menu-btn, & .ag-header-cell:focus-within .fa-header-menu-btn":
            { opacity: 1 },
          "& .ag-header-cell:not(:hover):not(:focus-within) .ag-header-cell-menu-button":
            { opacity: 0 },
          "& .lb-group-row": {
            bgcolor: alpha(theme.palette.text.primary, 0.025),
          },
          "& .ag-pinned-left-cols-container .lb-baseline-row": {
            boxShadow: `inset 3px 0 0 ${theme.palette.blue?.[500] ?? theme.palette.info.main}`,
          },
          "& .lb-baseline-row": {
            bgcolor: alpha(theme.palette.info.main, 0.04),
          },
          // EL#12: the ghost baseline row shows its name only.
          "& .lb-ghost-row .ag-cell:not([col-id='experiment']):not([col-id='ag-Grid-AutoColumn']) > *":
            { visibility: "hidden" },
          "& .ag-floating-top": {
            borderBottomColor: "divider",
          },
          // A focused or scrolled-to row (Tab, links, scrollIntoView) stops
          // below the sticky experiment group row, never underneath it, so
          // its name stays visible and clickable.
          "& .ag-body-viewport": { scrollPaddingTop: `${ROW_HEIGHT}px` },
          "& .ag-cell": { display: "flex", alignItems: "center" },
          "& .ag-cell-wrapper": { width: "100%", height: "100%", minWidth: 0 },
          "& .ag-group-value": {
            minWidth: 0,
            flex: 1,
            height: "100%",
            display: "flex",
            alignItems: "center",
            overflow: "hidden",
          },
        })}
      >
        <LeaderboardContext.Provider value={cellContext}>
          <AgGridReact
            ref={gridRef}
            theme={agTheme}
            rowData={rowData}
            columnDefs={columnDefs}
            autoGroupColumnDef={autoGroupColumnDef}
            groupDisplayType="singleColumn"
            groupDefaultExpanded={1}
            groupHideParentOfSingleChild
            suppressAggFuncInHeader
            pinnedTopRowData={pinnedTopRowData}
            postSortRows={postSortRows}
            isExternalFilterPresent={isExternalFilterPresent}
            doesExternalFilterPass={doesExternalFilterPass}
            rowSelection={rowSelection}
            selectionColumnDef={{
              pinned: "left",
              width: SELECTION_WIDTH,
              lockPosition: "left",
              suppressHeaderMenuButton: true,
              suppressHeaderContextMenu: true,
            }}
            getRowId={(p) =>
              p.data.__pinned ? `pinned:${p.data.run_id}` : p.data.run_id
            }
            rowClassRules={{
              "lb-group-row": (p) => !!p.node.group,
              "lb-baseline-row": (p) => p.node.rowPinned === "top",
              "lb-ghost-row": (p) =>
                isGhostBaseline(p.node, p.data, ctxRef.current),
              "lb-kb-focus": (p) =>
                !!p.node.id && p.node.id === ctxRef.current.kbFocusId,
            }}
            context={ctxRef.current}
            rowHeight={ROW_HEIGHT}
            headerHeight={HEADER_HEIGHT}
            animateRows={false}
            suppressCellFocus
            suppressDragLeaveHidesColumns
            {...shaping}
            getMainMenuItems={getMainMenuItems}
            onSelectionChanged={handleSelection}
            onRowClicked={handleRowClicked}
            onCellContextMenu={onCellContextMenu}
            preventDefaultOnContextMenu
            getContextMenuItems={NO_AG_CONTEXT_MENU}
            onFirstDataRendered={(e) => {
              checkMoreRight(e);
              // D25: a visible row cursor on load, so J / K / Enter work
              // without a click (only when nothing has the cursor yet).
              if (!focusRef.current) setFocus(displayed()[0] ?? null);
            }}
            onBodyScroll={checkMoreRight}
            onGridSizeChanged={checkMoreRight}
            onDisplayedColumnsChanged={checkMoreRight}
            onColumnResized={(e) => {
              onColumnResized(e);
              checkMoreRight();
            }}
            defaultColDef={DEFAULT_COL_DEF}
          />
        </LeaderboardContext.Provider>
        <RunRowMenuHost
          menu={rowMenu}
          rows={rowData}
          context={cellContext}
          onClose={closeRowMenu}
        />
        <ContextMenu {...cm.menuProps} />
        {moreRight > 0 && (
          <Box
            sx={(theme) => ({
              position: "absolute",
              top: 0,
              bottom: 0,
              right: 0,
              width: 40,
              pointerEvents: "none",
              background: `linear-gradient(90deg, ${alpha(theme.palette.background.paper, 0)} 0, ${alpha(theme.palette.background.paper, 0.92)} 100%)`,
              display: "flex",
              alignItems: "flex-start",
              justifyContent: "flex-end",
              pt: `${(HEADER_HEIGHT - 24) / 2}px`,
              zIndex: 2,
            })}
          >
            <CustomTooltip
              show
              title={`${fmtCount(moreRight, "more column")} to the right · Shift+scroll also works`}
              size="small"
              placement="top"
              arrow
            >
              <IconButton
                size="small"
                aria-label={`Scroll to ${fmtCount(moreRight, "more column")}`}
                onClick={scrollToNextColumn}
                sx={{
                  pointerEvents: "auto",
                  width: 24,
                  height: 24,
                  color: "text.primary",
                  bgcolor: "background.paper",
                  border: "1px solid",
                  borderColor: "divider",
                  "&:hover": { bgcolor: "action.hover" },
                }}
              >
                <Iconify icon="mdi:chevron-right" width={16} />
              </IconButton>
            </CustomTooltip>
          </Box>
        )}
      </Box>
    );
  },
);

LeaderboardGrid.displayName = "LeaderboardGrid";

LeaderboardGrid.propTypes = {
  runs: PropTypes.array.isRequired,
  catalog: PropTypes.array.isRequired,
  layout: PropTypes.object.isRequired,
  group: PropTypes.string.isRequired,
  onGroup: PropTypes.func,
  context: PropTypes.object.isRequired,
  onSelectionChange: PropTypes.func.isRequired,
  onOpenRow: PropTypes.func.isRequired,
};

export default LeaderboardGrid;
