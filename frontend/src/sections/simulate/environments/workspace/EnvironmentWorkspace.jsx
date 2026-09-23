import PropTypes from "prop-types";
import { useEffect, useState } from "react";
import { alpha } from "@mui/material/styles";
import { Box, Button, Stack, Typography, IconButton } from "@mui/material";
import {
  useNavigate,
  useParams,
  useMatch,
  Outlet,
} from "react-router-dom";

import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { paths } from "src/routes/paths";
import { useQuery } from "@tanstack/react-query";
import {
  useEnvironment,
  canRunHeader,
  stageOutputsToWorld,
  harnessEnvironmentQuery,
} from "src/api/simulate-environments/environment";
import { useBuildProgress } from "src/api/simulate-environments/buildProgress";
import { useWorkspaceChat } from "src/api/simulate-environments/workspaceChat";
import { runSelectionTarget } from "src/api/simulate-environments/runs";

import { useEnvironmentsStore } from "../store/useEnvironmentsStore";
import { useEnvState } from "../store/envState";
import { BUILD_STATUS } from "../myEnvironments.constants";
import SectionCard from "../components/SectionCard";
import EmptyState from "../components/EmptyState";
import BuilderConsole from "../buildEnvironment/console/BuilderConsole";
import { CONSOLE_COPY } from "../buildEnvironment/build.constants";
import {
  subscribeScenarioSelection,
  clearScenarioSelection,
} from "../buildEnvironment/console/scenarioSelectionBus";
import BuildingStage from "../buildEnvironment/building/BuildingStage";
import WorkspaceHeader from "./WorkspaceHeader";
import SystemBanners from "./SystemBanners";
import VersionBar from "./VersionBar";
import TemplateLockBanner from "./TemplateLockBanner";
import WorkspacePanels from "./WorkspacePanels";
import useWorkspaceTab from "./helpers/useWorkspaceTab";
import { gapsByTab, counts } from "./helpers/workspaceGaps";
import { forkEnvironment as buildFork } from "./helpers/forkEnvironment";
import { CHIPS_BY_TAB, WORKSPACE_COPY } from "./workspace.constants";

const EXECUTION_PATTERN = {
  path: `${paths.dashboard.simulate.environments.root}/:envId/runs/:testId/:executionId`,
  end: false,
};

// The named reason a run is blocked, in the designer's order: an agent first,
// then scenarios, then evals, then a generic fallback. Only read when the header
// button is disabled, so it never shows for a runnable environment.
// Evals are intentionally not a run blocker — canRun needs only an agent and
// scenarios, so a run without evals is allowed (it just isn't scored).
const blockedReason = (envState) => {
  if (!envState.agent) return WORKSPACE_COPY.runBlocked.agent;
  if (!envState.scenarios?.length) return WORKSPACE_COPY.runBlocked.scenarios;
  return WORKSPACE_COPY.runBlocked.generic;
};

/**
 * The environment workspace at `environments/:envId`.
 *
 * Resolves the id (an adopted client env, a harness job, or a prebuilt
 * template) and lays out the shared workspace: header, system banners, the
 * version pairing strip, the builder console on the left and the tabbed panels
 * on the right — the same composition the build page swaps in at 7/7. A deep
 * link to `runs/:testId/:executionId` renders the run detail as its own full
 * page instead (the workspace chrome is replaced by the nested Outlet).
 */
