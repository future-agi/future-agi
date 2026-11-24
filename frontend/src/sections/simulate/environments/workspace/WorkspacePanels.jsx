import PropTypes from "prop-types";
import { Box, Stack, Tab, Tooltip } from "@mui/material";
import { useNavigate } from "react-router-dom";
import { CustomTabs } from "src/components/tabs/tabs";
import { paths } from "src/routes/paths";
import { useEnvironmentRuns } from "src/api/simulate-environments/runs";
import { useEnvironmentFixRuns } from "src/api/simulate-environments/fixRuns";
import OverviewPanel from "./overview/OverviewPanel";
import RlContractPanel from "./contract/RlContractPanel";
import WorldPanel from "./world/WorldPanel";
import ActorsTab from "./actors/ActorsTab";
import ScenariosStep from "./scenarios/ScenariosStep";
import EvalsStep from "./evals/EvalsStep";
import RunsSummary from "./runs/summary/RunsSummary";
import SettingsPanel from "./settings/SettingsPanel";
import VersionsPanel from "./versions/VersionsPanel";
import ImprovementsPanel from "./improvements/ImprovementsPanel";
import WorkspaceTabLabel from "./WorkspaceTabLabel";
import { BUILD_STATUS } from "../myEnvironments.constants";
import TabWaiting from "./TabWaiting";
import {
  TAB_WAITING_COPY,
  WORKSPACE_COPY,
  WORKSPACE_TABS,
} from "./workspace.constants";

