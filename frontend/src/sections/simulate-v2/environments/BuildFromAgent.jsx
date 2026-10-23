import PropTypes from "prop-types";
import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { useNavigate, useLocation } from "react-router-dom";
import { alpha } from "@mui/material/styles";
import {
  Box, Stack, Typography, Button, TextField, InputAdornment, MenuItem, Tooltip, IconButton, Collapse, Popover,
  Dialog,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { useSnackbar } from "notistack";
import { buildCreditsCheck } from "../_mock/metering";
import { Upload } from "src/components/upload";
import { formatFileSize } from "src/utils/utils";
import { paths } from "src/routes/paths";
import { pipelineStatus, pipelineSummary } from "../_mock/buildPipeline";
import { subscribeScenarioSelection, getScenarioSelection } from "../_mock/scenarioSelectionBus";
import TrialsPicker from "../workspace/scenarios/TrialsPicker";
import { setupGaps, gapCounts } from "../_mock/setupGaps";
import { useSimStore, useEnvState, useConnectedAgents } from "../store";
import { SectionCard } from "../components/primitives";
import { textTone } from "../components/textTone";
import {
  HARNESS_STAGES, REF_KINDS, DERIVATION_OUTPUTS,
  builderRun, derivedEnvironment, detectedStack, assistantIdLabel,
  sourceKindsFor, providersFor, runtimeTypeFor, runtimeTypesFor, runtimeValuesFrom, sourceOwnedKeys,
} from "../_mock/builder";
import { ADAPTERS } from "../_mock/rlContract";
import { generatedPool } from "../_mock/scenarios";
import { agentPromptOf } from "../_mock/agentPrompt";
import { agentPinPatch } from "../_mock/versions";
import { mcpBuildStateOf } from "../_mock/worldTools";
import { agentRecordFromSource, envAgentKey } from "../_mock/agentRegistry";
import { MODE_ICONS, REPLICATION_MODES, replicationRecordOf } from "../_mock/replication";
import AgentPromptDrawer from "../workspace/agents/AgentPromptDrawer";
import { stampProvenance as provStamp, currentUser } from "../_mock/scenarioProvenance";
import { detectAddScenariosIntent } from "../_mock/addScenariosIntent";
import { stampNewBatch, unanswerableNote, rejectedNote } from "../_mock/addScenarios";
import { asProposal } from "../_mock/scenarioSources";
import { parseCurl, describeFill } from "../_mock/curl";
import { detectEndpoints, CONFIDENCE } from "../_mock/endpoints";
import AssistantConsole from "../assistant/AssistantConsole";
import BuilderRail from "../workspace/BuilderRail";
import { readBuilderCollapsed, writeBuilderCollapsed } from "../workspace/builderCollapse";
import VoiceInput from "../assistant/VoiceInput";
import DerivedPanels from "./DerivedPanels";
import AddEvalsDrawer from "../workspace/evals/AddEvalsDrawer";
import DynamicField from "../workspace/connect/DynamicField";
import SecretRefField from "../agents/connect/SecretRefField";
import { isSecretRef, withoutRawSecrets } from "../_mock/secretRefs";
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
    const persona = pool[(i * 7 + round) % pool.length]?.persona || base.persona;
    return { ...base, id: `${base.id}~${round + 1}`, persona };
  });
}

