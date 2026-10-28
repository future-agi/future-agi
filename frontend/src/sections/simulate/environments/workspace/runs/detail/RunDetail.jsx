import PropTypes from "prop-types";
import { useEffect, useRef, useState } from "react";
import { enqueueSnackbar } from "notistack";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { ErrorBoundary } from "react-error-boundary";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { harnessEnvironmentQuery } from "src/api/simulate-environments/environment";
import { alpha } from "@mui/material/styles";
import {
  Box,
  Stack,
  Typography,
  Button,
  IconButton,
  Tab,
  Skeleton,
} from "@mui/material";

import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { CustomTabs } from "src/components/tabs/tabs";
import { paths } from "src/routes/paths";
import {
  useRunDetail,
  useOptimizationRuns,
  runVerdict,
} from "src/api/simulate-environments/runDetail";
import {
  callDetailKeyPrefix,
  compareRunsKeyPrefix,
  runAnalyticsKey,
} from "src/api/simulate-environments/runEvals";
import {
  listMatchingCalls,
  uniqueScenarioKeys,
  useRerunCallsInRun,
} from "src/api/simulate-environments/rerunScenarios";
import {
  SAMPLE_PROOF_KIND,
  SAMPLE_PROVENANCE,
  SAMPLE_REGRADE,
} from "src/api/simulate-environments/_fixtures/detail/runHeader";

import SectionCard from "../../../components/SectionCard";
import EmptyState from "../../../components/EmptyState";
import UiOnlyFlag from "../../../components/UiOnlyFlag";
import useUiSample from "../../../hooks/useUiSample";
import StatusChip from "../StatusChip";
import StopRunControl from "../StopRunControl";
import AgentDiffersTag from "../../run/AgentDiffersTag";
import AddEvaluationDrawer from "../../evals/AddEvaluationDrawer";
import AddEvalsDrawer from "../../evals/AddEvalsDrawer";
import RunTraceTable from "./trace/RunTraceTable";
import CallDrawer from "./CallDrawer";
import AllEvaluationsDrawer from "./AllEvaluationsDrawer";
import RunEvalDialogs from "./RunEvalDialogs";
import { useRunEvalActions } from "./useRunEvalActions";
import FixMyAgentDrawer from "./fixmyagent/FixMyAgentDrawer";
import OptimizationRunsList from "./fixmyagent/OptimizationRunsList";
import LaunchOptimizationDrawer from "./fixmyagent/LaunchOptimizationDrawer";
import BetaChip from "./fixmyagent/BetaChip";
import { useSelfImprovementOpen } from "./fixmyagent/selfImprovement";
import RunAnalytics from "./RunAnalytics";
import RunSummaryChip, { ProofKindChip } from "./RunSummaryChip";
import ShareMenu from "./ShareMenu";
import RerunMenu from "./RerunMenu";
import RunProgressBar from "./RunProgressBar";
import useCallListNavigation from "./useCallListNavigation";
import useOpenCallParam from "./useOpenCallParam";
import {
  UI_ONLY_STARTED,
  agoLabel,
  costLabel,
  formatSpan,
  plural,
  runSpanMs,
  scenarioCount,
  unitFor,
} from "./runHeaderFormat";

// The run page's tabs, kept in `?tab=`. The calls table is the default and
// leaves the param off.
const RUN_TABS = ["tasks", "trials", "analytics"];

// What a tab shows when it throws while rendering: a retry, never a blank page.
function TabError({ resetErrorBoundary }) {
  return (
    <SectionCard>
      <EmptyState
        icon="solar:danger-triangle-linear"
        title="This tab failed to load"
        body="Something went wrong showing it. Try again."
        action={
          <Button size="small" variant="outlined" onClick={resetErrorBoundary}>
            Retry
          </Button>
        }
      />
    </SectionCard>
  );
}
TabError.propTypes = { resetErrorBoundary: PropTypes.func };

// One clause of the header's second line, after a middle dot.
const dot = (node) => (
  <>
    {" · "}
    {node}
  </>
);

