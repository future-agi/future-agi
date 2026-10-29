import PropTypes from "prop-types";
import {
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";
import { useNavigate, useLocation } from "react-router-dom";
import { alpha } from "@mui/material/styles";
import {
  Box,
  Stack,
  Typography,
  Button,
  TextField,
  Tooltip,
  IconButton,
  Popover,
  Chip,
  Dialog,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { useSnackbar } from "notistack";
import { buildCreditsCheck } from "../_mock/metering";
import useCreditBalance from "../usage/useCreditBalance";
import { paths } from "src/routes/paths";
import { pipelineStatus, pipelineSummary } from "../_mock/buildPipeline";
import {
  subscribeScenarioSelection,
  getScenarioSelection,
} from "../_mock/scenarioSelectionBus";
import TrialsPicker from "../workspace/scenarios/TrialsPicker";
import { setupGaps, gapCounts } from "../_mock/setupGaps";
import { useSimStore, useEnvState, useConnectedAgents } from "../store";
import {
  HARNESS_STAGES,
  DERIVATION_OUTPUTS,
  builderRun,
  derivedEnvironment,
  detectedStack,
  runtimeTypeFor,
} from "../_mock/builder";
import { generatedPool } from "../_mock/scenarios";
import { getEnvironment } from "../_mock/environments";
import { scenariosForPack } from "./worldSource";
import { agentPromptOf } from "../_mock/agentPrompt";
import { agentPinPatch } from "../_mock/versions";
import { mcpBuildStateOf } from "../_mock/worldTools";
import { agentRecordFromSource, envAgentKey } from "../_mock/agentRegistry";
import {
  MODE_ICONS,
  REPLICATION_MODES,
  replicationRecordOf,
} from "../_mock/replication";
import AgentPromptDrawer from "../workspace/agents/AgentPromptDrawer";
import {
  stampProvenance as provStamp,
  currentUser,
} from "../_mock/scenarioProvenance";
import { detectAddScenariosIntent } from "../_mock/addScenariosIntent";
import {
  stampNewBatch,
  unanswerableNote,
  rejectedNote,
} from "../_mock/addScenarios";
import { asProposal } from "../_mock/scenarioSources";
import AssistantConsole from "../assistant/AssistantConsole";
import BuilderRail from "../workspace/BuilderRail";
import {
  readBuilderCollapsed,
  writeBuilderCollapsed,
} from "../workspace/builderCollapse";
import DerivedPanels from "./DerivedPanels";
import AddEvalsDrawer from "../workspace/evals/AddEvalsDrawer";
import AgentReadReceipt from "./AgentReadReceipt";
import { clickable, focusRing } from "../components/clickable";
import { LiveConsentCard } from "../agents/AgentLiveConsent";
import { uniqueEnvName } from "../_mock/envNames";
import { subGoalCatalogOf } from "../_mock/envModel";
import IssueReturnBanner from "../errorFeed/IssueReturnBanner";
import { currentAgentVersion } from "../_mock/versions";

/**
 * Stage 1 — connect an agent source.
 *
 * This replaces the old build-from-scratch wizard, which asked the user to
 * type tool names and rules. The builder reads them from the agent's source —
 * with exact argument names and permitted values — so asking for them produced
 * a strictly worse contract than reading them, and one nothing could prove.
 *
 * Two things the screen has to do that it previously did not: give a way back
 * out (this is a route, not a modal), and say what pointing at a source is
 * actually going to produce. Waiting is tolerable when you know what for.
 */
/* Fit the derived pool to a requested count. Past the pool's size, the same
   situations recur with a different caller persona, as a generator would. */
function sizeToCount(pool, count) {
  if (!count || !pool.length) return pool;
  if (count <= pool.length) return pool.slice(0, count);
  return Array.from({ length: count }, (_, i) => {
    const base = pool[i % pool.length];
    if (i < pool.length) return base;
    const round = Math.floor(i / pool.length);
    const persona =
      pool[(i * 7 + round) % pool.length]?.persona || base.persona;
    return { ...base, id: `${base.id}~${round + 1}`, persona };
  });
}

export default function BuildFromAgent() {
  const creditBalance = useCreditBalance();
  const navigate = useNavigate();
  const location = useLocation();
  const { state: storeState, dispatch } = useSimStore();
  /* Every write below waits for the saved state to load: a page reached from
     another area mounts a fresh store that hydrates from IndexedDB a moment
     later, and hydrating replaces state — a write made before it is lost
     (the build's agent, env v1 and adoption all were, once per environment). */
  const hydrated = !!storeState.hydrated;
  /* Read inside the adopt effect without re-running it on every store write. */
  const envsRef = useRef(storeState.myEnvironments);
  envsRef.current = storeState.myEnvironments;
  /* When the new "Create Environments" picker in StartEnvironment sends a
     user here, the source they picked (kind + value + credentials + agent
     type) rides along in location.state.presetSource. That skips the
     modality/kind selection UI and lands the user straight in the chat
     + derivation view. */
  const presetSource = location.state?.presetSource || null;
  /* Sent here by an Error Feed issue's Fix: the agent had no environment, and
     this first one becomes its main — Fix runs there. */
  const fromIssue = location.state?.fromIssue || null;
  /* Started from New improvement: the person came to improve the agent. */
  const toImprove = !!location.state?.toImprove;
  /* Set on this history entry once the build is in My environments — so
     coming back to it (Back from the first run, a reload) opens that
     environment instead of building a second copy from the same source. */
  /* Read once, on arrival: the build marks its own entry while it runs, and
     that must not send it away from itself. */
  const [builtEnvId] = useState(() => location.state?.builtEnvId || null);
  const bootedPresetRef = useRef(false);

  /* Name and difficulty live in the header now — an auto-generated name you
     can correct in place beats a form field asking you to invent one. */
  const [name, setName] = useState("");
  const [difficulty, setDifficulty] = useState("Advanced");
  const [editingName, setEditingName] = useState(false);
  const [evalIds, setEvalIds] = useState([]);
  const [addingEvals, setAddingEvals] = useState(false);
  const [source, setSource] = useState(null);
  const [turns, setTurns] = useState([]);
  const [done, setDone] = useState([]);
  const [running, setRunning] = useState(false);
  const [chips, setChips] = useState([]);
  const timers = useRef([]);
  /* A build stopped part-way: canceled by the person, or failed — with what
     the job said, and whether trying again can work. A retry runs the stage
     it stopped at again; what was done before it stays. */
  const [halt, setHalt] = useState(null);
  const [retryStage, setRetryStage] = useState(null);
  const [cancelAsk, setCancelAsk] = useState(false);
  /*
    Intake: a short questionnaire between "source picked" and "builder starts".
    The chat is full-width during intake so the questionnaire card can sit
    right above the input the same way Claude's own AskUserQuestion does. Once
    the user submits, `intake` flips and the layout shrinks the chat back to
    the left to make room for the derivation panels.
  */
  const [intake, setIntake] = useState(null);
  /*
    True after the "understand" stage finishes streaming, until the
    user acknowledges the AgentReadReceipt (audit of tools/rules/
    data/behavior + open questions). While true, the chain does not
    auto-advance to "build".
  */
  const [awaitReadAck, setAwaitReadAck] = useState(false);
  const [readAnswers, setReadAnswers] = useState(null);

  /* A new environment per build — minted once for this screen. */
  const [buildId] = useState(
    () => `env-returns-line-${Date.now().toString(36)}`,
  );
  const env = useMemo(
    () => (source ? derivedEnvironment(source, { id: buildId }) : null),
    [source, buildId],
  );
  /* Depth is a property of generation, so changing it regenerates the pool
     rather than just relabelling the environment. */
  const buildDone = done.includes("build");
  const scenariosDone = done.includes("scenarios");
  /* Started from a prebuilt world: its own proven scenarios come with it
     unchanged — the tests its sample agent runs — rather than a new set
     written for this agent, so the two compare on the same test. */
  /* "Adapt scenarios to my agent" writes new ones instead — a fork whose
     results don't compare with the sample. */
  const ws = source?.worldSource || null;
  const fixedWorld =
    ws?.kind === "template" && !ws.adapt
      ? getEnvironment(ws.id) || null
      : null;
  const draftPool = () =>
    fixedWorld
      ? scenariosForPack(fixedWorld, ws.pack || "full")
      : sizeToCount(
          generatedPool({ ...env, difficulty }),
          source?.scenarioCount,
        );
  /* Everything the build writes is one batch — the environment's first —
     added by whoever ran the build, at the moment it ran. Scenarios added
     afterwards arrive as batches of their own. */
  const [builtAt] = useState(() => new Date().toISOString());
  const pool = useMemo(
    // No cap unless the connect form asked for a count; then exactly that many.
    () =>
      env && buildDone
        ? draftPool().map((r) =>
            provStamp(r, {
              source: "derived",
              actor: currentUser(),
              at: builtAt,
              batchId: `batch_build_${buildId}`,
            }),
          )
        : [],
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [
      env,
      buildDone,
      difficulty,
      source?.scenarioCount,
      builtAt,
      buildId,
      fixedWorld,
    ],
  );
  /* Scenarios land one at a time while the stage writes them, so the
     Scenarios tab fills as they are written rather than all at once at the
     end. `revealed` counts how many have been written so far, `checked` how
     many of those have passed their checks — each is checked as it's
     written, so a new row reads "Checking…" until it's proved. */
  const [revealed, setRevealed] = useState(0);
  const [checked, setChecked] = useState(0);
  /* The scenario the third gate sends back once — the third written, as the
     builder's own narration says — rewritten and re-checked before it's kept. */
  const rewrittenIdx = pool.length ? Math.min(3, pool.length) - 1 : null;
  const scenarios = useMemo(() => {
    const mark = (r, i) => (i === rewrittenIdx ? { ...r, rewritten: true } : r);
    if (scenariosDone) return pool.map(mark);
    return pool
      .slice(0, revealed)
      .map((r, i) =>
        i >= checked ? { ...mark(r, i), checking: true } : mark(r, i),
      );
  }, [pool, revealed, checked, scenariosDone, rewrittenIdx]);
  const provedCount = scenarios.filter((r) => !r.checking).length;
  const scenarioSyncKey = `${scenarios.length}:${provedCount}`;
  const scenarioProgress = {
    shown: scenarios.length,
    total: pool.length,
    proved: provedCount,
    rewritten: rewrittenIdx != null && provedCount > rewrittenIdx ? 1 : 0,
    streaming: buildDone && !scenariosDone,
  };

  /* The contract as it's written: how many tools, rules and stores the
     builder has found so far. The Overview's build table fills in from this
     while the agent is read and the world is seeded. */
  const [contractFound, setContractFound] = useState({
    tools: 0,
    rules: 0,
    stores: 0,
    handlers: 0,
  });
  /* Streaming from the first frame of the build, not from the first tool:
     before any tool had been read this was false, and the table showed the
     whole finished contract for the two seconds until the first one landed. */
  const contractProgress = {
    ...contractFound,
    streaming: !!source && !buildDone,
  };
  /* Spread `count` reveals of one kind across a stretch of a stage's steps. */
  const streamFound = (kind, count, fromMs, toMs) => {
    for (let k = 1; k <= count; k += 1) {
      const at = fromMs + ((toMs - fromMs) * k) / count;
      timers.current.push(
        setTimeout(
          () =>
            setContractFound((c) => ({ ...c, [kind]: Math.max(c[kind], k) })),
          at,
        ),
      );
    }
  };

  useEffect(() => {
    if (!env) return;
    /* Numbered up front if the name is taken, so it doesn't change mid-build. */
    setName(uniqueEnvName(env.name || "", envsRef.current, env.id));
    setDifficulty(env.difficulty || "Advanced");
    /*
      Don't auto-seed the preset into envState.evals — the Evaluations
      panel now treats the preset as **suggestions** that the user
      explicitly promotes into Added. Auto-adding them would land the
      user on a screen where every suggestion is already "Added" and
      the empty Suggested list reads as broken.
    */
    setEvalIds([]);
  }, [env]);

  /*
    Derivation runs itself. There is nothing for a person to decide between
    reading the agent, building the world and proving the scenarios — each is
    the input to the next — so making them click through it was ceremony. The
    chat stays open the whole time for corrections and additions.
  */
  const STAGE_ORDER = ["understand", "build", "scenarios"];

  const play = (title, steps, nextChips, stageId, pace = 380) => {
    setRunning(true);
    setChips([]);
    const id = `t-${Date.now()}`;
    setTurns((prev) => [...prev, { id, role: "builder", title, steps: [] }]);
    steps.forEach((step, i) => {
      timers.current.push(
        setTimeout(
          () => {
            setTurns((prev) =>
              prev.map((t) =>
                t.id === id ? { ...t, steps: [...t.steps, step] } : t,
              ),
            );
            if (i !== steps.length - 1) return;

            setRunning(false);
            if (stageId)
              setDone((d) => (d.includes(stageId) ? d : [...d, stageId]));

            // Chain to the next stage rather than offering it as a button.
            const next = stageId
              ? STAGE_ORDER[STAGE_ORDER.indexOf(stageId) + 1]
              : null;
            if (next) {
              timers.current.push(setTimeout(() => runStage(next), 500));
            } else {
              setChips(asideChips(nextChips));
            }
          },
          pace * (i + 1),
        ),
      );
    });
  };

  /*
    The stage-advancing chips are gone with the clicking; what is left are the
    asides — ask what it found, or tell it to write more.
  */
  const asideChips = (list = []) => {
    const stageChips = HARNESS_STAGES.filter((st) => st.chip).map(
      (st) => st.chip,
    );
    return list.filter(
      (c) =>
        !stageChips.some((t) => c.startsWith(t)) &&
        !c.startsWith("use this environment"),
    );
  };

  const beginBuild = (answers) => {
    /* Apply the intake answers to the derived env before the builder
       fires: name, thoroughness (maps to difficulty + eventual scenario
       count), and a freeform focus note that would be piped into the
       generator in a real backend. */
    if (answers?.name)
      setName(uniqueEnvName(answers.name, envsRef.current, env?.id));
    if (answers?.difficulty) setDifficulty(answers.difficulty);
    setIntake({ ...answers, at: new Date().toISOString() });
    setTurns([]);
    /* Receipt-first was the previous behaviour — show the audit +
       open questions before streaming `understand`. Hidden for the
       demo cut; flip this back to `setAwaitReadAck(true)` (and drop
       the direct startDerivationAfterAck call) once the ingestion
       story is presentable again. */
    startDerivationAfterAck();
  };

  const startDerivationAfterAck = () => {
    setAwaitReadAck(false);
    const stage = builderRun("understand", source);
    /* Reading the agent is where the contract gets written, so it runs at a
       pace the table can visibly fill at: tools while their signatures are
       read (steps 3–5), rules while they're separated out (steps 6–8). */
    const pace = 600;
    setContractFound({ tools: 0, rules: 0, stores: 0, handlers: 0 });
    streamFound("tools", env?.tools?.length || 0, pace * 3, pace * 5);
    streamFound("rules", env?.rules?.length || 0, pace * 6, pace * 8);
    play(stage.title, stage.steps, stage.chips, "understand", pace);
  };

  /* Preset-source bootstrap — when a picker in StartEnvironment hands us
     a ready-made source via location.state, seed it into local state on
     mount and auto-fire beginBuild once source lands. That skips the
     modality/kind picker + intake questionnaire so the user sees the
     builder chat + derivation panels straight away — the whole point of
     clicking "Build environment" from the new picker. */
  useEffect(() => {
    if (!builtEnvId || !hydrated) return;
    bootedPresetRef.current = true;
    /* The environment it built is gone (deleted, or saved data restored):
       there's nothing to come back to, so the Environments list — never the
       old source form, which is a dead end on this route. */
    navigate(
      storeState.myEnvironments?.some((e) => e.id === builtEnvId)
        ? paths.dashboard.simulate.environmentDetail(builtEnvId)
        : paths.dashboard.simulate.environments,
      { replace: true },
    );
  }, [builtEnvId, hydrated]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!presetSource || bootedPresetRef.current || builtEnvId) return;
    bootedPresetRef.current = true;
    setSource(presetSource);
  }, [presetSource]);

  /* Guard: /environments/new/build is only meaningful with a presetSource
     in location.state. If someone lands here without one (browser back
     onto a stale URL, refresh mid-flow, hand-typed URL), bounce them to
     the Create Environments picker instead of showing them BuildFromAgent's
     modality picker fallback, which is dead-end in this route. */
  useEffect(() => {
    if (!presetSource && !source && location.pathname.endsWith("/new/build")) {
      navigate(paths.dashboard.simulate.environments, { replace: true });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    /* Only fire once, once source is present, and only for preset flows. */
    if (!presetSource || !source || intake) return;
    beginBuild({ name: "", difficulty: "Advanced" });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [source, presetSource]);

  /* Leaving the screen is checked first — it used to sit behind the stage
     lookup, which matched it and returned before ever getting here. */
  const onChip = (chip) => {
    const next = HARNESS_STAGES.find(
      (s) => s.chip && chip.startsWith(s.chip),
    )?.id;
    if (next) return runStage(next);
    onSend(chip);
  };

  const runStage = (id) => {
    /* The builder narrates the count this screen will actually produce — and
       the distinct sub-goals those scenarios use, the same count the
       Environments list shows. */
    const draft = env ? draftPool() : [];
    const draftCount = env ? draft.length : undefined;
    const subGoalCount = env
      ? subGoalCatalogOf(env, { scenarios: draft }).length
      : undefined;
    const stage = builderRun(id, {
      ...source,
      draftCount,
      subGoalCount,
      ...(fixedWorld && { fixedWorld: fixedWorld.name }),
    });
    /* Writing scenarios is the stage people wait on, so it runs slower and
       drops each scenario into the table as it is written — spread across
       the stage, finishing on its last step. Each is then checked: its row
       reads "Checking…" for a beat before it's proved. Checks run in order,
       so the one the third gate sends back holds the queue while it's
       rewritten and re-checked. */
    const pace = id === "scenarios" ? 650 : id === "build" ? 550 : 380;
    /* Writing scenarios spends AI credits. Past what's left the job stops
       before it writes any, and says so — a retry with fewer can work. */
    const requested = Number(source?.scenarioCount) || draftCount || 0;
    const credits =
      id === "scenarios" ? buildCreditsCheck(requested, creditBalance) : null;
    if (credits && !credits.ok) {
      setRunning(true);
      setChips([]);
      const turnId = `t-${Date.now()}`;
      setTurns((prev) => [
        ...prev,
        { id: turnId, role: "builder", title: stage.title, steps: [] },
      ]);
      stage.steps.slice(0, 1).forEach((step, i) =>
        timers.current.push(
          setTimeout(
            () => {
              setTurns((prev) =>
                prev.map((t) =>
                  t.id === turnId ? { ...t, steps: [...t.steps, step] } : t,
                ),
              );
            },
            pace * (i + 1),
          ),
        ),
      );
      timers.current.push(
        setTimeout(() => {
          setRunning(false);
          setHalt({
            kind: "failed",
            stage: "scenarios",
            stepId: "generate-scenarios",
            title: "Out of AI credits",
            message: `Stopped before writing any scenarios. ${credits.message}`,
            retryable: credits.affordable > 0,
            affordable: credits.affordable,
          });
          if (env?.id)
            dispatch({
              type: "patchEnvironment",
              envId: env.id,
              patch: { buildStatus: "failed" },
            });
        }, pace * 2.5),
      );
      return;
    }
    if (id === "build") {
      /* Writing handlers (steps 1–3), then seeding the world (steps 3–5). */
      streamFound("handlers", env?.tools?.length || 0, pace, pace * 3);
      streamFound("stores", env?.seed?.tables?.length || 0, pace * 3, pace * 5);
    }
    if (id === "scenarios" && draftCount) {
      setRevealed(0);
      setChecked(0);
      const from = pace;
      const span = pace * (stage.steps.length - 2) - from;
      const gap = span / draftCount;
      const redo = Math.min(3, draftCount); /* 1-based: the one sent back */
      let lastCheck = 0;
      for (let k = 1; k <= draftCount; k += 1) {
        const writtenAt = from + gap * k;
        timers.current.push(setTimeout(() => setRevealed(k), writtenAt));
        const checkFor = gap * (k === redo ? 2.2 : 0.7);
        lastCheck = Math.max(writtenAt, lastCheck) + checkFor;
        timers.current.push(setTimeout(() => setChecked(k), lastCheck));
      }
    }
    play(stage.title, stage.steps, stage.chips, id, pace);
  };

  /*
    The chat chip was the only way forward, so typing something else stranded
    you. This is the same progression as a persistent button that is always
    visible and always says what happens next.
  */
  /* Stages advance from the chat, which is where the builder offers them.
     The header carries the one thing the chat cannot do: leave. */
  const runNow = (scenarioIds, trials) =>
    adopt(
      { ...env, name: name.trim(), difficulty },
      "run",
      scenarioIds,
      trials,
    );

  const onSend = (text) => {
    setTurns((prev) => [
      ...prev,
      { id: `u-${Date.now()}`, role: "user", text },
    ]);
    /* Recognise "add N scenarios" here too, while the environment is still
       being built. Mirrors the intent detect in EnvironmentWorkspace.sendChat. */
    const intent = detectAddScenariosIntent(text);
    if (intent && env?.id && envPatch) {
      const existing = new Set((envState?.scenarios || []).map((s) => s.id));
      const pool = generatedPool(env).filter((s) => !existing.has(s.id));
      const fresh = pool.slice(0, intent.count);
      /* Stamped like every other add route — tools, proof, the environment
         version it was added under — so the tool check runs here too. */
      const batch = stampNewBatch(
        fresh.map((r) => ({ ...r, chatAsk: text })),
        { env, envState, source: "chat", actor: currentUser() },
      );
      /* Chat-written scenarios get the same review as every other source:
         proposed, in exploration, until someone approves them. */
      if (fresh.length > 0) {
        envPatch({
          scenarios: [
            ...(envState?.scenarios || []),
            ...batch.rows.map((r) => asProposal(r, "chat")),
          ],
        });
      }
      const warn =
        fresh.length > 0
          ? [unanswerableNote(batch), rejectedNote(batch)]
              .filter(Boolean)
              .join(" ") || null
          : null;
      setTurns((prev) => [
        ...prev,
        {
          id: `a-${Date.now()}`,
          role: "assistant",
          steps: [
            {
              kind: "note",
              text:
                fresh.length === 0
                  ? "Every scenario this agent's world can write is already here — remove a few on the Scenarios tab first."
                  : fresh.length < intent.count
                    ? `${batch.rows.length} proposed — review and add them on the Scenarios tab. Only ${fresh.length} new ones could be written.${warn ? ` ${warn}` : ""}`
                    : `${batch.rows.length} proposed — review and add them on the Scenarios tab.${warn ? ` ${warn}` : ""}`,
            },
          ],
        },
      ]);
      return;
    }
    const reply = builderRun("ask", source, text);
    play(
      null,
      reply.steps,
      asideChips(reply.chips.length ? reply.chips : chips),
      null,
    );
  };

  /*
    Broadcast a builder turn from a downstream panel (e.g. a panel when the
    user promotes an additional agent to source). Same shape as an internal
    stage — just no next-stage chaining and no gap-check. Keeps the panels
    unaware of `setTurns` / timers / running-state, and reuses the streaming
    animation so it feels like part of the same builder.
  */
  const runBuilderTurn = (title, steps) => {
    play(title, steps, chips, null);
  };

  /*
    Split from what used to be one `adopt()`. `prime` writes the derived env
    into the store so the workspace's own panels (Scenarios, Personas, Actors,
    Evals) can be mounted right here on the build screen — the user reviews
    and edits before the run rather than being sent to a second screen for it.
    `adopt` primes and then navigates.

    Priming runs whenever the derived env or its name changes; adoptEnvironment
    is idempotent for the same id, so re-priming is safe.
  */
  const prime = (confirmed) => {
    dispatch({
      type: "adoptEnvironment",
      env: {
        ...confirmed,
        ...(buildReplication && { replication: buildReplication }),
        ...(fromIssue && { returnToIssue: fromIssue, returnFrom: "built" }),
      },
      now: new Date().toISOString(),
    });
    /* The agent itself was attached when the environment was first created
       (see the seeding effect) — priming only carries the derived content. */
    const patch = {};
    if (scenarios?.length) patch.scenarios = scenarios;
    if (evalIds?.length) patch.evals = evalIds;
    /* Parallel worlds picked in the Build dialog become the environment's
       run setting — once; a later change in Settings isn't undone here. */
    const worlds = Math.round(Number(source?.parallelWorlds));
    const prevState = storeState.byEnv?.[confirmed.id];
    if (
      worlds >= 1 &&
      prevState?.parallelRuns === undefined &&
      prevState?.runDefaults?.conversationsAtOnce == null
    ) {
      patch.parallelRuns = worlds > 1;
      patch.runDefaults = {
        ...(prevState?.runDefaults || {}),
        conversationsAtOnce: worlds > 1 ? worlds : 1,
      };
    }
    if (Object.keys(patch).length) {
      dispatch({ type: "patchEnvState", envId: confirmed.id, patch });
    }
  };

  const adopt = (confirmed, mode = "open", scenarioIds, trials) => {
    prime(confirmed);
    let target;
    if (mode === "run") {
      target = paths.dashboard.simulate.simulationRun(
        confirmed.id,
        `run-${Date.now().toString(36)}`,
      );
      const params = new URLSearchParams();
      if (Array.isArray(scenarioIds) && scenarioIds.length > 0) {
        params.set("only", scenarioIds.join(","));
      }
      /* PRD §10.2 AC-10.7: only append when the user has changed
         it from the default 3, so plain links stay clean. */
      const k = Number(trials);
      if (Number.isFinite(k) && k >= 1) {
        params.set("trials", String(Math.min(20, Math.floor(k))));
      }
      const qs = params.toString();
      if (qs) target += `?${qs}`;
    } else {
      target = paths.dashboard.simulate.environmentDetail(confirmed.id);
    }
    /* Replace the build in history: Back from here goes to where the build
       started, never to a build that would start again. */
    navigate(target, {
      replace: true,
      ...(fromIssue && { state: { fromIssue } }),
    });
  };

  /* Live envState for whichever env has been primed. Feeds the panels below. */
  const { envState, patch: envPatch } = useEnvState(env?.id);
  /* What kind of copy this build runs against — recorded on the environment
     so every result can say where it came from. A provider clone is created
     here, labelled fagi-sim-<env>-<build>. */
  const buildReplication = useMemo(() => {
    if (!env || !buildsAgent(source)) return null;
    const agent = source.connectedAgent || agentForBuild(source, env, "");
    return agent ? replicationRecordOf(agent, env, 1) : null;
  }, [env, source]);

  /*
    Derivation being done + a name being set is the *floor* to run —
    but the header status chip already tracks blocking user gaps
    (e.g. "no evaluations added"), and a run cannot actually start
    while any of those are open. The button gates on the same signal
    the chip uses so both surfaces agree; the tooltip below names
    the first outstanding gap instead of a generic message.

    Lives BELOW useEnvState because it reads envState — moving it
    above triggered a temporal-dead-zone crash the moment env became
    truthy (click "Read this agent"), because `envState` was
    referenced before it was declared with `const`.
  */
  const blockingGapsForRun =
    env && envState
      ? setupGaps(env, envState).filter((g) => g.status === "blocking")
      : [];
  const derivationDoneForRun = done.length >= DERIVATION_OUTPUTS.length;
  const canLeave =
    derivationDoneForRun && !!name.trim() && blockingGapsForRun.length === 0;
  const runBlockedReason = !derivationDoneForRun
    ? "Finish the three stages on the left first"
    : !name.trim()
      ? "Name this environment first"
      : blockingGapsForRun.length > 0
        ? blockingGapsForRun[0].id === "no-evals"
          ? "Add at least one evaluation on the Evaluations tab"
          : blockingGapsForRun[0].title
        : "";

  /*
    Adopt the environment into My environments as soon as the pipeline has
    something meaningful to hand back — the read of the agent has finished
    (understand is done) and the derived evals have landed. That gives the
    user a card they can leave and come back to while scenarios and personas
    keep building in the background, instead of trapping them on this screen
    for the whole derivation.

    Two flags travel with it:
      buildStatus  — "building" until every derivation stage is done, then
                     "ready". The gallery card reads this to show a chip and
                     dim the card while it's still working.
      buildProgress — {done, total} so the card can show "3/3 done" or
                     "building scenarios · 2/3" without needing to know what
                     the stages are.
  */
  const adoptedRef = useRef(null);
  const readyStampedRef = useRef(false);
  const syncedScenariosRef = useRef(0);
  /*
    The derived env has a stable id (`env-returns-line`), so a previous
    session's envState — including anything the user added under
    `evals` — persists in localStorage. First-see of this env id, seed
    the Added-evaluations list with the environment's preset so Overview
    doesn't fire the "Add evaluations to run" gap while the user hasn't
    visited the Evals tab yet. User additions and removals made later in
    the same session are safe: this only fires the first time we see
    this env id. */
  const evalsResetRef = useRef(null);
  useEffect(() => {
    if (!env?.id || !hydrated) return;
    if (evalsResetRef.current === env.id) return;
    evalsResetRef.current = env.id;
    const preset = env.evalPreset || [];
    dispatch({
      type: "patchEnvState",
      envId: env.id,
      patch: { evals: preset },
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [env?.id, hydrated]);

  /*
    A build from an agent is a *fresh* environment at v1 — for both the agent
    and the environment version — with no run history. Each build has its own
    id, so this only ever initialises a new environment. Fires once per env
    id, so drawer-added versions and real runs later in the session are safe.
  */
  const versionsSeededRef = useRef(null);
  useEffect(() => {
    if (!env?.id || !hydrated) return;
    if (versionsSeededRef.current === env.id) return;
    versionsSeededRef.current = env.id;
    const now = new Date().toISOString();
    /* The environment is built from agent v1, so agent v1 is its test
       subject from the start: the agent the source points at, attached here.
       (A new build has no stored agent to reuse — it used to borrow one from
       the demo state under the shared id.) v1's tools are read from its
       source, and env v1's world is built to answer exactly those. */
    const v1Tools = (env.tools || []).map((t) => ({ ...t }));
    /* Built from an MCP server: record it as the World tab's Add tools does. */
    const mcp = mcpBuildStateOf(v1Tools);
    const mcpPatch = {
      ...(mcp && { toolEnv: { ...(envState.toolEnv || {}), ...mcp.toolEnv } }),
      /* The egress list given at connect is the environment's to start with — edited later in Settings. */
      ...(source?.allowedEgressDomains?.length && {
        egressDomains: source.allowedEgressDomains,
      }),
    };
    /* The real agent from the first moment — identity, definition (GitHub
       link, entry file) and where it runs — so leaving mid-build never leaves
       a placeholder behind for "Use a connected agent" to duplicate. */
    const connected = source?.connectedAgent;
    if (connected && !envState.agent) {
      const {
        versions: connectedVersions,
        usedIn,
        latest,
        id,
        ...record
      } = connected;
      dispatch({
        type: "patchEnvState",
        envId: env.id,
        patch: {
          agent: { ...record, connectedAt: record.connectedAt || now },
          ...(connectedVersions?.length && {
            agentVersions: connectedVersions,
          }),
          /* Built at the version picked — following production when that is it. */
          ...agentPinPatch(
            { agent: record, agentVersions: connectedVersions || [] },
            source.agentVersion || latest || "v1",
          ),
          envVersions: [
            {
              id: `${env.id}-v1`,
              label: "v1",
              createdAt: now,
              note: `First build, read from agent ${source.agentVersion || latest || "v1"}${source.worldSource?.label ? `, starting from ${source.worldSource.label}` : ""}${source.worldSource?.runsOn === "local" ? ", on your machine" : ""}.`,
              scenarios: envState.scenarios?.length || 0,
              changed: ["contract", "seed"],
              tools: v1Tools,
              builtFor: source.agentVersion || latest || "v1",
            },
          ],
          activeEnvVersion: "v1",
          runs: [],
          ...mcpPatch,
        },
      });
      return;
    }
    const agent = envState.agent
      ? {
          ...envState.agent,
          sourceKind: source?.kind || envState.agent.sourceKind || "repo",
        }
      : agentForBuild(source, env, now);
    /* Every environment belongs to an agent — an MCP server or a prebuilt
       world is the world source of an agent's build, never a build of its
       own. Only an old link to a source with no agent lands here. */
    if (!agent) {
      dispatch({
        type: "patchEnvState",
        envId: env.id,
        patch: {
          agent: null,
          envVersions: [
            {
              id: `${env.id}-v1`,
              label: "v1",
              createdAt: now,
              note: "First build.",
              scenarios: envState.scenarios?.length || 0,
              changed: ["contract", "seed"],
              tools: v1Tools,
            },
          ],
          activeEnvVersion: "v1",
          runs: [],
          ...mcpPatch,
        },
      });
      return;
    }
    dispatch({
      type: "patchEnvState",
      envId: env.id,
      patch: {
        agent,
        agentVersions: [
          {
            id: "agent-v1",
            label: "v1",
            note: "The version this environment was built from.",
            reach: source?.kind === "platform" ? "platform" : "endpoint",
            createdAt: now,
            tools: v1Tools,
            values: agent.values,
            via: agent.via,
            connectedAt: now,
            /* The prompt the build was read from — viewable from the build
               screen and from the test subject afterwards. */
            prompt: agentPromptOf(source, env),
          },
        ],
        activeAgentVersion: "v1",
        envVersions: [
          {
            id: `${env.id}-v1`,
            label: "v1",
            createdAt: now,
            note: "First build, read from agent v1.",
            scenarios: envState.scenarios?.length || 0,
            changed: ["contract", "seed"],
            tools: v1Tools,
            builtFor: "v1",
          },
        ],
        activeEnvVersion: "v1",
        runs: [],
        ...mcpPatch,
      },
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [env?.id, hydrated]);
  useEffect(() => {
    if (!env?.id || !hydrated) return;
    const understandDone = done.includes("understand");
    /* The derived evals land in the environment's state (its preset); ones
       added here by hand are in evalIds. Either counts. */
    const readyToAdopt =
      understandDone &&
      ((evalIds?.length || 0) > 0 || (envState.evals?.length || 0) > 0);
    const allDone = STAGE_ORDER.every((s) => done.includes(s));
    const buildProgress = { done: done.length, total: STAGE_ORDER.length };
    const buildStatus = allDone ? "ready" : "building";

    if (readyToAdopt && adoptedRef.current !== env.id) {
      /* The store numbers a name that's taken; show the same name here. */
      const adoptedName = uniqueEnvName(
        (name || env.name || "").trim(),
        envsRef.current,
        env.id,
      );
      if (adoptedName && adoptedName !== name) setName(adoptedName);
      dispatch({
        type: "adoptEnvironment",
        env: {
          ...env,
          name: adoptedName,
          difficulty,
          buildStatus,
          buildProgress,
          ...(buildReplication && { replication: buildReplication }),
          /* Built to run on the person's machine through the CLI, not in our sandbox. */
          ...(source?.worldSource?.runsOn === "local" && { runsOn: "local" }),
          /* From a ready world: its release and pack, the reviewed tool
             binding and the cases it can't measure — or adapted, a fork. */
          ...(ws?.kind === "template" && {
            templateId: ws.id,
            ...(ws.release && { release: ws.release }),
            ...(ws.catalogueId && { catalogueId: ws.catalogueId }),
            ...(ws.pack && { pack: ws.pack }),
            ...(ws.binding && { toolBinding: ws.binding }),
            ...(ws.unmeasured?.length && { unmeasured: ws.unmeasured }),
            ...(ws.adapt && { adaptedFrom: ws.id }),
          }),
          /* Built from an issue's Fix: the environment links back to it. */
          ...(fromIssue && { returnToIssue: fromIssue, returnFrom: "built" }),
        },
        now: new Date().toISOString(),
      });
      adoptedRef.current = env.id;
      navigate(location.pathname, {
        replace: true,
        state: { ...(location.state || {}), builtEnvId: env.id },
      });
      if (allDone) readyStampedRef.current = true;
    } else if (adoptedRef.current === env.id) {
      /* Keep the card in sync as the pipeline advances — buildProgress ticks
         each stage, and the final ready stamp fires exactly once. */
      if (allDone && !readyStampedRef.current) {
        dispatch({
          type: "patchEnvironment",
          envId: env.id,
          patch: { buildStatus: "ready", buildProgress },
        });
        readyStampedRef.current = true;
      } else if (!allDone) {
        dispatch({
          type: "patchEnvironment",
          envId: env.id,
          patch: { buildStatus, buildProgress },
        });
      }
    }

    /* envState catches up regardless of adoption — the panels on this screen
       read from it while the user reviews. */
    const nextPatch = {};
    /* Sync what the stage has written so far — keyed on what was last
       written and how many have passed their checks, not on the store, so a
       scenario the user adds or removes afterwards is not overwritten by the
       generated list, while a row turning from Checking… to Proved still is. */
    if (scenarios?.length && syncedScenariosRef.current !== scenarioSyncKey) {
      syncedScenariosRef.current = scenarioSyncKey;
      nextPatch.scenarios = scenarios;
    }
    if (evalIds?.length && (envState.evals?.length || 0) !== evalIds.length) {
      nextPatch.evals = evalIds;
    }
    if (Object.keys(nextPatch).length) {
      dispatch({ type: "patchEnvState", envId: env.id, patch: nextPatch });
    }
  }, [
    env?.id,
    hydrated,
    done,
    scenarios?.length,
    scenarioSyncKey,
    evalIds?.length,
    envState.scenarios?.length,
    envState.evals?.length,
    envState.agent,
    source,
    name,
    difficulty,
    dispatch,
    buildReplication,
  ]);

  /*
    Step 5, First run (RSI PRD: "the suite runs once on the current agent
    version to show where it stands"). Once the proven scenarios are saved,
    the builder ends at Ready to run and the person starts that run from Run
    simulation. A blocking gap (a Live agent's test-call consent, say) is
    named first — the header's gap chip is the one click.
  */
  const firstRunRef = useRef(null);
  const firstRunWaitRef = useRef(null);
  useEffect(() => {
    if (
      !env?.id ||
      !hydrated ||
      !scenariosDone ||
      adoptedRef.current !== env.id
    )
      return;
    if (firstRunRef.current === env.id || !envState?.agent) return;
    const saved = (envState.scenarios || []).filter(
      (r) => !r.proposed && !r.checking,
    );
    if (!pool.length || saved.length < pool.length) return;
    const gap = blockingGapsForRun[0];
    if (gap) {
      if (firstRunWaitRef.current === env.id) return;
      firstRunWaitRef.current = env.id;
      setTurns((prev) => [
        ...prev,
        {
          id: `first-run-wait-${Date.now()}`,
          role: "builder",
          title: "First run",
          steps: [
            {
              kind: "note",
              text: fromIssue
                ? `Waiting on one step: ${gap.title || "a setup step"}. Then continue fixing ${fromIssue} from the banner above.`
                : `Waiting on one step: ${gap.title || "a setup step"}. Then run it once — Run simulation, top right.`,
            },
          ],
        },
      ]);
      return;
    }
    /* Built — the first run is the person's to start and watch, like every
       run: a live agent's run makes real calls and costs credits, so it never
       starts (or gets recorded) on its own. Built from an issue's Fix, that
       first run is the fix's own first step — it measures these scenarios on
       today's version — so the note sends the person back to the fix. */
    firstRunRef.current = env.id;
    const agentLabel = currentAgentVersion(envState)?.label || "v1";
    if (toImprove && !fromIssue)
      dispatch({
        type: "patchEnvState",
        envId: env.id,
        patch: { improveIntent: { at: new Date().toISOString() } },
      });
    play(
      fromIssue ? "Ready to fix" : "Ready to run",
      [
        {
          kind: "note",
          text: fromIssue
            ? `Built — ${saved.length} scenarios. Continue fixing ${fromIssue} from the banner above: the fix's first step runs them on agent ${agentLabel}, so there's nothing to run by hand.`
            : toImprove
              ? `Built. Run it once on agent ${agentLabel} to see where it fails — ${saved.length} scenarios, 3 repeats each — then improve from what it finds. Run simulation, top right, starts it; when it finishes, the run's page picks the improvement up.`
              : `Built. Run it once on agent ${agentLabel} to see where it stands today — ${saved.length} scenarios, 3 repeats each. Run simulation, top right, starts it, and you watch it as it goes.`,
        },
      ],
      [],
      null,
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    env?.id,
    hydrated,
    scenariosDone,
    envState?.agent,
    envState?.scenarios,
    pool.length,
    blockingGapsForRun.length,
    fromIssue,
  ]);

  /* Back out of the source picker leaves the route; back out of a derivation
     returns to the picker, so a wrong URL is one click to fix, not a reload.
     Preset-source flows (arrived here from Create Environments) skip the
     modality picker entirely — so "back" for them jumps straight to
     /environments instead of dropping into a picker they never used. */
  /* Stopping and restarting. A restart runs the stage it stopped at, once
     the page has the source it should use (a smaller count, say). */
  const stageNow = STAGE_ORDER.find((sid) => !done.includes(sid)) || null;
  const FIRST_STEP_OF = {
    understand: "understand",
    build: "generate-env",
    scenarios: "generate-scenarios",
  };
  const building =
    !!source && !!stageNow && !halt && !(!intake && !presetSource);
  /* Finished while the question was open: there's nothing left to stop. */
  useEffect(() => {
    if (cancelAsk && !building) setCancelAsk(false);
  }, [cancelAsk, building]);
  const cancelBuild = () => {
    if (!stageNow) {
      setCancelAsk(false);
      return;
    }
    timers.current.forEach(clearTimeout);
    timers.current = [];
    setRunning(false);
    setCancelAsk(false);
    setHalt({
      kind: "canceled",
      stage: stageNow,
      stepId: FIRST_STEP_OF[stageNow],
    });
    setTurns((prev) => [
      ...prev,
      {
        id: `a-${Date.now()}`,
        role: "assistant",
        steps: [
          {
            kind: "note",
            text: "Build canceled. What was done before it stays; restart it from the bar at the top.",
          },
        ],
      },
    ]);
    if (env?.id)
      dispatch({
        type: "patchEnvironment",
        envId: env.id,
        patch: { buildStatus: "canceled" },
      });
  };
  const restartBuild = (count) => {
    if (count) setSource((cur) => ({ ...cur, scenarioCount: count }));
    const from = halt?.stage || stageNow;
    setHalt(null);
    if (env?.id)
      dispatch({
        type: "patchEnvironment",
        envId: env.id,
        patch: { buildStatus: "building" },
      });
    setRetryStage(from);
  };
  useEffect(() => {
    if (!retryStage || !source) return;
    const from = retryStage;
    setRetryStage(null);
    runStage(from);
  }, [retryStage, source]); // eslint-disable-line react-hooks/exhaustive-deps
  /* Said before the build gets there: this many scenarios is more than the credits left cover. */
  const creditsAhead =
    building && !done.includes("scenarios")
      ? buildCreditsCheck(Number(source?.scenarioCount) || 0, creditBalance)
      : null;

  const goBack = () => {
    timers.current.forEach(clearTimeout);
    timers.current = [];
    /* A finished build is kept as it stands — the name typed in the header
       included — before leaving; there's no separate "Open environment". */
    if (env && done.includes("build"))
      prime({ ...env, name: name.trim() || env.name, difficulty });
    if (!source || presetSource)
      return navigate(paths.dashboard.simulate.environments);
    setSource(null);
    setTurns([]);
    setDone([]);
    setRevealed(0);
    syncedScenariosRef.current = 0;
    setChips([]);
    setRunning(false);
    setIntake(null);
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
      <Header
        onBack={goBack}
        backLabel={source ? "Change source" : "All environments"}
        source={source}
        env={env}
        envState={envState}
        patch={envPatch}
        done={done}
        scenarioCount={scenarios?.length || 0}
        scenarioProgress={scenarioProgress}
        agentPrompt={
          buildsAgent(source) && env ? agentPromptOf(source, env) : null
        }
        replication={buildReplication}
        name={name}
        setName={setName}
        editingName={editingName}
        setEditingName={setEditingName}
        onRun={runNow}
        canGo={canLeave}
        blockedReason={runBlockedReason}
        halt={halt}
        onCancel={building ? () => setCancelAsk(true) : null}
      />

      {halt && <BuildHaltStrip halt={halt} onRestart={restartBuild} />}
      {!halt && creditsAhead && !creditsAhead.ok && (
        <BuildCreditsStrip
          credits={creditsAhead}
          count={Number(source?.scenarioCount) || 0}
          onUse={(n) => setSource((cur) => ({ ...cur, scenarioCount: n }))}
        />
      )}
      <Dialog
        open={cancelAsk}
        onClose={() => setCancelAsk(false)}
        PaperProps={{ sx: { width: 560, p: 2.5, backgroundImage: "none" } }}
      >
        <Typography sx={{ typography: "m3", fontWeight: 600 }}>
          Stop this build?
        </Typography>
        <Typography sx={{ typography: "s2", color: "text.secondary", mt: 1 }}>
          {`It stops at ${(STAGE_LABEL[stageNow] || "the current step").toLowerCase()}. What's done so far stays, and nothing more is built or charged. You can restart it from here.`}
        </Typography>
        <Stack
          direction="row"
          justifyContent="flex-end"
          spacing={1}
          sx={{ mt: 2.5 }}
        >
          <Button
            size="small"
            onClick={() => setCancelAsk(false)}
            sx={{ color: "text.secondary" }}
          >
            Keep building
          </Button>
          <Button
            size="small"
            variant="contained"
            color="primary"
            onClick={cancelBuild}
          >
            Stop build
          </Button>
        </Stack>
      </Dialog>

      {/* Sent here by an issue's Fix: once the environment exists, the shared
          banner says what's left and leads back (Continue fixing opens Fix). */}
      {fromIssue &&
      env?.id &&
      storeState.myEnvironments?.some(
        (e) => e.id === env.id && e.returnToIssue,
      ) ? (
        <IssueReturnBanner envId={env.id} />
      ) : (
        fromIssue && (
          <Stack
            direction="row"
            alignItems="center"
            spacing={1.25}
            sx={{
              px: 2,
              py: 1,
              borderBottom: "1px solid",
              borderColor: "divider",
              flexShrink: 0,
            }}
          >
            <Iconify
              icon="solar:bug-linear"
              width={16}
              sx={{ color: "text.secondary", flexShrink: 0 }}
            />
            <Typography
              sx={{
                typography: "s2",
                color: "text.secondary",
                flex: 1,
                minWidth: 0,
              }}
            >
              {`Building this agent's environment for ${fromIssue}. When it's built, you can continue the fix.`}
            </Typography>
          </Stack>
        )
      )}

      {/* The build view runs edge to edge — chat and environment split by one
          rule — so it drops the padding the picker and intake steps use. */}
      <Box
        sx={{
          flex: 1,
          minHeight: 0,
          overflow: source ? "hidden" : "auto",
          p: source && (intake || presetSource) && !awaitReadAck ? 0 : 2,
        }}
      >
        {/* This route always arrives with its source (the old source form
            and intake questionnaire are gone): until it's set, nothing. */}
        {!source ? null : awaitReadAck ? (
          /*
            Receipt-first: right after intake, before any streaming,
            the user audits what the reader extracted from the agent
            and resolves any open ambiguities. Clicking Build the
            world starts the full derivation chain (understand →
            build → scenarios).
          */
          <AgentReadReceipt
            agentRef={agentRefLabel(source, env)}
            reading={buildReading(env)}
            questions={buildQuestions(env)}
            onBack={() => setIntake(null)}
            onBuild={(answers) => {
              setReadAnswers(answers);
              startDerivationAfterAck();
            }}
          />
        ) : (
          <Deriving
            turns={turns}
            running={running}
            chips={chips}
            onSend={onSend}
            onChip={onChip}
            done={done}
            source={source}
            env={env}
            envState={envState}
            patch={envPatch}
            scenarios={scenarios}
            scenarioProgress={scenarioProgress}
            contractProgress={contractProgress}
            evalIds={evalIds}
            onAddEvals={() => setAddingEvals(true)}
            onBuilderTurn={runBuilderTurn}
            onRun={runNow}
          />
        )}
      </Box>

      {/* The same drawer the workspace uses — one eval flow, not two. */}
      <AddEvalsDrawer
        open={addingEvals}
        onClose={() => setAddingEvals(false)}
        env={env}
        envState={{ scenarios, evals: evalIds, agent: null }}
        existingIds={new Set(evalIds)}
        onAdd={(added) =>
          setEvalIds((ids) => [...new Set([...ids, ...added.map((e) => e.id)])])
        }
      />
    </Box>
  );
}

/* ── read-receipt payload builders ────────────────────────────────────────
   These shape env + source into the { agentRef, reading, questions } the
   AgentReadReceipt renders. Kept out of the component so it stays a pure
   presentation surface. */

function agentRefLabel(source, env) {
  if (source?.kind === "repo") {
    const ref = source.ref?.value ? `@${source.ref.value}` : "";
    return `${source.value || env?.id}${ref}`;
  }
  if (source?.kind === "endpoint") return source.value || env?.id;
  if (source?.kind === "platform")
    return `${source.provider || "platform"} · ${source.value || env?.id}`;
  return env?.id || "agent";
}

function buildReading(env) {
  if (!env) return { tools: [], rules: [], data: [], behavior: [] };
  const tools = (env.tools || []).map((t, i) => ({
    name: t.name,
    origin: i < 4 ? "config" : "callGraph",
  }));
  const rules = (env.rules || []).map((r) => ({
    name: r.length > 42 ? `${r.slice(0, 42)}…` : r,
    origin: "policy",
  }));
  const data = (env.seed?.tables || []).slice(0, 4).map((t) => ({
    name: `${t.name}.csv`,
    note: `${t.rows.toLocaleString()} rows`,
    origin: "fixture",
  }));
  /* Behavior — a small hand-picked set of prompt/inferred facts to
     illustrate the source distinction. */
  const behavior = [
    {
      name: "routing prompt",
      note: `${Math.max(3, (env.tools || []).length - 1)} branches`,
      origin: "prompt",
    },
    { name: "transfer → front desk", origin: "prompt" },
    { name: "escalate on 2× refusal", origin: "inferred" },
  ];
  return { tools, rules, data, behavior };
}

function buildQuestions(env) {
  if (!env) return [];
  const lastTool = (env.tools || [])[Math.max(0, (env.tools || []).length - 1)];
  const questions = [];
  if (lastTool) {
    questions.push({
      id: "tool-side-effects",
      title: `Does ${lastTool.name} change data?`,
      why: "It's called from your code but never described in the prompt. Your answer decides whether a scenario may trigger real side-effects.",
      kind: "choice",
      options: [
        { id: "read", label: "Read-only" },
        { id: "write", label: "Writes to state" },
        { id: "escalate", label: "Escalates externally" },
      ],
    });
  }
  questions.push({
    id: "policy-enforcement",
    title:
      "Are the policy values enforced in your backend, or only stated in the prompt?",
    why: "We can see the values, not where they're enforced. A rule we only infer is graded more softly than a hard one.",
    kind: "boolean",
  });
  questions.push({
    id: "refusal-escalation",
    title: "On a third refusal, does the agent transfer or end the call?",
    why: "The prompt covers the first two refusals, not beyond. Escalation scenarios need the real branch.",
    kind: "choice",
    options: [
      { id: "transfer", label: "Transfer to human" },
      { id: "end", label: "End the call" },
      { id: "loop", label: "Keep trying" },
    ],
  });
  /* Cap at 2 — the receipt is an audit, not an intake form. Two is
     the smallest count where distinct ambiguities are still named. */
  return questions.slice(0, 2);
}

/* ── header ──────────────────────────────────────────────────────────────── */

function Header({
  onBack,
  backLabel,
  source,
  env,
  envState,
  patch,
  done,
  running,
  scenarioCount,
  scenarioProgress,
  agentPrompt,
  replication,
  name,
  setName,
  editingName,
  setEditingName,
  onRun,
  canGo,
  blockedReason,
  halt = null,
  onCancel = null,
}) {
  const [promptOpen, setPromptOpen] = useState(false);
  const navigate = useNavigate();
  /* Live-test consent, given in place — saved to the agent like its Connections tab does. */
  const [consentOpen, setConsentOpen] = useState(false);
  const { setAgentValues } = useConnectedAgents();
  const { enqueueSnackbar } = useSnackbar();
  /* Hide the header's Run simulation when the Scenarios toolbar has
     transformed into the SelectionBar — its "Run N selected" is
     the primary in that state, and keeping the header CTA visible
     duplicates the affordance in two places. */
  const scenarioSelection = useSyncExternalStore(
    subscribeScenarioSelection,
    getScenarioSelection,
    getScenarioSelection,
  );
  const selectionCount = scenarioSelection?.ids?.length || 0;
  /* Repeats picker — same PRD AC-10.7 dial as the workspace header.
     Run simulation starts straight away with this value. */
  const [headerTrials, setHeaderTrials] = useState(3);
  /*
    The environment stage builds three things — tool handlers, a seeded world
    and coded checks — none of which are "sub-goals" as this product uses the
    word (sub-goals are the checks themselves, one line down in write_checks).
    Counting tools+rules and labelling the total "sub-goals" was flattening two
    different concepts into one number and calling it a third.
  */
  /*
    The full pipeline, opened from the milestone rail below.

    The four milestones summarise; the twelve steps say what actually happened
    to arrive at them. Rendered as a popover rather than expanded inline so the
    header stays compact — the ninety per cent case is "glance, keep working",
    not "audit the pipeline".
  */
  const [pipeAnchor, setPipeAnchor] = useState(null);
  const pipeline = pipelineStatus(
    done,
    running && !halt,
    "setup",
    halt?.kind === "failed"
      ? { stepId: halt.stepId, message: halt.message }
      : null,
  );
  const pipeSummary = pipelineSummary(pipeline);
  /* True once every setup-phase stage has finished — the environment is built.
     Hoisted to the Header body so both the status chip and the Run-simulation
     button (which is hidden until the env is built) can read it. */
  const setupDone = pipeline
    .filter((st) => st.phase === "setup")
    .every((st) => st.status === "done");

  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={2}
      sx={{
        px: 3,
        py: 1.75,
        borderBottom: "1px solid",
        borderColor: "divider",
        flexShrink: 0,
      }}
    >
      <Tooltip title={backLabel} arrow>
        <Button
          onClick={onBack}
          sx={{
            minWidth: 32,
            width: 32,
            color: "text.subtitle",
            flexShrink: 0,
          }}
        >
          <Iconify icon="solar:alt-arrow-left-linear" width={20} />
        </Button>
      </Tooltip>

      {!source ? (
        <Box flex={1} minWidth={0}>
          <Stack direction="row" alignItems="center" spacing={1}>
            <Typography noWrap sx={{ typography: "s1_2", fontWeight: 600 }}>
              Connect agent source
            </Typography>
            <Typography
              sx={{ typography: "s3", color: "text.subtitle", flexShrink: 0 }}
            >
              · Stage 1
            </Typography>
          </Stack>
          <Typography noWrap sx={{ typography: "s2", color: "text.subtitle" }}>
            We read the agent rather than asking you to describe it
          </Typography>
        </Box>
      ) : (
        <>
          {/* The derived name, corrected in place rather than asked for in a form. */}
          <Stack
            direction="row"
            alignItems="center"
            spacing={1}
            sx={{ flexShrink: 0, minWidth: 0 }}
          >
            {editingName ? (
              <TextField
                size="small"
                value={name}
                autoFocus
                onChange={(e) => setName(e.target.value)}
                onBlur={() => setEditingName(false)}
                onKeyDown={(e) => e.key === "Enter" && setEditingName(false)}
                sx={{
                  width: 360,
                  "& .MuiInputBase-input": {
                    typography: "s1_2",
                    fontWeight: 600,
                    py: 0.5,
                  },
                }}
              />
            ) : (
              <>
                <Typography
                  noWrap
                  title={name}
                  sx={{ typography: "s1_2", fontWeight: 600, maxWidth: 360 }}
                >
                  {name}
                </Typography>
                <Tooltip arrow title="Rename">
                  <IconButton size="small" onClick={() => setEditingName(true)}>
                    <Iconify
                      icon="solar:pen-new-square-linear"
                      width={14}
                      sx={{ color: "text.subtitle" }}
                    />
                  </IconButton>
                </Tooltip>
                {/* No env-version chip — the environment's own page has its history. */}
                {/*
                  Env status chip.

                  Sits inside the identity cluster (name · version · status)
                  rather than in a separate center slot, so a reader sees one
                  group of chips that all describe the same thing — this env's
                  current state. Building/Live/gap/failed states are mutually
                  exclusive; the IIFE picks whichever applies.
                  Building is purple everywhere (the environments table's colour).
                */}
                <Box sx={{ display: { xs: "none", md: "block" } }}>
                  {(() => {
                    const running = pipeline.find(
                      (st) => st.status === "running",
                    );
                    /*
                "Ready to run" was only checking pipeline status —
                it flipped green the moment derivation finished, even
                though blocking user gaps (no evals added, missing
                sandbox secret) genuinely stop a run. Read the same
                setupGaps the tabs use so the chip tells the truth.
              */
                    const blockingGaps =
                      env && envState
                        ? setupGaps(env, envState).filter(
                            (g) => g.status === "blocking",
                          )
                        : [];

                    if (pipeSummary.failed) {
                      return (
                        <Stack
                          direction="row"
                          alignItems="center"
                          spacing={1}
                          sx={{
                            px: 1.25,
                            py: 0.625,
                            borderRadius: 999,
                            border: "1px solid",
                            borderColor: "status.danger.border",
                            bgcolor: "status.danger.bg",
                          }}
                        >
                          <Iconify
                            icon="solar:danger-triangle-bold"
                            width={14}
                            sx={{ color: "status.danger.fg" }}
                          />
                          <Typography
                            sx={{
                              typography: "s2",
                              fontWeight: 600,
                              color: "status.danger.fg",
                            }}
                          >
                            Failed at {pipeSummary.failed.label.toLowerCase()}
                          </Typography>
                        </Stack>
                      );
                    }
                    if (halt?.kind === "canceled") {
                      return (
                        <Stack
                          direction="row"
                          alignItems="center"
                          spacing={0.75}
                          sx={{
                            px: 1.125,
                            py: 0.5,
                            borderRadius: 999,
                            border: "1px solid",
                            borderColor: "divider",
                          }}
                        >
                          <Iconify
                            icon="solar:stop-circle-linear"
                            width={14}
                            sx={{ color: "text.secondary" }}
                          />
                          <Typography
                            sx={{
                              typography: "s2",
                              fontWeight: 600,
                              color: "text.secondary",
                            }}
                          >
                            Build canceled
                          </Typography>
                        </Stack>
                      );
                    }
                    if (running || !setupDone) {
                      return (
                        <Stack
                          direction="row"
                          alignItems="center"
                          spacing={0.75}
                          sx={{
                            px: 1.125,
                            py: 0.5,
                            borderRadius: 999,
                            border: "1px solid",
                            borderColor: "status.discovery.border",
                            bgcolor: "status.discovery.bg",
                          }}
                        >
                          <Box
                            sx={{
                              width: 6,
                              height: 6,
                              borderRadius: "50%",
                              bgcolor: "status.discovery.solid",
                              flexShrink: 0,
                              animation: "chip-pulse 1.4s ease-in-out infinite",
                              "@keyframes chip-pulse": {
                                "0%,100%": {
                                  opacity: 0.4,
                                  transform: "scale(1)",
                                },
                                "50%": { opacity: 1, transform: "scale(1.15)" },
                              },
                            }}
                          />
                          <Typography
                            sx={{
                              typography: "s2",
                              fontWeight: 600,
                              color: "status.discovery.fg",
                              fontVariantNumeric: "tabular-nums",
                            }}
                          >
                            {scenarioProgress?.streaming &&
                            scenarioProgress.total
                              ? `Writing scenarios · ${scenarioProgress.shown} of ${scenarioProgress.total}`
                              : "Building"}
                          </Typography>
                        </Stack>
                      );
                    }

                    /*
                Blocking gaps outrank "ready" — the run can't start until
                they're answered. When there's a single well-known gap
                (e.g. no evaluations added), name it instead of the
                generic count so the user knows exactly what to do.
              */
                    if (blockingGaps.length > 0) {
                      const g = blockingGaps[0];
                      const label =
                        blockingGaps.length === 1
                          ? g.id === "no-evals"
                            ? "Add evaluations to run"
                            : g.title || "1 step to complete"
                          : `${blockingGaps.length} steps to complete`;
                      /* Neutral, with the one red mark. Live-test consent is given right
                   here in a dialog; anything else goes where the step is done. */
                      const consent =
                        blockingGaps.length === 1 &&
                        g.code === "gap_live_consent" &&
                        !!envState?.agent;
                      const href =
                        !consent && blockingGaps.length === 1 && g.ask?.href
                          ? g.ask.href
                          : null;
                      return (
                        <Tooltip
                          arrow
                          title={
                            blockingGaps.length === 1
                              ? g.why || ""
                              : blockingGaps.map((x) => x.title).join(" · ")
                          }
                        >
                          <Stack
                            direction="row"
                            alignItems="center"
                            spacing={0.75}
                            {...clickable(
                              consent
                                ? () => setConsentOpen(true)
                                : href
                                  ? () => navigate(href)
                                  : undefined,
                            )}
                            sx={{
                              ...focusRing,
                              px: 1,
                              height: 24,
                              borderRadius: "4px",
                              border: "1px solid",
                              borderColor: "divider",
                              cursor: href || consent ? "pointer" : "default",
                              "&:hover":
                                href || consent
                                  ? { bgcolor: "action.hover" }
                                  : undefined,
                            }}
                          >
                            <Iconify
                              icon="solar:danger-triangle-linear"
                              width={14}
                              sx={{ color: "status.danger.fg" }}
                            />
                            <Typography
                              noWrap
                              sx={{
                                typography: "s3",
                                fontWeight: 600,
                                color: "text.primary",
                              }}
                            >
                              {label}
                            </Typography>
                            {(href || consent) && (
                              <Iconify
                                icon="solar:arrow-right-linear"
                                width={12}
                                sx={{ color: "text.subtitle" }}
                              />
                            )}
                          </Stack>
                        </Tooltip>
                      );
                    }

                    /*
                Just the pill. The "N need your input" chip lived here too, but
                the dedicated Needs-your-input tab already carries that count in
                its label — a second surface for the same signal was noise.
              */
                    return (
                      <Stack
                        direction="row"
                        alignItems="center"
                        spacing={0.75}
                        sx={{
                          px: 1.125,
                          py: 0.5,
                          borderRadius: 999,
                          border: "1px solid",
                          borderColor: "status.success.border",
                          bgcolor: "status.success.bg",
                        }}
                      >
                        <Box
                          sx={{
                            width: 6,
                            height: 6,
                            borderRadius: "50%",
                            bgcolor: "status.success.solid",
                          }}
                        />
                        <Typography
                          sx={{
                            typography: "s2",
                            fontWeight: 600,
                            color: "status.success.fg",
                          }}
                        >
                          Ready
                        </Typography>
                      </Stack>
                    );
                  })()}
                </Box>
                {/* The copy this build runs against — for a provider clone,
                    the labelled clone it created. */}
                {replication && <CopyChip replication={replication} />}
                {/* The prompt the build is reading — open while it builds and
                    after, so what was derived can be checked against it. */}
                {agentPrompt && (
                  <Tooltip
                    arrow
                    title="The agent's v1 prompt — what this build read"
                  >
                    <IconButton
                      size="small"
                      onClick={() => setPromptOpen(true)}
                      aria-label="Agent v1 prompt"
                      sx={{ flexShrink: 0 }}
                    >
                      <Iconify
                        icon="solar:document-text-linear"
                        width={16}
                        sx={{ color: "text.subtitle" }}
                      />
                    </IconButton>
                  </Tooltip>
                )}
              </>
            )}
          </Stack>
          <AgentPromptDrawer
            open={promptOpen}
            onClose={() => setPromptOpen(false)}
            label="v1"
            prompt={agentPrompt}
          />
          {envState?.agent && (
            <Dialog
              open={consentOpen}
              onClose={() => setConsentOpen(false)}
              maxWidth={false}
              fullWidth
              PaperProps={{
                sx: {
                  maxWidth: 720,
                  borderRadius: 2,
                  backgroundImage: "none",
                  border: "1px solid",
                  borderColor: "divider",
                },
              }}
            >
              <LiveConsentCard
                agent={envState.agent}
                env={env}
                onCancel={() => setConsentOpen(false)}
                onSave={(values, msg) => {
                  setAgentValues(envAgentKey(envState.agent, env?.id), values);
                  if (msg) enqueueSnackbar(msg, { variant: "success" });
                  setConsentOpen(false);
                }}
              />
            </Dialog>
          )}

          {/* Spacer pushes the Run-simulation button to the far right;
              status chip now lives in the identity cluster on the left. */}
          <Box sx={{ flex: 1 }} />
        </>
      )}

      {/* While it builds, the one thing to do is stop it. */}
      {onCancel && !setupDone && (
        <Button
          size="small"
          variant="outlined"
          onClick={onCancel}
          startIcon={<Iconify icon="solar:stop-circle-linear" width={16} />}
          sx={{ color: "text.primary", borderColor: "divider", flexShrink: 0 }}
        >
          Cancel build
        </Button>
      )}
      {source &&
        setupDone &&
        (() => {
          const totalCount = scenarioCount || envState?.scenarios?.length || 0;
          const runCount = selectionCount > 0 ? selectionCount : totalCount;
          const runLabel =
            selectionCount > 0 ? "Run selected" : "Run simulation";
          return (
            <Stack direction="row" alignItems="center" spacing={1}>
              {/* No "Open environment" — this already is the environment; Back keeps it as it stands. */}
              <TrialsPicker
                trials={headerTrials}
                onChange={setHeaderTrials}
                scenarioCount={runCount}
                unit={env?.surface === "voice" ? "call" : "conversation"}
              />
              <RunButton
                label={runLabel}
                onRun={() => {
                  const sel = scenarioSelection?.ids || [];
                  onRun(sel.length > 0 ? sel : undefined, headerTrials);
                }}
                canGo={canGo}
                blockedReason={blockedReason}
              />
            </Stack>
          );
        })()}
    </Stack>
  );
}

/* The copy a build runs against, as one quiet chip: the mode, and for a
   provider clone the labelled clone's name. The tooltip says the rest. */
function CopyChip({ replication }) {
  const meta = REPLICATION_MODES[replication.mode] || {};
  /* Sealed · Live · Prompt only — recorded with the build since the PRD's live model. */
  const proof = replication.proofLabel || replication.fidelityLabel;
  /* The same "Runs: Live" chip the environment page shows; which copy it
     runs against is on hover. */
  const tip = replication.cloneName
    ? `Runs against ${replication.cloneName}, a test copy in your account, called with its real tools.`
    : meta.how || replication.label || "";
  return (
    <Tooltip arrow title={tip}>
      <Chip
        size="small"
        variant="outlined"
        icon={
          <Iconify
            icon={MODE_ICONS[replication.mode] || "solar:copy-linear"}
            width={12}
          />
        }
        label={
          <>
            <Box component="span" sx={{ color: "text.tertiary", mr: 0.5 }}>
              Runs:
            </Box>
            {proof || replication.label}
          </>
        }
        sx={{
          display: { xs: "none", md: "inline-flex" },
          flexShrink: 0,
          cursor: "default",
        }}
      />
    </Tooltip>
  );
}
CopyChip.propTypes = { replication: PropTypes.object };

/* Header's Run button — kept plain "Run simulation" (run-all) per
   PRD §6.1.2: "hand-curation and chat-curation must not share a
   control". Selection-scoped actions live in the SelectionBar; the
   env header stays context-free. */
function RunButton({ onRun, canGo, blockedReason, label = "Run simulation" }) {
  return (
    <Tooltip arrow title={canGo ? "" : blockedReason || ""}>
      <span>
        <Button
          variant="contained"
          color="primary"
          disabled={!canGo}
          onClick={() => onRun()}
          startIcon={<Iconify icon="solar:play-bold" width={14} />}
          sx={{ flexShrink: 0, whiteSpace: "nowrap" }}
        >
          {label}
        </Button>
      </span>
    </Tooltip>
  );
}
RunButton.propTypes = {
  onRun: PropTypes.func.isRequired,
  canGo: PropTypes.bool,
  blockedReason: PropTypes.string,
  label: PropTypes.string,
};

Header.propTypes = {
  /* A build stopped part-way ({ kind: "canceled" | "failed", … }), and how to stop one. */
  halt: PropTypes.object,
  onCancel: PropTypes.func,
  onBack: PropTypes.func,
  backLabel: PropTypes.string,
  source: PropTypes.object,
  env: PropTypes.object,
  envState: PropTypes.object,
  patch: PropTypes.func,
  scenarioCount: PropTypes.number,
  done: PropTypes.array,
  running: PropTypes.bool,
  scenarioProgress: PropTypes.object,
  agentPrompt: PropTypes.object,
  replication: PropTypes.object,
  name: PropTypes.string,
  setName: PropTypes.func,
  editingName: PropTypes.bool,
  setEditingName: PropTypes.func,
  onRun: PropTypes.func,
  canGo: PropTypes.bool,
  blockedReason: PropTypes.string,
};

/* ── the engine working ──────────────────────────────────────────────────── */

function Deriving({
  turns,
  running,
  chips,
  onSend,
  onChip,
  done,
  source,
  env,
  envState,
  patch,
  scenarios,
  scenarioProgress,
  contractProgress,
  evalIds,
  onAddEvals,
  onBuilderTurn,
  onRun,
}) {
  /* The builder folds to a rail here too — the same choice as the
     environment page, remembered across both. */
  const [collapsed, setCollapsedRaw] = useState(readBuilderCollapsed);
  const [unseen, setUnseen] = useState(0);
  const setCollapsed = (v) => {
    setCollapsedRaw(v);
    if (!v) setUnseen(0);
    writeBuilderCollapsed(v);
  };
  const seenTurns = useRef(turns.length);
  useEffect(() => {
    const added = turns.length - seenTurns.current;
    seenTurns.current = turns.length;
    if (collapsed && added > 0 && turns[turns.length - 1]?.role === "assistant")
      setUnseen((n) => n + 1);
  }, [turns, collapsed]);
  return (
    <Box
      sx={{
        display: "grid",
        height: "100%",
        minHeight: 0,
        /*
          Chat narrow, artifacts wide — the shape Lovable, Figma Make and v0
          all landed on for the same reason: the input is a column of text and
          the output is a whole surface. An even split turned the right side
          into a cramped preview of what it should be showing at full size.
          Ratio is ~1:2.6 on desktop; still stacked on mobile.
        */
        gridTemplateColumns: {
          xs: "1fr",
          lg: collapsed ? "48px 1fr" : "minmax(360px, 400px) 1fr",
        },
      }}
    >
      {collapsed && (
        <BuilderRail
          running={running}
          unseen={unseen}
          onExpand={() => setCollapsed(false)}
        />
      )}
      {/* One surface, split by a single rule — no card around either side. */}
      <Box
        sx={{
          height: "100%",
          minHeight: 0,
          display: "flex",
          flexDirection: "column",
          overflow: "hidden",
          ...(collapsed && { display: "none" }),
          /* Widths and colour set separately: a responsive "1px solid"
             shorthand resets the colour to the text colour, which drew this
             rule bright white instead of the divider grey. */
          borderStyle: "solid",
          borderWidth: 0,
          borderColor: "divider",
          borderRightWidth: { xs: 0, lg: 1 },
          borderBottomWidth: { xs: 1, lg: 0 },
        }}
      >
        <AssistantConsole
          onCollapse={() => setCollapsed(true)}
          turns={turns}
          running={running}
          chips={chips}
          onSend={onSend}
          onChip={onChip}
          frozen={env?.buildStatus === "building"}
          frozenReason="Environment is still being built"
        />
      </Box>

      <Box
        sx={{
          minHeight: 0,
          display: "flex",
          flexDirection: "column",
          overflow: "hidden",
          px: 2,
        }}
      >
        <PanelBoundary>
          <DerivedPanels
            env={env}
            envState={envState}
            patch={patch}
            source={source}
            done={done}
            running={running}
            scenarios={scenarios}
            scenarioProgress={scenarioProgress}
            contractProgress={contractProgress}
            evalIds={evalIds}
            onAddEvals={onAddEvals}
            onBuilderTurn={onBuilderTurn}
            onBuilderPrompt={onSend}
            onStartRun={onRun}
          />
        </PanelBoundary>
      </Box>
    </Box>
  );
}

/**
 * Component-scoped error boundary around the derivation panels. If
 * something in there throws — a shape mismatch, a bad ref, whatever
 * — we render the error inline instead of nuking the whole app
 * behind the top-level "Houston" page. Users can copy the message
 * and keep working on the left-hand chat.
 */
import { Component as ReactComponent } from "react";
class PanelBoundary extends ReactComponent {
  constructor(props) {
    super(props);
    this.state = { err: null };
  }
  static getDerivedStateFromError(err) {
    return { err };
  }
  componentDidCatch(err, info) {
    // eslint-disable-next-line no-console
    console.error("[PanelBoundary] caught:", err, info?.componentStack);
  }
  render() {
    if (this.state.err) {
      return (
        <Box
          sx={{
            p: 3,
            m: 2,
            border: "1px solid",
            borderColor: "status.danger.border",
            borderRadius: "8px",
            bgcolor: "status.danger.bg",
          }}
        >
          <Typography
            sx={{
              typography: "s1",
              fontWeight: 600,
              color: "status.danger.fg",
              mb: 1,
            }}
          >
            The right-side panel crashed
          </Typography>
          <Typography
            sx={{ typography: "s2", color: "text.secondary", mb: 1.5 }}
          >
            The chat on the left still works. Copy the message below and paste
            it back to me.
          </Typography>
          <Typography
            component="pre"
            sx={{
              typography: "s3",
              fontFamily: "ui-monospace, Menlo, monospace",
              p: 1.5,
              borderRadius: 1,
              bgcolor: "background.neutral",
              whiteSpace: "pre-wrap",
              wordBreak: "break-word",
              color: "text.primary",
            }}
          >
            {String(this.state.err?.message || this.state.err)}
            {"\n\n"}
            {String(this.state.err?.stack || "")
              .split("\n")
              .slice(0, 5)
              .join("\n")}
          </Typography>
          <Button
            variant="text"
            size="small"
            onClick={() => this.setState({ err: null })}
            sx={{ mt: 1 }}
          >
            Retry
          </Button>
        </Box>
      );
    }
    return this.props.children;
  }
}
PanelBoundary.propTypes = { children: PropTypes.node };

Deriving.propTypes = {
  turns: PropTypes.array,
  running: PropTypes.bool,
  chips: PropTypes.array,
  onSend: PropTypes.func,
  onChip: PropTypes.func,
  done: PropTypes.array,
  source: PropTypes.object,
  env: PropTypes.object,
  envState: PropTypes.object,
  patch: PropTypes.func,
  scenarios: PropTypes.array,
  scenarioProgress: PropTypes.object,
  contractProgress: PropTypes.object,
  evalIds: PropTypes.array,
  onAddEvals: PropTypes.func,
  onBuilderTurn: PropTypes.func,
  onRun: PropTypes.func,
};

/*
  The full pipeline as a popover.

  Grouped by phase — Setup and First run — because they belong to different
  parts of the story: setup happens once when the environment is built, and the
  first-run phase happens each time somebody runs a scenario. A single flat
  list would have blurred that, and someone would have read "Uploading
  artifacts" as a setup task.
*/
function PipelinePopover({ anchor, onClose, pipeline, summary }) {
  const phases = [
    { id: "setup", label: "Setup — building the environment" },
    { id: "run", label: "First run — putting the agent through it" },
  ];

  return (
    <Popover
      open={!!anchor}
      anchorEl={anchor}
      onClose={onClose}
      anchorOrigin={{ vertical: "bottom", horizontal: "center" }}
      transformOrigin={{ vertical: "top", horizontal: "center" }}
      PaperProps={{
        sx: {
          mt: 1,
          width: 420,
          borderRadius: 1,
          backgroundImage: "none",
          border: "1px solid",
          borderColor: "divider",
        },
      }}
    >
      <Stack
        direction="row"
        alignItems="center"
        spacing={1}
        sx={{
          px: 2,
          py: 1.5,
          borderBottom: "1px solid",
          borderColor: "divider",
        }}
      >
        <Box flex={1} minWidth={0}>
          <Typography
            sx={{ typography: "s1", fontWeight: 600, lineHeight: 1.2 }}
          >
            Build pipeline
          </Typography>
          <Typography
            sx={{
              typography: "s3",
              lineHeight: 1.2,
              mt: 0.25,
              color: summary.failed ? "status.danger.fg" : "text.subtitle",
              fontWeight: summary.failed ? 600 : 400,
            }}
          >
            {summary.label}
          </Typography>
        </Box>
        <Stack direction="row" alignItems="center" spacing={0.625}>
          <Box
            sx={{
              width: 7,
              height: 7,
              borderRadius: "50%",
              bgcolor: summary.failed
                ? "status.danger.solid"
                : summary.running
                  ? "status.warning.solid"
                  : "status.success.solid",
            }}
          />
          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
            {summary.failed
              ? "halted"
              : summary.running
                ? "running"
                : summary.done === summary.total
                  ? "ready"
                  : "paused"}
          </Typography>
        </Stack>
      </Stack>

      <Box sx={{ maxHeight: 480, overflowY: "auto", py: 1 }}>
        {phases.map((phase) => {
          const rows = pipeline.filter((step) => step.phase === phase.id);
          if (!rows.length) return null;
          return (
            <Box key={phase.id} sx={{ py: 1 }}>
              <Typography
                sx={{
                  px: 2,
                  mb: 0.5,
                  typography: "s3",
                  fontWeight: 600,
                  letterSpacing: 0.5,
                  color: "text.disabled",
                  textTransform: "none",
                }}
              >
                {phase.label}
              </Typography>
              <Stack>
                {rows.map((step) => (
                  <PipelineRow key={step.id} step={step} />
                ))}
              </Stack>
            </Box>
          );
        })}
      </Box>
    </Popover>
  );
}

PipelinePopover.propTypes = {
  anchor: PropTypes.any,
  onClose: PropTypes.func,
  pipeline: PropTypes.array,
  summary: PropTypes.object,
};

/*
  One pipeline row.

  Failed rows expand — a red banner underneath with the error title, detail
  and a retry button. Nothing worth reading is more than one click away.
*/
function PipelineRow({ step }) {
  const [open, setOpen] = useState(step.status === "failed");
  const failed = step.status === "failed";

  const icon = (() => {
    if (step.status === "done")
      return (
        <Iconify
          icon="solar:check-circle-bold"
          width={16}
          sx={{ color: "status.success.fg", display: "block" }}
        />
      );
    if (step.status === "failed")
      return (
        <Iconify
          icon="solar:close-circle-bold"
          width={16}
          sx={{ color: "status.danger.fg", display: "block" }}
        />
      );
    if (step.status === "running") {
      return (
        <Box
          sx={{
            width: 12,
            height: 12,
            mt: "2px",
            borderRadius: "50%",
            border: "2px solid",
            borderColor: "status.warning.solid",
            borderTopColor: "transparent",
            animation: "spin 0.8s linear infinite",
            "@keyframes spin": { to: { transform: "rotate(360deg)" } },
          }}
        />
      );
    }
    return (
      <Iconify
        icon="solar:circle-linear"
        width={16}
        sx={{ color: "text.disabled", display: "block" }}
      />
    );
  })();

  return (
    <Box>
      <Stack
        direction="row"
        alignItems="flex-start"
        spacing={1.25}
        {...clickable(failed ? () => setOpen((o) => !o) : undefined, {
          expanded: failed ? open : undefined,
        })}
        sx={{
          ...focusRing,
          px: 2,
          py: 1,
          cursor: failed ? "pointer" : "default",
          "&:hover": failed ? { bgcolor: "action.hover" } : {},
        }}
      >
        <Box sx={{ mt: "2px", flexShrink: 0 }}>{icon}</Box>
        <Box flex={1} minWidth={0}>
          <Stack direction="row" alignItems="center" spacing={0.75}>
            <Typography
              sx={{
                typography: "s2",
                fontWeight: 600,
                color:
                  step.status === "pending"
                    ? "text.subtitle"
                    : failed
                      ? "status.danger.fg"
                      : "text.primary",
              }}
            >
              {step.label}
            </Typography>
            {failed && (
              <Iconify
                icon={
                  open
                    ? "solar:alt-arrow-up-linear"
                    : "solar:alt-arrow-down-linear"
                }
                width={12}
                sx={{ color: "status.danger.fg" }}
              />
            )}
          </Stack>
          <Typography
            sx={{ typography: "s3", color: "text.subtitle", mt: 0.125 }}
          >
            {step.detail}
          </Typography>
        </Box>
      </Stack>

      {failed && open && step.failure && (
        <Box
          sx={{
            mx: 1.5,
            mb: 1,
            px: 1.75,
            py: 1.5,
            borderRadius: 1,
            border: "1px solid",
            borderColor: "status.danger.border",
            bgcolor: "status.danger.bg",
          }}
        >
          <Typography
            sx={{
              typography: "s2",
              fontWeight: 600,
              color: "status.danger.fg",
              mb: 0.5,
            }}
          >
            {step.failure.title}
          </Typography>
          <Typography
            sx={{ typography: "s3", color: "text.secondary", mb: 1.5 }}
          >
            {step.failure.detail}
          </Typography>
          <Stack direction="row" spacing={1}>
            {step.failure.retryable !== false && (
              <Button
                size="small"
                variant="contained"
                startIcon={<Iconify icon="solar:refresh-linear" width={14} />}
                color="error"
                onClick={(e) => {
                  e.stopPropagation();
                  step.failure.onRetry?.();
                }}
              >
                Retry
              </Button>
            )}
            <Button
              size="small"
              variant="outlined"
              startIcon={
                <Iconify icon="solar:document-text-linear" width={14} />
              }
              sx={{
                color: "text.primary",
                borderColor: (t) => alpha(t.palette.text.primary, 0.2),
              }}
              onClick={(e) => e.stopPropagation()}
            >
              View log
            </Button>
          </Stack>
        </Box>
      )}
    </Box>
  );
}

PipelineRow.propTypes = { step: PropTypes.object };

/* An MCP server (the world's tools) and a local runtime (where the world
   runs) aren't agents — building from either attaches none. */
const buildsAgent = (source) =>
  !!source && !["mcp", "local", "scratch"].includes(source.kind);

/* The agent a build read: the same record — and the same identity — the
   Agents page and the GitHub page create for that source, plus how the
   build reached it. */
function agentForBuild(source, env, now) {
  if (!buildsAgent(source)) return null;
  const rt = runtimeTypeFor(
    source?.modality,
    source?.kind,
    source?.runtimeTypeId,
  );
  /* Named as typed — else the GitHub agent's name, else where it lives. */
  const record = agentRecordFromSource(source, now);
  const where = (source.value || "").trim();
  return {
    ...record,
    typeId: rt?.id || env?.agentType || record.typeId,
    via:
      source.kind === "platform"
        ? `${source.provider || "Hosted"} assistant ${where}`.trim()
        : source.kind === "upload"
          ? `Uploaded — ${where || "bundle"}`
          : `Read from ${where}`,
    location: where,
    note: "Environment source",
  };
}

/* What each derivation stage reads as, for "it stops at …". */
const STAGE_LABEL = {
  understand: "Understanding the agent",
  build: "Building the environment",
  scenarios: "Writing scenarios",
};

/*
  A build that stopped part-way, under the header: canceled (restart it), or
  failed — the job's own message, and Retry when trying again can work. Out
  of credits, the retry offers the most scenarios the credits left cover.
*/
function BuildHaltStrip({ halt, onRestart }) {
  const failed = halt.kind === "failed";
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={1.25}
      sx={{
        px: 2,
        py: 1.125,
        borderBottom: "1px solid",
        borderColor: "divider",
        flexShrink: 0,
        bgcolor: "background.neutral",
      }}
    >
      <Iconify
        icon={
          failed ? "solar:danger-triangle-bold" : "solar:stop-circle-linear"
        }
        width={16}
        sx={{ color: failed ? "error.main" : "text.secondary", flexShrink: 0 }}
      />
      <Box flex={1} minWidth={0}>
        <Typography sx={{ typography: "s2", fontWeight: 600 }}>
          {failed
            ? `Build failed at ${(STAGE_LABEL[halt.stage] || "a step").toLowerCase()} — ${halt.title}`
            : `Build canceled at ${(STAGE_LABEL[halt.stage] || "a step").toLowerCase()}`}
        </Typography>
        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
          {failed
            ? halt.message
            : "What was done before it stays. Restart to carry on from there."}
        </Typography>
      </Box>
      {failed && halt.affordable > 0 && (
        <Button
          size="small"
          variant="contained"
          color="primary"
          onClick={() => onRestart(halt.affordable)}
          sx={{ flexShrink: 0 }}
        >
          {`Retry with ${halt.affordable.toLocaleString()} scenarios`}
        </Button>
      )}
      {(!failed || halt.retryable) && !(failed && halt.affordable > 0) && (
        <Button
          size="small"
          variant="outlined"
          onClick={() => onRestart()}
          startIcon={<Iconify icon="solar:refresh-linear" width={14} />}
          sx={{ color: "text.primary", borderColor: "divider", flexShrink: 0 }}
        >
          {failed ? "Retry" : "Restart build"}
        </Button>
      )}
    </Stack>
  );
}
BuildHaltStrip.propTypes = {
  halt: PropTypes.object.isRequired,
  onRestart: PropTypes.func.isRequired,
};

/* Out of credits for the scenarios still to come — said while there's time to build fewer. */
function BuildCreditsStrip({ credits, count, onUse }) {
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={1.25}
      sx={{
        px: 2,
        py: 1.125,
        borderBottom: "1px solid",
        borderColor: "divider",
        flexShrink: 0,
        bgcolor: "background.neutral",
      }}
    >
      <Iconify
        icon="solar:wallet-money-linear"
        width={16}
        sx={{ color: "warning.main", flexShrink: 0 }}
      />
      <Box flex={1} minWidth={0}>
        <Typography
          sx={{ typography: "s2", fontWeight: 600 }}
        >{`Not enough AI credits for ${count.toLocaleString()} scenarios`}</Typography>
        <Typography
          sx={{ typography: "s3", color: "text.subtitle" }}
        >{`${credits.message} The build stops when it gets there.`}</Typography>
      </Box>
      {credits.affordable > 0 && (
        <Button
          size="small"
          variant="outlined"
          onClick={() => onUse(credits.affordable)}
          sx={{ color: "text.primary", borderColor: "divider", flexShrink: 0 }}
        >
          {`Build ${credits.affordable.toLocaleString()} instead`}
        </Button>
      )}
    </Stack>
  );
}
BuildCreditsStrip.propTypes = {
  credits: PropTypes.object.isRequired,
  count: PropTypes.number.isRequired,
  onUse: PropTypes.func.isRequired,
};
