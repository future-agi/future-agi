import React, {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
} from "react";
import PropTypes from "prop-types";
import { useLocation, useNavigate } from "react-router";
import { useAuthContext } from "src/auth/hooks";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";
import { leaderboardGates } from "./leaderboardAccess";
import useDatasetAccess from "src/sections/common/Access/useDatasetAccess";
import { LeaderboardShell } from "./LeaderboardStates";
import ExperimentMetricStrip from "./ExperimentMetricStrip";
import LeaderboardGrid from "./LeaderboardGrid";
// HO results R9: the host of a row's `Compare with… · A run on another dataset…`.
import MatchDoorHost from "src/sections/common/Leaderboards/MatchDoorHost";
import UploadResultsPopover from "./UploadResultsPopover";
import { Box, Button, Typography } from "@mui/material";
import { useLeaderboardActions } from "./useLeaderboardActions";
import { LEADERBOARD_URL_KEYS, useLeaderboardView } from "./useLeaderboard";
import {
  CHARTS_SECTION,
  CHART_CARDS,
  LB_SURFACE,
  useLeaderboardPrefs,
  useLegacyPrefsMigration,
} from "./leaderboardPrefs";
import { notify } from "src/sections/common/ux/toast";
import { useViewUrlState } from "src/sections/common/ux/viewState";
import { Section, useColumnLayout } from "src/sections/common/ux/oneview";
import { ColumnsMenuHost } from "src/sections/common/ux/oneview/ColumnsMenu";
import { useLayoutPrefs } from "src/sections/common/ux/oneview/layoutPrefs";
import { columnCatalog } from "./leaderboardColumns";
// HO results R7 (W5 B-H2): run metrics as leaderboard columns.
import { useLeaderboardRunMetrics } from "src/sections/common/DerivedMetrics/useLeaderboardRunMetrics";
import { experimentRunsOf, experimentUrl, latestRun } from "./leaderboardModel";
import { CHART_VIEW_DEFAULTS, chartsSummary } from "./metricStripModel";
import LeaderboardAnswer, { defaultPair } from "./LeaderboardAnswer";
import { pickHeadline } from "./leaderboardHeadline";
import CustomTooltip from "src/components/tooltip";
import { bestLikeForLike } from "./leaderboardHeadline";
import { successFrom } from "./leaderboardVersions";
import CompareAnyHint from "./CompareAnyHint";
import LeaderboardFooter from "./LeaderboardFooter";
import useLeaderboardRoom from "./useLeaderboardRoom";
import useLeaderboardNumbers from "./useLeaderboardNumbers";

/**
 * Experiments tab body (PRODUCT-SPEC §3.7, ONE-VIEW-SPEC A2): the verdict
 * line, the Charts section (open on roomy windows, collapsed on 1280×720
 * with its one-line answer), the run leaderboard and its status footer.
 * The toolbar and selection bar live in ExperimentTabHeader; both read the
 * same URL state, R1 query and layout prefs.
 *
 * Ref API (DevelopDetailView / ExperimentTabHeader): refreshExperimentTab(),
 * clearRowSelection().
 */
