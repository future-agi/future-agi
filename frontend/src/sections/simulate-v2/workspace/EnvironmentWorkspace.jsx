import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { alpha } from "@mui/material/styles";
import PropTypes from "prop-types";
import {
  Box, Stack, Typography, Button, Tooltip, Tab, IconButton, Menu, MenuItem, useMediaQuery,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { CustomTabs } from "src/components/tabs/tabs";
import { paths } from "src/routes/paths";
import { protoRunId } from "../_mock/executionAdapter";
import { runListCount } from "../_mock/runList";
import { scenariosFromDescription } from "../_mock/scenarios";
import { scenarioPoolFor } from "../_mock/scenarioPool";
import { detectAddScenariosIntent, describedAsk } from "../_mock/addScenariosIntent";
import { stampNewBatch, unanswerableNote, rejectedNote } from "../_mock/addScenarios";
import { asProposal } from "../_mock/scenarioSources";
import { currentUser } from "../_mock/scenarioProvenance";
import { getEnvironment } from "../_mock/environments";
import { getSurface } from "../_mock/surfaces";
import { BOOT_STEPS } from "../_mock/runStream";
import { useSimStore, useEnvState } from "../store";
import DeleteEnvironmentDialog from "../environments/DeleteEnvironmentDialog";
import TrialsPicker from "./scenarios/TrialsPicker";
import { setupGaps, runReadiness, gapMeter, gapCoverage } from "../_mock/setupGaps";
import { subscribeGapsInbox, openGapsInbox } from "../_mock/gapsInboxBus";
import { scenarioStepOf, versionCardOf } from "../_mock/versionCard";
import SideDrawer from "../components/SideDrawer";
import SetupGapsPanel from "./SetupGapsPanel";
import { runPlan } from "../_mock/proofs";
import { runScopeOf } from "../_mock/runScope";
import { resolveEval } from "../_mock/evals";
import { estimateRun, checkUsage, meterLine, limitMessage, budgetNotice } from "../_mock/metering";
import { runSizeOf, parallelOf } from "../_mock/runStream";
import RunUsageBanner from "./RunUsageBanner";
import IssueReturnBanner from "../errorFeed/IssueReturnBanner";
import { subscribeBuilderPrompt, subscribeOpenBuilderChat } from "../_mock/builderPromptBus";
import { subscribeComposerScaffold } from "../_mock/composerScaffoldBus";
import { subscribeScenarioSelection, clearScenarioSelection, getScenarioSelection } from "../_mock/scenarioSelectionBus";
import { subscribeBuilderMode } from "../_mock/builderModeBus";
import { currentAgentVersion, currentEnvVersion, environmentVersions, scenariosInSet } from "../_mock/versions";
import { withPinnedWorld, worldToolsFor } from "../_mock/toolFit";
import { proofKindOf, isComingSoon, COMING_SOON_NOTE, runDefaultsOf } from "../_mock/rsiModel";
import { finishBuildPatch } from "../_mock/quickBuild";
import { envLabelOf } from "../_mock/envVersionKinds";
import { isConnectOnly } from "../_mock/envModel";
import { currentEnvSnapshot } from "../_mock/envSnapshot";
import { SurfaceIcon, EmptyState } from "../components/primitives";
import { ProvisioningPanel } from "../components/loading";
import AssistantConsole from "../assistant/AssistantConsole";
import BuilderRail from "./BuilderRail";
import { readBuilderCollapsed, writeBuilderCollapsed } from "./builderCollapse";
import OverviewPanel from "./OverviewPanel";
import ScenariosStep from "./ScenariosStep";
import EvalsStep from "./EvalsStep";
import RunsPanel from "./RunsPanel";
import VersionBar from "./VersionBar";
import useEnvViewAs from "./useEnvViewAs";
import BuildRecordPanel from "./BuildRecordPanel";
import RlContractPanel from "./RlContractPanel";
import { parseContractIntent } from "../_mock/chatActions";
import { selectionEditOf } from "../_mock/scenarioEdits";
import { contractEditPlan } from "../_mock/rlContract";
import useRunFitCheck from "../run/useRunFitCheck";
import useRunGroup from "../run/useRunGroup";
import AlsoRunIn from "../run/AlsoRunIn";
import WorldPanel from "./WorldPanel";
import SettingsPanel from "./SettingsPanel";
import { clickable, focusRing } from "../components/clickable";

/**
 * `setup` marks the items that gate a run — those carry a completion tick and
 * feed the progress counter. The rest are views of the environment itself, so
 * the rail splits them rather than presenting eight equal steps.
 */
/*
  Nikhil's env-first feedback: env is the reusable asset, agent is
  one attribute. Overview names it in one line — the version tested here,
  Switch when production is ahead — and View agent opens it in Agents, where
  everything the agent owns lives.
*/
/*
  Horizontal tabs, unified with the build/review screen (chat on the left,
  tabs on the right). The post-run "Environment" rail entries — Instances,
  Files, Interface, Settings — are dropped; what's left is the contract, the
  world, and grading. The agent isn't a tab: Overview's agent line opens it
  in Agents.
*/
const TABS = [
  { id: "overview",  label: "Overview",          icon: "solar:widget-5-linear" },
  { id: "world",     label: "World",             icon: "solar:globe-linear" },
  { id: "contract",  label: "Contract",          icon: "solar:document-text-linear" },
  { id: "scenarios", label: "Scenarios",         icon: "solar:layers-minimalistic-linear", badge: "scenarios" },
  { id: "evals",     label: "Evaluations",       icon: "solar:shield-check-linear", badge: "evals" },
  { id: "runs",      label: "Runs",              icon: "solar:play-circle-linear", badge: "runs" },
  { id: "settings",  label: "Settings",          icon: "solar:settings-linear" },
];

/*
  Each tab opens straight on its content — a heading that repeated the tab
  name was spent height. What that heading used to say is the tab's hover
  hint instead. Overview has none: its description is content.
*/
const tabHintOf = (id, env) => ({
  world: isConnectOnly(env)
    ? "What a run of this environment stands on. The environment version is the contract alone."
    : "What your agent talks to during a run — the data, and the tool handlers that answer every call. It learns a new tool whenever a later agent version brings one.",
  contract: "The rules the agent holds to and what it's for, then how a conversation runs — when it ends, who else is in it, and how it's scored. Editing it makes a new environment version; the scenarios that depend on the change are marked Re-check.",
  scenarios: "Each scenario is one task your agent has to complete, with its own caller. Every agent version runs the same suite; comparisons run over the scenarios both runs share.",
  evals: "Sub-goals decide pass or fail. Mark an evaluation required to let it fail a conversation; the rest score it.",
  runs: "Every simulation of this environment and how it scored. Select two or more runs to compare them scenario by scenario.",
  settings: "How this environment is built and how runs behave inside it.",
}[id] || "");

const PANEL_IDS = TABS.map((t) => t.id);

/* Suggested builder prompts per tab — the same console the build screen uses. */
const CHIPS_BY_TAB = {
  overview: [
    "Summarise what's in this environment",
    "What's still missing before we can run?",
    "Explain the tools and rules to me",
  ],
  contract: [
    "When does a run end early?",
    "Add an actor who wants something else",
    "How is a run scored?",
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
    "Which environment variables is the grader reading?",
    "Change the task timeout to 10 minutes",
  ],
};

/* Setup-gap areas map onto the visible tab that owns the underlying answer,
   so a blocking gap surfaces as a badge on that tab. The agent is Overview →
   Test subject; tools and the sandbox are the World. */
const GAP_AREA_TO_STEP = {
  Agent: "overview",
  Sandbox: "world",
  Tools: "world",
  World: "world",
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
  /* Narrow screens: the header keeps its one line by saying less. */
  const wide = useMediaQuery((t) => t.breakpoints.up("lg"));

  /* Back always lands on the Environments landing page. */
  const goBack = () => navigate(paths.dashboard.simulate.environments);
  /* Asked first when the environment was built but never run. */

  const env =
    getEnvironment(envId) || state.myEnvironments.find((e) => e.id === envId);

  const { envState, patch, canRun } = useEnvState(envId);
  /* Locked = a catalogue template opened as-is. A copy adopted with "Use
     this template" carries `templateId` and is the user's own — editable. */
  const isSeededTemplate = !!envState.seededFromTemplate && !env?.templateId;
  /* The newest state, for chat confirmations answered after a re-render. */
  const latestEnvState = useRef(envState);
  latestEnvState.current = envState;
  /* Panels see the world of the pinned environment version. */
  const worldEnv = useMemo(
    () => withPinnedWorld(env, envState),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [env, envState.envVersions, envState.activeEnvVersion],
  );
  /* Every new run asks first when the agent calls a tool the world can't answer. */
  const { check: checkRunFit, dialog: runFitDialog } = useRunFitCheck(env, envState, patch, { locked: isSeededTemplate });

  // Opening an environment goes straight to it — a boot sequence on every entry
  // is a delay the user did not ask for. The sequence is kept for "Reset state",
  // where re-provisioning is the whole point of pressing the button.
  const [booting, setBooting] = useState(false);

  /* The builder console, unified with the build/review screen. A prototype
     chat: mock replies, seeded greeting once the env resolves. */
  const [turns, setTurns] = useState([]);
  const [chatRunning, setChatRunning] = useState(false);
  /* Builder pane can fold down to a rail so the panels get the full width.
     Remembered per browser — a viewer convenience, not shared state. */
  const [builderCollapsed, setBuilderCollapsed] = useState(readBuilderCollapsed);
  const [unseenTurns, setUnseenTurns] = useState(0);
  const setCollapsed = (v) => {
    setBuilderCollapsed(v);
    if (!v) setUnseenTurns(0);
    writeBuilderCollapsed(v);
  };
  /* While folded, count builder replies so the rail can say something
     arrived instead of hiding it. */
  const lastTurnCount = useRef(0);
  useEffect(() => {
    const added = turns.length - lastTurnCount.current;
    lastTurnCount.current = turns.length;
    if (builderCollapsed && added > 0 && turns[turns.length - 1]?.role === "assistant") setUnseenTurns((n) => n + 1);
  }, [turns, builderCollapsed]);
  /* "Edit with builder" pins a chip in the composer — that's a request to
     use the builder, so it opens. */
  useEffect(() => subscribeComposerScaffold(() => setCollapsed(false)), []);
  /* So does a scenario's "From chat" badge, and Add scenarios → From chat. */
  useEffect(() => subscribeOpenBuilderChat(() => setCollapsed(false)), []);
  /*
    Scenario selection published by the table on the Scenarios tab.
    When non-empty, the next chat send is treated as a bulk edit
    against those rows: the reply names them and the selection
    clears on submit so the next message starts fresh.
  */
  /* Subscribe via useSyncExternalStore so React guarantees the header
     button re-renders whenever ScenariosStep publishes a new
     selection — the previous useState + useEffect wire sometimes lost
     the subscription across HMR module swaps. */
  const scenarioSelection = useSyncExternalStore(
    subscribeScenarioSelection,
    getScenarioSelection,
    getScenarioSelection,
  );
  /* What Run will actually run — the selection (or everything) on the pinned
     version, minus what can't stage. The same plan the live run uses, so the
     count here is the count there. */
  const plan = useMemo(
    () => (env ? runPlan(env, envState, { only: scenarioSelection?.ids?.length ? scenarioSelection.ids : null }) : null),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [env, envState.scenarios, envState.envVersions, envState.activeEnvVersion, scenarioSelection],
  );
  /* Header trials picker — how many times each scenario runs when the
     user clicks the top-of-page "Run simulation" button (no selection).
     Same PRD AC-10.7 dial as the SelectionBar's picker, but scoped to
     "run all". Run simulation starts straight away with this value. */
  /* Three samples by default — the fewest where a scenario can be flaky
     ("passed twice, failed once") rather than a coin flip read as a verdict. */
  /* …or the environment's own default, set in Settings → Simulation defaults. */
  const [headerTrials, setHeaderTrials] = useState(() => runDefaultsOf(envState).repeats || 3);
  /* Other environments the next run also starts in — one run each, same
     agent version, read together as a group. Empty is a plain run. */
  const [alsoRunIn, setAlsoRunIn] = useState([]);
  const runGroup = useRunGroup(envId);
  /* Header overflow menu (Delete environment) and its confirm step. */
  const [actionsAnchor, setActionsAnchor] = useState(null);
  const [runMenu, setRunMenu] = useState(null);
  const [confirmDelete, setConfirmDelete] = useState(null);
  /*
    Builder mode — Auto (default) or Guided. In Guided, the builder
    pauses at decisions and asks Claude-style AskUserQuestion cards
    in the chat instead of quietly applying a default. Subscribed
    from the module bus so the composer's picker and the reply
    logic stay in sync.
  */
  const [builderMode, setBuilderModeLocal] = useState("auto");
  useEffect(() => subscribeBuilderMode(setBuilderModeLocal), []);

  /* "Needs input" — the setup-gaps inbox, in a right drawer. Opened from
     the header count, or from the Overview tab's blocking list (via the bus). */
  const [inbox, setInbox] = useState({ open: false, focus: null });
  const { viewAs: viewingOlder } = useEnvViewAs(env, envState);
  useEffect(() => subscribeGapsInbox((focus) => setInbox({ open: true, focus })), []);

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
        text: env.buildStatus === "building"
          ? `${env.name} is still building. Once it's ready, ask me to add scenarios, change a rule, or add an evaluation — or edit directly on the right.`
          : `${env.name} is ready. Ask me to add scenarios, change a rule, or add an evaluation — or edit directly on the right.`,
      }],
    }]));
  }, [env?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  /* Module-level pub/sub for builder-prompt suggestions. The Edit-scenario
     drawer's suggestion pills emit here; we append the message directly
     to `turns` (rather than routing through `sendChat`) so this doesn't
     depend on `sendChat` being defined below in the file. */
  useEffect(() => {
    return subscribeBuilderPrompt((text) => {
      setTurns((prev) => [...prev, { id: `u-${Date.now()}`, role: "user", text }]);
      setChatRunning(true);
      setTimeout(() => {
        setChatRunning(false);
        setTurns((prev) => [...prev, {
          id: `a-${Date.now()}`,
          role: "assistant",
          steps: [{ kind: "note", text: workspaceReply(text) }],
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
    auto-seed the moment it mounted. Once per environment (`evalsSeeded`), so
    clearing them stays cleared and Run says it needs one. */
  useEffect(() => {
    if (!env?.id || envState?.evalsSeeded) return;
    if ((envState?.evals?.length || 0) > 0) { patch({ evalsSeeded: true }); return; }
    const preset = env?.evalPreset || [];
    if (!preset.length) return;
    patch({ evals: preset, evalsSeeded: true });
  }, [env?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  /* A build runs on the builder page. Left before its last stage, the
     environment would read "Building" forever — so opening it finishes the
     build here: the scenarios it was asked for, its v1, ready. Its first run
     is started by the person, from Run simulation. */
  useEffect(() => {
    if (!state.hydrated || env?.buildStatus !== "building") return;
    const done = finishBuildPatch(env, envState);
    if (!done) return;
    dispatch({ type: "patchEnvState", envId: env.id, patch: done.statePatch });
    dispatch({ type: "patchEnvironment", envId: env.id, patch: done.envPatch });
    /* No run is made here — the first run is started from Run simulation. */
  }, [env?.id, state.hydrated]); // eslint-disable-line react-hooks/exhaustive-deps

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
  /*
    Env is Live when its build has settled. While derivation is in
    flight (initial build, or a re-derivation triggered by a version
    bump), the builder input is locked because you can't correct a
    world that isn't done being built. Seeded envs default to not
    building, so this is Live by default in the prototype.
  */
  const envLive = env.buildStatus !== "building";

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
  /* startRun mints a fresh runId and navigates to the live-run view.
     When called with a non-empty scenarioIds array, the ids are
     tacked on as `?only=` (matching the existing subset convention
     LiveRunView already reads) so the run only exercises that
     subset — otherwise the run covers every scenario in the env. */
  const launchRun = (scenarioIds, trials, { agent } = {}) => {
    const runId = protoRunId(envId, Date.now().toString(36));
    let url = paths.dashboard.simulate.simulationRun(envId, runId);
    const params = new URLSearchParams();
    /* A bridge or backfill from the preview runs an older agent version. */
    if (agent) params.set("agent", agent);
    if (Array.isArray(scenarioIds) && scenarioIds.length > 0) {
      params.set("only", scenarioIds.join(","));
    }
    /*
      PRD §10.2 AC-10.7 — k trials per scenario. Always stated on the
      link, so a run is exactly what was asked for (LiveRunView falls
      back to 3 only for links that carry none).
    */
    const k = Number(trials);
    if (Number.isFinite(k) && k >= 1) {
      params.set("trials", String(Math.min(20, Math.floor(k))));
    }
    const qs = params.toString();
    if (qs) url += `?${qs}`;
    navigate(url);
  };
  /* `also` — other environments to start alongside, as a run group. A
     group runs each environment's own full scenario set, so it never
     carries a selection. */
  const runNow = (scenarioIds, trials, { also } = {}) => checkRunFit(() => (
    also?.length && !scenarioIds?.length
      ? runGroup.start(also, { trials: Number(trials) || 3 })
      : launchRun(scenarioIds, trials)
  ));
  const startRun = (scenarioIds, trials, opts = {}) => runNow(scenarioIds, trials, opts);

  // Unknown steps render Overview, and the rail highlights it, so the URL and
  // the highlighted item never disagree.
  /*
    Evals still need scenarios before anything can be added — an eval's
    variables map onto what a run produces — but that is enforced *on* the
    Evals step, which says so and offers the way forward. Redirecting to
    Scenarios instead made the rail item look broken: you click Evals and
    nothing appears to happen.
  */
  /* Every tab is always there, Runs included — a fresh environment's Runs
     tab says there are no runs yet and offers the baseline run. */
  const visibleTabs = TABS;

  const panel = PANEL_IDS.includes(step) ? step : "overview";
  /* Which horizontal tab is highlighted — `agent` (no tab of its own) keeps
     Overview lit. */
  const activeTab = visibleTabs.some((t) => t.id === panel) ? panel : "overview";

  const counts = {
    /* Scenarios in the set — the same count Overview and the version bar show. */
    scenarios: scenariosInSet(envState, currentEnvVersion(env, envState)?.label).length || null,
    evals: envState.evals.length || null,
    /* Every row the Runs list shows — manual runs, self-improvement trials and
       the queued one — counted by the same rules the list uses. Counting only
       the manual runs put "2" on the tab above a list of 11. */
    runs: runListCount(env, envState) || null,
  };
  /*
    Setup gaps are shown as amber dots on the rail items that own them
    instead of a dedicated "Needs input" step. Each blocking gap
    surfaces on the panel where the answer belongs — Agent for
    sandbox/tool secrets, Contract for manifest questions, Evaluations
    for grading choices — so a user always knows where to go.
  */
  const allGaps = setupGaps(worldEnv, envState);
  /* The inbox's meter and what a run would cover with optional gaps still
     open (PRD v6 AC-3.7). */
  const gapTally = gapMeter(allGaps);
  const gapCover = gapCoverage(worldEnv, envState, allGaps);
  const gapsByStep = {};
  allGaps.forEach((g) => {
    if (g.status !== "blocking") return;
    const stepId = GAP_AREA_TO_STEP[g.area];
    if (!stepId) return;
    (gapsByStep[stepId] = gapsByStep[stepId] || []).push(g);
  });

  /*
    Reason the Run simulation button is disabled — the same readiness every
    run entry point checks (agent, where it runs, scenarios, evaluations),
    then whether anything selected can actually stage on the pinned version.
  */
  const readiness = runReadiness(envState);
  const runnable = plan?.run.length || 0;
  /* Any blocking gap holds the run — readiness covers the common ones; the
     inbox may hold others (a Live tool with no safe target, consent). */
  const firstBlockingGap = allGaps.find((g) => g.status === "blocking");
  const runBlockedReason = (() => {
    /* An older version is read-only — runs go on the version in use. */
    if (viewingOlder) return "Back to current to run";
    if (!readiness.ready) return readiness.reason;
    if (firstBlockingGap) return `${firstBlockingGap.title} — see Needs input`;
    if (runnable === 0) {
      return plan?.selectionMissing
        ? `None of the selected scenarios are part of environment ${envLabelOf(env, envState, plan.envVersion)}`
        : `Nothing can stage on environment ${envLabelOf(env, envState, plan?.envVersion)} — see the Scenarios tab`;
    }
    return "";
  })();
  const runEnabled = canRun && runnable > 0 && !firstBlockingGap && !viewingOlder;

  /*
    What the next Run meters, and the pre-check at that paid boundary
    (AC-16.7, AC-16.8). Counted over exactly what Run will take — the
    selection, or the plan minus what sits out on an open optional gap —
    times the repeats picked. A refusal holds Run with the standard limit
    message; a budget warning lets it go and says so quietly.
  */
  const headerSelCount = scenarioSelection?.ids?.length || 0;
  const headerGrouped = headerSelCount === 0
    && alsoRunIn.some((id) => runGroup.candidates.some((c) => c.envId === id && c.ready));
  /* One count of what Run takes, everywhere it's said (button, Repeats,
     tooltip, the Evaluations cost): runScopeOf — the plan minus what sits
     out on an open optional question. A selection or a group is its own run. */
  const scope = runScopeOf(worldEnv, envState, { plan, gaps: allGaps });
  const meteredScenarios = runEnabled && gapTally.blocking === 0 && !headerSelCount && !headerGrouped
    ? scope.count
    : runnable;
  const sitOut = Math.max(0, runnable - meteredScenarios);
  /* How many open questions those wait on — counted, never named by gap id. */
  const sitOutGaps = scope.sitOutGaps;
  const runEstimate = estimateRun({
    env,
    scenarios: meteredScenarios,
    repeats: Number(headerTrials) || 3,
    evals: (envState.evals || []).map(resolveEval).filter(Boolean),
    runLocation: envState?.agent?.values?.runLocation || null,
  });
  const usageCheck = checkUsage(runEstimate);
  const usageRefusal = runnable > 0 ? limitMessage(usageCheck, runEstimate) : null;
  const usageNotice = runnable > 0 ? budgetNotice(usageCheck, runEstimate) : null;

  /* Leave first, then drop it: while this page is mounted, the adopt-on-open
     effect would put a missing environment straight back. */
  const deleteEnvironment = (target) => {
    setConfirmDelete(null);
    navigate(paths.dashboard.simulate.environments, { replace: true });
    dispatch({ type: "removeEnvironment", envId: target.id });
  };

  /* Fork — a fresh environment carrying everything this one is (world, tools
     and their answers, rules, scenarios, evaluations, actors, gap answers, and
     the agent it tests) but none of its runs or history, then lands on it.
     Versions restart at v1 so the fork's lineage is its own; a clone-backed
     fork gets its own sandbox rather than sharing this one's. */
  const forkEnvironment = () => {
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
    /* History stays behind; everything else comes along. */
    const {
      runs, releases, optimizations, previousAgents, unsavedEnvChanges, envVersions, activeEnvVersion,
      ...carried
    } = envState;
    const twin = envState.twinBacking;
    const agentLabel = currentAgentVersion(envState)?.label;
    dispatch({
      type: "patchEnvState",
      envId: forkedId,
      patch: {
        ...carried,
        twinBacking: twin ? {
          ...twin,
          endpoints: Object.fromEntries((twin.services || []).map((sId) => [
            sId, `https://${sId}.sandbox.futureagi.com/e/${forkedId.slice(-6)}`,
          ])),
          activity: Object.fromEntries((twin.services || []).map((sId) => [sId, { requests: 0, failures: 0 }])),
          provisionedAt: now,
        } : null,
        agentVersions: envState.agentVersions ? envState.agentVersions.map((v) => ({ ...v })) : undefined,
        /* The fork starts from the world as it stands — including tools a
           rebuild taught it — not from the first build's. */
        envVersions: [{
          id: `${forkedId}-v1`,
          label: "v1",
          createdAt: now,
          note: `Forked from ${env.name} at environment ${currentEnvSnapshot(env, envState)?.label || currentEnvVersion(env, envState).label}.`,
          scenarios: (envState.scenarios || []).length,
          changed: [],
          tools: worldToolsFor(env, envState),
          ...(agentLabel && { builtFor: agentLabel }),
        }],
        activeEnvVersion: "v1",
        activeAgentVersion: agentLabel || null,
        /* A fork tests what its source tested — pinned only if that was. */
        agentVersionPinned: !!envState.agentVersionPinned,
        /* Scenarios a rebuild added are part of this world now. */
        scenarios: (envState.scenarios || []).map((sc) => (sc.addedInEnv ? { ...sc, addedInEnv: "v1", provedAgainst: "v1" } : sc)),
        seededFromTemplate: false,
      },
    });
    navigate(paths.dashboard.simulate.environmentDetail(forkedId));
  };

  /* A locked template (isSeededTemplate, above) is read-only until forked:
     the env-version dropdown, versions, agent and world edits are locked, and
     Fork sits beside Run simulation. */

  const chips = CHIPS_BY_TAB[activeTab] || CHIPS_BY_TAB.overview;
  const sendChat = (text) => {
    const trimmed = (text || "").trim();
    if (!trimmed) return;
    /*
      Snapshot the selection at the moment of send. If the user has
      rows checked on the Scenarios tab, treat the message as a bulk
      edit against those rows — the reply names them and the
      selection clears so the next message starts fresh.
    */
    const sel = scenarioSelection;
    const hasSelection = (sel?.ids?.length || 0) > 0;
    setTurns((prev) => [...prev, { id: `u-${Date.now()}`, role: "user", text: trimmed }]);
    setChatRunning(true);
    if (hasSelection) clearScenarioSelection();

    /*
      A correction to the contract after the build — a rule, a use case, or
      the values a tool argument allows. Says what it costs first (the
      environment version it makes, the scenarios it sends to Re-check),
      then asks; confirmed, it lands the same way the Contract tab's Edit
      does. Asked for Live on a tool that writes, it says no.
    */
    const contractIntent = !hasSelection ? parseContractIntent(trimmed, { env, envState }) : null;
    if (contractIntent) {
      const say = (text) => setTurns((cur) => [...cur, { id: `a-${Date.now()}-${cur.length}`, role: "assistant", steps: [{ kind: "note", text }] }]);
      setTimeout(() => {
        setChatRunning(false);
        if (contractIntent.kind === "liveRefused") {
          say(`${contractIntent.tool} writes data, so it can't run Live — Live is only for read-only tools, once you allow it for that tool. It stays simulated against the world, recorded or fixed; change it on the World tab.`);
          return;
        }
        if (isSeededTemplate) {
          say("This is a catalogue template, so its contract is read-only. Fork it from the header, then ask again.");
          return;
        }
        const plan = contractEditPlan(env, latestEnvState.current, contractIntent.edit);
        if (!plan.ok) { say(plan.error); return; }
        setTurns((prev) => [...prev, {
          id: `a-${Date.now()}`,
          role: "assistant",
          steps: [
            { kind: "note", text: plan.costLine },
            {
              kind: "ask",
              question: {
                step: 1,
                total: 1,
                prompt: `${plan.title}?`,
                multiSelect: false,
                options: [
                  { label: `Save as environment ${plan.next}`, description: plan.detail },
                  { label: "Leave the contract as it is", description: "Nothing changes and no version is made." },
                ],
              },
              onSubmit: (answer) => {
                if (answer.pick !== 0 || answer.other?.trim()) { say("Left the contract as it is."); return; }
                /* Re-planned against the newest state — something may have moved. */
                const latest = latestEnvState.current;
                const again = contractEditPlan(env, latest, contractIntent.edit);
                if (!again.ok) { say(again.error); return; }
                patch(again.patch(latest));
                const n = again.affected.length;
                say(`Saved as environment ${again.next} — ${again.detail}. ${n ? `${n} scenario${n === 1 ? " is" : "s are"} marked Re-check on the Scenarios tab.` : "No scenario needed a re-check."} The Contract tab shows the change.`);
              },
              onSkip: () => say("Left the contract as it is."),
            },
          ],
        }]);
      }, 700);
      return;
    }

    const say = (text) => setTurns((cur) => [...cur, { id: `a-${Date.now()}-${cur.length}`, role: "assistant", steps: [{ kind: "note", text }] }]);

    /* "Add 10 more scenarios" / "add scenarios for callers disputing a double
       charge" really appends them — as proposals, reviewed on the Scenarios
       tab like every other source. */
    const addIntent = !hasSelection ? detectAddScenariosIntent(trimmed) : null;
    const proposeScenarios = (described, intent) => {
      const latest = latestEnvState.current;
      const existing = new Set((latest.scenarios || []).map((sc) => sc.id));
      const fresh = described
        ? scenariosFromDescription(env, described, intent.asked ? intent.count : 3)
        : scenarioPoolFor(env, latest).filter((sc) => !existing.has(sc.id)).slice(0, intent.count);
      if (fresh.length === 0) {
        say("Every scenario this environment would derive is already in. Tell me what's missing — e.g. “callers disputing a double charge” — and I'll write those.");
        return;
      }
      /* Stamped like every other add route — tools, proof, the environment
         version it was added under — so the tool check runs here too. */
      const batch = stampNewBatch(fresh.map((r) => ({ ...r, chatAsk: described || trimmed })), { env, envState: latest, source: "chat", actor: currentUser() });
      /* Chat-written scenarios get the same review every other source does:
         they land proposed, and join the environment only once someone
         adds them on the Scenarios tab. */
      const stamped = batch.rows.map((r) => asProposal(r, "chat"));
      const warn = [unanswerableNote(batch), rejectedNote(batch)].filter(Boolean).join(" ");
      patch({ scenarios: [...(latest.scenarios || []), ...stamped] });
      const added = stamped.length;
      /* A described ask only asked for a number if it named one. */
      const requested = described && !intent.asked ? added : intent.count;
      const shortfall = added < requested;
      const review = `${added} proposed — review and add them on the Scenarios tab.`;
      say((described
        ? `Wrote ${added} scenario${added === 1 ? "" : "s"} for “${described}” — ${stamped.map((sc) => sc.summary.toLowerCase()).join("; ")}. ${review}${shortfall ? ` That's every distinct way I could see it playing out; describe another angle for more.` : ""}`
        : shortfall
          ? `${review} (The derivation pool only had ${added} unused rows left.)`
          : review) + (warn ? ` ${warn}` : ""));
    };

    /*
      Guided mode: the builder pauses at a real decision before acting. The
      one it can act on from here is writing scenarios — which kind — and the
      answer is what it writes. Anything else gets the same plain reply as
      Auto: nothing is "applied" that wasn't.
    */
    if (builderMode === "guided" && addIntent && !describedAsk(trimmed)) {
      setTimeout(() => {
        setChatRunning(false);
        const q = guidedScenarioQuestion();
        setTurns((prev) => [...prev, {
          id: `a-${Date.now()}`,
          role: "assistant",
          steps: [
            { kind: "note", text: "Before I write them, one decision:" },
            {
              kind: "ask",
              question: q,
              onSubmit: (answer) => {
                const picked = q.options[answer.pick];
                const described = answer.other?.trim() || picked?.description || null;
                proposeScenarios(described, addIntent);
              },
              onSkip: () => proposeScenarios(null, addIntent),
            },
          ],
        }]);
      }, 900);
      return;
    }

    setTimeout(() => {
      setChatRunning(false);
      if (addIntent) {
        proposeScenarios(describedAsk(trimmed), addIntent);
        return;
      }
      /* With scenarios selected, only what the Scenarios tab can do to a
         selection is done — archive, restore, must-pass — as the next set
         version. Everything else is said plainly. */
      if (hasSelection) {
        if (isSeededTemplate) { say("This is a catalogue template, so its scenarios are read-only. Fork it from the header, then ask again."); return; }
        const out = selectionEditOf(trimmed, sel.rows, latestEnvState.current, { by: currentUser().name });
        if (out.patch) patch(out.patch);
        say(out.reply);
        return;
      }
      say(workspaceReply(trimmed));
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
        <Tooltip title="Back" arrow>
          <Button
            onClick={goBack}
            sx={{ minWidth: 32, width: 32, height: 32, p: 0, color: "text.subtitle" }}
          >
            <Iconify icon="solar:alt-arrow-left-linear" width={18} />
          </Button>
        </Tooltip>

        <SurfaceIcon surface={env.surface} size={36} />

        <Box minWidth={0} flex={1}>
          {/* One line at any width: the name truncates but never disappears,
              chips keep their shape, and the quieter ones step out first. */}
          <Stack direction="row" alignItems="center" spacing={1} sx={{ minWidth: 0, flexWrap: "nowrap" }}>
            <Typography noWrap sx={{ typography: "s1_2", fontWeight: 700, minWidth: 72, flexShrink: 1 }}>{env.name}</Typography>
            {/*
              Env status pill. `Ready` = fully booted and accepting
              edits from the builder console + inline. When the env is
              still being derived (or re-derived on a version bump),
              flips to a purple `Building` pill (the environments table's
              colour) and the builder input
              locks — you can't correct a world that isn't done being
              built.
            */}
            <Tooltip
              arrow
              title={envLive
                ? "Environment is ready. You can edit via the builder or inline."
                : "Environment is still being built. Editing resumes when it's ready."}
            >
              <Stack
                direction="row" alignItems="center" spacing={0.5}
                sx={{
                  px: 0.75, height: 22, borderRadius: 0.75, cursor: "default", flexShrink: 0, whiteSpace: "nowrap",
                  /* Ready is the quiet default — it steps out on narrow screens. */
                  display: envLive ? { xs: "none", lg: "flex" } : "flex",
                  color: envLive ? "#16A34A" : "#7857FC",
                  bgcolor: (t) => alpha(envLive ? "#16A34A" : "#7857FC", t.palette.mode === "dark" ? 0.16 : 0.1),
                  border: () => `1px solid ${alpha(envLive ? "#16A34A" : "#7857FC", 0.24)}`,
                }}
              >
                <Box
                  sx={{
                    width: 6, height: 6, borderRadius: "50%",
                    bgcolor: envLive ? "#16A34A" : "#7857FC",
                    animation: envLive ? undefined : "env-pulse 1.4s ease-in-out infinite",
                    "@keyframes env-pulse": {
                      "0%,100%": { opacity: 0.4 },
                      "50%": { opacity: 1 },
                    },
                  }}
                />
                {/* "Ready", not "Live" — Live now names how a run reaches the agent (the chip beside it). */}
                <Typography sx={{ typography: "s3", fontWeight: 600 }}>{envLive ? "Ready" : "Building"}</Typography>
              </Stack>
            </Tooltip>
            {/*
              Sealed · Live · Prompt only (RSI PRD "Environments, defined"):
              where the agent runs and how its tools are answered — which is
              how much a pass here proves. Quiet outline, the drift chip's
              shape. Browser and computer-use agents read "Coming soon".
            */}
            {(() => {
              const chips = [];
              if (envState?.agent) {
                /* A SaaS sandbox answers the agent's app calls in sandboxed copies
                   reset between runs — nothing reaches the real apps — so it
                   reads Sealed whatever kind of agent it tests. */
                const sandboxed = !!envState?.twinBacking?.services?.length;
                const pk = sandboxed ? { kind: "sealed", label: "Sealed" } : proofKindOf(envState.agent);
                chips.push({
                  key: "proof",
                  /* "Runs:" says what the chip describes, so it doesn't read as a second status beside Ready. */
                  prefix: "Runs:",
                  icon: pk.kind === "sealed" ? "solar:lock-keyhole-minimalistic-linear" : pk.kind === "live" ? "solar:plug-circle-linear" : "solar:document-text-linear",
                  label: pk.label,
                  tip: pk.kind === "prompt-only"
                    ? "Prompt only — checked by running the prompt without your tools."
                    : pk.kind === "live"
                      ? "Live — the agent runs in your account and its tools reach your real systems. Only the starting state is checked, and a model judges sub-goals from the conversation."
                      : sandboxed
                        ? "Sealed — the agent's calls to these apps land in sandboxed copies, reset between runs; sub-goals are checked against what landed there."
                        : env?.runsOn === "local" || env?.builtFrom?.worldSource?.runsOn === "local"
                          ? "Sealed — the agent's tool calls are answered by this environment's world, running on your machine through the CLI, and sub-goals are checked against the world's end state and the tool calls."
                          : "Sealed — the agent's tool calls are answered by this environment's world inside our sandbox, and sub-goals are checked against the world's end state and the tool calls.",
                });
              }
              if (isComingSoon(env)) chips.push({ key: "soon", icon: "solar:clock-circle-linear", label: "Coming soon", tip: COMING_SOON_NOTE });
              return chips.map((c) => (
                <Tooltip key={c.key} arrow title={c.tip}>
                  <Stack
                    direction="row" alignItems="center" spacing={0.5}
                    sx={{ px: 0.75, height: 22, borderRadius: 0.75, cursor: "default", border: "1px solid", borderColor: "divider", color: "text.secondary", flexShrink: 0, whiteSpace: "nowrap", display: { xs: "none", md: "flex" } }}
                  >
                    <Iconify icon={c.icon} width={11} />
                    <Typography sx={{ typography: "s3", fontWeight: 600 }}>
                      {c.prefix && <Box component="span" sx={{ fontWeight: 500, color: "text.subtitle", mr: 0.5 }}>{c.prefix}</Box>}
                      {c.label}
                    </Typography>
                  </Stack>
                </Tooltip>
              ));
            })()}
            {/*
              Drifted (PRD v7 §4.10.5): a new agent version raised nudges
              nobody has answered. E and S stay pinned meanwhile; the chip
              opens the inbox. A dismissed nudge leaves a quieter badge.
            */}
            {(() => {
              /* One card per agent version — the chip names the version, not
                 a count — or the scenario step a rebuild left. */
              const card = versionCardOf(envState);
              const step = scenarioStepOf(envState);
              const open = card?.status === "open" || step?.status === "open";
              if (!card && !step) return null;
              const label = card?.status === "open" ? `Agent ${card.newest} changed`
                : step?.status === "open" ? "Update scenarios"
                  : "Changes skipped";
              return (
                <Tooltip
                  arrow
                  title={card?.status === "open"
                    ? `Agent ${card.newest} changed. The environment and scenarios stay as they are until you rebuild them.`
                    : step?.status === "open"
                      ? "The environment was rebuilt; its scenarios are the next step."
                      : "The agent's changes were skipped — the environment and scenarios stayed as they are. Review them again any time."}
                >
                  <Stack
                    direction="row" alignItems="center" spacing={0.5}
                    {...clickable(() => openGapsInbox("nudges"))}
                    sx={{ ...focusRing,
                      px: 0.75, height: 22, borderRadius: 0.75, cursor: "pointer", border: "1px solid", flexShrink: 0, whiteSpace: "nowrap",
                      borderColor: open ? "text.secondary" : "divider",
                      color: open ? "text.primary" : "text.secondary",
                      "&:hover": { bgcolor: "action.hover" },
                    }}
                  >
                    <Iconify icon="solar:branching-paths-up-linear" width={11} />
                    <Typography sx={{ typography: "s3", fontWeight: 600 }}>{label}</Typography>
                  </Stack>
                </Tooltip>
              );
            })()}
            {/* No env-version chip here — the version bar under the header
                shows agent × env and opens the same history. */}
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
                      Sandboxed apps
                    </Typography>
                    <Typography sx={{ typography: "s3", color: "common.white" }}>
                      Sandboxed copies of {envState.twinBacking.services.join(", ")} — reset between runs.
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
                    {`${envState.twinBacking.services.length} sandboxed app${envState.twinBacking.services.length === 1 ? "" : "s"}`}
                  </Typography>
                </Stack>
              </Tooltip>
            )}
          </Stack>
          <Typography noWrap sx={{ typography: "s2", color: "text.subtitle" }}>{env.tagline}</Typography>
        </Box>


        {/*
          Reset state only makes sense for a clone/twin-backed env, where it
          re-provisions the live sandbox (zero activity counters, bump
          provisionedAt to drop prior runs). A clone never expires. A plain seeded
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
                },
              });
              setBooting(true);
            }}
            sx={{ color: "text.primary", borderColor: "divider", typography: "s2", fontWeight: 600 }}
          >
            Reset state
          </Button>
        )}
        {/* Header-anchored primary actions. Stay in the top-right in
            BOTH the empty-selection state (runs every scenario) and
            the active-selection state (runs the selected subset) so
            the button never jumps as the user checks boxes. The label
            switches scope automatically; repeats come from the pill. */}
        {(() => {
          const selectedCount = scenarioSelection?.ids?.length || 0;
          /* Only environments that can still run join the group. */
          const groupWith = selectedCount > 0
            ? []
            : alsoRunIn.filter((id) => runGroup.candidates.some((c) => c.envId === id && c.ready));
          /* Optional gaps still open: the run goes ahead without what rests on
             them, and says how much of the suite that leaves (AC-3.7). */
          const partial = runEnabled && gapTally.blocking === 0 && sitOut > 0;
          /* The Run button's menu: the other environments this agent is
             tested in ("Also run in"). Off while rows are selected — a
             selection is its own run. */
          const groupable = runGroup.candidates.length > 0;
          const showRunMenu = !selectedCount && groupable;
          const runLabel = selectedCount > 0
            ? `Run simulation (${selectedCount})`
            : groupWith.length
              ? `Run in ${groupWith.length + 1} environments`
              : partial
                ? (wide ? `Run anyway · ${sitOut} scenario${sitOut === 1 ? " sits" : "s sit"} out` : "Run anyway")
                : "Run simulation";
          const gapTip = !runEnabled
            ? runBlockedReason
            : partial && !selectedCount && !groupWith.length
              ? `${sitOut} of the ${runnable} scenarios that can run sit out — they wait on ${sitOutGaps === 1 ? "1 open question" : `${sitOutGaps} open questions`} in Needs input. Answer or accept the defaults to run them all.`
              : "";
          /* The estimate rides on the tooltip; a refusal replaces it. */
          const estimateLine = `≈ ${meterLine(runEstimate)}${groupWith.length ? " here, plus each other environment" : ""}`;
          const runTip = !runEnabled
            ? gapTip
            : usageRefusal
              ? `${usageRefusal.title} — ${usageRefusal.line}`
              : (
                <Stack spacing={0.25} sx={{ py: 0.25 }}>
                  <Typography sx={{ typography: "s3", fontWeight: 700, color: "inherit" }}>{estimateLine}</Typography>
                  {(() => { const sz = runSizeOf({ scenarios: meteredScenarios, repeats: headerTrials, surface: env.surface, parallel: parallelOf(envState) }); return <Typography sx={{ typography: "s3", color: "inherit" }}>{sz.over ? `${sz.reason}.` : `${sz.conversations} of ${sz.cap} conversations a run allows · up to ${sz.parallel} at once${sz.voice ? ` · voice runs in real time: ${sz.voice.line}` : ""}`}</Typography>; })()}
                  {usageNotice && <Typography sx={{ typography: "s3", color: "inherit", opacity: 0.8 }}>{usageNotice.line}</Typography>}
                  {gapTip && <Typography sx={{ typography: "s3", color: "inherit" }}>{gapTip}</Typography>}
                  {isComingSoon(env) && <Typography sx={{ typography: "s3", color: "inherit", opacity: 0.8 }}>Coming soon — runs work here, but improvements don&apos;t run on browser or computer-use agents yet.</Typography>}
                </Stack>
              );
          /* An agent change is one card here (and its scenario step one
             more) — counted as such, never as the changes inside it. */
          const agentCard = versionCardOf(envState);
          const agentStep = scenarioStepOf(envState);
          const openSuggestions = (agentCard?.status === "open" ? 1 : 0) + (agentStep?.status === "open" ? 1 : 0);
          const openGaps = gapTally.blocking + gapTally.optional + openSuggestions;
          /* With rows checked on Scenarios, the selection bar carries Repeats
             and Run simulation (N) — the header steps aside so there's one. */
          const runInSelectionBar = activeTab === "scenarios" && selectedCount > 0;
          return (
            <Stack direction="row" alignItems="center" spacing={1} sx={{ flexShrink: 0 }}>
              {/* Needs input — the count of what's open; red only when
                  something blocks the run. */}
              <Tooltip arrow title={openGaps ? [gapTally.line, agentCard?.status === "open" ? `Agent ${agentCard.newest} changed` : agentStep?.status === "open" ? "Scenarios to update" : null].filter(Boolean).join(" · ") : `Needs input · ${gapTally.resolved} resolved, nothing open`}>
                <Button
                  size="small"
                  variant="outlined"
                  onClick={() => setInbox({ open: true, focus: null })}
                  startIcon={<Iconify icon="solar:inbox-linear" width={15} />}
                  sx={{
                    typography: "s2", fontWeight: 600, whiteSpace: "nowrap", color: "text.primary", borderColor: "divider",
                    minWidth: 0, px: openGaps ? 1.25 : 0.875,
                    "& .MuiButton-startIcon": { mr: openGaps ? 0.75 : 0, ml: 0 },
                  }}
                >
                  {openGaps > 0 && (
                    <>
                      Needs input
                      <Box
                        component="span"
                        sx={{
                          ml: 0.75, px: 0.625, minWidth: 18, height: 18, borderRadius: 0.75,
                          display: "inline-flex", alignItems: "center", justifyContent: "center",
                          typography: "s3", fontWeight: 700, fontVariantNumeric: "tabular-nums",
                          color: gapTally.blocking ? "#DC2626" : "text.secondary",
                          bgcolor: (t) => (gapTally.blocking ? alpha("#DC2626", t.palette.mode === "dark" ? 0.18 : 0.1) : t.palette.action.hover),
                        }}
                      >
                        {openGaps}
                      </Box>
                    </>
                  )}
                </Button>
              </Tooltip>
              {!runInSelectionBar && (
              <>
              <TrialsPicker
                trials={headerTrials}
                onChange={setHeaderTrials}
                scenarioCount={meteredScenarios}
                unit={env.surface === "voice" ? "call" : "conversation"}
              />
              {/* Refused at the pre-check: the standard limit message, at the button. */}
              {runEnabled && usageRefusal && (
                <Typography sx={{ typography: "s3", color: "#DC2626", whiteSpace: "nowrap", fontVariantNumeric: "tabular-nums" }}>
                  {`${usageRefusal.short} · `}
                  <Box
                    component="a"
                    href={paths.dashboard.settings.billing}
                    sx={{ color: "text.primary", fontWeight: 700, textDecoration: "none", "&:hover": { textDecoration: "underline" } }}
                  >
                    Upgrade
                  </Box>
                </Typography>
              )}
              {/* One button: Run, with its menu as the end of the same shape. */}
              <Box sx={{ display: "inline-flex", alignItems: "stretch", flexShrink: 0 }}>
              <Tooltip title={runTip} arrow>
                <span style={{ display: "inline-flex" }}>
                  <Button
                    variant="contained"
                    color="primary"
                    size="small"
                    disabled={!runEnabled || !!usageRefusal}
                    onClick={() => {
                      const sel = scenarioSelection?.ids || [];
                      /* Run anyway: the scenarios resting on an open or skipped
                         optional gap sit out (gapCover.attribution says which gap). */
                      const covered = partial && !sel.length && !groupWith.length
                        ? (plan?.run || []).map((sc) => sc.id).filter((id) => !gapCover.attribution[id])
                        : null;
                      startRun(sel.length > 0 ? sel : covered?.length ? covered : undefined, headerTrials, { also: groupWith });
                    }}
                    startIcon={<Iconify icon="solar:play-bold" width={15} />}
                    sx={{
                      typography: "s2", fontWeight: 700, whiteSpace: "nowrap",
                      ...(showRunMenu && { borderTopRightRadius: 0, borderBottomRightRadius: 0 }),
                    }}
                  >
                    {runLabel}
                  </Button>
                </span>
              </Tooltip>
              {showRunMenu && (
                <>
                  <Tooltip arrow title={groupWith.length ? `Running in ${groupWith.length + 1} environments — change` : "More ways to run"}>
                    <span style={{ display: "inline-flex" }}>
                      <Button
                        variant="contained"
                        color="primary"
                        size="small"
                        aria-label="More ways to run"
                        disabled={!runEnabled || !!usageRefusal}
                        onClick={(e) => setRunMenu(e.currentTarget)}
                        sx={{
                          minWidth: 0, px: 0.875, borderTopLeftRadius: 0, borderBottomLeftRadius: 0, boxShadow: "none",
                          /* A hairline inside the button, not a gap between two. */
                          borderLeft: (t) => `1px solid ${alpha(t.palette.primary.contrastText, 0.16)}`,
                        }}
                      >
                        <Iconify icon="solar:alt-arrow-down-linear" width={14} />
                      </Button>
                    </span>
                  </Tooltip>
                </>
              )}
              </Box>
              {showRunMenu && (
                <>
                  <Menu
                    anchorEl={runMenu}
                    open={!!runMenu}
                    onClose={() => setRunMenu(null)}
                    anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
                    transformOrigin={{ vertical: "top", horizontal: "right" }}
                    slotProps={{ paper: { sx: { width: 360, mt: 0.5 } } }}
                  >
                    {/* Also run in — picked environments join the next Run as a group. */}
                    {groupable && (
                      /* The menu items' own inset, so the two icons line up. */
                      <Box sx={{ px: 1, py: 1.25 }} onKeyDown={(e) => e.stopPropagation()}>
                        <Stack direction="row" alignItems="flex-start" spacing={1.25}>
                          <Iconify icon="solar:layers-linear" width={16} sx={{ mt: "2px", color: "text.secondary", flexShrink: 0 }} />
                          <Box sx={{ minWidth: 0, flex: 1 }}>
                            <Typography sx={{ typography: "s2", fontWeight: 600 }}>Run in more environments</Typography>
                            <Typography sx={{ typography: "s3", color: "text.subtitle", whiteSpace: "normal", mb: 1 }}>
                              {`Agent ${currentAgentVersion(envState)?.label || ""} in each — one run per environment, started together and read side by side. Picked ones join the next Run.`}
                            </Typography>
                            <AlsoRunIn
                              variant="inline"
                              hideLabel
                              candidates={runGroup.candidates}
                              value={alsoRunIn}
                              onChange={setAlsoRunIn}
                            />
                          </Box>
                        </Stack>
                      </Box>
                    )}
                  </Menu>
                </>
              )}
              </>
              )}
            </Stack>
          );
        })()}

        {/* A locked catalogue template isn't the user's to delete — its way
            out is Fork. An adopted copy is theirs, so it can be deleted. */}
        {!isSeededTemplate && (
          <>
            <Tooltip arrow title="More actions">
              <IconButton
                size="small"
                aria-label="More actions"
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
              slotProps={{ paper: { sx: { minWidth: 180, mt: 0.5 } } }}
            >
              <MenuItem
                onClick={() => { setConfirmDelete(env); setActionsAnchor(null); }}
                sx={{ gap: 1, py: 1, color: "#DC2626" }}
              >
                <Iconify icon="solar:trash-bin-trash-linear" width={16} sx={{ flexShrink: 0 }} />
                <Typography sx={{ typography: "s2", fontWeight: 600, color: "inherit" }}>
                  Delete environment
                </Typography>
              </MenuItem>
            </Menu>
          </>
        )}
        <DeleteEnvironmentDialog
          env={confirmDelete}
          onCancel={() => setConfirmDelete(null)}
          onConfirm={deleteEnvironment}
        />
        <SideDrawer open={inbox.open} onClose={() => setInbox({ open: false, focus: null })} width={{ xs: "100%", md: 560 }}>
          {inbox.open && (
            <SetupGapsPanel
              env={worldEnv}
              envState={envState}
              /* Read-only while viewing an older version: answers and rebuilds
                 belong to the version in use. */
              patch={isSeededTemplate || viewingOlder ? null : patch}
              onGo={go}
              focusId={inbox.focus}
              onClose={() => setInbox({ open: false, focus: null })}
            />
          )}
        </SideDrawer>
      </Stack>

      {/*
        Template lock banner — a neutral bordered strip: the lock, what it
        means, and the way out. The state is in the words, not a tint.
      */}
      {isSeededTemplate && (
        <Box sx={{ px: 2, pt: 1.5, flexShrink: 0 }}>
          <Stack
            direction="row" alignItems="center" spacing={1.5}
            sx={{
              px: 2, py: 1.25, borderRadius: 1.5,
              border: "1px solid", borderColor: "divider",
              bgcolor: "background.paper",
            }}
          >
            <Iconify icon="solar:lock-keyhole-linear" width={16} sx={{ color: "text.subtitle", flexShrink: 0 }} />

            <Box sx={{ flex: 1, minWidth: 0 }}>
              <Typography sx={{ typography: "s2", fontWeight: 700, color: "text.primary", mb: 0.25 }}>
                You&apos;re viewing a template
              </Typography>
              <Typography noWrap sx={{ typography: "s3", color: "text.subtitle" }}>
                Scenarios, evaluations, contract and agent are read-only. Run as-is, or fork to make changes.
              </Typography>
            </Box>

            <Button
              variant="outlined"
              size="small"
              onClick={forkEnvironment}
              startIcon={<Iconify icon="solar:copy-linear" width={13} />}
              sx={{ typography: "s2", fontWeight: 700, color: "text.primary", borderColor: "divider", flexShrink: 0 }}
            >
              Fork to edit
            </Button>
          </Stack>
        </Box>
      )}

      {/*
        System banners: things that change what any panel below means.
        Building = the derivation is still in flight; the panels show
        loading and this banner explains why. Off-latest = the user has
        pinned an older env version to work off; the amber pin in the
        header is the control, this is the reminder so an hour of edits
        doesn't run against the wrong world.
      */}
      <SystemBanners env={env} envState={envState} patch={patch} />
      {/* The run pre-check: a refusal holds Run until the estimate fits;
          a budget warning lets it go and says so quietly. */}
      <RunUsageBanner refusal={usageRefusal} notice={usageNotice} />
      {/* Built from an Error Feed issue's Fix: the way back to it. */}
      <IssueReturnBanner envId={env.id} />

      {/* ── body: rail + panel ── */}
      {/* A run is a pairing — this environment version × that agent version. */}
      <VersionBar env={env} envState={envState} patch={patch} locked={isSeededTemplate} />

      {/* ── body: builder console (left) + tabbed panels (right) — one
            surface split by a single rule, the same as the build screen ── */}
      <Box
        sx={{
          flex: 1, minHeight: 0, display: "grid",
          gridTemplateColumns: { xs: "1fr", lg: builderCollapsed ? "48px 1fr" : "minmax(340px, 400px) 1fr" },
        }}
      >
        {builderCollapsed && (
          <BuilderRail running={chatRunning} unseen={unseenTurns} onExpand={() => setCollapsed(false)} />
        )}
        {/* The chat acts on the Scenarios selection ("Archive these"), so
            clicking or typing in it keeps the selection (ScenariosStep's
            click-away skips data-scenarios-safe). */}
        <Box
          data-scenarios-safe="builder-chat"
          sx={{
            height: "100%", minHeight: 0, display: "flex", flexDirection: "column", overflow: "hidden",
            /* Widths and colour set separately: a responsive "1px solid"
               shorthand resets the colour to the text colour (bright white). */
            borderStyle: "solid", borderWidth: 0, borderColor: "divider",
            borderRightWidth: { xs: 0, lg: 1 }, borderBottomWidth: { xs: 1, lg: 0 },
            ...(builderCollapsed && { display: "none" }),
          }}
        >
          <AssistantConsole
            onCollapse={() => setCollapsed(true)}
            turns={turns}
            running={chatRunning}
            chips={chips}
            onSend={sendChat}
            onChip={sendChat}
            frozen={!envLive}
            frozenReason="Environment is still being built"
            preComposer={
              scenarioSelection.ids.length > 0 && (
                <SelectionContextChip
                  count={scenarioSelection.ids.length}
                  rows={scenarioSelection.rows}
                  onClear={clearScenarioSelection}
                />
              )
            }
          />
        </Box>

        <Box
          sx={{
            height: "100%", minHeight: 0, display: "flex", flexDirection: "column",
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
              {/* The Tooltip forwards the props Tabs clones onto its child
                  (value, selected, onChange…) to the Tab inside it. */}
              {visibleTabs.map((t) => (
                <Tooltip
                  key={t.id}
                  value={t.id}
                  arrow
                  placement="bottom"
                  enterDelay={400}
                  enterNextDelay={200}
                  title={tabHintOf(t.id, worldEnv) || gapsByStep[t.id]?.length ? <TabHint hint={tabHintOf(t.id, worldEnv)} gaps={gapsByStep[t.id]} /> : ""}
                  slotProps={{ tooltip: { sx: { maxWidth: 320 } } }}
                >
                  <Tab sx={{ minHeight: 42 }} label={<TabLabel tab={t} counts={counts} gaps={gapsByStep[t.id]} />} />
                </Tooltip>
              ))}
            </CustomTabs>
          </Box>

          {/*
            Fall back to Overview for an unrecognised step rather than rendering
            an empty pane — a bad link should land somewhere, not nowhere.
          */}
          <Box sx={{ flex: 1, minWidth: 0, minHeight: 0, overflow: "auto" }}>
            {panel === "scenarios" ? (
              <ScenariosStep
                env={worldEnv} envState={envState} patch={patch} onGo={go} onBuilderPrompt={sendChat} onStartRun={startRun}
                groupCandidates={runGroup.candidates} locked={isSeededTemplate} onFork={forkEnvironment}
                selectionRun={{
                  repeats: headerTrials,
                  onRepeats: setHeaderTrials,
                  unit: env.surface === "voice" ? "call" : "conversation",
                  disabledReason: !runEnabled ? (runBlockedReason || "Not ready to run") : usageRefusal ? `${usageRefusal.title} — ${usageRefusal.line}` : null,
                }}
              />
            ) : panel === "evals" ? (
              <EvalsStep env={worldEnv} envState={envState} patch={patch} onGo={go} locked={isSeededTemplate} onFork={forkEnvironment} repeats={Number(headerTrials) || 3} />
            ) : panel === "runs" ? (
              <RunsPanel env={worldEnv} envState={envState} onGo={go} />
            ) : panel === "world" ? (
              <WorldPanel env={worldEnv} envState={envState} patch={patch} onGo={go} locked={isSeededTemplate || !!viewingOlder} />
            ) : panel === "contract" ? (
              <RlContractPanel env={worldEnv} envState={envState} patch={patch} onGo={go} locked={isSeededTemplate || !!viewingOlder} onFork={forkEnvironment} />
            ) : panel === "settings" ? (
              <SettingsPanel env={worldEnv} envState={envState} patch={patch} locked={isSeededTemplate} onFork={forkEnvironment} onDelete={isSeededTemplate ? undefined : () => setConfirmDelete(env)} />
            ) : panel === "build" ? (
              <BuildRecordPanel env={worldEnv} envState={envState} patch={patch} />
            ) : (
              <OverviewPanel env={worldEnv} envState={envState} patch={patch} onGo={go} agentConnected={!!envState.agent} locked={isSeededTemplate} onFork={forkEnvironment} />
            )}
          </Box>
        </Box>
      </Box>
      {runFitDialog}
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
      {/* What needs input is listed in the tab's hover hint. */}
      {gaps?.length > 0 && (
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
      )}
    </Stack>
  );
}
TabLabel.propTypes = { tab: PropTypes.object, counts: PropTypes.object, gaps: PropTypes.array };

/* A tab's hover hint — what the page is for, and what still needs input
   on it before a run. Empty (no tooltip) when there's neither. */
function TabHint({ hint, gaps }) {
  if (!hint && !gaps?.length) return null;
  return (
    <Box>
      {hint && <Typography sx={{ typography: "s3", color: "inherit" }}>{hint}</Typography>}
      {gaps?.length > 0 && (
        <Box sx={{ mt: hint ? 0.75 : 0 }}>
          <Typography sx={{ typography: "s3", fontWeight: 700, mb: 0.375 }}>
            Answer these before you can run:
          </Typography>
          {gaps.map((g) => (
            <Typography key={g.id} sx={{ typography: "s3", opacity: 0.9 }}>· {g.title}</Typography>
          ))}
        </Box>
      )}
    </Box>
  );
}
TabHint.propTypes = { hint: PropTypes.string, gaps: PropTypes.array };

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
/*
  The reply for a message no chat action handled. The chat never claims a
  change it didn't make: it says plainly that this can't be done from chat
  yet, and where it can.
*/
function workspaceReply(userText) {
  const t = userText.toLowerCase();
  if (/\b(drop|remove|cut|archive|delete)\b/.test(t) && /scenario/.test(t)) {
    return "I can't remove scenarios from chat by description yet. Select them on the Scenarios tab, then ask me to archive them — or use Archive there. Archived scenarios stay listed and the set moves to its next version.";
  }
  if (/\b(rule|refund|escalat|policy)\b/.test(t)) {
    return "To change a rule, say which one and how — “Change R2 to …”, “Add a rule: …” or “Remove rule R3”. I'll show what it costs and ask before saving it as the next environment version. You can also edit rules on the Contract tab.";
  }
  if (/\b(eval|evals|evaluation|grader|grade|judge)\b/.test(t)) {
    return "I can add a catalogue evaluation by name — e.g. “Add a compliance judge”. For anything else (a custom evaluation, thresholds, mappings), use the Evaluations tab.";
  }
  if (/\b(run|fail|failed|failing|last)\b/.test(t)) {
    return "Ask “Why did <scenario> fail?” or “Show the trace for <scenario>” and I'll answer from the latest run — or open the Runs tab for the full list.";
  }
  return "I can't make that change from chat yet. From here I can add scenarios, correct the contract (rules, use cases, a tool argument's allowed values), add a catalogue evaluation, set a tool's tier or faults, point a secret at a new reference, and answer what's missing or why a scenario failed. Everything else is on the tabs.";
}

/**
 * Reply shape when the user typed a message with scenarios selected
 * on the Scenarios tab. Names the count and (up to three) row names
 * so the acknowledgment is grounded in what got edited.
 */
/**
 * Small chip that shows above the chat composer when the user has
 * scenarios selected on the Scenarios tab. Turns the input's
 * placeholder-shaped hint into an explicit "you are editing N rows"
 * status so the next send doesn't feel like it came from nowhere.
 */
function SelectionContextChip({ count, rows, onClear }) {
  const preview = (rows || []).slice(0, 2).map((r) => r.title || r.name || "scenario").join(", ");
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
        Editing <b>{count}</b> scenario{count === 1 ? "" : "s"} — {preview}{rest}
      </Typography>
      <Tooltip arrow title="Clear selection">
        <IconButton size="small" onClick={onClear} sx={{ p: 0.25 }}>
          <Iconify icon="solar:close-circle-linear" width={14} sx={{ color: "text.subtitle" }} />
        </IconButton>
      </Tooltip>
    </Stack>
  );
}
SelectionContextChip.propTypes = {
  count: PropTypes.number, rows: PropTypes.array, onClear: PropTypes.func,
};

/*
  Guided mode's one question: which kind of scenarios to write. The option
  picked is what the scenarios are written for.
*/
function guidedScenarioQuestion() {
  return {
    step: 1, total: 1,
    prompt: "What kind of scenarios should I write?",
    multiSelect: false,
    options: [
      { label: "Happy path", description: "Standard requests with no edge cases" },
      { label: "Adversarial", description: "Rushed callers, off-topic tangents and skepticism" },
      { label: "Tool-fault cases", description: "A tool returns unexpected results and the agent has to cope" },
    ],
  };
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
            bgcolor: "background.neutral",
            borderBottom: "1px solid",
            borderColor: "divider",
          }}
        >
          <Iconify icon="solar:history-linear" width={14} sx={{ color: "text.secondary", flexShrink: 0 }} />
          <Typography sx={{ typography: "s2", fontWeight: 700, color: "text.primary", flexShrink: 0 }}>
            Editing off {envLabelOf(env, envState, active.label)}
          </Typography>
          <Typography sx={{ typography: "s2", color: "text.secondary", flex: 1, minWidth: 0 }}>
            Latest is <b>{envLabelOf(env, envState, newest.label)}</b>. Runs from here will stamp against {envLabelOf(env, envState, active.label)}, and any edits sit on {envLabelOf(env, envState, active.label)}, not on the latest world.
          </Typography>
          <Button
            size="small"
            onClick={() => patch({ activeEnvVersion: newest.label })}
            sx={{ typography: "s2", fontWeight: 700, color: "text.primary", flexShrink: 0 }}
          >
            Switch to {envLabelOf(env, envState, newest.label)}
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




