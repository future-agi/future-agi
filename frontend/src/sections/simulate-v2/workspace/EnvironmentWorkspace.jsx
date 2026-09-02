import { useEffect, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { alpha } from "@mui/material/styles";
import PropTypes from "prop-types";
import { Box, Stack, Typography, Button, Tooltip } from "@mui/material";
import Iconify from "src/components/iconify";
import { paths } from "src/routes/paths";
import { protoRunId } from "../_mock/executionAdapter";
import { getEnvironment } from "../_mock/environments";
import { getSurface } from "../_mock/surfaces";
import { BOOT_STEPS } from "../_mock/runStream";
import { useSimStore, useEnvState } from "../store";
import { setupGaps } from "../_mock/setupGaps";
import { environmentVersions } from "../_mock/versions";
import { getAgentType } from "../_mock/agentTypes";
import { SurfaceIcon, EmptyState } from "../components/primitives";
import { ProvisioningPanel } from "../components/loading";
import OverviewPanel from "./OverviewPanel";
import AgentsPanel from "./AgentsPanel";
import ScenariosStep from "./ScenariosStep";
import EvalsStep from "./EvalsStep";
import RunsPanel from "./RunsPanel";
import OptimizationsPanel from "./OptimizationsPanel";
import InstancesPanel from "./InstancesPanel";
import FilesPanel from "./FilesPanel";
import SettingsPanel from "./SettingsPanel";
import PersonasPanel from "./PersonasPanel";
import RlPanel from "./RlPanel";
import VersionBar from "./VersionBar";
import EnvVersionPin from "./EnvVersionPin";
import BuildRecordPanel from "./BuildRecordPanel";
import RlContractPanel from "./RlContractPanel";
import ActorsPanel from "./ActorsPanel";

/**
 * `setup` marks the items that gate a run — those carry a completion tick and
 * feed the progress counter. The rest are views of the environment itself, so
 * the rail splits them rather than presenting eight equal steps.
 */
const STEPS = [
  { id: "overview",  label: "Overview",  icon: "solar:widget-5-linear", group: "Setup" },
  { id: "agent",     label: "Agents",    icon: "solar:cpu-bolt-linear", group: "Setup", setup: true },
  { id: "contract",  label: "Contract", icon: "solar:document-text-linear", group: "Setup" },
  { id: "build",     label: "How this was built", icon: "solar:history-linear", group: "Setup" },

  { id: "scenarios", label: "Scenarios", icon: "solar:layers-minimalistic-linear", group: "The world", setup: true },
  { id: "personas",  label: "Personas",  icon: "solar:users-group-rounded-linear", group: "The world" },
  { id: "actors",    label: "Actors",    icon: "solar:users-group-two-rounded-linear", group: "The world" },

  { id: "evals",     label: "Evaluations", icon: "solar:shield-check-linear", group: "Grading", setup: true },
  { id: "runs",      label: "Runs",      icon: "solar:play-circle-linear", group: "Grading" },
  { id: "optimizations", label: "Optimizations", icon: "solar:magic-stick-3-linear", group: "Grading" },

  { id: "instances", label: "Instances", icon: "solar:server-square-linear", group: "Environment" },
  { id: "files",     label: "Files",     icon: "solar:folder-linear", group: "Environment" },
  { id: "rl",        label: "Interface", icon: "solar:refresh-circle-linear", group: "Environment" },
  { id: "settings",  label: "Settings",  icon: "solar:settings-minimalistic-linear", group: "Environment" },
];

const RAIL_GROUPS = ["Setup", "The world", "Grading", "Environment"];

/* Setup-gap areas map onto the rail step that owns the underlying
   answer, so a blocking gap surfaces as an amber dot on that step's
   label. See DerivedPanels for the top-tabs equivalent. */
const GAP_AREA_TO_STEP = {
  Sandbox: "agent",
  Tools: "agent",
  Contract: "contract",
  Grading: "evals",
};

/**
 * The environment workspace.
 *
 * Once you are inside an environment, everything else is configuration of that
 * environment — so this is a persistent shell with a progress rail rather than
 * a linear wizard you fall out of. The rail doubles as navigation and as the
 * "what is still missing before I can run" answer.
 */
export default function EnvironmentWorkspace() {
  const { envId, step = "overview" } = useParams();
  const navigate = useNavigate();
  const { state, dispatch } = useSimStore();

  const env =
    getEnvironment(envId) || state.myEnvironments.find((e) => e.id === envId);

  const { envState, patch, addAgentVersion, steps, canRun } = useEnvState(envId);

  // Opening an environment goes straight to it — a boot sequence on every entry
  // is a delay the user did not ask for. The sequence is kept for "Reset state",
  // where re-provisioning is the whole point of pressing the button.
  const [booting, setBooting] = useState(false);

  // Adopt on direct navigation so a deep link works from a cold start.
  useEffect(() => {
    if (env && !state.myEnvironments.some((e) => e.id === env.id)) {
      dispatch({ type: "adoptEnvironment", env, now: new Date().toISOString() });
    }
  }, [env, state.myEnvironments, dispatch]);

  if (!env) {
    return (
      <Box sx={{ p: 2 }}>
        <EmptyState
          icon="solar:danger-triangle-linear"
          title="Environment not found"
          body="It may have been removed from your workspace."
          action={
            <Button variant="contained" color="primary" size="small" onClick={() => navigate(paths.dashboard.simulate.environments)}>
              Back to environments
            </Button>
          }
        />
      </Box>
    );
  }

  const surface = getSurface(env.surface);

  // Booting the environment is a real thing we do, so we show it happening.
  if (booting) {
    return (
      <Box sx={{ p: 2, height: "100%", minHeight: 420, display: "grid", placeItems: "center" }}>
        <Box sx={{ width: "100%", maxWidth: 520, border: "1px solid", borderColor: "divider", borderRadius: 2, bgcolor: "background.paper" }}>
          <ProvisioningPanel
            icon={surface.icon}
              title={`Starting ${env.name}`}
            subtitle="Restoring a clean copy of the environment for this session."
            steps={BOOT_STEPS[surface.stage] || BOOT_STEPS.voice}
            onDone={() => setBooting(false)}
          />
        </Box>
      </Box>
    );
  }

  const go = (s) => navigate(paths.dashboard.simulate.environmentStep(envId, s));

  /*
    Straight into the run.

    The button is already gated on canRun — an agent is connected and there are
    scenarios — which is the same condition the Runs panel's pre-flight checks
    before it will let you start. So landing there first was a summary of a
    question that had just been answered, and a second Start button to press.
    Pre-flight and history stay on the Runs step for when they are the thing
    you came for.
  */
  const startRun = () =>
    navigate(paths.dashboard.simulate.simulationRun(envId, protoRunId(envId, Date.now().toString(36))));

  // Unknown steps render Overview, and the rail highlights it, so the URL and
  // the highlighted item never disagree.
  /*
    Evals still need scenarios before anything can be added — an eval's
    variables map onto what a run produces — but that is enforced *on* the
    Evals step, which says so and offers the way forward. Redirecting to
    Scenarios instead made the rail item look broken: you click Evals and
    nothing appears to happen.
  */
  const panel = STEPS.some((s) => s.id === step) ? step : "overview";

  const doneById = Object.fromEntries(steps.map((s) => [s.id, s.done]));
  const counts = {
    scenarios: envState.scenarios.length || null,
    evals: envState.evals.length || null,
    runs: envState.runs.length || null,
  };
  /*
    Setup gaps are shown as amber dots on the rail items that own them
    instead of a dedicated "Needs your input" step. Each blocking gap
    surfaces on the panel where the answer belongs — Agent for
    sandbox/tool secrets, Contract for manifest questions, Evaluations
    for grading choices — so a user always knows where to go.
  */
  const allGaps = setupGaps(env, envState);
  const gapsByStep = {};
  allGaps.forEach((g) => {
    if (g.status !== "blocking") return;
    const stepId = GAP_AREA_TO_STEP[g.area];
    if (!stepId) return;
    (gapsByStep[stepId] = gapsByStep[stepId] || []).push(g);
  });

  /*
    Reason the Run simulation button is disabled — matches the build
    view's blockedReason logic so both surfaces say the same thing.
    Names the specific gap where possible (the common "no evals"
    case gets a first-class message) instead of a generic "connect
    an agent and add scenarios" that stays put even after the user
    has done both.
  */
  const runBlockedReason = (() => {
    if (canRun) return "";
    if (!envState.agent) return "Connect an agent on the Agents tab";
    if (!envState.scenarios.length) return "Add scenarios on the Scenarios tab";
    if (!envState.evals.length) return "Add at least one evaluation on the Evaluations tab";
    const firstBlocking = allGaps.find((g) => g.status === "blocking");
    return firstBlocking?.title || "Setup incomplete";
  })();


  return (
    <Box sx={{ display: "flex", flexDirection: "column", height: "100%", minHeight: 0 }}>
      {/* ── environment header ── */}
      <Stack
        direction="row"
        alignItems="center"
        spacing={2}
        sx={{ px: 3, py: 2, borderBottom: "1px solid", borderColor: "divider", flexShrink: 0 }}
      >
        <Tooltip title="All environments" arrow>
          <Button
            onClick={() => navigate(paths.dashboard.simulate.environments)}
            sx={{ minWidth: 32, width: 32, height: 32, p: 0, color: "text.subtitle" }}
          >
            <Iconify icon="solar:alt-arrow-left-linear" width={18} />
          </Button>
        </Tooltip>

        <SurfaceIcon surface={env.surface} size={36} />

        <Box minWidth={0} flex={1}>
          <Stack direction="row" alignItems="center" spacing={1}>
            <Typography noWrap sx={{ typography: "s1_2", fontWeight: 700 }}>{env.name}</Typography>
            <Stack
              direction="row" alignItems="center" spacing={0.5}
              sx={{
                px: 0.75, height: 22, borderRadius: 0.75,
                color: "#16A34A",
                bgcolor: (t) => alpha("#16A34A", t.palette.mode === "dark" ? 0.16 : 0.1),
                border: () => `1px solid ${alpha("#16A34A", 0.24)}`,
              }}
            >
              <Box sx={{ width: 6, height: 6, borderRadius: "50%", bgcolor: "#16A34A" }} />
              <Typography sx={{ typography: "s3", fontWeight: 600 }}>Live</Typography>
            </Stack>
            {/*
              The env-version pin. Clicks open a menu of every version
              with its note + change summary; selecting one switches the
              active version, and the next run stamps against that pick.
              Amber-tinted when the active version is not the latest, so
              "editing off v1 while v3 exists" is a visible state rather
              than a silent one.
            */}
            <EnvVersionPin env={env} envState={envState} patch={patch} />
            {/*
              Twin-backing badge. Present on every tab so the reader
              always knows "this env's world is a live sandbox, not a
              seed table." Count reflects how many services are twinned.
              Tooltip surfaces the service names without needing to
              navigate to Overview or Settings.
            */}
            {envState?.twinBacking?.services?.length > 0 && (
              <Tooltip
                arrow
                title={
                  <Box>
                    <Typography sx={{ typography: "s3", fontWeight: 700, color: "common.white", mb: 0.5 }}>
                      Clone-backed environment
                    </Typography>
                    <Typography sx={{ typography: "s3", color: "common.white" }}>
                      Clones of {envState.twinBacking.services.join(", ")} — provisioned per run.
                    </Typography>
                  </Box>
                }
              >
                <Stack
                  direction="row" alignItems="center" spacing={0.5}
                  sx={{
                    px: 0.75, height: 22, borderRadius: 0.75,
                    color: "#7857FC",
                    bgcolor: (t) => alpha("#7857FC", t.palette.mode === "dark" ? 0.16 : 0.09),
                    border: (t) => `1px solid ${alpha("#7857FC", t.palette.mode === "dark" ? 0.35 : 0.28)}`,
                    cursor: "default",
                  }}
                >
                  <Iconify icon="solar:server-square-linear" width={11} sx={{ color: "#7857FC" }} />
                  <Typography sx={{ typography: "s3", fontWeight: 700, letterSpacing: 0.3 }}>
                    CLONE · {envState.twinBacking.services.length}
                  </Typography>
                </Stack>
              </Tooltip>
            )}
            {/*
              TTL countdown pill. Only shows for short-lived envs
              (twinBacking.ttlMinutes set). Amber when > 2 minutes,
              red when running out. Displays absolute countdown so the
              user always knows how much time the sandbox has left
              before it self-expires.
            */}
            {envState?.twinBacking?.expiresAt && (
              <TtlCountdownPill expiresAt={envState.twinBacking.expiresAt} />
            )}
          </Stack>
          <Typography noWrap sx={{ typography: "s2", color: "text.subtitle" }}>{env.tagline}</Typography>
        </Box>

        <Button
          variant="outlined"
          size="small"
          startIcon={<Iconify icon="solar:restart-linear" width={16} />}
          onClick={() => {
            /*
              Reset-state action. For twin-backed envs this is the
              "provision a fresh sandbox" moment Arga models: activity
              counters go to zero, provisionedAt gets bumped (which is
              the epoch liveSandboxContentFor uses to drop prior runs),
              and if the env is short-lived the countdown restarts. The
              boot animation runs so the user sees the ceremony —
              same one used on cold-start.
            */
            if (envState?.twinBacking) {
              const now = new Date().toISOString();
              const services = envState.twinBacking.services || [];
              patch({
                twinBacking: {
                  ...envState.twinBacking,
                  provisionedAt: now,
                  activity: Object.fromEntries(services.map((s) => [s, { requests: 0, failures: 0 }])),
                  expiresAt: envState.twinBacking.ttlMinutes
                    ? new Date(Date.now() + envState.twinBacking.ttlMinutes * 60_000).toISOString()
                    : null,
                },
              });
            }
            setBooting(true);
          }}
          sx={{ color: "text.primary", borderColor: "divider", typography: "s2", fontWeight: 600 }}
        >
          Reset state
        </Button>
        {/*
          Active agent pill — shows which agent the next simulation
          run will target. Clicking jumps to the Agents tab so the
          user can flip the active agent. Hidden when only the source
          agent exists — there's nothing to pick between.
        */}
        <ActiveAgentPill
          envState={envState}
          onGo={() => go("agent")}
        />
        <Tooltip title={canRun ? "" : runBlockedReason} arrow>
          <span>
            <Button
              variant="contained"
              color="primary"
              size="small"
              disabled={!canRun}
              onClick={startRun}
              startIcon={<Iconify icon="solar:play-bold" width={15} />}
              sx={{ typography: "s2", fontWeight: 700 }}
            >
              Run simulation
            </Button>
          </span>
        </Tooltip>
      </Stack>

      {/*
        System banners: things that change what any panel below means.
        Building = the derivation is still in flight; the panels show
        loading and this banner explains why. Off-latest = the user has
        pinned an older env version to work off; the amber pin in the
        header is the control, this is the reminder so an hour of edits
        doesn't run against the wrong world.
      */}
      <SystemBanners env={env} envState={envState} patch={patch} />

      {/* ── body: rail + panel ── */}
      {/* A run is a pairing — this environment version × that agent version. */}
      <VersionBar
        env={env}
        envState={envState}
        scenarioCount={envState.scenarios.length}
        onAddVersion={addAgentVersion}
        patch={patch}
        onRunAfterVersion={startRun}
      />

      <Box sx={{ display: "flex", flex: 1, minHeight: 0 }}>
        <SetupRail
          current={panel}
          onGo={go}
          doneById={doneById}
          counts={counts}
          gapsByStep={gapsByStep}
        />

        {/*
          Fall back to Overview for an unrecognised step rather than rendering
          an empty pane — a bad link should land somewhere, not nowhere.
        */}
        <Box sx={{ flex: 1, minWidth: 0, overflow: "auto" }}>
          {panel === "agent" ? (
            <AgentsPanel env={env} envState={envState} patch={patch} onGo={go} />
          ) : panel === "scenarios" ? (
            <ScenariosStep env={env} envState={envState} patch={patch} onGo={go} />
          ) : panel === "evals" ? (
            <EvalsStep env={env} envState={envState} patch={patch} onGo={go} />
          ) : panel === "runs" ? (
            <RunsPanel env={env} envState={envState} onGo={go} />
          ) : panel === "optimizations" ? (
            <OptimizationsPanel env={env} envState={envState} />
          ) : panel === "instances" ? (
            <InstancesPanel env={env} envState={envState} onGo={go} />
          ) : panel === "files" ? (
            <FilesPanel env={env} />
          ) : panel === "contract" ? (
            <RlContractPanel env={env} envState={envState} onGo={go} />
          ) : panel === "actors" ? (
            <ActorsPanel env={env} envState={envState} patch={patch} onGo={go} />
          ) : panel === "build" ? (
            <BuildRecordPanel env={env} envState={envState} patch={patch} />
          ) : panel === "personas" ? (
            <PersonasPanel env={env} envState={envState} patch={patch} onGo={go} />) : panel === "rl" ? (
            <RlPanel env={env} envState={envState} patch={patch} />
          ) : panel === "settings" ? (
            <SettingsPanel env={env} envState={envState} patch={patch} />
          ) : (
            <OverviewPanel env={env} envState={envState} patch={patch} onGo={go} agentConnected={!!envState.agent} />
          )}
        </Box>
      </Box>
    </Box>
  );
}

/* ── left rail ───────────────────────────────────────────────────────────── */

function SetupRail({ current, onGo, doneById, counts, gapsByStep = {} }) {
  return (
    <Box
      sx={{
        width: 200, flexShrink: 0, borderRight: "1px solid", borderColor: "divider",
        display: "flex", flexDirection: "column", bgcolor: "background.paper",
      }}
    >
      <Box sx={{ p: 2, flex: 1 }}>
        {RAIL_GROUPS.map((group) => (
          <Box key={group} sx={{ mb: 2 }}>
            <Typography
              sx={{
                typography: "s3", fontWeight: 700, color: "text.subtitle",
                letterSpacing: .4, textTransform: "uppercase", mb: 1,
              }}
            >
              {group}
            </Typography>
            <Stack spacing={0.25}>
              {STEPS.filter((s) => s.group === group).map((s) => {
                const active = current === s.id;
                const done = doneById[s.id];
                return (
                  <Stack
                    key={s.id}
                    direction="row"
                    alignItems="center"
                    spacing={1.25}
                    onClick={() => onGo(s.id)}
                    sx={{
                      px: 1.25, py: 0.875, borderRadius: 1,
                      cursor: "pointer",
                      // Same selected treatment as the app's left nav
                      // (nav-section/vertical/nav-item): brand primary at 0.1
                      // for fill and border, primary.dark for the label. The
                      // rail used the environment's own accent, so selection
                      // changed colour depending on which environment you were
                      // in — two different answers to "what does selected look
                      // like" on one screen.
                      // text.primary, not text.secondary: this rail sits
                      // directly beside the app's left nav, which paints its
                      // idle items at full strength. Two lists of the same
                      // kind at two different weights read as one being
                      // disabled.
                      color: active ? "primary.dark" : "text.primary",
                      bgcolor: active ? (t) => alpha(t.palette.primary.main, 0.1) : "transparent",
                      border: "1px solid",
                      borderColor: active ? (t) => alpha(t.palette.primary.main, 0.1) : "transparent",
                      transition: "background-color .15s ease",
                      "&:hover": { bgcolor: active ? undefined : "action.hover" },
                    }}
                  >
                    <Iconify icon={s.icon} width={17} sx={{ color: active ? "primary.main" : "text.primary", flexShrink: 0 }} />
                    <Typography sx={{ flex: 1, typography: "s2", fontWeight: active ? 600 : 500, color: "inherit" }}>
                      {s.label}
                    </Typography>
                    {gapsByStep[s.id]?.length > 0 && (
                      /*
                        Filled red pencil in a soft red halo — "needs
                        your input" is now shown on the step that owns
                        the missing answer, not a separate tab. The
                        tooltip lists exactly what's missing.
                      */
                      <Tooltip
                        arrow
                        title={
                          <Box>
                            <Typography sx={{ typography: "s3", fontWeight: 700, mb: 0.375 }}>
                              Needs your input before you can run:
                            </Typography>
                            {gapsByStep[s.id].map((g) => (
                              <Typography key={g.id} sx={{ typography: "s3", opacity: 0.9 }}>
                                · {g.title}
                              </Typography>
                            ))}
                          </Box>
                        }
                      >
                        <Box
                          sx={{
                            display: "grid", placeItems: "center", flexShrink: 0,
                            minWidth: 16, height: 16, px: "5px",
                            borderRadius: "8px",
                            bgcolor: (t) => alpha("#DC2626", t.palette.mode === "dark" ? 0.2 : 0.12),
                            color: "#DC2626",
                            typography: "s3", fontWeight: 700, lineHeight: 1,
                            fontVariantNumeric: "tabular-nums",
                            fontSize: 10,
                          }}
                        >
                          {gapsByStep[s.id].length}
                        </Box>
                      </Tooltip>
                    )}
                    {counts[s.id] != null && !done && (
                      <Typography sx={{ typography: "s3", color: "text.subtitle" }}>{counts[s.id]}</Typography>
                    )}
                    {done && (
                      <Iconify icon="solar:check-circle-bold" width={15} sx={{ color: "#16A34A", flexShrink: 0 }} />
                    )}
                  </Stack>
                );
              })}
            </Stack>
          </Box>
        ))}
      </Box>
    </Box>
  );
}

SetupRail.propTypes = {
  current: PropTypes.string,
  onGo: PropTypes.func,
  doneById: PropTypes.object,
  counts: PropTypes.object,
  gapsByStep: PropTypes.object,
};

/**
 * Two banners that change how everything below reads:
 *
 *   building     — the derivation is still in flight. Panels show
 *                  loading; without this the user has no top-level
 *                  signal that "still building" is why.
 *   off-latest   — the user pinned an older env version to work from.
 *                  The amber pin in the header is the control, this is
 *                  the reminder because an hour of editing off v1 while
 *                  v3 exists is otherwise silent.
 */
function SystemBanners({ env, envState, patch }) {
  const versions = environmentVersions(env, envState);
  const newest = versions[0];
  const active = versions.find((v) => v.current) || newest;
  const offLatest = active && newest && active.label !== newest.label;

  const buildStatus = env.buildStatus;
  const buildProgress = env.buildProgress;

  if (buildStatus !== "building" && !offLatest) return null;

  return (
    <Stack spacing={0}>
      {buildStatus === "building" && (
        <Stack
          direction="row" alignItems="center" spacing={1.25}
          sx={{
            px: 3, py: 1.25,
            bgcolor: (t) => alpha("#7857FC", t.palette.mode === "dark" ? 0.12 : 0.06),
            borderBottom: "1px solid",
            borderColor: (t) => alpha("#7857FC", 0.24),
          }}
        >
          <Box
            sx={{
              width: 8, height: 8, borderRadius: "50%", bgcolor: "#7857FC",
              animation: "wb-pulse 1.4s ease-in-out infinite",
              "@keyframes wb-pulse": {
                "0%,100%": { opacity: 0.4 },
                "50%": { opacity: 1 },
              },
              flexShrink: 0,
            }}
          />
          <Typography sx={{ typography: "s2", fontWeight: 700, color: "#7857FC", flexShrink: 0 }}>
            Environment is still being built
          </Typography>
          <Typography sx={{ typography: "s2", color: "text.secondary" }}>
            {buildProgress?.done != null && buildProgress?.total != null
              ? `${buildProgress.done} of ${buildProgress.total} steps done.`
              : "Scenarios and personas are still deriving."}{" "}
            You can leave and come back — this page will fill in as each stage lands.
          </Typography>
        </Stack>
      )}

      {offLatest && (
        <Stack
          direction="row" alignItems="center" spacing={1.25}
          sx={{
            px: 3, py: 1.25,
            bgcolor: (t) => alpha("#CA8A04", t.palette.mode === "dark" ? 0.12 : 0.07),
            borderBottom: "1px solid",
            borderColor: (t) => alpha("#CA8A04", 0.3),
          }}
        >
          <Iconify icon="solar:danger-triangle-bold" width={14} sx={{ color: "#CA8A04", flexShrink: 0 }} />
          <Typography sx={{ typography: "s2", fontWeight: 700, color: "#CA8A04", flexShrink: 0 }}>
            Editing off {active.label}
          </Typography>
          <Typography sx={{ typography: "s2", color: "text.secondary", flex: 1, minWidth: 0 }}>
            Latest is <b>{newest.label}</b>. Runs from here will stamp against {active.label}, and any edits sit on {active.label}, not on the latest world.
          </Typography>
          <Button
            size="small"
            onClick={() => patch({ activeEnvVersion: newest.label })}
            sx={{ typography: "s2", fontWeight: 700, color: "#CA8A04", flexShrink: 0 }}
          >
            Switch to {newest.label}
          </Button>
        </Stack>
      )}
    </Stack>
  );
}

SystemBanners.propTypes = {
  env: PropTypes.object.isRequired,
  envState: PropTypes.object.isRequired,
  patch: PropTypes.func.isRequired,
};

/**
 * Header pill showing which agent the next run targets. Only rendered
 * when there are 2+ agents attached — with a single agent there's
 * nothing to pick between.
 */
function ActiveAgentPill({ envState, onGo }) {
  const source = envState.agent;
  const additional = envState.additionalAgents || [];
  const activeId = envState.activeAgentId;

  if (!source || additional.length === 0) return null;

  const activeAgent = activeId
    ? additional.find((a) => a.id === activeId) || source
    : source;
  const isSourceActive = activeId == null;
  const type = getAgentType(activeAgent.typeId);

  return (
    <Tooltip
      arrow
      title={`Simulation runs will target ${type?.label || "this agent"}. Click to switch.`}
    >
      <Button
        size="small"
        onClick={onGo}
        startIcon={<Iconify icon={type?.icon || "solar:cpu-bolt-linear"} width={14} />}
        endIcon={<Iconify icon="solar:alt-arrow-down-linear" width={12} />}
        sx={{
          typography: "s2", fontWeight: 600, textTransform: "none",
          color: "text.primary", border: "1px solid",
          borderColor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.14 : 0.12),
          borderRadius: 1, px: 1.25, py: 0.375, flexShrink: 0,
          bgcolor: "background.paper",
          "&:hover": { bgcolor: "action.hover" },
        }}
      >
        <Stack direction="row" alignItems="center" spacing={0.75}>
          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>Runs:</Typography>
          <Typography sx={{ typography: "s2", fontWeight: 600, color: "text.primary" }}>
            {type?.label || "Agent"}
          </Typography>
          {isSourceActive && (
            <Typography sx={{ typography: "s3", color: "#7857FC", fontWeight: 700 }}>
              · source
            </Typography>
          )}
        </Stack>
      </Button>
    </Tooltip>
  );
}
ActiveAgentPill.propTypes = {
  envState: PropTypes.object,
  onGo: PropTypes.func,
};