const ExperimentTab = forwardRef(({ setRowSelected }, ref) => {
  const view = useLeaderboardView();
  const navigate = useNavigate();
  const location = useLocation();
  const { role, user } = useAuthContext();
  const prefs = useLeaderboardPrefs(view.datasetId);
  const gridRef = useRef(null);
  const [selectedCount, setSelectedCount] = useState(0);
  // IT-R3 G2 P5: with two or more runs ticked the headline follows them.
  const [selectedIds, setSelectedIds] = useState(null);
  // IT-R1 G2 P2: `Compare any runs…` highlights the checkboxes.
  const [compareAny, setCompareAny] = useState(false);
  // R4 (lane Rb, D26): the pinned baseline row has its own checkbox (AG
  // Grid can't select a pinned row), so `Compare 3 runs` can include it.
  const [baselineTicked, setBaselineTicked] = useState(false);
  const gridRows = useRef([]);
  const baselineRef = useRef(null);
  const publish = useCallback(
    (rows, ticked) => {
      const b = baselineRef.current;
      const withBase =
        ticked && b && !rows.some((r) => r.run_id === b.run_id)
          ? [b, ...rows]
          : rows;
      setSelectedCount(withBase.length);
      setSelectedIds(
        withBase.length >= 2 ? new Set(withBase.map((r) => r.run_id)) : null,
      );
      setRowSelected(withBase);
    },
    [setRowSelected],
  );
  const onSelectionChange = useCallback(
    (rows) => {
      gridRows.current = rows;
      publish(rows, baselineTicked);
    },
    [publish, baselineTicked],
  );
  const toggleBaselineTick = useCallback(() => {
    const next = !baselineTicked;
    setBaselineTicked(next);
    publish(gridRows.current, next);
  }, [publish, baselineTicked]);
  const [upload, setUpload] = useState(null);
  const actions = useLeaderboardActions(view.datasetId, {
    metricDefs: view.metricDefs,
    pinnedRunId: view.pinnedRunId,
    runs: view.allRuns,
    onDone: () => {
      gridRef.current?.clearSelection();
      setRowSelected([]);
    },
  });

  // W4 §9 (M6): the role and the dataset's access decide the doors.
  const access = useDatasetAccess(view.datasetId);
  const gates = useMemo(
    () =>
      leaderboardGates(
        {
          roleEdit: RolePermission.DATASETS[PERMISSIONS.UPDATE][role],
          roleDelete: RolePermission.DATASETS[PERMISSIONS.DELETE][role],
        },
        access,
      ),
    // access.can / access.reason are memoised on the access view.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [role, access.can, access.reason],
  );
  const { canEdit, canRun, canDelete, canDownload, tips } = gates;

  useImperativeHandle(
    ref,
    () => ({
      // Keyboard (header's useShortcuts → grid row focus).
      get kb() {
        return gridRef.current?.kb;
      },
      refreshExperimentTab: () => {
        view.query.refetch();
        gridRef.current?.clearSelection();
        setRowSelected([]);
      },
      clearRowSelection: () => {
        gridRef.current?.clearSelection();
        gridRows.current = [];
        setBaselineTicked(false);
        setRowSelected([]);
      },
    }),
    [view.query, setRowSelected],
  );

  // The tab's params mean something else on the Data tab (`version` is the
  // as-of version there, `filters` a grid filter), so drop the ones this
  // tab set when the user switches away.
  const navigateRef = useRef(navigate);
  navigateRef.current = navigate;
  useEffect(() => {
    const ownPath = window.location.pathname;
    // Values another tab owned before this one mounted (the Data tab's
    // as-of `version`) are left alone; only what this tab set is dropped.
    const atMount = new URLSearchParams(window.location.search);
    const before = Object.fromEntries(
      LEADERBOARD_URL_KEYS.map((k) => [k, atMount.get(k)]),
    );
    return () => {
      // Only a tab switch on this page; never rewrite another page's URL.
      if (window.location.pathname !== ownPath) return;
      const params = new URLSearchParams(window.location.search);
      if (params.get("tab") === "experiments") return;
      const ours = LEADERBOARD_URL_KEYS.filter(
        (k) => params.has(k) && params.get(k) !== before[k],
      );
      if (!ours.length) return;
      ours.forEach((k) => params.delete(k));
      navigateRef.current(
        { pathname: window.location.pathname, search: `?${params}` },
        { replace: true },
      );
    };
  }, []);
  // Selection never outlives the tab.
  useEffect(() => () => setRowSelected([]), [setRowSelected]);

  const room = useLeaderboardRoom();
  const runMetrics = useLeaderboardRunMetrics(view.datasetId, view.allRuns);
  const catalog = useMemo(
    () => columnCatalog(view.metricDefs, view.allRuns, { room, runMetrics }),
    [view.metricDefs, view.allRuns, room, runMetrics],
  );
  // A2-07: the columns are G's layout; the old Columns choices move in once.
  useLegacyPrefsMigration(view.datasetId, catalog, Boolean(view.query.data));
  const layout = useColumnLayout({
    surface: LB_SURFACE,
    scopeId: view.datasetId,
    columns: catalog,
  });

  const runsPerExperiment = useMemo(() => {
    const counts = {};
    view.allRuns.forEach((r) => {
      counts[r.experiment_id] = (counts[r.experiment_id] ?? 0) + 1;
    });
    return counts;
  }, [view.allRuns]);

  const visibleByExperiment = useMemo(() => {
    const map = {};
    view.runs.forEach((r) => {
      (map[r.experiment_id] ??= []).push(r);
    });
    return map;
  }, [view.runs]);

  const fromUrl = `${location.pathname}${location.search}`;
  const baselineRun = view.baselineRun;
  baselineRef.current = baselineRun;
  // R4 (lane Rb, D22/D26): one source for numbers and versions.
  const numbers = useLeaderboardNumbers(view);
  // IT-R4 G2 P4/P5: the answer's pair (LeaderboardAnswer.defaultPair) names
  // the candidate; pickHeadline is the fallback before numbers load.
  const answerPair = useMemo(
    () => defaultPair(numbers, view.runs, baselineRun, selectedIds),
    [numbers, view.runs, baselineRun, selectedIds],
  );
  const headlineCandId =
    answerPair?.candId ??
    pickHeadline(view.allRuns, baselineRun)?.candidate?.run_id ??
    null;
  const newCandId =
    answerPair && answerPair.baseId === view.pinnedRunId
      ? answerPair.candId
      : null;

  const context = useMemo(
    () => ({
      datasetId: view.datasetId,
      q: view.q,
      group: view.group,
      agg: view.agg,
      showDeltas: prefs.showDeltas,
      baselineId: view.baseline?.run_id ?? null,
      baselineLabel: view.baseline?.label ?? null,
      baselineSource: view.baseline?.source ?? null,
      baselineRun,
      pinnedRunId: view.pinnedRunId,
      // IT-R4 G2 P4 (S2): every Compare link names this candidate when it
      // is among the runs (the headline's pick).
      headlineCandId: headlineCandId ?? null,
      newCandId,
      // IT-R4 G2 P9: runs by someone else carry an owner chip.
      viewerId: user?.id ?? null,
      // "Latest" means latest on the dataset, so only without a version filter.
      latestExperimentId:
        view.version === "all"
          ? latestRun(view.allRuns)?.experiment_id ?? null
          : null,
      runsPerExperiment,
      visibleByExperiment,
      // IT-R1 G2 P9: side by side pairs with a sibling of the same experiment.
      allRuns: view.allRuns,
      canEdit,
      canRun,
      canDelete,
      canDownload,
      tips,
      fromUrl,
      navigate,
      pinBaseline: actions.pinBaseline,
      unpinBaseline: actions.unpinBaseline,
      requestRerun: actions.requestRerun,
      requestDelete: actions.requestDelete,
      openRename: actions.openRename,
      downloadCsv: actions.downloadCsv,
      duplicate: actions.duplicate,
      copyText: actions.copyText,
      openUpload: (run, anchor) => setUpload({ run, anchor }),
      numbers,
      baselineTicked,
      toggleBaselineTick: baselineRun ? toggleBaselineTick : null,
      compareAny,
    }),
    [
      compareAny,
      view.datasetId,
      view.q,
      view.group,
      view.agg,
      prefs.showDeltas,
      view.baseline,
      baselineRun,
      headlineCandId,
      newCandId,
      user?.id,
      view.pinnedRunId,
      view.version,
      view.allRuns,
      runsPerExperiment,
      visibleByExperiment,
      canEdit,
      canRun,
      canDelete,
      canDownload,
      tips,
      fromUrl,
      navigate,
      actions.pinBaseline,
      actions.unpinBaseline,
      actions.requestRerun,
      actions.requestDelete,
      actions.openRename,
      actions.downloadCsv,
      actions.duplicate,
      actions.copyText,
      numbers,
      baselineTicked,
      toggleBaselineTick,
    ],
  );

  const openRow = useCallback(
    (run, { experimentOnly } = {}) =>
      navigate(
        experimentUrl(view.datasetId, run.experiment_id, {
          runId: experimentOnly ? undefined : run.run_id,
          runs: experimentOnly
            ? experimentRunsOf(view.allRuns, run.experiment_id)
            : undefined,
        }),
      ),
    [navigate, view.datasetId, view.allRuns],
  );

  const onPointClick = useCallback((run, event) => {
    if (event?.shiftKey) gridRef.current?.toggleSelection(run.run_id);
    else gridRef.current?.scrollToRun(run.run_id);
  }, []);

  // Keep the upload popover's run fresh while it polls.
  const uploadRun = upload
    ? view.allRuns.find((r) => r.run_id === upload.run.run_id) ?? upload.run
    : null;

  // A2-01: the Charts section is the viewer's (remembered; IT-R1 G2 P3:
  // collapsed to its one-line summary until the viewer opens it). `charts=1|0` in a link still opens or
  // closes it: applied once to the layout, and dropped from the URL as soon
  // as the viewer toggles the section themselves.
  const enoughRuns = view.runs.length >= 2;
  const { chartsOpen, setChartsOpen } = prefs;
  const appliedParam = useRef(null);
  useEffect(() => {
    const p = view.chartsParam;
    if (p !== "1" && p !== "0") {
      appliedParam.current = null;
      return;
    }
    appliedParam.current = p;
    if (chartsOpen !== (p === "1")) setChartsOpen(p === "1");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view.chartsParam]);
  const lastOpen = useRef(chartsOpen);
  useEffect(() => {
    const was = lastOpen.current;
    lastOpen.current = chartsOpen;
    const p = appliedParam.current;
    if (was === chartsOpen || !p || chartsOpen === (p === "1")) return;
    appliedParam.current = null;
    view.setChartsParam(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chartsOpen]);

  const [chartView] = useViewUrlState(CHART_VIEW_DEFAULTS);
  // IT-R3 G2 P3: one `best` rule (like for like), its rule on hover.
  const bestLike = useMemo(
    () =>
      bestLikeForLike(
        view.allRuns,
        (r) => successFrom(numbers, r),
        baselineRun,
      ),
    [view.allRuns, numbers, baselineRun],
  );
  const summary = useMemo(() => {
    // IT-R3 G2 P5: three facts — best · cheapest · fastest.
    const text = chartsSummary(view.runs, view.metricDefs, {
      scoreKey: chartView.chart_eval,
      best: bestLike,
      filtered: view.isFiltered ? numbers?.total : null,
    });
    if (!text || !bestLike) return text;
    return (
      <CustomTooltip show title={bestLike.rule} size="small" placement="top">
        <Typography
          component="span"
          typography="s2"
          color="text.secondary"
          noWrap
          data-testid="charts-summary"
        >
          {text}
        </Typography>
      </CustomTooltip>
    );
  }, [
    view.runs,
    view.metricDefs,
    chartView.chart_eval,
    bestLike,
    numbers?.total,
    view.isFiltered,
  ]);

  // IT-R5 G2 P1: closed, the Charts row reads as the action it is —
  // `› Show charts · best #2D 91.0% · cheapest #2A $0.0040`.
  const [lbLayout] = useLayoutPrefs(LB_SURFACE, view.datasetId);
  const chartsShown = lbLayout?.sections?.[CHARTS_SECTION] === true;

  const hideCard = (id) => {
    const before = prefs.chartCards;
    prefs.setChartCards((cards) => cards.filter((c) => c !== id));
    // EL#46: hiding a chart is reversible (and the View menu lists it).
    const label = CHART_CARDS.find((c) => c.id === id)?.label ?? "Chart";
    notify(`${label} chart hidden`, {
      key: `chart-hidden-${id}`,
      undo: { onUndo: () => prefs.setChartCards(before) },
    });
  };

  return (
    <>
      <LeaderboardShell
        view={view}
        chartsOpen={chartsOpen}
        answer={
          <>
            <LeaderboardAnswer
              runs={view.runs}
              allRuns={view.allRuns}
              numbers={numbers}
              baselineRun={baselineRun}
              datasetId={view.datasetId}
              datasetKind={numbers?.kind}
              fromUrl={fromUrl}
              compareAnyOn={compareAny}
              onCompareAny={() => setCompareAny((on) => !on)}
              selectedIds={selectedIds}
              pinnedRunId={view.pinnedRunId}
              rowLabels={view.rowLabels}
            />
            {compareAny ? (
              <CompareAnyHint
                runs={view.allRuns}
                filters={view.filters}
                onFilters={view.setFilters}
                selectedCount={selectedCount}
              />
            ) : null}
          </>
        }
        onClearFilters={() => {
          view.setQ("");
          view.setFilters([]);
          view.setVersion("all");
        }}
        strip={
          enoughRuns ? (
            <Section
              id={CHARTS_SECTION}
              surface={LB_SURFACE}
              scopeId={view.datasetId}
              title="Charts"
              defaultOpen={false}
              summary={summary}
              // IT-R3 G2 P5: a 28 px header row (the table starts higher).
              sx={{
                px: 1.5,
                pb: 0.25,
                "& > .MuiStack-root:first-of-type": { minHeight: 28 },
                // IT-R5 G2 P1: closed, the row reads as its action (the
                // heading keeps `Charts` for ⌘K and screen readers).
                ...(chartsShown
                  ? {}
                  : {
                      "& h3": {
                        fontSize: 0,
                        "&::before": (t) => ({
                          ...t.typography.s1,
                          fontWeight: t.typography.fontWeightMedium,
                          content: '"Show charts ·"',
                        }),
                      },
                    }),
              }}
            >
              {prefs.chartCards.length ? (
                <Box sx={{ mx: -1.5 }}>
                  <ExperimentMetricStrip
                    runs={view.runs}
                    metricDefs={view.metricDefs}
                    baselineRun={baselineRun}
                    cards={prefs.chartCards}
                    onHideCard={hideCard}
                    onPointClick={onPointClick}
                  />
                </Box>
              ) : (
                <Typography
                  typography="s2"
                  color="text.secondary"
                  sx={{ pb: 1 }}
                >
                  Every chart is hidden.{" "}
                  <Button
                    size="small"
                    onClick={() =>
                      prefs.setChartCards(CHART_CARDS.map((c) => c.id))
                    }
                    sx={{ typography: "s2", minWidth: 0, px: 0.5, py: 0 }}
                  >
                    Show all charts
                  </Button>
                </Typography>
              )}
            </Section>
          ) : null
        }
        grid={
          <LeaderboardGrid
            ref={gridRef}
            runs={view.runs}
            catalog={catalog}
            layout={layout}
            group={view.group}
            onGroup={view.setGroup}
            context={context}
            onSelectionChange={onSelectionChange}
            onOpenRow={openRow}
          />
        }
        footer={<LeaderboardFooter view={view} selectedCount={selectedCount} />}
      />
      {actions.dialogs}
      <MatchDoorHost />
      {/* integration C2: ONE persistent Columns menu for the leaderboard layout,
          outside every popover: View › Columns › Edit, ⌘K Columns… and a
          header right-click open it, anchored to the grid header. */}
      <ColumnsMenuHost
        layout={layout}
        getAnchor={() => gridRef.current?.headerElement?.()}
      />
      {uploadRun && (
        <UploadResultsPopover
          run={uploadRun}
          anchorEl={upload.anchor}
          onClose={() => setUpload(null)}
        />
      )}
    </>
  );
});

ExperimentTab.displayName = "ExperimentTab";

ExperimentTab.propTypes = {
  experimentSearch: PropTypes.string,
  setRowSelected: PropTypes.func.isRequired,
  setCurrentTab: PropTypes.func,
};

export default ExperimentTab;
