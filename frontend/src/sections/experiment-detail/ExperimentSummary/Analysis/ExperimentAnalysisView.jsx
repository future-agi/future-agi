import React, {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import PropTypes from "prop-types";
import { useLocation, useParams, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  Alert,
  Box,
  Button,
  Drawer,
  IconButton,
  LinearProgress,
  Skeleton,
  Stack,
  Typography,
  useMediaQuery,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { useAuthContext } from "src/auth/hooks";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";
import axios, { endpoints } from "src/utils/axios";
import { useRunAnalysis } from "src/api/develop/analysis";
import { useRescore } from "src/sections/common/Runs/useRescore";
import { useRunColors } from "src/sections/common/Runs/runColors";
import { parsePills } from "src/sections/common/Results/filterPills";
import {
  resultsGridHref,
  reviewHref,
  taskCompareHref,
} from "src/sections/common/Analysis/analysisLinks";
import MetricCard from "src/sections/common/Analysis/MetricCard";
import BreakdownTable from "src/sections/common/Analysis/BreakdownTable";
import EmptyState from "src/sections/common/ux/EmptyState";
import StartReviewDialog from "src/sections/develop-detail/Review/StartReviewDialog";
import SummaryToolbar from "./SummaryToolbar";
import SummaryVerdicts from "./SummaryVerdicts";
import { summaryVerdicts, verdictMarkdown } from "./verdictItems";
import { useDatasetKind } from "src/sections/common/Datasets/datasetKind";
import { Section, useSections } from "src/sections/common/ux/oneview";
import { useViewUrlState } from "src/sections/common/ux/viewState";
import { FAB_GUTTER_PB } from "src/sections/common/ux/useFabGutter";
import { rowsPill } from "src/sections/common/Results/filterPills";
import { useExperimentDetailContext } from "../../experiment-context";
import ChooseWinnerDrawer from "../ChooseWinnerDrawer";
import { FailedEvalsLine, TradeoffDetail } from "./TradeoffDetail";
import SummaryOutline, { revealAnchor } from "./SummaryOutline";
import {
  agentSummary,
  breakdownSummary,
  evalsSummary,
  humanSummary,
  opsSummary,
} from "./sectionSummaries";
import AllRunsTable from "./AllRunsTable";
import VoiceSummarySection from "src/sections/common/Analysis/VoiceSummarySection";
import HumanCard from "./HumanCard";
import { AgentCard, OpsCard } from "./OpsAgentCards";
import WorklistTable from "./WorklistTable";
import FailsOnCards from "./FailsOnCards";
import { useFalconRowLinks } from "./useFalconRowLinks";
import { readR2Params } from "src/sections/common/Analysis/analysisLinks";
import Workbench from "src/sections/common/Analysis/workbench/Workbench";
import RootCauseCard from "src/sections/common/Analysis/RootCauseCard";
import ClassificationCard from "src/sections/common/DerivedMetrics/ClassificationCard";

const PARAMS = ["cand", "group_by", "bmetric", "wmetric"];

// The Summary's Sections share one remembered layout per user (surface
// `summary`): open / collapsed is a reading preference, not data.
const SURFACE = "summary";

/** Which Section holds a metric's card, and the card's anchor. */
const sectionOfMetric = (key) =>
  key === "human" || key === "pref"
    ? { sectionId: "human", anchorId: "metric-human" }
    : key.startsWith("ops:")
      ? { sectionId: "ops", anchorId: "metric-ops" }
      : key.startsWith("agent:")
        ? { sectionId: "agent", anchorId: "metric-agent" }
        : { sectionId: "evals", anchorId: `metric-${key}` };

/**
 * The page scrolls itself only where its parent gives it a fixed height
 * (compare Summary); elsewhere (experiment Summary) the dashboard scrolls,
 * and an `overflow: auto` box that never scrolls would pin the sticky
 * `On this page` list in place. Overflow follows what the box really does.
 */
function useScrollsItself(ref, deps) {
  const [scrolls, setScrolls] = useState(true);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return undefined;
    const check = () => setScrolls(el.scrollHeight > el.clientHeight + 1);
    check();
    if (typeof ResizeObserver === "undefined") return undefined;
    const ro = new ResizeObserver(check);
    ro.observe(el);
    if (el.firstElementChild) ro.observe(el.firstElementChild);
    return () => ro.disconnect();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return scrolls;
}

/** `#2 Five ways · v2` — the experiment the Summary is about (CSV/copy title). */
const runs0 = (analysis) => analysis?.runs?.[0]?.experiment_name ?? "Summary";

function LoadingState() {
  return (
    <Stack gap={1.5} sx={{ p: 1.5 }}>
      <Skeleton variant="rounded" height={40} />
      <Skeleton variant="rounded" height={72} />
      <Skeleton variant="rounded" height={160} />
      <Box
        sx={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fill, minmax(360px, 1fr))",
          gap: 1.5,
        }}
      >
        {Array.from({ length: 6 }, (_, i) => (
          <Skeleton key={i} variant="rounded" height={220} />
        ))}
      </Box>
    </Stack>
  );
}

