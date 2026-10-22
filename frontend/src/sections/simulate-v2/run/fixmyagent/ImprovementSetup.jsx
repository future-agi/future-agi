import PropTypes from "prop-types";
import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { alpha } from "@mui/material/styles";
import {
  Box, Button, Chip, Dialog, IconButton, ListSubheader, MenuItem, Select, Stack, Switch, TextField, Tooltip, Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { clickable, focusRing } from "../../components/clickable";
import { connectedAgentPath } from "../../improvements/improvementNav";
import AgentPicker from "../../improvements/AgentPicker";
import { agentPickerChoice, agentPickerHelper } from "../../improvements/agentPickerChoices";
import { useSimStore, useConnectedAgents, useEnvState } from "../../store";
import { agentDefinitionOf } from "../../_mock/agentRegistry";
import { getEval } from "../../_mock/evals";
import { evalReliabilityLine, evalReliabilityOf } from "../../_mock/evalReliability";
import AddEvalsDrawer from "../../workspace/evals/AddEvalsDrawer";
import { useAppliedEvals } from "../../workspace/evals/useAppliedEvals";
import { agentVersions, currentAgentVersion, productionAgentVersion } from "../../_mock/versions";
import { improverModelsText, limitRows, OPTIMIZER_MODELS, SAME_AS_PLANNER } from "../../_mock/optimizationRuns";
import { OPTIMIZERS, goalWords, improvementRunName } from "../../_mock/optimizer";
import { baseRunOf, budgetsWithin, improvementEstimate, outcomeMetricsOf, planSearch } from "../../_mock/improvementStart";
import { measureTodayRun, measuredSummaryOf } from "../../_mock/measureToday";
import { agentStatusOf } from "../../_mock/agentLifecycle";
import { runIdentityOf } from "../../_mock/runIdentity";
import { DEFAULT_METHOD, methodLabelOf } from "./improvementView";
import { limitsOf } from "../../agents/agentLimits";
import { rereadFor, textSearchDefault } from "../../_mock/improvementPolicy";
import { recordOutsideChange } from "../../_mock/nudgeEffects";
import { creditsForUsd, creditsLabel } from "../../_mock/metering";

/* Selected reads in white, as the product's other choices do — purple is for actions, not state. */
const selectedBg = (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.08 : 0.05);
/* "Raise the pass rate", "Raise booking success rate", "Raise Policy adherence". */
const WARN = "#CA8A04";
const goalText = (g) => (g.id === "pass-rate" ? "Raise the pass rate" : `Raise ${g.group === "outcome" ? `${g.label.charAt(0).toLowerCase()}${g.label.slice(1)}` : g.label}`);
const fieldLabel = { typography: "s2", fontWeight: 700, mb: 0.875 };
const selectSx = { height: 34, typography: "s2", "& .MuiSelect-select": { typography: "s2", py: 0.75 } };

/**
 * New improvement (from Improvements) or Run self improvement (from a run's
 * Debug failures) — the step between a run's Fix my agent diagnosis and
 * the search. The same smallest set of decisions it always asked for (a name,
 * an optimizer, the model doing the improving), plus the two the search can't
 * guess: the goal and what it may spend. The agent's constraints come along
 * as they are — summarised, changed on the agent.
 *
 * The caller (Fix my agent) has already decided the agent and environment
 * (`preset`) and starts the run itself (`onStart`).
 */
/*
  One of the improver's two models, as chips. The writer also offers "Same
  as planner", its default. Neither is the model the agent runs on.
*/
function ModelPicker({ title, hint, value, onChange, same = null }) {
  const options = [
    ...(same ? [{ id: SAME_AS_PLANNER, label: `Same as planner (${same})`, note: "One model plans and writes" }] : []),
    ...OPTIMIZER_MODELS,
  ];
  return (
    <Box>
      <Stack direction="row" alignItems="center" spacing={0.75} sx={{ mb: 0.25 }}>
        <Typography sx={{ typography: "s2", fontWeight: 700 }}>{title}</Typography>
        <Tooltip arrow title="The model doing the improving, not the model your agent runs on.">
          <Box component="span" sx={{ display: "flex" }}>
            <Iconify icon="solar:info-circle-linear" width={13} sx={{ color: "text.disabled" }} />
          </Box>
        </Tooltip>
      </Stack>
      <Typography sx={{ typography: "s3", color: "text.subtitle", mb: 0.875 }}>{hint}</Typography>
      <Stack direction="row" spacing={0.875} flexWrap="wrap" rowGap={0.875} useFlexGap>
        {options.map((m) => {
          const active = m.id === value;
          return (
            <Tooltip key={m.id} arrow title={m.note}>
              <Chip
                label={m.label}
                onClick={() => onChange(m.id)}
                sx={{
                  height: 27, borderRadius: 0.75, border: "1px solid",
                  borderColor: active ? "text.primary" : "divider",
                  bgcolor: (t) => (active ? selectedBg(t) : "transparent"),
                  color: active ? "text.primary" : "text.secondary",
                  "& .MuiChip-label": { px: 1.125, typography: "s2", fontWeight: 600 },
                  "&:hover": { bgcolor: selectedBg },
                }}
              />
            </Tooltip>
          );
        })}
      </Stack>
    </Box>
  );
}
ModelPicker.propTypes = { title: PropTypes.string, hint: PropTypes.string, value: PropTypes.string, onChange: PropTypes.func, same: PropTypes.string };

export default function ImprovementSetup({ open, onClose, preset, tasks, included = [], onStart, bare = false, onBack = null, agentField = null }) {
  const navigate = useNavigate();
  const { state, commit } = useSimStore();
  const { agents } = useConnectedAgents();

  const main = state.myEnvironments.find((e) => e.id === preset?.envId) || null;
  const es = main ? state.byEnv[main.id] : null;
  /* Evaluations added from the goal menu go on the environment, the same way its Evaluations step adds them. */
  const { envState: evalEnvState, patch: patchEnv } = useEnvState(main?.id);
  const { appliedIds, add: addEvals, slotsLeft } = useAppliedEvals(evalEnvState, patchEnv);
  const [addingEval, setAddingEval] = useState(false);
  const ADD_EVAL = "__add_eval";
  const agent = agents.find((a) => a.key === preset?.agentKey) || null;

  const [name, setName] = useState("");
  const [optimizerId, setOptimizerId] = useState(DEFAULT_METHOD);
  const [model, setModel] = useState(OPTIMIZER_MODELS[0].id);
  const [writer, setWriter] = useState(SAME_AS_PLANNER);
  const [goal, setGoal] = useState("pass-rate");
  const [budget, setBudget] = useState(null);
  /* The agent's other environments can add their must-pass sets (RSI PRD:
     "Targets come from one environment; the others add their must-pass
     sets"), and the search can try earlier versions' changes again. */
  const [alsoIn, setAlsoIn] = useState([]);
  const [acrossLineage, setAcrossLineage] = useState(false);
  /* The search method and the improving model sit in an advanced panel,
     with defaults applied — most people never open it. */
  const [advanced, setAdvanced] = useState(false);

  useEffect(() => {
    if (!open) return;
    setAdvanced(false);
    setAlsoIn([]);
    setAcrossLineage(false);
    setName(improvementRunName(es, null));
    setOptimizerId(DEFAULT_METHOD);
    setModel(OPTIMIZER_MODELS[0].id);
    setWriter(SAME_AS_PLANNER);
    setGoal("pass-rate");
    setBudget(null);
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps
  /* Another agent picked in the same drawer: its own name, goals and environments. */
  useEffect(() => {
    if (!open || !main) return;
    setName(improvementRunName(es, null));
    setGoal("pass-rate");
    setAlsoIn([]);
    setAcrossLineage(false);
  }, [main?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const limits = useMemo(() => limitsOf(state, preset?.agentKey), [state, preset?.agentKey]);
  /* A voice agent searches in text; its finalists are confirmed as real calls. */
  const inText = main?.surface === "voice" && textSearchDefault(main?.surface);

  /* What the search scores candidates on: the pass rate, an outcome read
     from the tool calls, or one eval. */
  const goals = useMemo(() => [
    { id: "pass-rate", label: "Pass rate", group: "rate" },
    ...outcomeMetricsOf(main).map((m) => ({ ...m, group: "outcome" })),
    ...((es?.evals || []).map((e) => {
      const id = typeof e === "string" ? e : e.id;
      return { id, label: getEval(id)?.name || id, group: "eval", reliability: evalReliabilityOf(id) };
    })),
  ], [es, main]);
  const goalOf = goals.find((g) => g.id === goal) || goals[0];
  const goalArg = goalOf ? {
    id: goalOf.id, label: goalOf.label, group: goalOf.group,
    /* Carried into the record, so the proof says what the goal was worth. */
    ...(goalOf.reliability && { reliability: goalOf.reliability.measured ? { kappa: goalOf.reliability.kappa, tier: goalOf.reliability.tier, labelled: goalOf.reliability.labelled } : { tier: "unmeasured" } }),
  } : undefined;
  const shaky = goalOf?.reliability && goalOf.reliability.tier !== "reliable";

  /* From the version in production. A goal improvement measures today
     itself — its first step runs the environment on that version, recorded
     as a run — so nothing is run by hand first; one opened from a run's
     Debug failures starts from that run. */
  const prodLabel = es ? productionAgentVersion(es)?.label || null : null;
  const seed = `goal-${main?.id}-${(es?.optimizations || []).length}`;
  const today = useMemo(
    () => (main && preset?.fromGoal && !tasks ? measureTodayRun(main, es, { key: seed, measuredFor: { kind: "goal" }, startedBy: "Improvement · goal" }) : null),
    [main, es, preset?.fromGoal, tasks, seed],
  );
  const base = useMemo(
    () => (today ? measuredSummaryOf(main, es, today) : main ? baseRunOf(main, es, prodLabel) || baseRunOf(main, es) : null),
    [today, main, es, prodLabel],
  );
  const others = useMemo(() => (agent?.usedIn || [])
    .filter((u) => u.envId !== main?.id)
    .map((u) => state.myEnvironments.find((e) => e.id === u.envId))
    .filter(Boolean), [agent, main, state.myEnvironments]);
  const mustPassIn = (e) => (state.byEnv[e.id]?.scenarios || []).filter((r) => !r.archived && !r.retired && (r.critical || r.regressionMemory)).length;
  const lineageVersions = useMemo(() => {
    const list = agentVersions(es);
    const from = base?.agentVersion || prodLabel;
    const at = list.findIndex((v) => v.label === from);
    return (at > 0 ? list.slice(0, at) : []).filter((v) => (v.changes || []).length);
  }, [es, base, prodLabel]);
  const searchTasks = tasks || base?.tasks;
  const envIds = main ? [main.id, ...alsoIn] : [];
  const preview = useMemo(
    () => (main && searchTasks ? planSearch({ env: main, es, agentVersion: base?.agentVersion || currentAgentVersion(es)?.label || null, tasks: searchTasks, limits, seed, textSearch: inText, goal: goalArg, optimizerId, state, regressionEnvIds: envIds }) : null),
    [main, es, base, searchTasks, limits, seed, inText, goalArg?.id, optimizerId, state], // eslint-disable-line react-hooks/exhaustive-deps
  );
  const nothing = !!preview?.goal && preview.goalTargets === 0;
  const status = agent ? agentStatusOf(agent) : null;
  const blocked = status && status.status !== "connected" ? status.reason : null;
  const identity = main && es ? runIdentityOf({ agentVersion: base?.agentVersion || currentAgentVersion(es)?.label || null, envVersion: base?.envVersion || null }, es, main).label : null;
  const estimate = improvementEstimate({
    preview, surface: main?.surface, extraScenarios: others.filter((e) => alsoIn.includes(e.id)).reduce((n, e) => n + mustPassIn(e), 0),
    /* Measuring today is part of what it spends. */
    firstConversations: today ? (today.scenarioIds || []).length * (today.repeats || 1) : 0,
  });
  const budgets = budgetsWithin(limits.budget.dollars);
  const spend = budget ?? budgets[budgets.length - 1];
  const overBudget = estimate.likely > spend;

  const agentModel = useMemo(() => {
    if (!agent || !main) return null;
    const def = agentDefinitionOf({ agent, env: main, envState: es });
    return def.model?.visible ? def.model.name : null;
  }, [agent, main, es]);
  const train = preview?.trainMeasured || 0;
  const held = preview?.heldMeasured || 0;
  /* Must-pass in the main environment, plus each other environment checked. */
  const mustPassMain = preview?.mustPassMeasured ?? preview?.mustPass?.total ?? 0;
  const mustPassAll = mustPassMain + others.filter((e) => alsoIn.includes(e.id)).reduce((n, e) => n + mustPassIn(e), 0);
  /* Every constraint the search is held to, the time cap and must-pass
     included; the budget has its own picker above. */
  const constraints = limitRows(limits, agentModel, { mustPass: mustPassAll }).filter((c) => c.id !== "budget").map((c) => `${c.label}: ${c.value}`).join(" · ");
  const trials = preview?.trials?.length || 0;
  const ready = !!(agent && main && searchTasks && !blocked && !nothing && name.trim());

  const start = () => {
    /* Re-read the agent first; a change made outside Future AGI is saved as
       a version and becomes the baseline. */
    const reread = rereadFor(agent, state);
    recordOutsideChange(commit, state, { agent, reread, activateEnvId: main.id });
    onStart({
      name: name.trim(), model, optimizerId, ...(writer !== SAME_AS_PLANNER && writer !== model && { writerModel: writer }),
      agent, env: main, envIds, goal: goalOf, budget: spend, limits, seed, reread, textSearch: inText,
      lineage: acrossLineage ? lineageVersions : undefined, base, today,
    });
  };

  return (
    <SetupFrame bare={bare} open={open} onClose={onClose}>
      <Stack direction="row" alignItems="center" spacing={1.5} sx={{ px: 2.5, py: 2, borderBottom: "1px solid", borderColor: "divider", flexShrink: 0 }}>
        {onBack && (
          <IconButton size="small" onClick={onBack} aria-label="Back to agents" sx={{ ml: -0.75 }}>
            <Iconify icon="solar:alt-arrow-left-linear" width={17} />
          </IconButton>
        )}
        <Box flex={1}>
          <Typography sx={{ typography: "s1", fontWeight: 700 }}>{preset?.fromGoal ? "New improvement" : "Run self improvement"}</Typography>
          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
            {main ? `Searches for a better ${agent?.name ? `${agent.name}` : "agent"}, scored by this environment` : "Pick the agent to improve — it runs in that agent's main environment."}
          </Typography>
        </Box>
        <IconButton size="small" onClick={onClose}><Iconify icon="eva:close-fill" width={17} /></IconButton>
      </Stack>

      <Stack spacing={2.5} sx={{ px: 2.5, py: 2.5, overflowY: "auto", ...(bare ? { flex: 1, minHeight: 0 } : { maxHeight: "62vh" }) }}>
        {agentField || (agent && (
          /* Debug failures: the same field as New improvement, fixed to the agent this run tested. */
          <Box>
            <Typography sx={{ typography: "s3", fontWeight: 600, mb: 0.5 }}>Agent</Typography>
            <AgentPicker
              choices={[agentPickerChoice(agent, main)]}
              value={agent.key}
              locked="Debug failures improves the agent this run tested. To improve another agent, start a new improvement from Improvements."
            />
            <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 0.5 }}>
              {agentPickerHelper(agentPickerChoice(agent, main), main?.name)}
            </Typography>
          </Box>
        ))}
        {!main && agentField && (
          <Typography sx={{ typography: "s3", color: "text.subtitle", mt: "-12px !important" }}>
            Its goals, budget and cost show here once it&apos;s picked.
          </Typography>
        )}
        {main && (<>
        <Box>
          <Typography sx={fieldLabel}>Name</Typography>
          <TextField size="small" fullWidth value={name} onChange={(e) => setName(e.target.value)} sx={{ "& .MuiInputBase-input": { typography: "s2" } }} />
        </Box>

        {/* Optimiser names sit in an advanced panel; the defaults apply until someone opens it. */}
        <Box>
          <Stack
            direction="row" alignItems="center" spacing={0.75}
            {...clickable(() => setAdvanced((v) => !v), { expanded: advanced })}
            sx={{ ...focusRing, cursor: "pointer", width: "fit-content" }}
          >
            <Iconify icon={advanced ? "eva:arrow-ios-downward-fill" : "eva:arrow-ios-forward-fill"} width={14} sx={{ color: "text.subtitle" }} />
            <Typography sx={{ typography: "s2", fontWeight: 700 }}>Advanced</Typography>
            <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
              {`${methodLabelOf(optimizerId)} search (${OPTIMIZERS.find((o) => o.id === optimizerId)?.label || optimizerId}) · changes ${improverModelsText(model, writer)}${optimizerId === DEFAULT_METHOD && model === OPTIMIZER_MODELS[0].id && writer === SAME_AS_PLANNER ? " — the defaults" : ""}`}
            </Typography>
          </Stack>
          {advanced && (
            <Stack spacing={2.5} sx={{ mt: 1.5 }}>
              <Box>
                <Typography sx={fieldLabel}>Search method</Typography>
                <Stack direction="row" spacing={1} flexWrap="wrap" rowGap={1} useFlexGap role="radiogroup" aria-label="Search method">
                  {OPTIMIZERS.map((o) => {
                    const active = o.id === optimizerId;
                    return (
                      <Tooltip key={o.id} arrow title={o.fit}>
                        <Box
                          {...clickable(() => setOptimizerId(o.id), { role: "radio", checked: active })}
                          sx={{
                            ...focusRing,
                            px: 1.5, py: 1.125, borderRadius: 1, cursor: "pointer", flex: "1 1 46%", minWidth: 190,
                            border: "1px solid", borderColor: active ? "text.primary" : "divider",
                            bgcolor: (t) => (active ? selectedBg(t) : "transparent"),
                            "&:hover": { borderColor: active ? "text.primary" : "text.disabled" },
                          }}
                        >
                          <Stack direction="row" alignItems="center" spacing={0.75}>
                            <Iconify icon={active ? "solar:check-circle-bold" : "solar:circle-linear"} width={14} sx={{ color: active ? "text.primary" : "text.disabled" }} />
                            {/* How many runs it tries is in Cost below — set by the budget, not the card. */}
                            {/* One name for the method everywhere: plain words, the optimiser's in brackets. */}
                            <Typography sx={{ typography: "s2", fontWeight: 700 }}>{`${methodLabelOf(o.id)} search (${o.label})`}</Typography>
                          </Stack>
                          <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 0.25 }}>{o.desc}</Typography>
                        </Box>
                      </Tooltip>
                    );
                  })}
                </Stack>
              </Box>

              <ModelPicker
                title="Planner"
                hint="Reads the failures and decides what to change. It decides the outcome — use the strongest model you can."
                value={model} onChange={setModel}
              />
              <ModelPicker
                title="Writer"
                hint="Turns each plan into the exact edit — the prompt line, the tool description. A cheaper model is usually enough."
                value={writer} onChange={setWriter}
                same={OPTIMIZER_MODELS.find((m) => m.id === model)?.label}
              />
            </Stack>
          )}
        </Box>

        </>)}

        {/* The two things the search can't guess — what to aim at, what it may spend. */}
        {main && (
          <Box>
            <Stack direction="row" spacing={1.5}>
              <Box flex={1} minWidth={0}>
                <Typography sx={fieldLabel}>Goal</Typography>
                <Select
                  size="small" fullWidth value={goalOf.id} sx={selectSx} renderValue={() => goalText(goalOf)}
                  onChange={(e) => (e.target.value === ADD_EVAL ? setAddingEval(true) : setGoal(e.target.value))}
                  MenuProps={{ PaperProps: { sx: { maxHeight: 460 } } }}
                >
                  {[["rate", null], ["outcome", "Outcomes · from tool calls"], ["eval", "Evaluations"]].flatMap(([group, title]) => {
                    const list = goals.filter((g) => g.group === group);
                    if (!list.length && group !== "eval") return [];
                    return [
                      ...(title ? [<ListSubheader key={group} sx={{ typography: "s3", fontWeight: 700, color: "text.subtitle", lineHeight: "28px" }}>{title}</ListSubheader>] : []),
                      ...list.map((g) => (
                        <MenuItem key={g.id} value={g.id} sx={{ typography: "s2", gap: 2 }}>
                          <Box component="span" sx={{ flex: 1, minWidth: 0 }}>
                            <Box component="span" sx={{ display: "block" }}>{goalText(g)}</Box>
                            {/* What an outcome counts — read off its tool's calls. */}
                            {g.group === "outcome" && g.note && (
                              <Box component="span" sx={{ display: "block", typography: "s3", color: "text.subtitle" }}>{g.note}</Box>
                            )}
                          </Box>
                          {g.reliability && (
                            <Box component="span" sx={{ typography: "s3", fontVariantNumeric: "tabular-nums", color: g.reliability.tier === "unreliable" ? WARN : "text.subtitle", flexShrink: 0 }}>
                              {g.reliability.measured ? `κ ${g.reliability.kappa.toFixed(2)} · ${g.reliability.label}` : g.reliability.label}
                            </Box>
                          )}
                        </MenuItem>
                      )),
                      /* Your own evaluation: the environment's add-evaluations flow, then it's here to pick. */
                      ...(group === "eval" ? [
                        <MenuItem key={ADD_EVAL} value={ADD_EVAL} disabled={slotsLeft <= 0} sx={{ typography: "s2", gap: 1, color: "text.secondary" }}>
                          <Iconify icon="mingcute:add-line" width={14} />
                          <Box component="span" sx={{ flex: 1, minWidth: 0 }}>
                            <Box component="span" sx={{ display: "block", fontWeight: 600, color: "text.primary" }}>Add an evaluation</Box>
                            <Box component="span" sx={{ display: "block", typography: "s3", color: "text.subtitle" }}>
                              {slotsLeft <= 0 ? "This environment has all the evaluations it can take" : `From the library, or your own — added to ${main?.name || "this environment"}`}
                            </Box>
                          </Box>
                        </MenuItem>,
                      ] : []),
                    ];
                  })}
                </Select>
              </Box>
              <Box sx={{ width: 180, flexShrink: 0 }}>
                <Typography sx={fieldLabel}>Budget</Typography>
                <Select size="small" fullWidth value={spend} onChange={(e) => setBudget(e.target.value)} sx={selectSx}>
                  {budgets.map((b) => <MenuItem key={b} value={b} sx={{ typography: "s2" }}>{creditsLabel(b)}</MenuItem>)}
                </Select>
              </Box>
            </Stack>
            {/* An evaluation as the goal is only as good as its agreement with people. */}
            {shaky && (
              <Stack direction="row" spacing={0.75} alignItems="flex-start" sx={{ mt: 0.75 }}>
                <Iconify icon="solar:danger-triangle-linear" width={14} sx={{ color: goalOf.reliability.tier === "unreliable" ? WARN : "text.subtitle", flexShrink: 0, mt: "2px" }} />
                <Typography sx={{ typography: "s3", color: goalOf.reliability.tier === "unreliable" ? "text.primary" : "text.secondary" }}>
                  {`${goalOf.label}: ${evalReliabilityLine(goalOf.reliability)}`}
                </Typography>
              </Stack>
            )}
            <Typography sx={{ typography: "s3", color: nothing || overBudget ? "text.primary" : "text.subtitle", mt: 0.75 }}>
              {nothing
                ? `Nothing to improve — every scenario already meets ${goalWords(goalOf)}. Pick another goal.`
                : `${preview?.goal ? `Targets the ${preview.goalTargets} ${preview.goalTargets === 1 ? "scenario" : "scenarios"} short of it. ` : ""}${overBudget ? "Likely to stop at the budget — you'll get the best it found by then." : "If it runs over, it stops at the budget with the best it found."}`}
            </Typography>
          </Box>
        )}

        {/* Where else it must hold, and whether to try earlier versions' changes. */}
        {main && (others.length > 0 || lineageVersions.length > 0) && (
          <Stack spacing={1.5}>
            {others.length > 0 && (
              <Box>
                <Typography sx={fieldLabel}>Also check must-pass in</Typography>
                <Stack spacing={0.25}>
                  {others.map((e) => {
                    const on = alsoIn.includes(e.id);
                    const n = mustPassIn(e);
                    return (
                      <Stack
                        key={e.id} direction="row" alignItems="center" spacing={1}
                        {...clickable(() => setAlsoIn((ids) => (on ? ids.filter((x) => x !== e.id) : [...ids, e.id])), { role: "checkbox", checked: on })}
                        sx={{ ...focusRing, px: 1, py: 0.75, borderRadius: 1, cursor: "pointer", "&:hover": { bgcolor: "action.hover" } }}
                      >
                        <Iconify icon={on ? "solar:check-square-bold" : "solar:stop-linear"} width={16} sx={{ color: on ? "text.primary" : "text.subtitle", flexShrink: 0 }} />
                        <Typography noWrap sx={{ typography: "s2", flex: 1, minWidth: 0 }}>{e.name}</Typography>
                        <Typography noWrap sx={{ typography: "s3", color: "text.subtitle", fontVariantNumeric: "tabular-nums" }}>{n ? `${n} must-pass` : "No must-pass yet"}</Typography>
                      </Stack>
                    );
                  })}
                </Stack>
              </Box>
            )}
            {lineageVersions.length > 0 && (
              <Stack direction="row" alignItems="center" spacing={1}>
                <Box flex={1} minWidth={0}>
                  <Typography sx={{ typography: "s2", fontWeight: 600 }}>Search across earlier versions</Typography>
                  <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                    {`Also tries the changes ${lineageVersions[0].label}–${lineageVersions[lineageVersions.length - 1].label} made. Checked the same way as every other proposed change.`}
                  </Typography>
                </Box>
                <Switch size="small" checked={acrossLineage} onChange={(e) => setAcrossLineage(e.target.checked)} />
              </Stack>
            )}
          </Stack>
        )}

        {/* What the search starts from, what it's never allowed to see, and what it costs. */}
        {main && (
          <Box sx={{ px: 2, py: 1.75, borderRadius: 1, border: "1px solid", borderColor: "divider", bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.03 : 0.015) }}>
            <Stack spacing={1.25}>
              <Row
                label="Starts from"
                value={preset?.fromGoal
                  ? `${identity || "the version in production"} · aimed at ${goalWords(goalOf)}`
                  : `${identity || "the current version"} · ${included.length} ${included.length === 1 ? "change" : "changes"} you included`}
                note={preset?.fromGoal
                  ? "The search tries changes on the scenarios short of the goal, and proves the winner on held-back ones."
                  : included.length ? "Every run is built from these, so each one can be read line by line." : "Nothing included — the search explores every change the diagnosis found."}
              />
              <Row
                label="Scenarios"
                value={`${train} target · ${held} held back · ${mustPassAll} must-pass`}
                note="The search sees target scenarios in full and must-pass ones as pass or fail only. The headline number comes from the held-back scenarios, which are never shown while searching."
              />
              <Row
                label="Cost"
                value={`Likely ${creditsLabel(estimate.likely)} · between ${creditsForUsd(estimate.low).toLocaleString()} and ${creditsForUsd(estimate.high).toLocaleString()} · about ${estimate.minutes} min`}
                note={`${trials} runs × ${train} target scenarios, then the finalists on held-back and must-pass. ${inText ? "Runs as text conversations; only the finalists are confirmed as real calls." : "A run is a full pass through the real tools, not a dataset lookup."}`}
              />
              <Row
                label="Limits"
                value="From the agent"
                note={constraints}
                action={agent ? { label: "Change", onClick: () => { onClose(); navigate(connectedAgentPath(agent.id, "constraints")); } } : null}
              />
            </Stack>
          </Box>
        )}
      </Stack>

      <Stack direction="row" alignItems="center" spacing={1.5} sx={{ px: 2.5, py: 2, borderTop: "1px solid", borderColor: "divider", flexShrink: 0 }}>
        <Typography sx={{ typography: "s3", color: blocked ? "#DC2626" : "text.subtitle", flex: 1 }}>
          {blocked || (main && !searchTasks ? `${main.name} has no scenarios to run yet.` : today ? "It runs the environment on today's version first. Nothing ships on its own — a winner comes back as a draft." : "Nothing ships on its own — a winner comes back as a draft.")}
        </Typography>
        <Button onClick={onClose} sx={{ typography: "s2", fontWeight: 600, color: "text.secondary" }}>Cancel</Button>
        <Button
          variant="contained" color="primary" disabled={!ready} onClick={start}
          startIcon={<Iconify icon="solar:play-bold" width={15} />}
          sx={{ typography: "s2", fontWeight: 700 }}
        >
          Start improvement
        </Button>
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

/* A dialog where it opens over a drawer; the drawer's own body where it is the drawer. */
function SetupFrame({ bare, open, onClose, children }) {
  if (bare) return <Stack sx={{ height: "100%", minHeight: 0 }}>{children}</Stack>;
  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth PaperProps={{ sx: { borderRadius: 1.5, backgroundImage: "none" } }}>
      {children}
    </Dialog>
  );
}
SetupFrame.propTypes = { bare: PropTypes.bool, open: PropTypes.bool, onClose: PropTypes.func, children: PropTypes.node };

ImprovementSetup.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func.isRequired,
  /* Rendered as the body of a drawer it sits in, not a dialog of its own. */
  bare: PropTypes.bool,
  /* Back to the step before (New improvement's agent list). */
  onBack: PropTypes.func,
  /* The agent picker, first in the form, when the agent is chosen here. */
  agentField: PropTypes.node,
  preset: PropTypes.shape({ agentKey: PropTypes.string, envId: PropTypes.string, fromGoal: PropTypes.bool }),
  tasks: PropTypes.array,
  /* The diagnosis changes ticked in Fix my agent. */
  included: PropTypes.array,
  onStart: PropTypes.func.isRequired,
};

function Row({ label, value, note, action }) {
  return (
    <Box>
      <Stack direction="row" alignItems="baseline" spacing={1}>
        <Typography sx={{ typography: "s3", color: "text.subtitle", width: 78, flexShrink: 0 }}>{label}</Typography>
        <Typography sx={{ typography: "s2", fontWeight: 600, flex: 1, minWidth: 0 }}>{value}</Typography>
        {action && (
          <Typography {...clickable(action.onClick)} sx={{ typography: "s3", fontWeight: 700, color: "text.secondary", cursor: "pointer", "&:hover": { color: "text.primary" }, ...focusRing }}>
            {action.label}
          </Typography>
        )}
      </Stack>
      {note && <Typography sx={{ typography: "s3", color: "text.subtitle", pl: "86px" }}>{note}</Typography>}
    </Box>
  );
}
Row.propTypes = { label: PropTypes.string, value: PropTypes.string, note: PropTypes.string, action: PropTypes.object };