/*
  TTL countdown for short-lived twin-backed envs. Ticks once a second
  and colors the pill by urgency: purple → amber under 2 minutes → red
  under 30 seconds → grey once expired. Once expired the pill sticks
  (and reads "Expired") because it's a signal that the env can no
  longer be run against, not a value that should silently disappear.
*/
function TtlCountdownPill({ expiresAt }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);
  const ms = Math.max(0, Date.parse(expiresAt) - now);
  const s = Math.floor(ms / 1000);
  const label = ms === 0
    ? "Expired"
    : s >= 3600
      ? `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m left`
      : s >= 60
        ? `${Math.floor(s / 60)}m ${s % 60}s left`
        : `${s}s left`;
  const tint = ms === 0 ? "#6B7280"
    : s < 30 ? "#DC2626"
    : s < 120 ? "#CA8A04"
    : "#7857FC";
  return (
    <Tooltip arrow title="Short-lived environment — auto-expires when the timer ends. Reset state to restart the timer.">
      <Stack direction="row" alignItems="center" spacing={0.5}
        sx={{
          px: 0.75, height: 22, borderRadius: 0.75,
          color: tint,
          bgcolor: (t) => alpha(tint, t.palette.mode === "dark" ? 0.16 : 0.09),
          border: (t) => `1px solid ${alpha(tint, t.palette.mode === "dark" ? 0.35 : 0.28)}`,
          cursor: "default", fontVariantNumeric: "tabular-nums",
        }}>
        <Iconify icon="solar:clock-circle-linear" width={11} sx={{ color: tint }} />
        <Typography sx={{ typography: "s3", fontWeight: 700, letterSpacing: 0.3 }}>
          {label}
        </Typography>
      </Stack>
    </Tooltip>
  );
}
TtlCountdownPill.propTypes = { expiresAt: PropTypes.string };