/**
 * The Summary of an experiment and of a comparison (PRODUCT-SPEC §3.13):
 * toolbar · verdict · all runs · metric cards (evals, human, ops, agent) ·
 * rows to review · breakdown. Every count links to the results grid
 * filtered to exactly those rows (C10).
 *   mode="experiment"  runs of one experiment; baseline = `baseline=` or run A
 *   mode="compare"     the `runs=` set; baseline may be "none" (absolute)
 */
const ExperimentAnalysisOverview = ({
  datasetId,
  runIds = [],
  baselineId,
  mode = "compare",
}) => {
  const location = useLocation();
  const { experimentId } = useParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const { role } = useAuthContext();
  const canEdit = Boolean(
    RolePermission.DATASETS?.[PERMISSIONS.UPDATE]?.[role] ?? true,
  );
  const { chooseWinnerOpen, setChooseWinnerOpen } =
    useExperimentDetailContext();
  const [review, setReview] = useState(null);
  // URL view state (AS#26): Table/Radar and the 95% CI toggle.
  // (`chart` belongs to the Workbench, so the all-runs view is `allruns`.)
  // (`ci` is no longer a URL param: the 95% CI switch is remembered per
  // user in the All runs layout.)
  const [viewState, setViewState] = useViewUrlState({
    allruns: "table",
    wtab: "",
  });
  const pageRef = useRef(null);
  const sections = useSections(SURFACE);
  const wide = useMediaQuery("(min-width:1600px)");
  const pills = useMemo(
    () => parsePills(searchParams.get("filters")),
    [searchParams],
  );
  const q = Object.fromEntries(
    PARAMS.map((k) => [k, searchParams.get(k) || undefined]),
  );
  const from = `${location.pathname}${location.search}`;

  const setParam = useCallback(
    (key, value) =>
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          if (value == null || value === "") next.delete(key);
          else next.set(key, value);
          return next;
        },
        { replace: true },
      ),
    [setSearchParams],
  );

  const {
    data: analysis,
    isLoading,
    isError,
    refetch,
    isFetching,
  } = useRunAnalysis(datasetId, {
    runIds,
    baselineId,
    filters: pills,
    groupBy: q.group_by,
    bmetric: q.bmetric,
    cand: q.cand,
    wmetric: q.wmetric,
  });

  // Choose winner ranks and the legacy eval list come from stats/ (experiment page).
  const { data: stats } = useQuery({
    queryKey: ["experiment-summary", experimentId],
    queryFn: () =>
      axios.get(endpoints.develop.experiment.getSummary(experimentId)),
    select: (e) => e?.data?.result,
    enabled: mode === "experiment" && Boolean(experimentId),
  });
  const ranks = useMemo(
    () =>
      Object.fromEntries(
        (stats?.table_data ?? [])
          .filter((r) => r.rank)
          .map((r) => [r.dataset_id, r.rank]),
      ),
    [stats],
  );

  const runOrder = analysis?.runs?.map((r) => r.run_id) ?? runIds;
  const colors = useRunColors(runOrder, analysis?.baseline_run_id ?? null);
  const baseId = analysis?.baseline_run_id ?? null;
  const baseLabel = analysis?.baseline_label ?? null;
  // W1-2: "Re-score #2A on vN" is the one re-score door (useRescore): a
  // confirm with the judge's cost, then a new run with the flipped rows.
  const rescore = useRescore({ datasetId });
  // L7b G5: the caveat names the version the other run was scored with;
  // the re-score scores with it (eval_versions, as See changes' Use vN), so
  // the child run is on that version and the confirm names it.
  const rescoreStale = (r, name) =>
    r.version
      ? rescore.request({
          posts: [
            {
              url: endpoints.develop.experiment.reRunExperimentCell(
                r.experiment_id,
              ),
              body: {
                user_eval_metric_ids: [r.metric],
                run_ids: [r.run_id],
                eval_versions: { [r.metric]: r.version },
              },
            },
          ],
          evalNames: [name ?? r.metric],
          experimentId: r.experiment_id,
          title: () =>
            `Re-score ${r.run_label} with ${name ?? r.metric} ${r.version}?`,
          confirmLabel: `Use ${r.version}`,
        })
      : rescore.request({
          runs: [
            {
              run_id: r.run_id,
              label: r.run_label,
              experiment_id: r.experiment_id,
            },
          ],
          evalIdsOf: () => [r.metric],
          evalNames: [name ?? r.metric],
        });

  useFalconRowLinks(datasetId);

  const grid = useCallback(
    (extraPills = [], opts = {}) =>
      resultsGridHref({
        mode,
        datasetId,
        experimentId,
        runIds,
        baselineId: baseId,
        pills: [...pills, ...extraPills],
        ...opts,
      }),
    [mode, datasetId, experimentId, runIds, baseId, pills],
  );

  const links = useMemo(
    () => ({
      grid: () => grid(),
      pill: (pill) => grid(pill ? [pill] : []),
      movement: (metric, dir, run) =>
        grid([{ type: "movement", metric, dir, run }]),
      fails: (metric, run) => grid([{ type: "fails", metric, run }]),
      errors: (run) => grid([{ type: "error", run }]),
      flaky: (run) => grid([{ type: "flaky", run }]),
      toolErrors: (run, tool) =>
        grid([{ type: "tool_error", run, ...(tool && { tool }) }]),
      moreSteps: (run) =>
        grid([
          { type: "movement", metric: "agent:steps", dir: "regressed", run },
        ]),
      sort: (metric, run) =>
        `${grid()}${grid().includes("?") ? "&" : "?"}sort=${encodeURIComponent(`delta:${metric}:${run}:asc`)}`,
      pref: (run, value, count) =>
        count == null
          ? null
          : grid([{ type: "pref", run, value, source: "human" }]),
      disagree: (run) => grid([{ type: "pref_disagree", run }]),
      review: (queueId) => reviewHref(datasetId, queueId, from),
      row: (exampleId, tab) =>
        grid(tab?.pill ? [tab.pill] : [], {
          row: exampleId,
          show: tab?.pill ? undefined : tab?.show,
        }),
      tab: (tab) =>
        grid(tab?.pill ? [tab.pill] : [], {
          show: tab?.pill ? undefined : tab?.show,
        }),
      addToDataset: (tab) =>
        grid(tab?.pill ? [tab.pill] : [], {
          show: tab?.pill ? undefined : tab?.show,
        }),
    }),
    [grid, datasetId, from],
  );

  const verdictItems = useMemo(() => summaryVerdicts(analysis), [analysis]);
  const { kind: datasetKindValue } = useDatasetKind(datasetId);
  const verdictRowsHref = useCallback(
    (item) => {
      const withRows = [
        ...pills,
        rowsPill(item.worseIds, {
          label: `Got worse on ${item.run.label} vs ${item.base.label}`,
        }),
      ];
      const args = {
        mode,
        datasetId,
        experimentId,
        runIds,
        baselineId: item.base.run_id,
        pills: withRows,
        from,
      };
      // The results grid honours the `rows` pill: it shows exactly these
      // tasks, so the count on the link equals the rows that open.
      return resultsGridHref(args);
    },
    [mode, datasetId, experimentId, runIds, from, pills],
  );
  const sideBySideHref = useCallback(
    (item) =>
      taskCompareHref({
        datasetId,
        runIds: [item.base.run_id, item.run.run_id],
        baselineId: item.base.run_id,
        rowId: item.worseIds[0] ?? undefined,
        rail: item.worseIds.length ? "regressed" : undefined,
        from,
      }),
    [datasetId, from],
  );
  // A metric name in the trade-offs opens its Section and scrolls to it.
  const openSection = useCallback(
    (id) => sections.setOpen(id, true),
    [sections],
  );
  const scrollTo = (key) =>
    revealAnchor({ ...sectionOfMetric(key), open: openSection });
  const scrollsItself = useScrollsItself(pageRef, [isLoading]);

  // Keep `cand` valid when the run set changes.
  useEffect(() => {
    if (q.cand && analysis && !analysis.runs.some((r) => r.run_id === q.cand))
      setParam("cand", null);
  }, [analysis, q.cand, setParam]);

  if (isLoading) return <LoadingState />;
  if (isError || !analysis) {
    return (
      <Box sx={{ p: 1.5 }}>
        <Alert
          severity="error"
          action={
            <Button color="inherit" size="small" onClick={() => refetch()}>
              Retry
            </Button>
          }
        >
          Analysis could not be loaded.
        </Alert>
      </Box>
    );
  }

  const evals = analysis.metrics.filter((m) => m.family === "eval");
  const running = analysis.runs.filter((r) =>
    ["Running", "Queued", "NotStarted", "ExperimentEvaluation"].includes(
      r.status,
    ),
  );
  const waiting = analysis.runs.filter((r) => r.status === "WaitingForResults");
  const defaultEvalNames = analysis.default_evals ?? [];
  const outlineScope =
    mode === "experiment" && experimentId
      ? experimentId
      : `compare.${datasetId}.${[...runIds].sort().join(",")}`;
  const onRescore = canEdit
    ? (r) =>
        rescoreStale(r, analysis.metrics.find((m) => m.key === r.metric)?.name)
    : undefined;
  const hasHuman =
    Object.keys(analysis.preference ?? {}).length > 0 ||
    (analysis.human ?? []).length > 0;
  const hasOps = analysis.metrics.some(
    (m) => m.key === "ops:latency" || m.key === "ops:cost",
  );
  const hasAgent = (analysis.agent ?? []).length > 0;
  const hasBreakdown = evals.length > 0;
  const outline = [
    { id: "verdict", label: "Verdict", anchorId: "analysis-verdict" },
    ...(baselineId !== "none"
      ? [{ id: "root", label: "Root cause", anchorId: "analysis-root-cause" }]
      : []),
    { id: "runs", label: "All runs", anchorId: "analysis-all-runs" },
    { id: "worklist", label: "Tasks to review", anchorId: "analysis-worklist" },
    {
      id: "evals",
      label: "Evals",
      anchorId: "summary-section-evals",
      sectionId: "evals",
    },
    {
      id: "human",
      label: "Human review",
      anchorId: "summary-section-human",
      sectionId: "human",
    },
    {
      id: "ops",
      label: "Latency and cost",
      anchorId: "summary-section-ops",
      sectionId: "ops",
    },
    {
      id: "agent",
      label: "Agent steps",
      anchorId: "summary-section-agent",
      sectionId: "agent",
    },
    {
      id: "breakdown",
      label: "Breakdown by split",
      anchorId: "summary-section-breakdown",
      sectionId: "breakdown",
    },
  ];
  // Each Section header: chevron · title (· count) · its one-line answer
  // when collapsed (computed, never typed; none → title only).
  const sectionSx = {
    scrollMarginTop: 72,
    borderTop: "1px solid",
    borderColor: "divider",
    pt: 0.5,
  };
  const quiet = (text) => (
    <Typography typography="s2" color="text.secondary" sx={{ pb: 1 }}>
      {text}
    </Typography>
  );

  const toolbarInCard = mode === "compare" && verdictItems.length > 0;
  const toolbar = (
    <SummaryToolbar
      mode={mode}
      datasetId={datasetId}
      analysis={analysis}
      runIds={runIds}
      baselineId={baselineId ?? baseId ?? "none"}
      onBaselineChange={(id) => setParam("baseline", id)}
      filters={pills}
      canEdit={canEdit}
      verdicts={verdictItems}
      candidateId={analysis.candidate_run_id ?? undefined}
      copySummary={
        toolbarInCard
          ? () =>
              verdictMarkdown(analysis, verdictItems, {
                title: runs0(analysis),
              })
          : undefined
      }
      onToggleChart={() =>
        setViewState({
          allruns: viewState.allruns === "radar" ? "table" : "radar",
        })
      }
    />
  );
  return (
    <Box
      ref={pageRef}
      sx={{
        p: 1.5,
        // The Falcon bubble sits bottom-right: keep a gutter on the right and
        // at the end so it never covers a number, chip or button.
        pb: `calc(12px + ${FAB_GUTTER_PB})`,
        pr: `calc(12px + ${FAB_GUTTER_PB})`,
        flex: 1,
        overflowY: scrollsItself ? "auto" : "visible",
        minWidth: 0,
      }}
    >
      <Box
        sx={{
          display: "grid",
          gridTemplateColumns: wide ? "minmax(0, 1fr) 176px" : "minmax(0, 1fr)",
          columnGap: 3,
          alignItems: "start",
        }}
      >
        <Stack gap={1.5} sx={{ minWidth: 0 }}>
          {/* IT-R5 G6 P3: on Compare, Explore and More sit next to
              Expand all in the verdict card (one row less). */}
          {toolbarInCard ? null : toolbar}
          {isFetching && <LinearProgress sx={{ height: 2, mt: -1 }} />}
          {running.length > 0 && (
            <Alert
              severity="info"
              icon={<Iconify icon="mdi:progress-clock" />}
              sx={{ py: 0 }}
            >
              Running ·{" "}
              {running
                .map(
                  (r) =>
                    `${r.label} ${r.progress?.done ?? 0}/${r.progress?.total ?? 0}`,
                )
                .join(" · ")}{" "}
              — numbers update every 5 s.
            </Alert>
          )}
          {waiting.length > 0 && (
            <Alert
              severity="warning"
              icon={<Iconify icon="mdi:cloud-upload-outline" />}
              sx={{ py: 0 }}
            >
              {waiting.map((r) => r.label).join(", ")} waiting for results.
              Values appear when the upload arrives.
            </Alert>
          )}
          {pills.length > 0 && (
            <Alert
              severity="info"
              sx={{ py: 0 }}
              action={
                <Button
                  size="small"
                  color="inherit"
                  onClick={() => setParam("filters", null)}
                >
                  Clear
                </Button>
              }
            >
              Showing {analysis.examples_in_view} of {analysis.examples_total}{" "}
              rows (filters from the grid apply here).
            </Alert>
          )}
          <SummaryVerdicts
            analysis={analysis}
            items={verdictItems}
            headerActions={
              toolbarInCard ? (
                <Box sx={{ width: 236, display: "flex" }}>{toolbar}</Box>
              ) : null
            }
            rowsHref={verdictRowsHref}
            sideBySideHref={sideBySideHref}
            title={runs0(analysis)}
            outlineScope={outlineScope}
            candidateId={analysis.candidate_run_id ?? undefined}
            datasetKind={datasetKindValue}
            shipHref={(ship) =>
              ship.rowId && baseId
                ? taskCompareHref({
                    datasetId,
                    runIds: [baseId, ship.runId],
                    baselineId: baseId,
                    rowId: ship.rowId,
                    from,
                  })
                : null
            }
            renderTradeoffs={(v, id) => (
              <TradeoffDetail
                id={id}
                verdict={v}
                analysis={analysis}
                links={links}
                onScrollTo={scrollTo}
                rescoring={rescore.pending}
                onRescore={onRescore}
              />
            )}
            footer={<FailedEvalsLine analysis={analysis} links={links} />}
          />
          {/* L7c g6 (sum:baseline): a cause says what fails where the
              baseline passes, so None mode (no baseline) has no cause card. */}
          {baselineId !== "none" && (
            <Box id="analysis-root-cause" sx={{ scrollMarginTop: 72 }}>
              <RootCauseCard
                datasetId={datasetId}
                runIds={runIds}
                baselineId={baselineId ?? baseId}
                mode={mode}
              />
            </Box>
          )}
          {/* R4 lane X: voice datasets answer speed and correctness first. */}
          <VoiceSummarySection
            datasetId={datasetId}
            runIds={runIds}
            baselineId={
              baselineId === "none" ? undefined : baselineId ?? baseId
            }
            scopeId={experimentId ?? datasetId}
          />
          {/* R4 lane X (8c): what each run fails on, linked to the rows. */}
          {runIds.length > 0 && (
            <FailsOnCards
              datasetId={datasetId}
              runIds={runIds}
              baselineId={
                baselineId === "none" ? undefined : baselineId ?? baseId
              }
              pillHref={links.pill}
            />
          )}
          {/* W5 B-H3: accuracy and macro F1 per run when a setup exists. */}
          <ClassificationCard datasetId={datasetId} runs={analysis.runs} />
          <AllRunsTable
            analysis={analysis}
            colors={colors}
            ranks={ranks}
            links={links}
            chart={viewState.allruns === "radar" ? "radar" : "table"}
            onChart={(v) => setViewState({ allruns: v })}
          />
          {evals.length > 0 && (
            <WorklistTable
              analysis={analysis}
              colors={colors}
              metricKey={analysis.worklist?.metric ?? undefined}
              onMetric={(v) => setParam("wmetric", v)}
              links={links}
              onReview={(ids) =>
                setReview({ exampleIds: ids, runB: analysis.worklist?.run_id })
              }
              candidate={{
                items: verdictItems,
                onChange: (id) => setParam("cand", id),
              }}
              tab={viewState.wtab || undefined}
              onTab={(v) => setViewState({ wtab: v })}
              sideBySideHref={(rowId, candId) =>
                taskCompareHref({
                  datasetId,
                  runIds: [baseId, candId].filter(Boolean),
                  baselineId: baseId,
                  rowId,
                  from,
                })
              }
            />
          )}
          <Box id="summary-section-evals" sx={sectionSx}>
            <Section
              id="evals"
              surface={SURFACE}
              title="Evals"
              count={evals.length}
              summary={evalsSummary(analysis) ?? undefined}
              defaultOpen
            >
              {evals.length === 0 ? (
                <EmptyState
                  compact
                  icon="material-symbols:check-circle-outline"
                  title="No evals on this experiment"
                  body="Add an eval to score these runs. Use Add Evaluations on the Data tab of the experiment."
                  sx={{ pb: 1.5 }}
                />
              ) : (
                <Box
                  sx={{
                    display: "grid",
                    gridTemplateColumns: {
                      xs: "1fr",
                      md: "repeat(2, minmax(0, 1fr))",
                      xl: "repeat(3, minmax(0, 1fr))",
                    },
                    gap: 1.5,
                    alignItems: "start",
                    pb: 0.5,
                  }}
                >
                  {evals.map((m) => (
                    <MetricCard
                      key={m.key}
                      metric={m}
                      runs={analysis.runs}
                      colors={colors}
                      baselineId={baseId}
                      baselineLabel={baseLabel}
                      candidateId={analysis.candidate_run_id}
                      links={links}
                      onRescore={
                        canEdit && m.mixed_versions
                          ? () => {
                              const stale = analysis.verdicts
                                .flatMap((v) => v.caveats)
                                .find(
                                  (c) =>
                                    c.kind === "evaluator_version" &&
                                    c.metric === m.key,
                                )?.rescore;
                              if (stale) rescoreStale(stale, m.name);
                            }
                          : undefined
                      }
                    />
                  ))}
                </Box>
              )}
            </Section>
          </Box>
          <Box id="summary-section-human" sx={sectionSx}>
            <Section
              id="human"
              surface={SURFACE}
              title="Human review"
              summary={humanSummary(analysis) ?? "No human review yet"}
              defaultOpen={hasHuman ? "roomy" : false}
            >
              {hasHuman ? (
                <Box sx={{ pb: 0.5 }}>
                  <HumanCard
                    bare
                    analysis={analysis}
                    colors={colors}
                    links={links}
                  />
                </Box>
              ) : (
                <EmptyState
                  compact
                  icon="mdi:account-check-outline"
                  title="No human review yet"
                  body={`Reviewers compare two runs row by row; their preferences and the LLM judge's agreement show here.${baseId && analysis.candidate_run_id ? "" : " Pick two runs to start."}`}
                  actions={
                    baseId && analysis.candidate_run_id && canEdit
                      ? [
                          {
                            label: "Start a pairwise review…",
                            icon: "mdi:compare-horizontal",
                            onClick: () =>
                              setReview({
                                exampleIds: [],
                                runB: analysis.candidate_run_id,
                              }),
                          },
                        ]
                      : []
                  }
                  sx={{ pb: 1.5 }}
                />
              )}
            </Section>
          </Box>
          <Box id="summary-section-ops" sx={sectionSx}>
            <Section
              id="ops"
              surface={SURFACE}
              title="Latency and cost"
              summary={opsSummary(analysis) ?? undefined}
              defaultOpen={hasOps ? "roomy" : false}
            >
              {hasOps ? (
                <Box sx={{ pb: 0.5 }}>
                  <OpsCard
                    bare
                    analysis={analysis}
                    colors={colors}
                    baselineId={baseId}
                    baselineLabel={baseLabel}
                  />
                </Box>
              ) : (
                quiet("No latency or cost was recorded for these runs.")
              )}
            </Section>
          </Box>
          <Box id="summary-section-agent" sx={sectionSx}>
            <Section
              id="agent"
              surface={SURFACE}
              title="Agent steps"
              summary={
                agentSummary(analysis) ??
                (hasAgent ? undefined : "No agent runs in view")
              }
              defaultOpen={hasAgent ? "roomy" : false}
            >
              {hasAgent ? (
                <Box sx={{ pb: 0.5 }}>
                  <AgentCard
                    bare
                    analysis={analysis}
                    colors={colors}
                    baselineId={baseId}
                    baselineLabel={baseLabel}
                    links={links}
                  />
                </Box>
              ) : (
                quiet(
                  "Steps, tool calls and tool errors show here for agent runs.",
                )
              )}
            </Section>
          </Box>
          <Box id="summary-section-breakdown" sx={sectionSx}>
            <Section
              id="breakdown"
              surface={SURFACE}
              title={`Breakdown by ${(
                analysis.breakdown_options?.find(
                  (o) => o.value === (analysis.breakdown?.group_by ?? "split"),
                )?.label ?? "Split"
              ).toLowerCase()}`}
              summary={breakdownSummary(analysis) ?? undefined}
              defaultOpen={hasBreakdown ? "roomy" : false}
            >
              {hasBreakdown ? (
                <Box sx={{ pb: 0.5 }}>
                  <BreakdownTable
                    breakdown={analysis.breakdown}
                    options={analysis.breakdown_options}
                    metrics={analysis.metrics.filter(
                      (m) => m.family !== "ops" || m.key === "ops:latency",
                    )}
                    runs={analysis.runs}
                    colors={colors}
                    baselineId={baseId}
                    baselineLabel={baseLabel}
                    groupBy={analysis.breakdown?.group_by ?? "split"}
                    onGroupBy={(v) => setParam("group_by", v)}
                    onMetric={(v) => setParam("bmetric", v)}
                    cellHref={(groupPill, metric, dir, run) =>
                      grid([groupPill, { type: "movement", metric, dir, run }])
                    }
                  />
                </Box>
              ) : (
                quiet("Add an eval to compare runs split by split.")
              )}
            </Section>
          </Box>
        </Stack>
        <SummaryOutline
          items={outline}
          onOpenSection={openSection}
          rail={wide}
          rootRef={pageRef}
        />
      </Box>
      {mode === "experiment" && (
        <ChooseWinnerDrawer
          open={chooseWinnerOpen}
          onClose={() => setChooseWinnerOpen(false)}
          evalsList={stats?.column_config ?? []}
          defaultEvalNames={defaultEvalNames}
        />
      )}
      <StartReviewDialog
        open={Boolean(review)}
        onClose={() => setReview(null)}
        datasetId={datasetId}
        mode="pairwise"
        runA={baseId ?? undefined}
        runB={review?.runB}
        exampleIds={review?.exampleIds ?? []}
        returnTo={from}
      />
      {rescore.dialog}
    </Box>
  );
};

