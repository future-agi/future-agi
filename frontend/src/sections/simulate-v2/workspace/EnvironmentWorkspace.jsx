import { useEffect, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { alpha } from "@mui/material/styles";
import PropTypes from "prop-types";
import { Box, Stack, Typography, Button, Tooltip, Tab, IconButton, Menu, MenuItem } from "@mui/material";
import Iconify from "src/components/iconify";
import { CustomTabs } from "src/components/tabs/tabs";
import { paths } from "src/routes/paths";
import { protoRunId } from "../_mock/executionAdapter";
import { getEnvironment } from "../_mock/environments";
import { getSurface } from "../_mock/surfaces";
import { BOOT_STEPS } from "../_mock/runStream";
import { useSimStore, useEnvState } from "../store";
import { setupGaps } from "../_mock/setupGaps";
import { subscribeBuilderPrompt } from "../_mock/builderPromptBus";
import { environmentVersions } from "../_mock/versions";
import { getAgentType } from "../_mock/agentTypes";
import { SurfaceIcon, EmptyState, SectionCard } from "../components/primitives";
import { ProvisioningPanel } from "../components/loading";
import AssistantConsole from "../assistant/AssistantConsole";
import OverviewPanel from "./OverviewPanel";
import AgentsPanel from "./AgentsPanel";
import ScenariosStep from "./ScenariosStep";
import EvalsStep from "./EvalsStep";
import RunsPanel from "./RunsPanel";
import VersionBar from "./VersionBar";
import EnvVersionPin from "./EnvVersionPin";
import BuildRecordPanel from "./BuildRecordPanel";
import RlContractPanel from "./RlContractPanel";
import SettingsPanel from "./SettingsPanel";

/**
 * `setup` marks the items that gate a run — those carry a completion tick and
 * feed the progress counter. The rest are views of the environment itself, so
 * the rail splits them rather than presenting eight equal steps.
 */
/*
  Nikhil's env-first feedback: env is the reusable asset, agent is
  one attribute. Agent details AND version management live inside
  Overview — no standalone tab. "Manage versions" opens a drawer
  overlaying Overview so the user stays on the same tab.
*/
/*
  Horizontal tabs, unified with the build/review screen (chat on the left,
  tabs on the right). The post-run "Environment" rail entries — Instances,
  Files, Interface, Settings — are dropped; what's left is the contract, the
  world, and grading. Agent + version management live inside Overview, so
  there's no standalone Agent tab.
*/
const TABS = [
  { id: "overview",  label: "Overview",          icon: "solar:widget-5-linear" },
  { id: "contract",  label: "Contract",          icon: "solar:document-text-linear" },
  { id: "scenarios", label: "Scenarios",         icon: "solar:layers-minimalistic-linear", badge: "scenarios" },
  { id: "evals",     label: "Evaluations",       icon: "solar:shield-check-linear", badge: "evals" },
  { id: "runs",      label: "Runs",              icon: "solar:play-circle-linear", badge: "runs" },
  { id: "settings",  label: "Settings",          icon: "solar:settings-linear" },
];

/* `agent` isn't a visible tab (it lives inside Overview) but stays a valid
   panel so Overview's "Manage versions" / the active-agent pill still work. */
const PANEL_IDS = [...TABS.map((t) => t.id), "agent"];

/* Suggested builder prompts per tab — the same console the build screen uses. */
const CHIPS_BY_TAB = {
  overview: [
    "Summarise what's in this environment",
    "What's still missing before we can run?",
    "Explain the tools and rules to me",
  ],
  contract: [
    "Tighten the refund rule",
    "Add a hard rule about escalations",
    "Explain the reward function",
  ],
  scenarios: [
    "Add a dispute case",
    "Add an edge case where a tool fails",
    "Rewrite the rushed-caller persona",
  ],
  evals: [
    "Add a grader for tool-choice correctness",
    "Tighten the hand-off quality bar",
    "Explain what each grader measures",
  ],
  build: [
    "Why was this tool included?",
    "What did we infer vs read directly?",
  ],
  runs: [
    "Summarise the last run",
    "Which scenarios fail most often?",
  ],
  settings: [
    "Rotate my OpenAI key",
    "Which env vars are the grader reading?",
    "Change the task timeout to 10 minutes",
  ],
};

/* Setup-gap areas map onto the tab that owns the underlying answer, so a
   blocking gap surfaces as a badge on that tab. */
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

  const { envState, patch, addAgentVersion, canRun } = useEnvState(envId);

  // Opening an environment goes straight to it — a boot sequence on every entry
  // is a delay the user did not ask for. The sequence is kept for "Reset state",
  // where re-provisioning is the whole point of pressing the button.
  const [booting, setBooting] = useState(false);
  /* Secondary header actions (Fork, …) live in an overflow menu so the header
     keeps its focus on the primary action, Run simulation. */
  const [actionsAnchor, setActionsAnchor] = useState(null);

  /* The builder console, unified with the build/review screen. A prototype
     chat: mock replies, seeded greeting once the env resolves. */
  const [turns, setTurns] = useState([]);
  const [chatRunning, setChatRunning] = useState(false);

  // Adopt on direct navigation so a deep link works from a cold start.
  useEffect(() => {
    if (env && !state.myEnvironments.some((e) => e.id === env.id)) {
      dispatch({ type: "adoptEnvironment", env, now: new Date().toISOString() });
    }
  }, [env, state.myEnvironments, dispatch]);

  useEffect(() => {
    if (!env) return;
    setTurns((prev) => (prev.length ? prev : [{
      id: "ws-greet",
      role: "assistant",
      steps: [{
        kind: "note",
        text: `${env.name} is live. Ask me to tweak scenarios, tighten a rule, or add an eval — or edit directly on the right.`,
      }],
    }]));
  }, [env?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  /* Module-level pub/sub for builder-prompt suggestions. The Edit-scenario
     drawer's suggestion pills emit here; we append the message directly
     to `turns` (rather than routing through `sendChat`) so this doesn't
     depend on `sendChat` being defined below in the file. */
  useEffect(() => {
    return subscribeBuilderPrompt((text) => {
      // eslint-disable-next-line no-console
      console.log("[simv2] builder prompt received:", text);
      setTurns((prev) => [...prev, { id: `u-${Date.now()}`, role: "user", text }]);
      setChatRunning(true);
      setTimeout(() => {
        setChatRunning(false);
        setTurns((prev) => [...prev, {
          id: `a-${Date.now()}`,
          role: "assistant",
          steps: [{ kind: "note", text: mockWorkspaceReply(text) }],
        }]);
      }, 900);
    });
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  /*
    Heal legacy state whose `envState.evals` was seeded to [] before we
    started auto-adding the env's preset at build time. Fires once per
    env id and only when there's nothing in evals — user removals stay
    removed. Without this the Overview page fires the "Add evaluations
    to run" gap on any env built pre-fix, even though the eval tab would
    auto-seed the moment it mounted. */
  useEffect(() => {
    if (!env?.id) return;
    if ((envState?.evals?.length || 0) > 0) return;
    const preset = env?.evalPreset || [];
    if (!preset.length) return;
    patch({ evals: preset });
  }, [env?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!env) {
    /* Still loading the store — a custom-built env lives only in
       myEnvironments, which is empty until hydrate runs. Showing
       "not found" here would flash on every cold load / HMR reload. */
    if (!state.hydrated) {
      return <Box sx={{ p: 2, height: "100%", minHeight: 420, display: "grid", placeItems: "center" }} />;
    }
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
  /* The Runs tab only exists once a run does — a fresh environment has no run
     history to show, and the first run is started from the header's "Run
     simulation" button (which goes straight to the live run view). */
  const hasRuns = (envState.runs?.length || 0) > 0;
  const visibleTabs = TABS.filter((t) => t.id !== "runs" || hasRuns);

  const panel = (PANEL_IDS.includes(step) && (step !== "runs" || hasRuns)) ? step : "overview";
  /* Which horizontal tab is highlighted — `agent` (no tab of its own) keeps
     Overview lit. */
  const activeTab = visibleTabs.some((t) => t.id === panel) ? panel : "overview";

  const counts = {
    scenarios: envState.scenarios.length || null,
    evals: envState.evals.length || null,
    /* The synthetic build-and-fit-check row IS run #1 (the store seeds it on
       adopt, and both the environments list and the Runs tab count it), so the
       rail badge counts it too — filtering it out here made the rail disagree
       with every other surface for a freshly-built env (list showed 1, rail 0). */
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

  /* Fork — mints a fresh env instance carrying the same world (tools, rules,
     scenarios, evals, seed) but with no agent binding and no run history, then
     lands on the new workspace. For duplicating the world for a different agent
     or team; demoted to the overflow menu since in-place agent-swap covers the
     everyday case. */
  const forkEnvironment = () => {
    setActionsAnchor(null);
    const suffix = Math.random().toString(36).slice(2, 8);
    const forkedId = `${env.id}-fork-${suffix}`;
    const now = new Date().toISOString();
    const forked = {
      ...env,
      id: forkedId,
      name: `${env.name} · fork`,
      custom: true,
      adoptedAt: undefined,
      buildProgress: undefined,
      forkedFrom: env.id,
    };
    dispatch({ type: "adoptEnvironment", env: forked, now });
    /* A fork of a template-seeded env inherits the world but becomes
       editable — the template sticker is peeled off, agent + env version
       management come back, and history restarts at v1 against the seeded
       baseline agent so the fork's lineage is its own. */
    dispatch({
      type: "patchEnvState",
      envId: forkedId,
      patch: {
        scenarios: envState.scenarios || [],
        evals: envState.evals || [],
        scenarioSource: envState.scenarioSource,
        twinBacking: envState.twinBacking,
        agent: envState.agent,
        agentVersions: envState.agentVersions
          ? envState.agentVersions.map((v) => ({ ...v }))
          : undefined,
        envVersions: [{
          id: `${forkedId}-v1`,
          label: "v1",
          createdAt: now,
          note: `Forked from ${env.name}.`,
          scenarios: (envState.scenarios || []).length,
          changed: ["fork"],
        }],
        envDerivedForAgent: "v1",
        activeAgentVersion: "v1",
        seededFromTemplate: false,
      },
    });
    navigate(paths.dashboard.simulate.environmentDetail(forkedId));
  };

  /* Template-seeded envs are read-only until forked. Locks the env-version
     dropdown, the Manage-versions button, and the refresh banner; promotes
     Fork from the overflow menu to a top-level action beside Run simulation. */
  const isSeededTemplate = !!envState.seededFromTemplate;

  const chips = CHIPS_BY_TAB[activeTab] || CHIPS_BY_TAB.overview;
  const sendChat = (text) => {
    const trimmed = (text || "").trim();
    if (!trimmed) return;
    setTurns((prev) => [...prev, { id: `u-${Date.now()}`, role: "user", text: trimmed }]);
    setChatRunning(true);
    setTimeout(() => {
      setChatRunning(false);
      setTurns((prev) => [...prev, {
        id: `a-${Date.now()}`,
        role: "assistant",
        steps: [{ kind: "note", text: mockWorkspaceReply(trimmed) }],
      }]);
    }, 900);
  };

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
            <EnvVersionPin env={env} envState={envState} patch={patch} readOnly={isSeededTemplate} />
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


        {/*
          Reset state only makes sense for a clone/twin-backed env, where it
          re-provisions the live sandbox (zero activity counters, bump
          provisionedAt to drop prior runs, restart the TTL). A plain seeded
          env has no live sandbox to reset between runs — each simulated run is
          self-contained — so the button is hidden there.
        */}
        {envState?.twinBacking && (
          <Button
            variant="outlined"
            size="small"
            startIcon={<Iconify icon="solar:restart-linear" width={16} />}
            onClick={() => {
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
              setBooting(true);
            }}
            sx={{ color: "text.primary", borderColor: "divider", typography: "s2", fontWeight: 600 }}
          >
            Reset state
          </Button>
        )}
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

        {/*
          Template-seeded envs surface Fork inside the Test-subject card on
          Overview (the primary way out of the locked template), so the
          header overflow is suppressed for them. Regular envs keep the
          overflow menu with Fork tucked inside.
        */}
        {!isSeededTemplate && (
          <>
            <Tooltip arrow title="More actions">
              <IconButton
                size="small"
                onClick={(e) => setActionsAnchor(e.currentTarget)}
                sx={{ color: "text.subtitle" }}
              >
                <Iconify icon="solar:menu-dots-bold" width={18} />
              </IconButton>
            </Tooltip>
            <Menu
              anchorEl={actionsAnchor}
              open={!!actionsAnchor}
              onClose={() => setActionsAnchor(null)}
              anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
              transformOrigin={{ vertical: "top", horizontal: "right" }}
              slotProps={{ paper: { sx: { minWidth: 260, mt: 0.5 } } }}
            >
              <MenuItem onClick={forkEnvironment} sx={{ alignItems: "flex-start", gap: 1.25, py: 1 }}>
                <Iconify icon="solar:copy-linear" width={16} sx={{ color: "text.subtitle", mt: "2px", flexShrink: 0 }} />
                <Box minWidth={0}>
                  <Typography sx={{ typography: "s2", fontWeight: 600 }}>Fork environment</Typography>
                  <Typography sx={{ typography: "s3", color: "text.subtitle", whiteSpace: "normal" }}>
                    Duplicate the world for a different agent or team. Agent + runs reset.
                  </Typography>
                </Box>
              </MenuItem>
            </Menu>
          </>
        )}
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

      {/* ── body: builder console (left) + tabbed panels (right) —
            unified with the build/review screen ── */}
      <Box
        sx={{
          flex: 1, minHeight: 0, display: "grid", gap: 2, p: 2,
          gridTemplateColumns: { xs: "1fr", lg: "minmax(340px, 400px) 1fr" },
        }}
      >
        <SectionCard sx={{ height: "100%", minHeight: 0, display: "flex", flexDirection: "column", overflow: "hidden" }}>
          <AssistantConsole
            turns={turns}
            running={chatRunning}
            chips={chips}
            onSend={sendChat}
            onChip={sendChat}
          />
        </SectionCard>

        <Box
          sx={{
            height: "100%", minHeight: 0, display: "flex", flexDirection: "column",
            border: "1px solid", borderColor: "divider", borderRadius: 1.5,
            bgcolor: "background.paper", overflow: "hidden",
          }}
        >
          <Box sx={{ flexShrink: 0, borderBottom: "1px solid", borderColor: "divider" }}>
            <CustomTabs
              value={activeTab}
              onChange={(_, v) => go(v)}
              variant="scrollable"
              scrollButtons={false}
              sx={{ minHeight: 42, px: 2.5, "& .MuiTab-root": { typography: "s2", minHeight: 42 } }}
            >
              {visibleTabs.map((t) => (
                <Tab key={t.id} value={t.id} sx={{ minHeight: 42 }} label={<TabLabel tab={t} counts={counts} gaps={gapsByStep[t.id]} />} />
              ))}
            </CustomTabs>
          </Box>

          {/*
            Fall back to Overview for an unrecognised step rather than rendering
            an empty pane — a bad link should land somewhere, not nowhere.
          */}
          <Box sx={{ flex: 1, minWidth: 0, minHeight: 0, overflow: "auto" }}>
            {panel === "agent" ? (
              <AgentsPanel env={env} envState={envState} patch={patch} onGo={go} />
            ) : panel === "scenarios" ? (
              <ScenariosStep env={env} envState={envState} patch={patch} onGo={go} onBuilderPrompt={sendChat} />
            ) : panel === "evals" ? (
              <EvalsStep env={env} envState={envState} patch={patch} onGo={go} />
            ) : panel === "runs" ? (
              <RunsPanel env={env} envState={envState} onGo={go} />
            ) : panel === "contract" ? (
              <RlContractPanel env={env} envState={envState} patch={patch} onGo={go} />
            ) : panel === "settings" ? (
              <SettingsPanel env={env} envState={envState} patch={patch} />
            ) : panel === "build" ? (
              <BuildRecordPanel env={env} envState={envState} patch={patch} />
            ) : (
              <OverviewPanel env={env} envState={envState} patch={patch} onGo={go} agentConnected={!!envState.agent} locked={isSeededTemplate} onFork={forkEnvironment} />
            )}
          </Box>
        </Box>
      </Box>
    </Box>
  );
}

/* ── one horizontal tab's label — name + optional count + gap badge ─────── */

function TabLabel({ tab, counts, gaps }) {
  const count = tab.badge ? counts[tab.id] : null;
  return (
    <Stack direction="row" alignItems="center" spacing={0.75}>
      <Typography component="span" sx={{ typography: "s2", color: "inherit" }}>{tab.label}</Typography>
      {count != null && count > 0 && (
        <Typography component="span" sx={{ typography: "s3", color: "text.subtitle", fontVariantNumeric: "tabular-nums" }}>
          {count}
        </Typography>
      )}
      {gaps?.length > 0 && (
        <Tooltip
          arrow
          title={
            <Box>
              <Typography sx={{ typography: "s3", fontWeight: 700, mb: 0.375 }}>
                Needs your input before you can run:
              </Typography>
              {gaps.map((g) => (
                <Typography key={g.id} sx={{ typography: "s3", opacity: 0.9 }}>· {g.title}</Typography>
              ))}
            </Box>
          }
        >
          <Box
            sx={{
              display: "grid", placeItems: "center", flexShrink: 0,
              minWidth: 16, height: 16, px: "5px", borderRadius: "8px",
              bgcolor: (th) => alpha("#DC2626", th.palette.mode === "dark" ? 0.2 : 0.12),
              color: "#DC2626", typography: "s3", fontWeight: 700, lineHeight: 1,
              fontVariantNumeric: "tabular-nums", fontSize: 10,
            }}
          >
            {gaps.length}
          </Box>
        </Tooltip>
      )}
    </Stack>
  );
}
TabLabel.propTypes = { tab: PropTypes.object, counts: PropTypes.object, gaps: PropTypes.array };

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
/* Prototype builder replies for the workspace console. */
function mockWorkspaceReply(userText) {
  const t = userText.toLowerCase();
  if (/drop|remove|cut/.test(t) && /scenario/.test(t)) {
    return "Dropped the matching scenarios — the Scenarios tab on the right is updated.";
  }
  if (/add/.test(t) && /scenario/.test(t)) {
    return "Added a scenario. You'll see it in the Scenarios tab on the right.";
  }
  if (/rule|refund|escalat/.test(t)) {
    return "Updated the rule. The grader will enforce the new wording on the next run.";
  }
  if (/eval|grader|grade/.test(t)) {
    return "Added that grader on the Evaluations tab — it'll score every scenario on the next run.";
  }
  if (/run|fail|last/.test(t)) {
    return "Pulled that from the latest run — open the Runs tab on the right for the full breakdown.";
  }
  return "Applied that to the environment — the panels on the right reflect the change.";
}

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


