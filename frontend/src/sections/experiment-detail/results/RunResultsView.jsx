import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import PropTypes from "prop-types";
import {
  Box,
  Checkbox,
  FormControlLabel,
  Skeleton,
  Stack,
  Typography,
  useMediaQuery,
} from "@mui/material";
import { useLocation, useNavigate } from "react-router-dom";
import { enqueueSnackbar } from "notistack";
import ViewMenu from "src/sections/common/ux/oneview/ViewMenu";
import { useSections } from "src/sections/common/ux/oneview/Section";
import useViewportDensity from "src/sections/common/ux/oneview/useViewportDensity";
import { useLayoutPrefs } from "src/sections/common/ux/oneview/layoutPrefs";
import { headerMenuItems } from "src/sections/common/ux/oneview/headerMenuItems";
import { nextDensity } from "src/sections/common/ux/DensityMenu";
import ErrorState from "src/sections/common/ux/ErrorState";
import { isMixedRunsRefusal } from "src/utils/mixedRunsRefusal";
import MixedRunsNotice from "./MixedRunsNotice";
import { TextDiffLegend } from "src/sections/common/ValueViewer/TextDiff";
import useShortcuts from "src/sections/common/ux/useShortcuts";
import { useRegisterCommands } from "src/sections/common/CommandPalette/commandRegistry";
import notify from "src/sections/common/ux/toast";
import { fmtCount } from "src/sections/common/format/fmt";
import { pairOf, reviewedText, useReviewMarks } from "./reviewMarks";
import { showLabel } from "./ResultsToolbar";
import { taskCompareHref } from "src/sections/common/Analysis/analysisLinks";
import { useAuthContext } from "src/auth/hooks";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";
import { useRunColors } from "src/sections/common/Runs/runColors";
import { brandedEntryOfConfig } from "src/sections/common/RunSources/brandLogos";
import useValueViewMode from "src/sections/common/ValueViewer/useValueViewMode";
import {
  ANY_RUN,
  rowsPill,
  setMovementPill,
  samePill,
  serializePills,
  togglePill,
} from "src/sections/common/Results/filterPills";
import {
  useRerunResultCells,
  useRunResults,
} from "src/api/develop/run-results";
import ExperimentBarDataRightSection from "../ExperimentBarRightSection/ExperimentBarDataRightSection";
import { ResultsStatusLine } from "../ExperimentData/ExperimentStatusBar";
import { erroredTriesOf } from "./erroredTries";
import useResultsUrlState from "./useResultsUrlState";
import usePausedResults from "./usePausedResults";
import { useRescore } from "src/sections/common/Runs/useRescore";
import { useEvalVersionDoor } from "./headers/useEvalVersionDoor";
import useResultsPrefs from "./useResultsPrefs";
import {
  RESULTS_COLUMN_GROUPS,
  buildResultColumns,
  scorePinFor,
} from "./resultsColumns";
import { buildGroupMenu, fromHeaderItems, trialAggItems } from "./groupMenu";
import { runLedgerParts } from "src/sections/common/Runs/runLedger";
import useResultsColumns from "./useResultsColumns";
import ResultsGrid from "./ResultsGrid";
import ResultsToolbar, { anyWorseTip } from "./ResultsToolbar";
import FilterPills from "./FilterPills";
import ResultsExpressionDialog from "src/sections/common/filters/ResultsExpressionDialog";
import {
  expressionPill,
  replacePill,
} from "src/sections/common/filters/expressionPill";
// HO results R2: `Hide rows with no output` as a View menu line too.
import {
  hideEmptyViewExtra,
  hidesEmpty,
  withHideEmpty,
} from "src/sections/common/filters/hideEmpty";
import { HideEmptyToggle } from "src/sections/common/filters/HideEmptySwitch";
import RunBar from "./RunBar";
import SelectionBar from "./SelectionBar";
import { inputsPreviewChoice } from "src/sections/common/Preview/distinguishingField";
import useDatasetColumnsLite from "src/sections/develop/useDatasetColumnsLite";
import { columnNamesById, displayInputs } from "./cells/displayInputs";
import EvalDetailsDialog from "./EvalDetailsDialog";
import ResultDrawer from "./ResultDrawer";
import ResultsVerdict from "./ResultsVerdict";
import { listDirection, triageRunOf } from "./triageRun";
import usePagePair from "../paths/usePagePair";
import RunsChip from "./RunsChip";
import { primaryPassMetric } from "./resultsSimple";
import { useDatasetKind } from "src/sections/common/Datasets/datasetKind";
import { useVoiceSummary } from "src/api/develop/r4";
import { callTable } from "src/sections/common/Analysis/voiceSummaryModel";
import { voiceQuickFilters } from "./voiceFilters";
import { shareLinkOf, shareLinkSummary } from "./shareLink";
import { copyText } from "src/sections/common/ux/CopyButton";
import { rowLabelOf } from "src/sections/common/Runs/runNumbers";
import { defaultSortFor } from "./sortModel";
import { useRunConfigs } from "src/api/develop/r2";
import LiveStatus from "./LiveStatus";
import RetestStrip from "./RetestStrip";
import SeeWhyButton from "./SeeWhyButton";
import { populationLine } from "./populationLine";
import { bannerPairOf } from "../iterate/bannerPair";
import { fixCounterText, useIterateStore } from "../iterate/iterateStore";
import { productionOf, retestSummary } from "./retestModel";
import {
  fixRunsNextToBaseline,
  liveProgress,
  withLiveProgress,
  newResultsSince,
} from "./liveModel";
import { countsByRun, usePublishLiveCounts } from "./liveCountShare";

/**
 * Row height per density (one view, A3-04/06): Compact is one line with
 * the trial pips beside it; the others clamp 3 / 6 / 20 lines above the
 * trace chip row (cells read `linesFor(rowHeight)`).
 */
export const ROW_HEIGHT = Object.freeze({
  compact: 44,
  comfortable: 104,
  tall: 164,
  full: 444,
});
export const COMPACT_ROW_HEIGHT = ROW_HEIGHT.compact;

/**
 * The density a results page opens with: the viewer's, else by window.
 * R4 8f: compact below 1080 px high, so 8+ rows sit above the fold at
 * 1440×900; the full-HD window keeps the 3-line preview.
 */
export const defaultResultsDensity = (viewport) =>
  viewport === "tall" ? "comfortable" : "compact";

/** URL keys a saved results view carries (and `Reset view` clears). */
// L7c g6 (grid:search): the search box is a view key too, so links,
// reloads and saved views keep it.
const VIEW_KEYS = ["show", "filters", "sort", "agg", "diff", "search"];
const NOT_IN_RELEASE = "Not available in this release";

/**
 * The results surface for 1..N runs (PRODUCT-SPEC §3.10): run bar, toolbar,
 * filter pills, grid, status line and the result drawer. Mounted by the
 * experiment Data tab (that experiment's runs) and the compare Data tab
 * (`runs=`). State lives in the URL; display prefs in localStorage.
 */
// IT-R4 G3 (P3): fix runs whose `Fixed rows (n)` filter was applied.
const FIXED_FILTER_APPLIED = new Set();