export default function BuildFromAgent() {
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
  /* Set on this history entry once the build is in My environments — so
     coming back to it (Back from the first run, a reload) opens that
     environment instead of building a second copy from the same source. */
  /* Read once, on arrival: the build marks its own entry while it runs, and
     that must not send it away from itself. */
  const [builtEnvId] = useState(() => location.state?.builtEnvId || null);
  const bootedPresetRef = useRef(false);

  const [modality, setModality] = useState(null);
  const [kind, setKind] = useState("repo");
  const [value, setValue] = useState("");
  const [refKind, setRefKind] = useState("branch");
  const [refValue, setRefValue] = useState("");
  const [provider, setProvider] = useState("vapi");
  /* The provider key by reference (AC-6.12) — never the value. */
  const [keyRef, setKeyRef] = useState("");
  const [file, setFile] = useState(null);
  const [runtime, setRuntime] = useState({});
  /* Name and difficulty live in the header now — an auto-generated name you
     can correct in place beats a form field asking you to invent one. */
  const [name, setName] = useState("");
  const [difficulty, setDifficulty] = useState("Advanced");
  const [editingName, setEditingName] = useState(false);
  const [evalIds, setEvalIds] = useState([]);
  const [addingEvals, setAddingEvals] = useState(false);
  const [runtimeTypeId, setRuntimeTypeId] = useState(null);
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
  const [buildId] = useState(() => `env-returns-line-${Date.now().toString(36)}`);
  const env = useMemo(() => (source ? derivedEnvironment(source, { id: buildId }) : null), [source, buildId]);
  /* Depth is a property of generation, so changing it regenerates the pool
     rather than just relabelling the environment. */
  const buildDone = done.includes("build");
  const scenariosDone = done.includes("scenarios");
  /* Everything the build writes is one batch — the environment's first —
     added by whoever ran the build, at the moment it ran. Scenarios added
     afterwards arrive as batches of their own. */
  const [builtAt] = useState(() => new Date().toISOString());
  const pool = useMemo(
    // No cap unless the connect form asked for a count; then exactly that many.
    () => (env && buildDone
      ? sizeToCount(generatedPool({ ...env, difficulty }), source?.scenarioCount)
        .map((r) => provStamp(r, { source: "derived", actor: currentUser(), at: builtAt, batchId: `batch_build_${buildId}` }))
      : []),
    [env, buildDone, difficulty, source?.scenarioCount, builtAt, buildId],
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
    return pool.slice(0, revealed).map((r, i) => (i >= checked ? { ...mark(r, i), checking: true } : mark(r, i)));
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
  const [contractFound, setContractFound] = useState({ tools: 0, rules: 0, stores: 0, handlers: 0 });
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
      timers.current.push(setTimeout(() => setContractFound((c) => ({ ...c, [kind]: Math.max(c[kind], k) })), at));
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
  const sources = sourceKindsFor(modality);
  const chosen = sources.find((s) => s.id === kind) || sources[0];

  /* Changing the kind of agent changes which sources are legitimate, so a
     selection that is no longer offered falls back rather than persisting
     invisibly. */
  const pickModality = (id) => {
    setModality(id);
    const allowed = sourceKindsFor(id);
    if (!allowed.some((x) => x.id === kind)) setKind(allowed[0].id);
    const provs = providersFor(id);
    if (provs.length && !provs.some((o) => o.value === provider)) setProvider(provs[0].value);
  };

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
      timers.current.push(setTimeout(() => {
        setTurns((prev) => prev.map((t) => (t.id === id ? { ...t, steps: [...t.steps, step] } : t)));
        if (i !== steps.length - 1) return;

        setRunning(false);
        if (stageId) setDone((d) => (d.includes(stageId) ? d : [...d, stageId]));

        // Chain to the next stage rather than offering it as a button.
        const next = stageId ? STAGE_ORDER[STAGE_ORDER.indexOf(stageId) + 1] : null;
        if (next) {
          timers.current.push(setTimeout(() => runStage(next), 500));
        } else {
          setChips(asideChips(nextChips));
        }
      }, pace * (i + 1)));
    });
  };

  /*
    The stage-advancing chips are gone with the clicking; what is left are the
    asides — ask what it found, or tell it to write more.
  */
  const asideChips = (list = []) => {
    const stageChips = HARNESS_STAGES.filter((st) => st.chip).map((st) => st.chip);
    return list.filter(
      (c) => !stageChips.some((t) => c.startsWith(t)) && !c.startsWith("use this environment"),
    );
  };

  /*
    Two-step now. `start` sets the source and drops a short builder
    greeting turn — the questionnaire renders under the chat input in
    that greeting, and no derivation fires yet. `beginBuild` runs after
    the user submits the questionnaire: applies the answers (name,
    difficulty, notes) and finally kicks off the "understand" stage.
  */
  const start = () => {
    const ref = kind === "repo"
      ? { kind: refKind, value: refValue.trim() || REF_KINDS.find((r) => r.id === refKind).placeholder }
      : null;
    const src = {
      kind,
      modality,
      value: kind === "upload" ? (file?.name || chosen.placeholder) : (value.trim() || chosen.placeholder),
      ref,
      provider: kind === "platform" ? provider : null,
      ...(kind === "platform" && isSecretRef(keyRef) && { apiKeyRef: keyRef.trim() }),
    };
    src.runtimeTypeId = runtimeTypeId;
    src.runtime = { ...runtimeValuesFrom({ ...src }), ...runtime };
    setSource(src);
    /* No greeting turn — the chat starts empty until the builder fires.
       The intake card above the composer carries the whole "before we
       build" story on its own, so a greeting message just added another
       block to look at. */
    setTurns([]);
  };

  const beginBuild = (answers) => {
    /* Apply the intake answers to the derived env before the builder
       fires: name, thoroughness (maps to difficulty + eventual scenario
       count), and a freeform focus note that would be piped into the
       generator in a real backend. */
    if (answers?.name) setName(uniqueEnvName(answers.name, envsRef.current, env?.id));
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
    if (storeState.myEnvironments?.some((e) => e.id === builtEnvId)) {
      bootedPresetRef.current = true;
      navigate(paths.dashboard.simulate.environmentDetail(builtEnvId), { replace: true });
    }
  }, [builtEnvId, hydrated]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!presetSource || bootedPresetRef.current || builtEnvId) return;
    bootedPresetRef.current = true;
    if (presetSource.modality) setModality(presetSource.modality);
    if (presetSource.kind) setKind(presetSource.kind);
    if (presetSource.provider) setProvider(presetSource.provider);
    if (presetSource.value) setValue(presetSource.value);
    if (isSecretRef(presetSource.apiKeyRef)) setKeyRef(presetSource.apiKeyRef);
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
    const next = HARNESS_STAGES.find((s) => s.chip && chip.startsWith(s.chip))?.id;
    if (next) return runStage(next);
    onSend(chip);
  };

  const runStage = (id) => {
    /* The builder narrates the count this screen will actually produce — and
       the distinct sub-goals those scenarios use, the same count the
       Environments list shows. */
    const draft = env ? sizeToCount(generatedPool({ ...env, difficulty }), source?.scenarioCount) : [];
    const draftCount = env ? draft.length : undefined;
    const subGoalCount = env ? subGoalCatalogOf(env, { scenarios: draft }).length : undefined;
    const stage = builderRun(id, { ...source, draftCount, subGoalCount });
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
    const credits = id === "scenarios" ? buildCreditsCheck(requested) : null;
    if (credits && !credits.ok) {
      setRunning(true);
      setChips([]);
      const turnId = `t-${Date.now()}`;
      setTurns((prev) => [...prev, { id: turnId, role: "builder", title: stage.title, steps: [] }]);
      stage.steps.slice(0, 1).forEach((step, i) => timers.current.push(setTimeout(() => {
        setTurns((prev) => prev.map((t) => (t.id === turnId ? { ...t, steps: [...t.steps, step] } : t)));
      }, pace * (i + 1))));
      timers.current.push(setTimeout(() => {
        setRunning(false);
        setHalt({
          kind: "failed", stage: "scenarios", stepId: "generate-scenarios",
          title: "Out of AI credits",
          message: `Stopped before writing any scenarios. ${credits.message}`,
          retryable: credits.affordable > 0, affordable: credits.affordable,
        });
        if (env?.id) dispatch({ type: "patchEnvironment", envId: env.id, patch: { buildStatus: "failed" } });
      }, pace * 2.5));
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
  const runNow = (scenarioIds, trials) => adopt({ ...env, name: name.trim(), difficulty }, "run", scenarioIds, trials);

  const onSend = (text) => {
    setTurns((prev) => [...prev, { id: `u-${Date.now()}`, role: "user", text }]);
    /* Recognise "add N scenarios" here too, while the environment is still
       being built. Mirrors the intent detect in EnvironmentWorkspace.sendChat. */
    const intent = detectAddScenariosIntent(text);
    if (intent && env?.id && envPatch) {
      const existing = new Set((envState?.scenarios || []).map((s) => s.id));
      const pool = generatedPool(env).filter((s) => !existing.has(s.id));
      const fresh = pool.slice(0, intent.count);
      /* Stamped like every other add route — tools, proof, the environment
         version it was added under — so the tool check runs here too. */
      const batch = stampNewBatch(fresh.map((r) => ({ ...r, chatAsk: text })), { env, envState, source: "chat", actor: currentUser() });
      /* Chat-written scenarios get the same review as every other source:
         proposed, in exploration, until someone approves them. */
      if (fresh.length > 0) {
        envPatch({ scenarios: [...(envState?.scenarios || []), ...batch.rows.map((r) => asProposal(r, "chat"))] });
      }
      const warn = fresh.length > 0 ? [unanswerableNote(batch), rejectedNote(batch)].filter(Boolean).join(" ") || null : null;
      setTurns((prev) => [...prev, {
        id: `a-${Date.now()}`,
        role: "assistant",
        steps: [{ kind: "note", text: fresh.length === 0
          ? "Every scenario this agent's world can write is already here — remove a few on the Scenarios tab first."
          : fresh.length < intent.count
            ? `${batch.rows.length} proposed — review and add them on the Scenarios tab. Only ${fresh.length} new ones could be written.${warn ? ` ${warn}` : ""}`
            : `${batch.rows.length} proposed — review and add them on the Scenarios tab.${warn ? ` ${warn}` : ""}` }],
      }]);
      return;
    }
    const reply = builderRun("ask", source, text);
    play(null, reply.steps, asideChips(reply.chips.length ? reply.chips : chips), null);
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
    dispatch({ type: "adoptEnvironment", env: { ...confirmed, ...(buildReplication && { replication: buildReplication }), ...(fromIssue && { returnToIssue: fromIssue, returnFrom: "built" }) }, now: new Date().toISOString() });
    /* The agent itself was attached when the environment was first created
       (see the seeding effect) — priming only carries the derived content. */
    const patch = {};
    if (scenarios?.length) patch.scenarios = scenarios;
    if (evalIds?.length) patch.evals = evalIds;
    if (Object.keys(patch).length) {
      dispatch({ type: "patchEnvState", envId: confirmed.id, patch });
    }
  };

  const adopt = (confirmed, mode = "open", scenarioIds, trials) => {
    prime(confirmed);
    let target;
    if (mode === "run") {
      target = paths.dashboard.simulate.simulationRun(confirmed.id, `run-${Date.now().toString(36)}`);
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
    navigate(target, { replace: true, ...(fromIssue && { state: { fromIssue } }) });
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
  const blockingGapsForRun = env && envState
    ? setupGaps(env, envState).filter((g) => g.status === "blocking")
    : [];
  const derivationDoneForRun = done.length >= DERIVATION_OUTPUTS.length;
  const canLeave = derivationDoneForRun && !!name.trim() && blockingGapsForRun.length === 0;
  const runBlockedReason = !derivationDoneForRun
    ? "Finish the three stages on the left first"
    : !name.trim()
      ? "Name this environment first"
      : blockingGapsForRun.length > 0
        ? (blockingGapsForRun[0].id === "no-evals"
          ? "Add at least one evaluation on the Evaluations tab"
          : blockingGapsForRun[0].title)
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
    dispatch({ type: "patchEnvState", envId: env.id, patch: { evals: preset } });
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
      ...(source?.allowedEgressDomains?.length && { egressDomains: source.allowedEgressDomains }),
    };
    /* The real agent from the first moment — identity, definition (GitHub
       link, entry file) and where it runs — so leaving mid-build never leaves
       a placeholder behind for "Use a connected agent" to duplicate. */
    const connected = source?.connectedAgent;
    if (connected && !envState.agent) {
      const { versions: connectedVersions, usedIn, latest, id, ...record } = connected;
      dispatch({
        type: "patchEnvState",
        envId: env.id,
        patch: {
          agent: { ...record, connectedAt: record.connectedAt || now },
          ...(connectedVersions?.length && { agentVersions: connectedVersions }),
          /* Built at the version picked — following production when that is it. */
          ...agentPinPatch({ agent: record, agentVersions: connectedVersions || [] }, source.agentVersion || latest || "v1"),
          envVersions: [
            { id: `${env.id}-v1`, label: "v1", createdAt: now, note: `First build, read from agent ${source.agentVersion || latest || "v1"}${source.worldSource?.label ? `, starting from ${source.worldSource.label}` : ""}${source.worldSource?.runsOn === "local" ? ", on your machine" : ""}.`, scenarios: envState.scenarios?.length || 0, changed: ["contract", "seed"], tools: v1Tools, builtFor: source.agentVersion || latest || "v1" },
          ],
          activeEnvVersion: "v1",
          runs: [],
          ...mcpPatch,
        },
      });
      return;
    }
    const agent = envState.agent
      ? { ...envState.agent, sourceKind: source?.kind || envState.agent.sourceKind || "repo" }
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
            { id: `${env.id}-v1`, label: "v1", createdAt: now, note: "First build.", scenarios: envState.scenarios?.length || 0, changed: ["contract", "seed"], tools: v1Tools },
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
            id: "agent-v1", label: "v1", note: "The version this environment was built from.",
            reach: source?.kind === "platform" ? "platform" : "endpoint", createdAt: now, tools: v1Tools,
            values: agent.values, via: agent.via, connectedAt: now,
            /* The prompt the build was read from — viewable from the build
               screen and from the test subject afterwards. */
            prompt: agentPromptOf(source, env),
          },
        ],
        activeAgentVersion: "v1",
        envVersions: [
          { id: `${env.id}-v1`, label: "v1", createdAt: now, note: "First build, read from agent v1.", scenarios: envState.scenarios?.length || 0, changed: ["contract", "seed"], tools: v1Tools, builtFor: "v1" },
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
    const readyToAdopt = understandDone && ((evalIds?.length || 0) > 0 || (envState.evals?.length || 0) > 0);
    const allDone = STAGE_ORDER.every((s) => done.includes(s));
    const buildProgress = { done: done.length, total: STAGE_ORDER.length };
    const buildStatus = allDone ? "ready" : "building";

    if (readyToAdopt && adoptedRef.current !== env.id) {
      /* The store numbers a name that's taken; show the same name here. */
      const adoptedName = uniqueEnvName((name || env.name || "").trim(), envsRef.current, env.id);
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
          /* Built from an issue's Fix: the environment links back to it. */
          ...(fromIssue && { returnToIssue: fromIssue, returnFrom: "built" }),
        },
        now: new Date().toISOString(),
      });
      adoptedRef.current = env.id;
      navigate(location.pathname, { replace: true, state: { ...(location.state || {}), builtEnvId: env.id } });
      if (allDone) readyStampedRef.current = true;
    } else if (adoptedRef.current === env.id) {
      /* Keep the card in sync as the pipeline advances — buildProgress ticks
         each stage, and the final ready stamp fires exactly once. */
      if (allDone && !readyStampedRef.current) {
        dispatch({ type: "patchEnvironment", envId: env.id, patch: { buildStatus: "ready", buildProgress } });
        readyStampedRef.current = true;
      } else if (!allDone) {
        dispatch({ type: "patchEnvironment", envId: env.id, patch: { buildStatus, buildProgress } });
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
  }, [env?.id, hydrated, done, scenarios?.length, scenarioSyncKey, evalIds?.length, envState.scenarios?.length, envState.evals?.length, envState.agent, source, name, difficulty, dispatch, buildReplication]);

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
    if (!env?.id || !hydrated || !scenariosDone || adoptedRef.current !== env.id) return;
    if (firstRunRef.current === env.id || !envState?.agent) return;
    const saved = (envState.scenarios || []).filter((r) => !r.proposed && !r.checking);
    if (!pool.length || saved.length < pool.length) return;
    const gap = blockingGapsForRun[0];
    if (gap) {
      if (firstRunWaitRef.current === env.id) return;
      firstRunWaitRef.current = env.id;
      setTurns((prev) => [...prev, {
        id: `first-run-wait-${Date.now()}`,
        role: "builder",
        title: "First run",
        steps: [{ kind: "note", text: fromIssue
          ? `Waiting on one step: ${gap.title || "a setup step"}. Then continue fixing ${fromIssue} from the banner above.`
          : `Waiting on one step: ${gap.title || "a setup step"}. Then run it once — Run simulation, top right.` }],
      }]);
      return;
    }
    /* Built — the first run is the person's to start and watch, like every
       run: a live agent's run makes real calls and costs credits, so it never
       starts (or gets recorded) on its own. Built from an issue's Fix, that
       first run is the fix's own first step — it measures these scenarios on
       today's version — so the note sends the person back to the fix. */
    firstRunRef.current = env.id;
    const agentLabel = currentAgentVersion(envState)?.label || "v1";
    play(fromIssue ? "Ready to fix" : "Ready to run", [
      { kind: "note", text: fromIssue
        ? `Built — ${saved.length} scenarios. Continue fixing ${fromIssue} from the banner above: the fix's first step runs them on agent ${agentLabel}, so there's nothing to run by hand.`
        : `Built. Run it once on agent ${agentLabel} to see where it stands today — ${saved.length} scenarios, 3 repeats each. Run simulation, top right, starts it, and you watch it as it goes.` },
    ], [], null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [env?.id, hydrated, scenariosDone, envState?.agent, envState?.scenarios, pool.length, blockingGapsForRun.length, fromIssue]);

  /* Back out of the source picker leaves the route; back out of a derivation
     returns to the picker, so a wrong URL is one click to fix, not a reload.
     Preset-source flows (arrived here from Create Environments) skip the
     modality picker entirely — so "back" for them jumps straight to
     /environments instead of dropping into a picker they never used. */
  /* Stopping and restarting. A restart runs the stage it stopped at, once
     the page has the source it should use (a smaller count, say). */
  const stageNow = STAGE_ORDER.find((sid) => !done.includes(sid)) || null;
  const FIRST_STEP_OF = { understand: "understand", build: "generate-env", scenarios: "generate-scenarios" };
  const building = !!source && !!stageNow && !halt && !(!intake && !presetSource);
  /* Finished while the question was open: there's nothing left to stop. */
  useEffect(() => { if (cancelAsk && !building) setCancelAsk(false); }, [cancelAsk, building]);
  const cancelBuild = () => {
    if (!stageNow) { setCancelAsk(false); return; }
    timers.current.forEach(clearTimeout);
    timers.current = [];
    setRunning(false);
    setCancelAsk(false);
    setHalt({ kind: "canceled", stage: stageNow, stepId: FIRST_STEP_OF[stageNow] });
    setTurns((prev) => [...prev, { id: `a-${Date.now()}`, role: "assistant", steps: [{ kind: "note", text: "Build canceled. What was done before it stays; restart it from the bar at the top." }] }]);
    if (env?.id) dispatch({ type: "patchEnvironment", envId: env.id, patch: { buildStatus: "canceled" } });
  };
  const restartBuild = (count) => {
    if (count) setSource((cur) => ({ ...cur, scenarioCount: count }));
    const from = halt?.stage || stageNow;
    setHalt(null);
    if (env?.id) dispatch({ type: "patchEnvironment", envId: env.id, patch: { buildStatus: "building" } });
    setRetryStage(from);
  };
  useEffect(() => {
    if (!retryStage || !source) return;
    const from = retryStage;
    setRetryStage(null);
    runStage(from);
  }, [retryStage, source]); // eslint-disable-line react-hooks/exhaustive-deps
  /* Said before the build gets there: this many scenarios is more than the credits left cover. */
  const creditsAhead = building && !done.includes("scenarios") ? buildCreditsCheck(Number(source?.scenarioCount) || 0) : null;

  const goBack = () => {
    timers.current.forEach(clearTimeout);
    timers.current = [];
    /* A finished build is kept as it stands — the name typed in the header
       included — before leaving; there's no separate "Open environment". */
    if (env && done.includes("build")) prime({ ...env, name: name.trim() || env.name, difficulty });
    if (!source || presetSource) return navigate(paths.dashboard.simulate.environments);
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
    <Box sx={{ display: "flex", flexDirection: "column", height: "100%", minHeight: 0 }}>
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
        agentPrompt={buildsAgent(source) && env ? agentPromptOf(source, env) : null}
        replication={buildReplication}
        name={name} setName={setName}
        editingName={editingName} setEditingName={setEditingName}
        onRun={runNow}
        canGo={canLeave}
        blockedReason={runBlockedReason}
        halt={halt}
        onCancel={building ? () => setCancelAsk(true) : null}
      />

      {halt && <BuildHaltStrip halt={halt} onRestart={restartBuild} />}
      {!halt && creditsAhead && !creditsAhead.ok && (
        <BuildCreditsStrip credits={creditsAhead} count={Number(source?.scenarioCount) || 0} onUse={(n) => setSource((cur) => ({ ...cur, scenarioCount: n }))} />
      )}
      <Dialog open={cancelAsk} onClose={() => setCancelAsk(false)} PaperProps={{ sx: { width: 440, p: 2.5, borderRadius: 2, backgroundImage: "none" } }}>
        <Typography sx={{ typography: "m3", fontWeight: 700 }}>Stop this build?</Typography>
        <Typography sx={{ typography: "s2", color: "text.secondary", mt: 1 }}>
          {`It stops at ${(STAGE_LABEL[stageNow] || "the current step").toLowerCase()}. What's done so far stays, and nothing more is built or charged. You can restart it from here.`}
        </Typography>
        <Stack direction="row" justifyContent="flex-end" spacing={1} sx={{ mt: 2.5 }}>
          <Button size="small" onClick={() => setCancelAsk(false)} sx={{ typography: "s2", fontWeight: 600, color: "text.secondary" }}>Keep building</Button>
          <Button size="small" variant="contained" color="primary" onClick={cancelBuild} sx={{ typography: "s2", fontWeight: 700 }}>Stop build</Button>
        </Stack>
      </Dialog>

      {/* Sent here by an issue's Fix: once the environment exists, the shared
          banner says what's left and leads back (Continue fixing opens Fix). */}
      {fromIssue && env?.id && storeState.myEnvironments?.some((e) => e.id === env.id && e.returnToIssue) ? (
        <IssueReturnBanner envId={env.id} />
      ) : fromIssue && (
        <Stack direction="row" alignItems="center" spacing={1.25} sx={{ px: 2, py: 1, borderBottom: "1px solid", borderColor: "divider", flexShrink: 0 }}>
          <Iconify icon="solar:bug-linear" width={15} sx={{ color: "text.secondary", flexShrink: 0 }} />
          <Typography sx={{ typography: "s2", color: "text.secondary", flex: 1, minWidth: 0 }}>
            {`Building this agent's environment for ${fromIssue}. When it's built, you can continue the fix.`}
          </Typography>
        </Stack>
      )}

      {/* The build view runs edge to edge — chat and environment split by one
          rule — so it drops the padding the picker and intake steps use. */}
      <Box sx={{ flex: 1, minHeight: 0, overflow: source ? "hidden" : "auto", p: source && (intake || presetSource) && !awaitReadAck ? 0 : 2 }}>
        {!source ? (
          <SourcePicker
            kind={kind} setKind={setKind}
            value={value} setValue={setValue}
            refKind={refKind} setRefKind={setRefKind}
            refValue={refValue} setRefValue={setRefValue}
            chosen={chosen}
            sources={sources}
            modality={modality} onPickModality={pickModality}
            provider={provider} setProvider={setProvider}
            keyRef={keyRef} setKeyRef={setKeyRef}
            file={file} setFile={setFile}
            runtime={runtime} setRuntime={setRuntime}
            runtimeTypeId={runtimeTypeId} setRuntimeTypeId={setRuntimeTypeId}
            onStart={start}
          />
        ) : (!intake && !presetSource) ? (
          /*
            Intake: chat is full-width and the questionnaire card sits
            right above the input, Claude-style. Nothing derives yet;
            submit fires the builder and hands off to <Deriving/>.
            Skipped entirely when a presetSource is present — the
            useEffect below auto-fires beginBuild in that path, and
            rendering IntakeStage for the single frame between source
            landing and intake settling produced a visible flash.
          */
          <IntakeStage
            turns={turns} running={running}
            onSend={onSend}
            defaultName={env?.name || ""}
            defaultDifficulty={env?.difficulty || "Advanced"}
            source={source}
            onSubmit={beginBuild}
          />
        ) : awaitReadAck ? (
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
            turns={turns} running={running} chips={chips}
            onSend={onSend} onChip={onChip} done={done} source={source}
            env={env} envState={envState} patch={envPatch}
            scenarios={scenarios}
            scenarioProgress={scenarioProgress}
            contractProgress={contractProgress}
            evalIds={evalIds} onAddEvals={() => setAddingEvals(true)}
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
        onAdd={(added) => setEvalIds((ids) => [...new Set([...ids, ...added.map((e) => e.id)])])}
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
  if (source?.kind === "platform") return `${source.provider || "platform"} · ${source.value || env?.id}`;
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
    { name: "routing prompt", note: `${Math.max(3, (env.tools || []).length - 1)} branches`, origin: "prompt" },
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
    title: "Are the policy values enforced in your backend, or only stated in the prompt?",
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
  onBack, backLabel, source, env, envState, patch, done, running, scenarioCount,
  scenarioProgress, agentPrompt, replication,
  name, setName, editingName, setEditingName,
  onRun, canGo, blockedReason, halt = null, onCancel = null,
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
  const scenarioSelection = useSyncExternalStore(subscribeScenarioSelection, getScenarioSelection, getScenarioSelection);
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
  const pipeline = pipelineStatus(done, running && !halt, "setup", halt?.kind === "failed" ? { stepId: halt.stepId, message: halt.message } : null);
  const pipeSummary = pipelineSummary(pipeline);
  /* True once every setup-phase stage has finished — the environment is built.
     Hoisted to the Header body so both the status chip and the Run-simulation
     button (which is hidden until the env is built) can read it. */
  const setupDone = pipeline.filter((st) => st.phase === "setup").every((st) => st.status === "done");


  return (
    <Stack
      direction="row" alignItems="center" spacing={2}
      sx={{ px: 3, py: 1.75, borderBottom: "1px solid", borderColor: "divider", flexShrink: 0 }}
    >
      <Tooltip title={backLabel} arrow>
        <Button onClick={onBack} sx={{ minWidth: 32, width: 32, height: 32, p: 0, color: "text.subtitle", flexShrink: 0 }}>
          <Iconify icon="solar:alt-arrow-left-linear" width={18} />
        </Button>
      </Tooltip>

      {!source ? (
        <Box flex={1} minWidth={0}>
          <Stack direction="row" alignItems="center" spacing={1}>
            <Typography noWrap sx={{ typography: "s1_2", fontWeight: 700 }}>Connect agent source</Typography>
            <Typography sx={{ typography: "s3", color: "text.subtitle", flexShrink: 0 }}>· Stage 1</Typography>
          </Stack>
          <Typography noWrap sx={{ typography: "s2", color: "text.subtitle" }}>
            We read the agent rather than asking you to describe it
          </Typography>
        </Box>
      ) : (
        <>
          {/* The derived name, corrected in place rather than asked for in a form. */}
          <Stack direction="row" alignItems="center" spacing={1} sx={{ flexShrink: 0, minWidth: 0 }}>
            {editingName ? (
              <TextField
                size="small" value={name} autoFocus
                onChange={(e) => setName(e.target.value)}
                onBlur={() => setEditingName(false)}
                onKeyDown={(e) => e.key === "Enter" && setEditingName(false)}
                sx={{ width: 360, "& .MuiInputBase-input": { typography: "s1_2", fontWeight: 700, py: 0.5 } }}
              />
            ) : (
              <>
                <Typography noWrap title={name} sx={{ typography: "s1_2", fontWeight: 700, maxWidth: 360 }}>{name}</Typography>
                <Tooltip arrow title="Rename">
                  <IconButton size="small" onClick={() => setEditingName(true)}>
                    <Iconify icon="solar:pen-new-square-linear" width={14} sx={{ color: "text.subtitle" }} />
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
                    const running = pipeline.find((st) => st.status === "running");
              /*
                "Ready to run" was only checking pipeline status —
                it flipped green the moment derivation finished, even
                though blocking user gaps (no evals added, missing
                sandbox secret) genuinely stop a run. Read the same
                setupGaps the tabs use so the chip tells the truth.
              */
              const blockingGaps = env && envState
                ? setupGaps(env, envState).filter((g) => g.status === "blocking")
                : [];

              if (pipeSummary.failed) {
                return (
                  <Stack
                    direction="row" alignItems="center" spacing={1}
                    sx={{
                      px: 1.25, py: 0.625, borderRadius: 999,
                      border: "1px solid", borderColor: alpha("#DC2626", 0.4),
                      bgcolor: (t) => alpha("#DC2626", t.palette.mode === "dark" ? 0.1 : 0.05),
                    }}
                  >
                    <Iconify icon="solar:danger-triangle-bold" width={13} sx={{ color: "#DC2626" }} />
                    <Typography sx={{ typography: "s2", fontWeight: 600, color: "#DC2626" }}>
                      Failed at {pipeSummary.failed.label.toLowerCase()}
                    </Typography>
                  </Stack>
                );
              }
              if (halt?.kind === "canceled") {
                return (
                  <Stack direction="row" alignItems="center" spacing={0.75} sx={{ px: 1.125, py: 0.5, borderRadius: 999, border: "1px solid", borderColor: "divider" }}>
                    <Iconify icon="solar:stop-circle-linear" width={13} sx={{ color: "text.secondary" }} />
                    <Typography sx={{ typography: "s2", fontWeight: 600, color: "text.secondary" }}>Build canceled</Typography>
                  </Stack>
                );
              }
              if (running || !setupDone) {
                return (
                  <Stack
                    direction="row" alignItems="center" spacing={0.75}
                    sx={{
                      px: 1.125, py: 0.5, borderRadius: 999,
                      border: "1px solid", borderColor: alpha("#7857FC", 0.35),
                      bgcolor: (t) => alpha("#7857FC", t.palette.mode === "dark" ? 0.14 : 0.08),
                    }}
                  >
                    <Box
                      sx={{
                        width: 6, height: 6, borderRadius: "50%", bgcolor: "#7857FC", flexShrink: 0,
                        animation: "chip-pulse 1.4s ease-in-out infinite",
                        "@keyframes chip-pulse": {
                          "0%,100%": { opacity: 0.4, transform: "scale(1)" },
                          "50%": { opacity: 1, transform: "scale(1.15)" },
                        },
                      }}
                    />
                    <Typography sx={{ typography: "s2", fontWeight: 700, color: "#7857FC", fontVariantNumeric: "tabular-nums" }}>
                      {scenarioProgress?.streaming && scenarioProgress.total
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
                const label = blockingGaps.length === 1
                  ? (g.id === "no-evals" ? "Add evaluations to run" : g.title || "1 step to complete")
                  : `${blockingGaps.length} steps to complete`;
                /* Neutral, with the one red mark. Live-test consent is given right
                   here in a dialog; anything else goes where the step is done. */
                const consent = blockingGaps.length === 1 && g.code === "gap_live_consent" && !!envState?.agent;
                const href = !consent && blockingGaps.length === 1 && g.ask?.href ? g.ask.href : null;
                return (
                  <Tooltip arrow title={blockingGaps.length === 1 ? (g.why || "") : blockingGaps.map((x) => x.title).join(" · ")}>
                    <Stack
                      direction="row" alignItems="center" spacing={0.75}
                      {...clickable(consent ? () => setConsentOpen(true) : href ? () => navigate(href) : undefined)}
                      sx={{ ...focusRing,
                        px: 1, height: 24, borderRadius: 0.75, border: "1px solid", borderColor: "divider",
                        cursor: href || consent ? "pointer" : "default", "&:hover": href || consent ? { bgcolor: "action.hover" } : undefined,
                      }}
                    >
                      <Iconify icon="solar:danger-triangle-linear" width={13} sx={{ color: "#DC2626" }} />
                      <Typography noWrap sx={{ typography: "s3", fontWeight: 600, color: "text.primary" }}>{label}</Typography>
                      {(href || consent) && <Iconify icon="solar:arrow-right-linear" width={12} sx={{ color: "text.subtitle" }} />}
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
                  direction="row" alignItems="center" spacing={0.75}
                  sx={{
                    px: 1.125, py: 0.5, borderRadius: 999,
                    border: "1px solid",
                    borderColor: (t) => alpha("#16A34A", 0.35),
                    bgcolor: (t) => alpha("#16A34A", t.palette.mode === "dark" ? 0.14 : 0.08),
                  }}
                >
                  <Box sx={{ width: 6, height: 6, borderRadius: "50%", bgcolor: "#16A34A" }} />
                  <Typography sx={{ typography: "s2", fontWeight: 700, color: "#16A34A" }}>
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
                  <Tooltip arrow title="The agent's v1 prompt — what this build read">
                    <IconButton size="small" onClick={() => setPromptOpen(true)} aria-label="Agent v1 prompt" sx={{ flexShrink: 0 }}>
                      <Iconify icon="solar:document-text-linear" width={16} sx={{ color: "text.subtitle" }} />
                    </IconButton>
                  </Tooltip>
                )}
              </>
            )}
          </Stack>
          <AgentPromptDrawer open={promptOpen} onClose={() => setPromptOpen(false)} label="v1" prompt={agentPrompt} />
          {envState?.agent && (
            <Dialog
              open={consentOpen} onClose={() => setConsentOpen(false)} maxWidth="sm" fullWidth
              PaperProps={{ sx: { borderRadius: 2, backgroundImage: "none", border: "1px solid", borderColor: "divider" } }}
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
          size="small" variant="outlined" onClick={onCancel}
          startIcon={<Iconify icon="solar:stop-circle-linear" width={15} />}
          sx={{ typography: "s2", fontWeight: 700, color: "text.primary", borderColor: "divider", flexShrink: 0 }}
        >
          Cancel build
        </Button>
      )}
      {source && setupDone && (() => {
        const totalCount = scenarioCount || envState?.scenarios?.length || 0;
        const runCount = selectionCount > 0 ? selectionCount : totalCount;
        const runLabel = selectionCount > 0 ? `Run simulation (${selectionCount})` : "Run simulation";
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
      <Stack
        direction="row" alignItems="center" spacing={0.5}
        sx={{ display: { xs: "none", md: "flex" }, px: 0.75, height: 22, borderRadius: 0.75, border: "1px solid", borderColor: "divider", flexShrink: 0, cursor: "default" }}
      >
        <Iconify icon={MODE_ICONS[replication.mode] || "solar:copy-linear"} width={11} sx={{ color: "text.secondary", flexShrink: 0 }} />
        <Typography noWrap sx={{ typography: "s3", fontWeight: 600, color: "text.secondary" }}>
          <Box component="span" sx={{ fontWeight: 500, color: "text.subtitle", mr: 0.5 }}>Runs:</Box>
          {proof || replication.label}
        </Typography>
      </Stack>
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
    <Tooltip arrow title={canGo ? "" : (blockedReason || "")}>
      <span>
        <Button
          variant="contained" color="primary"
          disabled={!canGo}
          onClick={() => onRun()}
          startIcon={<Iconify icon="solar:play-bold" width={14} />}
          sx={{ flexShrink: 0, typography: "s2", fontWeight: 700, whiteSpace: "nowrap" }}
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
  halt: PropTypes.object, onCancel: PropTypes.func,
  onBack: PropTypes.func, backLabel: PropTypes.string,
  source: PropTypes.object, env: PropTypes.object, envState: PropTypes.object, patch: PropTypes.func, scenarioCount: PropTypes.number, done: PropTypes.array, running: PropTypes.bool,
  scenarioProgress: PropTypes.object, agentPrompt: PropTypes.object, replication: PropTypes.object,
  name: PropTypes.string, setName: PropTypes.func,
  editingName: PropTypes.bool, setEditingName: PropTypes.func,
  onRun: PropTypes.func, canGo: PropTypes.bool, blockedReason: PropTypes.string,
};

/* ── pick a source ───────────────────────────────────────────────────────── */

/* Said after a curl fill that held a raw token. */
const TOKEN_LEFT_OUT = " · token left out — add its reference below";

function SourcePicker({ kind, setKind, value, setValue, refKind, setRefKind, refValue, setRefValue, chosen, sources, modality, onPickModality, provider, setProvider, keyRef, setKeyRef, file, setFile, runtime, setRuntime, runtimeTypeId, setRuntimeTypeId, onStart }) {
  /* What a run still needs, minus anything the source above already answers. */
  const [showMore, setShowMore] = useState(false);
  const [curl, setCurl] = useState("");
  const [curlNote, setCurlNote] = useState(null);
  /* Found by reading the location above — offered rather than asked for. */
  const found = detectEndpoints(kind, value);
  const runtimeChoices = kind === "platform" ? [] : runtimeTypesFor(modality);
  const runtimeType = runtimeTypeFor(modality, kind, runtimeTypeId);
  const owned = sourceOwnedKeys(kind);
  const allRuntimeFields = (runtimeType?.fields || []).filter((f) => !owned.includes(f.key));
  /*
    Only what a connection genuinely cannot work without. The rest — extra
    headers, reply and session paths, SSE — are things the test call can
    detect, and asking for seven fields to point at an endpoint is a form
    rather than a connection. They stay one click away, never gone.
  */
  const runtimeFields = allRuntimeFields.filter((f) => f.required);
  const optionalFields = allRuntimeFields.filter((f) => !f.required);

  /*
    Source-config block. Renders on the right column as soon as any
    source kind is picked, so the fields the user actually types into
    (provider + API key on hosted platforms, upload dropzone, source
    URL / endpoint, repo ref) all sit next to the runtime section
    instead of trailing below a long source-kind list on the left.
  */
  const sourceConfigSection = modality ? (
    <SectionCard
      title="Point us at the source"
      subtitle="What we need to reach it — anything else comes from reading, not typing."
    >
      <Stack spacing={1.5} sx={{ p: 2.5 }}>
        {/*
          An agent on Vapi or Retell has no repo to point at and no plain
          endpoint — its tools and prompt live in the provider's assistant
          config. So we read that instead. Still a read, never a form.
        */}
        {chosen.platform && (
          <Stack spacing={1.5}>
            <TextField
              select size="small" label="Provider" value={provider}
              onChange={(e) => setProvider(e.target.value)}
              sx={{ minWidth: 160, "& .MuiInputBase-input": { typography: "s2", py: 0.75 } }}
            >
              {providersFor(modality).map((o) => (
                <MenuItem key={o.value} value={o.value} sx={{ typography: "s2" }}>{o.label}</MenuItem>
              ))}
            </TextField>
            <SecretRefField
              label="API key"
              placeholder={`vault://acme/sim/${provider}_api_key`}
              value={keyRef} onChange={setKeyRef}
            />
          </Stack>
        )}

        {chosen.upload ? (
          file ? (
            <Stack
              direction="row" alignItems="center" spacing={1.5}
              sx={{ p: 1.5, borderRadius: 1.25, border: "1px solid", borderColor: "divider", bgcolor: "background.neutral" }}
            >
              <Iconify icon="solar:file-check-linear" width={18} sx={{ color: "#16A34A", flexShrink: 0 }} />
              <Box flex={1} minWidth={0}>
                <Typography noWrap sx={{ typography: "s2", fontWeight: 600, fontFamily: "ui-monospace, Menlo, monospace" }}>
                  {file.name}
                </Typography>
                <Typography sx={{ typography: "s3", color: "text.subtitle" }}>{formatFileSize(file.size)}</Typography>
              </Box>
              <Tooltip arrow title="Remove">
                <IconButton size="small" onClick={() => setFile(null)}>
                  <Iconify icon="solar:close-circle-linear" width={16} sx={{ color: "text.subtitle" }} />
                </IconButton>
              </Tooltip>
            </Stack>
          ) : (
            <Upload
              showDropRejection={false}
              hidePreview
              showIllustration={false}
              heading="Upload your agent"
              description="A zip of the source, or an SDK bundle (.zip, .tar.gz)"
              uploadIcon={<Iconify icon="solar:upload-square-linear" width={30} sx={{ color: "primary.main" }} />}
              actionButton={
                <Button size="small" variant="outlined" color="primary">Browse files</Button>
              }
              accept={{ "application/zip": [".zip"], "application/gzip": [".tar.gz", ".tgz"] }}
              sx={{ py: 3 }}
              onDrop={(accepted) => setFile(accepted?.[0] || null)}
            />
          )
        ) : (
          <TextField
            fullWidth size="small" value={value}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && onStart()}
            placeholder={chosen.platform ? assistantIdLabel(provider).placeholder : chosen.placeholder}
            label={chosen.platform ? assistantIdLabel(provider).label : "Location"}
            InputProps={{
              startAdornment: (
                <InputAdornment position="start">
                  <Iconify
                    icon={chosen.platform ? "solar:phone-calling-rounded-linear" : "solar:link-linear"}
                    width={15}
                    sx={{ color: "text.subtitle" }}
                  />
                </InputAdornment>
              ),
            }}
            sx={{ "& .MuiInputBase-input": { typography: "s2", fontFamily: "ui-monospace, Menlo, monospace" } }}
          />
        )}

        {/*
          A branch name is not a version. Whatever is given here resolves to
          an exact commit, and that is what gets recorded — so a result read
          back months later still says what it actually ran.
        */}
        {kind === "repo" && (
          <Stack direction={{ xs: "column", sm: "row" }} spacing={1.5} alignItems={{ sm: "center" }}>
            <TextField
              select size="small" label="Pin to" value={refKind}
              onChange={(e) => setRefKind(e.target.value)}
              sx={{ minWidth: 128, "& .MuiInputBase-input": { typography: "s2", py: 0.75 } }}
            >
              {REF_KINDS.map((r) => (
                <MenuItem key={r.id} value={r.id} sx={{ typography: "s2" }}>{r.label}</MenuItem>
              ))}
            </TextField>
            <TextField
              size="small" value={refValue}
              onChange={(e) => setRefValue(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && onStart()}
              placeholder={REF_KINDS.find((r) => r.id === refKind).placeholder}
              sx={{ minWidth: 170, "& .MuiInputBase-input": { typography: "s2", fontFamily: "ui-monospace, Menlo, monospace" } }}
            />
            <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
              {REF_KINDS.find((r) => r.id === refKind).note}
            </Typography>
          </Stack>
        )}
      </Stack>
    </SectionCard>
  ) : null;

  /*
    The runtime-connection block — "How we reach it during a run".
    Lives here as a variable so it can be rendered on the right column
    (next to the derivation-engine explainer) instead of buried at the
    bottom of the source-kind list on the left.
  */
  const runtimeSection = runtimeFields.length > 0 ? (
    <SectionCard
      title="How we reach it during a run"
      subtitle={owned.length > 0
        ? "The rest came from the source above — this is what it cannot tell us."
        : "Reading tells us what the agent can do; a run has to call it turn by turn."}
    >
      <Box sx={{ p: 2.5 }}>
        {runtimeChoices.length > 1 && (
          <TextField
            select size="small" label="Connect via" fullWidth
            value={runtimeType?.id || ""}
            onChange={(e) => { setRuntimeTypeId(e.target.value); setRuntime({}); }}
            sx={{ mb: 2.25 }}
          >
            {runtimeChoices.map((t) => (
              <MenuItem key={t.id} value={t.id} sx={{ display: "block" }}>
                <Typography sx={{ typography: "s2", fontWeight: 600 }}>{t.label}</Typography>
                <Typography sx={{ typography: "s3", color: "text.subtitle" }}>{t.blurb}</Typography>
              </MenuItem>
            ))}
          </TextField>
        )}

        {/*
          The deployed URL is usually already written down in the repo.
          Offering what we found — and where — beats asking someone to
          retype it, and the source is what lets them judge it:
          fly.toml is the deployment, .env.example is a guess.
        */}
        {found.length > 0 && allRuntimeFields.some((f) => f.key === "endpoint") && (
          <Box sx={{ mb: 2.25, border: "1px solid", borderColor: "divider", borderRadius: 1.25, overflow: "hidden" }}>
            <Stack direction="row" alignItems="center" spacing={1} sx={{ px: 1.75, py: 1.25, bgcolor: "background.neutral" }}>
              <Iconify icon="solar:magnifer-linear" width={14} sx={{ color: "primary.main", flexShrink: 0 }} />
              <Typography sx={{ typography: "s2", fontWeight: 700 }}>
                {found.length} endpoints in your source
              </Typography>
              <Typography sx={{ typography: "s3", color: "text.subtitle" }}>· pick one to fill this in</Typography>
            </Stack>
            <Stack divider={<Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />}>
              {found.map((c) => (
                <Stack key={c.id} direction="row" alignItems="center" spacing={1.5} sx={{ px: 1.75, py: 1.25 }}>
                  <Box flex={1} minWidth={0}>
                    <Stack direction="row" alignItems="center" spacing={0.875}>
                      <Typography noWrap sx={{ typography: "s2", fontFamily: "ui-monospace, Menlo, monospace" }}>
                        {c.url}
                      </Typography>
                      <Typography sx={{ typography: "s3", color: textTone(CONFIDENCE[c.confidence].color, "text.secondary"), fontWeight: 700, flexShrink: 0 }}>
                        {CONFIDENCE[c.confidence].label}
                      </Typography>
                    </Stack>
                    <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                      <Box component="span" sx={{ fontFamily: "ui-monospace, Menlo, monospace" }}>{c.from}</Box> — {c.note}
                    </Typography>
                  </Box>
                  <Button
                    size="small"
                    onClick={() => {
                      /* A curl carries the token itself — that never fills the form. */
                      const { values: extra, dropped } = c.curl ? withoutRawSecrets(parseCurl(c.curl)) : { values: null, dropped: [] };
                      setRuntime((st) => ({ ...st, ...(extra || {}), endpoint: c.url }));
                      setCurlNote({ ok: true, text: `${extra ? `Filled ${describeFill({ ...extra, endpoint: c.url })}` : "Filled endpoint"} from ${c.from}${dropped.length ? TOKEN_LEFT_OUT : ""}` });
                    }}
                    sx={{ typography: "s2", fontWeight: 700, color: "primary.main", flexShrink: 0, minWidth: 0 }}
                  >
                    Use
                  </Button>
                </Stack>
              ))}
            </Stack>
          </Box>
        )}

        {allRuntimeFields.some((f) => f.key === "endpoint") && (
          <Box sx={{ mb: 2.25 }}>
            <Stack direction="row" spacing={1} alignItems="flex-start">
              <TextField
                fullWidth size="small" multiline maxRows={4}
                label="Paste a curl instead"
                placeholder="curl https://api.yourapp.com/agent/chat -H 'Authorization: Bearer …'"
                value={curl}
                onChange={(e) => { setCurl(e.target.value); setCurlNote(null); }}
                sx={{ "& .MuiInputBase-input": { typography: "s2", fontFamily: "ui-monospace, Menlo, monospace" } }}
              />
              <Button
                variant="outlined" size="small"
                disabled={!curl.trim()}
                onClick={() => {
                  const parsed = parseCurl(curl);
                  if (!parsed) return setCurlNote({ ok: false, text: "No URL in that — paste the whole command." });
                  /* The token in a pasted curl is the key itself — left out; its reference goes in the field. */
                  const { values: safe, dropped } = withoutRawSecrets(parsed);
                  setRuntime((st) => ({ ...st, ...safe }));
                  setCurlNote({ ok: true, text: `Filled ${describeFill(safe)}${dropped.length ? TOKEN_LEFT_OUT : ""}` });
                }}
                sx={{ typography: "s2", fontWeight: 700, flexShrink: 0, mt: 0.25, color: "text.primary", borderColor: "divider" }}
              >
                Fill
              </Button>
            </Stack>
            {curlNote && (
              <Stack direction="row" spacing={0.875} alignItems="center" sx={{ mt: 0.875 }}>
                <Iconify
                  icon={curlNote.ok ? "solar:check-circle-bold" : "solar:info-circle-linear"}
                  width={14}
                  sx={{ color: curlNote.ok ? "#16A34A" : "text.subtitle", flexShrink: 0 }}
                />
                <Typography sx={{ typography: "s3", color: curlNote.ok ? "text.secondary" : "text.subtitle" }}>
                  {curlNote.text}
                </Typography>
              </Stack>
            )}
          </Box>
        )}

        <Stack spacing={2.25}>
          {runtimeFields.map((f) => (
            <DynamicField
              key={f.key}
              field={f}
              value={runtime[f.key]}
              values={runtime}
              onChange={(v) => setRuntime((st) => ({ ...st, [f.key]: v }))}
            />
          ))}
        </Stack>

        {optionalFields.length > 0 && (
          <Box sx={{ mt: 1.75 }}>
            <Button
              size="small"
              onClick={() => setShowMore((o) => !o)}
              startIcon={
                <Iconify icon={showMore ? "solar:alt-arrow-up-linear" : "solar:alt-arrow-down-linear"} width={14} />
              }
              sx={{ typography: "s2", fontWeight: 600, color: "text.secondary", px: 0.5 }}
            >
              {showMore ? "Hide" : `${optionalFields.length} more, usually detected`}
            </Button>
            <Collapse in={showMore} unmountOnExit>
              <Typography sx={{ typography: "s3", color: "text.subtitle", mb: 2, mt: 0.5 }}>
                We work these out from the first call. Set them only if we get them wrong.
              </Typography>
              <Stack spacing={2.25}>
                {optionalFields.map((f) => (
                  <DynamicField
                    key={f.key}
                    field={f}
                    value={runtime[f.key]}
                    values={runtime}
                    onChange={(v) => setRuntime((st) => ({ ...st, [f.key]: v }))}
                  />
                ))}
              </Stack>
            </Collapse>
          </Box>
        )}
      </Box>
    </SectionCard>
  ) : null;

  return (
    <Box sx={{ display: "grid", gap: 2, gridTemplateColumns: { xs: "1fr", lg: "minmax(0, 1.55fr) minmax(300px, 1fr)" } }}>
      {/* left — where the agent lives */}
      <Stack spacing={2}>
      <SectionCard
        title="What kind of agent is it?"
        subtitle="This decides how we can reach it — a hosted voice platform and a coding agent have nothing in common"
      >
        <Box sx={{ p: 2.5, display: "grid", gap: 1.25, gridTemplateColumns: "repeat(auto-fill, minmax(180px, 1fr))" }}>
          {ADAPTERS.map((a) => {
            const on = a.id === modality;
            return (
              /*
                Monochrome, matching the source rows below. Each adapter owns a
                colour for charts and chips, but using six of them as selection
                states made one question look like six unrelated ones.
              */
              <Box
                key={a.id}
                {...clickable(() => onPickModality(a.id), { role: "radio", checked: on })}
                sx={{ ...focusRing,
                  p: 1.5, borderRadius: 1.25, cursor: "pointer", border: "1px solid",
                  borderColor: on ? "primary.main" : "divider",
                  bgcolor: (t) => on ? alpha(t.palette.primary.main, t.palette.mode === "dark" ? 0.1 : 0.05) : "transparent",
                }}
              >
                <Stack direction="row" alignItems="center" spacing={1}>
                  <Iconify icon={a.icon} width={15} sx={{ color: on ? "primary.main" : "text.subtitle", flexShrink: 0 }} />
                  <Typography sx={{ typography: "s2", fontWeight: 700 }}>{a.label}</Typography>
                </Stack>
                <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 0.5 }}>{a.blurb}</Typography>
              </Box>
            );
          })}
        </Box>
      </SectionCard>

      {/* Nothing below until the question above is answered — the sources on
          offer depend entirely on the answer. */}
      {modality && (
      <SectionCard
        title="Where does your agent live?"
        subtitle="Every source is read, never typed — but they do not all carry the same things"
      >
        <Box sx={{ p: 2.5 }}>
          <Stack spacing={1.25} sx={{ mb: 2.5 }}>
            {sources.map((s) => {
              const on = kind === s.id;
              return (
                <Stack
                  key={s.id}
                  direction="row" alignItems="flex-start" spacing={1.75}
                  {...clickable(() => setKind(s.id), { role: "radio", checked: on })}
                  sx={{ ...focusRing,
                    p: 1.75, borderRadius: 1.25, cursor: "pointer", border: "1px solid",
                    /*
                      Neutral grey selection in dark mode (matches the
                      template row); purple stays in light mode where
                      it reads calmly.
                    */
                    borderColor: (t) => on
                      ? (t.palette.mode === "dark" ? alpha(t.palette.text.primary, 0.35) : t.palette.primary.main)
                      : t.palette.divider,
                    bgcolor: (t) => on
                      ? (t.palette.mode === "dark"
                        ? alpha(t.palette.text.primary, 0.06)
                        : alpha(t.palette.primary.main, 0.05))
                      : "transparent",
                    transition: "border-color .16s ease, background-color .16s ease",
                  }}
                >
                  <Box flex={1} minWidth={0}>
                    <Stack direction="row" alignItems="center" spacing={1}>
                      <Iconify
                        icon={s.icon} width={15}
                        sx={{
                          color: (t) => on
                            ? (t.palette.mode === "dark" ? t.palette.text.primary : t.palette.primary.main)
                            : t.palette.text.subtitle,
                          flexShrink: 0,
                        }}
                      />
                      <Typography sx={{ typography: "s2", fontWeight: 700 }}>{s.label}</Typography>
                    </Stack>
                    <Typography sx={{ typography: "s2", color: "text.secondary", mt: 0.25 }}>{s.blurb}</Typography>
                    <Typography
                      sx={{
                        typography: "s3",
                        color: (t) => on
                          ? (t.palette.mode === "dark" ? t.palette.text.primary : t.palette.primary.main)
                          : t.palette.text.subtitle,
                        mt: 0.5,
                      }}
                    >
                      {s.depth}
                    </Typography>
                  </Box>
                </Stack>
              );
            })}
          </Stack>

          {/* Source-config fields (Provider/API, Location, Pin to)
              and the Read-this-agent CTA all live on the right column
              now — see {sourceConfigSection} + the right-column stack
              below. Left column stops at the source-kind list. */}
        </Box>
      </SectionCard>
      )}
      </Stack>

      {/* right — source-config + runtime-connection blocks, followed
          by the "Read this agent" CTA and the "Nothing touches
          production" reassurance. Everything the user actively fills
          in on this screen now lives on one side. */}
      <Stack spacing={2}>
        {sourceConfigSection}
        {runtimeSection}

        {modality && (
          <Box>
            <Button
              variant="contained" color="primary" onClick={onStart}
              disabled={chosen.upload && !file}
              endIcon={<Iconify icon="solar:arrow-right-linear" width={16} />}
              sx={{ typography: "s2", fontWeight: 700 }}
            >
              Read this agent
            </Button>
          </Box>
        )}

        <Box
          sx={{
            p: 2, borderRadius: 1.25, border: "1px solid",
            borderColor: alpha("#16A34A", 0.3),
            bgcolor: (t) => alpha("#16A34A", t.palette.mode === "dark" ? 0.08 : 0.04),
          }}
        >
          <Stack direction="row" spacing={1.25} alignItems="flex-start">
            <Iconify icon="solar:shield-keyhole-linear" width={16} sx={{ color: "#16A34A", flexShrink: 0, mt: "1px" }} />
            <Typography sx={{ typography: "s2", color: "text.secondary" }}>
              <Box component="span" sx={{ fontWeight: 700, color: "text.primary" }}>Nothing touches production.</Box>{" "}
              We stand up a shadow copy of your agent in an isolated sandbox with seeded data and
              test credentials. Your deployed agent is never called.
            </Typography>
          </Stack>
        </Box>

      </Stack>
    </Box>
  );
}

SourcePicker.propTypes = {
  kind: PropTypes.string, setKind: PropTypes.func,
  value: PropTypes.string, setValue: PropTypes.func,
  refKind: PropTypes.string, setRefKind: PropTypes.func,
  refValue: PropTypes.string, setRefValue: PropTypes.func,
  chosen: PropTypes.object, onStart: PropTypes.func,
  sources: PropTypes.array, modality: PropTypes.string, onPickModality: PropTypes.func,
  runtime: PropTypes.object, setRuntime: PropTypes.func,
  runtimeTypeId: PropTypes.string, setRuntimeTypeId: PropTypes.func,
  provider: PropTypes.string, setProvider: PropTypes.func,
  keyRef: PropTypes.string, setKeyRef: PropTypes.func,
  file: PropTypes.object, setFile: PropTypes.func,
};

/* ── intake ─────────────────────────────────────────────────────────────── */

/**
 * The set-up questionnaire.
 *
 * A chat with the builder runs at full width; the questions the builder
 * needs answered sit right above the input area, the same shape Claude's
 * own AskUserQuestion uses in this app. When the user submits, the parent
 * fires `beginBuild(answers)` — the builder starts and the layout shrinks
 * the chat back to the left so the derivation panels can render.
 *
 * Three questions, chosen for signal-to-noise:
 *   Name        — a default is offered; the user overrides if it's off.
 *   Depth       — Focused / Balanced / Comprehensive; maps to difficulty
 *                 and (later) scenario count.
 *   Focus note  — freeform: what the caller cares about the builder
 *                 covering. Fed to the generator in a real backend.
 */
/*
  The intake questionnaire spec — one entry per question, exactly the
  shape Claude's AskUserQuestion accepts. The renderer below reads this
  and shows one card at a time with 1/3, 2/3, 3/3 progress.
*/
/*
  Intake is capped at two questions. The old three-step form asked
  for the env name too — but the header already carries an
  auto-derived name and lets you edit it inline, so re-asking here
  was ceremony. The two remaining questions are the ones that
  actually shape what the builder produces:

    · Coverage → how much to generate (depth × scenario count)
    · Focus   → what to emphasise (adversarial / edge / rules / long)

  Anything else the user can adjust after seeing the first pass.
*/
const INTAKE_QUESTIONS = [
  {
    header: "Coverage",
    prompt: "How thorough should the coverage be?",
    multiSelect: false,
    options: [
      { label: "Focused", description: "Fewer scenarios, faster feedback loop. Good for a first pass." },
      { label: "Balanced", description: "The default — enough coverage to catch most regressions." },
      { label: "Comprehensive", description: "Every derivation path the builder can produce." },
    ],
  },
  {
    header: "Focus",
    prompt: "What should we press hardest on?",
    multiSelect: true,
    options: [
      { label: "Adversarial callers", description: "Manipulation attempts, refund pressure, authority claims." },
      { label: "Edge cases in data", description: "Awkward rows the seeded world hides." },
      { label: "Rule enforcement", description: "Scenarios that only pass if the agent holds a policy line." },
      { label: "Long conversations", description: "Multi-turn calls where context matters." },
    ],
  },
];

function IntakeStage({ turns, running, onSend, defaultName, defaultDifficulty, source, onSubmit }) {
  /*
    Pixel-faithful copy of Claude's own AskUserQuestion card as it renders
    in-chat: an amber "1/3" step badge on the left of the header, the
    question next to it, a chevron + close on the right; each option is
    an inset row with the label + description on the left and either a
    numeric keyboard-shortcut badge (single-select) or a real checkbox
    (multi-select) on the right; an "Other" row sits at the bottom with
    an inline text input; a bottom bar carries Back / Skip / Next|Submit.
  */
  const [step, setStep] = useState(0);
  const [answers, setAnswers] = useState(() => INTAKE_QUESTIONS.map((q) => (q.multiSelect ? { picks: [], other: "" } : { pick: null, other: "" })));
  /*
    Freeform intake — the questionnaire covers the three narrow calls
    that shape derivation, but a user might have context that doesn't
    fit any of them: an existing SOP, a runbook, a call-review doc, a
    spoken description of edge cases they hit last week. The composer
    below the card carries that context — text, uploaded files, voice-
    dictated notes — and everything gets folded into `notes` and
    `attachments` on submit.
  */
  const [notes, setNotes] = useState("");
  const [attachments, setAttachments] = useState([]);
  const fileInputRef = useRef(null);

  const addFiles = (files) => {
    const list = Array.from(files || []).map((f) => ({
      id: `${f.name}-${f.size}-${f.lastModified}`,
      name: f.name,
      size: f.size,
      type: f.type,
    }));
    setAttachments((prev) => {
      const seen = new Set(prev.map((a) => a.id));
      return [...prev, ...list.filter((a) => !seen.has(a.id))];
    });
  };
  const removeAttachment = (id) => setAttachments((prev) => prev.filter((a) => a.id !== id));

  const q = INTAKE_QUESTIONS[step];
  const a = answers[step];
  const isLast = step === INTAKE_QUESTIONS.length - 1;

  const setAt = (i, next) => setAnswers((prev) => prev.map((v, idx) => (idx === i ? { ...v, ...next } : v)));

  const chosen = (i) => {
    if (INTAKE_QUESTIONS[i].multiSelect) {
      return answers[i].picks.length > 0 || answers[i].other.trim().length > 0;
    }
    return answers[i].pick != null || answers[i].other.trim().length > 0;
  };

  const answerFor = (i) => {
    const qi = INTAKE_QUESTIONS[i];
    const ai = answers[i];
    if (qi.multiSelect) {
      const labels = ai.picks.map((idx) => qi.options[idx].label);
      if (ai.other.trim()) labels.push(ai.other.trim());
      return labels;
    }
    if (ai.pick != null) return qi.options[ai.pick].label;
    return ai.other.trim();
  };

  const advance = () => {
    if (isLast) {
      /*
        Two-question intake: [0] Coverage → depth/difficulty, [1] Focus.
        Name is no longer collected here — the header carries the
        auto-derived name and its inline editor is the right place
        to change it. `defaultName` becomes the initial value.
      */
      const depthA = answerFor(0);
      const focusA = answerFor(1);
      const depthLabel = String(depthA).toLowerCase();
      const depthId = depthLabel === "focused" ? "focused" : depthLabel === "comprehensive" ? "comprehensive" : "balanced";
      onSubmit({
        name: defaultName,
        difficulty: difficultyForDepth(depthId),
        depth: depthId,
        focus: Array.isArray(focusA) ? focusA.join(" · ") : String(focusA || ""),
        notes: notes.trim(),
        attachments,
      });
    } else {
      setStep(step + 1);
    }
  };

  const askCard = (
    <AskUserQuestionCard
      step={step}
      total={INTAKE_QUESTIONS.length}
      question={q}
      answer={a}
      onPick={(i) => {
        if (q.multiSelect) {
          const has = a.picks.includes(i);
          setAt(step, { picks: has ? a.picks.filter((p) => p !== i) : [...a.picks, i] });
        } else {
          setAt(step, { pick: i });
        }
      }}
      onOtherChange={(v) => setAt(step, { other: v })}
      onBack={step > 0 ? () => setStep(step - 1) : null}
      onSkip={() => advance()}
      onNext={() => advance()}
      canSubmit={chosen(step)}
      isLast={isLast}
    />
  );

  return (
    <Box
      sx={{
        display: "flex", flexDirection: "column",
        height: "100%", minHeight: 0,
        alignItems: "center",
        overflowY: "auto",
      }}
    >
      {/*
        No chat during intake — a fake chat pane with an empty state
        was reading as "broken" rather than "ready". This is a proper
        hero: an icon, a title, a subtitle telling the user what the
        builder is about to do, then the question card. Once they hit
        Submit, the layout swaps to the split view (chat left, panels
        right) and the chat surface earns its space because there's
        real work happening in it.
      */}
      <Box
        sx={{
          width: "100%",
          maxWidth: 960,
          display: "flex", flexDirection: "column",
          alignItems: "center",
          py: { xs: 4, md: 6 },
          px: 2,
          gap: 3,
        }}
      >
        <Stack alignItems="center" spacing={1.5} sx={{ textAlign: "center" }}>
          <Box
            sx={{
              width: 52, height: 52, borderRadius: 1.5,
              display: "grid", placeItems: "center",
              bgcolor: (t) => alpha("#7857FC", t.palette.mode === "dark" ? 0.16 : 0.1),
              color: "#7857FC",
            }}
          >
            <Iconify icon="solar:magic-stick-3-linear" width={26} />
          </Box>
          <Typography sx={{ typography: "m2", fontWeight: 700 }}>
            Let&apos;s build your environment
          </Typography>
          <Typography sx={{ typography: "s1", color: "text.secondary", maxWidth: 520 }}>
            A few quick calls before I read from <b>{source?.value}</b>. This shapes what I derive — you can still steer everything after it starts.
          </Typography>
        </Stack>

        <Box sx={{ width: "100%" }}>
          {askCard}
        </Box>

        {/*
          Freeform composer under the question card — for the context
          that doesn't fit the three narrow questions: a spoken note,
          an uploaded SOP/runbook/dataset, extra instructions the user
          wants the builder to weigh. Everything here rides on top of
          the answers when the user hits Submit above.
        */}
        <Box sx={{ width: "100%" }}>
          <Typography sx={{ typography: "s3", fontWeight: 700, color: "text.subtitle", textTransform: "uppercase", letterSpacing: 0.4, mb: 1 }}>
            Extra context (optional)
          </Typography>

          {attachments.length > 0 && (
            <Stack direction="row" spacing={0.75} flexWrap="wrap" rowGap={0.75} sx={{ mb: 1 }}>
              {attachments.map((f) => (
                <Stack
                  key={f.id} direction="row" alignItems="center" spacing={0.75}
                  sx={{
                    px: 1, py: 0.5, borderRadius: 0.875,
                    border: "1px solid", borderColor: "divider",
                    bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.04 : 0.02),
                    maxWidth: 260,
                  }}
                >
                  <Iconify icon="solar:paperclip-linear" width={12} sx={{ color: "text.subtitle", flexShrink: 0 }} />
                  <Typography noWrap sx={{ typography: "s3", color: "text.secondary", flex: 1, minWidth: 0 }}>
                    {f.name}
                  </Typography>
                  <Typography sx={{ typography: "s3", color: "text.disabled", flexShrink: 0 }}>
                    {formatFileSize(f.size)}
                  </Typography>
                  <IconButton
                    size="small" onClick={() => removeAttachment(f.id)}
                    sx={{ p: 0.25, color: "text.subtitle" }}
                  >
                    <Iconify icon="mingcute:close-line" width={11} />
                  </IconButton>
                </Stack>
              ))}
            </Stack>
          )}

          <Stack
            direction="row" alignItems="flex-end" spacing={1}
            sx={{
              p: 1.5, borderRadius: 2, border: "1.5px solid",
              borderColor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.16 : 0.14),
              bgcolor: "background.paper",
              transition: "border-color 0.15s ease",
              "&:focus-within": {
                borderColor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.32 : 0.28),
              },
            }}
          >
            <TextField
              fullWidth multiline maxRows={6} variant="standard"
              placeholder="Anything else — paste a snippet, dictate a note, or attach an SOP…"
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              InputProps={{ disableUnderline: true, sx: { typography: "s2", lineHeight: 1.6, px: 1, py: 0.5 } }}
            />
            <IconButton
              size="small"
              onClick={() => fileInputRef.current?.click()}
              title="Attach a file"
              sx={{ color: "text.subtitle" }}
            >
              <Iconify icon="solar:paperclip-linear" width={16} />
            </IconButton>
            <VoiceInput
              onTranscript={(text) => setNotes((prev) => (prev ? `${prev} ${text}` : text))}
            />
          </Stack>

          <input
            ref={fileInputRef}
            type="file" multiple hidden
            onChange={(e) => { addFiles(e.target.files); e.target.value = ""; }}
          />
        </Box>
      </Box>
    </Box>
  );
}

/**
 * Claude's AskUserQuestion card, faithful copy of the shell + row shape.
 *
 * Header: amber step pill on the left (`1/3`), the question next to it,
 * a chevron on the right that folds the card to its header. Options
 * render as inset rows with a numeric badge (single-select) or a real checkbox (multi-select) on the
 * right. "Other" sits at the bottom with an inline text input inside a
 * matching row. Bottom bar carries Back / Skip / Next|Submit; Back only
 * shows past the first question, Submit only on the last.
 */
function AskUserQuestionCard({
  step, total, question, answer, onPick, onOtherChange, onBack, onSkip, onNext, canSubmit, isLast,
}) {
  const otherIndex = question.options.length;
  const otherActive = answer.other.trim().length > 0;
  /* Folds the card to its header — the question stays, the options and the
     bar step aside. There is no close: the intake has no other way forward. */
  const [collapsed, setCollapsed] = useState(false);

  const rowIsSelected = (i) => {
    if (i === otherIndex) return otherActive;
    if (question.multiSelect) return answer.picks.includes(i);
    return answer.pick === i;
  };

  return (
    <Box
      sx={{
        borderRadius: 2,
        border: "1px solid",
        borderColor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.1 : 0.08),
        bgcolor: "background.paper",
        overflow: "hidden",
      }}
    >
      {/* ── header ── */}
      <Stack direction="row" alignItems="center" spacing={1} sx={{ px: 1.75, py: 1.25 }}>
        <Box
          sx={{
            px: 0.75, py: 0.125, borderRadius: 999, flexShrink: 0,
            bgcolor: (t) => alpha("#B45309", t.palette.mode === "dark" ? 0.28 : 0.16),
            color: "text.secondary",
            typography: "s3", fontWeight: 700,
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {step + 1}/{total}
        </Box>
        <Typography sx={{ typography: "s2", fontWeight: 700, flex: 1, minWidth: 0 }}>
          {question.prompt}
        </Typography>
        <IconButton
          size="small"
          onClick={() => setCollapsed((c) => !c)}
          aria-label={collapsed ? "Expand question" : "Collapse question"}
          aria-expanded={!collapsed}
          sx={{ color: "text.subtitle", p: 0.5 }}
        >
          <Iconify icon={collapsed ? "solar:alt-arrow-right-linear" : "solar:alt-arrow-down-linear"} width={14} />
        </IconButton>
      </Stack>

      {!collapsed && (
      <>
      {/* ── options ── */}
      <Stack spacing={0.5} sx={{ px: 1.25, pb: 0.75 }}>
        {question.options.map((opt, i) => (
          <AskOptionRow
            key={opt.label}
            label={opt.label}
            description={opt.description}
            selected={rowIsSelected(i)}
            multi={question.multiSelect}
            index={i + 1}
            onSelect={() => onPick(i)}
          />
        ))}

        {/* Other + inline input, always at the bottom */}
        <AskOptionRow
          label="Other"
          selected={otherActive}
          multi={question.multiSelect}
          index={otherIndex + 1}
          onSelect={() => { /* focused when the user types */ }}
          bottomChildren={(
            <TextField
              size="small" fullWidth variant="outlined"
              value={answer.other}
              onChange={(e) => onOtherChange(e.target.value)}
              placeholder="Type your own answer here"
              InputProps={{
                sx: {
                  typography: "s2",
                  bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.06 : 0.03),
                  "& fieldset": { border: "none" },
                },
              }}
              sx={{ mt: 0.75 }}
            />
          )}
        />
      </Stack>

      {/* ── bottom bar ── */}
      <Stack direction="row" alignItems="center" sx={{ px: 1.5, py: 1.25 }}>
        {onBack ? (
          <Button
            size="small" onClick={onBack}
            sx={{
              typography: "s2", fontWeight: 600,
              color: "text.primary",
              bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.08 : 0.05),
              px: 1.5, borderRadius: 1,
              "&:hover": {
                bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.12 : 0.08),
              },
            }}
          >
            Back
          </Button>
        ) : <Box />}
        <Box flex={1} />
        <Stack direction="row" spacing={1}>
          <Button
            size="small" onClick={onSkip}
            sx={{
              typography: "s2", fontWeight: 600,
              color: "text.primary",
              bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.08 : 0.05),
              px: 1.5, borderRadius: 1,
              "&:hover": {
                bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.12 : 0.08),
              },
            }}
          >
            Skip
          </Button>
          <Button
            size="small" onClick={onNext}
            disabled={!canSubmit}
            sx={{
              typography: "s2", fontWeight: 600,
              color: "text.primary",
              bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.16 : 0.09),
              px: 1.5, borderRadius: 1,
              "&:hover": {
                bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.22 : 0.14),
              },
              "&.Mui-disabled": {
                color: "text.disabled",
                bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.06 : 0.04),
              },
            }}
          >
            {isLast ? "Submit" : "Next"}
          </Button>
        </Stack>
      </Stack>
      </>
      )}
    </Box>
  );
}

