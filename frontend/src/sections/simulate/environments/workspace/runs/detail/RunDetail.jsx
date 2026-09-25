import PropTypes from "prop-types";
import { useState } from "react";
import { enqueueSnackbar } from "notistack";
import { useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography, Button, IconButton, Tab } from "@mui/material";

import Iconify from "src/components/iconify";
import { CustomTabs } from "src/components/tabs/tabs";
import { paths } from "src/routes/paths";
import { fToNow } from "src/utils/format-time";
import {
  useRunDetail,
  useOptimizationRuns,
} from "src/api/simulate-environments/runDetail";
import { exportRunResults } from "src/api/simulate-environments/runAnalytics";

import SectionCard from "../../../components/SectionCard";
import StatusChip from "../StatusChip";
import StopRunControl from "../StopRunControl";
import AddEvaluationDrawer from "../../evals/AddEvaluationDrawer";
import AddEvalsDrawer from "../../evals/AddEvalsDrawer";
import RunTraceTable from "./trace/RunTraceTable";
import CallDrawer from "./CallDrawer";
import FixMyAgentDrawer from "./fixmyagent/FixMyAgentDrawer";
import OptimizationRunsList from "./fixmyagent/OptimizationRunsList";
import LaunchOptimizationDrawer from "./fixmyagent/LaunchOptimizationDrawer";
import RunAnalytics from "./RunAnalytics";
import useCallListNavigation from "./useCallListNavigation";

