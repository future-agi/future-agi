import PropTypes from "prop-types";
import { Box, Stack, Tab } from "@mui/material";
import { useNavigate } from "react-router-dom";
import { CustomTabs } from "src/components/tabs/tabs";
import { paths } from "src/routes/paths";
import { useEnvironmentRuns } from "src/api/simulate-environments/runs";
import { ENV_TABS_SX } from "../environmentOptions";
import OverviewPanel from "./overview/OverviewPanel";
import RlContractPanel from "./contract/RlContractPanel";
import ScenariosStep from "./scenarios/ScenariosStep";
import EvalsStep from "./evals/EvalsStep";
import RunsPanel from "./runs/RunsPanel";
import SettingsPanel from "./settings/SettingsPanel";
import WorkspaceTabLabel from "./WorkspaceTabLabel";
import { WORKSPACE_TABS } from "./workspace.constants";

// The shared rail + body switch. One instance serves the route workspace and
// the build page's in-place swap at 7/7, so it stays presentational: the active
// tab is controlled (`tab`/`onTabChange`), the setup gaps and item counts are
// passed in, and the only data it reads for itself is the Runs history (the live
// executions for a completed harness job — envState.runs is empty for those).
//
// The rail reuses the product tab treatment Phase-1 established (ENV_TABS_SX)
// rather than the designer's CustomTabs px:1. Numeric counts are hidden while
// the builder streams — the parent passes `counts={null}` then. A deep-linked
// run detail is a full page (the workspace early-returns the Outlet), so the
// panels never host the run detail themselves.
export default function WorkspacePanels({
  env,
  // The client store as the workspace holds it — what every panel reads.
  envState,
  // The same state with a backed environment's real applied evals
  // (`evaluations.selected` off the environment detail) overlaid, exactly as
  // EnvironmentWorkspace already computes it for the tab badges and the setup
  // gaps. Every panel that counts or names applied evals reads this one, so
  // the tab badge, the Overview checklist and the Runs pre-flight tile all
  // agree with the Evaluations tab. It defaults to `envState` for the build
  // page, which has no backend detail to overlay yet.
  serverEnvState = envState,
  patch,
  tab,
  onTabChange,
  locked = false,
  backed = false,
  onFork,
  buildMode = false,
  gapsByTab,
  counts,
  overviewCounts,
  overviewWorld,
  graphData,
  onStartRun,
  canRun = false,
}) {
  const navigate = useNavigate();
  const { runs } = useEnvironmentRuns(env, envState);

  // Default landing is the Overview, which is also the first tab in the rail.
  const current =
    WORKSPACE_TABS.find((t) => t.id === tab) ||
    WORKSPACE_TABS.find((t) => t.id === "overview");

  const go = (tabId) => {
    if (!WORKSPACE_TABS.some((t) => t.id === tabId)) return;
    onTabChange(tabId);
  };

  const openRun = (run) => {
    const runTestId = env?.platform?.runTestId;
    if (!runTestId || !run?.executionId) return;
    navigate(paths.dashboard.simulate.environments.execution(env.id, runTestId, run.executionId));
  };

  // Runs is badged from the live executions (envState.runs is empty for a
  // harness env); the rest come from the injected client-state counts. A null
  // `counts` (builder still streaming) hides every numeric badge.
  const badgeCount = (badge) => {
    if (!counts) return null;
    if (badge === "runs") return runs.length;
    return counts[badge] ?? 0;
  };

  const renderBody = () => {
    switch (current.id) {
      case "contract":
        return <RlContractPanel env={env} envState={envState} patch={patch} onGo={go} locked={locked} onFork={onFork} graphData={graphData} />;
      case "scenarios":
        return <ScenariosStep env={env} envState={envState} patch={patch} locked={locked} onFork={onFork} onStartRun={onStartRun} canRun={canRun} />;
      case "evals":
        return <EvalsStep env={env} envState={envState} patch={patch} onGo={go} locked={locked} backed={backed} onFork={onFork} />;
      case "runs":
        return (
          // The pre-flight "evals applied" tile counts and names the server's
          // `evaluations.selected` on a backed env — the same list the
          // Evaluations tab renders — so it is handed the overlaid state.
          <RunsPanel
            env={env}
            envState={serverEnvState}
            runs={runs}
            onStart={() => onStartRun?.(undefined, 1)}
            onOpenRun={openRun}
            onGo={go}
          />
        );
      case "settings":
        return <SettingsPanel env={env} locked={locked} backed={backed} />;
      default:
        return (
          // The next-steps checklist's "Add evaluations" step and its CTA
          // count read `envState.evals`. On a backed env that set is the
          // server's, not the client store's, so the Overview gets the same
          // overlaid state the tab badge and the Runs tile do — otherwise it
          // can offer "Add evaluations (0)" while the Evaluations tab lists
          // eight.
          <OverviewPanel
            env={env}
            envState={serverEnvState}
            patch={patch}
            onGo={go}
            agentConnected={!!envState?.agent}
            locked={locked}
            counts={overviewCounts}
            backedWorld={overviewWorld}
            onFork={onFork}
            buildMode={buildMode}
          />
        );
    }
  };

  return (
    <Stack sx={{ height: "100%", minHeight: 0 }}>
      <Box sx={{ flexShrink: 0, borderBottom: "1px solid", borderColor: "divider" }}>
        <CustomTabs
          value={current.id}
          onChange={(_, v) => onTabChange(v)}
          variant="scrollable"
          scrollButtons={false}
          sx={ENV_TABS_SX}
        >
          {WORKSPACE_TABS.map((t) => (
            <Tab
              key={t.id}
              value={t.id}
              label={
                <WorkspaceTabLabel
                  label={t.label}
                  count={t.badge ? badgeCount(t.badge) : null}
                  gaps={gapsByTab?.[t.id]}
                />
              }
            />
          ))}
        </CustomTabs>
      </Box>

      <Box sx={{ flex: 1, minWidth: 0, minHeight: 0, overflow: "auto" }}>{renderBody()}</Box>
    </Stack>
  );
}