AskUserQuestionCard.propTypes = {
  step: PropTypes.number, total: PropTypes.number,
  question: PropTypes.object, answer: PropTypes.object,
  onPick: PropTypes.func, onOtherChange: PropTypes.func,
  onBack: PropTypes.func, onSkip: PropTypes.func, onNext: PropTypes.func,
  canSubmit: PropTypes.bool, isLast: PropTypes.bool,
};

/**
 * One row inside an AskUserQuestion. Label + description on the left,
 * either a numeric shortcut badge (single-select) or a real checkbox
 * (multi-select) on the right. Optional bottomChildren for the "Other"
 * row's inline text input.
 */
function AskOptionRow({ label, description, selected, multi, index, onSelect, bottomChildren }) {
  return (
    <Box
      {...clickable(onSelect, { role: multi ? "checkbox" : "radio", checked: selected })}
      sx={{ ...focusRing,
        p: 1.25, borderRadius: 1.25, cursor: "pointer",
        bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.05 : 0.03),
        "&:hover": {
          bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.08 : 0.05),
        },
      }}
    >
      <Stack direction="row" alignItems="flex-start" spacing={1.25}>
        <Box flex={1} minWidth={0}>
          <Typography sx={{ typography: "s2", fontWeight: 600, color: "text.primary" }}>
            {label}
          </Typography>
          {description && (
            <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 0.125 }}>
              {description}
            </Typography>
          )}
        </Box>

        {/* Right-side indicator: number badge for single-select, checkbox for multi */}
        {multi ? (
          <Box
            sx={{
              width: 18, height: 18, borderRadius: 0.5, flexShrink: 0, mt: "1px",
              display: "grid", placeItems: "center",
              border: "1.5px solid",
              borderColor: selected ? "#7857FC" : (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.22 : 0.2),
              bgcolor: selected ? "#7857FC" : "transparent",
              transition: "background-color .12s ease, border-color .12s ease",
            }}
          >
            {selected && (
              /*
                Inline SVG — Iconify fetches its glyphs async, so on the
                first paint the checkbox was reading as an empty purple
                square. Drawing the polyline ourselves guarantees the
                tick appears the moment the box turns purple.
              */
              <Box
                component="svg"
                viewBox="0 0 24 24" fill="none" stroke="#fff"
                strokeWidth={3.25} strokeLinecap="round" strokeLinejoin="round"
                sx={{ width: 11, height: 11, display: "block" }}
              >
                <polyline points="5,12.5 10,17.5 19,7" />
              </Box>
            )}
          </Box>
        ) : (
          <Box
            sx={{
              minWidth: 22, height: 20, px: 0.75, borderRadius: 0.75, flexShrink: 0, mt: "1px",
              display: "grid", placeItems: "center",
              border: "1px solid",
              borderColor: selected ? "#7857FC" : (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.16 : 0.12),
              bgcolor: selected ? "#7857FC" : "transparent",
              color: selected ? "#fff" : "text.subtitle",
              typography: "s3", fontWeight: 700, fontVariantNumeric: "tabular-nums",
            }}
          >
            {index}
          </Box>
        )}
      </Stack>

      {bottomChildren}
    </Box>
  );
}

