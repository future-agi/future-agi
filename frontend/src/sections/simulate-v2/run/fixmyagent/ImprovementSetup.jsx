import PropTypes from "prop-types";
import { SHOW_RUN_COST } from "../../_mock/costFlags";
import { voiceFixOf } from "../../_mock/voiceFix";
import { useEffect, useMemo, useState } from "react";
import { enqueueSnackbar } from "notistack";
import { useRoleGates } from "../../_mock/previewRole";
import {
  Checkbox,
  Box,
  Button,
  IconButton,
  Link,
  ListSubheader,
  MenuItem,
  Select,
  Stack,
  Switch,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { clickable, focusRing } from "../../components/clickable";
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
  protectedLabel,
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
  MAX_CANDIDATES,
  heldBackOf,
  improvementEstimate,
  outcomeMetricsOf,
  planSearch,
  runBaseOf,
  todayConversations,
} from "../../_mock/improvementStart";
import { markedNotCountedLine } from "../../_mock/mustPass";
import { epochBudgetLine } from "../../errorFeed/issueFixModel";
import { envVersionNameOf } from "../../_mock/codeRefs";
import { measureTodayRun, measuredSummaryOf } from "../../_mock/measureToday";
import { agentStatusOf } from "../../_mock/agentLifecycle";
import { runIdentityOf } from "../../_mock/runIdentity";
import { DEFAULT_METHOD } from "./improvementView";
import ImprovementConstraints, {
  ConstraintPicker,
} from "../../improvements/ImprovementConstraints";
import { mayChangeLineOf, withEdits } from "../../improvements/constraintEdits";
import CasePicker from "../../improvements/CasePicker";
import { isCodeAgent } from "../../agents/foundFacts";
import { limitsOf } from "../../agents/agentLimits";
import { reviewableOf } from "../../_mock/candidateReview";
import ReviewChangesFirstDrawer from "./ReviewChangesFirstDrawer";
import { rereadFor, textSearchDefault } from "../../_mock/improvementPolicy";
import { recordOutsideChange } from "../../_mock/nudgeEffects";
import { creditsLabel } from "../../_mock/metering";
import { creditsForUsd } from "../../_mock/metering";
import { casesForSkillOf, loadedVOf, skillSetOf } from "../../_mock/skills";
import ClosableDialog from "../../components/ClosableDialog";

/* "Raise the pass rate", "Raise booking success rate", "Raise Policy adherence". */
const goalText = (g) =>
  g.id === "pass-rate"
    ? "Raise the pass rate"
    : `Raise ${g.group === "outcome" ? `${g.label.charAt(0).toLowerCase()}${g.label.slice(1)}` : g.label}`;
const fieldLabel = { typography: "s2", fontWeight: 600, mb: 0.875 };
const NUM = { fontVariantNumeric: "tabular-nums" };
const selectSx = {
  height: 34,
  typography: "s2",
  "& .MuiSelect-select": { typography: "s2", py: 0.75 },
};

/**
 * New epoch (from Epochs) or Run Epoch (from a run's Debug failures) — the
 * step between a run's diagnosis and the search. One sheet of rows: the
 * goal, where it runs, the scenarios, what may change and the budget. What
 * may change is the agent's rules (Agents → Constraints), said in one line
 * and edited on the agent in place — never set for one epoch. The budget
 * and time are this epoch's own; past one of the agent's limits, the picker
 * asks whether the agent's own limit should move too. The search method and
 * the improving models are the defaults, never asked.
 *
 * The caller (Fix my agent) has already decided the agent and environment
 * (`preset`) and starts the run itself (`onStart`).
 */
