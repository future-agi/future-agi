import { Box, Button } from "@mui/material";
import {
  useNavigate,
  useParams,
  useMatch,
  Outlet,
} from "react-router-dom";

import { paths } from "src/routes/paths";
import {
  useEnvironment,
  canRunHeader,
} from "src/api/simulate-environments/environment";
import { useWorkspaceChat } from "src/api/simulate-environments/workspaceChat";

import { BUILD_STATUS } from "../helpers/harnessJobToRow";
import { useEnvironmentsStore } from "../store/useEnvironmentsStore";
import { useEnvState } from "../store/envState";
import SectionCard from "../components/SectionCard";
import EmptyState from "../components/EmptyState";
import BuilderConsole from "../buildEnvironment/console/BuilderConsole";
import WorkspaceHeader from "./WorkspaceHeader";
import SystemBanners from "./SystemBanners";
import VersionBar from "./VersionBar";
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
const blockedReason = (envState) => {
  if (!envState.agent) return WORKSPACE_COPY.runBlocked.agent;
  if (!envState.scenarios?.length) return WORKSPACE_COPY.runBlocked.scenarios;
  if (!envState.evals?.length) return WORKSPACE_COPY.runBlocked.evals;
  return WORKSPACE_COPY.runBlocked.generic;
};

/**
 * The environment workspace at `environments/:envId`.
 *
 * Resolves the id (an adopted client env, a harness job, or a prebuilt
 * template) and lays out the shared workspace: header, system banners, the
 * version pairing strip, the builder console on the left and the tabbed panels
 * on the right — the same composition the build page swaps in at 7/7. A deep
 * link to `runs/:testId/:executionId` forces the Runs tab and renders the reused
 * product execution detail (the Outlet) inside its body.
 */
export default function EnvironmentWorkspace() {
  const navigate = useNavigate();
  const { envId } = useParams();
  const { env, source, bootstrapState, notFound } = useEnvironment(envId);
  // A still-building harness job re-derives its bootstrap on every poll, so the
  // workspace shows it live and only lets the store keep it once the build is
  // terminal — otherwise the first poll's state is frozen in and the scenarios
  // the run produces afterwards never land.
  const { envState, patch, canRun } = useEnvState(envId, bootstrapState, {
    persist: env?.buildStatus !== BUILD_STATUS.BUILDING,
  });
  const { tab, setTab } = useWorkspaceTab();
  const chat = useWorkspaceChat(env);
  const registerFork = useEnvironmentsStore((s) => s.forkEnvironment);

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

  // Still resolving the id — a blank centred box, never a flash of "not found".
  if (!env) {
    return <Box sx={{ height: "100%", minHeight: 420, display: "grid", placeItems: "center" }} />;
  }

  // A template-seeded env stays locked until forked: the version pin is
  // read-only, the header overflow is hidden and Overview offers Fork instead.
  const locked = source === "client" && !!envState.seededFromTemplate;

  // The execution route forces the Runs tab and hands the panels the nested
  // product detail; switching tabs from there leaves the execution behind.
  const activeTab = executionMatch ? "runs" : tab;
  const onTabChange = (id) =>
    executionMatch
      ? navigate(paths.dashboard.simulate.environments.workspaceTab(env.id, id))
      : setTab(id);

  const onFork = () => {
    const fork = buildFork(env, envState, new Date().toISOString());
    registerFork(env.id, fork);
    navigate(paths.dashboard.simulate.environments.detail(fork.env.id));
  };

  const runnable = canRunHeader(source, env, canRun);

  return (
    <Box sx={{ display: "flex", flexDirection: "column", height: "100%", minHeight: 0 }}>
      <WorkspaceHeader
        env={env}
        envState={envState}
        patch={patch}
        canRun={runnable}
        runBlockedReason={blockedReason(envState)}
        locked={locked}
        onFork={onFork}
      />

      <SystemBanners env={env} envState={envState} patch={patch} />
      <VersionBar env={env} envState={envState} />

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
            chips={CHIPS_BY_TAB[activeTab]}
            onSend={chat.send}
            onChip={chat.send}
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
            env={env}
            envState={envState}
            patch={patch}
            tab={activeTab}
            onTabChange={onTabChange}
            locked={locked}
            canRun={runnable}
            onFork={onFork}
            gapsByTab={gapsByTab(env, envState)}
            counts={counts(envState)}
            executionOutlet={executionMatch ? <Outlet /> : undefined}
          />
        </Box>
      </Box>
    </Box>
  );
}
