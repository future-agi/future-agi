import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";
import { useParams, useNavigate, useSearchParams } from "react-router-dom";
import { useSnackbar } from "notistack";
import { alpha } from "@mui/material/styles";
import PropTypes from "prop-types";
import {
  Box,
  Stack,
  Typography,
  Button,
  Tooltip,
  Tab,
  IconButton,
  Divider,
  Menu,
  MenuItem,
  Popover,
  useMediaQuery,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { CustomTabs } from "src/components/tabs/tabs";
import { paths } from "src/routes/paths";
import { protoRunId } from "../_mock/executionAdapter";
import { runListCount } from "../_mock/runList";
import { scenariosFromDescription } from "../_mock/scenarios";
import { scenarioPoolFor } from "../_mock/scenarioPool";
import {
  detectAddScenariosIntent,
  describedAsk,
} from "../_mock/addScenariosIntent";
import {
  stampNewBatch,
  unanswerableNote,
  rejectedNote,
} from "../_mock/addScenarios";
import { asProposal } from "../_mock/scenarioSources";
import { currentUser } from "../_mock/scenarioProvenance";
import { getEnvironment } from "../_mock/environments";
import { getSurface } from "../_mock/surfaces";
import { BOOT_STEPS } from "../_mock/runStream";
import { useSimStore, useEnvState, useConnectedAgents } from "../store";
import {
  envAgentKey,
  needsFoundCheck,
  needsToolSafety,
} from "../_mock/agentRegistry";
import AgentFoundReview from "../agents/AgentFoundReview";
import ToolSafetyDialog from "../agents/ToolSafetyDialog";
import MakeCopyDialog from "./MakeCopyDialog";
import ReviewUpdateDialog from "./ReviewUpdateDialog";
import { catalogueOfEnv, releaseUpdateOf } from "../_mock/catalogue";
import {
  applyReleasePatch,
  releaseReviewOf,
  skipReleasePatch,
} from "../_mock/releaseUpdate";
import { agentRecordOf } from "../_mock/envCopy";
import { foundValuesOf } from "../agents/foundFacts";
import NextStepBanner from "./NextStepBanner";
import DeleteEnvironmentDialog from "../environments/DeleteEnvironmentDialog";
import TrialsPicker from "./scenarios/TrialsPicker";
import {
  setupGaps,
  runReadiness,
  gapMeter,
  gapCoverage,
} from "../_mock/setupGaps";
import { subscribeGapsInbox, openGapsInbox } from "../_mock/gapsInboxBus";
import { scenarioStepOf, versionCardOf } from "../_mock/versionCard";
import SideDrawer from "../components/SideDrawer";
import { atOnceOf } from "../run/atOnce";
import RunFromCodeDrawer from "./RunFromCodeDrawer";
import { managedOf, managedRepoOf } from "../_mock/envManaged";
import SetupGapsPanel from "./SetupGapsPanel";
import { runPlan } from "../_mock/proofs";
import { runScopeOf } from "../_mock/runScope";
import { resolveEval } from "../_mock/evals";
import { runSizeOf, parallelOf } from "../_mock/runStream";
import IssueReturnBanner from "../errorFeed/IssueReturnBanner";
import {
  subscribeBuilderPrompt,
  subscribeBuilderSend,
  subscribeOpenBuilderChat,
} from "../_mock/builderPromptBus";
import { subscribeComposerScaffold } from "../_mock/composerScaffoldBus";
import {
  subscribeScenarioSelection,
  clearScenarioSelection,
  getScenarioSelection,
} from "../_mock/scenarioSelectionBus";
import { subscribeBuilderMode } from "../_mock/builderModeBus";
import {
  agentVersions,
  currentAgentVersion,
  currentEnvVersion,
  environmentVersions,
  scenariosInSet,
  pendingEnvChanges,
  saveEnvChangesPatch,
  envChangeLabel,
} from "../_mock/versions";
import { pendingCostOf } from "../_mock/envVersionKinds";
import { ConfirmDialog } from "src/components/custom-dialog";
import {
  defaultRunCap,
  estimateRunPlan,
  rangeLabel,
  versionOptionsOf,
} from "../_mock/pricing";
import RunPlanDialog from "./RunPlanDialog";
import AnalyticsPanel from "./AnalyticsPanel";
import { useRoleGates } from "../_mock/previewRole";
import {
  teammateSaveOf,
  keepTheirsPatch,
  saveBothPatch,
} from "../_mock/teammateSave";
import SaveClashDialog from "./SaveClashDialog";
import { groupsOf as orgGroupsOf, groupRunPlanOf } from "../_mock/scenarioOrg";
import AttachmentsCard from "./data/AttachmentsCard";
import DataProposalCard from "./data/DataProposalCard";
import ActorsPanel from "./ActorsPanel";
import VersionHistoryMenu from "./VersionHistory";
import { envOf } from "../_mock/runGroup";
import { RUN_PLAN_PARAMS, runPlanRequestOf } from "../_mock/runPlanLink";
import { withPinnedWorld, worldToolsFor } from "../_mock/toolFit";
import {
  proofKindOf,
  runModeSwitchOf,
  isComingSoon,
  COMING_SOON_NOTE,
  runDefaultsOf,
} from "../_mock/rsiModel";
import { finishBuildPatch } from "../_mock/quickBuild";
import { envLabelOf } from "../_mock/envVersionKinds";
import { isConnectOnly } from "../_mock/envModel";
import { currentEnvSnapshot } from "../_mock/envSnapshot";
import { EmptyState } from "../components/primitives";
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
  { id: "overview", label: "Overview", icon: "solar:widget-5-linear" },
  { id: "world", label: "World & tools", icon: "solar:globe-linear" },
  { id: "contract", label: "Contract", icon: "solar:document-text-linear" },
  { id: "actors", label: "Actors", icon: "solar:users-group-rounded-linear" },
  {
    id: "scenarios",
    label: "Scenarios",
    icon: "solar:layers-minimalistic-linear",
    badge: "scenarios",
  },
  {
    id: "evals",
    label: "Evaluations",
    icon: "solar:shield-check-linear",
    badge: "evals",
  },
  {
    id: "runs",
    label: "Runs",
    icon: "solar:play-circle-linear",
    badge: "runs",
  },
  { id: "analytics", label: "Analytics", icon: "solar:chart-2-linear" },
  { id: "history", label: "History", icon: "solar:history-linear" },
  { id: "settings", label: "Settings", icon: "solar:settings-linear" },
];