const GAP_SHAPE = PropTypes.arrayOf(
  PropTypes.shape({ id: PropTypes.string, title: PropTypes.string }),
);

WorkspacePanels.propTypes = {
  env: PropTypes.shape({
    id: PropTypes.string,
    name: PropTypes.string,
    surface: PropTypes.string,
    platform: PropTypes.shape({
      runTestId: PropTypes.string,
      testExecutionId: PropTypes.string,
    }),
  }).isRequired,
  envState: PropTypes.shape({
    agent: PropTypes.shape({ name: PropTypes.string }),
    scenarios: PropTypes.arrayOf(PropTypes.shape({ id: PropTypes.string })),
    evals: PropTypes.arrayOf(
      PropTypes.oneOfType([PropTypes.string, PropTypes.shape({ id: PropTypes.string })]),
    ),
    runs: PropTypes.arrayOf(PropTypes.shape({ id: PropTypes.string })),
  }).isRequired,
  // Same shape as envState; on a backed env its `evals` are the environment
  // detail's selected entries (a catalogue entry plus `id` and `runnable`).
  serverEnvState: PropTypes.object,
  patch: PropTypes.func.isRequired,
  tab: PropTypes.string,
  onTabChange: PropTypes.func.isRequired,
  locked: PropTypes.bool,
  backed: PropTypes.bool,
  onFork: PropTypes.func,
  buildMode: PropTypes.bool,
  gapsByTab: PropTypes.objectOf(GAP_SHAPE),
  counts: PropTypes.shape({
    scenarios: PropTypes.number,
    evals: PropTypes.number,
  }),
  // The environment detail's real counts for the Overview summary tiles on a
  // backed env.
  overviewCounts: PropTypes.object,
  // Its real world content (stores/amendments/dependencies) for the Overview.
  overviewWorld: PropTypes.object,
  // Its real capability-graph data (tools/flows/personas/guardrails) for Contract.
  graphData: PropTypes.object,
  // Starts a run scoped to the scenario selection × trials — (ids, trials).
  onStartRun: PropTypes.func,
  // Whether the env is runnable; gates the scenarios selection-bar Run.
  canRun: PropTypes.bool,
};
