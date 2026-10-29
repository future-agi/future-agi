import PropTypes from "prop-types";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Box,
  Button,
  IconButton,
  Menu,
  MenuItem,
  Stack,
  Typography,
} from "@mui/material";
import { useSnackbar } from "notistack";
import Iconify from "src/components/iconify";
import { paths } from "src/routes/paths";
import { useConnectedAgents, useSimStore } from "../store";
import AgentSyncDialog from "../components/AgentSyncDialog";
import AgentRuntimeEditor from "../agents/AgentRuntimeEditor";
import SwitchVersionDialog from "../agents/SwitchVersionDialog";
import {
  agentIdOf,
  agentNameOf,
  agentNeedsRuntime,
  agentSourceOf,
  envAgentKey,
} from "../_mock/agentRegistry";
import {
  agentVersions,
  currentAgentVersion,
  currentEnvVersion,
  isAgentPinned,
  isPendingDraft,
  versionNumber,
} from "../_mock/versions";
import { envLabelOf } from "../_mock/envVersionKinds";
import { envVersionNameOf } from "../_mock/codeRefs";
import { replicationOf } from "../_mock/replication";
import { agentToolsFor, worldToolsFor } from "../_mock/toolFit";
import {
  applyAgentSwitch,
  firstAgentReadPlan,
  fitsEnvChannel,
} from "../_mock/agentSwitch";
import { channelLabel } from "../_mock/nudgeEffects";
import { scenarioStepOf, versionCardOf } from "../_mock/versionCard";
import AgentVersionCard, { ScenarioStepCard } from "./AgentVersionCard";
import NudgeCard from "./NudgeCard";
import ChangeAgentDrawer from "./agents/ChangeAgentDrawer";
import ConnectNewAgentDrawer from "./agents/ConnectNewAgentDrawer";

const outlined = {
  color: "text.primary",
  borderColor: "divider",
  flexShrink: 0,
};

/**
 * The agent this environment tests, in one line on Overview.
 *
 * The agent lives in Agents (RSI PRD: Agents is "the one list"), so this
 * says only what belongs to the pairing — which agent, the version tested
 * here, where it runs and what a run proves — and offers the one decision
 * that is this environment's: switch to the version in production when it's
 * ahead. View agent opens the agent on its Environments tab, with this
 * environment picked out and a way back. The menu holds Sync agent — a new
 * version of the same agent. An environment never points at a different
 * agent: one that moved platform or was rebuilt under a new ID is moved on
 * its own page (Connection → Move…) and stays the same agent; a different
 * agent builds its own environment. Picking an agent here is only for one
 * that has none yet (a template's placeholder, or nothing connected).
 *
 * `openPick` (a counter) opens the agent picker from elsewhere on the page —
 * "Connect agent" on an environment that has none.
 */