const ANALYSIS_PROP_TYPES = {
  datasetId: PropTypes.string,
  runIds: PropTypes.arrayOf(PropTypes.string),
  // a run id, or "none" for absolute values
  baselineId: PropTypes.string,
  mode: PropTypes.oneOf(["experiment", "compare"]),
};
ExperimentAnalysisOverview.propTypes = ANALYSIS_PROP_TYPES;

/**
 * Summary tab body: ONE view (owner rule 26). The pivot / chart tool is
 * not a second layout: `Explore: pivot and chart` (toolbar, ⌘K, or a
 * `view=workbench` link) opens it in a right-side sheet over the Summary;
 * closing it returns to the same scroll position.
 */
const EXPLORE_WIDTH = "min(1180px, 94vw)";
const ExperimentAnalysisView = (props) => {
  const [searchParams, setSearchParams] = useSearchParams();
  const { view } = readR2Params(searchParams);
  const exploreOpen = view === "workbench";
  const closeExplore = () =>
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.delete("view");
        return next;
      },
      { replace: true },
    );
  return (
    <>
      <ExperimentAnalysisOverview {...props} />
      <Drawer
        anchor="right"
        open={exploreOpen}
        onClose={closeExplore}
        PaperProps={{
          sx: {
            width: EXPLORE_WIDTH,
            display: "flex",
            flexDirection: "column",
          },
          "data-testid": "explore-sheet",
          "aria-label": "Explore: pivot and chart",
        }}
      >
        <Stack
          direction="row"
          alignItems="center"
          gap={1}
          sx={{
            px: 2,
            py: 1,
            borderBottom: "1px solid",
            borderColor: "divider",
            flexShrink: 0,
          }}
        >
          <Iconify icon="mdi:table-pivot" width={18} />
          <Typography typography="s1" fontWeight="fontWeightSemiBold">
            Explore: pivot and chart
          </Typography>
          <Typography typography="s2" color="text.secondary" noWrap>
            Slice these runs by any column; the Summary stays as it is.
          </Typography>
          <Box sx={{ flex: 1 }} />
          <IconButton
            aria-label="Close Explore"
            onClick={closeExplore}
            size="small"
          >
            <Iconify icon="mdi:close" width={18} />
          </IconButton>
        </Stack>
        {exploreOpen ? (
          <Box
            sx={{
              flex: 1,
              minHeight: 0,
              overflow: "auto",
              display: "flex",
              flexDirection: "column",
            }}
          >
            <Workbench
              datasetId={props.datasetId}
              runIds={props.runIds ?? []}
              baselineId={props.baselineId === "none" ? null : props.baselineId}
              mode={props.mode ?? "compare"}
            />
          </Box>
        ) : null}
      </Drawer>
    </>
  );
};

ExperimentAnalysisView.propTypes = ANALYSIS_PROP_TYPES;

export default ExperimentAnalysisView;