// Terminal execution failures/cancellations outrank call-level outcomes.
// Otherwise mixed pass/fail results are a completed run with findings.
function headerStatus(identity, stats) {
  // No verdict until the run resolves: while loading, identity is null and the
  // zeroed stats would otherwise read as "Failed".
  if (!identity) return null;
  if (identity.status === "running" || identity.status === "cancelling") {
    return identity.status;
  }
  if (identity.status === "failed" || identity.status === "cancelled") {
    return identity.status;
  }
  if (stats.passed === 0) return "failed";
  if (stats.failed === 0) return "passed";
  return "completed";
}

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
}) {
  const navigate = useNavigate();
  const [tab, setTab] = useState("tasks");
  const [analyticsFilters, setAnalyticsFilters] = useState({});
  const [addingEvals, setAddingEvals] = useState(false);
  // The open call, where it was opened from ("table" or "analytics") and, after
  // a prev/next step, the table page it sits on so the table can follow.
  const [openCall, setOpenCall] = useState(null);
  // The exact query the trace table reads, or null when it isn't mounted.
  const [tableQuery, setTableQuery] = useState(null);
  const [debugging, setDebugging] = useState(false);
  const [launching, setLaunching] = useState(false);
  const [exporting, setExporting] = useState(false);
  const { identity, stats } = useRunDetail(testId, executionId, {
    envName: env?.name,
  });
  const callNav = useCallListNavigation({
    executionId,
    openCall,
    tableQuery,
    live: identity?.status === "running",
    onStep: setOpenCall,
  });

  // The past self-improvement (optimization) runs for this execution — REAL,
  // scoped by test_execution_id. The Trials tab only appears once at least one
  // exists, so a run that was never optimized doesn't carry an empty tab.
  const { runs: optimizationRuns, isLoading: optimizationsLoading } =
    useOptimizationRuns(executionId);
  const hasTrials = optimizationRuns.length > 0;
  // Keep the client bound: `useQueryClient().invalidateQueries` detached from
  // the client throws on `this.#queryCache` in react-query v5.
  const queryClient = useQueryClient();

  const openOptimization = (row) =>
    navigate(
      `${paths.dashboard.simulate.test}/${testId}/${executionId}/${row.id}`,
    );

  const status = headerStatus(identity, stats);
  // finishedAt is a known gap (the executions row carries no end time), so the
  // sub-line reports when the run STARTED rather than inventing a finish.
  const startedLabel = identity?.startedAt ? fToNow(identity.startedAt) : "";

  const back = () =>
    navigate(
      paths.dashboard.simulate.environments.workspaceTab(env.id, "runs"),
    );

  const exportResults = async () => {
    setExporting(true);
    try {
      await exportRunResults(executionId);
      enqueueSnackbar("Run export downloaded", { variant: "success" });
    } catch {
      enqueueSnackbar("Run export could not be generated", {
        variant: "error",
      });
    } finally {
      setExporting(false);
    }
  };

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
        <IconButton size="small" onClick={back}>
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
            <Typography noWrap sx={{ typography: "s1_2", fontWeight: 700 }}>
              {identity
                ? `Run ${identity.ordinal} · agent ${identity.agentVersion ?? "-"}`
                : "Run complete"}
            </Typography>
            {status && <StatusChip status={status} />}
            <StopRunControl
              executionId={executionId}
              stoppable={!!identity?.stoppable}
              label="Stop simulation"
            />
          </Stack>
          <Typography noWrap sx={{ typography: "s2", color: "text.subtitle" }}>
            {env.name} · {stats.total} tasks
            {startedLabel ? ` · started ${startedLabel}` : ""}
          </Typography>
        </Box>

        <Button
          variant="outlined"
          size="small"
          onClick={() => setAddingEvals(true)}
          startIcon={<Iconify icon="solar:add-circle-linear" width={15} />}
          sx={{
            color: "text.primary",
            borderColor: "divider",
            typography: "s2",
            fontWeight: 600,
          }}
        >
          Add evals
        </Button>
        <Button
          variant="outlined"
          size="small"
          onClick={exportResults}
          disabled={exporting}
          startIcon={
            <Iconify icon="solar:download-minimalistic-linear" width={15} />
          }
          sx={{
            color: "text.primary",
            borderColor: "divider",
            typography: "s2",
            fontWeight: 600,
          }}
        >
          {exporting ? "Exporting…" : "Export"}
        </Button>
        <Button
          variant="outlined"
          size="small"
          startIcon={<Iconify icon="solar:refresh-linear" width={15} />}
          onClick={() =>
            onStartRun?.(
              identity?.scenarioIds || undefined,
              identity?.trials || 1,
            )
          }
          sx={{
            color: "text.primary",
            borderColor: "divider",
            typography: "s2",
            fontWeight: 600,
          }}
        >
          Run again
        </Button>
        <Button
          variant="contained"
          color="primary"
          size="small"
          onClick={() => setDebugging(true)}
          startIcon={<Iconify icon="solar:magnifer-linear" width={15} />}
          sx={{ typography: "s2", fontWeight: 700 }}
        >
          Debug failures
        </Button>
      </Stack>

      <Box sx={{ flex: 1, minHeight: 0, overflow: "auto" }}>
        <Box sx={{ p: 2 }}>
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
              label={`Test runs (${stats.total})`}
              sx={{ minHeight: 38 }}
            />
            {hasTrials && (
              <Tab
                value="trials"
                label={`Trials (${optimizationRuns.length})`}
                sx={{ minHeight: 38 }}
              />
            )}
            <Tab value="analytics" label="Analytics" sx={{ minHeight: 38 }} />
          </CustomTabs>

          {tab === "tasks" && (
            <RunTraceTable
              executionId={executionId}
              onOpenCall={(task) =>
                setOpenCall({ task, source: "table", page: null })
              }
              onQueryChange={setTableQuery}
              activeCallId={openCall?.task.id ?? null}
              activePage={openCall?.page ?? null}
              initialFilters={analyticsFilters}
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
                    <Iconify icon="solar:magic-stick-3-linear" width={15} />
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
                setOpenCall({ task, source: "analytics", page: null })
              }
              onOpenCalls={(filters) => {
                setAnalyticsFilters(filters);
                setTab("tasks");
              }}
            />
          )}
        </Box>
      </Box>

      {/* The same picker the Evaluations tab opens. Adding from here binds
          the eval to the environment exactly as the tab's add does and then
          queues this run's finished calls that hold no verdict for it; the
          drawer shows the counts the 202 returns. Only a backed environment
          has a backend to call — a client/template env (reachable here
          via the `?mockRuns=1` QA switch) gets the same store-only picker the
          Evaluations tab falls back to. */}
      {backed ? (
        <AddEvaluationDrawer
          open={addingEvals}
          env={env}
          executionId={executionId}
          completedCallsCount={stats.completed}
          onClose={() => setAddingEvals(false)}
        />
      ) : (
        <AddEvalsDrawer
          open={addingEvals}
          onClose={() => setAddingEvals(false)}
          env={env}
          envState={envState}
          existingIds={new Set((envState?.evals || []).map((e) => (typeof e === "string" ? e : e?.id)))}
          onAdd={() => setAddingEvals(false)}
        />
      )}

      <CallDrawer
        task={openCall?.task ?? null}
        agentType={stats.agentType}
        onClose={() => setOpenCall(null)}
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
        onLaunch={() => {
          setDebugging(false);
          setLaunching(true);
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
};