export default function EnvAgentLine({
  env,
  envState,
  patch,
  locked = false,
  onFork,
  openPick = 0,
}) {
  const navigate = useNavigate();
  const { enqueueSnackbar } = useSnackbar();
  const { agents, state, recordAgentVersion, changeEnvAgent } =
    useConnectedAgents();
  const { dispatch: storeDispatch } = useSimStore();
  const agent = envState?.agent || null;
  const placeholder = !!agent?.seeded;
  const key = agent ? envAgentKey(agent, env?.id) : null;
  const record = agents.find((a) => a.key === key) || null;
  const name =
    record?.name || agent?.name || agentNameOf(agent) || "the connected agent";

  const versions = agentVersions(envState);
  const active =
    currentAgentVersion(envState)?.label ||
    versions[versions.length - 1]?.label ||
    "v1";
  /* The environment's default version: production (moves with every
     released version) unless someone set another. */
  const pinned = isAgentPinned(envState);
  const production = record?.production || null;
  const behind = pinned && !!production && production !== active;
  /* A newer version that isn't in production yet — a synced change or a fix
     shipped as a draft. Said quietly; a released one can be tried here, a
     draft can't — a draft is never what an environment tests. */
  const newest = record?.latest || null;
  const newer =
    !behind && !!newest && versionNumber(newest) > versionNumber(active)
      ? newest
      : null;
  const newerIsDraft =
    !!newer && (!!record?.latestIsDraft || isPendingDraft(envState, newer));
  const switchTo = behind ? production : newerIsDraft ? null : newer;
  /* Through the store, against the newest state — a version saved a moment
     ago (Sync) is already production when this runs. */
  const testHere = (label) =>
    patch && storeDispatch({ type: "pinAgentVersion", envId: env.id, label });
  /* A code agent's version reads as its commit — "main @ 3f9a2c1". */
  const nm = (l) => envVersionNameOf(envState, l, env?.id).main;

  const [menu, setMenu] = useState(null);
  const [switching, setSwitching] = useState(false);
  const [syncOpen, setSyncOpen] = useState(false);
  const [changing, setChanging] = useState(false);
  const [connectingNew, setConnectingNew] = useState(false);
  /* Only an environment without a real agent picks one. */
  useEffect(() => {
    if (openPick && !locked && (!agent || placeholder)) setChanging(true);
  }, [openPick, locked]); // eslint-disable-line react-hooks/exhaustive-deps

  /* Connecting here only ever gives an environment its first real agent
     (none yet, or a template's placeholder). That agent arrives with its own
     versions — it is not the next version of the placeholder, so placeholder
     runs never read as an earlier version of it. */
  const choose = ({
    agent: next,
    versions: vs,
    activeLabel,
    name: n,
    picked,
  }) => {
    const candidate = picked || { ...next, name: n, versions: vs, usedIn: [] };
    /* Connected just now from "Connect a new agent" but it's another type:
       it's in Agents, and this environment keeps the agent it has. */
    if (!fitsEnvChannel(candidate, env, state)) {
      setChanging(false);
      setConnectingNew(false);
      enqueueSnackbar(
        `${n} is connected, but it isn't a ${channelLabel(env?.surface).toLowerCase()} agent — build an environment for it from Environments. This one keeps ${placeholder ? "its placeholder" : "no agent for now"}.`,
        { variant: "info" },
      );
      return;
    }
    /* Built from a description, the world has no tools yet: read them from the agent now. */
    const read = firstAgentReadPlan(env, envState, {
      picked: candidate,
      label: activeLabel,
      agentName: n,
      state,
    });
    /* Taking the sample agent's place: its runs stay, labelled Reference,
       so they're never read as this agent's. */
    const fromSample = !!agent?.sample;
    applyAgentSwitch({
      env,
      envState,
      plan: null,
      agent: next,
      versions: vs,
      activeLabel,
      changeEnvAgent,
      patch,
    });
    if (fromSample && (envState?.runs || []).length)
      storeDispatch({
        type: "patchEnvState",
        envId: env.id,
        patch: {
          runs: envState.runs.map((r) =>
            r.kind === "trial" ? r : { ...r, reference: true },
          ),
        },
      });
    if (read) {
      storeDispatch({
        type: "patchEnvironment",
        envId: env.id,
        patch: read.envPatch,
      });
      patch(read.statePatch);
    }
    setChanging(false);
    setConnectingNew(false);
    const live = (envState?.scenarios || []).filter(
      (r) => !r.retired && !r.archived,
    ).length;
    const viaMcp = read
      ? read.tools.filter(
          (t) =>
            t.via?.kind === "mcp" &&
            !read.fromAgent.some((a) => a.name === t.name),
        ).length
      : 0;
    enqueueSnackbar(
      read
        ? `This environment now tests ${n}${activeLabel ? ` ${activeLabel}` : ""} — its ${read.fromAgent.length} tool${read.fromAgent.length === 1 ? "" : "s"} were read into the world${viaMcp ? `, beside the ${viaMcp} added from MCP` : ""}${live ? `, and ${live} scenario${live === 1 ? "" : "s"} need re-checking` : ""}${read.savedEdits ? `. Your ${read.savedEdits} unsaved edit${read.savedEdits === 1 ? " was" : "s were"} saved with it` : ""}`
        : `This environment now tests ${n}${activeLabel ? ` ${activeLabel}` : ""} — the world and scenarios apply as they are`,
      { variant: "success" },
    );
  };
  /* The agent, on its Environments tab, with this environment picked out. */
  const viewAgent = () =>
    navigate(
      `${paths.dashboard.agents}/connected/${agentIdOf(key)}?tab=environments&env=${encodeURIComponent(env.id)}`,
      { state: { from: "environment", envId: env.id, envName: env.name } },
    );

  const drawers = (
    <>
      <ChangeAgentDrawer
        open={changing}
        onClose={() => setChanging(false)}
        env={env}
        currentKey={key}
        onChoose={choose}
        onConnectNew={() => {
          setChanging(false);
          setConnectingNew(true);
        }}
      />
      <ConnectNewAgentDrawer
        open={connectingNew}
        onClose={() => setConnectingNew(false)}
        onConnected={choose}
        channel={env?.surface || null}
      />
    </>
  );

  /* No agent: Overview's own "Connect your agent" banner says so; only the picker lives here. */
  if (!agent) return drawers;

  if (placeholder) {
    return (
      <>
        <Stack
          direction="row"
          alignItems="center"
          spacing={1.5}
          sx={{
            my: 2,
            px: 2,
            py: 1.25,
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "8px",
          }}
        >
          <Iconify
            icon="solar:cpu-bolt-linear"
            width={16}
            sx={{ color: "text.secondary", flexShrink: 0 }}
          />
          <Typography
            sx={{
              typography: "s2",
              flex: 1,
              minWidth: 0,
              color: "text.secondary",
            }}
          >
            {agent.sample
              ? locked
                ? `Tests ${agent.name}, the sample agent this world ships with. Fork the environment to test your own agent.`
                : `Tests ${agent.name}, the sample agent this world ships with. Connect your agent to test yours in this world.`
              : locked
                ? "Tests the placeholder this template ships with. Fork the environment to test your own agent."
                : `Tests ${agent.name || "a template placeholder"} until you connect your agent.`}
          </Typography>
          {locked ? (
            onFork && (
              <Button
                size="small"
                variant="outlined"
                onClick={onFork}
                sx={outlined}
              >
                Fork environment
              </Button>
            )
          ) : (
            <Button
              size="small"
              variant="contained"
              color="primary"
              onClick={() => setChanging(true)}
              sx={{ flexShrink: 0 }}
            >
              Connect your agent
            </Button>
          )}
        </Stack>
        {drawers}
      </>
    );
  }

  const source = agentSourceOf(agent);
  const rep = replicationOf(agent, env);
  const card = versionCardOf(envState);
  const step = scenarioStepOf(envState);
  /* The agent's change, said where the agent is — the same rows as Needs
     input (AgentVersionCard), so it reads and is answered the same way. */
  const changePatch = locked ? null : patch;
  const showCard = card?.status === "open";
  const showStep = step?.status === "open";
  return (
    <Box
      sx={{
        my: 2,
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "8px",
      }}
    >
      <Stack
        direction="row"
        alignItems="center"
        spacing={1.5}
        sx={{ px: 2, py: 1.25 }}
      >
        <Iconify
          icon={source.icon || "solar:cpu-bolt-linear"}
          width={16}
          sx={{ color: "text.secondary", flexShrink: 0 }}
        />
        <Typography noWrap sx={{ typography: "s2", flex: 1, minWidth: 0 }}>
          <Box component="span" sx={{ color: "text.tertiary" }}>
            Tests{" "}
          </Box>
          <Box
            component="span"
            sx={{ fontWeight: 600 }}
          >{`${name} ${nm(active)}`}</Box>
          <Box
            component="span"
            sx={{ color: "text.tertiary" }}
          >{` · ${source.label} · ${rep.proofLabel}`}</Box>
          <Box component="span" sx={{ color: "text.tertiary" }}>
            {pinned ? ` · default ${nm(active)}` : " · default: production"}
          </Box>
          {behind && (
            <Box
              component="span"
              sx={{ color: "text.secondary" }}
            >{` · ${nm(production)} is in production`}</Box>
          )}
          {newer && (
            <Box component="span" sx={{ color: "text.secondary" }}>
              {newerIsDraft
                ? ` · ${nm(newer)} is a draft, tested here once released`
                : ` · ${nm(newer)} is newer`}
            </Box>
          )}
        </Typography>
        {switchTo && !locked && (
          <Button
            size="small"
            variant="outlined"
            onClick={() => setSwitching(true)}
            sx={outlined}
          >
            {behind ? "Default to production" : `Try ${nm(switchTo)}`}
          </Button>
        )}
        <Button
          size="small"
          onClick={viewAgent}
          endIcon={<Iconify icon="solar:arrow-right-linear" width={14} />}
          sx={{ color: "text.primary", flexShrink: 0 }}
        >
          View agent
        </Button>
        {!locked && source.syncable && (
          <IconButton
            size="small"
            aria-label="More agent actions"
            onClick={(e) => setMenu(e.currentTarget)}
          >
            <Iconify icon="solar:menu-dots-linear" width={20} />
          </IconButton>
        )}
      </Stack>
      {agentNeedsRuntime(agent) && !locked && (
        <Box sx={{ px: 2, pb: 1.5 }}>
          <AgentRuntimeEditor agent={{ ...agent, key }} variant="embedded" />
        </Box>
      )}
      {/* The agent's change, said where the agent is — answered right here. */}
      {showCard && (
        <Box sx={{ borderTop: "1px solid", borderColor: "divider" }}>
          <AgentVersionCard
            env={env}
            envState={envState}
            patch={changePatch}
            px={2}
            renderNudge={(n) => (
              <NudgeCard
                nudge={n}
                env={env}
                envState={envState}
                patch={changePatch}
              />
            )}
          />
        </Box>
      )}
      {showStep && (
        <Box sx={{ borderTop: "1px solid", borderColor: "divider" }}>
          <ScenarioStepCard
            env={env}
            envState={envState}
            patch={changePatch}
            px={2}
          />
        </Box>
      )}

      <Menu
        anchorEl={menu}
        open={!!menu}
        onClose={() => setMenu(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
      >
        {source.syncable && (
          <MenuItem
            onClick={() => {
              setMenu(null);
              setSyncOpen(true);
            }}
            sx={{ typography: "s2" }}
          >
            <Iconify
              icon="solar:refresh-linear"
              width={16}
              sx={{ mr: 1, color: "text.secondary" }}
            />
            Sync agent
          </MenuItem>
        )}
      </Menu>

      <SwitchVersionDialog
        open={switching}
        onClose={() => setSwitching(false)}
        env={env}
        envState={envState}
        label={switchTo || active}
        onConfirm={() => {
          testHere(switchTo);
          setSwitching(false);
        }}
      />
      {source.syncable && (
        <AgentSyncDialog
          open={syncOpen}
          onClose={() => setSyncOpen(false)}
          agent={agent}
          versions={versions}
          env={env}
          baseTools={agentToolsFor(
            env,
            envState,
            versions[versions.length - 1]?.label,
          )}
          runningLabel={active}
          worldTools={worldToolsFor(env, envState).map((t) => t.name)}
          envVersionLabel={envLabelOf(
            env,
            envState,
            currentEnvVersion(env, envState)?.label,
          )}
          onSave={(v) => recordAgentVersion(key, v)}
          onRunHere={testHere}
        />
      )}
      {drawers}
    </Box>
  );
}

EnvAgentLine.propTypes = {
  env: PropTypes.object.isRequired,
  envState: PropTypes.object,
  patch: PropTypes.func.isRequired,
  locked: PropTypes.bool,
  onFork: PropTypes.func,
  /* Bumped to open the agent picker — "Connect agent" elsewhere on the page. */
  openPick: PropTypes.number,
};