/**
 * The designer-style run/execution detail page.
 *
 * Replaces the reused product `TestRunDetailView` at
 * `environments/:envId/runs/:testId/:executionId`. Phase 1 ports the designer's
 * `RunResults` chrome — the identity header and tab strip — over real run-level
 * data (`useRunDetail`).
 */
export default function RunDetail({
  env,
  envState,
  backed = false,
  testId,
  executionId,
  onStartRun,
  runStarting = false,
  creditBanner = null,
}) {
  const navigate = useNavigate();
  const location = useLocation();
  const [searchParams, setSearchParams] = useSearchParams();
  const tabParam = searchParams.get("tab");
  const tab = RUN_TABS.includes(tabParam) ? tabParam : "tasks";
  const setTab = (next) =>
    setSearchParams(
      (prev) => {
        const params = new URLSearchParams(prev);
        if (next === "tasks") params.delete("tab");
        else params.set("tab", next);
        return params;
      },
      { replace: true },
    );
  const showProofKind = useUiSample("proofKind");
  const showProvenance = useUiSample("provenance");
  const showRegrade = useUiSample("regrade");
  // Filters handed to the calls table from elsewhere on the page (an analytics
  // tile, a diagnosis issue). `seq` remounts the table so a repeat hand-off
  // re-applies even after the table's own filters were changed.
  const [tableHandoff, setTableHandoff] = useState({ filters: {}, seq: 0 });
  const openCallsWith = (filters) => {
    setTableHandoff((current) => ({ filters, seq: current.seq + 1 }));
    setTab("tasks");
  };
  const [addingEvals, setAddingEvals] = useState(false);
  // Whether the table has a call to tick for a re-run; a run made before calls
  // carried their scenario has none, so the header re-runs it whole.
  const [hasTickableCalls, setHasTickableCalls] = useState(null);
  const [allEvalsOpen, setAllEvalsOpen] = useState(false);
  // The exact query the trace table reads, or null when it isn't mounted.
  const [tableQuery, setTableQuery] = useState(null);
  // The open call, where it was opened from ("table", "analytics" or "link")
  // and, after a prev/next step, the table page it sits on so the table can
  // follow. Mirrored in `?rowId=`.
  const { openCall, showCall } = useOpenCallParam({
    executionId,
    tableQuery,
    tableShown: tab === "tasks",
  });
  const [debugging, setDebugging] = useState(false);
  const [launching, setLaunching] = useState(false);
  const [resolvingFailing, setResolvingFailing] = useState(false);
  const {
    identity,
    stats,
    isLoading: runLoading,
    error: runError,
    refetch: refetchRun,
  } = useRunDetail(testId, executionId, {
    envName: env?.name,
    callsShown: tab === "tasks",
  });
  const callNav = useCallListNavigation({
    executionId,
    openCall,
    tableQuery,
    live: identity?.status === "running",
    onStep: showCall,
  });
  // The All Evaluations drawer and the table's eval column menus share one
  // edit form, one confirm and one re-run request, so a re-run sent from
  // either holds both.
  const evalRun = useRunEvalActions({ envId: env?.id, executionId });
  const rerunInRun = useRerunCallsInRun(executionId);

  // The past self-improvement (optimization) runs for this execution — REAL,
  // scoped by test_execution_id. The Trials tab only appears once at least one
  // exists, so a run that was never optimized doesn't carry an empty tab.
  const { runs: optimizationRuns, isLoading: optimizationsLoading } =
    useOptimizationRuns(executionId);
  const hasTrials = optimizationRuns.length > 0;
  const selfImprovementOpen = useSelfImprovementOpen();
  // Keep the client bound: `useQueryClient().invalidateQueries` detached from
  // the client throws on `this.#queryCache` in react-query v5.
  const queryClient = useQueryClient();
  // The environment's selected evals carry each eval's description and labelled
  // inputs, which the All Evaluations drawer's expanded rows show.
  const envDetail = useQuery(
    harnessEnvironmentQuery(env?.id, { enabled: allEvalsOpen }),
  );
  const appliedEvals = envDetail.data?.evaluations?.selected;

  // Analytics and an open call drawer don't poll for grading, so both are
  // refreshed once, when grading on this run finishes and the scores are
  // final.
  const executionStatus = identity?.executionStatus ?? null;
  const previousStatus = useRef(executionStatus);
  useEffect(() => {
    const was = previousStatus.current;
    previousStatus.current = executionStatus;
    if (
      was === "evaluating" &&
      executionStatus &&
      executionStatus !== "evaluating"
    ) {
      queryClient.invalidateQueries({ queryKey: runAnalyticsKey(executionId) });
      queryClient.invalidateQueries({ queryKey: compareRunsKeyPrefix });
      queryClient.invalidateQueries({ queryKey: callDetailKeyPrefix });
    }
  }, [executionStatus, executionId, queryClient]);

  const openOptimization = (row) =>
    navigate(
      `${paths.dashboard.simulate.test}/${testId}/${executionId}/${row.id}`,
    );

  // A finished run reads as its verdict; until then, as where it is.
  const status =
    identity?.runState === "finished" ? runVerdict(stats) : identity?.runState;
  const loadFailed = !identity && !!runError;
  // A live run has no final results yet, so its header actions wait for it to
  // finish. Stop simulation is the exception and stays in its own control.
  const live =
    identity?.status === "running" || identity?.status === "cancelling";
  const unit = unitFor(stats.agentType, env?.surface);
  const finished = identity?.runState === "finished";
  // A finished run says when it finished; otherwise when it started.
  const finishedLabel = identity?.finishedAt
    ? agoLabel(identity.finishedAt)
    : null;
  const startedLabel = identity?.startedAt
    ? agoLabel(identity.startedAt)
    : null;
  const tookLabel = identity?.finishedAt
    ? formatSpan(runSpanMs(identity.startedAt, identity.finishedAt))
    : null;
  const scenarios = scenarioCount(identity, stats);
  const notScored = stats.notScored ?? 0;
  // The agent version this run was pinned to, else the one it was recorded with.
  const agentLabel =
    identity?.pinnedAgentVersion?.label ?? identity?.agentVersion ?? null;
  // The environment and scenario versions this run was pinned to, if any.
  const pinnedLabels = [
    identity?.environmentVersion?.label &&
      `env ${identity.environmentVersion.label}`,
    identity?.suiteVersion?.label && `scenarios ${identity.suiteVersion.label}`,
  ]
    .filter(Boolean)
    .map((label) => ` · ${label}`)
    .join("");
  // A re-run starts from the versions this run used.
  const rerunOptions = {
    initialPicks: {
      agent: identity?.pinnedAgentVersion?.id,
      environment: identity?.environmentVersion?.id,
      suite: identity?.suiteVersion?.id,
    },
  };

  // Back to whichever page opened the run (the runs tab, the compare page);
  // a run opened cold, from a pasted link, goes to its environment's runs.
  const back = () => {
    if (location.key !== "default") navigate(-1);
    else
      navigate(
        paths.dashboard.simulate.environments.workspaceTab(env.id, "runs"),
      );
  };

  // A run with failures re-runs them, unless its calls predate carrying their
  // scenario (the table says so); any other run re-runs whole. The failing
  // calls are fetched on click, so the Analytics tab offers it too.
  const rerunFailing =
    !!identity && stats.failed > 0 && hasTickableCalls !== false;
  const rerunWhole = !!identity && !rerunFailing;
  const rerunDisabledReason = live
    ? "Wait for this run to finish before re-running it"
    : runStarting
      ? "Starting the run…"
      : resolvingFailing
        ? "Finding the failing scenarios…"
        : null;
  const runFailingAgain = async () => {
    setResolvingFailing(true);
    try {
      const calls = await listMatchingCalls(executionId, {
        status: ["failed", "error"],
      });
      const keys = uniqueScenarioKeys(calls);
      if (keys.length) onStartRun?.(keys, identity?.trials || 1, rerunOptions);
      else
        enqueueSnackbar("No failing scenario could be found to re-run", {
          variant: "info",
        });
    } catch {
      enqueueSnackbar("Couldn't find the failing scenarios. Try again.", {
        variant: "error",
      });
    } finally {
      setResolvingFailing(false);
    }
  };
  const runWholeAgain = () =>
    onStartRun?.(
      identity?.scenarioIds || undefined,
      identity?.trials || 1,
      rerunOptions,
    );

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
        direction="row"
        alignItems="center"
        spacing={2}
        sx={{
          px: 3,
          py: 2,
          borderBottom: "1px solid",
          borderColor: "divider",
          flexShrink: 0,
        }}
      >
        <IconButton size="small" aria-label="Back" onClick={back}>
          <Iconify
            icon="solar:alt-arrow-left-linear"
            width={18}
            sx={{ color: "text.subtitle" }}
          />
        </IconButton>

        <Box flex={1} minWidth={0}>
          <Stack direction="row" alignItems="center" spacing={1}>
            {identity && (
              <Box
                sx={{
                  width: 24,
                  height: 24,
                  borderRadius: 0.875,
                  flexShrink: 0,
                  display: "grid",
                  placeItems: "center",
                  typography: "s3",
                  fontWeight: 700,
                  color: identity.color,
                  bgcolor: (t) =>
                    alpha(
                      identity.color,
                      t.palette.mode === "dark" ? 0.22 : 0.14,
                    ),
                }}
              >
                {identity.letter}
              </Box>
            )}
            {identity || !runLoading ? (
              <Typography
                noWrap
                sx={{
                  fontSize: 16,
                  lineHeight: "24px",
                  fontWeight: 600,
                  flexShrink: 0,
                }}
              >
                {identity ? `Run ${identity.ordinal}` : "Run"}
              </Typography>
            ) : (
              <Skeleton
                role="status"
                aria-label="Loading run"
                variant="text"
                width={180}
                sx={{ typography: "s1_2" }}
              />
            )}
            {identity?.agentDiffers && (
              <AgentDiffersTag
                agentVersion={identity.pinnedAgentVersion}
                environmentVersion={identity.environmentVersion}
              />
            )}
            {status && <StatusChip status={status} />}
            <RunSummaryChip
              executionId={executionId}
              enabled={!!identity && !live}
            />
            {showProofKind && <ProofKindChip proof={SAMPLE_PROOF_KIND} />}
            <StopRunControl
              executionId={executionId}
              stoppable={!!identity?.stoppable}
              label="Stop simulation"
            />
          </Stack>
          <Typography
            data-testid="run-subline"
            noWrap
            sx={{ typography: "s2", color: "text.subtitle" }}
          >
            {env.name}
            {agentLabel ? ` · agent ${agentLabel}` : ""}
            {pinnedLabels}
            {identity &&
              dot(
                `${plural(scenarios, "scenario")}${
                  (identity.trials || 1) > 1
                    ? ` × ${identity.trials} repeats`
                    : ""
                }`,
              )}
            {finished && finishedLabel
              ? dot(finishedLabel)
              : startedLabel
                ? dot(`started ${startedLabel}`)
                : null}
            {notScored > 0 &&
              dot(
                <CustomTooltip
                  show
                  arrow
                  size="small"
                  placement="bottom-start"
                  title={`${plural(notScored, unit)} ended in an error or without enough evidence to grade, so ${notScored === 1 ? "it isn't" : "they aren't"} scored. They count neither for nor against the agent.`}
                >
                  <Box component="span" sx={{ cursor: "default" }}>
                    {`${notScored} not scored`}
                  </Box>
                </CustomTooltip>,
              )}
            {showProvenance &&
              dot(
                <CustomTooltip
                  show
                  arrow
                  size="small"
                  placement="bottom-start"
                  title={SAMPLE_PROVENANCE.command}
                >
                  <Box component="span" sx={{ cursor: "default" }}>
                    {SAMPLE_PROVENANCE.label}
                  </Box>
                </CustomTooltip>,
              )}
            {showProvenance && (
              <UiOnlyFlag sx={{ ml: 0.75, verticalAlign: "middle" }} />
            )}
            {/* What the run took and cost: one hover, not two more clauses. */}
            {identity && (
              <CustomTooltip
                show
                arrow
                size="small"
                placement="bottom-start"
                title={
                  <Box sx={{ "& > div": { py: 0.125 } }}>
                    {finished && <div>{`Took ${tookLabel ?? "-"}`}</div>}
                    <div>{`Cost ${costLabel(stats.cost)}`}</div>
                  </Box>
                }
              >
                <Box
                  component="span"
                  aria-label="Run time and cost"
                  sx={{
                    display: "inline-flex",
                    verticalAlign: "middle",
                    ml: 0.75,
                    cursor: "default",
                  }}
                >
                  <Iconify
                    icon="solar:info-circle-linear"
                    width={14}
                    sx={{ color: "text.subtitle" }}
                  />
                </Box>
              </CustomTooltip>
            )}
          </Typography>
        </Box>

        {/* A backed run opens its evals, to grade again or add to; a
            client-only env keeps its store picker. */}
        <Button
          variant="outlined"
          size="small"
          onClick={() =>
            backed ? setAllEvalsOpen(true) : setAddingEvals(true)
          }
          // A re-grade reads as running, but its evals stay reachable so the
          // drawer can say why they are locked until grading finishes.
          disabled={live && executionStatus !== "evaluating"}
          startIcon={
            <Iconify
              icon={
                backed ? "solar:play-circle-linear" : "solar:add-circle-linear"
              }
              width={15}
            />
          }
          sx={{
            color: "text.primary",
            borderColor: "divider",
            typography: "s2",
            fontWeight: 600,
            flexShrink: 0,
          }}
        >
          {backed ? "Run evals" : "Add evals"}
        </Button>
        <ShareMenu executionId={executionId} disabled={live} />
        {(rerunFailing || rerunWhole) && onStartRun && (
          <RerunMenu
            failing={rerunFailing}
            scenarios={scenarios}
            runLabel={identity ? `run ${identity.ordinal}` : "this run"}
            trials={identity?.trials || 1}
            disabledReason={rerunDisabledReason}
            onRun={rerunFailing ? runFailingAgain : runWholeAgain}
            onRunWhole={runWholeAgain}
          />
        )}
        <Button
          variant="contained"
          color="primary"
          size="small"
          onClick={() => setDebugging(true)}
          disabled={live}
          startIcon={<Iconify icon="solar:magnifer-linear" width={15} />}
          sx={{ typography: "s2", fontWeight: 700, flexShrink: 0 }}
        >
          Debug failures
        </Button>
      </Stack>
      {live && identity && (
        <RunProgressBar
          stats={stats}
          startedAt={identity.startedAt}
          unit={unit}
        />
      )}
      {showRegrade && (
        <Stack
          direction="row"
          alignItems="center"
          spacing={1.25}
          sx={{
            px: 3,
            py: 0.875,
            borderBottom: "1px solid",
            borderColor: "divider",
            flexShrink: 0,
          }}
        >
          <Iconify
            icon="solar:restart-linear"
            width={16}
            sx={{ color: "text.subtitle", flexShrink: 0 }}
          />
          <Typography
            sx={{
              typography: "s2",
              color: "text.secondary",
              flex: 1,
              minWidth: 0,
            }}
          >
            {SAMPLE_REGRADE.line}
          </Typography>
          <UiOnlyFlag feature="regradeRun" />
          <Button
            size="small"
            variant="outlined"
            onClick={() =>
              enqueueSnackbar(UI_ONLY_STARTED, { variant: "info" })
            }
            sx={{
              color: "text.primary",
              borderColor: "divider",
              flexShrink: 0,
              whiteSpace: "nowrap",
            }}
          >
            {SAMPLE_REGRADE.action}
          </Button>
        </Stack>
      )}
      {creditBanner}

      <Box sx={{ flex: 1, minHeight: 0, overflow: "auto" }}>
        <Box sx={{ p: 2, pt: 0.5 }}>
          {loadFailed ? (
            <EmptyState
              icon="solar:danger-triangle-linear"
              title="Couldn't load this run"
              body="Check your connection and try again."
              action={
                <Button
                  size="small"
                  variant="outlined"
                  onClick={() => refetchRun?.()}
                >
                  Retry
                </Button>
              }
            />
          ) : (
            <>
              <CustomTabs
                value={tab}
                onChange={(_, v) => setTab(v)}
                sx={{
                  borderBottom: "1px solid",
                  borderColor: "divider",
                  mb: 2,
                  minHeight: 38,
                  // The app theme puts a 40px marginRight on every non-last Tab, and
                  // MUI's default 90px min-width centers short labels ("Analytics")
                  // inside a wide box — together they leave dead space between tabs.
                  // Zero the theme margin (matching its :not(:last-of-type) specificity)
                  // and size each tab to its label, then space them with one even
                  // gutter so the strip reads as a single seamless row.
                  "& .MuiTabs-flexContainer": { gap: 3 },
                  "& .MuiTab-root": { minWidth: "auto", px: 0 },
                  "& .MuiTab-root:not(:last-of-type)": { mr: 0 },
                }}
              >
                <Tab
                  value="tasks"
                  label={`Scenarios (${stats.total})`}
                  sx={{ minHeight: 38 }}
                />
                {hasTrials && (
                  <Tab
                    value="trials"
                    disabled={!selfImprovementOpen}
                    label={
                      <>
                        Trials ({optimizationRuns.length})
                        {!selfImprovementOpen && <BetaChip />}
                      </>
                    }
                    sx={{ minHeight: 38 }}
                  />
                )}
                <Tab
                  value="analytics"
                  label="Analytics"
                  sx={{ minHeight: 38 }}
                />
              </CustomTabs>

              {/* Wraps every tab body, so one that throws while it renders
                  shows a retry here instead of taking the page down. */}
              <ErrorBoundary resetKeys={[tab]} FallbackComponent={TabError}>
                {tab === "tasks" && (
                  <RunTraceTable
                    key={tableHandoff.seq}
                    executionId={executionId}
                    // From an eval cell, the call opens on that eval.
                    onOpenCall={(task, opened) =>
                      showCall({
                        task,
                        source: "table",
                        page: null,
                        focusEvalId: opened?.evalId ?? null,
                      })
                    }
                    onQueryChange={setTableQuery}
                    activeCallId={openCall?.task.id ?? null}
                    activePage={openCall?.page ?? null}
                    // Same gating as the All Evaluations drawer below; a client-only
                    // env has no server evals to act on.
                    evalActions={
                      backed
                        ? {
                            runTestId: testId,
                            canRun: executionStatus === "completed",
                            grading: executionStatus === "evaluating",
                            rerunPending: evalRun.isPending,
                            onRerun: evalRun.requestRerun,
                            onEdit: evalRun.requestEdit,
                          }
                        : null
                    }
                    initialFilters={tableHandoff.filters}
                    // Ticked calls re-run either inside this run (their rows are
                    // refilled in place) or as a new simulation, through the same
                    // start as any run: that one opens the new run.
                    onRerunScenarios={
                      onStartRun
                        ? (keys, trials) =>
                            onStartRun(keys, trials, rerunOptions)
                        : null
                    }
                    onRerunInRun={rerunInRun.mutate}
                    onTickableChange={setHasTickableCalls}
                    runTrials={identity?.trials || 1}
                    rerunDisabledReason={
                      live
                        ? "Wait for this run to finish before re-running calls"
                        : runStarting
                          ? "Starting the run…"
                          : rerunInRun.isPending
                            ? "Starting the re-run…"
                            : null
                    }
                  />
                )}

                {tab === "trials" && hasTrials && (
                  <SectionCard
                    title="Self-improvement runs"
                    subtitle="Optimizations launched against this run"
                    action={
                      <Button
                        variant="outlined"
                        size="small"
                        onClick={() => setLaunching(true)}
                        startIcon={
                          <Iconify
                            icon="solar:magic-stick-3-linear"
                            width={15}
                          />
                        }
                        sx={{
                          color: "text.primary",
                          borderColor: "divider",
                          typography: "s2",
                          fontWeight: 600,
                        }}
                      >
                        Launch optimization
                      </Button>
                    }
                    sx={{ border: "none" }}
                  >
                    <OptimizationRunsList
                      runs={optimizationRuns}
                      isLoading={optimizationsLoading}
                      onOpen={openOptimization}
                    />
                  </SectionCard>
                )}

                {tab === "analytics" && (
                  <RunAnalytics
                    executionId={executionId}
                    onOpenCall={(task) =>
                      showCall({ task, source: "analytics", page: null })
                    }
                    onOpenCalls={openCallsWith}
                  />
                )}
              </ErrorBoundary>
            </>
          )}
        </Box>
      </Box>

      {backed && (
        <AllEvaluationsDrawer
          open={allEvalsOpen}
          onClose={() => setAllEvalsOpen(false)}
          appliedEvals={appliedEvals}
          env={env}
          runTestId={testId}
          executionId={executionId}
          canRun={executionStatus === "completed"}
          grading={executionStatus === "evaluating"}
          rerunPending={evalRun.isPending}
          editOpen={evalRun.editOpen}
          onRerun={evalRun.requestRerun}
          onEdit={evalRun.requestEdit}
          onAddEvaluations={() => setAddingEvals(true)}
        />
      )}
      {backed && <RunEvalDialogs env={env} {...evalRun.dialogProps} />}

      {/* The same picker the Evaluations tab opens. Adding from here binds
          the eval to the environment exactly as the tab's add does and then
          grades this run's finished calls by name; the drawer shows the
          counts the run endpoint returns. Only a backed environment has a
          backend to call — a client/template env (reachable here via the
          `?mockRuns=1` QA switch) gets the same store-only picker the
          Evaluations tab falls back to. */}
      {backed ? (
        <AddEvaluationDrawer
          open={addingEvals}
          env={env}
          executionId={executionId}
          onClose={() => setAddingEvals(false)}
        />
      ) : (
        <AddEvalsDrawer
          open={addingEvals}
          onClose={() => setAddingEvals(false)}
          env={env}
          envState={envState}
          existingIds={
            new Set(
              (envState?.evals || []).map((e) =>
                typeof e === "string" ? e : e?.id,
              ),
            )
          }
          onAdd={() => setAddingEvals(false)}
        />
      )}

      <CallDrawer
        task={openCall?.task ?? null}
        executionId={backed ? executionId : null}
        agentType={stats.agentType}
        // Set only by an eval cell; a prev/next step opens the next call
        // without it.
        focusEvalId={openCall?.focusEvalId ?? null}
        onClose={() => showCall(null)}
        hasPrev={callNav.hasPrev}
        hasNext={callNav.hasNext}
        onPrev={callNav.onPrev}
        onNext={callNav.onNext}
      />

      <FixMyAgentDrawer
        open={debugging}
        onClose={() => setDebugging(false)}
        executionId={executionId}
        stats={stats}
        runLabel={identity ? `Run ${identity.ordinal}` : "Run"}
        onLaunch={() => {
          setDebugging(false);
          setLaunching(true);
        }}
        onViewCalls={(callExecutionIds) => {
          setDebugging(false);
          openCallsWith({ callExecutionId: callExecutionIds });
        }}
      />

      <LaunchOptimizationDrawer
        open={launching}
        onClose={() => setLaunching(false)}
        onLaunched={() => {
          queryClient.invalidateQueries({
            queryKey: ["agent-optimization-runs", executionId],
          });
          // Land on the run just started — the tab appears once the list refetches.
          setTab("trials");
        }}
      />
    </Box>
  );
}

RunDetail.propTypes = {
  env: PropTypes.shape({
    id: PropTypes.string,
    name: PropTypes.string,
    // "voice" or "chat": words a run's calls when the run doesn't say.
    surface: PropTypes.string,
    platform: PropTypes.shape({
      runTestId: PropTypes.string,
      testExecutionId: PropTypes.string,
    }),
  }).isRequired,
  // Client store state — only read for a non-backed env, to drive the
  // fixture-only `AddEvalsDrawer` fallback the same way it always has.
  envState: PropTypes.shape({
    evals: PropTypes.array,
  }),
  // Whether this environment has a real backend (`source === "harness"`,
  // computed once by EnvironmentWorkspace and threaded down through the same
  // Outlet-context route `envState` already takes). Gates which "Add evals"
  // drawer renders: the real API picker for a backed env, the client-store
  // picker otherwise.
  backed: PropTypes.bool,
  testId: PropTypes.string,
  executionId: PropTypes.string,
  onStartRun: PropTypes.func,
  // A run start is in flight; the table's Re-run waits for it.
  runStarting: PropTypes.bool,
  creditBanner: PropTypes.node,
};