export default function EnvironmentWorkspace() {
  const navigate = useNavigate();
  const { envId } = useParams();
  const { env, source, bootstrapState, notFound, error, refetch } = useEnvironment(envId);
  // A real (backed) env's applied evals live on §6 (evaluations.selected), not
  // the client store — so the Evaluations tab count + the "no evals" gap must
  // read §6, or they'd disagree with the panel and never clear after an add.
  // Shares the ["harness-environment", id] cache the Evals tab already uses.
  const backed = source === "harness";
  const evalDetailQuery = useQuery(harnessEnvironmentQuery(envId, { enabled: backed }));
  // While the job is still deriving, the harness bootstrap is only a placeholder
  // (generated-pool scenarios, a v1 stub) — and useEnvState seeds byEnv once, so
  // seeding it now would lock that placeholder in even after the real world lands.
  // Seed only once the env is ready; the build view below never reads envState.
  const building = !!env && env.buildStatus === BUILD_STATUS.BUILDING;
  const buildFailed = !!env && env.buildStatus === BUILD_STATUS.FAILED;
  const { envState, patch, canRun } = useEnvState(
    envId,
    building || buildFailed ? undefined : bootstrapState,
  );
  const { tab, setTab } = useWorkspaceTab();
  const chat = useWorkspaceChat(env, { source });
  const registerFork = useEnvironmentsStore((s) => s.forkEnvironment);
  const selection = useScenarioSelection();

  // While the job is still deriving, this page IS the build experience: the same
  // milestone poll the /build page used, now keyed off the resolved env id. The
  // env, the pipeline and the hero all read one shared ["harness-job", envId]
  // query, so a stage landing flips buildStatus building→ready and swaps the body
  // in place — no adopt hand-off, and a refresh mid-build resumes here.
  const progress = useBuildProgress({
    envId,
    agentRef: env?.name,
    enabled: building || buildFailed,
    mockMode: false,
  });
  // Real derived world from the running job's stage outputs (never the MOCK_WORLD
  // overlay) — the sandbox hero shows real tools/rules/tables as they land, a
  // neutral skeleton before.
  const derivedWorld = stageOutputsToWorld(progress.job?.stage_outputs || []).world;

  const executionMatch = useMatch(EXECUTION_PATTERN);

  if (notFound) {
    return (
      <Box sx={{ p: 2 }}>
        <EmptyState
          icon="solar:danger-triangle-linear"
          title={WORKSPACE_COPY.notFound.title}
          body={WORKSPACE_COPY.notFound.body}
          action={
            <Button
              variant="contained"
              color="primary"
              size="small"
              onClick={() => navigate(paths.dashboard.simulate.environments.root)}
            >
              {WORKSPACE_COPY.notFound.action}
            </Button>
          }
        />
      </Box>
    );
  }

  // Resolve failed for a reason other than a clean 404 (server/network error):
  // a recoverable error state with a retry, never a silent blank.
  if (error) {
    return (
      <Box sx={{ p: 2 }}>
        <EmptyState
          icon="solar:danger-triangle-linear"
          title={WORKSPACE_COPY.loadError.title}
          body={WORKSPACE_COPY.loadError.body}
          action={
            <Stack direction="row" spacing={1}>
              <Button variant="outlined" size="small" onClick={() => refetch?.()}>
                {WORKSPACE_COPY.loadError.retry}
              </Button>
              <Button
                variant="contained"
                color="primary"
                size="small"
                onClick={() => navigate(paths.dashboard.simulate.environments.root)}
              >
                {WORKSPACE_COPY.notFound.action}
              </Button>
            </Stack>
          }
        />
      </Box>
    );
  }

  // Still resolving the id — a blank centred box, never a flash of "not found".
  if (!env) {
    return <Box sx={{ height: "100%", minHeight: 420, display: "grid", placeItems: "center" }} />;
  }

  // Still deriving OR terminally failed: the workspace header over the two-pane
  // build stage (console + hero/pipeline). A failed build keeps this layout —
  // the chat stays usable and the pipeline shows which stage failed — but the
  // hero animation freezes and the header reads "Failed" (LivePill), rather than
  // a dead-end error page. Run is gated off and Fork is hidden (locked) until it
  // goes Live, at which point buildStatus flips and the branch below takes over.
  if (building || buildFailed) {
    return (
      <Box sx={{ display: "flex", flexDirection: "column", height: "100%", minHeight: 0 }}>
        <WorkspaceHeader
          env={env}
          envState={envState}
          patch={patch}
          canRun={false}
          runBlockedReason={buildFailed ? WORKSPACE_COPY.failedTooltip : WORKSPACE_COPY.buildingTooltip}
          locked
        />
        <Box sx={{ flex: 1, minHeight: 0, overflow: "hidden", p: 2 }}>
          <BuildingStage
            progress={progress}
            chat={chat}
            env={env}
            envState={envState}
            patch={patch}
            primed={false}
            source={env.name}
            world={derivedWorld}
          />
        </Box>
      </Box>
    );
  }

  // A deep-linked run detail is its own full page: the workspace chrome (header,
  // builder console, tab rail) makes way for the run view, matching the designer
  // — a run opens as a page, not a body swapped inside the tabbed workspace. The
  // env is already resolved above, so the nested Outlet still receives it via
  // context and RunDetail owns the viewport (its own header + Test-runs tabs).
  if (executionMatch) {
    return (
      <Box sx={{ display: "flex", flexDirection: "column", height: "100%", minHeight: 0 }}>
        <Outlet context={{ env, envState }} />
      </Box>
    );
  }

  // A template-seeded env stays locked until forked: the version pin is
  // read-only, the header overflow is hidden and Overview offers Fork instead.
  const locked = source === "client" && !!envState.seededFromTemplate;

  const activeTab = tab;
  const onTabChange = (id) => setTab(id);

  const onFork = () => {
    const fork = buildFork(env, envState, new Date().toISOString());
    registerFork(env.id, fork);
    navigate(paths.dashboard.simulate.environments.detail(fork.env.id));
  };

  const runnable = canRunHeader(source, env, canRun);

  // Start a run scoped to a scenario selection (or all, when ids is empty) ×
  // trials. The target rides ?only=…&trials=k; the product run page doesn't
  // honour those yet (see runSelectionTarget's honest-gap note).
  const startRun = (ids, trials) => navigate(runSelectionTarget(env, ids, trials));

  // A scenario selection on the Scenarios tab owns the primary Run — the header
  // yields its Run/Repeats while one is active ("one primary at a time").
  const selectionActive = (selection.count ?? selection.ids.length) > 0;

  // While the environment is still deriving, the builder can't accept edits —
  // the console freezes until it goes Live. SystemBanners reads the same value.
  const envLive = env.buildStatus === BUILD_STATUS.READY;

  // The tab count + "no evals" gap read the applied eval set. For a backed env
  // that set is §6 evaluations.selected (what the Evals panel shows), not the
  // client store — so overlay it here so the badge matches the panel and clears
  // after an add. Scenarios/runs keep their existing sources.
  const backedSelected = evalDetailQuery.data?.evaluations?.selected;
  const badgeEnvState =
    backed && Array.isArray(backedSelected)
      ? { ...envState, evals: backedSelected }
      : envState;

  // A §8 rename writes the fresh detail back into the §6 cache, but `env` here
  // is derived from the job poll (name from job metadata), so it would keep the
  // old name in the header. Overlay §6's name for a backed env so a rename shows
  // everywhere the moment it lands, not only in the Settings field.
  const backedName = evalDetailQuery.data?.overview?.name;
  const displayEnv = env && backed && backedName ? { ...env, name: backedName } : env;

  // Overview summary tiles need the real §6 counts: the job poll (which drives
  // env/envState here) carries the scenarios list but not the eval or run
  // counts. Reuse the §6 detail already fetched above rather than a second read.
  const backedDetail = evalDetailQuery.data;
  const overviewCounts =
    backed && backedDetail
      ? {
          scenarios: backedDetail.overview?.scenario_count ?? backedDetail.scenarios?.length,
          evaluations:
            backedDetail.overview?.evaluations_count ?? backedDetail.evaluations?.selected?.length,
          runs: backedDetail.overview?.runs_count,
          hardRules: backedDetail.contract?.hard_constraints?.length,
        }
      : undefined;
  // Real §6 world content for the Overview (stores/amendments/dependencies),
  // so those cards render live data and an honest empty state instead of the
  // fixture. Undefined for a non-backed env.
  const overviewWorld =
    backed && backedDetail
      ? {
          stores: backedDetail.world?.stores ?? [],
          amendments: backedDetail.contract?.amendments ?? [],
          dependencies: backedDetail.contract?.dependencies ?? [],
        }
      : undefined;
  // Real §6 capability-graph branches for a backed env: tools names,
  // real_use_cases (flows), world.personas names, hard_constraints (guardrails).
  // Empty arrays render the graph's honest "none yet" instead of a fixture.
  const graphData =
    backed && backedDetail
      ? {
          tools: (backedDetail.contract?.tools ?? []).map((t) => t?.name).filter(Boolean),
          flows: backedDetail.contract?.real_use_cases ?? [],
          personas: (backedDetail.world?.personas ?? []).map((p) => p?.name).filter(Boolean),
          guardrails: backedDetail.contract?.hard_constraints ?? [],
        }
      : undefined;

  return (
    <Box sx={{ display: "flex", flexDirection: "column", height: "100%", minHeight: 0 }}>
      <WorkspaceHeader
        env={displayEnv}
        envState={envState}
        patch={patch}
        canRun={runnable}
        runBlockedReason={blockedReason(envState)}
        locked={locked}
        backed={source === "harness"}
        onFork={onFork}
        onStartRun={startRun}
        selectionActive={selectionActive}
      />

      <SystemBanners env={env} envState={envState} patch={patch} />
      <VersionBar env={env} envState={envState} />

      {/* A template-seeded env is read-only until forked — the banner states the
          lock and offers the fork. */}
      {locked && <TemplateLockBanner onFork={onFork} />}

      <Box
        sx={{
          flex: 1,
          minHeight: 0,
          display: "grid",
          gap: 2,
          p: 2,
          gridTemplateColumns: { xs: "1fr", lg: "minmax(340px, 400px) 1fr" },
        }}
      >
        <SectionCard
          sx={{ height: "100%", minHeight: 0, display: "flex", flexDirection: "column", overflow: "hidden" }}
        >
          <BuilderConsole
            turns={chat.turns}
            running={chat.running}
            chips={CHIPS_BY_TAB[activeTab] || CHIPS_BY_TAB.overview}
            onSend={chat.send}
            onChip={chat.send}
            onStop={chat.stop}
            canStop={chat.inFlight}
            frozen={!envLive || chat.frozen}
            frozenReason={!envLive ? CONSOLE_COPY.frozen : chat.frozenReason}
            preComposer={
              (selection.count ?? selection.ids.length) > 0 ? (
                <SelectionContextChip
                  count={selection.count ?? selection.ids.length}
                  rows={selection.rows}
                  all={selection.all}
                  onClear={clearScenarioSelection}
                />
              ) : null
            }
          />
        </SectionCard>

        <Box
          sx={{
            height: "100%",
            minHeight: 0,
            display: "flex",
            flexDirection: "column",
            border: "1px solid",
            borderColor: "divider",
            borderRadius: 1.5,
            bgcolor: "background.paper",
            overflow: "hidden",
          }}
        >
          <WorkspacePanels
            env={displayEnv}
            envState={envState}
            patch={patch}
            tab={activeTab}
            onTabChange={onTabChange}
            locked={locked}
            backed={source === "harness"}
            onFork={onFork}
            gapsByTab={gapsByTab(env, badgeEnvState)}
            counts={counts(badgeEnvState)}
            overviewCounts={overviewCounts}
            overviewWorld={overviewWorld}
            graphData={graphData}
            onStartRun={startRun}
            canRun={runnable}
          />
        </Box>
      </Box>
    </Box>
  );
}