const RunResultsView = ({
  datasetId,
  runIds,
  baselineId,
  mode,
  experimentId,
  initialRuns,
  onBaselineChange,
  onRemoveRun,
  onAddRuns,
  onAddEvaluations,
  onExperimentEdited,
}) => {
  const url = useResultsUrlState();
  const { patch } = url;
  const navigate = useNavigate();
  const { pathname, search: locationSearch } = useLocation();
  // One view: density and the open sections are the viewer's (layout
  // `results`, per user); the window only picks the defaults.
  const viewport = useViewportDensity();
  const [viewPrefs, patchView] = useLayoutPrefs("results");
  const density = viewPrefs.density ?? defaultResultsDensity(viewport);
  const setDensity = useCallback((d) => patchView({ density: d }), [patchView]);
  const sections = useSections("results");
  const prefs = useResultsPrefs(datasetId);
  const { showReasons, setShowReasons } = prefs;
  const [format, setFormat] = useValueViewMode();
  const search = useMemo(
    () => new URLSearchParams(locationSearch).get("search") ?? "",
    [locationSearch],
  );
  const setSearch = useCallback(
    (v) => patch({ search: v ? String(v) : null }),
    [patch],
  );
  const [selectionSnapshot, setSelectionSnapshot] = useState(null);
  const [evalDetails, setEvalDetails] = useState(null);
  const gridRef = useRef(null);
  const { role } = useAuthContext();
  const readOnly = !RolePermission.DATASETS[PERMISSIONS.UPDATE][role];

  const multiRun = runIds.length > 1;
  const show = multiRun ? url.show : "all";
  // R3: Regressed first when there is a baseline (an explicit `sort=` wins,
  // `sort=order` included).
  const defaultSort = defaultSortFor({ runCount: runIds.length, baselineId });
  const sortParam = new URLSearchParams(locationSearch).get("sort");
  const sort = sortParam ?? defaultSort;
  const view = useMemo(
    () => ({
      runIds,
      baselineId,
      pills: url.pills,
      show,
      search,
      sort,
      // W1-8: `run` (each run's own reducer) is the page's default, not a
      // server mode; the server answers every reducer in `aggregates`.
      // L7c g6: an old `agg=pass` link reads as the default.
      agg: url.agg === "run" || url.agg === "pass" ? undefined : url.agg,
      mixVersions: url.mixVersions,
    }),
    [
      runIds,
      baselineId,
      url.pills,
      show,
      search,
      sort,
      url.agg,
      url.mixVersions,
    ],
  );
  const exportView = useMemo(() => ({ ...view, source: "page" }), [view]);
  const {
    data: liveData,
    isLoading,
    isError,
    error: resultsError,
    isFetching,
    refetch,
  } = useRunResults(datasetId, view);
  // iterate r2 G3 (P7): `Pause updates` freezes the rows, counts and
  // verdict inputs of a running view (the poll keeps going underneath).
  const { data, paused, setPaused, resultsBlocked } = usePausedResults({
    datasetId,
    view,
    liveData,
    isError,
  });
  const resultsScope = JSON.stringify([datasetId, view]);
  // A grid callback owns rows only in its dataset/view. Mask a mismatch before
  // SelectionBar can consume it, and discard storage so returning cannot revive it.
  const setSelected = useCallback((rows) => {
    if (!resultsBlocked) setSelectionSnapshot({ scopeKey: resultsScope, rows });
  }, [resultsScope, resultsBlocked]);
  useEffect(() => {
    setSelectionSnapshot((current) =>
      resultsBlocked || current?.scopeKey !== resultsScope ? null : current,
    );
  }, [resultsScope, resultsBlocked]);
  const selected = !resultsBlocked && selectionSnapshot?.scopeKey === resultsScope
    ? selectionSnapshot.rows : [];
  const liveResults = resultsBlocked ? null : liveData;
  const liveRunsNow = useMemo(
    () =>
      withLiveProgress(
        liveResults?.summary?.runs,
        liveResults?.summary?.runs,
        liveResults?.summary?.examples_total,
      ),
    [liveResults],
  );
  const live = liveProgress(liveRunsNow);
  // options-truth L2 finalize: the header pill reads these counts while
  // this tab is open (liveCountShare), so `Running · n/30` and the grid's
  // `n/30` are one number.
  const shareId = mode === "experiment" ? experimentId : null;
  // L7c g6 (live:status): `Pause updates` holds every number on the
  // screen — the grid, its headers, the footer and the header pill — until
  // Update; one number per screen.
  const shownRunsNow = useMemo(
    () =>
      paused
        ? withLiveProgress(
            paused.summary?.runs,
            paused.summary?.runs,
            paused.summary?.examples_total,
          )
        : liveRunsNow,
    [paused, liveRunsNow],
  );
  const sharedCounts = useMemo(() => countsByRun(shownRunsNow), [shownRunsNow]);
  usePublishLiveCounts(shareId, sharedCounts, {
    datasetId, view, blocked: resultsBlocked,
  });
  const newCount = paused
    ? newResultsSince(paused.summary?.runs, liveData?.summary?.runs)
    : 0;
  // P5: a fix-run sits next to the baseline (its parent chain from R1).
  // The configs also name each run's source: a branded one (Claude Code,
  // LangGraph, LiveKit) rides on the run so its header chip shows the logo.
  const { data: runCfg, held: runsHeld } = useRunConfigs(datasetId, {
    enabled: runIds.length > 0,
  });
  const summary = useMemo(() => {
    const s = data?.summary;
    if (!s?.runs?.length) return s;
    const cfgById = Object.fromEntries(
      (runCfg?.results ?? []).map((r) => [r.run_id, r]),
    );
    const withParents = s.runs.map((r) => {
      const c = cfgById[r.run_id];
      if (!c) return r;
      const parent =
        !r.parent_run_id && c.parent_run_id
          ? { parent_run_id: c.parent_run_id }
          : null;
      // Only a brand: a generic kind word would squeeze the run's name.
      const brand =
        !r.config && brandedEntryOfConfig(c.config, c.kind)
          ? {
              config: c.config,
              kind: r.kind ?? c.kind,
              source: r.source ?? c.source,
            }
          : null;
      return parent || brand ? { ...r, ...parent, ...brand } : r;
    });
    return {
      ...s,
      runs: fixRunsNextToBaseline(withParents, baselineId),
      // P1: rows imported from production get the Production column.
      production: (data?.rows ?? []).some((r) => productionOf(r)),
    };
  }, [data, runCfg, baselineId]);
  const rows = useMemo(() => data?.rows ?? [], [data]);
  const rowIds = useMemo(() => rows.map((r) => r.example_id), [rows]);

  const runs = useMemo(() => {
    if (resultsBlocked) return [];
    const fromSummary = summary?.runs;
    // S3: progress from the live summary (one query), clamped.
    if (fromSummary?.length)
      return withLiveProgress(
        fromSummary,
        data?.summary?.runs,
        summary?.examples_total,
      );
    const byId = Object.fromEntries(
      (initialRuns ?? []).map((r) => [r.run_id, r]),
    );
    return runIds.map((id) => byId[id]).filter(Boolean);
  }, [summary, initialRuns, runIds, data, resultsBlocked]);
  const runsById = useMemo(
    () => Object.fromEntries(runs.map((r) => [r.run_id, r])),
    [runs],
  );
  // iterate r1 G3 (P10): a run that joins the view (a fix-run that just
  // finished) scrolls its output column and its run chip into view.
  const seenRuns = useRef(null);
  useEffect(() => {
    const ids = runs.filter((r) => !r.not_found).map((r) => r.run_id);
    if (!ids.length) return undefined;
    const prev = seenRuns.current;
    seenRuns.current = ids;
    const added = prev ? ids.find((id) => !prev.includes(id)) : null;
    if (!added) return undefined;
    const t = setTimeout(() => {
      const api = gridRef.current?.api;
      const colId = `output|${added}`;
      // P5: the new column sits next to the baseline (fixRunsNextToBaseline);
      // bring it into view without pushing the baseline off-screen.
      if (api && !api.isDestroyed?.() && api.getColumn?.(colId))
        api.ensureColumnVisible(colId, "auto");
    }, 400);
    return () => clearTimeout(t);
  }, [runs]);
  const colors = useRunColors(
    runs.map((r) => r.run_id),
    baselineId,
  );
  const metrics = useMemo(() => summary?.metrics ?? [], [summary]);
  const defsByKey = useMemo(
    () =>
      Object.fromEntries(
        [
          ...metrics,
          ...(summary?.human_metrics ?? []),
          ...(summary?.system_metrics ?? []),
        ].map((d) => [d.key, d]),
      ),
    [metrics, summary],
  );
  const firstCandidate = runs.find(
    (r) => r.run_id !== baselineId && !r.not_found,
  )?.run_id;

  // ---------------------------------------------------------- pills
  const setPills = useCallback(
    (next) => patch({ filters: next.length ? JSON.stringify(next) : null }),
    [patch],
  );
  const addPill = useCallback(
    (pill) => {
      if (url.pills.some((p) => samePill(p, pill))) return;
      setPills(togglePill(url.pills, pill));
    },
    [url.pills, setPills],
  );
  const onMovement = useCallback(
    (metric, run, dir) => {
      const active = url.pills.find(
        (p) =>
          p.type === "movement" &&
          p.metric === metric &&
          p.run === run &&
          p.dir === dir,
      );
      setPills(
        active
          ? url.pills.filter((p) => p !== active)
          : setMovementPill(url.pills, { type: "movement", metric, dir, run }),
      );
    },
    [url.pills, setPills],
  );

  // ---------------------------------------------------------- columns
  const { kind: datasetKind, dataset: datasetMeta } = useDatasetKind(datasetId);
  // A native run's row keys its input by dataset column id
  // (`{values: {<id>: value}}`): the Input cell names each value by its
  // column (the dataset's columns, one cached read shared with the list).
  const { data: datasetColumns } = useDatasetColumnsLite(datasetId);
  const inputNames = useMemo(
    () => columnNamesById(datasetColumns),
    [datasetColumns],
  );
  const taskSuite = datasetKind === "task_suite";
  const rowNoun = taskSuite ? "task" : "row";
  const [runsOpen, setRunsOpen] = useState(false);
  // A3-05: the Why column is data-driven (multi-run, Show ≠ All).
  const why = multiRun && show !== "all";
  // MUST 5: scenarios datasets with calls get the voice
  // columns as their default preset and one-click voice filters.
  // (`datasetKindOf` folds `scenarios` into `rows`; read the raw kind.)
  const isScenarios = datasetMeta?.kind === "scenarios";
  const { data: voiceData } = useVoiceSummary(datasetId, {
    runIds,
    baselineId,
    enabled: isScenarios && runIds.length > 0,
  });
  const voice = useMemo(() => {
    if (!isScenarios || !voiceData?.voice) return null;
    const table = callTable(voiceData, { runIds });
    return Object.keys(table.calls).length ? table : null;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isScenarios, voiceData, runIds.join(",")]);
  const voiceFilters = useMemo(
    () => voiceQuickFilters(voice?.calls, { runIds }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [voice, runIds.join(",")],
  );
  // P8: freeze the score columns after Input when they fit this window
  // (decided once per window size; the viewer's own freezing wins).
  const [gridWidth, setGridWidth] = useState(() =>
    typeof window === "undefined" ? 1200 : window.innerWidth - 240,
  );
  useEffect(() => {
    const onResize = () => setGridWidth(window.innerWidth - 240);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);
  const scorePin = useMemo(
    () =>
      scorePinFor(summary, {
        gridWidth,
        primaryKey: primaryPassMetric(summary?.metrics)?.key ?? null,
      }),
    [summary, gridWidth],
  );
  const below1500 = useMediaQuery("(max-width:1499.95px)");
  const layout = useResultsColumns({
    summary,
    datasetId,
    scopeId:
      mode === "experiment" && experimentId
        ? experimentId
        : `compare-${datasetId}`,
    why,
    // P8: the Why column starts shown only with the Regressed filter (or a
    // regressed movement pill).
    whyDefault:
      show === "regressed" ||
      url.pills.some((p) => p.type === "movement" && p.dir === "regressed"),
    taskSuite,
    voice: Boolean(voice),
    scorePin,
    primaryKey: primaryPassMetric(summary?.metrics)?.key ?? null,
    expectedDefault: !(
      below1500 &&
      ((summary?.runs ?? []).filter((r) => !r.not_found).length >= 3 ||
        (rows.length > 0 &&
          !rows.some((r) => r.expected?.primary || r.expected?.value)))
    ),
  });
  // W1-2: "Re-run this eval" is the one re-score door (useRescore): a
  // confirm with the judge's cost, then a new run per run with the rows
  // whose verdict flipped.
  const rescore = useRescore({ datasetId });
  const { request: requestRescore } = rescore;
  // W1-3: See changes, Use vN on a run, and Compare anyway.
  const versionDoor = useEvalVersionDoor({
    runsById,
    requestRescore,
    patch,
    datasetId,
  });
  const liveRuns = useMemo(() => runs.filter((r) => !r.not_found), [runs]);
  // W1-2 / W1-7: a re-score from this page starts from the newest run of
  // each config in view (an older run in view already has a newer one).
  const rescoreRuns = useMemo(() => {
    const latest = liveRuns.filter((r) => r.latest_of_config !== false);
    return latest.length ? latest : liveRuns;
  }, [liveRuns]);
  // W1-7: those whose errored tries carry scores (their policy can change).
  const erroredRuns = useMemo(
    () =>
      rescoreRuns.filter(
        (r) => (r.errored_tries ?? 0) > 0 && r.target_type !== "external",
      ),
    [rescoreRuns],
  );
  // L7c g6 (status:ledger): `<N> errored tries counted as 0` in the footer
  // for every run in view (plain text; the re-score door is not offered).
  const erroredCounts = useMemo(() => erroredTriesOf(liveRuns), [liveRuns]);
  const groupMenu = useCallback(
    (group) =>
      buildGroupMenu(group, {
        agg: url.agg,
        setAgg: (agg) => patch({ agg: agg === "run" ? null : agg }),
        firstCandidate,
        multiRun,
        showReasons,
        toggleReasons: () => setShowReasons((v) => !v),
        hide: () => layout.hide(group.key),
        layoutItems: fromHeaderItems(headerMenuItems(layout, group.key)),
        sortBy: (sort) => patch({ sort }),
        openEvalDetails: (g) => setEvalDetails(g.def),
        rerunEval: (g) =>
          requestRescore({
            runs: rescoreRuns.filter((r) => g.def?.uem_ids?.[r.run_id]),
            evalIdsOf: (r) => [g.def?.uem_ids?.[r.run_id]],
            evalNames: [g.def?.name ?? g.key],
          }),
        readOnly,
        mode,
      }),
    [
      url.agg,
      patch,
      firstCandidate,
      multiRun,
      showReasons,
      setShowReasons,
      layout,
      readOnly,
      mode,
      requestRescore,
      rescoreRuns,
    ],
  );
  const primaryMetric = useMemo(() => primaryPassMetric(metrics), [metrics]);
  // Below 1500 px the toolbar keeps one row with a shorter Search and an
  // icon Filter; the ActionBar folds the rest by priority.
  const narrowToolbar = useMediaQuery("(max-width:1439.95px)");
  // P5 (rule 10): a laptop window (< 820 px high) tightens the chrome above
  // the grid so the grid keeps ≥ 60 % of the height.
  const shortWindow = useMediaQuery("(max-height:819.95px)");
  const columnDefs = useMemo(
    () =>
      buildResultColumns({
        summary,
        baselineId,
        runsById,
        colors,
        showReasons,
        agg: url.agg,
        pills: url.pills,
        handlers: {
          onMovement,
          groupMenu,
          seeChanges: versionDoor.seeChanges,
          compareAnyway: versionDoor.compareAnyway,
          rescoreOn: versionDoor.rescoreOn,
        },
        why,
        taskSuite,
        layout: layout.columns,
        voice,
      }),
    [
      voice,
      summary,
      baselineId,
      runsById,
      colors,
      showReasons,
      url.agg,
      url.pills,
      onMovement,
      groupMenu,
      versionDoor.seeChanges,
      versionDoor.compareAnyway,
      versionDoor.rescoreOn,
      why,
      taskSuite,
      layout.columns,
    ],
  );

  // ---------------------------------------------------------- grid context
  const rerun = useRerunResultCells(datasetId, {
    onSuccess: () =>
      enqueueSnackbar("Re-running the cell", { variant: "success" }),
  });
  const drawerOpen = !!url.row;
  // iterate r3 G3 (P1): a row opened from a filtered list focuses the run
  // being triaged (the pill's run › the page's candidate when it got worse
  // here › the first run that did), so the drawer and the page agree.
  const pagePair = usePagePair({
    datasetId: multiRun ? datasetId : null,
    experimentId: mode === "experiment" ? experimentId : undefined,
    runIds,
    baselineId,
    search: locationSearch,
  });
  const pageCand = pagePair.candidateId;
  const openRow = useCallback(
    (row, runId, extra = {}) =>
      patch(
        {
          row: row.example_id,
          run:
            runId && runId !== baselineId
              ? runId
              : triageRunOf({
                  row,
                  pills: url.pills,
                  show,
                  baselineId,
                  runIds: runs.filter((r) => !r.not_found).map((r) => r.run_id),
                  pageCand:
                    new URLSearchParams(window.location.search).get("cand") ||
                    pageCand,
                }) ??
                firstCandidate ??
                runId,
          dtab: extra.dtab ?? null,
          trial: extra.trial && extra.trial > 1 ? extra.trial : null,
        },
        { push: !drawerOpen },
      ),
    [
      patch,
      drawerOpen,
      baselineId,
      firstCandidate,
      url.pills,
      show,
      runs,
      pageCand,
    ],
  );
  const rowHeight = ROW_HEIGHT[density] ?? ROW_HEIGHT.comfortable;
  const rowsVersion =
    runsById[baselineId]?.dataset_version?.number ??
    runs.find((r) => r.dataset_version?.number)?.dataset_version?.number ??
    null;
  // RC#43: the empty grid says why and what to do next.
  const baseLabelForEmpty = runsById[baselineId]?.label ?? "the baseline";
  const emptyCopy = search
    ? {
        title: `No rows match "${search}"`,
        body: "Search looks in inputs, outputs and reasons.",
        actions: [{ label: "Clear search", onClick: () => setSearch("") }],
      }
    : show !== "all"
      ? {
          title:
            show === "regressed"
              ? `No rows worse than ${baseLabelForEmpty}. Nice.`
              : `No ${show} rows vs ${baseLabelForEmpty}.`,
          body:
            show === "regressed"
              ? `No run got worse than ${baseLabelForEmpty} on any eval.`
              : show === "improved"
                ? `No run got better than ${baseLabelForEmpty} on any eval.`
                : `Every run scored the same as ${baseLabelForEmpty}.`,
          actions: [
            {
              label: "Show all rows",
              onClick: () => patch({ show: null }),
            },
            {
              label: "Change baseline",
              onClick: () => setRunsOpen(true),
            },
          ],
        }
      : url.pills.length
        ? {
            title: "No rows match these filters",
            actions: [{ label: "Clear filters", onClick: () => setPills([]) }],
          }
        : { title: "No rows yet", body: "Results appear here as runs finish." };

  // R3 MUST 4: every door into S's side-by-side workspace (row hover
  // action, trace chip menu, drawer `O`) with `from` = this view.
  const here = `${pathname}${locationSearch}`;
  const sideBySideHref = useCallback(
    (rowId, { runId, trial } = {}) => {
      const lanes = runIds.filter(
        (id) => !runsById[id]?.not_found || !runsById[id],
      );
      const withRun =
        runId && !lanes.includes(runId) ? [...lanes, runId] : lanes;
      return taskCompareHref({
        datasetId,
        runIds: withRun,
        baselineId,
        rowId,
        trials: runId && trial > 1 ? { [runId]: trial } : undefined,
        from: here,
      });
    },
    [runIds, runsById, datasetId, baselineId, here],
  );
  const openSideBySide = useCallback(
    (row, runId, trial) =>
      navigate(sideBySideHref(row?.example_id ?? row, { runId, trial })),
    [navigate, sideBySideHref],
  );
  const context = useMemo(
    () => ({
      runsById,
      defsByKey,
      colors,
      baselineId,
      diff: url.diff,
      showReasons,
      // L7c g6 (view:options): Raw JSON values reach every cell.
      valueFormat: format,
      rowHeight,
      readOnly,
      splitsByName: Object.fromEntries(
        (summary?.splits ?? []).map((s) => [s.name, s]),
      ),
      flakyRows: new Set(
        rows
          .filter((r) => Object.values(r.results ?? {}).some((x) => x?.flaky))
          .map((r) => r.example_id),
      ),
      onFilter: addPill,
      onViewTrace: (row, runId, trial) =>
        openRow(row, runId, { dtab: "trace", trial }),
      onRerunCell: (row, runId) =>
        rerun.mutate([{ run: runsById[runId], exampleId: row.example_id }]),
      onSideBySide: (row, runId, trial) => openSideBySide(row, runId, trial),
      onOpenRow: (row) => openRow(row),
      onWhyChip: (metricKey, runId) => {
        const api = gridRef.current?.api;
        const colId = `${metricKey}|${runId}`;
        if (!api?.getColumn?.(colId)) return;
        api.ensureColumnVisible(colId, "middle");
        // Flash after the scroll renders the column (AG Grid throws on a
        // virtualised cell).
        setTimeout(() => {
          try {
            if (!api.isDestroyed?.()) api.flashCells?.({ columns: [colId] });
          } catch {
            /* the column is still off screen: no flash */
          }
        }, 120);
      },
      density,
      primaryMetricKey: primaryMetric?.key ?? null,
      previewField: inputsPreviewChoice(rows).field, // W5 C-H6
      // The Input cell's names for a native row's column ids, and its
      // preview field chosen over the inputs as named (the drawer's Find
      // keeps `previewField`, the field the server rows carry).
      inputNames,
      inputPreviewField: inputsPreviewChoice(
        rows.map((r) => ({ inputs: displayInputs(r?.inputs, inputNames) })),
      ).field,
      taskSuite,
      search,
      show,
      empty: emptyCopy,
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [
      runsById,
      defsByKey,
      colors,
      baselineId,
      url.diff,
      showReasons,
      format,
      rowHeight,
      readOnly,
      summary,
      rows,
      addPill,
      openRow,
      openSideBySide,
      density,
      primaryMetric,
      search,
      show,
      emptyCopy,
      taskSuite,
      inputNames,
    ],
  );

  // ---------------------------------------------------------- filters model
  const filterView = useMemo(
    () => ({
      runs: runs.filter((r) => !r.not_found),
      runCount: runs.filter((r) => !r.not_found).length,
      baselineId,
      metrics,
      humanMetrics: summary?.human_metrics ?? [],
      splits: summary?.splits ?? [],
      metadataKeys: summary?.metadata_keys ?? [],
      metadataValues: summary?.metadata_values ?? {},
      hasTrials: runs.some((r) => r.trials > 1),
      hasPreference: Object.keys(summary?.preference ?? {}).length > 0,
      hasAgent: runs.some((r) => r.target_type === "agent"),
      candidateId: firstCandidate,
      quick: voiceFilters,
      // integration S9: the expression dialog counts with the page's own R3 view.
      datasetId,
      resultsView: view,
    }),
    [
      runs,
      baselineId,
      metrics,
      summary,
      firstCandidate,
      voiceFilters,
      datasetId,
      view,
    ],
  );
  const [editingExpr, setEditingExpr] = useState(null);

  const filtered = url.pills.length > 0 || show !== "all" || !!search;
  const hasExpected = rows.some(
    (r) => r.expected?.primary || r.expected?.value,
  );

  // P12: while the fix-run banner speaks, the Why strip waits (one
  // message about the failure at a time).
  const lastFix = useIterateStore((st) => st.lastFinished);
  const activeFix = useIterateStore((st) => st.active);
  const dismissedFix = useIterateStore((st) => st.dismissed);
  const fixBannerShown = Boolean(
    bannerPairOf({
      pathname,
      search: locationSearch,
      datasetId,
      runIds,
      baselineId,
      last: lastFix,
      dismissed: dismissedFix,
    }),
  );

  // P1: the re-test answer for the run in focus (the open row's run, else
  // the candidate, else the only run).
  const retestRunId =
    (url.run && runsById[url.run] ? url.run : null) ??
    firstCandidate ??
    runs.find((r) => !r.not_found)?.run_id ??
    null;
  const retestNow = useMemo(
    () => retestSummary(rows, { runId: retestRunId, metrics }),
    [rows, retestRunId, metrics],
  );
  // The answer is about every imported row: a filter (e.g. `See the 2
  // still failing`) keeps the last unfiltered answer.
  const retestAll = useRef(null);
  // Filters keep the all-row answer, but never across datasets, run focus,
  // reducers or authorization failures.
  // Keep the last resolved identity through undefined-data filter loading,
  // but populated data/configs can really move focus (e.g. a late parent link).
  const retestInputs = JSON.stringify([datasetId, runIds, baselineId, url.run ?? null, view.agg, view.mixVersions]);
  const retainedRunId = retestAll.current?.inputKey === retestInputs
    ? retestAll.current.runId : null;
  const resolvedRetestRunId = data === undefined
    ? retainedRunId ?? retestRunId : retestRunId;
  const retestScope = JSON.stringify([retestInputs, resolvedRetestRunId]);
  const filteredView = url.pills.length > 0 || show !== "all" || !!search;
  if (resultsBlocked || retestAll.current?.scopeKey !== retestScope)
    retestAll.current = null;
  if (!resultsBlocked && (!filteredView || !retestAll.current?.summary))
    retestAll.current = {
      inputKey: retestInputs, scopeKey: retestScope,
      runId: resolvedRetestRunId, summary: retestNow,
    };
  const retest = resultsBlocked ? null : filteredView ? retestAll.current?.summary : retestNow;

  // IT-R4 G3 (P3): after a scoped fix run finishes, the grid shows the rows
  // it aimed at (`Fixed rows (6) ×`), once per run.
  const finishedFixes = useIterateStore((st) => st.finished);
  const fixedRowsFor = lastFix?.runId
    ? finishedFixes?.[lastFix.runId]?.cause?.rows ?? null
    : null;
  useEffect(() => {
    const id = lastFix?.runId;
    if (!id || FIXED_FILTER_APPLIED.has(id) || !runIds.includes(id)) return;
    if (!fixedRowsFor?.length) return;
    FIXED_FILTER_APPLIED.add(id);
    setPills([
      ...url.pills.filter((p) => p?.type !== "rows"),
      rowsPill(fixedRowsFor, { label: "Fixed rows" }),
    ]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lastFix?.runId, fixedRowsFor, runIds.join(",")]);

  // ------------------------------------------------- verdict actions (R3)
  const showWorse = useCallback(
    (runId, rowIds, label) =>
      setPills([
        ...url.pills.filter((p) => p?.type !== "rows"),
        rowsPill(rowIds, { label }),
      ]),
    [url.pills, setPills],
  );
  // iterate r3 G3 (P2): one count per word. The verdict's lead line
  // (`worse 2 (Task success)`) feeds the toolbar's first chip
  // `Task success worse (2)` — the same rows, one click.
  const [leadSnapshot, setLeadSnapshot] = useState(null);
  const setLead = useCallback((value) => {
    if (!resultsBlocked) setLeadSnapshot({ scopeKey: resultsScope, value });
  }, [resultsScope, resultsBlocked]);
  useEffect(() => {
    setLeadSnapshot((current) =>
      resultsBlocked || current?.scopeKey !== resultsScope ? null : current,
    );
  }, [resultsScope, resultsBlocked]);
  const lead = !resultsBlocked && leadSnapshot?.scopeKey === resultsScope
    ? leadSnapshot.value : null;
  const rowNameOf = useCallback(
    (id) => {
      const r = rows.find((x) => x.example_id === id);
      return r ? rowLabelOf({ n: r.row_order }, datasetKind) : null;
    },
    [rows, datasetKind],
  );
  // IT-R5 G3 (P2): each run's rows worse on any eval (the verdict's `on
  // any eval 9`), from the same summary as the Worse pill.
  const worseAnyOf = useCallback(
    (id) => {
      const r = (summary?.runs ?? []).find((x) => x.run_id === id);
      return Number.isFinite(r?.worse_any) ? r.worse_any : undefined;
    },
    [summary],
  );
  // IT-R5 G3 (P3): the review queue is the filter in view; its last row
  // offers the next filter (`Review Better (13) next`).
  const marks = useReviewMarks((st) => st.marks);
  const openFirstOnLoad = useRef(false);
  useEffect(() => {
    const counts = summary?.show_counts ?? {};
    const label =
      show === "all"
        ? `All rows (${rowIds.length})`
        : `${showLabel(show)} (${counts[show] ?? rowIds.length})`;
    const nextKey =
      show === "regressed"
        ? "improved"
        : show === "improved"
          ? "changed"
          : null;
    useReviewMarks.getState().setQueue({
      label,
      rowIds,
      next:
        nextKey && counts[nextKey]
          ? {
              label: `${showLabel(nextKey)} (${counts[nextKey]})`,
              onGo: () => {
                openFirstOnLoad.current = true;
                patch({ show: nextKey, row: null });
              },
            }
          : null,
    });
  }, [show, rowIds, summary?.show_counts, patch]);
  useEffect(() => {
    if (!openFirstOnLoad.current || !rows.length || url.row) return;
    openFirstOnLoad.current = false;
    patch({ row: rows[0].example_id, dtab: "review" });
  }, [rows, url.row, patch]);
  // L7c g6 (status:population): only votes on the pair in view count.
  const reviewPair =
    runIds.length === 2
      ? pairOf(runIds[0], runIds[1])
      : pairOf(
          baselineId,
          new URLSearchParams(locationSearch).get("cand") ??
            runIds.find((id) => id !== baselineId),
        );
  const reviewed = multiRun ? reviewedText(marks, rowIds, reviewPair) : null;
  const leadNames = useRef({});
  const leadNamesScope = useRef(resultsScope);
  if (resultsBlocked || leadNamesScope.current !== resultsScope) {
    leadNames.current = {};
    leadNamesScope.current = resultsScope;
  }
  (lead?.worseRows ?? []).forEach((id) => {
    const n = rowNameOf(id);
    if (n) leadNames.current[id] = n;
  });
  const taskWorse = useMemo(() => {
    if (!multiRun || !lead || !primaryMetric?.name) return null;
    const base = runsById[baselineId]?.label ?? "the baseline";
    const label = `${lead.label} worse than ${base}`;
    const active = url.pills.some(
      (p) => p?.type === "rows" && p.label === label,
    );
    const names = lead.worseRows
      .map((id) => leadNames.current[id])
      .filter(Boolean);
    return {
      label: `${primaryMetric.name} worse`,
      count: lead.worse,
      active,
      tooltip: lead.worse
        ? `${lead.label} passes fewer tries than ${base} on ${primaryMetric.name}${names.length ? `: ${names.slice(0, 6).join(", ")}${names.length > 6 ? ` +${names.length - 6}` : ""}` : ""}. The verdict's number; click to show only these.`
        : `No ${datasetKind === "task_suite" ? "task" : "row"} got worse on ${primaryMetric.name} for ${lead.label}`,
      onClick: () =>
        patch({
          filters: active
            ? serializePills(url.pills.filter((p) => p?.type !== "rows")) ||
              null
            : serializePills([
                ...url.pills.filter((p) => p?.type !== "rows"),
                rowsPill(lead.worseRows, { label }),
              ]),
          show: null,
        }),
      anyTip: anyWorseTip({
        metricName: primaryMetric.name,
        worse: lead.worse,
        names,
        total: summary?.show_counts?.regressed,
        leadLabel: lead.label,
        oneCandidate:
          runs.filter((r) => r.run_id !== baselineId && !r.not_found).length ===
          1,
      }),
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    multiRun,
    lead,
    primaryMetric,
    runsById,
    baselineId,
    url.pills,
    patch,
    summary,
    runs,
    datasetKind,
    rows,
  ]);
  const flakySubject =
    (multiRun ? lead?.runId : runs.find((r) => !r.not_found)?.run_id) ?? null;
  // W1-6: the run whose one ledger the status line prints (the page's lead).
  const ledgerRun =
    (summary?.runs ?? []).find((r) => r.run_id === flakySubject) ?? null;
  // IT-R4 G3 (P1, rule 28): `10 rows both ran · candidate on dataset v6,
  // baseline on v5 · 248 of 286 tries scored`, from the verdict's own
  // numbers (one source), only when the pair is not on the same rows.
  // L7c g6 (live:status): `Pause updates` holds the footer's population
  // line too (the lead it reads is the one shown when the page paused).
  const pausedLead = useRef(null);
  if (resultsBlocked || pausedLead.current?.scopeKey !== resultsScope)
    pausedLead.current = null;
  useEffect(() => {
    pausedLead.current = paused ? { scopeKey: resultsScope, value: lead } : null;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [paused, resultsScope, resultsBlocked]);
  const shownLead = paused ? pausedLead.current?.value ?? lead : lead;
  const population = useMemo(
    () =>
      populationLine({
        lead: shownLead,
        cand: runsById[shownLead?.runId],
        base: runsById[baselineId],
        noun: datasetKind === "task_suite" ? "task" : "row",
      }),
    [shownLead, runsById, baselineId, datasetKind],
  );
  const whyFirstWorse = useCallback(
    (runId, rowIds) => {
      if (rowIds?.[0]) openSideBySide(rowIds[0], runId);
    },
    [openSideBySide],
  );

  // ------------------------------------------------- palette (R3 MUST 11)
  const candidates = runs.filter(
    (r) => r.run_id !== baselineId && !r.not_found,
  );
  const baseLabel = runsById[baselineId]?.label ?? "the baseline";
  const nextDiff = url.diff === "expected" ? "baseline" : "expected";
  useRegisterCommands(
    "results-view",
    [
      multiRun && {
        id: "results.regressed",
        label: "Show rows worse than the baseline",
        keywords: ["regressed", "worse", "filter"],
        icon: "mdi:trending-down",
        run: () => patch({ show: "regressed" }),
      },
      {
        id: "results.diff",
        label:
          url.diff === "off"
            ? multiRun
              ? "Show diff vs baseline"
              : "Show diff vs expected"
            : `Switch diff vs ${nextDiff === "expected" ? "expected" : "baseline"}`,
        keywords: ["diff", "expected", "reference", "compare"],
        icon: "mdi:vector-difference",
        run: () =>
          patch({
            diff:
              url.diff === "off"
                ? multiRun
                  ? "baseline"
                  : "expected"
                : nextDiff === "baseline" && !multiRun
                  ? null
                  : nextDiff,
          }),
      },
      ...candidates.flatMap((r) => [
        {
          id: `results.baseline.${r.run_id}`,
          label: `Make ${r.label} baseline`,
          keywords: ["baseline", r.label],
          icon: "mdi:flag-outline",
          run: () => onBaselineChange(r.run_id),
        },
        {
          id: `results.pair.${r.run_id}`,
          label: `Compare ${r.label} vs ${baseLabel}`,
          keywords: ["side by side", r.label],
          icon: "mdi:compare-horizontal",
          run: () =>
            navigate(
              taskCompareHref({
                datasetId,
                runIds: [baselineId, r.run_id].filter(Boolean),
                baselineId,
                from: here,
              }),
            ),
        },
      ]),
      // Header numbers (was Display ▾ › Aggregate; also each eval
      // column's ⋮ menu).
      ...[
        ["avg", "Average"],
        ["median", "Median"],
        // L7c g6 (agg:basic, HIDE): `Pass rate` read a second pass rate
        // (ties counted as passes, CI in score units) that disagreed with
        // the leaderboard on multi-try runs; Average is the pass rate of a
        // pass/fail eval.
        // W1-8: the trial reducers when a run plays k > 1 tries per row.
        ...trialAggItems(
          { kind: "eval" },
          (summary?.metrics ?? []).reduce(
            (a, m) => ((m?.trials_k ?? 1) > (a?.trials_k ?? 1) ? m : a),
            null,
          ),
        ).map((a) => [a.value, a.label]),
      ].map(([agg, label]) => ({
        id: `results.agg.${agg}`,
        label: `Header numbers: ${label}`,
        keywords: ["aggregate", "average", "median", "pass rate", "header"],
        icon: "mdi:sigma",
        when: () => url.agg !== agg,
        run: () => patch({ agg: agg === "run" ? null : agg }),
      })),
      {
        id: "results.clearFilters",
        label: "Clear filters and search",
        keywords: ["reset", "all rows"],
        icon: "mdi:filter-remove-outline",
        when: () => url.pills.length > 0 || show !== "all" || !!search,
        run: () => {
          setSearch("");
          patch({ show: null, filters: null });
        },
      },
      url.row && {
        id: "results.sideBySideRow",
        label: "Open side by side for this row",
        shortcut: "openSideBySide",
        icon: "mdi:compare-horizontal",
        run: () => openSideBySide(url.row, url.run, url.trial),
      },
    ].filter(Boolean),
    [
      multiRun,
      url.diff,
      url.row,
      url.run,
      candidates.length,
      baselineId,
      here,
      mode,
      readOnly,
      url.pills.length,
      url.agg,
      show,
      search,
      summary?.metrics,
    ],
  );

  // ------------------------------------------------- keys (one view)
  const toggleDiff = useCallback(() => {
    const target = multiRun ? "baseline" : "expected";
    if (target === "expected" && !hasExpected) return;
    patch({ diff: url.diff === "off" ? target : null });
  }, [multiRun, hasExpected, url.diff, patch]);
  // 8f, D25: keyboard-first grid. J / K move the row cursor
  // (no click needed: useRowCursor focuses row 0), Enter opens the row, X
  // selects it, A adds the selected rows to a dataset, C copies the link.
  // `C` is Copy link here, so Columns stays in the View menu (F's note).
  const [addRequest, setAddRequest] = useState(0);
  const clearAddRequest = useCallback(() => setAddRequest(0), []);
  // P7: the cursor is a row id, so a refresh that re-orders or adds rows
  // never moves it to another row.
  const cursorId = useRef(null);
  // IT-R5 G3 (P3): keys typed before the grid has rows are queued, never
  // dropped (they run in order once the rows land).
  const pendingMoves = useRef([]);
  const moveCursor = useCallback((step) => {
    const api = gridRef.current?.api;
    const count = api?.getDisplayedRowCount?.() ?? 0;
    if (!api || !count) {
      pendingMoves.current.push(step);
      return;
    }
    const cur = api.getFocusedCell?.();
    const column = cur?.column ?? api.getAllDisplayedColumns?.()?.[0];
    const anchored =
      cursorId.current && api.getRowNode?.(cursorId.current)?.rowIndex;
    const from = Number.isFinite(anchored) ? anchored : cur?.rowIndex;
    const next = Math.max(
      0,
      Math.min(
        count - 1,
        !Number.isFinite(step)
          ? step > 0
            ? count - 1
            : 0
          : Number.isFinite(from)
            ? from + step
            : 0,
      ),
    );
    api.ensureIndexVisible?.(next);
    api.setFocusedCell?.(next, column);
    cursorId.current = api.getDisplayedRowAtIndex?.(next)?.id ?? null;
  }, []);
  // IT-R5 G3 (P3): the grid takes focus on load (J/K/X/Enter work without
  // a click), then any queued keys run in order.
  const focusedOnLoad = useRef(false);
  useEffect(() => {
    if (!rows.length) return undefined;
    const t = setTimeout(() => {
      const api = gridRef.current?.api;
      if (!api || api.isDestroyed?.()) return;
      const count = api.getDisplayedRowCount?.() ?? 0;
      if (!count) return;
      if (!focusedOnLoad.current) {
        focusedOnLoad.current = true;
        const active = document.activeElement;
        const idle =
          !active || active === document.body || active.id === "root";
        if (idle && !url.row && !api.getFocusedCell?.()) {
          const column = api.getAllDisplayedColumns?.()?.[0];
          if (column) {
            api.setFocusedCell?.(0, column);
            cursorId.current = api.getDisplayedRowAtIndex?.(0)?.id ?? null;
          }
        }
      }
      const queued = pendingMoves.current.splice(0);
      queued.forEach((step) => moveCursor(step));
    }, 60);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rows, moveCursor]);
  // P3: the footer's key hint stays lit until the first key.
  const [keysUsed, setKeysUsed] = useState(false);
  useEffect(() => {
    if (keysUsed) return undefined;
    const onKey = (e) => {
      if (!e.metaKey && !e.ctrlKey && /^[jkxJKX]$|^Arrow|^Enter$/.test(e.key))
        setKeysUsed(true);
    };
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  }, [keysUsed]);
  useEffect(() => {
    const api = gridRef.current?.api;
    const id = cursorId.current;
    if (!api || !id || api.isDestroyed?.()) return;
    const node = api.getRowNode?.(id);
    const cur = api.getFocusedCell?.();
    if (
      node &&
      cur &&
      Number.isFinite(node.rowIndex) &&
      node.rowIndex !== cur.rowIndex
    )
      api.setFocusedCell?.(node.rowIndex, cur.column);
  }, [rows]);
  const focusedNode = useCallback(() => {
    const api = gridRef.current?.api;
    const cur = api?.getFocusedCell?.();
    return cur ? api.getDisplayedRowAtIndex(cur.rowIndex) : null;
  }, []);
  const includeRowPref = viewPrefs.extras?.shareIncludeRow !== false;
  const copyShareLink = useCallback(
    async (includeRow = includeRowPref) => {
      const href = shareLinkOf(window.location.href, { includeRow });
      const ok = await copyText(href);
      const openRowData = url.row
        ? rows.find((r) => r.example_id === url.row)
        : null;
      const summaryText = shareLinkSummary({
        runCount: runIds.length,
        baselineLabel: multiRun ? runsById[baselineId]?.label : null,
        tab: "data",
        show,
        filters: url.pills.length,
        search,
        rowLabel:
          includeRow && openRowData
            ? rowLabelOf({ n: openRowData.row_order }, datasetKind)
            : null,
      });
      enqueueSnackbar(
        ok
          ? `Link copied: ${summaryText}`
          : "Couldn't copy the link. Copy it from the address bar.",
        {
          key: "results-copy-link",
          variant: ok ? "success" : "error",
          autoHideDuration: 6000,
          action: url.row
            ? () => (
                <FormControlLabel
                  sx={{
                    mr: 0.5,
                    "& .MuiFormControlLabel-label": { typography: "s2" },
                  }}
                  control={
                    <Checkbox
                      size="small"
                      color="default"
                      defaultChecked={includeRow}
                      sx={{ color: "inherit", p: 0.5 }}
                      onChange={(e) => {
                        patchView({
                          extras: { shareIncludeRow: e.target.checked },
                        });
                        copyShareLink(e.target.checked);
                      }}
                    />
                  }
                  label="Include the open row"
                />
              )
            : undefined,
        },
      );
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [
      includeRowPref,
      url.row,
      url.pills,
      rows,
      runIds,
      multiRun,
      show,
      datasetKind,
      search,
    ],
  );
  // iterate r1 G3 (P5): with the row panel open, J / K move the open row
  // (the panel's own keys, in the filtered order) and the grid follows it;
  // closed, they move the grid's row cursor.
  const ROW_KEYS_REASON = "The open row panel moves the rows";
  useShortcuts("results", {
    density: () => setDensity(nextDensity(density)),
    diffToggle: toggleDiff,
    next: {
      run: () => moveCursor(1),
      when: () => !drawerOpen,
      reason: ROW_KEYS_REASON,
    },
    prev: {
      run: () => moveCursor(-1),
      when: () => !drawerOpen,
      reason: ROW_KEYS_REASON,
    },
    // iterate r3 G3 (P4): Home / End / Page Up / Page Down.
    first: {
      run: () => moveCursor(-Infinity),
      when: () => !drawerOpen,
      reason: ROW_KEYS_REASON,
    },
    last: {
      run: () => moveCursor(Infinity),
      when: () => !drawerOpen,
      reason: ROW_KEYS_REASON,
    },
    pageDown: {
      run: () => moveCursor(10),
      when: () => !drawerOpen,
      reason: ROW_KEYS_REASON,
    },
    pageUp: {
      run: () => moveCursor(-10),
      when: () => !drawerOpen,
      reason: ROW_KEYS_REASON,
    },
    open: () => {
      const node = focusedNode();
      if (!node?.data) return;
      const col = gridRef.current?.api?.getFocusedCell?.()?.column;
      const runId = String(col?.getColId?.() ?? "").split("|")[1] ?? null;
      openRow(node.data, runId && runsById[runId] ? runId : undefined);
    },
    // `X` toggles the row under the visible cursor: the open row while
    // the panel is open, else the grid's cursor row.
    select: () => {
      const node = drawerOpen
        ? gridRef.current?.api?.getRowNode?.(url.row)
        : focusedNode();
      if (node) node.setSelected(!node.isSelected());
    },
    // IT-R5 G3 (P3): Shift+X selects every row the filter shows.
    selectAll: () => {
      const api = gridRef.current?.api;
      if (!api) return;
      api.selectAll?.("filtered");
      notify(
        `Selected ${fmtCount(rows.length, rowNoun)} · A adds them to a dataset`,
        {
          key: "results-select-all",
        },
      );
    },
    // P11: `A` right after `X` opens the dialog even before the selection
    // state lands (the grid's own selection is read at once; the request
    // waits for the selection bar). IT-R5 G3 (P3): with nothing selected,
    // `A` acts on the cursor row (it is selected first).
    addToDataset: {
      run: () => {
        const api = gridRef.current?.api;
        if (!selected.length && !(api?.getSelectedRows?.()?.length ?? 0)) {
          const node = drawerOpen ? api?.getRowNode?.(url.row) : focusedNode();
          if (!node) return;
          node.setSelected(true);
        }
        setAddRequest((n) => n + 1);
      },
      when: () => !readOnly,
      reason: "Editors can add rows to a dataset",
    },
    copyLink: () => copyShareLink(),
    // IT-R4 G3 (P6): R = review the cursor row (Review tab, votes focused).
    reviewTab: {
      run: () => {
        const node = drawerOpen
          ? gridRef.current?.api?.getRowNode?.(url.row)
          : focusedNode();
        if (!node?.data) return;
        openRow(node.data, undefined, { dtab: "review" });
        setTimeout(() => {
          const vote =
            document.querySelector("[data-review-vote]:not([disabled])") ??
            document.querySelector('[data-testid="review-skipped"]');
          vote?.focus?.();
        }, 600);
      },
      when: () => multiRun,
      reason: "Review compares two runs",
    },
  });

  // IT-R4 G3 (P6): reviewing holds the rows still — opening the Review tab
  // pauses live re-sort and re-count (the chip says what landed since).
  useEffect(() => {
    if (url.dtab === "review" && live && !paused && liveData)
      setPaused(liveData);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [url.dtab, Boolean(live)]);

  // ------------------------------------------------- View menu (A3-10)
  const viewState = useMemo(() => {
    const sp = new URLSearchParams(locationSearch);
    return Object.fromEntries(
      VIEW_KEYS.map((k) => [k, sp.get(k)]).filter(([, v]) => v != null),
    );
  }, [locationSearch]);
  const viewMenu = (
    <ViewMenu
      surface="results"
      density={{ value: density, onChange: setDensity }}
      columns={layout}
      // P8: `Row fields` / `Per run` headings in the Columns panel (the
      // panel reads them once ViewMenu forwards `groups`, G5).
      groups={RESULTS_COLUMN_GROUPS}
      extras={[
        hideEmptyViewExtra(url.pills, setPills),
        ...(why
          ? [
              {
                id: "why",
                label: "Why column",
                checked: layout.visibleIds.includes("why"),
                onChange: (v) => (v ? layout.show("why") : layout.hide("why")),
                description: "Why each row is in this Show filter",
              },
            ]
          : []),
        {
          id: "reasons",
          label: "Eval reasons in cells",
          checked: Boolean(showReasons),
          onChange: (v) => setShowReasons(Boolean(v)),
        },
        {
          id: "raw",
          label: "Raw JSON values",
          checked: format === "raw",
          onChange: (v) => setFormat(v ? "raw" : "formatted"),
        },
      ]}
      savedViews={{
        state: viewState,
        onApply: (v) =>
          patch(Object.fromEntries(VIEW_KEYS.map((k) => [k, v?.[k] ?? null]))),
      }}
      isModified={Object.keys(viewState).length > 0 || !!search}
      onReset={() => {
        setSearch("");
        patch(Object.fromEntries(VIEW_KEYS.map((k) => [k, null])));
      }}
    />
  );

  // All hooks above still run to invalidate retained inputs and shared counts.
  // Unmount independent comparison/row caches too, not just the results grid.
  // A failed read keeps `Hide rows with no output` (when it is on) under the
  // error, so it can still be turned off; it reads nothing itself.
  const withHideEmptyOff = (blocked) =>
    hidesEmpty(url.pills) ? (
      <Stack gap={1} alignItems="center">
        {blocked}
        <Box data-testid="results-hide-empty-blocked">
          <HideEmptyToggle
            on
            onToggle={(on) => setPills(withHideEmpty(url.pills, on))}
          />
        </Box>
      </Stack>
    ) : (
      blocked
    );
  // Runs whose dataset version, trial plan or evaluators differ: the read
  // refuses to line them up; say so, keep Retry, and link the first run's
  // experiment when it is not the page already open.
  if (resultsBlocked && isMixedRunsRefusal(resultsError)) {
    const first = (runCfg?.results ?? []).find((r) => r.run_id === runIds[0]);
    const elsewhere =
      first?.experiment_id &&
      !(mode === "experiment" && first.experiment_id === experimentId);
    return withHideEmptyOff(
      <MixedRunsNotice
        datasetId={datasetId}
        experimentId={elsewhere ? first.experiment_id : undefined}
        experimentName={elsewhere ? first?.experiment_name : undefined}
        onRetry={() => refetch()}
        sx={{
          border: "1px solid",
          borderColor: "divider",
          borderRadius: 0.5,
        }}
      />,
    );
  }
  if (resultsBlocked)
    return withHideEmptyOff(
      <ErrorState
        compact
        thing="these rows"
        onRetry={() => refetch()}
        sx={{
          border: "1px solid",
          borderColor: "divider",
          borderRadius: 0.5,
        }}
      />,
    );

  const runBar = (
    <RunBar
      datasetId={datasetId}
      mode={mode}
      runs={runs}
      colors={colors}
      baselineId={baselineId}
      pinnedRunId={summary?.dataset_baseline?.run_id}
      restrictTo={mode === "experiment" ? runIds : undefined}
      metrics={metrics}
      readOnly={readOnly}
      onBaselineChange={onBaselineChange}
      onRemoveRun={onRemoveRun}
      onAddRuns={onAddRuns}
    />
  );

  // IT-R5 G3 (P1): the run chips never take a row at any width; they sit
  // behind one `5 runs · baseline #2B ▾` chip (its popover is the run bar).
  const runSection = multiRun ? (
    <RunsChip
      short={narrowToolbar}
      runs={runs}
      colors={colors}
      baselineId={baselineId}
      open={runsOpen}
      onOpenChange={setRunsOpen}
    >
      {runBar}
    </RunsChip>
  ) : null;

  return (
    <Stack
      sx={{
        flex: 1,
        minHeight: 0,
        px: "12px",
        gap: shortWindow ? 0 : 0.5,
        // Header rows keep their height; only the grid gives way.
        "& > :not([data-results-grid])": { flexShrink: 0 },
      }}
    >
      {/* IT-R5 G3 (P1): the verdict strip is its own full-width row —
          the sentence (≤ 2 lines, wraps, never an ellipsis), one inline
          action, `+4`, `Why ▾`; the live counter and the `5 runs` chip end
          the row. */}
      <Stack
        direction="row"
        alignItems="center"
        gap={1.5}
        sx={{ minWidth: 0, minHeight: multiRun ? 24 : 0 }}
        data-testid="results-head"
      >
        {runsHeld && runIds.length ? (
          <Typography
            role="status"
            typography="s3"
            color="text.secondary"
            noWrap
            data-testid="results-runs-not-in-release"
          >
            {`Run history: ${NOT_IN_RELEASE}`}
          </Typography>
        ) : null}
        <Box sx={{ flex: 1, minWidth: 0 }}>
          {multiRun && baselineId && (
            <ResultsVerdict
              key={resultsScope}
              datasetId={datasetId}
              runIds={runIds}
              baselineId={baselineId}
              mode={mode}
              experimentId={experimentId}
              frozen={Boolean(paused)}
              onWorse={showWorse}
              onWhy={whyFirstWorse}
              primaryName={primaryMetric?.name}
              onLead={setLead}
              worseAnyOf={worseAnyOf}
              after={
                !retest && !fixBannerShown ? (
                  <SeeWhyButton
                    canWhy={Boolean(lead?.worseRows?.length)}
                    onWhy={() =>
                      lead && whyFirstWorse(lead.runId, lead.worseRows)
                    }
                    worse={lead?.worse ?? 0}
                    onShowWorse={() =>
                      lead &&
                      showWorse(
                        lead.runId,
                        lead.worseRows,
                        `${lead.label} worse than ${runsById[baselineId]?.label ?? "the baseline"}`,
                      )
                    }
                    noun={rowNoun}
                    datasetId={datasetId}
                    runIds={runIds}
                    baselineId={baselineId}
                    focusRunId={url.run ?? null}
                    candidateId={new URLSearchParams(locationSearch).get(
                      "cand",
                    )}
                    labelOf={(id) => runsById[id]?.label ?? "this run"}
                    winning={lead}
                    nameOf={(id) => leadNames.current[id] ?? rowNameOf(id)}
                  />
                ) : null
              }
            />
          )}
        </Box>
      </Stack>
      {!multiRun ? runBar : null}
      <RetestStrip
        summary={retest}
        runLabel={multiRun ? runsById[retestRunId]?.label : null}
        onShowStill={(ids) =>
          setPills([
            ...url.pills.filter((p) => p?.type !== "rows"),
            rowsPill(ids, { label: "Still failing after the re-test" }),
          ])
        }
      />

      <Stack
        direction="row"
        alignItems="center"
        gap={1}
        sx={{ flexWrap: "nowrap", minWidth: 0 }}
        data-testid="results-toolbar"
      >
        <ResultsToolbar
          search={search}
          onSearch={setSearch}
          show={show}
          showCounts={summary?.show_counts}
          onShow={(v) => patch({ show: v === "all" ? null : v })}
          multiRun={multiRun}
          filterView={filterView}
          onAddPill={addPill}
          sort={sort}
          onSort={(next) => patch({ sort: next === defaultSort ? null : next })}
          sortMetrics={metrics}
          runs={filterView.runs}
          baselineId={baselineId}
          hasTrials={filterView.hasTrials}
          total={data?.total}
          compact={narrowToolbar}
          defaultSort={defaultSort}
          taskWorse={taskWorse}
          searchOnly={drawerOpen}
        />
        {/* IT-R5 G3 (P1): the `5 runs` chip and the live counter sit in
            the toolbar row, so the verdict strip keeps the full width. */}
        {/* P5/P7: the runs still scoring say so (`12/30 · about 40 s
            left ⏸`), read from the same summary as the run chips (S3). */}
        <LiveStatus
          live={live}
          paused={Boolean(paused)}
          newCount={newCount}
          onPause={() => liveData && setPaused(liveData)}
          onUpdate={() => liveData && setPaused(liveData)}
          onResume={() => setPaused(null)}
          compact={narrowToolbar}
          fixText={
            activeFix && runIds.includes(activeFix.runId)
              ? fixCounterText(activeFix, { short: narrowToolbar })
              : undefined
          }
        />
        {drawerOpen ? null : runSection}
        {selected.length > 0 ? (
          <Box
            sx={{
              flex: 1,
              minWidth: 0,
              display: "flex",
              justifyContent: "flex-end",
            }}
          >
            <SelectionBar
              addRequest={addRequest}
              onAddHandled={clearAddRequest}
              datasetId={datasetId}
              selectedRows={selected}
              view={view}
              total={data?.total}
              loadedCount={rows.length}
              runs={filterView.runs}
              baselineId={baselineId}
              pills={url.pills}
              summary={summary}
              readOnly={readOnly}
              // IT-R4 G3 (P5): `Outputs from` defaults to the page's run.
              focusedRunId={url.run ?? pageCand ?? lead?.runId ?? undefined}
              onClear={() => gridRef.current?.api?.deselectAll()}
              // W4 §9 (M6): Comment… opens the row's answer on Review.
              onComment={(row, runId) =>
                openRow(row, runId, { dtab: "review" })
              }
            />
          </Box>
        ) : (
          <ExperimentBarDataRightSection
            mode={mode}
            datasetId={datasetId}
            experimentId={experimentId}
            runIds={runIds}
            baselineId={baselineId}
            pills={url.pills}
            diff={url.diff}
            onDiffChange={(d) => patch({ diff: d === "off" ? null : d })}
            hasExpected={hasExpected}
            readOnly={readOnly}
            onAddEvaluations={onAddEvaluations}
            onExperimentEdited={onExperimentEdited}
            viewMenu={viewMenu}
            // L7c g6 (grid:download): the grid's own view (search, sort,
            // filters, show), so Download… offers `This view`.
            view={exportView}
          />
        )}
      </Stack>

      {url.diff !== "off" && (
        <Stack
          direction="row"
          alignItems="center"
          gap={1}
          sx={{ minHeight: 20 }}
          data-testid="results-diff-legend"
        >
          <Typography typography="s2" color="text.secondary">
            {url.diff === "expected"
              ? "Diff vs expected:"
              : `Diff vs baseline ${runsById[baselineId]?.label ?? ""}:`}
          </Typography>
          <TextDiffLegend />
        </Stack>
      )}
      <FilterPills
        pills={url.pills}
        runs={runs}
        metrics={metrics}
        baselineId={baselineId}
        total={data?.total}
        examplesTotal={summary?.examples_total}
        onRemove={(pill) =>
          setPills(url.pills.filter((p) => !samePill(p, pill)))
        }
        onClear={() => setPills([])}
        onEdit={setEditingExpr}
      />
      <ResultsExpressionDialog
        open={!!editingExpr}
        editing={editingExpr}
        datasetId={datasetId}
        view={filterView}
        onClose={() => setEditingExpr(null)}
        onApply={(pill) => {
          setPills(replacePill(url.pills, editingExpr, pill, samePill));
          setEditingExpr(null);
        }}
      />

      <Box
        data-results-grid=""
        sx={{
          flex: 1,
          // P5: never taller than the window leaves (no page scroll at 720).
          minHeight: "min(420px, calc(100vh - 320px))",
          display: "flex",
          flexDirection: "column",
          border: "1px solid",
          borderColor: "divider",
          borderRadius: 0.5,
          overflow: "hidden",
          mb: shortWindow ? 0 : 0.5,
        }}
      >
        <ResultDrawer
          datasetId={datasetId}
          view={view}
          // W5 C4 (after integration): Find › "Filter rows with this" adds an
          // expression pill over the output and the previewed input.
          findPrimaryInput={context.previewField}
          onFilterRows={(expr) => addPill(expressionPill(expr))}
          runs={filterView.runs}
          runsById={runsById}
          colors={colors}
          baselineId={baselineId}
          summary={summary}
          readOnly={readOnly}
          url={url}
          mode={mode}
          rowIds={rowIds}
          grid={
            <Stack sx={{ flex: 1, minWidth: 0, height: "100%", minHeight: 0 }}>
              {isLoading && !data ? (
                <Stack
                  gap={0.75}
                  sx={{ p: 1, flex: 1, overflow: "hidden" }}
                  data-testid="results-loading"
                  aria-busy
                >
                  {/* RC#44: rows sized like the grid, never the rocket. */}
                  {Array.from({ length: 8 }, (_, i) => (
                    <Stack key={i} direction="row" gap={0.75}>
                      <Skeleton
                        variant="rounded"
                        width={56}
                        height={rowHeight - 8}
                      />
                      <Skeleton
                        variant="rounded"
                        width={260}
                        height={rowHeight - 8}
                      />
                      <Skeleton
                        variant="rounded"
                        width={260}
                        height={rowHeight - 8}
                      />
                      <Skeleton
                        variant="rounded"
                        sx={{ flex: 1 }}
                        height={rowHeight - 8}
                      />
                    </Stack>
                  ))}
                </Stack>
              ) : (
                <ResultsGrid
                  ref={gridRef}
                  rows={rows}
                  columnDefs={columnDefs}
                  context={context}
                  rowHeight={rowHeight}
                  activeRowId={url.row}
                  loading={isLoading || (isFetching && !rows.length)}
                  onOpenRow={openRow}
                  onCursorRow={(r) => patch({ row: r.example_id, trial: null })}
                  onSelectionChanged={setSelected}
                  layout={layout}
                />
              )}
              <Stack
                direction="row"
                alignItems="center"
                sx={{
                  borderTop: "1px solid",
                  borderColor: "divider",
                  minWidth: 0,
                  "& > :first-of-type": { minWidth: 0 },
                }}
              >
                <ResultsStatusLine
                  total={data?.total}
                  examplesTotal={summary?.examples_total}
                  errors={summary?.counts?.errors}
                  // W1-8: ONE flaky count, the Matrix's: rows
                  // whose tries disagree on any run in view (R3
                  // counts.flaky), named as such when several runs are in
                  // view; the dock names its own run (`on #3E`).
                  flaky={summary?.counts?.flaky}
                  flakyScope={multiRun ? "any run" : null}
                  // W1-6: the lead run's one ledger, each
                  // number opening its rows; named when several runs are in
                  // view. It replaces the line's own error counts.
                  ledgerParts={runLedgerParts(ledgerRun, {
                    noun: datasetKind === "task_suite" ? "task" : "row",
                  })}
                  ledgerScope={multiRun ? ledgerRun?.label ?? null : null}
                  onLedgerPart={(part) => {
                    if (!ledgerRun) return;
                    if (part.key === "errored")
                      setPills([
                        ...url.pills.filter(
                          (pl) => pl?.type !== "error" && pl?.type !== "rows",
                        ),
                        {
                          type: "error",
                          run: multiRun ? ledgerRun.run_id : ANY_RUN,
                          ...(part.cause ? { cause: part.cause } : {}),
                        },
                      ]);
                    else if (part.ids?.length)
                      setPills([
                        ...url.pills.filter((pl) => pl?.type !== "rows"),
                        rowsPill(part.ids, {
                          label: multiRun
                            ? `${ledgerRun.label} · ${part.label}`
                            : part.label,
                        }),
                      ]);
                  }}
                  filtered={filtered}
                  selected={selected.length}
                  onFilterErrors={() =>
                    addPill({ type: "error", run: ANY_RUN })
                  }
                  errorCauses={summary?.counts?.errors_by_cause ?? []}
                  errorPolicy={
                    erroredRuns.length &&
                    erroredRuns.every((r) => r.error_policy === "exclude")
                      ? "exclude"
                      : "zero"
                  }
                  // L7c g6 (status:ledger): the errored tries of every run
                  // in view, one count per policy, each run in the tooltip.
                  erroredTries={erroredCounts.zero}
                  erroredTriesLeftOut={erroredCounts.leftOut}
                  erroredTriesTitle={erroredCounts.title}
                  // L7c g6 (status:error-policy, HIDE): the footer's
                  // `count as 0 · leave out` re-score left the page on the
                  // old runs and mis-counted mixed views; it is not offered
                  // until it follows the new runs (owed to g5's rescore).
                  onErrorPolicy={undefined}
                  onFilterCause={(cause) =>
                    setPills([
                      ...url.pills.filter((pl) => pl?.type !== "error"),
                      { type: "error", run: ANY_RUN, cause },
                    ])
                  }
                  onFilterFlaky={() => addPill({ type: "flaky", run: ANY_RUN })}
                />
                {/* P8: which dataset version these rows are. IT-R4 G3 (P1,
                    rule 28): when the pair in view is not the same rows,
                    one population line says which rows the numbers are on. */}
                {population || rowsVersion ? (
                  <Typography
                    typography="s2"
                    color="text.secondary"
                    noWrap
                    title={population ?? undefined}
                    sx={{
                      flexShrink: 1,
                      minWidth: 0,
                      ml: -0.5,
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                    }}
                    data-testid="results-rows-version"
                  >
                    {population
                      ? `· ${population}`
                      : `· dataset v${rowsVersion}`}
                  </Typography>
                ) : null}
                {reviewed ? (
                  <Typography
                    typography="s2"
                    color="primary.main"
                    noWrap
                    data-testid="results-reviewed"
                    sx={{ flexShrink: 0, ml: 1, fontWeight: 500 }}
                  >
                    {`· ${reviewed}`}
                  </Typography>
                ) : null}
                {/* iterate r3 G3 (P4): the keys, where the eye ends.
                    IT-R5 G3 (P3): lit until the first key, so the grid's
                    keyboard focus on load is visible. */}
                <Typography
                  typography="s3"
                  color={keysUsed ? "text.disabled" : "primary.main"}
                  noWrap
                  data-testid="results-key-hints"
                  data-lit={keysUsed ? "false" : "true"}
                  sx={{
                    ml: "auto",
                    mr: 1,
                    px: keysUsed ? 0 : 0.75,
                    borderRadius: 0.5,
                    bgcolor: keysUsed
                      ? "transparent"
                      : (t) => `${t.palette.primary.main}14`,
                    transition: "background-color .3s, color .3s",
                    flexShrink: 1,
                    minWidth: 0,
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                  }}
                >
                  {drawerOpen
                    ? "J/K or ↑/↓ next row · Esc close · ? keys"
                    : "J/K or ↑/↓ move · Enter open · X select · ? keys"}
                </Typography>
              </Stack>
            </Stack>
          }
        />
      </Box>

      {rescore.dialog}
      {versionDoor.dialog}
      <EvalDetailsDialog
        def={evalDetails}
        runsById={runsById}
        colors={colors}
        onClose={() => setEvalDetails(null)}
      />
      {!summary && !isLoading && !isError && (
        <Typography typography="s2" color="text.secondary">
          No results yet
        </Typography>
      )}
    </Stack>
  );
};

RunResultsView.propTypes = {
  datasetId: PropTypes.string.isRequired,
  runIds: PropTypes.arrayOf(PropTypes.string).isRequired,
  baselineId: PropTypes.string,
  mode: PropTypes.oneOf(["experiment", "compare"]).isRequired,
  experimentId: PropTypes.string,
  initialRuns: PropTypes.array,
  onBaselineChange: PropTypes.func.isRequired,
  onRemoveRun: PropTypes.func,
  onAddRuns: PropTypes.func,
  onAddEvaluations: PropTypes.func,
  onExperimentEdited: PropTypes.func,
};

export default RunResultsView;