AskOptionRow.propTypes = {
  label: PropTypes.string, description: PropTypes.string,
  selected: PropTypes.bool, multi: PropTypes.bool, index: PropTypes.number,
  onSelect: PropTypes.func, bottomChildren: PropTypes.node,
};

IntakeStage.propTypes = {
  turns: PropTypes.array,
  running: PropTypes.bool,
  onSend: PropTypes.func,
  defaultName: PropTypes.string,
  defaultDifficulty: PropTypes.string,
  source: PropTypes.object,
  onSubmit: PropTypes.func,
};

const DEPTH_OPTIONS = [
  { id: "focused", label: "Focused", blurb: "Fewer scenarios, faster feedback loop. Good for a first pass." },
  { id: "balanced", label: "Balanced", blurb: "The default — enough coverage to catch most regressions." },
  { id: "comprehensive", label: "Comprehensive", blurb: "Every derivation path the builder can produce." },
];

const depthForDifficulty = (d) => (d === "Basic" ? "focused" : d === "Extreme" ? "comprehensive" : "balanced");
const difficultyForDepth = (d) => (d === "focused" ? "Basic" : d === "comprehensive" ? "Extreme" : "Advanced");

/* ── the engine working ──────────────────────────────────────────────────── */

function Deriving({
  turns, running, chips, onSend, onChip, done, source, env, envState, patch, scenarios,
  scenarioProgress, contractProgress, evalIds, onAddEvals, onBuilderTurn, onRun,
}) {
  /* The builder folds to a rail here too — the same choice as the
     environment page, remembered across both. */
  const [collapsed, setCollapsedRaw] = useState(readBuilderCollapsed);
  const [unseen, setUnseen] = useState(0);
  const setCollapsed = (v) => { setCollapsedRaw(v); if (!v) setUnseen(0); writeBuilderCollapsed(v); };
  const seenTurns = useRef(turns.length);
  useEffect(() => {
    const added = turns.length - seenTurns.current;
    seenTurns.current = turns.length;
    if (collapsed && added > 0 && turns[turns.length - 1]?.role === "assistant") setUnseen((n) => n + 1);
  }, [turns, collapsed]);
  return (
    <Box
      sx={{
        display: "grid", height: "100%", minHeight: 0,
        /*
          Chat narrow, artifacts wide — the shape Lovable, Figma Make and v0
          all landed on for the same reason: the input is a column of text and
          the output is a whole surface. An even split turned the right side
          into a cramped preview of what it should be showing at full size.
          Ratio is ~1:2.6 on desktop; still stacked on mobile.
        */
        gridTemplateColumns: { xs: "1fr", lg: collapsed ? "48px 1fr" : "minmax(360px, 400px) 1fr" },
      }}
    >
      {collapsed && <BuilderRail running={running} unseen={unseen} onExpand={() => setCollapsed(false)} />}
      {/* One surface, split by a single rule — no card around either side. */}
      <Box
        sx={{
          height: "100%", minHeight: 0, display: "flex", flexDirection: "column", overflow: "hidden",
          ...(collapsed && { display: "none" }),
          /* Widths and colour set separately: a responsive "1px solid"
             shorthand resets the colour to the text colour, which drew this
             rule bright white instead of the divider grey. */
          borderStyle: "solid", borderWidth: 0, borderColor: "divider",
          borderRightWidth: { xs: 0, lg: 1 }, borderBottomWidth: { xs: 1, lg: 0 },
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

      <Box sx={{ minHeight: 0, display: "flex", flexDirection: "column", overflow: "hidden", px: 2 }}>
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
  constructor(props) { super(props); this.state = { err: null }; }
  static getDerivedStateFromError(err) { return { err }; }
  componentDidCatch(err, info) {
    // eslint-disable-next-line no-console
    console.error("[PanelBoundary] caught:", err, info?.componentStack);
  }
  render() {
    if (this.state.err) {
      return (
        <Box sx={{ p: 3, m: 2, border: "1px solid", borderColor: alpha("#DC2626", 0.4), borderRadius: 1.5,
          bgcolor: (t) => alpha("#DC2626", t.palette.mode === "dark" ? 0.08 : 0.04) }}>
          <Typography sx={{ typography: "s1", fontWeight: 700, color: "#DC2626", mb: 1 }}>
            The right-side panel crashed
          </Typography>
          <Typography sx={{ typography: "s2", color: "text.secondary", mb: 1.5 }}>
            The chat on the left still works. Copy the message below and paste it back to me.
          </Typography>
          <Typography component="pre" sx={{
            typography: "s3", fontFamily: "ui-monospace, Menlo, monospace",
            p: 1.5, borderRadius: 1, bgcolor: "background.neutral",
            whiteSpace: "pre-wrap", wordBreak: "break-word", color: "text.primary",
          }}>
            {String(this.state.err?.message || this.state.err)}
            {"\n\n"}
            {String(this.state.err?.stack || "").split("\n").slice(0, 5).join("\n")}
          </Typography>
          <Button
            variant="text" size="small"
            onClick={() => this.setState({ err: null })}
            sx={{ mt: 1, typography: "s3", fontWeight: 700 }}
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
  turns: PropTypes.array, running: PropTypes.bool, chips: PropTypes.array,
  onSend: PropTypes.func, onChip: PropTypes.func, done: PropTypes.array,
  source: PropTypes.object, env: PropTypes.object,
  envState: PropTypes.object, patch: PropTypes.func,
  scenarios: PropTypes.array,
  scenarioProgress: PropTypes.object,
  contractProgress: PropTypes.object,
  evalIds: PropTypes.array, onAddEvals: PropTypes.func,
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
          mt: 1, width: 420, borderRadius: 1.5, backgroundImage: "none",
          border: "1px solid", borderColor: "divider",
          boxShadow: (t) => (t.palette.mode === "dark"
            ? "0 12px 40px rgba(0,0,0,0.4)"
            : "0 12px 40px rgba(16,24,40,0.12)"),
        },
      }}
    >
      <Stack
        direction="row" alignItems="center" spacing={1}
        sx={{ px: 2, py: 1.5, borderBottom: "1px solid", borderColor: "divider" }}
      >
        <Box flex={1} minWidth={0}>
          <Typography sx={{ typography: "s1", fontWeight: 700, lineHeight: 1.2 }}>Build pipeline</Typography>
          <Typography
            sx={{
              typography: "s3", lineHeight: 1.2, mt: 0.25,
              color: summary.failed ? "#DC2626" : "text.subtitle",
              fontWeight: summary.failed ? 600 : 400,
            }}
          >
            {summary.label}
          </Typography>
        </Box>
        <Stack direction="row" alignItems="center" spacing={0.625}>
          <Box
            sx={{
              width: 7, height: 7, borderRadius: "50%",
              bgcolor: summary.failed ? "#DC2626" : summary.running ? "#CA8A04" : "#16A34A",
            }}
          />
          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
            {summary.failed ? "halted" : summary.running ? "running" : summary.done === summary.total ? "ready" : "paused"}
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
                  px: 2, mb: 0.5, typography: "s3", fontWeight: 700, letterSpacing: 0.5,
                  color: "text.disabled", textTransform: "uppercase",
                }}
              >
                {phase.label}
              </Typography>
              <Stack>
                {rows.map((step) => <PipelineRow key={step.id} step={step} />)}
              </Stack>
            </Box>
          );
        })}
      </Box>
    </Popover>
  );
}

