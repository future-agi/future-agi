import PropTypes from "prop-types";
import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { alpha } from "@mui/material/styles";
import {
  Box, Stack, Typography, Button, TextField, InputAdornment, MenuItem, Tooltip, IconButton, Collapse, Popover,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { Upload } from "src/components/upload";
import { formatFileSize } from "src/utils/utils";
import { paths } from "src/routes/paths";
import { pipelineStatus, pipelineSummary } from "../_mock/buildPipeline";
import { setupGaps, gapCounts } from "../_mock/setupGaps";
import { useSimStore, useEnvState } from "../store";
import { SectionCard } from "../components/primitives";
import {
  HARNESS_STAGES, REF_KINDS, DERIVATION_OUTPUTS,
  builderRun, derivedEnvironment, detectedStack, assistantIdLabel,
  sourceKindsFor, providersFor, runtimeTypeFor, runtimeTypesFor, runtimeValuesFrom, sourceOwnedKeys,
} from "../_mock/builder";
import { ADAPTERS } from "../_mock/rlContract";
import { generatedPool } from "../_mock/scenarios";
import { parseCurl, describeFill } from "../_mock/curl";
import { detectEndpoints, CONFIDENCE } from "../_mock/endpoints";
import AssistantConsole from "../assistant/AssistantConsole";
import VoiceInput from "../assistant/VoiceInput";
import DerivedPanels from "./DerivedPanels";
import AddEvalsDrawer from "../workspace/evals/AddEvalsDrawer";
import DynamicField from "../workspace/connect/DynamicField";

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
export default function BuildFromAgent() {
  const navigate = useNavigate();
  const { dispatch } = useSimStore();

  const [modality, setModality] = useState(null);
  const [kind, setKind] = useState("repo");
  const [value, setValue] = useState("");
  const [refKind, setRefKind] = useState("branch");
  const [refValue, setRefValue] = useState("");
  const [provider, setProvider] = useState("vapi");
  const [apiKey, setApiKey] = useState("");
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
  /*
    Intake: a short questionnaire between "source picked" and "builder starts".
    The chat is full-width during intake so the questionnaire card can sit
    right above the input the same way Claude's own AskUserQuestion does. Once
    the user submits, `intake` flips and the layout shrinks the chat back to
    the left to make room for the derivation panels.
  */
  const [intake, setIntake] = useState(null);

  const env = useMemo(() => (source ? derivedEnvironment(source) : null), [source]);
  /* Depth is a property of generation, so changing it regenerates the pool
     rather than just relabelling the environment. */
  const scenarios = useMemo(
    // No cap: a fixed slice hid the very thing depth changes.
    () => (env && done.includes("scenarios") ? generatedPool({ ...env, difficulty }) : []),
    [env, done, difficulty],
  );

  useEffect(() => {
    if (!env) return;
    setName(env.name || "");
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

  const play = (title, steps, nextChips, stageId) => {
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
      }, 380 * (i + 1)));
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
      credential: kind === "platform" ? apiKey.trim() || undefined : undefined,
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
    if (answers?.name) setName(answers.name);
    if (answers?.difficulty) setDifficulty(answers.difficulty);
    setIntake({ ...answers, at: new Date().toISOString() });
    setTurns([]);
    const stage = builderRun("understand", source);
    play(stage.title, stage.steps, stage.chips, "understand");
  };

  /* Leaving the screen is checked first — it used to sit behind the stage
     lookup, which matched it and returned before ever getting here. */
  const onChip = (chip) => {
    const next = HARNESS_STAGES.find((s) => s.chip && chip.startsWith(s.chip))?.id;
    if (next) return runStage(next);
    onSend(chip);
  };

  const runStage = (id) => {
    const stage = builderRun(id, source);
    play(stage.title, stage.steps, stage.chips, id);
  };

  /*
    The chat chip was the only way forward, so typing something else stranded
    you. This is the same progression as a persistent button that is always
    visible and always says what happens next.
  */
  /* Stages advance from the chat, which is where the builder offers them.
     The header carries the one thing the chat cannot do: leave. */
  const runNow = () => adopt({ ...env, name: name.trim(), difficulty }, "run");

  const onSend = (text) => {
    setTurns((prev) => [...prev, { id: `u-${Date.now()}`, role: "user", text }]);
    const reply = builderRun("ask", source, text);
    play(null, reply.steps, asideChips(reply.chips.length ? reply.chips : chips), null);
  };

  /*
    Broadcast a builder turn from a downstream panel (e.g. AgentsPanel when the
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
    dispatch({ type: "adoptEnvironment", env: confirmed, now: new Date().toISOString() });
    const rt = runtimeTypeFor(source?.modality, source?.kind, source?.runtimeTypeId);
    const patch = {};
    if (scenarios?.length) patch.scenarios = scenarios;
    if (evalIds?.length) patch.evals = evalIds;
    if (rt && source?.runtime) {
      patch.agent = { typeId: rt.id, values: source.runtime, via: "endpoint", connectedAt: new Date().toISOString() };
    }
    if (Object.keys(patch).length) {
      dispatch({ type: "patchEnvState", envId: confirmed.id, patch });
    }
  };

  const adopt = (confirmed, mode = "open") => {
    prime(confirmed);
    navigate(
      mode === "run"
        ? paths.dashboard.simulate.simulationRun(confirmed.id, `run-${Date.now().toString(36)}`)
        : paths.dashboard.simulate.environmentDetail(confirmed.id),
    );
  };

  /* Live envState for whichever env has been primed. Feeds the panels below. */
  const { envState, patch: envPatch } = useEnvState(env?.id);

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
  useEffect(() => {
    if (!env?.id) return;
    const understandDone = done.includes("understand");
    const readyToAdopt = understandDone && (evalIds?.length || 0) > 0;
    const allDone = STAGE_ORDER.every((s) => done.includes(s));
    const buildProgress = { done: done.length, total: STAGE_ORDER.length };
    const buildStatus = allDone ? "ready" : "building";

    if (readyToAdopt && adoptedRef.current !== env.id) {
      dispatch({
        type: "adoptEnvironment",
        env: {
          ...env,
          name: (name || env.name || "").trim(),
          difficulty,
          buildStatus,
          buildProgress,
        },
        now: new Date().toISOString(),
      });
      adoptedRef.current = env.id;
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
    if (scenarios?.length && (envState.scenarios?.length || 0) !== scenarios.length) {
      nextPatch.scenarios = scenarios;
    }
    if (evalIds?.length && (envState.evals?.length || 0) !== evalIds.length) {
      nextPatch.evals = evalIds;
    }
    const rt = runtimeTypeFor(source?.modality, source?.kind, source?.runtimeTypeId);
    if (rt && source?.runtime && !envState.agent) {
      nextPatch.agent = { typeId: rt.id, values: source.runtime, via: "endpoint", connectedAt: new Date().toISOString() };
    }
    if (Object.keys(nextPatch).length) {
      dispatch({ type: "patchEnvState", envId: env.id, patch: nextPatch });
    }
  }, [env?.id, done, scenarios?.length, evalIds?.length, envState.scenarios?.length, envState.evals?.length, envState.agent, source, name, difficulty, dispatch]);

  /* Back out of the source picker leaves the route; back out of a derivation
     returns to the picker, so a wrong URL is one click to fix, not a reload. */
  const goBack = () => {
    timers.current.forEach(clearTimeout);
    timers.current = [];
    if (!source) return navigate(paths.dashboard.simulate.environments);
    setSource(null);
    setTurns([]);
    setDone([]);
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
        done={done}
        scenarioCount={scenarios?.length || 0}
        name={name} setName={setName}
        editingName={editingName} setEditingName={setEditingName}
        onRun={runNow}
        canGo={canLeave}
        blockedReason={runBlockedReason}
      />

      <Box sx={{ flex: 1, minHeight: 0, overflow: source ? "hidden" : "auto", p: 2 }}>
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
            apiKey={apiKey} setApiKey={setApiKey}
            file={file} setFile={setFile}
            runtime={runtime} setRuntime={setRuntime}
            runtimeTypeId={runtimeTypeId} setRuntimeTypeId={setRuntimeTypeId}
            onStart={start}
          />
        ) : !intake ? (
          /*
            Intake: chat is full-width and the questionnaire card sits
            right above the input, Claude-style. Nothing derives yet;
            submit fires the builder and hands off to <Deriving/>.
          */
          <IntakeStage
            turns={turns} running={running}
            onSend={onSend}
            defaultName={env?.name || ""}
            defaultDifficulty={env?.difficulty || "Advanced"}
            source={source}
            onSubmit={beginBuild}
          />
        ) : (
          <Deriving
            turns={turns} running={running} chips={chips}
            onSend={onSend} onChip={onChip} done={done} source={source}
            env={env} envState={envState} patch={envPatch}
            scenarios={scenarios}
            evalIds={evalIds} onAddEvals={() => setAddingEvals(true)}
            onBuilderTurn={runBuilderTurn}
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

/* ── header ──────────────────────────────────────────────────────────────── */

function Header({
  onBack, backLabel, source, env, envState, done, running, scenarioCount,
  name, setName, editingName, setEditingName,
  onRun, canGo, blockedReason,
}) {
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
  const pipeline = pipelineStatus(done, running, "setup");
  const pipeSummary = pipelineSummary(pipeline);


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
                sx={{ width: 230, "& .MuiInputBase-input": { typography: "s1_2", fontWeight: 700, py: 0.5 } }}
              />
            ) : (
              <>
                <Typography noWrap sx={{ typography: "s1_2", fontWeight: 700, maxWidth: 230 }}>{name}</Typography>
                <Tooltip arrow title="Rename">
                  <IconButton size="small" onClick={() => setEditingName(true)}>
                    <Iconify icon="solar:pen-new-square-linear" width={14} sx={{ color: "text.subtitle" }} />
                  </IconButton>
                </Tooltip>
              </>
            )}
          </Stack>

          {/*
            Header fingerprint.

            The pipeline pill that used to live here duplicated the setup
            timeline we now show on the loading screen. What's actually
            informative in the header, once setup is done, is the shape of
            what got built — tools, scenarios, evals, personas — plus any
            gaps still waiting on the user. One at-a-glance snapshot, not a
            real-time monitor.
          */}
          <Box sx={{ flex: 1, display: { xs: "none", md: "flex" }, justifyContent: "center" }}>
            {(() => {
              const running = pipeline.find((st) => st.status === "running");
              const setupDone = pipeline.filter((st) => st.phase === "setup").every((st) => st.status === "done");
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
              if (running || !setupDone) {
                return (
                  <Stack
                    direction="row" alignItems="center" spacing={1}
                    sx={{
                      px: 1.25, py: 0.625, borderRadius: 999,
                      border: "1px solid", borderColor: "divider",
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
                    <Typography sx={{ typography: "s2", fontWeight: 600 }}>
                      {running ? running.label : "Setup being built"}
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
                const label = (() => {
                  if (blockingGaps.length === 1) {
                    const g = blockingGaps[0];
                    if (g.id === "no-evals") return "Add evaluations to run";
                    return g.title || "1 step to complete";
                  }
                  return `${blockingGaps.length} steps to complete`;
                })();
                return (
                  <Stack
                    direction="row" alignItems="center" spacing={0.75}
                    sx={{
                      px: 1.125, py: 0.5, borderRadius: 999,
                      border: "1px solid",
                      borderColor: alpha("#CA8A04", 0.4),
                      bgcolor: (t) => alpha("#CA8A04", t.palette.mode === "dark" ? 0.14 : 0.08),
                    }}
                  >
                    <Iconify icon="solar:danger-triangle-bold" width={13} sx={{ color: "#CA8A04" }} />
                    <Typography sx={{ typography: "s2", fontWeight: 700, color: "#CA8A04" }}>
                      {label}
                    </Typography>
                  </Stack>
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
                  <Iconify icon="solar:check-circle-bold" width={13} sx={{ color: "#16A34A" }} />
                  <Typography sx={{ typography: "s2", fontWeight: 700, color: "#16A34A" }}>
                    Ready to run
                  </Typography>
                </Stack>
              );
            })()}
          </Box>
        </>
      )}

      {source && (
        /*
          One path out of setup: run the simulation. The old "Go to environment"
          secondary sent the user to the workspace shell, but that surface is a
          post-run one — Runs, Optimizations, Amendments — and the environment
          gets filed under My environments the moment the first run lands, so
          there is nowhere useful to go from here that the run doesn't reach
          first.
        */
        <Tooltip arrow title={canGo ? "" : (blockedReason || "Finish the three stages on the left first")}>
          <span>
            <Button
              variant="contained" color="primary"
              disabled={!canGo}
              onClick={onRun}
              startIcon={<Iconify icon="solar:play-bold" width={14} />}
              sx={{ flexShrink: 0, typography: "s2", fontWeight: 700 }}
            >
              Run simulation
            </Button>
          </span>
        </Tooltip>
      )}
    </Stack>
  );
}

Header.propTypes = {
  onBack: PropTypes.func, backLabel: PropTypes.string,
  source: PropTypes.object, env: PropTypes.object, envState: PropTypes.object, scenarioCount: PropTypes.number, done: PropTypes.array, running: PropTypes.bool,
  name: PropTypes.string, setName: PropTypes.func,
  editingName: PropTypes.bool, setEditingName: PropTypes.func,
  onRun: PropTypes.func, canGo: PropTypes.bool, blockedReason: PropTypes.string,
};

/* ── pick a source ───────────────────────────────────────────────────────── */

function SourcePicker({ kind, setKind, value, setValue, refKind, setRefKind, refValue, setRefValue, chosen, sources, modality, onPickModality, provider, setProvider, apiKey, setApiKey, file, setFile, runtime, setRuntime, runtimeTypeId, setRuntimeTypeId, onStart }) {
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
          <Stack direction={{ xs: "column", sm: "row" }} spacing={1.5}>
            <TextField
              select size="small" label="Provider" value={provider}
              onChange={(e) => setProvider(e.target.value)}
              sx={{ minWidth: 160, "& .MuiInputBase-input": { typography: "s2", py: 0.75 } }}
            >
              {providersFor(modality).map((o) => (
                <MenuItem key={o.value} value={o.value} sx={{ typography: "s2" }}>{o.label}</MenuItem>
              ))}
            </TextField>
            <TextField
              fullWidth size="small" type="password" label="API key" value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder="sk_live_…"
              helperText="Read-only scope is enough. Held for the length of the read."
              sx={{ "& .MuiInputBase-input": { typography: "s2", fontFamily: "ui-monospace, Menlo, monospace" } }}
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
                      <Typography sx={{ typography: "s3", color: CONFIDENCE[c.confidence].color, fontWeight: 700, flexShrink: 0 }}>
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
                      const extra = c.curl ? parseCurl(c.curl) : null;
                      setRuntime((st) => ({ ...st, ...(extra || {}), endpoint: c.url }));
                      setCurlNote({ ok: true, text: extra ? `Filled ${describeFill({ ...extra, endpoint: c.url })} from ${c.from}` : `Filled endpoint from ${c.from}` });
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
                  setRuntime((st) => ({ ...st, ...parsed }));
                  setCurlNote({ ok: true, text: `Filled ${describeFill(parsed)}` });
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
                onClick={() => onPickModality(a.id)}
                sx={{
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
                  onClick={() => setKind(s.id)}
                  sx={{
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
  apiKey: PropTypes.string, setApiKey: PropTypes.func,
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
 * chevron-down + close on the right. Options render as inset rows with a
 * numeric badge (single-select) or a real checkbox (multi-select) on the
 * right. "Other" sits at the bottom with an inline text input inside a
 * matching row. Bottom bar carries Back / Skip / Next|Submit; Back only
 * shows past the first question, Submit only on the last.
 */
function AskUserQuestionCard({
  step, total, question, answer, onPick, onOtherChange, onBack, onSkip, onNext, canSubmit, isLast,
}) {
  const otherIndex = question.options.length;
  const otherActive = answer.other.trim().length > 0;

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
            color: "#B45309",
            typography: "s3", fontWeight: 700,
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {step + 1}/{total}
        </Box>
        <Typography sx={{ typography: "s2", fontWeight: 700, flex: 1, minWidth: 0 }}>
          {question.prompt}
        </Typography>
        <IconButton size="small" sx={{ color: "text.subtitle", p: 0.5 }}>
          <Iconify icon="solar:alt-arrow-down-linear" width={14} />
        </IconButton>
        <IconButton size="small" sx={{ color: "text.subtitle", p: 0.5 }}>
          <Iconify icon="mingcute:close-line" width={14} />
        </IconButton>
      </Stack>

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
      onClick={onSelect}
      sx={{
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
  evalIds, onAddEvals, onBuilderTurn,
}) {
  return (
    <Box
      sx={{
        display: "grid", gap: 2, height: "100%", minHeight: 0,
        /*
          Chat narrow, artifacts wide — the shape Lovable, Figma Make and v0
          all landed on for the same reason: the input is a column of text and
          the output is a whole surface. An even split turned the right side
          into a cramped preview of what it should be showing at full size.
          Ratio is ~1:2.6 on desktop; still stacked on mobile.
        */
        gridTemplateColumns: { xs: "1fr", lg: "minmax(360px, 400px) 1fr" },
      }}
    >
      <SectionCard sx={{ height: "100%", minHeight: 0, display: "flex", flexDirection: "column", overflow: "hidden" }}>
        <AssistantConsole turns={turns} running={running} chips={chips} onSend={onSend} onChip={onChip} />
      </SectionCard>

      <SectionCard sx={{ minHeight: 0, display: "flex", flexDirection: "column", overflow: "hidden", px: 2.5 }}>
        <PanelBoundary>
        <DerivedPanels
          env={env}
          envState={envState}
          patch={patch}
          source={source}
          done={done}
          running={running}
          scenarios={scenarios}
          evalIds={evalIds}
          onAddEvals={onAddEvals}
          onBuilderTurn={onBuilderTurn}
        />
        </PanelBoundary>
      </SectionCard>
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
import { Component as ReactComponent } from "react";                            // eslint-disable-line import/first
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
  evalIds: PropTypes.array, onAddEvals: PropTypes.func,
  onBuilderTurn: PropTypes.func,
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
        onClick={() => failed && setOpen((o) => !o)}
        sx={{
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