export default function ImprovementSetup({
  open,
  onClose,
  preset,
  tasks,
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
  /* Review the changes before any of them runs, then search with the kept ones. */
  const [reviewFirst, setReviewFirst] = useState(false);
  const [reviewing, setReviewing] = useState(false);
  /* The cases chosen in place: { fix, protect } ids, or null for the suggestion. */
  const [pick, setPick] = useState(null);
  const [showCases, setShowCases] = useState(false);
  /* The agent's other environments can add their must-pass sets (RSI PRD:
     "Targets come from one environment; the others add their must-pass
     sets"), and the search can try earlier versions' changes again. */
  const [alsoIn, setAlsoIn] = useState([]);
  const [acrossLineage, setAcrossLineage] = useState(false);
  /* The agent's rules (what may change), opened in place from the May
     change row — saved to the agent, never set for one epoch. */
  const [rulesOpen, setRulesOpen] = useState(false);
  /* The rest of the plan opens in place. */
  const [more, setMore] = useState(false);
  /* Stamped when the setup opens: the seed is never reused, even after an
     improvement is deleted, and stays the same until Start. */
  const [openedAt, setOpenedAt] = useState(() => Date.now().toString(36));

  useEffect(() => {
    if (!open) return;
    setOpenedAt(Date.now().toString(36));
    setRulesOpen(false);
    setMore(false);
    setAlsoIn([]);
    setAcrossLineage(false);
    setName(improvementRunName(es, null));
    setEdits({});
    setGoal("pass-rate");
    setBudget(null);
    setSuggestOnly(false);
    setReviewFirst(false);
    setReviewing(false);
    setPick(null);
    setShowCases(false);
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
    setPick(null);
    setRulesOpen(false);
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
  /* What the search is held to: the agent's rules, with this epoch's own
     spend (budget, time) and skill focus. What may change is the agent's. */
  const limits = useMemo(
    () =>
      withEdits(agentLimits, {
        ...edits,
        ...(spend > agentLimits.budget.dollars && { dollars: spend }),
      }),
    [agentLimits, edits, spend],
  );
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
            startedBy: "Epoch · goal",
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
            pick,
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
      pick,
    ], // eslint-disable-line react-hooks/exhaustive-deps
  );
  const nothing = !!preview?.goal && preview.goalTargets === 0;
  const status = agent ? agentStatusOf(agent) : null;
  /* One fix in flight per agent, and never from a run still going. */
  const fixRunning = (es?.optimizations || []).some(
    (r) => improvementOutcome(r)?.ending?.key === "running",
  );
  /* A voice agent built outside Future AGI can't be changed by a search
     (Nikhil, 2 Nov): Debug failures diagnoses it, and suggests a change when
     its prompt can be read. */
  const voiceFix = voiceFixOf(agent);
  const blocked = voiceFix
    ? `${agent?.name || "This agent"} is built in ${voiceFix.where}, so an epoch can't change it. Use Debug failures on a run: it ${voiceFix.mode === "suggest" ? "shows what went wrong and suggests changes to copy in" : "shows what went wrong (diagnosis only — its prompt can't be read)"}.`
    : fixRunning
      ? `An epoch is already running for ${agent?.name || "this agent"} — wait for it to finish or stop it first.`
      : fromRun && base?.status === "running"
        ? "This run hasn't finished — run an epoch once its results are in."
        : swapped
          ? `This run tested ${agent?.name || "an agent"}, no longer ${main?.name || "this environment"}'s agent. Improve it from Epochs.`
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
  /* A locked release holds back its test split; the fix learns only from
     the train split (plan D04, improve §4). */
  const lockedTest = es?.lockedRelease?.splits?.test || null;
  const train = preview?.trainMeasured || 0;
  const held = lockedTest ? lockedTest.length : preview?.heldMeasured || 0;
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
  /* What this kind of agent can have changed: code agents their code,
     platform agents their settings; every agent its instructions and tools. */
  const codeAgent = !!agent && isCodeAgent(agent);
  /* The skills the agent loads — a skill can change only where it has some. */
  const loadedSkills = agent
    ? skillSetOf(agent, state.workspaceSettings).skills.filter(
        (x) => x.state === "on",
      )
    : [];
  const skillFocus = limits.skillFocus || loadedSkills[0]?.id || "";
  const skillDepth = limits.skillDepth || "description";
  const focusSkill = loadedSkills.find((x) => x.id === skillFocus);
  /* The most runs it can use — it stops early once runs stop gaining. */
  const trials = estimate.runs || 0;
  /* Whether held-back can tell a real gain from noise. */
  const heldBack = heldBackOf(held);
  /* What may change: the agent's rules, named as its Constraints tab names them. */
  const mayChange = mayChangeLineOf(limits, agent);
  /* Where it runs and what it starts from, in one value. */
  const runsIn = main
    ? `${main.name} · ${
        today
          ? "today's version, measured first"
          : base?.id
            ? `from ${base.ordinal ? `Run ${base.ordinal}` : base.id}${
                base.agentVersion || base.envVersion
                  ? ` (${[
                      base.agentVersion && `agent ${base.agentVersion}`,
                      base.envVersion && `env ${base.envVersion}`,
                    ]
                      .filter(Boolean)
                      .join(" × ")})`
                  : ""
              }`
            : "no run yet, today's version measured first"
      }`
    : null;
  const candidatesWord = `${trials} ${trials === 1 ? "candidate" : "candidates"}`;
  /* One helper under the two pickers, worded as every epoch surface words
     it. The budget is for the whole epoch, never per candidate. */
  const budgetLine = epochBudgetLine({
    candidates: trials,
    credits: creditsLabel(spend),
    minutes: limits.budget.minutes,
    ...(SHOW_RUN_COST && {
      stops: overBudget
        ? `stops at ${creditsLabel(spend)}; the full plan needs about ${creditsLabel(estimate.likely)} · ${estimate.minutes} min`
        : `about ${creditsLabel(estimate.likely)} · ${estimate.minutes} min`,
    }),
  });
  /* Under the goal: why there's nothing to do, or what it targets. */
  const goalLine = nothing
    ? `Nothing to improve — every scenario already meets ${goalWords(goalOf)}. Pick another goal.`
    : [
        preview?.goal
          ? `${preview.goalTargets} ${preview.goalTargets === 1 ? "scenario is" : "scenarios are"} short of it today.`
          : null,
        SHOW_RUN_COST && overBudget
          ? `May reach the limit before all ${candidatesWord} — you'll get the best it found by then.`
          : null,
      ]
        .filter(Boolean)
        .join(" ") || null;
  const moreParts = [
    "Name",
    !preset?.fromGoal && "goal",
    "how far it goes",
    others.length > 0 && "other environments",
    lineageVersions.length > 0 && "earlier versions",
  ]
    .filter(Boolean)
    .join(", ");
  /* The same epoch from code — what Run Epoch would make. Flags follow the
     API's fields (source run, target and must-pass scenarios, surfaces,
     locked paths, credits, minutes). */
  const cliCommand = main
    ? `fi api epochs create --source-run-id ${base?.id || "<run-id>"} \\\n  --target-case-keys ${main.id}/target --protected-case-keys ${main.id}/must-pass \\\n  --surfaces ${allowFlagOf(limits.allowed)}${(limits.protected || []).length ? ` --locked-paths ${limits.protected.join(",")}` : ""} \\\n  --budget-credits ${creditsForUsd(spend)} --max-minutes ${limits.budget.minutes}`
    : null;
  const ready = !!(
    agent &&
    main &&
    searchTasks &&
    !blocked &&
    !nothing &&
    name.trim()
  );

  const start = (reviewed = null) => {
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
            ? "Epoch · goal"
            : "Epoch · Debug failures",
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
      ...(pick && { pick }),
      base: from,
      today: first,
      ...(suggestOnly && { depth: "suggestion" }),
      ...(reviewed && { candidates: { items: reviewed } }),
    });
  };

  /* The goal, chosen in one place: the first row when the epoch starts from
     a goal, under More options when it starts from a run. */
  const goalSelect = (
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
            <MenuItem key={g.id} value={g.id} sx={{ typography: "s2", gap: 2 }}>
              <Box component="span" sx={{ flex: 1, minWidth: 0 }}>
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
                  <Box component="span" sx={{ flex: 1, minWidth: 0 }}>
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
  );
  const goalNotes = (
    <>
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
      {goalLine && (
        <Typography
          sx={{
            typography: "s3",
            color: nothing ? "text.primary" : "text.tertiary",
            mt: 0.75,
          }}
        >
          {goalLine}
        </Typography>
      )}
    </>
  );

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
              ? "New epoch"
              : `Run Epoch on ${agent?.name || "the agent"}`}
          </Typography>
          <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
            {main
              ? `Searches for a better ${agent?.name ? `${agent.name}` : "agent"}, scored by this environment`
              : "Pick the agent to improve — it runs in that agent's main environment."}
          </Typography>
        </Box>
        {cliCommand && <CopyCommandIcon command={cliCommand} />}
        <IconButton size="small" onClick={onClose} aria-label="Close">
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
        {/* New improvement: the agent picker leads; from a run the agent is fixed. */}
        {agentField}
        {/*
          One box, edited in place (Fix my agent v0's launch screen): each
          part of the plan on its own row — goal, where it runs, scenarios,
          what may change, budget — changed where it's shown. What may change
          is the agent's rules, said in one line and edited on the agent.
          The rest sits under More options.
        */}
        {main && (
          <Box>
            {/* improve.md: user intent first — a goal-started epoch leads
                with its goal, chosen right here. */}
            {preset?.fromGoal && (
              <ConfirmRow k="Goal">
                {goalSelect}
                {goalNotes}
              </ConfirmRow>
            )}
            {!agentField && (
              <ConfirmRow k="Agent">
                <Typography sx={{ typography: "s2" }}>
                  {`${agent?.name || "Agent"}${identity ? ` · ${identity}` : ""}`}
                </Typography>
              </ConfirmRow>
            )}
            {/* Where it runs and what it starts from (source_run_id): one row. */}
            <ConfirmRow k="Runs in">
              <Typography sx={{ typography: "s2" }}>{runsIn}</Typography>
            </ConfirmRow>
            <ConfirmRow k="Scenarios">
              <Typography sx={{ typography: "s2", ...NUM }}>
                {`Target ${train} · Must-pass ${mustPassAll} · Held back ${held}`}
                {preview?.pickable && (
                  <>
                    {" · "}
                    <InlineLink
                      onClick={() => setShowCases((v) => !v)}
                      expanded={showCases}
                    >
                      {showCases
                        ? "Hide"
                        : pick
                          ? "Edit · your pick"
                          : "Choose"}
                    </InlineLink>
                  </>
                )}
              </Typography>
              {lockedTest && (
                <Typography
                  sx={{ typography: "s3", color: "text.tertiary", mt: 0.5 }}
                >
                  {`Held back: release ${es.lockedRelease.version}'s test split. It learns only from the train split.`}
                </Typography>
              )}
              <HeldBackLine heldBack={heldBack} />
              {notCounted && (
                <Typography
                  sx={{ typography: "s3", color: "text.tertiary", mt: 0.5 }}
                >
                  {notCounted}
                </Typography>
              )}
              {showCases && preview?.pickable && (
                <Box sx={{ mt: 1.25 }}>
                  <CasePicker
                    pickable={preview.pickable}
                    pick={pick}
                    onChange={setPick}
                  />
                </Box>
              )}
            </ConfirmRow>
            {/* The agent's rules decide what may change (never set per
                epoch): one line, edited on the agent, in place. */}
            <ConfirmRow k="May change">
              <Typography sx={{ typography: "s2" }}>
                {mayChange}
                {agent && !agent.deleted?.at && (
                  <>
                    {" · "}
                    <InlineLink
                      onClick={() => setRulesOpen((v) => !v)}
                      expanded={rulesOpen}
                    >
                      {rulesOpen ? "Hide agent rules" : "Edit agent rules"}
                    </InlineLink>
                  </>
                )}
              </Typography>
              <Typography
                sx={{ typography: "s3", color: "text.tertiary", mt: 0.5 }}
              >
                {`From ${agent?.name || "the agent"}'s rules. Evaluators, hidden scenarios and frozen world inputs are never edited.`}
              </Typography>
            </ConfirmRow>
            {rulesOpen && (
              <Box
                sx={{
                  py: 1.5,
                  borderTop: "1px solid",
                  borderColor: "divider",
                }}
              >
                <ImprovementConstraints
                  agent={agent}
                  env={main}
                  envState={es}
                  model={agentModel}
                />
              </Box>
            )}
            {(limits.allowed || []).includes("skills") && focusSkill && (
              <ConfirmRow k="Skill">
                <Stack
                  direction="row"
                  spacing={1}
                  alignItems="center"
                  flexWrap="wrap"
                  useFlexGap
                >
                  <Select
                    size="small"
                    value={skillFocus}
                    onChange={(e) =>
                      setEdits((ed) => ({ ...ed, skillFocus: e.target.value }))
                    }
                    sx={{
                      typography: "s2",
                      fontFamily: "ui-monospace, Menlo, monospace",
                      minWidth: 180,
                    }}
                  >
                    {loadedSkills.map((x) => (
                      <MenuItem
                        key={x.id}
                        value={x.id}
                        sx={{ typography: "s2" }}
                      >
                        {`${x.id} v${loadedVOf(x)}`}
                      </MenuItem>
                    ))}
                  </Select>
                  <Select
                    size="small"
                    value={skillDepth}
                    onChange={(e) =>
                      setEdits((ed) => ({ ...ed, skillDepth: e.target.value }))
                    }
                    sx={{ typography: "s2", minWidth: 220 }}
                  >
                    <MenuItem value="description" sx={{ typography: "s2" }}>
                      When it&apos;s used only
                    </MenuItem>
                    <MenuItem value="instructions" sx={{ typography: "s2" }}>
                      When it&apos;s used, and its steps
                    </MenuItem>
                  </Select>
                  <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
                    {`From v${loadedVOf(focusSkill)}. Its scripts and resources stay as they are.`}
                  </Typography>
                  {preview?.trainMeasuredTasks &&
                    !casesForSkillOf(focusSkill, preview.trainMeasuredTasks)
                      .length && (
                      <Typography
                        sx={{ typography: "s3", color: "status.warning.fg" }}
                      >
                        {`None of the ${preview.trainMeasuredTasks.length} target ${preview.trainMeasuredTasks.length === 1 ? "scenario mentions" : "scenarios mention"} what ${focusSkill.id} is for. Pick another skill or other scenarios, or allow instructions in the agent rules.`}
                      </Typography>
                    )}
                </Stack>
              </ConfirmRow>
            )}
            {/* A code agent's files it must never edit — the agent's rule
                ("Never edited"), said here, edited with the agent rules. */}
            {codeAgent && (
              <ConfirmRow k="Never edited">
                <Typography
                  sx={{
                    typography: "s2",
                    fontFamily: "monospace",
                    wordBreak: "break-word",
                    ...(!(limits.protected || []).length && {
                      fontFamily: "inherit",
                      color: "text.tertiary",
                    }),
                  }}
                >
                  {(limits.protected || []).length
                    ? limits.protected.map(protectedLabel).join(", ")
                    : "No files locked"}
                </Typography>
              </ConfirmRow>
            )}
            {/* The one place spend is set: credits and time, for the whole
                epoch (never per candidate). */}
            <ConfirmRow k="Budget">
              <Stack direction="row" spacing={1.5} alignItems="flex-start">
                <Box sx={{ width: 200 }}>
                  <ConstraintPicker
                    agent={agent}
                    limits={agentLimits}
                    field="dollars"
                    value={spend}
                    onChange={setBudget}
                    fullWidth
                  />
                </Box>
                <Box sx={{ width: 200 }}>
                  <ConstraintPicker
                    agent={agent}
                    limits={agentLimits}
                    field="minutes"
                    value={limits.budget.minutes}
                    onChange={(v) => setEdits((e) => ({ ...e, minutes: v }))}
                    fullWidth
                  />
                </Box>
              </Stack>
              <Typography
                sx={{
                  typography: "s3",
                  color: "text.tertiary",
                  mt: 0.75,
                  ...NUM,
                }}
              >
                {budgetLine}
              </Typography>
            </ConfirmRow>
          </Box>
        )}

        {/* The rest — name, goal (when it didn't start from one), how far it
            goes, other environments, earlier versions — one click away. */}
        {main && (
          <Box>
            <Stack
              direction="row"
              alignItems="center"
              spacing={0.75}
              {...clickable(() => setMore((v) => !v), { expanded: more })}
              sx={{ ...focusRing, cursor: "pointer", width: "fit-content" }}
            >
              <Iconify
                icon={
                  more
                    ? "solar:alt-arrow-down-linear"
                    : "solar:alt-arrow-right-linear"
                }
                width={14}
                sx={{ color: "text.tertiary" }}
              />
              <Typography sx={{ typography: "s2", fontWeight: 600 }}>
                More options
              </Typography>
              <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
                {moreParts}
              </Typography>
            </Stack>
            {more && (
              <Stack spacing={2.5} sx={{ mt: 2 }}>
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

                {/* Started from a run: the goal is chosen here (from a goal,
                    it's the first row above). */}
                {!preset?.fromGoal && (
                  <Box>
                    <Typography sx={fieldLabel}>Goal</Typography>
                    {goalSelect}
                    {goalNotes}
                  </Box>
                )}

                {/* How far it goes — the approval mode. */}
                <Box>
                  <Typography sx={fieldLabel}>How far it goes</Typography>
                  <Select
                    size="small"
                    fullWidth
                    value={
                      onlySuggests || suggestOnly
                        ? "suggest"
                        : reviewFirst
                          ? "review"
                          : "search"
                    }
                    disabled={onlySuggests}
                    onChange={(e) => {
                      setSuggestOnly(e.target.value === "suggest");
                      setReviewFirst(e.target.value === "review");
                    }}
                    sx={selectSx}
                  >
                    <MenuItem value="search">
                      Search and prove — tries changes, proves the best on
                      held-back scenarios
                    </MenuItem>
                    <MenuItem value="suggest">
                      Suggest only — diagnoses and suggests a change, no
                      candidates
                    </MenuItem>
                  </Select>
                  {(onlySuggests || suggestOnly) && (
                    <Typography
                      sx={{
                        typography: "s3",
                        color: "text.tertiary",
                        mt: 0.5,
                      }}
                    >
                      {onlySuggests
                        ? "We can only call this agent, so an epoch ends in a suggested change."
                        : "Nothing is tried or proven. Apply the suggestion yourself; a run on the new version proves it."}
                    </Typography>
                  )}
                </Box>

                {/* Where else it must hold, and whether to try earlier versions' changes. */}
                {(others.length > 0 || lineageVersions.length > 0) && (
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
                                  sx={{
                                    typography: "s2",
                                    flex: 1,
                                    minWidth: 0,
                                  }}
                                >
                                  {e.name}
                                </Typography>
                                <Typography
                                  noWrap
                                  sx={{
                                    typography: "s3",
                                    color: "text.tertiary",
                                    ...NUM,
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
                          <Typography
                            sx={{ typography: "s2", fontWeight: 600 }}
                          >
                            Search across earlier versions
                          </Typography>
                          <Typography
                            sx={{ typography: "s3", color: "text.tertiary" }}
                          >
                            {`Also tries the changes ${versionName(lineageVersions[0].label)}–${versionName(lineageVersions[lineageVersions.length - 1].label)} made.`}
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
              </Stack>
            )}
          </Box>
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
        {/* Said once, here — and only once there's an agent to run. */}
        <Typography sx={{ typography: "s3", color: "text.tertiary", flex: 1 }}>
          {main && !searchTasks
            ? `${main.name} has no scenarios to run yet.`
            : main
              ? "A winner comes back as a new version. You decide whether to make it the default."
              : null}
        </Typography>
        <Button size="small" onClick={onClose} sx={{ color: "text.secondary" }}>
          Cancel
        </Button>
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
            onClick={() => (reviewFirst ? setReviewing(true) : start())}
            startIcon={
              <Iconify
                icon={reviewFirst ? "solar:eye-linear" : "solar:play-bold"}
                width={16}
              />
            }
          >
            {reviewFirst ? "Review the changes" : "Run Epoch"}
          </Button>
        )}
      </Stack>
      <ReviewChangesFirstDrawer
        open={reviewing}
        onClose={() => setReviewing(false)}
        candidates={reviewableOf(preview?.pool || [])}
        maxTrials={MAX_CANDIDATES}
        onRun={(items) => {
          setReviewing(false);
          start(items);
        }}
      />
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
          ? `Too few held-back scenarios: the result can only be Limited validation, never Verified, and any pull request opens as a draft. ${heldBack.n} of at least ${heldBack.minimum}; ${heldBack.recommended} recommended.`
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

/* A text link inside a row's value ("Choose", "Edit agent rules"). */
function InlineLink({ onClick, expanded, children }) {
  return (
    <Link
      component="button"
      type="button"
      underline="hover"
      onClick={onClick}
      aria-expanded={expanded}
      sx={{
        typography: "s2",
        fontWeight: 600,
        color: "text.primary",
        verticalAlign: "baseline",
        ...focusRing,
      }}
    >
      {children}
    </Link>
  );
}
InlineLink.propTypes = {
  onClick: PropTypes.func.isRequired,
  expanded: PropTypes.bool,
  children: PropTypes.node,
};

/* The same epoch as a CLI command, from the header — copied on click, shown on hover. */
function CopyCommandIcon({ command }) {
  const [copied, setCopied] = useState(false);
  const copy = () => {
    navigator.clipboard?.writeText(command).catch(() => {});
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };
  return (
    <Tooltip
      arrow
      title={
        <Box component="span">
          <Box component="span" sx={{ display: "block", fontWeight: 600 }}>
            {copied ? "Copied" : "Copy as CLI command"}
          </Box>
          <Box
            component="span"
            sx={{
              display: "block",
              fontFamily: "monospace",
              whiteSpace: "pre-wrap",
              opacity: 0.8,
              mt: 0.5,
            }}
          >
            {command}
          </Box>
        </Box>
      }
    >
      <IconButton size="small" onClick={copy} aria-label="Copy as CLI command">
        <Iconify
          icon={copied ? "solar:check-circle-linear" : "solar:code-linear"}
          width={16}
        />
      </IconButton>
    </Tooltip>
  );
}
CopyCommandIcon.propTypes = { command: PropTypes.string.isRequired };

/* A dialog where it opens over a drawer; the drawer's own body where it is the drawer. */
function SetupFrame({ bare, open, onClose, children }) {
  if (bare)
    return <Stack sx={{ height: "100%", minHeight: 0 }}>{children}</Stack>;
  return (
    <ClosableDialog
      open={open}
      onClose={onClose}
      maxWidth={false}
      fullWidth
      PaperProps={{
        sx: { maxWidth: 720, borderRadius: "8px", backgroundImage: "none" },
      }}
    >
      {children}
    </ClosableDialog>
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
  /* The diagnosis changes ticked in Fix my agent (the caller keeps them on the record). */
  included: PropTypes.array,
  onStart: PropTypes.func.isRequired,
};