PipelinePopover.propTypes = {
  anchor: PropTypes.any, onClose: PropTypes.func,
  pipeline: PropTypes.array, summary: PropTypes.object,
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
    if (step.status === "done") return <Iconify icon="solar:check-circle-bold" width={16} sx={{ color: "#16A34A", display: "block" }} />;
    if (step.status === "failed") return <Iconify icon="solar:close-circle-bold" width={16} sx={{ color: "#DC2626", display: "block" }} />;
    if (step.status === "running") {
      return (
        <Box
          sx={{
            width: 12, height: 12, mt: "2px", borderRadius: "50%",
            border: "2px solid", borderColor: "#CA8A04", borderTopColor: "transparent",
            animation: "spin 0.8s linear infinite",
            "@keyframes spin": { to: { transform: "rotate(360deg)" } },
          }}
        />
      );
    }
    return <Iconify icon="solar:circle-linear" width={16} sx={{ color: "text.disabled", display: "block" }} />;
  })();

  return (
    <Box>
      <Stack
        direction="row" alignItems="flex-start" spacing={1.25}
        {...clickable(failed ? () => setOpen((o) => !o) : undefined, { expanded: failed ? open : undefined })}
        sx={{ ...focusRing,
          px: 2, py: 1,
          cursor: failed ? "pointer" : "default",
          "&:hover": failed ? { bgcolor: "action.hover" } : {},
        }}
      >
        <Box sx={{ mt: "2px", flexShrink: 0 }}>{icon}</Box>
        <Box flex={1} minWidth={0}>
          <Stack direction="row" alignItems="center" spacing={0.75}>
            <Typography
              sx={{
                typography: "s2", fontWeight: 700,
                color: step.status === "pending" ? "text.subtitle" : failed ? "#DC2626" : "text.primary",
              }}
            >
              {step.label}
            </Typography>
            {failed && (
              <Iconify
                icon={open ? "eva:arrow-ios-upward-fill" : "eva:arrow-ios-downward-fill"}
                width={12} sx={{ color: "#DC2626" }}
              />
            )}
          </Stack>
          <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 0.125 }}>
            {step.detail}
          </Typography>
        </Box>
      </Stack>

      {failed && open && step.failure && (
        <Box
          sx={{
            mx: 1.5, mb: 1, px: 1.75, py: 1.5, borderRadius: 1,
            border: "1px solid", borderColor: (t) => alpha("#DC2626", 0.35),
            bgcolor: (t) => alpha("#DC2626", t.palette.mode === "dark" ? 0.1 : 0.05),
          }}
        >
          <Typography sx={{ typography: "s2", fontWeight: 700, color: "#DC2626", mb: 0.5 }}>
            {step.failure.title}
          </Typography>
          <Typography sx={{ typography: "s3", color: "text.secondary", mb: 1.5 }}>
            {step.failure.detail}
          </Typography>
          <Stack direction="row" spacing={1}>
            {step.failure.retryable !== false && (
              <Button
                size="small" variant="contained"
                startIcon={<Iconify icon="solar:refresh-linear" width={13} />}
                sx={{ typography: "s3", fontWeight: 700, bgcolor: "#DC2626", "&:hover": { bgcolor: "#B91C1C" } }}
                onClick={(e) => { e.stopPropagation(); step.failure.onRetry?.(); }}
              >
                Retry
              </Button>
            )}
            <Button
              size="small" variant="outlined"
              startIcon={<Iconify icon="solar:document-text-linear" width={13} />}
              sx={{
                typography: "s3", fontWeight: 700, color: "text.primary",
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
const buildsAgent = (source) => !!source && !["mcp", "local", "scratch"].includes(source.kind);

/* The agent a build read: the same record — and the same identity — the
   Agents page and the GitHub page create for that source, plus how the
   build reached it. */
function agentForBuild(source, env, now) {
  if (!buildsAgent(source)) return null;
  const rt = runtimeTypeFor(source?.modality, source?.kind, source?.runtimeTypeId);
  /* Named as typed — else the GitHub agent's name, else where it lives. */
  const record = agentRecordFromSource(source, now);
  const where = (source.value || "").trim();
  return {
    ...record,
    typeId: rt?.id || env?.agentType || record.typeId,
    via: source.kind === "platform" ? `${source.provider || "Hosted"} assistant ${where}`.trim()
      : source.kind === "upload" ? `Uploaded — ${where || "bundle"}` : `Read from ${where}`,
    location: where,
    note: "Environment source",
  };
}

/* What each derivation stage reads as, for "it stops at …". */
const STAGE_LABEL = { understand: "Understanding the agent", build: "Building the environment", scenarios: "Writing scenarios" };

/*
  A build that stopped part-way, under the header: canceled (restart it), or
  failed — the job's own message, and Retry when trying again can work. Out
  of credits, the retry offers the most scenarios the credits left cover.
*/
function BuildHaltStrip({ halt, onRestart }) {
  const failed = halt.kind === "failed";
  return (
    <Stack direction="row" alignItems="center" spacing={1.25} sx={{ px: 2, py: 1.125, borderBottom: "1px solid", borderColor: "divider", flexShrink: 0, bgcolor: "background.neutral" }}>
      <Iconify icon={failed ? "solar:danger-triangle-bold" : "solar:stop-circle-linear"} width={16} sx={{ color: failed ? "error.main" : "text.secondary", flexShrink: 0 }} />
      <Box flex={1} minWidth={0}>
        <Typography sx={{ typography: "s2", fontWeight: 700 }}>
          {failed ? `Build failed at ${(STAGE_LABEL[halt.stage] || "a step").toLowerCase()} — ${halt.title}` : `Build canceled at ${(STAGE_LABEL[halt.stage] || "a step").toLowerCase()}`}
        </Typography>
        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
          {failed ? halt.message : "What was done before it stays. Restart to carry on from there."}
        </Typography>
      </Box>
      {failed && halt.affordable > 0 && (
        <Button size="small" variant="contained" color="primary" onClick={() => onRestart(halt.affordable)} sx={{ typography: "s2", fontWeight: 700, flexShrink: 0 }}>
          {`Retry with ${halt.affordable.toLocaleString()} scenarios`}
        </Button>
      )}
      {(!failed || halt.retryable) && !(failed && halt.affordable > 0) && (
        <Button size="small" variant="outlined" onClick={() => onRestart()} startIcon={<Iconify icon="solar:refresh-linear" width={14} />} sx={{ typography: "s2", fontWeight: 700, color: "text.primary", borderColor: "divider", flexShrink: 0 }}>
          {failed ? "Retry" : "Restart build"}
        </Button>
      )}
    </Stack>
  );
}
BuildHaltStrip.propTypes = { halt: PropTypes.object.isRequired, onRestart: PropTypes.func.isRequired };

/* Out of credits for the scenarios still to come — said while there's time to build fewer. */
function BuildCreditsStrip({ credits, count, onUse }) {
  return (
    <Stack direction="row" alignItems="center" spacing={1.25} sx={{ px: 2, py: 1.125, borderBottom: "1px solid", borderColor: "divider", flexShrink: 0, bgcolor: "background.neutral" }}>
      <Iconify icon="solar:wallet-money-linear" width={16} sx={{ color: "warning.main", flexShrink: 0 }} />
      <Box flex={1} minWidth={0}>
        <Typography sx={{ typography: "s2", fontWeight: 700 }}>{`Not enough AI credits for ${count.toLocaleString()} scenarios`}</Typography>
        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>{`${credits.message} The build stops when it gets there.`}</Typography>
      </Box>
      {credits.affordable > 0 && (
        <Button size="small" variant="outlined" onClick={() => onUse(credits.affordable)} sx={{ typography: "s2", fontWeight: 700, color: "text.primary", borderColor: "divider", flexShrink: 0 }}>
          {`Build ${credits.affordable.toLocaleString()} instead`}
        </Button>
      )}
    </Stack>
  );
}
BuildCreditsStrip.propTypes = { credits: PropTypes.object.isRequired, count: PropTypes.number.isRequired, onUse: PropTypes.func.isRequired };