// The shared rail + body switch. One instance serves the route workspace and
// the build page's in-place swap at 7/7, so it stays presentational: the active
// tab is controlled (`tab`/`onTabChange`), the setup gaps and item counts are
// passed in, and the only data it reads for itself is the Runs history (the live
// executions for a completed harness job — envState.runs is empty for those).
//
// The rail is the theme's own tabs, with no restyling. Numeric counts are hidden while
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
  // the tab badge and the Overview checklist agree with the Evaluations tab.
  // It defaults to `envState` for the build page, which has no backend detail
  // to overlay yet.
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
  runDialogOpen = false,
  canRun = false,
  runBlockedReason,
  contractChat = null,
  tabWaits = {},
}) {
  const navigate = useNavigate();
  const {
    runs = [],
    count: runCount,
    isLoading: runsLoading,
  } = useEnvironmentRuns(env, envState);

  // Editing an eval waits while any run is being graded. Only the newest page
  // of runs is read, and it polls while one is grading, so this clears itself.
  const grading = runs.some((run) => run.grading);

  // The runs list's total is what the Runs tab lists. The environment's own
  // count comes with the environment, so it stands in until the list loads;
  // it can run high once loaded, since a deleted run leaves its job behind.
  const runsTotal = runsLoading
    ? overviewCounts?.runs ?? 0
    : runCount ?? runs.length;
  // Runs appears once the environment has a run. While the list is still
  // loading, a deep link to ?tab=runs keeps the tab so it does not bounce to
  // the Overview and back.
  const showRuns = runsTotal > 0 || (runsLoading && tab === "runs");
  // Improvements appears once the environment has a fix, the same way Runs does.
  const { count: fixCount, isLoading: fixesLoading } = useEnvironmentFixRuns(
    env?.id,
  );
  const showImprovements =
    fixCount > 0 || (fixesLoading && tab === "improvements");
  // Versions appears only while versioning is switched on for this environment.
  const showVersions = Boolean(env?.versioning?.enabled);
  const tabs = WORKSPACE_TABS.filter(
    (t) =>
      (t.id !== "runs" || showRuns) &&
      (t.id !== "improvements" || showImprovements) &&
      (t.id !== "versions" || showVersions) &&
      (t.id !== "actors" || Boolean(env?.actorsTab)),
  );

  // Default landing is the Overview, which is also the first tab in the rail.
  const current =
    tabs.find((t) => t.id === tab) || tabs.find((t) => t.id === "overview");

  const go = (tabId) => {
    if (!tabs.some((t) => t.id === tabId)) return;
    onTabChange(tabId);
  };

  const openRun = (run) => {
    const runTestId = env?.platform?.runTestId;
    if (!runTestId || !run?.executionId) return;
    navigate(
      paths.dashboard.simulate.environments.execution(
        env.id,
        runTestId,
        run.executionId,
      ),
    );
  };

  // The rest come from the injected client-state counts. A null `counts`
  // (builder still streaming) hides every numeric badge.
  const badgeCount = (badge) => {
    if (!counts) return null;
    if (badge === "runs") return runsTotal;
    if (badge === "improvements") return fixCount;
    return counts[badge] ?? 0;
  };

  const buildLock =
    env?.buildStatus === BUILD_STATUS.BUILDING
      ? WORKSPACE_COPY.buildLock.building
      : env?.buildStatus === BUILD_STATUS.FAILED
        ? WORKSPACE_COPY.buildLock.stopped
        : null;

  const renderBody = () => {
    const wait = tabWaits[current.id];
    if (wait)
      return (
        <TabWaiting tabId={current.id} tabLabel={current.label} wait={wait} />
      );
    switch (current.id) {
      case "world":
        return (
          <WorldPanel
            env={env}
            envState={envState}
            graphData={graphData}
            chat={locked ? null : contractChat}
          />
        );
      case "contract":
        return (
          <RlContractPanel
            env={env}
            backed={backed}
            locked={locked}
            chat={contractChat}
            scenarios={serverEnvState?.scenarios || envState?.scenarios || []}
            onGo={go}
          />
        );
      case "actors":
        return <ActorsTab env={env} locked={locked} lockReason={buildLock} />;
      case "scenarios":
        return (
          <ScenariosStep
            env={env}
            envState={envState}
            patch={patch}
            locked={locked}
            lockReason={buildLock}
            onFork={onFork}
            onStartRun={onStartRun}
            runDialogOpen={runDialogOpen}
            canRun={canRun}
            runBlockedReason={runBlockedReason}
          />
        );
      case "evals":
        return (
          <EvalsStep
            env={env}
            envState={envState}
            patch={patch}
            onGo={go}
            locked={locked}
            backed={backed}
            grading={grading}
            onFork={onFork}
            scenarioCount={counts?.scenarios}
          />
        );
      case "runs":
        // The summary's eval columns come from the runs' own scores, so it
        // only needs the store for its scenario count.
        return (
          <RunsSummary
            key={env.id}
            env={env}
            envState={envState}
            scenarioCount={counts?.scenarios}
            onOpenRun={openRun}
            onGo={go}
          />
        );
      case "improvements":
        return <ImprovementsPanel env={env} />;
      case "versions":
        return <VersionsPanel env={env} />;
      case "settings":
        return <SettingsPanel env={env} locked={locked} backed={backed} />;
      default:
        return (
          // The next-steps checklist's "Add evaluations" step and its CTA
          // count read `envState.evals`. On a backed env that set is the
          // server's, not the client store's, so the Overview gets the same
          // overlaid state the tab badge does — otherwise it can offer
          // "Add evaluations (0)" while the Evaluations tab lists eight.
          <OverviewPanel
            env={env}
            envState={serverEnvState}
            onGo={go}
            agentConnected={!!envState?.agent}
            locked={locked}
            counts={overviewCounts}
            backedWorld={overviewWorld}
            onFork={onFork}
            buildMode={buildMode}
            waits={tabWaits}
          />
        );
    }
  };

  return (
    <Stack sx={{ height: "100%", minHeight: 0 }}>
      <Box
        sx={{
          flexShrink: 0,
          borderBottom: "1px solid",
          borderColor: "divider",
        }}
      >
        <CustomTabs
          sx={{ boxShadow: "none" }}
          value={current.id}
          onChange={(_, v) => onTabChange(v)}
          variant="scrollable"
          scrollButtons={false}
        >
          {tabs.map((t) => {
            const wait = tabWaits[t.id];
            const tip = wait ? TAB_WAITING_COPY.tabTip(wait) : t.hint;
            const label = (
              <Box
                component="span"
                sx={
                  wait?.now
                    ? { color: "text.primary", fontWeight: 500 }
                    : wait
                      ? { color: "text.tertiary" }
                      : undefined
                }
              >
                <WorkspaceTabLabel
                  label={t.label}
                  count={t.badge && !wait ? badgeCount(t.badge) : null}
                  gaps={wait ? null : gapsByTab?.[t.id]}
                />
              </Box>
            );
            return (
              <Tab
                key={t.id}
                value={t.id}
                label={
                  tip ? (
                    <Tooltip
                      title={tip}
                      describeChild
                      arrow
                      placement="bottom"
                      enterDelay={400}
                      enterNextDelay={200}
                      slotProps={{ tooltip: { sx: { maxWidth: 320 } } }}
                    >
                      <span>{label}</span>
                    </Tooltip>
                  ) : (
                    label
                  )
                }
              />
            );
          })}
        </CustomTabs>
      </Box>

      <Box sx={{ flex: 1, minWidth: 0, minHeight: 0, overflow: "auto" }}>
        {renderBody()}
      </Box>
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
    versioning: PropTypes.shape({ enabled: PropTypes.bool }),
    actorsTab: PropTypes.oneOf(["edit", "list"]),
    buildStatus: PropTypes.string,
  }).isRequired,
  envState: PropTypes.shape({
    agent: PropTypes.shape({ name: PropTypes.string }),
    scenarios: PropTypes.arrayOf(PropTypes.shape({ id: PropTypes.string })),
    evals: PropTypes.arrayOf(
      PropTypes.oneOfType([
        PropTypes.string,
        PropTypes.shape({ id: PropTypes.string }),
      ]),
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
  // Its real capability-graph data (tools/flows/personas/guardrails) for World & tools.
  graphData: PropTypes.object,
  // Starts a run scoped to the scenario selection × trials — (ids, trials).
  onStartRun: PropTypes.func,
  runDialogOpen: PropTypes.bool,
  // Whether the env is runnable; gates the scenarios selection-bar Run.
  canRun: PropTypes.bool,
  // Why Run is unavailable, for the Scenarios selection bar's disabled Run.
  runBlockedReason: PropTypes.string,
  tabWaits: PropTypes.objectOf(
    PropTypes.shape({
      phase: PropTypes.string.isRequired,
      now: PropTypes.bool,
      stopped: PropTypes.bool,
    }),
  ),
  contractChat: PropTypes.shape({
    send: PropTypes.func,
    busy: PropTypes.bool,
    live: PropTypes.bool,
  }),
};