// Mirror the module-level scenario selection into component state so the
// console's pre-composer chip re-renders when rows are checked or cleared on
// the Scenarios tab. The bus fires the current value on subscribe.
function useScenarioSelection() {
  const [selection, setSelection] = useState({ ids: [], rows: [], count: 0, all: false });
  useEffect(() => subscribeScenarioSelection(setSelection), []);
  return selection;
}

// Shown above the console composer when the user has scenarios checked on the
// Scenarios tab. Turns an implicit selection into an explicit "you are editing
// N rows" status so the next send doesn't feel like it came from nowhere.
function SelectionContextChip({ count, rows, all = false, onClear }) {
  const preview = (rows || []).slice(0, 2).map((r) => r.name || r.title || "scenario").join(", ");
  const rest = count > 2 ? ` +${count - 2}` : "";
  return (
    <Stack
      direction="row" alignItems="center" spacing={1}
      sx={{
        px: 1.5, py: 1,
        borderBottom: "1px solid", borderColor: "divider",
        bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.06 : 0.03),
      }}
    >
      <Iconify icon="solar:layers-minimalistic-linear" width={14} sx={{ color: "text.subtitle", flexShrink: 0 }} />
      <Typography sx={{ typography: "s3", color: "text.secondary", flex: 1, minWidth: 0 }} noWrap>
        {/* all-mode (select-all-matching) can't name the rows — the match may run
            to thousands and none may be on the current page — so it states the
            count alone. */}
        Editing <b>{count}</b> scenario{count === 1 ? "" : "s"}{all ? " matching" : ` — ${preview}${rest}`}
      </Typography>
      <CustomTooltip show title="Clear selection" size="small" arrow>
        <IconButton size="small" aria-label="Clear selection" onClick={onClear} sx={{ p: 0.25 }}>
          <Iconify icon="solar:close-circle-linear" width={14} sx={{ color: "text.subtitle" }} />
        </IconButton>
      </CustomTooltip>
    </Stack>
  );
}
SelectionContextChip.propTypes = {
  count: PropTypes.number,
  rows: PropTypes.array,
  all: PropTypes.bool,
  onClear: PropTypes.func,
};
