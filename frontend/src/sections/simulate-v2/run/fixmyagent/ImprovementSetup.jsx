import PropTypes from "prop-types";
import { useEffect, useMemo, useState } from "react";
import { enqueueSnackbar } from "notistack";
import { useRoleGates } from "../../_mock/previewRole";
import {
  Checkbox,
  Box,
  Button,
  ButtonBase,
  Dialog,
  IconButton,
  ListSubheader,
  MenuItem,
  Select,
  Stack,
  Switch,
  TextField,
  Typography,
  Tooltip,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { clickable, focusRing } from "../../components/clickable";
import AgentPicker from "../../improvements/AgentPicker";
import {
  agentPickerChoice,
  agentPickerHelper,
} from "../../improvements/agentPickerChoices";
import { useSimStore, useConnectedAgents, useEnvState } from "../../store";
import {
  agentDefinitionOf,
  connectedAgentsFrom,
  envAgentKey,
} from "../../_mock/agentRegistry";
import { getEval } from "../../_mock/evals";
import {
  evalReliabilityLine,
  evalReliabilityOf,
} from "../../_mock/evalReliability";
import AddEvalsDrawer from "../../workspace/evals/AddEvalsDrawer";
import { useAppliedEvals } from "../../workspace/evals/useAppliedEvals";
import {
  agentVersions,
  currentAgentVersion,
  productionAgentVersion,
} from "../../_mock/versions";
import {
  allowFlagOf,
  improvementOutcome,
  limitRows,
  optimizationId,
  OPTIMIZER_MODELS,
  PER_CONVERSATION,
} from "../../_mock/optimizationRuns";
import { fixDepthOf } from "../../_mock/rsiModel";
import { goalWords, improvementRunName } from "../../_mock/optimizer";
import {
  baseRunOf,
  budgetsWithin,
  envMustPassOf,
  envStateOnVersion,
  FIX_DEFAULTS,
  fixReviewSentence,
  heldBackOf,
  improvementEstimate,
  outcomeMetricsOf,
  planSearch,
  runBaseOf,
  stopEarlyNote,
  todayConversations,
} from "../../_mock/improvementStart";
import { markedNotCountedLine } from "../../_mock/mustPass";
import { envVersionNameOf } from "../../_mock/codeRefs";
import { measureTodayRun, measuredSummaryOf } from "../../_mock/measureToday";
import { agentStatusOf } from "../../_mock/agentLifecycle";
import { runIdentityOf } from "../../_mock/runIdentity";
import { DEFAULT_METHOD } from "./improvementView";
import ImprovementConstraints, {
  ConstraintPicker,
} from "../../improvements/ImprovementConstraints";
import { changedKeysOf, withEdits } from "../../improvements/constraintEdits";
import CostPartsTable from "../../improvements/CostPartsTable";
import { limitsOf } from "../../agents/agentLimits";
import { rereadFor, textSearchDefault } from "../../_mock/improvementPolicy";
import { recordOutsideChange } from "../../_mock/nudgeEffects";
import { creditsLabel } from "../../_mock/metering";
import CopyCommandButton from "../../components/CopyCommandButton";
import { creditsForUsd } from "../../_mock/metering";

/* "Raise the pass rate", "Raise booking success rate", "Raise Policy adherence". */
const goalText = (g) =>
  g.id === "pass-rate"
    ? "Raise the pass rate"
    : `Raise ${g.group === "outcome" ? `${g.label.charAt(0).toLowerCase()}${g.label.slice(1)}` : g.label}`;
const fieldLabel = { typography: "s2", fontWeight: 600, mb: 0.875 };
const selectSx = {
  height: 34,
  typography: "s2",
  "& .MuiSelect-select": { typography: "s2", py: 0.75 },
};

/**
 * New improvement (from Improvements) or Run self improvement (from a run's
 * Debug failures) — the step between a run's Fix my agent diagnosis and
 * the search. A name, plus the two things the search can't guess: the goal
 * and what it may spend. The search method and the improving models are the
 * defaults, never asked. The agent's constraints come along under Advanced,
 * editable for this improvement in place — past one of the agent's limits,
 * it asks there whether the agent's own limit should move too.
 *
 * The caller (Fix my agent) has already decided the agent and environment
 * (`preset`) and starts the run itself (`onStart`).
 */
export default function ImprovementSetup({
  open,
  onClose,
  preset,
  tasks,
  included = [],
  onStart,
  bare = false,
  onBack = null,
  agentField = null,
  onBuildEnv = null,
}) {
  const { state, commit } = useSimStore();
  const { agents } = useConnectedAgents();

  const main = state.myEnvironments.find((e) => e.id === preset?.envId) || null;
  const es = main ? state.byEnv[main.id] : null;
  /* Evaluations added from the goal menu go on the environment, the same way its Evaluations step adds them. */
  const { envState: evalEnvState, patch: patchEnv } = useEnvState(main?.id);
  const {
    appliedIds,
    add: addEvals,
    slotsLeft,
  } = useAppliedEvals(evalEnvState, patchEnv);
  const [addingEval, setAddingEval] = useState(false);
  const ADD_EVAL = "__add_eval";
  /* The agent to improve — still named when it's been deleted since (its
     kept record, else the environment's copy), so the field never vanishes
     and the reason Start is off is said. */
  const agent = useMemo(() => {
    const key = preset?.agentKey;
    if (!key) return null;
    const found =
      agents.find((a) => a.key === key) ||
      connectedAgentsFrom(state, { withDeleted: true }).find(
        (a) => a.key === key,
      );
    if (found) return found;
    const copy =
      main && es?.agent && envAgentKey(es.agent, main.id) === key
        ? es.agent
        : null;
    return copy
      ? {
          ...copy,
          key,
          name: copy.name || `${main.name} agent`,
          versions: agentVersions(es),
          usedIn: [{ envId: main.id, envName: main.name }],
        }
      : null;
  }, [agents, state, main, es, preset?.agentKey]);
  /* Debug failures on a run of an agent this environment has since swapped out. */
  const envKey = main && es?.agent ? envAgentKey(es.agent, main.id) : null;
  const swapped =
    !preset?.fromGoal &&
    !!preset?.agentKey &&
    !!envKey &&
    preset.agentKey !== envKey;

  const [name, setName] = useState("");
  /* The search method and the improving model are always the defaults. */
  const optimizerId = DEFAULT_METHOD;
  const model = OPTIMIZER_MODELS[0].id;
  /* This improvement's own constraints, on top of the agent's. */
  const [edits, setEdits] = useState({});
  const [goal, setGoal] = useState("pass-rate");
  const [budget, setBudget] = useState(null);
  /* How far it goes (approval mode): search and prove, or suggest only —
     measure, diagnose and suggest a change, with no trials. */
  const [suggestOnly, setSuggestOnly] = useState(false);
  /* The agent's other environments can add their must-pass sets (RSI PRD:
     "Targets come from one environment; the others add their must-pass
     sets"), and the search can try earlier versions' changes again. */
  const [alsoIn, setAlsoIn] = useState([]);
  const [acrossLineage, setAcrossLineage] = useState(false);
  /* The constraints sit in an advanced panel, the agent's applied — most
     people never open it. */
  const [advanced, setAdvanced] = useState(false);
  /* From a run: a short confirm first (agent, scenarios, budget, price), the
     same as Fix's — Edit plan opens everything below in place. */
  const [compact, setCompact] = useState(!preset?.fromGoal);
  /* Stamped when the setup opens: the seed is never reused, even after an
     improvement is deleted, and stays the same until Start. */
  const [openedAt, setOpenedAt] = useState(() => Date.now().toString(36));

  useEffect(() => {
    if (!open) return;
    setOpenedAt(Date.now().toString(36));
    setAdvanced(false);
    setCompact(!preset?.fromGoal);
    setAlsoIn([]);
    setAcrossLineage(false);
    setName(improvementRunName(es, null));
    setEdits({});
    setGoal("pass-rate");
    setBudget(null);
    setSuggestOnly(false);
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps
  /* Another agent picked in the same drawer: its own name, goals,
     environments and budget (the budgets come from its own limits). */
  useEffect(() => {
    if (!open || !main) return;
    setName(improvementRunName(es, null));
    setGoal("pass-rate");
    setAlsoIn([]);
    setAcrossLineage(false);
    setBudget(null);
    setEdits({});
  }, [preset?.agentKey]); // eslint-disable-line react-hooks/exhaustive-deps

  const gates = useRoleGates();
  const agentLimits = useMemo(
    () => limitsOf(state, preset?.agentKey),
    [state, preset?.agentKey],
  );
  /* 1,500 credits by default, within the agent's run budget; more is this
     improvement's own. */
  const budgets = budgetsWithin(agentLimits.budget.dollars);
  const spend =
    budget ??
    (budgets.includes(FIX_DEFAULTS.dollars)
      ? FIX_DEFAULTS.dollars
      : budgets[budgets.length - 1]);
  /* What the search is held to: the agent's constraints with this
     improvement's own edits, the budget picked beside the goal included. */
  const limits = useMemo(
    () =>
      withEdits(agentLimits, {
        ...edits,
        ...(spend > agentLimits.budget.dollars && { dollars: spend }),
      }),
    [agentLimits, edits, spend],
  );
  const edited = changedKeysOf(agentLimits, {
    ...edits,
    ...(spend > agentLimits.budget.dollars && { dollars: spend }),
  }).length;
  /* A voice agent searches in text; its finalists are confirmed as real calls. */
  const inText = main?.surface === "voice" && textSearchDefault(main?.surface);

  /* What the search scores candidates on: the pass rate, an outcome read
     from the tool calls, or one eval. */
  const goals = useMemo(
    () => [
      { id: "pass-rate", label: "Pass rate", group: "rate" },
      ...outcomeMetricsOf(main).map((m) => ({ ...m, group: "outcome" })),
      ...(es?.evals || []).map((e) => {
        const id = typeof e === "string" ? e : e.id;
        return {
          id,
          label: getEval(id)?.name || id,
          group: "eval",
          reliability: evalReliabilityOf(id),
        };
      }),
    ],
    [es, main],
  );
  const goalOf = goals.find((g) => g.id === goal) || goals[0];
  const goalArg = goalOf
    ? {
        id: goalOf.id,
        label: goalOf.label,
        group: goalOf.group,
        /* Carried into the record, so the proof says what the goal was worth. */
        ...(goalOf.reliability && {
          reliability: goalOf.reliability.measured
            ? {
                kappa: goalOf.reliability.kappa,
                tier: goalOf.reliability.tier,
                labelled: goalOf.reliability.labelled,
              }
            : { tier: "unmeasured" },
        }),
      }
    : undefined;
  const shaky = goalOf?.reliability && goalOf.reliability.tier !== "reliable";

  /* From the version in production. A goal improvement measures today
     itself — its first step runs the environment on that version, recorded
     as a run — so nothing is run by hand first; one opened from a run's
     Debug failures starts from that run. */
  const prodLabel = es ? productionAgentVersion(es)?.label || null : null;
  const seed = `goal-${main?.id}-${optimizationId(es)}-${openedAt}`;
  const today = useMemo(
    () =>
      main && preset?.fromGoal && !tasks?.length
        ? measureTodayRun(main, es, {
            key: seed,
            measuredFor: { kind: "goal" },
            startedBy: "Improvement · goal",
          })
        : null,
    [main, es, preset?.fromGoal, tasks, seed],
  );
  /* Debug failures starts from the run it diagnosed — that run's agent and
     environment versions — never the newest production run. */
  const fromRun =
    !preset?.fromGoal && !!(preset?.runId || preset?.agentVersion);
  const base = useMemo(() => {
    if (today) return measuredSummaryOf(main, es, today);
    if (!main) return null;
    if (fromRun) {
      const run = runBaseOf(main, es, preset?.runId);
      return {
        ...(run || { id: preset?.runId || null, tasks: tasks || null }),
        agentVersion: preset?.agentVersion ?? run?.agentVersion ?? null,
        envVersion: preset?.envVersion ?? run?.envVersion ?? null,
      };
    }
    return baseRunOf(main, es, prodLabel) || baseRunOf(main, es);
  }, [
    today,
    main,
    es,
    prodLabel,
    fromRun,
    preset?.runId,
    preset?.agentVersion,
    preset?.envVersion,
    tasks,
  ]);
  const others = useMemo(
    () =>
      (agent?.usedIn || [])
        .filter((u) => u.envId !== main?.id)
        .map((u) => state.myEnvironments.find((e) => e.id === u.envId))
        .filter(Boolean),
    [agent, main, state.myEnvironments],
  );
  /* Each other environment's must-pass set — the same rule the search
     holds it to (passes every baseline repeat; marked-but-flaky is named). */
  const otherMustPass = useMemo(
    () =>
      Object.fromEntries(
        others.map((e) => [e.id, envMustPassOf(e, state.byEnv[e.id])]),
      ),
    [others, state.byEnv],
  );
  const mustPassIn = (e) => otherMustPass[e.id]?.gated.length || 0;
  /* Earlier versions: strictly older than the one the search starts from. */
  const lineageVersions = useMemo(() => {
    const list = agentVersions(es);
    const from = base?.agentVersion || prodLabel;
    const at = list.findIndex((v) => v.label === from);
    return (at > 0 ? list.slice(0, at) : []).filter(
      (v) => (v.changes || []).length,
    );
  }, [es, base, prodLabel]);
  const versionName = (label) =>
    main && es ? envVersionNameOf(es, label, main.id).main : label;
  /* An environment with no scenarios has nothing to search — not "every scenario meets it". */
  const searchTasks =
    (tasks?.length ? tasks : null) || (base?.tasks?.length ? base.tasks : null);
  const envIds = useMemo(
    () => (main ? [main.id, ...alsoIn] : []),
    [main, alsoIn],
  );
  /* The other environments are priced inside the search (one formula), so
     the estimate follows what's checked, in any order. */
  const preview = useMemo(
    () =>
      main && searchTasks
        ? planSearch({
            env: main,
            es,
            agentVersion:
              base?.agentVersion || currentAgentVersion(es)?.label || null,
            tasks: searchTasks,
            limits,
            seed,
            textSearch: inText,
            goal: goalArg,
            optimizerId,
            state,
            regressionEnvIds: envIds,
          })
        : null,
    [
      main,
      es,
      base,
      searchTasks,
      limits,
      seed,
      inText,
      goalArg?.id,
      optimizerId,
      state,
      envIds,
    ], // eslint-disable-line react-hooks/exhaustive-deps
  );
  const nothing = !!preview?.goal && preview.goalTargets === 0;
  const status = agent ? agentStatusOf(agent) : null;
  /* One fix in flight per agent, and never from a run still going. */
  const fixRunning = (es?.optimizations || []).some(
    (r) => improvementOutcome(r)?.ending?.key === "running",
  );
  const blocked = fixRunning
    ? `A fix is already running for ${agent?.name || "this agent"} — wait for it to finish or stop it first.`
    : fromRun && base?.status === "running"
      ? "This run hasn't finished — start a fix once its results are in."
      : swapped
        ? `This run tested ${agent?.name || "an agent"}, which is no longer ${main?.name || "this environment"}'s agent — improve it from Improvements instead.`
        : preset?.agentKey && !agent
          ? "This agent can't be found — it may have been deleted."
          : status && status.status !== "connected"
            ? status.reason
            : null;
  const identity =
    main && es
      ? runIdentityOf(
          {
            agentVersion:
              base?.agentVersion || currentAgentVersion(es)?.label || null,
            envVersion: base?.envVersion || null,
            ...(swapped && agent?.name && { agentName: agent.name }),
          },
          es,
          main,
        ).label
      : null;
  /* An agent we can only call always ends in a suggestion. */
  const onlySuggests = fixDepthOf(es?.agent).depth === "suggestion";
  /* Measuring today is part of what it spends. Suggest only measures the
     target and held-back scenarios once, and runs no trials. */
  const searched = improvementEstimate({
    preview,
    surface: main?.surface,
    firstConversations: todayConversations(today),
  });
  const estimate = (() => {
    if (!suggestOnly || !preview) return searched;
    const per = PER_CONVERSATION[main?.surface === "voice" ? "voice" : "chat"];
    const conv =
      (preview.trainMeasured + preview.heldMeasured) * (preview.repeats || 3) +
      todayConversations(today);
    const usd = conv * per;
    return {
      parts: [
        {
          id: "measure",
          label: "Measuring",
          detail: "a suggestion only — no candidates run",
          conversations: conv,
          usd,
        },
      ],
      runs: 0,
      conversations: conv,
      low: Math.round(usd * 0.8),
      likely: Math.round(usd),
      high: Math.round(usd * 1.25),
      minutes: Math.max(3, Math.round(searched.minutes / 4)),
    };
  })();
  const overBudget = estimate.likely > spend;

  const agentModel = useMemo(() => {
    if (!agent || !main) return null;
    const def = agentDefinitionOf({ agent, env: main, envState: es });
    return def.model?.visible ? def.model.name : null;
  }, [agent, main, es]);
  const train = preview?.trainMeasured || 0;
  const held = preview?.heldMeasured || 0;
  /* Must-pass in the main environment, plus each other environment checked. */
  const mustPassMain =
    preview?.mustPassMeasured ?? preview?.mustPass?.total ?? 0;
  const mustPassAll =
    mustPassMain +
    others
      .filter((e) => alsoIn.includes(e.id))
      .reduce((n, e) => n + mustPassIn(e), 0);
  /* Marked must-pass but not passing every repeat today: named, not counted. */
  const notCounted = markedNotCountedLine([
    ...(preview?.markedNotCounted || []),
    ...others
      .filter((e) => alsoIn.includes(e.id))
      .flatMap((e) => otherMustPass[e.id]?.markedNotCounted || []),
  ]);
  /* Every constraint the search is held to, the time cap and must-pass
     included; the budget has its own picker above. */
  const constraints = limitRows(limits, agentModel, { mustPass: mustPassAll })
    .filter((c) => c.id !== "budget")
    .map((c) => `${c.label}: ${c.value}`)
    .join(" · ");
  /* The most runs it can use — it stops early once runs stop gaining. */
  const trials = estimate.runs || 0;
  /* The plan in one sentence, and whether held-back can tell a fix from noise. */
  const review = fixReviewSentence({
    targets: train,
    protectedN: mustPassAll,
    allowed: limits.allowed,
    credits: creditsLabel(spend),
    minutes: limits.budget.minutes,
  });
  const heldBack = heldBackOf(held);
  const ready = !!(
    agent &&
    main &&
    searchTasks &&
    !blocked &&
    !nothing &&
    name.trim()
  );

  const start = () => {
    if (!gates.canRun)
      return enqueueSnackbar(gates.runReason, { variant: "info" });
    /* Re-read the agent first; a change made outside Future AGI is saved as
       a version and becomes the baseline — measured on that version, so the
       baseline's version and its scores are the same version's. */
    const reread = rereadFor(agent, state);
    recordOutsideChange(commit, state, {
      agent,
      reread,
      activateEnvId: main.id,
    });
    const moved = !!(reread?.changed && reread.version);
    const esNow = moved ? envStateOnVersion(es, reread) : es;
    const first = moved
      ? measureTodayRun(main, esNow, {
          key: seed,
          measuredFor: preset?.fromGoal
            ? { kind: "goal" }
            : { kind: "run", runId: base?.id || null },
          startedBy: preset?.fromGoal
            ? "Improvement · goal"
            : "Improvement · Debug failures",
        })
      : today;
    const from =
      first && first !== today ? measuredSummaryOf(main, esNow, first) : base;
    onStart({
      name: name.trim(),
      model,
      optimizerId,
      agent,
      env: main,
      envIds,
      goal: goalOf,
      budget: spend,
      limits,
      seed,
      reread,
      textSearch: inText,
      lineage: acrossLineage ? lineageVersions : undefined,
      base: from,
      today: first,
      ...(suggestOnly && { depth: "suggestion" }),
    });
  };

  return (
    <SetupFrame bare={bare} open={open} onClose={onClose}>
      <Stack
        direction="row"
        alignItems="center"
        spacing={1.5}
        sx={{
          px: 2.5,
          py: 2,
          borderBottom: "1px solid",
          borderColor: "divider",
          flexShrink: 0,
        }}
      >
        {onBack && (
          <IconButton
            size="small"
            onClick={onBack}
            aria-label="Back to agents"
            sx={{ ml: -0.75 }}
          >
            <Iconify icon="solar:alt-arrow-left-linear" width={16} />
          </IconButton>
        )}
        <Box flex={1}>
          <Typography sx={{ typography: "s1", fontWeight: 600 }}>
            {preset?.fromGoal
              ? "New improvement"
              : `Improve ${agent?.name || "the agent"}`}
          </Typography>
          <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
            {main
              ? `Searches for a better ${agent?.name ? `${agent.name}` : "agent"}, scored by this environment`
              : "Pick the agent to improve — it runs in that agent's main environment."}
          </Typography>
        </Box>
        <IconButton size="small" onClick={onClose}>
          <Iconify icon="solar:close-linear" width={16} />
        </IconButton>
      </Stack>

      <Stack
        spacing={2.5}
        sx={{
          px: 2.5,
          py: 2.5,
          overflowY: "auto",
          ...(bare ? { flex: 1, minHeight: 0 } : { maxHeight: "62vh" }),
        }}
      >
        {compact && main ? (
          <Box>
            <Typography sx={{ typography: "s2", mb: 1.5 }}>{review}</Typography>
            <ConfirmRow k="Agent">
              <Typography sx={{ typography: "s2" }}>
                {`${agent?.name || "Agent"}${identity ? ` · ${identity}` : ""}`}
              </Typography>
            </ConfirmRow>
            <ConfirmRow k="Runs in">
              <Typography sx={{ typography: "s2" }}>{main.name}</Typography>
            </ConfirmRow>
            <ConfirmRow k="Scenarios">
              <Typography sx={{ typography: "s2" }}>
                {`${train} failing to fix · ${held} held back to prove it · ${mustPassAll} must keep passing`}
              </Typography>
              <HeldBackLine heldBack={heldBack} />
            </ConfirmRow>
            <ConfirmRow k="Budget">
              <Box sx={{ maxWidth: 240 }}>
                <ConstraintPicker
                  agent={agent}
                  limits={agentLimits}
                  field="dollars"
                  value={spend}
                  onChange={setBudget}
                  fullWidth
                />
              </Box>
              <Typography
                sx={{ typography: "s3", color: "text.secondary", mt: 0.75 }}
              >
                {`Up to ${trials} trials · ${creditsLabel(estimate.likely)} · ${estimate.minutes} min. Stops at the limit with the best it found.`}
              </Typography>
            </ConfirmRow>
            {estimate.parts?.length > 0 && (
              <Box
                sx={{
                  pt: 1,
                  borderTop: "1px solid",
                  borderColor: "divider",
                }}
              >
                <CostPartsTable
                  parts={estimate.parts}
                  totalLabel="Up to"
                  totalUsd={estimate.likely}
                />
              </Box>
            )}
          </Box>
        ) : (
          <>
            {agentField ||
              (preset?.agentKey && (
                /* Debug failures: the same field as New improvement, fixed to the
             agent this run tested and the version it tested — shown even when
             that agent has been deleted since. */
                <Box>
                  <Typography
                    sx={{ typography: "s3", fontWeight: 600, mb: 0.5 }}
                  >
                    Agent
                  </Typography>
                  <AgentPicker
                    choices={
                      agent
                        ? [
                            agentPickerChoice(
                              agent,
                              main,
                              null,
                              preset?.fromGoal ? null : base?.agentVersion,
                            ),
                          ]
                        : []
                    }
                    value={preset.agentKey}
                    locked="Debug failures improves the agent this run tested. To improve another agent, start a new improvement from Improvements."
                  />
                  {agent && (
                    <Typography
                      sx={{ typography: "s3", color: "text.tertiary", mt: 0.5 }}
                    >
                      {agentPickerHelper(
                        agentPickerChoice(
                          agent,
                          main,
                          null,
                          preset?.fromGoal ? null : base?.agentVersion,
                        ),
                        main?.name,
                      )}
                    </Typography>
                  )}
                </Box>
              ))}
            {main && (
              <>
                <Box>
                  <Typography sx={fieldLabel}>Name</Typography>
                  <TextField
                    size="small"
                    fullWidth
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    sx={{ "& .MuiInputBase-input": { typography: "s2" } }}
                  />
                </Box>
              </>
            )}

            {/* The two things the search can't guess — what to aim at, what it may spend. */}
            {main && (
              <Box>
                <Stack direction="row" spacing={1.5}>
                  <Box flex={1} minWidth={0}>
                    <Typography sx={fieldLabel}>Goal</Typography>
                    <Select
                      size="small"
                      fullWidth
                      value={goalOf.id}
                      sx={selectSx}
                      renderValue={() => goalText(goalOf)}
                      onChange={(e) =>
                        e.target.value === ADD_EVAL
                          ? setAddingEval(true)
                          : setGoal(e.target.value)
                      }
                      MenuProps={{ PaperProps: { sx: { maxHeight: 460 } } }}
                    >
                      {[
                        ["rate", null],
                        ["outcome", "Outcomes · from tool calls"],
                        ["eval", "Evaluations"],
                      ].flatMap(([group, title]) => {
                        const list = goals.filter((g) => g.group === group);
                        if (!list.length && group !== "eval") return [];
                        return [
                          ...(title
                            ? [
                                <ListSubheader
                                  key={group}
                                  sx={{
                                    typography: "s3",
                                    fontWeight: 600,
                                    color: "text.tertiary",
                                    lineHeight: "28px",
                                  }}
                                >
                                  {title}
                                </ListSubheader>,
                              ]
                            : []),
                          ...list.map((g) => (
                            <MenuItem
                              key={g.id}
                              value={g.id}
                              sx={{ typography: "s2", gap: 2 }}
                            >
                              <Box
                                component="span"
                                sx={{ flex: 1, minWidth: 0 }}
                              >
                                <Box component="span" sx={{ display: "block" }}>
                                  {goalText(g)}
                                </Box>
                                {/* What an outcome counts — read off its tool's calls. */}
                                {g.group === "outcome" && g.note && (
                                  <Box
                                    component="span"
                                    sx={{
                                      display: "block",
                                      typography: "s3",
                                      color: "text.tertiary",
                                    }}
                                  >
                                    {g.note}
                                  </Box>
                                )}
                              </Box>
                              {g.reliability && (
                                <Box
                                  component="span"
                                  sx={{
                                    typography: "s3",
                                    fontVariantNumeric: "tabular-nums",
                                    color:
                                      g.reliability.tier === "unreliable"
                                        ? "status.warning.fg"
                                        : "text.tertiary",
                                    flexShrink: 0,
                                  }}
                                >
                                  {g.reliability.measured
                                    ? `κ ${g.reliability.kappa.toFixed(2)} · ${g.reliability.label}`
                                    : g.reliability.label}
                                </Box>
                              )}
                            </MenuItem>
                          )),
                          /* Your own evaluation: the environment's add-evaluations flow, then it's here to pick. */
                          ...(group === "eval"
                            ? [
                                <MenuItem
                                  key={ADD_EVAL}
                                  value={ADD_EVAL}
                                  disabled={slotsLeft <= 0}
                                  sx={{
                                    typography: "s2",
                                    gap: 1,
                                  }}
                                >
                                  <Iconify icon="solar:add-linear" width={14} />
                                  <Box
                                    component="span"
                                    sx={{ flex: 1, minWidth: 0 }}
                                  >
                                    <Box
                                      component="span"
                                      sx={{
                                        display: "block",
                                        fontWeight: 600,
                                        color: "text.primary",
                                      }}
                                    >
                                      Add an evaluation
                                    </Box>
                                    <Box
                                      component="span"
                                      sx={{
                                        display: "block",
                                        typography: "s3",
                                        color: "text.tertiary",
                                      }}
                                    >
                                      {slotsLeft <= 0
                                        ? "This environment has all the evaluations it can take"
                                        : `From the library, or your own — added to ${main?.name || "this environment"}`}
                                    </Box>
                                  </Box>
                                </MenuItem>,
                              ]
                            : []),
                        ];
                      })}
                    </Select>
                  </Box>
                  <Box sx={{ width: 220, flexShrink: 0 }}>
                    <Typography sx={fieldLabel}>Budget</Typography>
                    <ConstraintPicker
                      agent={agent}
                      limits={agentLimits}
                      field="dollars"
                      value={spend}
                      onChange={setBudget}
                      fullWidth
                      sx={selectSx}
                    />
                  </Box>
                </Stack>
                {/* How far it goes — the approval mode. */}
                <Box sx={{ mt: 1.25 }}>
                  <Typography sx={fieldLabel}>How far it goes</Typography>
                  <Select
                    size="small"
                    fullWidth
                    value={onlySuggests || suggestOnly ? "suggest" : "search"}
                    disabled={onlySuggests}
                    onChange={(e) =>
                      setSuggestOnly(e.target.value === "suggest")
                    }
                    sx={selectSx}
                  >
                    <MenuItem value="search">
                      Search and prove — tries changes, proves the best on
                      held-back scenarios
                    </MenuItem>
                    <MenuItem value="suggest">
                      Suggest only — diagnoses and suggests a change, no trials
                    </MenuItem>
                  </Select>
                  <Typography
                    sx={{ typography: "s3", color: "text.tertiary", mt: 0.5 }}
                  >
                    {onlySuggests
                      ? "We can only call this agent, so an improvement ends in a suggested change."
                      : suggestOnly
                        ? "Nothing is tried or proven. Apply the suggestion yourself; a run on the new version proves it."
                        : "Either way, a winner comes back as a new version. You decide whether to make it the default."}
                  </Typography>
                </Box>
                {/* An evaluation as the goal is only as good as its agreement with people. */}
                {shaky && (
                  <Stack
                    direction="row"
                    spacing={0.75}
                    alignItems="flex-start"
                    sx={{ mt: 0.75 }}
                  >
                    <Iconify
                      icon="solar:danger-triangle-linear"
                      width={14}
                      sx={{
                        color:
                          goalOf.reliability.tier === "unreliable"
                            ? "status.warning.fg"
                            : "text.tertiary",
                        flexShrink: 0,
                        mt: "2px",
                      }}
                    />
                    <Typography
                      sx={{
                        typography: "s3",
                        color:
                          goalOf.reliability.tier === "unreliable"
                            ? "text.primary"
                            : "text.secondary",
                      }}
                    >
                      {`${goalOf.label}: ${evalReliabilityLine(goalOf.reliability)}`}
                    </Typography>
                  </Stack>
                )}
                <Typography
                  sx={{
                    typography: "s3",
                    color:
                      nothing || overBudget ? "text.primary" : "text.tertiary",
                    mt: 0.75,
                  }}
                >
                  {nothing
                    ? `Nothing to improve — every scenario already meets ${goalWords(goalOf)}. Pick another goal.`
                    : `${preview?.goal ? `Targets the ${preview.goalTargets} ${preview.goalTargets === 1 ? "scenario" : "scenarios"} short of it. ` : ""}${overBudget ? `May reach the limit before all ${trials} trials — you'll get the best it found by then.` : "If it runs over, it stops at the limit with the best it found."}`}
                </Typography>
              </Box>
            )}

            {/* Where else it must hold, and whether to try earlier versions' changes. */}
            {main && (others.length > 0 || lineageVersions.length > 0) && (
              <Stack spacing={1.5}>
                {others.length > 0 && (
                  <Box>
                    <Typography sx={fieldLabel}>
                      Also check must-pass in
                    </Typography>
                    <Stack spacing={0.25}>
                      {others.map((e) => {
                        const on = alsoIn.includes(e.id);
                        const n = mustPassIn(e);
                        return (
                          <Stack
                            key={e.id}
                            direction="row"
                            alignItems="center"
                            spacing={1}
                            {...clickable(
                              () =>
                                setAlsoIn((ids) =>
                                  on
                                    ? ids.filter((x) => x !== e.id)
                                    : [...ids, e.id],
                                ),
                              { role: "checkbox", checked: on },
                            )}
                            sx={{
                              ...focusRing,
                              px: 1,
                              py: 0.75,
                              borderRadius: 1,
                              cursor: "pointer",
                              "&:hover": { bgcolor: "action.hover" },
                            }}
                          >
                            <Checkbox
                              size="small"
                              checked={on}
                              tabIndex={-1}
                              disableRipple
                              inputProps={{ "aria-hidden": true }}
                              sx={{ p: 0, flexShrink: 0 }}
                            />
                            <Typography
                              noWrap
                              sx={{ typography: "s2", flex: 1, minWidth: 0 }}
                            >
                              {e.name}
                            </Typography>
                            <Typography
                              noWrap
                              sx={{
                                typography: "s3",
                                color: "text.tertiary",
                                fontVariantNumeric: "tabular-nums",
                              }}
                            >
                              {n ? `${n} must-pass` : "No must-pass yet"}
                            </Typography>
                          </Stack>
                        );
                      })}
                    </Stack>
                  </Box>
                )}
                {lineageVersions.length > 0 && (
                  <Stack direction="row" alignItems="center" spacing={1}>
                    <Box flex={1} minWidth={0}>
                      <Typography sx={{ typography: "s2", fontWeight: 600 }}>
                        Search across earlier versions
                      </Typography>
                      <Typography
                        sx={{ typography: "s3", color: "text.tertiary" }}
                      >
                        {`Also tries the changes ${versionName(lineageVersions[0].label)}–${versionName(lineageVersions[lineageVersions.length - 1].label)} made. Checked the same way as every other proposed change.`}
                      </Typography>
                    </Box>
                    <Switch
                      size="small"
                      checked={acrossLineage}
                      onChange={(e) => setAcrossLineage(e.target.checked)}
                    />
                  </Stack>
                )}
              </Stack>
            )}

            {/* Advanced: the constraints it's held to — the agent's, editable for this improvement. */}
            {main && (
              <Box>
                <Stack
                  direction="row"
                  alignItems="center"
                  spacing={0.75}
                  {...clickable(() => setAdvanced((v) => !v), {
                    expanded: advanced,
                  })}
                  sx={{ ...focusRing, cursor: "pointer", width: "fit-content" }}
                >
                  <Iconify
                    icon={
                      advanced
                        ? "solar:alt-arrow-down-linear"
                        : "solar:alt-arrow-right-linear"
                    }
                    width={14}
                    sx={{ color: "text.tertiary" }}
                  />
                  <Typography sx={{ typography: "s2", fontWeight: 600 }}>
                    Advanced
                  </Typography>
                  <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
                    {edited
                      ? `Constraints · ${edited} set for this improvement`
                      : `Constraints from ${agent?.name || "the agent"}`}
                  </Typography>
                </Stack>
                {advanced && (
                  <Box sx={{ mt: 1.5 }}>
                    <ImprovementConstraints
                      agent={agent}
                      env={main}
                      envState={es}
                      limits={agentLimits}
                      edits={edits}
                      onChange={setEdits}
                      model={agentModel}
                    />
                  </Box>
                )}
              </Box>
            )}

            {/* What the search starts from, what it's never allowed to see, and what it costs. */}
            {main && (
              <Box
                sx={{
                  px: 2,
                  py: 1.75,
                  borderRadius: 1,
                  border: "1px solid",
                  borderColor: "divider",
                  bgcolor: "background.neutral",
                }}
              >
                <Stack spacing={1.25}>
                  <Row label="Plan" value={review} />
                  <Row
                    label="Starts from"
                    value={
                      preset?.fromGoal
                        ? `${identity || "the version in production"} · aimed at ${goalWords(goalOf)}`
                        : `${identity || "the current version"} · ${included.length} ${included.length === 1 ? "change" : "changes"} you included`
                    }
                    note={
                      preset?.fromGoal
                        ? "The search tries changes on the scenarios short of the goal, and proves the winner on held-back ones."
                        : included.length
                          ? "Every run is built from these, so each one can be read line by line."
                          : "Nothing included — the search explores every change the diagnosis found."
                    }
                  />
                  <Row
                    label="Scenarios"
                    value={`${train} target · ${held} held back · ${mustPassAll} must-pass`}
                    note={`The headline number comes from held-back scenarios the search never sees.${notCounted ? ` ${notCounted}` : ""}`}
                  />
                  <HeldBackLine heldBack={heldBack} />
                  <Row
                    label="Cost"
                    value={`Up to ${creditsLabel(estimate.likely)} · ${estimate.minutes} min`}
                    note={`Up to ${trials} trials × ${train} target scenarios, then the finalists on held-back and must-pass. ${stopEarlyNote(trials)} ${inText ? "Runs as text conversations; only the finalists are confirmed as real calls." : "A trial is a full pass through the real tools, not a dataset lookup."}`}
                  />
                  <Row
                    label="Constraints"
                    value={
                      edited
                        ? `From ${agent?.name || "the agent"} · ${edited} set for this improvement`
                        : `From ${agent?.name || "the agent"}`
                    }
                    note={constraints}
                    action={
                      advanced
                        ? null
                        : { label: "Edit", onClick: () => setAdvanced(true) }
                    }
                  />
                </Stack>
              </Box>
            )}
          </>
        )}
      </Stack>

      {/* Why Start is off, said above the footer — never a silently disabled button. */}
      {blocked && (
        <Stack
          direction="row"
          spacing={0.75}
          alignItems="flex-start"
          sx={{
            px: 2.5,
            py: 1.25,
            borderTop: "1px solid",
            borderColor: "divider",
            flexShrink: 0,
          }}
        >
          <Iconify
            icon="solar:danger-triangle-linear"
            width={14}
            sx={{ color: "status.danger.fg", flexShrink: 0, mt: "2px" }}
          />
          <Typography sx={{ typography: "s3", color: "text.primary" }}>
            {blocked}
          </Typography>
        </Stack>
      )}
      <Stack
        direction="row"
        alignItems="center"
        spacing={1.5}
        sx={{
          px: 2.5,
          py: 2,
          borderTop: "1px solid",
          borderColor: "divider",
          flexShrink: 0,
        }}
      >
        <Typography sx={{ typography: "s3", color: "text.tertiary", flex: 1 }}>
          {main && !searchTasks
            ? `${main.name} has no scenarios to run yet.`
            : today
              ? "It runs the environment on today's version first. A winner comes back as a new version. You decide whether to make it the default."
              : "A winner comes back as a new version. You decide whether to make it the default."}
        </Typography>
        {/* The same experiment from code — what Start improvement would make. */}
        {main && (
          <CopyCommandButton
            command={`agent-learn improve create --baseline ${base?.id || "<run-id>"} \\\n  --goal "${goalOf?.label || "Raise the pass rate"}" --allow ${allowFlagOf(limits.allowed)} \\\n  --suite ${main.id}/target --holdout ${main.id}/holdout \\\n  --budget ${creditsForUsd(spend)} --budget-unit credits${suggestOnly ? " --suggest-only" : ""}`}
          />
        )}
        <Button size="small" onClick={onClose} sx={{ color: "text.secondary" }}>
          Cancel
        </Button>
        {compact && main && (
          <Tooltip
            arrow
            title="Change the goal, scenarios, budget, other environments or rules before starting."
          >
            <Button
              size="small"
              variant="outlined"
              color="inherit"
              onClick={() => setCompact(false)}
              sx={{ borderColor: "divider" }}
            >
              Edit plan
            </Button>
          </Tooltip>
        )}
        {/* The picked agent has no environment: build one, as Fix does. */}
        {!main && onBuildEnv ? (
          <Button
            size="small"
            variant="contained"
            color="primary"
            onClick={onBuildEnv}
          >
            Build environment
          </Button>
        ) : (
          <Button
            size="small"
            variant="contained"
            color="primary"
            disabled={!ready}
            onClick={start}
            startIcon={<Iconify icon="solar:play-bold" width={16} />}
          >
            Start improvement
          </Button>
        )}
      </Stack>
      {main && (
        <AddEvalsDrawer
          open={addingEval}
          onClose={() => setAddingEval(false)}
          env={main}
          envState={evalEnvState}
          existingIds={appliedIds}
          slotsLeft={slotsLeft}
          onAdd={(entries) => {
            addEvals(entries);
            /* The first one added becomes the goal. */
            if (entries[0]?.id) setGoal(entries[0].id);
            setAddingEval(false);
          }}
        />
      )}
    </SetupFrame>
  );
}

/* Held-back below the minimum still starts — proven on the targets only. */
function HeldBackLine({ heldBack }) {
  if (!heldBack.belowRecommended) return null;
  return (
    <Stack
      direction="row"
      spacing={0.75}
      alignItems="flex-start"
      sx={{ mt: 0.5 }}
    >
      {heldBack.tooFew && (
        <Iconify
          icon="solar:danger-triangle-linear"
          width={14}
          sx={{ color: "status.warning.fg", flexShrink: 0, mt: "2px" }}
        />
      )}
      <Typography
        sx={{
          typography: "s3",
          color: heldBack.tooFew ? "text.primary" : "text.tertiary",
        }}
      >
        {heldBack.tooFew
          ? `Too few held-back scenarios to tell a real fix from noise. The fix will be proven on the targets only. ${heldBack.n} of at least ${heldBack.minimum}; ${heldBack.recommended} recommended.`
          : `${heldBack.n} held back — ${heldBack.recommended} recommended for a surer read.`}
      </Typography>
    </Stack>
  );
}

HeldBackLine.propTypes = { heldBack: PropTypes.object.isRequired };

/* One line of the short confirm: a label and its value. */
function ConfirmRow({ k, children }) {
  return (
    <Stack
      direction="row"
      spacing={1.5}
      sx={{
        py: 1,
        borderTop: "1px solid",
        borderColor: "divider",
        "&:first-of-type": { borderTop: 0, pt: 0 },
      }}
    >
      <Typography
        sx={{
          typography: "s2",
          color: "text.subtitle",
          width: 110,
          flexShrink: 0,
        }}
      >
        {k}
      </Typography>
      <Box sx={{ flex: 1, minWidth: 0 }}>{children}</Box>
    </Stack>
  );
}
ConfirmRow.propTypes = { k: PropTypes.string, children: PropTypes.node };

/* A dialog where it opens over a drawer; the drawer's own body where it is the drawer. */
function SetupFrame({ bare, open, onClose, children }) {
  if (bare)
    return <Stack sx={{ height: "100%", minHeight: 0 }}>{children}</Stack>;
  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth={false}
      fullWidth
      PaperProps={{
        sx: { maxWidth: 720, borderRadius: "8px", backgroundImage: "none" },
      }}
    >
      {children}
    </Dialog>
  );
}
SetupFrame.propTypes = {
  bare: PropTypes.bool,
  open: PropTypes.bool,
  onClose: PropTypes.func,
  children: PropTypes.node,
};

ImprovementSetup.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func.isRequired,
  /* Rendered as the body of a drawer it sits in, not a dialog of its own. */
  bare: PropTypes.bool,
  /* Back to the step before (New improvement's agent list). */
  onBack: PropTypes.func,
  /* The agent picker, first in the form, when the agent is chosen here. */
  agentField: PropTypes.node,
  onBuildEnv: PropTypes.func,
  /* Debug failures also names the run it diagnosed and the versions it tested. */
  preset: PropTypes.shape({
    agentKey: PropTypes.string,
    envId: PropTypes.string,
    fromGoal: PropTypes.bool,
    runId: PropTypes.string,
    agentVersion: PropTypes.string,
    envVersion: PropTypes.string,
  }),
  tasks: PropTypes.array,
  /* The diagnosis changes ticked in Fix my agent. */
  included: PropTypes.array,
  onStart: PropTypes.func.isRequired,
};

function Row({ label, value, note, action }) {
  return (
    <Box>
      <Stack direction="row" alignItems="baseline" spacing={1}>
        <Typography
          sx={{
            typography: "s3",
            color: "text.tertiary",
            width: 78,
            flexShrink: 0,
          }}
        >
          {label}
        </Typography>
        <Typography
          sx={{ typography: "s2", fontWeight: 600, flex: 1, minWidth: 0 }}
        >
          {value}
        </Typography>
        {action && (
          <ButtonBase
            onClick={action.onClick}
            sx={{
              typography: "s3",
              fontWeight: 600,
              color: "text.secondary",
              "&:hover": { color: "text.primary" },
              ...focusRing,
            }}
          >
            {action.label}
          </ButtonBase>
        )}
      </Stack>
      {note && (
        <Typography
          sx={{ typography: "s3", color: "text.tertiary", pl: "86px" }}
        >
          {note}
        </Typography>
      )}
    </Box>
  );
}
Row.propTypes = {
  label: PropTypes.string,
  value: PropTypes.string,
  note: PropTypes.string,
  action: PropTypes.object,
};