/*
  Each tab opens straight on its content — a heading that repeated the tab
  name was spent height. What that heading used to say is the tab's hover
  hint instead. Overview has none: its description is content.
*/
const tabHintOf = (id, env) =>
  ({
    world: isConnectOnly(env)
      ? "What a run of this environment stands on. The environment version is the contract alone."
      : "What your agent talks to during a run — the data, and the tool handlers that answer every call. It learns a new tool whenever a later agent version brings one.",
    contract:
      "The rules the agent holds to and what it's for, then how a conversation runs — when it ends, who else is in it, and how it's scored. Editing it makes a new environment version; the scenarios that depend on the change are marked Re-check.",
    scenarios:
      "Each scenario is one task your agent has to complete, with its own caller. Every agent version runs the same suite; comparisons run over the scenarios both runs share.",
    evals:
      "Sub-goals decide pass or fail. Mark an evaluation required to let it fail a conversation; the rest score it.",
    runs: "Every simulation of this environment and how it scored. Select two or more runs to compare them scenario by scenario.",
    actors:
      "The other people in a conversation — a manager, a colleague — each with their own goal. They aren't the agent's own workers, and they aren't the caller.",
    analytics:
      "Trends across this environment's runs — by agent version, scenario source and outcome.",
    history:
      "Every environment version and agent version, newest first. Roll back makes the next version from an older one; nothing is deleted.",
    settings: "How this environment is built and how runs behave inside it.",
  })[id] || "";

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
  build: ["Why was this tool included?", "What did we infer vs read directly?"],
  runs: ["Summarise the last run", "Which scenarios fail most often?"],
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

  const { envState, patch: storePatch, canRun } = useEnvState(envId);
  /* Roles: a viewer looks at everything but changes nothing — an edit says
     why it didn't happen instead of landing. */
  const gates = useRoleGates();
  const { enqueueSnackbar: notifyRole } = useSnackbar();
  const patch = useCallback(
    (p) =>
      gates.canEdit
        ? storePatch(p)
        : notifyRole(gates.editReason, {
            variant: "info",
            preventDuplicate: true,
          }),
    [gates.canEdit, gates.editReason, storePatch, notifyRole],
  );
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
  const { check: checkRunFit, dialog: runFitDialog } = useRunFitCheck(
    env,
    envState,
    patch,
    { locked: isSeededTemplate },
  );

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
  const [builderCollapsed, setBuilderCollapsed] =
    useState(readBuilderCollapsed);
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
    if (
      builderCollapsed &&
      added > 0 &&
      turns[turns.length - 1]?.role === "assistant"
    )
      setUnseenTurns((n) => n + 1);
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
    () =>
      env
        ? runPlan(env, envState, {
            only: scenarioSelection?.ids?.length ? scenarioSelection.ids : null,
          })
        : null,
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [
      env,
      envState.scenarios,
      envState.envVersions,
      envState.activeEnvVersion,
      scenarioSelection,
    ],
  );
  /* Header trials picker — how many times each scenario runs when the
     user clicks the top-of-page "Run simulation" button (no selection).
     Same PRD AC-10.7 dial as the SelectionBar's picker, but scoped to
     "run all". Run simulation starts straight away with this value. */
  /* Three samples by default — the fewest where a scenario can be flaky
     ("passed twice, failed once") rather than a coin flip read as a verdict. */
  /* …or the environment's own default, set in Settings → Simulation defaults. */
  /* The code drawer, open on a tab: "cli" from Run from code, "train" or
     "package" from the ⋯ menu. */
  const [codeOpen, setCodeOpen] = useState(null);
  /* The run plan a Run button opened: { scenarioIds, trials, also }. */
  const [runPlanReq, setRunPlanReq] = useState(null);
  /* A run asked for while edits are unsaved: which version it runs on. */
  const [draftAsk, setDraftAsk] = useState(null);
  /* A save asked for after a teammate saved first: { after } to run next. */
  const [clashReq, setClashReq] = useState(null);
  /* ?price=missing shows the held state a missing rate puts a run in. */
  const [searchParams, setSearchParams] = useSearchParams();
  const missingRate = searchParams.get("price") === "missing";
  /* A Run or Re-run elsewhere links here with the plan prefilled. */
  useEffect(() => {
    const req = runPlanRequestOf(searchParams);
    if (!req) return;
    setRunPlanReq(req);
    const next = new URLSearchParams(searchParams);
    RUN_PLAN_PARAMS.forEach((k) => next.delete(k));
    setSearchParams(next, { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams]);
  const [headerTrials, setHeaderTrials] = useState(
    () => runDefaultsOf(envState).repeats || 3,
  );
  /* Other environments the next run also starts in — one run each, same
     agent version, read together as a group. Empty is a plain run. */
  const [alsoRunIn, setAlsoRunIn] = useState([]);
  const runGroup = useRunGroup(envId);
  /* Header overflow menu (Delete environment) and its confirm step. */
  const [actionsAnchor, setActionsAnchor] = useState(null);
  const [runMenu, setRunMenu] = useState(null);
  /* The run group's picker, opened from the Run menu's third option. */
  const [groupAnchor, setGroupAnchor] = useState(null);
  /* Run a group, from the Run menu. */
  const [groupRunAnchor, setGroupRunAnchor] = useState(null);
  /* The picks as they were when the picker opened — Cancel puts them back. */
  const [groupBefore, setGroupBefore] = useState([]);
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
  /* "Check what we found" for a newly connected code agent, opened here. */
  const [foundOpen, setFoundOpen] = useState(false);
  /* Make a copy: null, or { version } when a History entry preselects one. */
  const [copyOpen, setCopyOpen] = useState(null);
  /* A newer release of the world this environment came from. */
  const [reviewOpen, setReviewOpen] = useState(false);
  /* ?copy=1 (the environment list's row menu) opens Make a copy. */
  useEffect(() => {
    const p = new URLSearchParams(window.location.search);
    if (p.get("copy") !== "1") return;
    setCopyOpen({});
    p.delete("copy");
    window.history.replaceState(
      window.history.state,
      "",
      `${window.location.pathname}${p.toString() ? `?${p}` : ""}`,
    );
  }, []);
  /* Before a live run: only which tools must never run for real. */
  const [safetyOpen, setSafetyOpen] = useState(false);
  const { agents: connectedAgents, setAgentValues } = useConnectedAgents();
  const { enqueueSnackbar } = useSnackbar();
  const { viewAs: viewingOlder } = useEnvViewAs(env, envState);
  useEffect(
    () => subscribeGapsInbox((focus) => setInbox({ open: true, focus })),
    [],
  );

  // Adopt on direct navigation so a deep link works from a cold start.
  useEffect(() => {
    if (env && !state.myEnvironments.some((e) => e.id === env.id)) {
      dispatch({
        type: "adoptEnvironment",
        env,
        now: new Date().toISOString(),
      });
    }
  }, [env, state.myEnvironments, dispatch]);

  /* The conversation belongs to one environment: opening another (the same
     screen, a new id) starts its own, instead of greeting with the last one's name. */
  const turnsEnvRef = useRef(null);
  useEffect(() => {
    if (!env) return;
    const switched =
      turnsEnvRef.current !== null && turnsEnvRef.current !== env.id;
    turnsEnvRef.current = env.id;
    setTurns((prev) =>
      prev.length && !switched
        ? prev
        : [
            {
              id: "ws-greet",
              role: "assistant",
              steps: [
                {
                  kind: "note",
                  text:
                    env.buildStatus === "building"
                      ? `${env.name} is still building. Once it's ready, ask me to add scenarios, change a rule, or add an evaluation — or edit directly on the right.`
                      : `${env.name} is ready. Ask me to add scenarios, change a rule, or add an evaluation — or edit directly on the right.`,
                },
              ],
            },
          ],
    );
  }, [env?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  /* Module-level pub/sub for builder-prompt suggestions. The Edit-scenario
     drawer's suggestion pills emit here; we append the message directly
     to `turns` (rather than routing through `sendChat`) so this doesn't
     depend on `sendChat` being defined below in the file. */
  /* A screen handing the builder a request ("Write scenarios for the new
     tools") — sent as if typed, with the chat unfolded to show it. The
     send itself is defined further down; the ref reaches it. */
  const sendChatRef = useRef(null);
  useEffect(
    () =>
      subscribeBuilderSend((text) => {
        setCollapsed(false);
        sendChatRef.current?.(text);
      }),
    [],
  ); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    return subscribeBuilderPrompt((text) => {
      setTurns((prev) => [
        ...prev,
        { id: `u-${Date.now()}`, role: "user", text },
      ]);
      setChatRunning(true);
      setTimeout(() => {
        setChatRunning(false);
        setTurns((prev) => [
          ...prev,
          {
            id: `a-${Date.now()}`,
            role: "assistant",
            steps: [{ kind: "note", text: workspaceReply(text) }],
          },
        ]);
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
    if ((envState?.evals?.length || 0) > 0) {
      patch({ evalsSeeded: true });
      return;
    }
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
      return (
        <Box
          sx={{
            p: 2,
            height: "100%",
            minHeight: 420,
            display: "grid",
            placeItems: "center",
          }}
        />
      );
    }
    return (
      <Box sx={{ p: 2 }}>
        <EmptyState
          icon="solar:danger-triangle-linear"
          title="Environment not found"
          body="It may have been removed from your workspace."
          action={
            <Button
              variant="contained"
              color="primary"
              size="small"
              onClick={() => navigate(paths.dashboard.simulate.environments)}
            >
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
      <Box
        sx={{
          p: 2,
          height: "100%",
          minHeight: 420,
          display: "grid",
          placeItems: "center",
        }}
      >
        <Box
          sx={{
            width: "100%",
            maxWidth: 520,
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "8px",
            bgcolor: "background.paper",
          }}
        >
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

  const go = (s) =>
    navigate(paths.dashboard.simulate.environmentStep(envId, s));

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
  const launchRun = (
    scenarioIds,
    trials,
    { agent, cap, alertAt, continues, snapshot, envVersion, candidate } = {},
  ) => {
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
    /* The run plan's spending limit and warning line. */
    if (cap) params.set("cap", String(cap));
    if (alertAt) params.set("warn", String(Math.round(alertAt * 100)));
    if (continues) params.set("continues", continues);
    if (snapshot) params.set("snapshot", snapshot);
    if (envVersion) params.set("env", envVersion);
    /* A code agent's commit or uploaded archive, picked in the run plan. */
    if (candidate) params.set("candidate", candidate);
    const qs = params.toString();
    if (qs) url += `?${qs}`;
    navigate(url);
  };
  /* `also` — other environments to start alongside, as a run group. A
     group runs each environment's own full scenario set, so it never
     carries a selection. */
  /* Every Run opens the run plan first — scope, estimate and limit are
     confirmed before anything starts. */
  /* Saving: straight through, unless a teammate saved this environment
     while these edits were being made — then both changes are shown first. */
  const clash = teammateSaveOf(env, envState);
  const requestSave = (after) => {
    if (clash) return setClashReq({ after: after || null });
    patch(saveEnvChangesPatch(env, envState));
    return after?.();
  };
  /* Runs always use a saved version; with edits unsaved, the person picks —
     the saved one, or save the edits first — never half-finished edits. */
  const runNow = (scenarioIds, trials, { also, saved } = {}) => {
    if (!saved && !isSeededTemplate && pendingEnvChanges(envState).length)
      return setDraftAsk({ scenarioIds, trials, also });
    return checkRunFit(() => setRunPlanReq({ scenarioIds, trials, also }));
  };
  const startPlannedRun = ({ attempts, versions, candidate, cap, alertAt }) => {
    const req = runPlanReq;
    setRunPlanReq(null);
    if (!req) return;
    const current = currentAgentVersion(envState)?.label;
    if (versions.length > 1)
      return runGroup.startVersions(versions, {
        trials: attempts,
        cap,
        alertAt,
      });
    if (req.also?.length && !req.scenarioIds?.length)
      return runGroup.start(req.also, { trials: attempts, cap, alertAt });
    return launchRun(req.scenarioIds, attempts, {
      agent: versions[0] && versions[0] !== current ? versions[0] : undefined,
      cap,
      alertAt,
      continues: req.continues,
      snapshot: req.snapshot,
      envVersion: req.envVersion,
      candidate,
    });
  };
  const startRun = (scenarioIds, trials, opts = {}) =>
    runNow(scenarioIds, trials, opts);

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
  const activeTab = visibleTabs.some((t) => t.id === panel)
    ? panel
    : "overview";

  const counts = {
    /* Scenarios in the set — the same count Overview and the version bar show. */
    scenarios:
      scenariosInSet(envState, currentEnvVersion(env, envState)?.label)
        .length || null,
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
    if (!gates.canRun) return gates.runReason;
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
  const runEnabled =
    canRun &&
    gates.canRun &&
    runnable > 0 &&
    !firstBlockingGap &&
    !viewingOlder;

  /*
    The one thing standing between this environment and a run, as a banner
    with the action that resolves it here — the disabled Run button's
    tooltip alone left people not knowing where to click.
  */
  const agentKey = envAgentKey(envState.agent, envId);
  const connectedAgent =
    connectedAgents.find((a) => a.key === agentKey) || envState.agent;
  const agentName = connectedAgent?.name || "the agent";
  const nextStep = (() => {
    if (viewingOlder) return null;
    /* Sealed runs go ahead on what we read; the review is still worth doing. */
    if (runEnabled)
      return needsFoundCheck(envState.agent)
        ? {
            tone: "info",
            title: "Runs use what we read from the agent's code.",
            line: `Optional — review ${agentName}'s tools, prompts and credentials to correct anything.`,
            actionLabel: "Review what we found",
            onAction: () => setFoundOpen(true),
          }
        : null;
    if (!readiness.ready) {
      /* Live needs one answer; the rest of the review is optional. Sealed
         needs none, so it's offered as the way out. */
      if (readiness.foundCheck) {
        const toSealed = runModeSwitchOf(connectedAgent)?.to === "sealed";
        return {
          title: "Mark the tools that must never run for real to run live.",
          line: "Everything else we read is optional to check.",
          actionLabel: "Mark the tools",
          onAction: () => setSafetyOpen(true),
          ...(toSealed && {
            secondaryLabel: "Run sealed instead",
            onSecondary: () => {
              setAgentValues(agentKey, { runMode: "sealed" });
              enqueueSnackbar(
                `${agentName} runs sealed — its tool calls are answered by this environment's world.`,
                { variant: "success" },
              );
            },
          }),
        };
      }
      /* Consent is given right here (the same form as the agent's
         Connections tab, saved to the agent) — nobody leaves to agree. */
      const consentGap =
        readiness.gap === "gap_live_consent" &&
        !isSeededTemplate &&
        allGaps.find((g) => g.code === "gap_live_consent");
      if (consentGap)
        return {
          title: "Let us call your agent for tests first to run.",
          line: "A run makes real calls to it, with its real tools.",
          actionLabel: "Give consent",
          onAction: () => setInbox({ open: true, focus: consentGap.id }),
        };
      return {
        title: `${readiness.reason.replace(/ on Overview$/, "")} to run.`,
        actionLabel: readiness.href ? "Open agent" : "Open Overview",
        onAction: () =>
          readiness.href ? navigate(readiness.href) : go("overview"),
      };
    }
    if (firstBlockingGap)
      return {
        title: `Answer ${gapTally.blocking === 1 ? "1 question" : `${gapTally.blocking} questions`} to run.`,
        line: firstBlockingGap.title,
        actionLabel: "Open Needs input",
        onAction: () => setInbox({ open: true, focus: firstBlockingGap.id }),
      };
    if (runnable === 0)
      return {
        title: "No scenario can run yet.",
        line: runBlockedReason.replace(/ — see the Scenarios tab$/, ""),
        actionLabel: "Open Scenarios",
        onAction: () => go("scenarios"),
      };
    return null;
  })();

  /*
    What the next Run meters, and the pre-check at that paid boundary
    (AC-16.7, AC-16.8). Counted over exactly what Run will take — the
    selection, or the plan minus what sits out on an open optional gap —
    times the repeats picked. A refusal holds Run with the standard limit
    message; a budget warning lets it go and says so quietly.
  */
  const headerSelCount = scenarioSelection?.ids?.length || 0;
  const headerGrouped =
    headerSelCount === 0 &&
    alsoRunIn.some((id) =>
      runGroup.candidates.some((c) => c.envId === id && c.ready),
    );
  /* One count of what Run takes, everywhere it's said (button, Repeats,
     tooltip, the Evaluations cost): runScopeOf — the plan minus what sits
     out on an open optional question. A selection or a group is its own run. */
  const scope = runScopeOf(worldEnv, envState, { plan, gaps: allGaps });
  const meteredScenarios =
    runEnabled && gapTally.blocking === 0 && !headerSelCount && !headerGrouped
      ? scope.count
      : runnable;
  /* A run group's other environments, priced in the plan with this one —
     each runs its own runnable scenarios. */
  const alsoScopes = (
    runPlanReq?.scenarioIds?.length ? [] : runPlanReq?.also || []
  )
    .map((id) => {
      const e = envOf(state, id);
      const es = state.byEnv?.[id];
      if (!e || !es) return null;
      return {
        env: e,
        envState: es,
        scenarios: runScopeOf(e, es).count,
        evals: (es.evals || []).map(resolveEval).filter(Boolean),
      };
    })
    .filter(Boolean);
  /* Run from code previews the same estimate the run plan opens with. */
  const codeEstimate = codeOpen
    ? estimateRunPlan({
        env,
        envState,
        scenarios: meteredScenarios,
        attempts: Number(headerTrials) || 3,
        versions: [currentAgentVersion(envState)?.label].filter(Boolean),
        evals: (envState.evals || []).map(resolveEval).filter(Boolean),
        runLocation: envState?.agent?.values?.runLocation || null,
      })
    : null;
  const sitOut = Math.max(0, runnable - meteredScenarios);
  /* How many open questions those wait on — counted, never named by gap id. */
  const sitOutGaps = scope.sitOutGaps;

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
  /* Make a copy (catalogue.md): `opts` from the dialog — a name, the
     source version, duplicate or fork, the scenarios it keeps, and for a
     fork the agent it tests. Called bare (a save clash's "save a copy"),
     it's the whole environment as a fork, as before. */
  const forkEnvironment = (opts = {}) => {
    const suffix = Math.random().toString(36).slice(2, 8);
    const forkedId = `${env.id}-fork-${suffix}`;
    const now = new Date().toISOString();
    const keep = opts.scope?.ids ? new Set(opts.scope.ids) : null;
    const forked = {
      ...env,
      id: forkedId,
      name: opts.name || `${env.name} · fork`,
      custom: true,
      adoptedAt: undefined,
      buildProgress: undefined,
      forkedFrom: env.id,
      /* What it was copied from, and how — the source stays as it was. */
      copyOf: {
        envId: env.id,
        envName: env.name,
        version: opts.version || envState.activeEnvVersion || null,
        kind: opts.kind || "fork",
        scope: opts.scope
          ? {
              id: opts.scope.id,
              label: opts.scope.label,
              count: opts.scope.ids.length,
            }
          : null,
      },
    };
    dispatch({ type: "adoptEnvironment", env: forked, now });
    /* History stays behind; everything else comes along. */
    const {
      runs,
      releases,
      optimizations,
      previousAgents,
      unsavedEnvChanges,
      envVersions,
      activeEnvVersion,
      ...carried
    } = envState;
    const twin = envState.twinBacking;
    const agentLabel = currentAgentVersion(envState)?.label;
    dispatch({
      type: "patchEnvState",
      envId: forkedId,
      patch: {
        ...carried,
        twinBacking: twin
          ? {
              ...twin,
              endpoints: Object.fromEntries(
                (twin.services || []).map((sId) => [
                  sId,
                  `https://${sId}.sandbox.futureagi.com/e/${forkedId.slice(-6)}`,
                ]),
              ),
              activity: Object.fromEntries(
                (twin.services || []).map((sId) => [
                  sId,
                  { requests: 0, failures: 0 },
                ]),
              ),
              provisionedAt: now,
            }
          : null,
        agentVersions: envState.agentVersions
          ? envState.agentVersions.map((v) => ({ ...v }))
          : undefined,
        /* The fork starts from the world as it stands — including tools a
           rebuild taught it — not from the first build's. */
        envVersions: [
          {
            id: `${forkedId}-v1`,
            label: "v1",
            createdAt: now,
            note: `${opts.kind === "duplicate" ? "Duplicated" : "Forked"} from ${env.name} at environment ${opts.version || currentEnvSnapshot(env, envState)?.label || currentEnvVersion(env, envState).label}${keep ? ` — ${keep.size} of ${(envState.scenarios || []).length} scenarios` : ""}.`,
            scenarios: (envState.scenarios || []).length,
            changed: [],
            tools: worldToolsFor(env, envState),
            ...(agentLabel && { builtFor: agentLabel }),
          },
        ],
        activeEnvVersion: "v1",
        activeAgentVersion: agentLabel || null,
        /* A fork tests what its source tested — pinned only if that was. */
        agentVersionPinned: !!envState.agentVersionPinned,
        /* Scenarios a rebuild added are part of this world now; a scoped
           copy keeps only the ones picked. */
        scenarios: (envState.scenarios || [])
          .filter((sc) => !keep || keep.has(sc.id))
          .map((sc) =>
            sc.addedInEnv
              ? { ...sc, addedInEnv: "v1", provedAgainst: "v1" }
              : sc,
          ),
        seededFromTemplate: false,
        /* A fork to another agent starts with none, so taking the new one
           doesn't re-register the source's agent under the fork's name. */
        ...(opts.agent && { agent: null, agentVersions: [] }),
      },
    });
    /* A fork tests another agent: it takes that agent from the start. */
    if (opts.agent)
      dispatch({
        type: "changeEnvAgent",
        envId: forkedId,
        agent: agentRecordOf(opts.agent),
        versions: opts.agent.versions,
        activeLabel: opts.agent.production || opts.agent.latest,
      });
    setCopyOpen(null);
    navigate(paths.dashboard.simulate.environmentDetail(forkedId));
  };

  /* A locked template (isSeededTemplate, above) is read-only until forked:
     the env-version dropdown, versions, agent and world edits are locked, and
     Fork sits beside Run simulation. */

  const chips = CHIPS_BY_TAB[activeTab] || CHIPS_BY_TAB.overview;
  /* A source opened from a citation: World & tools, with it open. */
  const openSource = (id) =>
    navigate(
      `${paths.dashboard.simulate.environmentStep(envId, "world")}${id ? `?source=${encodeURIComponent(id)}` : ""}`,
    );
  /*
    Files attached in builder chat (roadmap data.md): each uploads on its
    own; once they've settled, the readable ones join the environment's
    sources (not its world yet) and the builder proposes what to do with
    them — rules, seed tables, cases — each citing its passage or row.
  */
  const sendFiles = (text, files) => {
    const names = files.map((f) => f.name).join(", ");
    setTurns((prev) => [
      ...prev,
      {
        id: `u-${Date.now()}`,
        role: "user",
        text: text ? `${text} · Attached: ${names}` : `Attached: ${names}`,
      },
      {
        id: `a-files-${Date.now()}`,
        role: "assistant",
        steps: [
          {
            kind: "note",
            text: `Reading ${files.length === 1 ? "the file" : `${files.length} files`} — each uploads on its own, so one failing keeps the others and your message.`,
          },
          {
            kind: "node",
            node: (
              <AttachmentsCard
                files={files}
                onDone={(read) => {
                  const usable = read.filter((s) => s.status !== "unsupported");
                  patch({
                    sources: [
                      ...(latestEnvState.current.sources || []),
                      ...read,
                    ],
                  });
                  setTurns((prev) => [
                    ...prev,
                    {
                      id: `a-proposal-${Date.now()}`,
                      role: "assistant",
                      steps: usable.length
                        ? [
                            {
                              kind: "note",
                              text: `Here's what I'd change${text ? ` for “${text}”` : ""}. Expected answers stay with the evaluator; everything else cites where it came from.`,
                            },
                            {
                              kind: "node",
                              node: (
                                <DataProposalCard
                                  env={env}
                                  sourceIds={usable.map((s) => s.id)}
                                  onOpenSource={openSource}
                                />
                              ),
                            },
                          ]
                        : [
                            {
                              kind: "note",
                              text: "None of those can be read here, so nothing changes. Attach a PDF, text, Markdown, CSV or JSON file.",
                            },
                          ],
                    },
                  ]);
                }}
              />
            ),
          },
        ],
      },
    ]);
  };

  const sendChat = (text, files = []) => {
    const trimmed = (text || "").trim();
    if (files?.length) return sendFiles(trimmed, files);
    if (!trimmed) return;
    /*
      Snapshot the selection at the moment of send. If the user has
      rows checked on the Scenarios tab, treat the message as a bulk
      edit against those rows — the reply names them and the
      selection clears so the next message starts fresh.
    */
    const sel = scenarioSelection;
    const hasSelection = (sel?.ids?.length || 0) > 0;
    setTurns((prev) => [
      ...prev,
      { id: `u-${Date.now()}`, role: "user", text: trimmed },
    ]);
    setChatRunning(true);
    if (hasSelection) clearScenarioSelection();

    /*
      A correction to the contract after the build — a rule, a use case, or
      the values a tool argument allows. Says what it costs first (the
      environment version it makes, the scenarios it sends to Re-check),
      then asks; confirmed, it lands the same way the Contract tab's Edit
      does. Asked for Live on a tool that writes, it says no.
    */
    const contractIntent = !hasSelection
      ? parseContractIntent(trimmed, { env, envState })
      : null;
    if (contractIntent) {
      const say = (text) =>
        setTurns((cur) => [
          ...cur,
          {
            id: `a-${Date.now()}-${cur.length}`,
            role: "assistant",
            steps: [{ kind: "note", text }],
          },
        ]);
      setTimeout(() => {
        setChatRunning(false);
        if (contractIntent.kind === "liveRefused") {
          say(
            `${contractIntent.tool} writes data, so it can't run Live — Live is only for read-only tools, once you allow it for that tool. It stays simulated against the world, recorded or fixed; change it on the World tab.`,
          );
          return;
        }
        if (isSeededTemplate) {
          say(
            "This is a catalogue template, so its contract is read-only. Fork it from the header, then ask again.",
          );
          return;
        }
        const plan = contractEditPlan(
          env,
          latestEnvState.current,
          contractIntent.edit,
        );
        if (!plan.ok) {
          say(plan.error);
          return;
        }
        setTurns((prev) => [
          ...prev,
          {
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
                    {
                      label: `Save as environment ${plan.next}`,
                      description: plan.detail,
                    },
                    {
                      label: "Leave the contract as it is",
                      description: "Nothing changes and no version is made.",
                    },
                  ],
                },
                onSubmit: (answer) => {
                  if (answer.pick !== 0 || answer.other?.trim()) {
                    say("Left the contract as it is.");
                    return;
                  }
                  /* Re-planned against the newest state — something may have moved. */
                  const latest = latestEnvState.current;
                  const again = contractEditPlan(
                    env,
                    latest,
                    contractIntent.edit,
                  );
                  if (!again.ok) {
                    say(again.error);
                    return;
                  }
                  patch(again.patch(latest));
                  const n = again.affected.length;
                  say(
                    `Saved as environment ${again.next} — ${again.detail}. ${n ? `${n} scenario${n === 1 ? " is" : "s are"} marked Re-check on the Scenarios tab.` : "No scenario needed a re-check."} The Contract tab shows the change.`,
                  );
                },
                onSkip: () => say("Left the contract as it is."),
              },
            ],
          },
        ]);
      }, 700);
      return;
    }

    const say = (text) =>
      setTurns((cur) => [
        ...cur,
        {
          id: `a-${Date.now()}-${cur.length}`,
          role: "assistant",
          steps: [{ kind: "note", text }],
        },
      ]);

    /* "Add 10 more scenarios" / "add scenarios for callers disputing a double
       charge" really appends them — as proposals, reviewed on the Scenarios
       tab like every other source. */
    const addIntent = !hasSelection ? detectAddScenariosIntent(trimmed) : null;
    const proposeScenarios = (described, intent) => {
      const latest = latestEnvState.current;
      const existing = new Set((latest.scenarios || []).map((sc) => sc.id));
      const fresh = described
        ? scenariosFromDescription(
            env,
            described,
            intent.asked ? intent.count : 3,
          )
        : scenarioPoolFor(env, latest)
            .filter((sc) => !existing.has(sc.id))
            .slice(0, intent.count);
      if (fresh.length === 0) {
        say(
          "Every scenario this environment would derive is already in. Tell me what's missing — e.g. “callers disputing a double charge” — and I'll write those.",
        );
        return;
      }
      /* Stamped like every other add route — tools, proof, the environment
         version it was added under — so the tool check runs here too. */
      const batch = stampNewBatch(
        fresh.map((r) => ({ ...r, chatAsk: described || trimmed })),
        { env, envState: latest, source: "chat", actor: currentUser() },
      );
      /* Chat-written scenarios get the same review every other source does:
         they land proposed, and join the environment only once someone
         adds them on the Scenarios tab. */
      const stamped = batch.rows.map((r) => asProposal(r, "chat"));
      const warn = [unanswerableNote(batch), rejectedNote(batch)]
        .filter(Boolean)
        .join(" ");
      patch({ scenarios: [...(latest.scenarios || []), ...stamped] });
      const added = stamped.length;
      /* A described ask only asked for a number if it named one. */
      const requested = described && !intent.asked ? added : intent.count;
      const shortfall = added < requested;
      const review = `${added} proposed — review and add them on the Scenarios tab.`;
      say(
        (described
          ? `Wrote ${added} scenario${added === 1 ? "" : "s"} for “${described}” — ${stamped.map((sc) => sc.summary.toLowerCase()).join("; ")}. ${review}${shortfall ? ` That's every distinct way I could see it playing out; describe another angle for more.` : ""}`
          : shortfall
            ? `${review} (The derivation pool only had ${added} unused rows left.)`
            : review) + (warn ? ` ${warn}` : ""),
      );
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
        setTurns((prev) => [
          ...prev,
          {
            id: `a-${Date.now()}`,
            role: "assistant",
            steps: [
              { kind: "note", text: "Before I write them, one decision:" },
              {
                kind: "ask",
                question: q,
                onSubmit: (answer) => {
                  const picked = q.options[answer.pick];
                  const described =
                    answer.other?.trim() || picked?.description || null;
                  proposeScenarios(described, addIntent);
                },
                onSkip: () => proposeScenarios(null, addIntent),
              },
            ],
          },
        ]);
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
        if (isSeededTemplate) {
          say(
            "This is a catalogue template, so its scenarios are read-only. Fork it from the header, then ask again.",
          );
          return;
        }
        const out = selectionEditOf(trimmed, sel.rows, latestEnvState.current, {
          by: currentUser().name,
        });
        if (out.patch) patch(out.patch);
        say(out.reply);
        return;
      }
      say(workspaceReply(trimmed));
    }, 900);
  };
  sendChatRef.current = sendChat;

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        minHeight: 0,
      }}
    >
      {/* ── environment header: one row, nothing that wraps ── */}
      <Stack
        direction="row"
        alignItems="center"
        spacing={1.5}
        sx={{
          px: 3,
          py: 1.25,
          borderBottom: "1px solid",
          borderColor: "divider",
          flexShrink: 0,
        }}
      >
        <Tooltip title="Back" arrow>
          <IconButton
            size="small"
            aria-label="Back"
            onClick={goBack}
            sx={{ color: "text.tertiary" }}
          >
            <Iconify icon="solar:alt-arrow-left-linear" width={18} />
          </IconButton>
        </Tooltip>

        <Box minWidth={0} flex={1}>
          {/* One line at any width: the name truncates but never disappears,
              chips keep their shape, and the quieter ones step out first. */}
          <Stack
            direction="row"
            alignItems="center"
            spacing={1}
            sx={{ minWidth: 0, flexWrap: "nowrap" }}
          >
            <Tooltip
              arrow
              title={[env.name, env.tagline].filter(Boolean).join(" — ")}
              enterDelay={400}
            >
              <Typography
                noWrap
                sx={{
                  /* The page-title size every page uses (16/24, 600). */
                  fontSize: 15,
                  lineHeight: "24px",
                  fontWeight: 600,
                  minWidth: 72,
                  flexShrink: 1,
                }}
              >
                {env.name}
              </Typography>
            </Tooltip>
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
              title={
                envLive
                  ? "Environment is ready. You can edit via the builder or inline."
                  : "Environment is still being built. Editing resumes when it's ready."
              }
            >
              <Stack
                direction="row"
                alignItems="center"
                spacing={0.5}
                sx={{
                  px: 0.75,
                  height: 22,
                  borderRadius: "4px",
                  cursor: "default",
                  flexShrink: 0,
                  whiteSpace: "nowrap",
                  /* Ready is the quiet default — it steps out on narrow screens. */
                  display: envLive ? { xs: "none", lg: "flex" } : "flex",
                  color: envLive ? "status.success.fg" : "primary.main",
                  bgcolor: envLive
                    ? "status.success.bg"
                    : "status.discovery.bg",
                  border: "1px solid",
                  borderColor: envLive
                    ? "status.success.border"
                    : "status.discovery.border",
                }}
              >
                <Box
                  sx={{
                    width: 6,
                    height: 6,
                    borderRadius: "50%",
                    bgcolor: envLive ? "status.success.solid" : "primary.main",
                    animation: envLive
                      ? undefined
                      : "env-pulse 1.4s ease-in-out infinite",
                    "@keyframes env-pulse": {
                      "0%,100%": { opacity: 0.4 },
                      "50%": { opacity: 1 },
                    },
                  }}
                />
                {/* "Ready", not "Live" — Live now names how a run reaches the agent (the chip beside it). */}
                <Typography sx={{ typography: "s3", fontWeight: 600 }}>
                  {envLive ? "Ready" : "Building"}
                </Typography>
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
                const pk = sandboxed
                  ? { kind: "sealed", label: "Sealed" }
                  : proofKindOf(envState.agent);
                /* Sealed is the normal case (and the Overview's agent line says so);
                   only Live or Prompt only earns a header chip — they change what a run
                   does or proves, right where Run is pressed. */
                /* An agent that can run either way shows its mode here and
                   switches on click — Live asks for the never-for-real tools first. */
                const modeSwitch = sandboxed
                  ? null
                  : runModeSwitchOf(connectedAgent);
                const switchMode = modeSwitch
                  ? () => {
                      if (
                        modeSwitch.to === "live" &&
                        needsToolSafety(connectedAgent)
                      )
                        return setSafetyOpen(true);
                      setAgentValues(agentKey, { runMode: modeSwitch.to });
                      enqueueSnackbar(
                        modeSwitch.to === "live"
                          ? `${agentName} runs live — its real tools are called, except the ones marked never for real.`
                          : `${agentName} runs sealed — its tool calls are answered by this environment's world.`,
                        { variant: "success" },
                      );
                    }
                  : null;
                if (pk.kind !== "sealed" || modeSwitch)
                  chips.push({
                    ...(switchMode && { onClick: switchMode }),
                    key: "proof",
                    /* "Runs:" says what the chip describes, so it doesn't read as a second status beside Ready. */
                    prefix: "Runs:",
                    icon:
                      pk.kind === "sealed"
                        ? "solar:lock-keyhole-minimalistic-linear"
                        : pk.kind === "live"
                          ? "solar:plug-circle-linear"
                          : "solar:document-text-linear",
                    label: pk.label,
                    tip:
                      pk.kind === "prompt-only"
                        ? "Prompt only — checked by running the prompt without your tools."
                        : pk.kind === "live"
                          ? "Live — the agent runs in your account and its tools reach your real systems. Only the starting state is checked, and a model judges sub-goals from the conversation."
                          : sandboxed
                            ? "Sealed — the agent's calls to these apps land in sandboxed copies, reset between runs; sub-goals are checked against what landed there."
                            : modeSwitch
                              ? "Sealed — your agent runs where serve() runs, and its tool calls are routed to this environment's world; sub-goals are checked against the world's end state."
                              : env?.runsOn === "local" ||
                                  env?.builtFrom?.worldSource?.runsOn ===
                                    "local"
                                ? "Sealed — the agent's tool calls are answered by this environment's world, running on your machine through the CLI, and sub-goals are checked against the world's end state and the tool calls."
                                : "Sealed — the agent's tool calls are answered by this environment's world inside our sandbox, and sub-goals are checked against the world's end state and the tool calls.",
                    ...(modeSwitch && {
                      tipAction: `Click to ${modeSwitch.to === "live" ? "run live — its real tools are called" : "run sealed — tools answered by the world"}.`,
                    }),
                  });
              }
              /* "Managed in code" lives in the Code drawer — the header's Code button opens it. */
              if (isComingSoon(env))
                chips.push({
                  key: "soon",
                  icon: "solar:clock-circle-linear",
                  label: "Coming soon",
                  tip: COMING_SOON_NOTE,
                });
              return chips.map((c) => (
                <Tooltip
                  key={c.key}
                  arrow
                  title={c.tipAction ? `${c.tip} ${c.tipAction}` : c.tip}
                >
                  <Stack
                    direction="row"
                    alignItems="center"
                    spacing={0.5}
                    onClick={c.onClick}
                    sx={{
                      px: 0.75,
                      height: 22,
                      borderRadius: "4px",
                      cursor: c.onClick ? "pointer" : "default",
                      border: "1px solid",
                      borderColor: "divider",
                      color: "text.secondary",
                      flexShrink: 0,
                      whiteSpace: "nowrap",
                      display: { xs: "none", md: "flex" },
                    }}
                  >
                    <Iconify icon={c.icon} width={12} />
                    <Typography sx={{ typography: "s3", fontWeight: 600 }}>
                      {c.prefix && (
                        <Box
                          component="span"
                          sx={{
                            fontWeight: 500,
                            color: "text.tertiary",
                            mr: 0.5,
                          }}
                        >
                          {c.prefix}
                        </Box>
                      )}
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
              const label =
                card?.status === "open"
                  ? `Agent ${card.newest} changed`
                  : step?.status === "open"
                    ? "Update scenarios"
                    : "Changes skipped";
              return (
                <Tooltip
                  arrow
                  title={
                    card?.status === "open"
                      ? `Agent ${card.newest} changed. The environment and scenarios stay as they are until you rebuild them.`
                      : step?.status === "open"
                        ? "The environment was rebuilt; its scenarios are the next step."
                        : "The agent's changes were skipped — the environment and scenarios stayed as they are. Review them again any time."
                  }
                >
                  <Stack
                    direction="row"
                    alignItems="center"
                    spacing={0.5}
                    {...clickable(() => openGapsInbox("nudges"))}
                    sx={{
                      ...focusRing,
                      px: 0.75,
                      height: 22,
                      borderRadius: "4px",
                      cursor: "pointer",
                      border: "1px solid",
                      flexShrink: 0,
                      whiteSpace: "nowrap",
                      borderColor: open ? "text.secondary" : "divider",
                      color: open ? "text.primary" : "text.secondary",
                      "&:hover": { bgcolor: "action.hover" },
                    }}
                  >
                    <Iconify
                      icon="solar:branching-paths-up-linear"
                      width={12}
                    />
                    <Typography sx={{ typography: "s3", fontWeight: 600 }}>
                      {label}
                    </Typography>
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
                classes={{ tooltip: "ds-hovercard" }}
                arrow
                title={
                  <Box>
                    <Typography
                      sx={{
                        typography: "s3",
                        fontWeight: 600,
                        color: "text.primary",
                        mb: 0.5,
                      }}
                    >
                      Sandboxed apps
                    </Typography>
                    <Typography
                      sx={{ typography: "s3", color: "text.primary" }}
                    >
                      Sandboxed copies of{" "}
                      {envState.twinBacking.services.join(", ")} — reset between
                      runs.
                    </Typography>
                  </Box>
                }
              >
                <Stack
                  direction="row"
                  alignItems="center"
                  spacing={0.5}
                  sx={{
                    px: 0.75,
                    height: 22,
                    borderRadius: "4px",
                    color: "primary.main",
                    bgcolor: "status.discovery.bg",
                    border: (t) =>
                      `1px solid ${t.palette.status.discovery.border}`,
                    cursor: "default",
                  }}
                >
                  <Iconify
                    icon="solar:server-square-linear"
                    width={12}
                    sx={{ color: "primary.main" }}
                  />
                  <Typography
                    sx={{
                      typography: "s3",
                      fontWeight: 600,
                      letterSpacing: 0.3,
                    }}
                  >
                    {`${envState.twinBacking.services.length} sandboxed app${envState.twinBacking.services.length === 1 ? "" : "s"}`}
                  </Typography>
                </Stack>
              </Tooltip>
            )}
          </Stack>
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
                  activity: Object.fromEntries(
                    services.map((s) => [s, { requests: 0, failures: 0 }]),
                  ),
                },
              });
              setBooting(true);
            }}
            sx={{ color: "text.primary", borderColor: "divider" }}
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
          const groupWith =
            selectedCount > 0
              ? []
              : alsoRunIn.filter((id) =>
                  runGroup.candidates.some((c) => c.envId === id && c.ready),
                );
          /* Optional gaps still open: the run goes ahead without what rests on
             them, and says how much of the suite that leaves (AC-3.7). */
          const partial = runEnabled && gapTally.blocking === 0 && sitOut > 0;
          /* The Run button's menu: the other environments this agent is
             tested in ("Also run in"). Off while rows are selected — a
             selection is its own run. */
          const groupable = runGroup.candidates.length > 0;
          const showRunMenu = !selectedCount && groupable;
          /* Over 200 conversations a run takes the full "Conversations at
             once" — the same run; the tooltip says so. */
          const runSize = runSizeOf({
            scenarios: selectedCount || meteredScenarios,
            repeats: headerTrials,
            surface: env.surface,
            parallel: parallelOf(envState),
          });
          const batchAtOnce = atOnceOf(envState);
          /* Short and steady — counts and who sits out live in the tooltip,
             so the button never grows with the numbers. */
          const runLabel =
            selectedCount > 0
              ? "Run selected"
              : groupWith.length
                ? `Run in ${groupWith.length + 1}`
                : wide
                  ? "Run simulation"
                  : "Run";
          const gapTip = !runEnabled
            ? runBlockedReason
            : partial && !selectedCount && !groupWith.length
              ? `${sitOut} of the ${runnable} scenarios that can run sit out — they wait on ${sitOutGaps === 1 ? "1 open question" : `${sitOutGaps} open questions`} in Needs input. Answer or accept the defaults to run them all.`
              : "";
          /* The estimate rides on the tooltip; a refusal replaces it. */
          /* Credits, as the run plan says it — opening Run shows the breakdown. */
          const planEstimate = estimateRunPlan({
            env,
            envState,
            scenarios: selectedCount || meteredScenarios,
            attempts: Number(headerTrials) || 3,
            versions: [currentAgentVersion(envState)?.label].filter(Boolean),
            evals: (envState.evals || []).map(resolveEval).filter(Boolean),
            runLocation: envState?.agent?.values?.runLocation || null,
            missingRate,
          });
          const estimateLine = `${
            planEstimate.platform.unknown
              ? "Can't estimate — a rate is missing"
              : `≈ ${rangeLabel(planEstimate.platform.low, planEstimate.platform.high)}`
          }${groupWith.length ? " here, plus each other environment" : ""}`;
          const runTip = !runEnabled ? (
            gapTip
          ) : (
            <Stack spacing={0.25} sx={{ py: 0.25 }}>
              <Typography
                sx={{ typography: "s3", fontWeight: 600, color: "inherit" }}
              >
                {estimateLine}
              </Typography>
              {(() => {
                const sz = runSize;
                return (
                  <Typography sx={{ typography: "s3", color: "inherit" }}>
                    {sz.over
                      ? `${selectedCount || meteredScenarios} scenarios × ${sz.repeats} repeats = ${sz.conversations} conversations — over ${sz.cap}, so it runs ${batchAtOnce} at once (Settings → Conversations at once). You can leave the page while it runs.`
                      : `${sz.conversations} of ${sz.cap} conversations a run allows · up to ${sz.parallel} at once${sz.voice ? ` · voice runs in real time: ${sz.voice.line}` : ""}`}
                  </Typography>
                );
              })()}
              {gapTip && (
                <Typography sx={{ typography: "s3", color: "inherit" }}>
                  {gapTip}
                </Typography>
              )}
              {isComingSoon(env) && (
                <Typography
                  sx={{ typography: "s3", color: "inherit", opacity: 0.8 }}
                >
                  Coming soon — runs work here, but improvements don&apos;t run
                  on browser or computer-use agents yet.
                </Typography>
              )}
            </Stack>
          );
          /* An agent change is one card here (and its scenario step one
             more) — counted as such, never as the changes inside it. */
          const agentCard = versionCardOf(envState);
          const agentStep = scenarioStepOf(envState);
          const openSuggestions =
            (agentCard?.status === "open" ? 1 : 0) +
            (agentStep?.status === "open" ? 1 : 0);
          const openGaps =
            gapTally.blocking + gapTally.optional + openSuggestions;
          /* With rows checked on Scenarios, the selection bar carries Repeats
             and Run simulation (N) — the header steps aside so there's one. */
          const runInSelectionBar =
            activeTab === "scenarios" && selectedCount > 0;
          return (
            <Stack
              direction="row"
              alignItems="center"
              spacing={1}
              sx={{ flexShrink: 0 }}
            >
              {/* Needs input — the count of what's open; red only when
                  something blocks the run. */}
              <Tooltip
                arrow
                title={
                  openGaps
                    ? [
                        "Needs input",
                        gapTally.line,
                        agentCard?.status === "open"
                          ? `Agent ${agentCard.newest} changed`
                          : agentStep?.status === "open"
                            ? "Scenarios to update"
                            : null,
                      ]
                        .filter(Boolean)
                        .join(" · ")
                    : `Needs input · ${gapTally.resolved} resolved, nothing open`
                }
              >
                <Button
                  size="small"
                  variant="outlined"
                  onClick={() => setInbox({ open: true, focus: null })}
                  aria-label="Needs input"
                  startIcon={<Iconify icon="solar:inbox-linear" width={16} />}
                  sx={{
                    whiteSpace: "nowrap",
                    color: "text.primary",
                    borderColor: "divider",
                    minWidth: 0,
                    "& .MuiButton-startIcon": {
                      mr: openGaps ? 0.75 : 0,
                      ml: 0,
                    },
                  }}
                >
                  {openGaps > 0 && (
                    <>
                      {/* Icon and count only; the words are on hover. */}
                      <Box
                        component="span"
                        sx={{
                          ml: 0,
                          px: 0.625,
                          minWidth: 18,
                          height: 18,
                          borderRadius: "4px",
                          display: "inline-flex",
                          alignItems: "center",
                          justifyContent: "center",
                          typography: "s3",
                          fontWeight: 600,
                          fontVariantNumeric: "tabular-nums",
                          color: gapTally.blocking
                            ? "status.danger.fg"
                            : "text.secondary",
                          bgcolor: (t) =>
                            gapTally.blocking
                              ? t.palette.status.danger.bg
                              : t.palette.action.hover,
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
                  {/* Who the run leaves out is said in the Run tooltip, and
                      Needs input carries the count — the header doesn't
                      grow a caption. */}
                  {/* One button: Run, with its menu as the end of the same shape. */}
                  <Box
                    sx={{
                      display: "inline-flex",
                      alignItems: "stretch",
                      flexShrink: 0,
                    }}
                  >
                    <Tooltip title={runMenu ? "" : runTip} arrow>
                      <span style={{ display: "inline-flex" }}>
                        <Button
                          variant="contained"
                          color="primary"
                          size="small"
                          disabled={!runEnabled}
                          aria-haspopup="menu"
                          onClick={(e) => setRunMenu(e.currentTarget)}
                          startIcon={
                            <Iconify icon="solar:play-bold" width={16} />
                          }
                          endIcon={
                            <Iconify
                              icon="solar:alt-arrow-down-linear"
                              width={14}
                            />
                          }
                          sx={{ whiteSpace: "nowrap" }}
                        >
                          {runLabel}
                        </Button>
                      </span>
                    </Tooltip>
                  </Box>
                  {/* Run asks where: here, or from code (the same run as a
                      command) — and offers more environments below. */}
                  {(() => {
                    const runHere = () => {
                      setRunMenu(null);
                      setGroupAnchor(null);
                      const sel = scenarioSelection?.ids || [];
                      /* Run anyway: the scenarios resting on an open or skipped
                         optional gap sit out (gapCover.attribution says which gap). */
                      const covered =
                        partial && !sel.length && !groupWith.length
                          ? (plan?.run || [])
                              .map((sc) => sc.id)
                              .filter((id) => !gapCover.attribution[id])
                          : null;
                      startRun(
                        sel.length > 0
                          ? sel
                          : covered?.length
                            ? covered
                            : undefined,
                        headerTrials,
                        { also: groupWith },
                      );
                    };
                    const cancelGroup = () => {
                      setAlsoRunIn(groupBefore);
                      setGroupAnchor(null);
                    };
                    const RUN_WAYS = [
                      {
                        id: "here",
                        icon: "solar:play-circle-linear",
                        title: "Run here",
                        body: "Starts now — you watch it here, and it lands in Runs.",
                        onClick: runHere,
                      },
                      /* A group is a playlist of scenarios — run exactly what it selects. */
                      ...(orgGroupsOf(envState).length
                        ? [
                            {
                              id: "group",
                              icon: "solar:folder-2-linear",
                              title: "Run a group",
                              body: `${orgGroupsOf(envState)
                                .slice(0, 2)
                                .map((g) => g.name)
                                .join(
                                  ", ",
                                )}${orgGroupsOf(envState).length > 2 ? "…" : ""} — the scenarios each group selects.`,
                              onClick: () => {
                                setGroupRunAnchor(runMenu);
                                setRunMenu(null);
                              },
                            },
                          ]
                        : []),
                      {
                        id: "code",
                        icon: "solar:code-linear",
                        title: "Run from code",
                        body: "The same run from your terminal, Python or CI — copy the command with these scenarios, repeats and limit.",
                        onClick: () => {
                          setRunMenu(null);
                          setCodeOpen("cli");
                        },
                      },
                    ];
                    return (
                      <>
                        <Menu
                          anchorEl={runMenu}
                          open={!!runMenu}
                          onClose={() => setRunMenu(null)}
                          anchorOrigin={{
                            vertical: "bottom",
                            horizontal: "right",
                          }}
                          transformOrigin={{
                            vertical: "top",
                            horizontal: "right",
                          }}
                          slotProps={{
                            paper: { sx: { width: 360, mt: 0.5, p: 0.5 } },
                          }}
                        >
                          {RUN_WAYS.map((w) => (
                            <MenuItem
                              key={w.id}
                              onClick={w.onClick}
                              sx={{
                                alignItems: "flex-start",
                                gap: 1.5,
                                p: 1.25,
                                borderRadius: 1,
                                whiteSpace: "normal",
                              }}
                            >
                              <Iconify
                                icon={w.icon}
                                width={20}
                                sx={{
                                  mt: 0.25,
                                  color: "text.secondary",
                                  flexShrink: 0,
                                }}
                              />
                              <Box sx={{ minWidth: 0 }}>
                                <Typography
                                  sx={{ typography: "s1", fontWeight: 600 }}
                                >
                                  {w.title}
                                </Typography>
                                <Typography
                                  sx={{
                                    typography: "s2",
                                    color: "text.secondary",
                                  }}
                                >
                                  {w.body}
                                </Typography>
                              </Box>
                            </MenuItem>
                          ))}
                          {showRunMenu && (
                            <MenuItem
                              onClick={() => {
                                setGroupBefore(alsoRunIn);
                                setGroupAnchor(runMenu);
                                setRunMenu(null);
                              }}
                              sx={{
                                alignItems: "flex-start",
                                gap: 1.5,
                                p: 1.25,
                                borderRadius: 1,
                                whiteSpace: "normal",
                              }}
                            >
                              <Iconify
                                icon="solar:layers-linear"
                                width={20}
                                sx={{
                                  mt: 0.25,
                                  color: "text.secondary",
                                  flexShrink: 0,
                                }}
                              />
                              <Box sx={{ minWidth: 0, flex: 1 }}>
                                <Typography
                                  sx={{ typography: "s1", fontWeight: 600 }}
                                >
                                  Run in more environments
                                </Typography>
                                <Typography
                                  sx={{
                                    typography: "s2",
                                    color: "text.secondary",
                                  }}
                                >
                                  {alsoRunIn.length
                                    ? `${alsoRunIn.length} picked — a change can fix one environment and break another.`
                                    : `${runGroup.candidates.length} other environment${runGroup.candidates.length === 1 ? "" : "s"} test this agent — a change can fix one and break another.`}
                                </Typography>
                              </Box>
                              <Iconify
                                icon="solar:alt-arrow-right-linear"
                                width={16}
                                sx={{
                                  mt: 0.5,
                                  color: "text.tertiary",
                                  flexShrink: 0,
                                }}
                              />
                            </MenuItem>
                          )}
                        </Menu>
                        {/* Pick the other environments, then run them with this
                      one — each its own run, read side by side. */}
                        <Popover
                          open={!!groupAnchor}
                          anchorEl={groupAnchor}
                          onClose={cancelGroup}
                          anchorOrigin={{
                            vertical: "bottom",
                            horizontal: "right",
                          }}
                          transformOrigin={{
                            vertical: "top",
                            horizontal: "right",
                          }}
                          slotProps={{ paper: { sx: { width: 360, mt: 0.5 } } }}
                        >
                          <Box
                            sx={{
                              px: 2,
                              py: 1.5,
                              borderBottom: "1px solid",
                              borderColor: "divider",
                            }}
                          >
                            <Typography
                              sx={{ typography: "s1", fontWeight: 600 }}
                            >
                              Run in more environments
                            </Typography>
                            <Typography
                              sx={{
                                typography: "s3",
                                color: "text.tertiary",
                                mt: 0.25,
                              }}
                            >
                              The same agent version in each — one run per
                              environment, started together and read side by
                              side. Each costs what its own run would.
                            </Typography>
                          </Box>
                          <Box
                            sx={{
                              px: 2,
                              py: 1.5,
                              maxHeight: 280,
                              overflowY: "auto",
                            }}
                          >
                            <AlsoRunIn
                              variant="inline"
                              hideLabel
                              candidates={runGroup.candidates}
                              value={alsoRunIn}
                              onChange={setAlsoRunIn}
                            />
                          </Box>
                          <Stack
                            direction="row"
                            alignItems="center"
                            spacing={1}
                            sx={{
                              px: 2,
                              py: 1.25,
                              borderTop: "1px solid",
                              borderColor: "divider",
                            }}
                          >
                            <Typography
                              sx={{
                                typography: "s3",
                                color: "text.tertiary",
                                flex: 1,
                              }}
                            >
                              {alsoRunIn.length
                                ? `${alsoRunIn.length + 1} runs, side by side`
                                : "Pick at least one"}
                            </Typography>
                            <Button
                              size="small"
                              onClick={cancelGroup}
                              sx={{ color: "text.secondary" }}
                            >
                              Cancel
                            </Button>
                            <Button
                              size="small"
                              variant="contained"
                              color="primary"
                              disabled={!groupWith.length}
                              onClick={runHere}
                              startIcon={
                                <Iconify icon="solar:play-bold" width={14} />
                              }
                            >
                              {`Run in ${groupWith.length + 1}`}
                            </Button>
                          </Stack>
                        </Popover>
                      </>
                    );
                  })()}
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
                sx={{ color: "text.tertiary" }}
              >
                <Iconify icon="solar:menu-dots-bold" width={20} />
              </IconButton>
            </Tooltip>
            <Menu
              anchorEl={actionsAnchor}
              open={!!actionsAnchor}
              onClose={() => setActionsAnchor(null)}
              anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
              transformOrigin={{ vertical: "top", horizontal: "right" }}
              slotProps={{ paper: { sx: { width: 300, mt: 0.5 } } }}
            >
              <MenuItem
                onClick={() => {
                  setActionsAnchor(null);
                  setCopyOpen({});
                }}
                sx={{
                  gap: 1,
                  py: 1,
                  alignItems: "flex-start",
                  whiteSpace: "normal",
                }}
              >
                <Iconify
                  icon="solar:copy-linear"
                  width={16}
                  sx={{ flexShrink: 0, mt: "2px", color: "text.secondary" }}
                />
                <Box>
                  <Typography sx={{ typography: "s2", fontWeight: 600 }}>
                    Make a copy
                  </Typography>
                  <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
                    Same agent makes a duplicate; another agent a fork.
                  </Typography>
                </Box>
              </MenuItem>
              {!releaseUpdateOf(env, envState) &&
                releaseUpdateOf(env, envState, { includeSkipped: true }) && (
                  <MenuItem
                    onClick={() => {
                      setActionsAnchor(null);
                      setReviewOpen(true);
                    }}
                    sx={{
                      gap: 1,
                      py: 1,
                      alignItems: "flex-start",
                      whiteSpace: "normal",
                    }}
                  >
                    <Iconify
                      icon="solar:refresh-linear"
                      width={16}
                      sx={{ flexShrink: 0, mt: "2px", color: "text.secondary" }}
                    />
                    <Box>
                      <Typography sx={{ typography: "s2", fontWeight: 600 }}>
                        {`Review release ${releaseUpdateOf(env, envState, { includeSkipped: true }).to}`}
                      </Typography>
                      <Typography
                        sx={{ typography: "s3", color: "text.tertiary" }}
                      >
                        You skipped it — it&apos;s still here to take.
                      </Typography>
                    </Box>
                  </MenuItem>
                )}
              <Divider sx={{ my: 0.5 }} />
              {[
                {
                  id: "package",
                  icon: "solar:box-linear",
                  title: "Package this world",
                  body: "Publish it privately so another agent can run the same tasks.",
                },
                {
                  id: "train",
                  icon: "solar:graph-up-linear",
                  title: "Train on this world",
                  body: "One training run, then tested on cases it never saw.",
                },
              ].map((m) => (
                <MenuItem
                  key={m.id}
                  onClick={() => {
                    setActionsAnchor(null);
                    setCodeOpen(m.id);
                  }}
                  sx={{
                    gap: 1,
                    py: 1,
                    alignItems: "flex-start",
                    whiteSpace: "normal",
                  }}
                >
                  <Iconify
                    icon={m.icon}
                    width={16}
                    sx={{ flexShrink: 0, mt: "2px", color: "text.secondary" }}
                  />
                  <Box>
                    <Typography sx={{ typography: "s2", fontWeight: 600 }}>
                      {m.title}
                    </Typography>
                    <Typography
                      sx={{ typography: "s3", color: "text.tertiary" }}
                    >
                      {m.body}
                    </Typography>
                  </Box>
                </MenuItem>
              ))}
              <Divider sx={{ my: 0.5 }} />
              <MenuItem
                onClick={() => {
                  setConfirmDelete(env);
                  setActionsAnchor(null);
                }}
                // eslint-disable-next-line no-restricted-syntax -- ds: destructive menu item
                sx={{ gap: 1, py: 1, color: "status.danger.fg" }}
              >
                <Iconify
                  icon="solar:trash-bin-trash-linear"
                  width={16}
                  sx={{ flexShrink: 0 }}
                />
                <Typography
                  sx={{ typography: "s2", fontWeight: 600, color: "inherit" }}
                >
                  Delete environment
                </Typography>
              </MenuItem>
            </Menu>
          </>
        )}
        {reviewOpen &&
          (() => {
            const update = releaseUpdateOf(env, envState, {
              includeSkipped: true,
            });
            if (!update) return null;
            const review = releaseReviewOf(env, envState, update);
            return (
              <ReviewUpdateDialog
                env={env}
                update={update}
                review={review}
                onClose={() => setReviewOpen(false)}
                onSkip={() => {
                  patch(skipReleasePatch(envState, update));
                  setReviewOpen(false);
                  enqueueSnackbar(
                    `Release ${update.to} skipped — review it any time from the ⋯ menu.`,
                    { variant: "info" },
                  );
                }}
                onApply={() => {
                  const done = applyReleasePatch(env, envState, update, review);
                  dispatch({
                    type: "patchEnvironment",
                    envId: env.id,
                    patch: done.envPatch,
                  });
                  patch(done.statePatch);
                  setReviewOpen(false);
                  enqueueSnackbar(
                    `Release ${update.to} applied as environment ${done.label}${review.recheck.length ? ` — ${review.recheck.length} scenario${review.recheck.length === 1 ? "" : "s"} to re-check` : ""}. Earlier runs stay on their version.`,
                    { variant: "success" },
                  );
                }}
              />
            );
          })()}
        <MakeCopyDialog
          open={!!copyOpen}
          env={env}
          envState={envState}
          version={copyOpen?.version || null}
          selectedIds={scenarioSelection?.ids || []}
          onClose={() => setCopyOpen(null)}
          onCreate={(o) => forkEnvironment(o)}
        />
        <DeleteEnvironmentDialog
          env={confirmDelete}
          onCancel={() => setConfirmDelete(null)}
          onConfirm={deleteEnvironment}
        />
        {(() => {
          const pending = pendingEnvChanges(envState);
          const cost = pendingCostOf(env, envState);
          const savedLabel =
            currentEnvSnapshot(env, envState)?.label ||
            currentEnvVersion(env, envState)?.label;
          const choose = (saveFirst) => {
            const req = draftAsk;
            setDraftAsk(null);
            if (!req) return;
            const run = () =>
              runNow(req.scenarioIds, req.trials, {
                also: req.also,
                saved: true,
              });
            if (saveFirst) requestSave(run);
            else run();
          };
          return (
            <ConfirmDialog
              open={!!draftAsk}
              onClose={() => setDraftAsk(null)}
              title="Which version should this run use?"
              content={
                <Stack spacing={1.25}>
                  <Typography
                    sx={{ typography: "s2", color: "text.secondary" }}
                  >
                    {`Runs use a saved version. You have ${pending.length} unsaved edit${pending.length === 1 ? "" : "s"} since environment ${savedLabel}:`}
                  </Typography>
                  <Box
                    sx={{
                      border: "1px solid",
                      borderColor: "divider",
                      borderRadius: 1,
                      maxHeight: 180,
                      overflowY: "auto",
                    }}
                  >
                    {pending.map((c, i) => (
                      <Stack
                        key={`${c.kind}-${c.at || i}`}
                        direction="row"
                        spacing={1.25}
                        sx={{
                          px: 1.25,
                          py: 0.75,
                          "& + &": {
                            borderTop: "1px solid",
                            borderColor: "divider",
                          },
                        }}
                      >
                        <Typography
                          sx={{
                            typography: "s3",
                            color: "text.tertiary",
                            width: 96,
                            flexShrink: 0,
                          }}
                        >
                          {envChangeLabel(c.kind)}
                        </Typography>
                        <Typography sx={{ typography: "s3", flex: 1 }}>
                          {c.detail || envChangeLabel(c.kind)}
                        </Typography>
                      </Stack>
                    ))}
                  </Box>
                  {cost?.comparability && (
                    <Typography
                      sx={{ typography: "s3", color: "text.tertiary" }}
                    >
                      {`Saving makes environment ${cost.label}. ${cost.comparability}`}
                    </Typography>
                  )}
                </Stack>
              }
              action={
                <>
                  <Button
                    size="small"
                    variant="outlined"
                    onClick={() => choose(false)}
                    sx={{ color: "text.primary", borderColor: "divider" }}
                  >
                    {`Run environment ${savedLabel}`}
                  </Button>
                  <Button
                    size="small"
                    variant="contained"
                    color="primary"
                    onClick={() => choose(true)}
                  >
                    {`Save as ${cost?.label || "new version"} and run`}
                  </Button>
                </>
              }
            />
          );
        })()}
        <Menu
          anchorEl={groupRunAnchor}
          open={!!groupRunAnchor}
          onClose={() => setGroupRunAnchor(null)}
          anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
          transformOrigin={{ vertical: "top", horizontal: "right" }}
          slotProps={{ paper: { sx: { width: 320, mt: 0.5 } } }}
        >
          {orgGroupsOf(envState).map((g) => {
            const plan = groupRunPlanOf(g, envState.scenarios);
            return (
              <MenuItem
                key={g.id}
                disabled={!plan.run.length}
                onClick={() => {
                  setGroupRunAnchor(null);
                  runNow(plan.run, headerTrials);
                }}
                sx={{ display: "block", whiteSpace: "normal" }}
              >
                <Typography sx={{ typography: "s2", fontWeight: 600 }}>
                  {g.name}
                </Typography>
                <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
                  {`${g.ids.length} scenario${g.ids.length === 1 ? "" : "s"}${plan.left.length ? ` · ${plan.left.length} archived, blocked or proposed sit out` : ""} — the run plan shows the rest`}
                </Typography>
              </MenuItem>
            );
          })}
        </Menu>
        <SaveClashDialog
          open={!!clashReq && !!clash}
          save={clash}
          envState={envState}
          onClose={() => setClashReq(null)}
          onKeepTheirs={() => {
            const after = clashReq?.after;
            setClashReq(null);
            patch(keepTheirsPatch(env, envState, clash));
            after?.();
          }}
          onSaveBoth={(pick) => {
            const after = clashReq?.after;
            setClashReq(null);
            patch(saveBothPatch(env, envState, clash, pick));
            after?.();
          }}
          onSaveCopy={() => {
            setClashReq(null);
            /* The copy takes your edits; this environment takes theirs. */
            forkEnvironment();
            patch(keepTheirsPatch(env, envState, clash));
          }}
        />
        <RunPlanDialog
          open={!!runPlanReq}
          onClose={() => setRunPlanReq(null)}
          onStart={startPlannedRun}
          env={env}
          envState={envState}
          scenarioCount={runPlanReq?.scenarioIds?.length || meteredScenarios}
          scopeNote={
            runPlanReq?.scenarioIds?.length &&
            runPlanReq.scenarioIds.length ===
              (scenarioSelection?.ids?.length || 0)
              ? `${runPlanReq.scenarioIds.length} selected`
              : `${runPlanReq?.scenarioIds?.length || meteredScenarios} ready to run`
          }
          initialAttempts={Number(runPlanReq?.trials) || 3}
          versionOptions={versionOptionsOf(
            envState,
            agentVersions(envState),
            envId,
          )}
          evals={(envState.evals || []).map(resolveEval).filter(Boolean)}
          runLocation={envState?.agent?.values?.runLocation || null}
          parallel={parallelOf(envState)}
          missingRate={missingRate}
          alsoCount={runPlanReq?.also?.length || 0}
          initialVersion={runPlanReq?.agent}
          alsoScopes={alsoScopes}
          scopeLead={
            runPlanReq?.continues
              ? `Continues run ${(envState.runs || []).find((r) => r.id === runPlanReq.continues)?.ordinal ?? ""} — only the scenarios its limit stopped from starting. The ones that finished aren't run or charged again.`
              : runPlanReq?.snapshot
                ? "Re-runs the exact snapshot — a new run, charged as its own."
                : null
          }
        />
        <RunFromCodeDrawer
          open={!!codeOpen}
          initialTab={codeOpen || "cli"}
          budget={defaultRunCap(codeEstimate)}
          estimate={codeEstimate}
          trainEvals={(envState.evals || []).map(resolveEval).filter(Boolean)}
          onClose={() => setCodeOpen(null)}
          env={env}
          agentName={currentAgentVersion(envState)?.label}
          repeats={Number(headerTrials) || 3}
          parallel={parallelOf(envState)}
          batchAt={
            runSizeOf({
              scenarios: meteredScenarios,
              repeats: headerTrials,
              surface: env.surface,
            }).over
              ? atOnceOf(envState)
              : null
          }
          managedIn={managedOf(env, envState)}
          repo={managedRepoOf(env, envState)}
          onPulled={() => patch?.({ managedIn: "code" })}
        />
        <ToolSafetyDialog
          open={safetyOpen && !!connectedAgent}
          agentName={agentName}
          tools={connectedAgent ? foundValuesOf(connectedAgent).tools : []}
          marked={connectedAgent?.values?.neverRunForReal || null}
          onClose={() => setSafetyOpen(false)}
          onSave={(never) => {
            setAgentValues(agentKey, {
              neverRunForReal: never,
              toolSafetyAt: new Date().toISOString(),
              runMode: "live",
            });
            setSafetyOpen(false);
            enqueueSnackbar(
              `${agentName} runs live — ${never.length} ${never.length === 1 ? "tool is" : "tools are"} never called for real.`,
              { variant: "success" },
            );
          }}
        />
        <SideDrawer
          open={foundOpen}
          onClose={() => setFoundOpen(false)}
          width={{ xs: "100%", md: 640 }}
        >
          {foundOpen && connectedAgent && (
            <Stack sx={{ height: "100%" }}>
              <Stack
                direction="row"
                alignItems="center"
                gap={1}
                sx={{
                  px: 2.5,
                  py: 1.75,
                  borderBottom: "1px solid",
                  borderColor: "divider",
                }}
              >
                <Box sx={{ flex: 1, minWidth: 0 }}>
                  <Typography sx={{ typography: "m3", fontWeight: 600 }}>
                    Check what we found
                  </Typography>
                  <Typography
                    noWrap
                    sx={{ typography: "s3", color: "text.tertiary" }}
                  >
                    {`${agentName} · confirm it once and every environment testing it can run`}
                  </Typography>
                </Box>
                <IconButton
                  size="small"
                  aria-label="Close"
                  onClick={() => setFoundOpen(false)}
                >
                  <Iconify icon="solar:close-linear" width={18} />
                </IconButton>
              </Stack>
              <Box sx={{ p: 2.5, flex: 1, minHeight: 0, overflowY: "auto" }}>
                <AgentFoundReview
                  agent={connectedAgent}
                  onCancel={() => setFoundOpen(false)}
                  onSave={(valuesPatch, message) => {
                    setAgentValues(agentKey, valuesPatch);
                    setFoundOpen(false);
                    if (message)
                      enqueueSnackbar(message, { variant: "success" });
                  }}
                />
              </Box>
            </Stack>
          )}
        </SideDrawer>
        <SideDrawer
          open={inbox.open}
          onClose={() => setInbox({ open: false, focus: null })}
          width={{ xs: "100%", md: 640 }}
        >
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
            direction="row"
            alignItems="center"
            spacing={1.5}
            sx={{
              px: 2,
              py: 1.25,
              borderRadius: "8px",
              border: "1px solid",
              borderColor: "divider",
              bgcolor: "background.paper",
            }}
          >
            <Iconify
              icon="solar:lock-keyhole-linear"
              width={16}
              sx={{ color: "text.tertiary", flexShrink: 0 }}
            />

            <Box sx={{ flex: 1, minWidth: 0 }}>
              <Typography
                sx={{
                  typography: "s2",
                  fontWeight: 600,
                  color: "text.primary",
                  mb: 0.25,
                }}
              >
                You&apos;re viewing a template
              </Typography>
              <Typography
                noWrap
                sx={{ typography: "s3", color: "text.tertiary" }}
              >
                Scenarios, evaluations, contract and agent are read-only. Run
                as-is, or fork to make changes.
              </Typography>
            </Box>

            <Button
              variant="outlined"
              size="small"
              onClick={() => setCopyOpen({})}
              startIcon={<Iconify icon="solar:copy-linear" width={14} />}
              sx={{
                color: "text.primary",
                borderColor: "divider",
                flexShrink: 0,
              }}
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
      {/* Built from an Error Feed issue's Fix: the way back to it. */}
      <IssueReturnBanner envId={env.id} />

      {/* ── body: rail + panel ── */}
      {/* A run is a pairing — this environment version × that agent version. */}
      <VersionBar
        env={env}
        envState={envState}
        patch={patch}
        locked={isSeededTemplate}
        onOpenHistory={() => go("history")}
        onSave={() => requestSave()}
      />
      {nextStep && (
        <Box sx={{ px: 3, pt: 1.5 }}>
          <NextStepBanner {...nextStep} />
        </Box>
      )}
      {/* The world's release: a newer one to review, or a withdrawn/retired notice. */}
      {(() => {
        if (isSeededTemplate || viewingOlder) return null;
        const entry = catalogueOfEnv(env);
        const update = releaseUpdateOf(env, envState);
        const banner = update
          ? {
              tone: "info",
              title: `${entry.task} — release ${update.to} is out.`,
              line: `${update.notes} Nothing changes until you review it.`,
              actionLabel: "Review update",
              onAction: () => setReviewOpen(true),
            }
          : entry?.release?.status === "withdrawn"
            ? {
                title: `This world's release ${entry.release.version} was withdrawn.`,
                line: `${entry.release.note.replace(/^Withdrawn — /, "")} This environment keeps running and its runs are kept.`,
              }
            : entry?.release?.status === "retired"
              ? {
                  tone: "info",
                  title: `Release ${entry.release.version} is retired.`,
                  line: "It still runs and can be copied, but won't be updated.",
                }
              : null;
        return banner ? (
          <Box sx={{ px: 3, pt: 1.5 }}>
            <NextStepBanner {...banner} />
          </Box>
        ) : null;
      })()}

      {/* ── body: builder console (left) + tabbed panels (right) — one
            surface split by a single rule, the same as the build screen ── */}
      <Box
        sx={{
          flex: 1,
          minHeight: 0,
          display: "grid",
          gridTemplateColumns: {
            xs: "1fr",
            lg: builderCollapsed ? "48px 1fr" : "minmax(340px, 400px) 1fr",
          },
        }}
      >
        {builderCollapsed && (
          <BuilderRail
            running={chatRunning}
            unseen={unseenTurns}
            onExpand={() => setCollapsed(false)}
          />
        )}
        {/* The chat acts on the Scenarios selection ("Archive these"), so
            clicking or typing in it keeps the selection (ScenariosStep's
            click-away skips data-scenarios-safe). */}
        <Box
          data-scenarios-safe="builder-chat"
          sx={{
            height: "100%",
            minHeight: 0,
            display: "flex",
            flexDirection: "column",
            overflow: "hidden",
            /* Widths and colour set separately: a responsive "1px solid"
               shorthand resets the colour to the text colour (bright white). */
            borderStyle: "solid",
            borderWidth: 0,
            borderColor: "divider",
            borderRightWidth: { xs: 0, lg: 1 },
            borderBottomWidth: { xs: 1, lg: 0 },
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
            height: "100%",
            minHeight: 0,
            display: "flex",
            flexDirection: "column",
            bgcolor: "background.paper",
            overflow: "hidden",
          }}
        >
          <Box
            sx={{
              flexShrink: 0,
              borderBottom: "1px solid",
              borderColor: "divider",
            }}
          >
            <Box sx={{ px: 2.5 }}>
              <CustomTabs
                value={activeTab}
                onChange={(_, v) => go(v)}
                variant="scrollable"
                scrollButtons={false}
              >
                {/* The Tooltip forwards the props Tabs clones onto its child
                  (value, selected, onChange…) to the Tab inside it. */}
                {visibleTabs.map((t) => (
                  <Tooltip
                    classes={{ tooltip: "ds-hovercard" }}
                    key={t.id}
                    value={t.id}
                    arrow
                    placement="bottom"
                    enterDelay={400}
                    enterNextDelay={200}
                    title={
                      tabHintOf(t.id, worldEnv) || gapsByStep[t.id]?.length ? (
                        <TabHint
                          hint={tabHintOf(t.id, worldEnv)}
                          gaps={gapsByStep[t.id]}
                        />
                      ) : (
                        ""
                      )
                    }
                    slotProps={{ tooltip: { sx: { maxWidth: 320 } } }}
                  >
                    <Tab
                      label={
                        <TabLabel
                          tab={t}
                          counts={counts}
                          gaps={gapsByStep[t.id]}
                        />
                      }
                    />
                  </Tooltip>
                ))}
              </CustomTabs>
            </Box>
          </Box>

          {/*
            Fall back to Overview for an unrecognised step rather than rendering
            an empty pane — a bad link should land somewhere, not nowhere.
          */}
          <Box sx={{ flex: 1, minWidth: 0, minHeight: 0, overflow: "auto" }}>
            {panel === "scenarios" ? (
              <ScenariosStep
                env={worldEnv}
                envState={envState}
                patch={patch}
                onGo={go}
                onBuilderPrompt={sendChat}
                onStartRun={startRun}
                groupCandidates={runGroup.candidates}
                locked={isSeededTemplate}
                onFork={() => setCopyOpen({})}
                selectionRun={{
                  repeats: headerTrials,
                  onRepeats: setHeaderTrials,
                  unit: env.surface === "voice" ? "call" : "conversation",
                  disabledReason: !runEnabled
                    ? runBlockedReason || "Not ready to run"
                    : null,
                }}
              />
            ) : panel === "evals" ? (
              <EvalsStep
                env={worldEnv}
                envState={envState}
                patch={patch}
                onGo={go}
                locked={isSeededTemplate}
                onFork={() => setCopyOpen({})}
                repeats={Number(headerTrials) || 3}
              />
            ) : panel === "runs" ? (
              <RunsPanel env={worldEnv} envState={envState} onGo={go} />
            ) : panel === "world" ? (
              <WorldPanel
                env={worldEnv}
                envState={envState}
                patch={patch}
                onGo={go}
                locked={isSeededTemplate || !!viewingOlder}
              />
            ) : panel === "contract" ? (
              <RlContractPanel
                env={worldEnv}
                envState={envState}
                patch={patch}
                onGo={go}
                locked={isSeededTemplate || !!viewingOlder}
                onFork={() => setCopyOpen({})}
              />
            ) : panel === "actors" ? (
              <ActorsPanel
                env={worldEnv}
                envState={envState}
                patch={patch}
                locked={isSeededTemplate || !!viewingOlder}
              />
            ) : panel === "analytics" ? (
              <AnalyticsPanel env={worldEnv} envState={envState} />
            ) : panel === "history" ? (
              <VersionHistoryMenu
                page
                env={worldEnv}
                envState={envState}
                patch={patch}
                readOnly={isSeededTemplate}
                onCopy={(label) => setCopyOpen({ version: label })}
              />
            ) : panel === "settings" ? (
              <SettingsPanel
                env={worldEnv}
                envState={envState}
                patch={patch}
                locked={isSeededTemplate}
                onFork={() => setCopyOpen({})}
                onDelete={
                  isSeededTemplate ? undefined : () => setConfirmDelete(env)
                }
              />
            ) : panel === "build" ? (
              <BuildRecordPanel
                env={worldEnv}
                envState={envState}
                patch={patch}
              />
            ) : (
              <OverviewPanel
                env={worldEnv}
                envState={envState}
                patch={patch}
                onGo={go}
                agentConnected={!!envState.agent}
                locked={isSeededTemplate}
                onFork={() => setCopyOpen({})}
              />
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
      <Typography component="span" sx={{ typography: "s2", color: "inherit" }}>
        {tab.label}
      </Typography>
      {count != null && count > 0 && (
        <Typography
          component="span"
          sx={{
            typography: "s3",
            color: "text.tertiary",
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {count}
        </Typography>
      )}
      {/* What needs input is listed in the tab's hover hint. */}
      {gaps?.length > 0 && (
        <Box
          sx={{
            display: "grid",
            placeItems: "center",
            flexShrink: 0,
            minWidth: 16,
            height: 16,
            px: "5px",
            borderRadius: "8px",
            bgcolor: "status.danger.bg",
            color: "status.danger.fg",
            typography: "s3",
            fontWeight: 600,
            lineHeight: 1,
            fontVariantNumeric: "tabular-nums",
            fontSize: 12,
          }}
        >
          {gaps.length}
        </Box>
      )}
    </Stack>
  );
}
TabLabel.propTypes = {
  tab: PropTypes.object,
  counts: PropTypes.object,
  gaps: PropTypes.array,
};

/* A tab's hover hint — what the page is for, and what still needs input
   on it before a run. Empty (no tooltip) when there's neither. */
function TabHint({ hint, gaps }) {
  if (!hint && !gaps?.length) return null;
  return (
    <Box>
      {hint && (
        <Typography sx={{ typography: "s3", color: "inherit" }}>
          {hint}
        </Typography>
      )}
      {gaps?.length > 0 && (
        <Box sx={{ mt: hint ? 0.75 : 0 }}>
          <Typography sx={{ typography: "s3", fontWeight: 600, mb: 0.375 }}>
            Answer these before you can run:
          </Typography>
          {gaps.map((g) => (
            <Typography key={g.id} sx={{ typography: "s3", opacity: 0.9 }}>
              · {g.title}
            </Typography>
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
  const preview = (rows || [])
    .slice(0, 2)
    .map((r) => r.title || r.name || "scenario")
    .join(", ");
  const rest = count > 2 ? ` +${count - 2}` : "";
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={1}
      sx={{
        px: 1.5,
        py: 1,
        borderBottom: "1px solid",
        borderColor: "divider",
        bgcolor: "action.hover",
      }}
    >
      <Iconify
        icon="solar:layers-minimalistic-linear"
        width={14}
        sx={{ color: "text.tertiary", flexShrink: 0 }}
      />
      <Typography
        sx={{ typography: "s3", color: "text.secondary", flex: 1, minWidth: 0 }}
        noWrap
      >
        Editing <b>{count}</b> scenario{count === 1 ? "" : "s"} — {preview}
        {rest}
      </Typography>
      <Tooltip arrow title="Clear selection">
        <IconButton size="small" onClick={onClear}>
          <Iconify
            icon="solar:close-circle-linear"
            width={14}
            sx={{ color: "text.tertiary" }}
          />
        </IconButton>
      </Tooltip>
    </Stack>
  );
}
SelectionContextChip.propTypes = {
  count: PropTypes.number,
  rows: PropTypes.array,
  onClear: PropTypes.func,
};

/*
  Guided mode's one question: which kind of scenarios to write. The option
  picked is what the scenarios are written for.
*/
function guidedScenarioQuestion() {
  return {
    step: 1,
    total: 1,
    prompt: "What kind of scenarios should I write?",
    multiSelect: false,
    options: [
      {
        label: "Happy path",
        description: "Standard requests with no edge cases",
      },
      {
        label: "Adversarial",
        description: "Rushed callers, off-topic tangents and skepticism",
      },
      {
        label: "Tool-fault cases",
        description:
          "A tool returns unexpected results and the agent has to cope",
      },
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
          direction="row"
          alignItems="center"
          spacing={1.25}
          sx={{
            px: 3,
            py: 1.25,
            bgcolor: "status.discovery.bg",
            borderBottom: "1px solid",
            borderColor: "status.discovery.border",
          }}
        >
          <Box
            sx={{
              width: 8,
              height: 8,
              borderRadius: "50%",
              bgcolor: "primary.main",
              animation: "wb-pulse 1.4s ease-in-out infinite",
              "@keyframes wb-pulse": {
                "0%,100%": { opacity: 0.4 },
                "50%": { opacity: 1 },
              },
              flexShrink: 0,
            }}
          />
          <Typography
            sx={{
              typography: "s2",
              fontWeight: 600,
              color: "primary.main",
              flexShrink: 0,
            }}
          >
            Environment is still being built
          </Typography>
          <Typography sx={{ typography: "s2", color: "text.secondary" }}>
            {buildProgress?.done != null && buildProgress?.total != null
              ? `${buildProgress.done} of ${buildProgress.total} steps done.`
              : "Scenarios and personas are still deriving."}{" "}
            You can leave and come back — this page will fill in as each stage
            lands.
          </Typography>
        </Stack>
      )}

      {offLatest && (
        <Stack
          direction="row"
          alignItems="center"
          spacing={1.25}
          sx={{
            px: 3,
            py: 1.25,
            bgcolor: "background.neutral",
            borderBottom: "1px solid",
            borderColor: "divider",
          }}
        >
          <Iconify
            icon="solar:history-linear"
            width={14}
            sx={{ color: "text.secondary", flexShrink: 0 }}
          />
          <Typography
            sx={{
              typography: "s2",
              fontWeight: 600,
              color: "text.primary",
              flexShrink: 0,
            }}
          >
            Editing off {envLabelOf(env, envState, active.label)}
          </Typography>
          <Typography
            sx={{
              typography: "s2",
              color: "text.secondary",
              flex: 1,
              minWidth: 0,
            }}
          >
            Latest is <b>{envLabelOf(env, envState, newest.label)}</b>. Runs
            from here will stamp against{" "}
            {envLabelOf(env, envState, active.label)}, and any edits sit on{" "}
            {envLabelOf(env, envState, active.label)}, not on the latest world.
          </Typography>
          <Button
            size="small"
            onClick={() => patch({ activeEnvVersion: newest.label })}
            sx={{ color: "text.primary", flexShrink: 0 }}
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
