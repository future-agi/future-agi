import PropTypes from "prop-types";
import IssueReturnBanner from "../errorFeed/IssueReturnBanner";
import { useEffect, useMemo, useRef, useState } from "react";
import { ErrorBoundary } from "react-error-boundary";
import { useSnackbar } from "notistack";
import { useLocation, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { alpha } from "@mui/material/styles";
import {
  Box, Stack, Typography, Button, IconButton, Tab, Tooltip, Drawer,
  Popover, MenuItem, ListItemIcon, ListItemText, Switch,
} from "@mui/material";
import SideDrawer from "../components/SideDrawer";
import { coveredDrawerSx, useDrawerCovered } from "src/hooks/use-drawer-stack";
import { FilterPanel } from "src/components/filter-panel";
import Iconify from "src/components/iconify";
import { clickable, focusRing } from "../components/clickable";
import { subTasksFor } from "../_mock/contract";
import { CustomTabs } from "src/components/tabs/tabs";
import { paths } from "src/routes/paths";
import { getEval } from "../_mock/evals";
import {
  SectionCard, StatusChip, EmptyState,
} from "../components/primitives";
import RunAnalytics from "./RunAnalyticsV2";
import CallDrawer from "./CallDrawer";
import VoiceDetailDrawerV2 from "src/components/VoiceDetailDrawerV2";
import { effectiveModality } from "../_mock/rlContract";
import { taskToVoiceData } from "../_mock/voiceCallData";
import { subGoalResultsOf } from "../_mock/scenarioSubGoals";
import AddEvalsDrawer from "../workspace/evals/AddEvalsDrawer";
import EditEvalDrawer from "../workspace/evals/EditEvalDrawer";
import { canEditEval } from "../workspace/evals/canEditEval";
import { useAppliedEvals } from "../workspace/evals/useAppliedEvals";
import { useEnvState } from "../store";
import useRunFitCheck from "./useRunFitCheck";
import { protoRunId } from "../_mock/executionAdapter";
import { runSummaries, trialSummaries } from "../_mock/comparison";
import { textTone } from "../components/textTone";
import { resolveEval } from "../_mock/evals";
import TraceTable, { TraceGroupByPicker, TraceColumnsPicker } from "./TraceTable";
import { defaultTraceColumns, deriveUseCaseLabel } from "./traceTableHelpers";
import FixMyAgentDrawer from "./fixmyagent/FixMyAgentDrawer";
import { improvementOf, isCandidateRun, runIdentityOf } from "../_mock/runIdentity";
import { runPinsOf } from "../_mock/envModel";
import { REPLAY_HEADER, stubbedToolNames } from "../_mock/replay";
import FromImprovement from "./FromImprovement";
import RunGroupBar from "./RunGroupBar";
import InsightsTab from "./InsightsTab";
import { TraceSheet } from "./TraceWaterfall";
import { runTracingOf, runIssueImprovement } from "../errorFeed/runIssueFix";
import { startImproverSession, useIntegrations } from "../_mock/integrations";
import { useSimStore } from "../store";
import { envAgentKey } from "../_mock/agentRegistry";
import { rereadFor } from "../_mock/improvementPolicy";
import { recordOutsideChange } from "../_mock/nudgeEffects";
import { runReportOf } from "../_mock/runReport";
import RunSummaryChip, { HealthChip } from "./RunSummaryChip";
import { causeOf, outcomeOfCause } from "../_mock/decoupled";
import { regradeOf, regradeRecordOf } from "../_mock/regrade";
import { agentVersions } from "../_mock/versions";
import { scenarioAtVersion } from "../_mock/decoupled";
import MeteredText from "./RunRecord";
import ShareMenu from "./ShareMenu";
import ImprovementRunStrip from "./ImprovementRunStrip";

import { SHOW_INSIGHTS } from "../_mock/featureFlags";
import { nextSnapshotLabel } from "../_mock/envSnapshot";
import { scenarioTitle } from "../_mock/scenarios";

/**
 * Run results.
 *
 * Ordered by what a user does next: how did it go overall, which tasks failed
 * and why, then verify the builder itself, then optimise. Each of
 * those is a tab rather than a separate page so the run stays one object.
 */
/* Re-score a task's re-run eval columns to fresh, deterministic values (seeded
   by the per-eval version bump). Only evals present in `rescore` change; the
   rest of the task is untouched. */
function rescoreTaskEvals(t, rescore) {
  if (!t.evalResults?.length) return t;
  let changed = false;
  const evalResults = t.evalResults.map((r) => {
    const v = rescore[r.id];
    if (!v) return r;
    changed = true;
    let h = 0;
    const key = `${t.id}:${r.id}:${v}`;
    for (let i = 0; i < key.length; i += 1) h = (h * 31 + key.charCodeAt(i)) >>> 0;
    const score = Math.round((0.5 + (h % 50) / 100) * 100) / 100; // 0.50–0.99
    return { ...r, score, passed: score >= (r.threshold ?? 0.8) };
  });
  return changed ? { ...t, evalResults } : t;
}

export default function RunResults({ env, runId, tasks, stats, evals, seed = 7 }) {
  /* Per-eval-column actions (from the column menu): a set of columns the user
     deleted, and a re-score version bump per column that regenerates just that
     column's cell values (no full simulation). */
  const [deletedEvals, setDeletedEvals] = useState(() => new Set());
  const [evalRescore, setEvalRescore] = useState({}); // evalId -> version
  const [rescoringEval, setRescoringEval] = useState(null); // evalId currently re-scoring

  /*
    Adhoc evals: an evaluation applied to a subset of this run's scenarios
    without re-running the calls. New columns appear in the trace table;
    only the scenarios that were selected at the time carry a score, and
    the rest show "—" with a tooltip explaining why.

    Kept as component state — this is a scratchpad on the run view rather
    than a permanent change to the environment. Same reason the "adhoc"
    marker travels on the eval descriptor: it changes how the column reads
    (only these N were scored) but nothing outside this screen.
  */
  const [adhocEvals, setAdhocEvals] = useState([]);
  /* Which scenarios the user had selected when they picked "Run evals on
     these" — captured then, not read from `selected` later, so opening the
     drawer doesn't strand the answer to "on which scenarios?". */
  const [adhocIds, setAdhocIds] = useState([]);
  const [addingAdhocEvals, setAddingAdhocEvals] = useState(false);
  const [rerunAnchor, setRerunAnchor] = useState(null);

  /*
    A run always measures something — a scenario can't have run without
    at least one grader firing against it. But the `evals` prop reflects
    what the env has applied *now*, which is empty for seeded envs (and
    can drift for older runs after evals were added or removed). The
    columns should show what this run actually measured, so we derive
    them from the union of `task.evalResults` and fall back to the env's
    current list only when the tasks carry nothing.
  */
  const shownEvals = useMemo(() => {
    const byId = new Map();
    tasks.forEach((t) => {
      (t.evalResults || []).forEach((r) => {
        if (!byId.has(r.id)) {
          const known = getEval(r.id);
          byId.set(r.id, {
            id: r.id,
            name: r.name || known?.name || r.id,
            icon: known?.icon || "solar:shield-check-linear",
          });
        }
      });
    });
    const all = byId.size ? [...byId.values()] : evals;
    const known = all.filter((e) => !deletedEvals.has(e.id));
    /* Adhoc columns. Fold them in after the known columns, marked with
       adhoc + the set of task ids that were included, so the cell renderer
       can tell an unscored row apart from a missing result. Multiple adhoc
       runs of the same eval merge their coverage. */
    if (adhocEvals.length) {
      const knownIds = new Set(known.map((e) => e.id));
      const byAdhocId = new Map();
      adhocEvals.forEach((ae) => {
        if (knownIds.has(ae.id) || deletedEvals.has(ae.id)) return;
        const existing = byAdhocId.get(ae.id);
        const includedIds = new Set([...(existing?.includedIds || []), ...ae.taskIds]);
        const results = new Map(existing?.results || []);
        Object.entries(ae.results).forEach(([tid, r]) => results.set(tid, r));
        const kn = getEval(ae.id);
        byAdhocId.set(ae.id, {
          id: ae.id,
          name: ae.name || kn?.name || ae.id,
          icon: ae.icon || kn?.icon || "solar:shield-check-linear",
          adhoc: true,
          adhocRanAt: ae.ranAt,
          includedIds,
          results,
        });
      });
      known.push(...byAdhocId.values());
    }
    return known;
  }, [tasks, evals, deletedEvals, adhocEvals]);

  const rerunEvalColumn = (e) => {
    setRescoringEval(e.id);
    setTimeout(() => {
      setEvalRescore((prev) => ({ ...prev, [e.id]: (prev[e.id] || 0) + 1 }));
      setRescoringEval(null);
    }, 900);
  };
  const deleteEvalColumn = (e) => {
    setDeletedEvals((prev) => new Set(prev).add(e.id));
  };
  const navigate = useNavigate();
  const { envId } = useParams();
  const [params, setParams] = useSearchParams();
  /* Opened from an Error Feed issue's fix (View fix, the status chip): back
     goes to that issue, named — on a reload too, while the entry lasts. */
  const location = useLocation();
  /* Kept for the page's life: in-page links (?improve=, tabs) push entries
     without it. */
  const [fromIssue] = useState(() => location.state?.fromIssue || null);
  /*
    A comparison can send scenarios straight here — "these four broke, work out
    why". Landing on the default tab with no memory of what was sent would make
    the reader find them again by hand, so the link carries both the tab and
    the subset.
  */
  /* `optimize` is the old name for this tab; links minted before it became
     it became Optimization runs still carry it. */
  /* The step a diagnosis named, carried into the trace drawer. */
  const [focusStep, setFocusStep] = useState(null);
  /* Fix my agent is a drawer over whatever you were reading, not a tab you
     navigate away to — the diagnosis is about the run still on screen. */
  /* `?improve=OPT-…` lands with the drawer open on that improvement run —
     how the Improvements page links into one. */
  const [fixOpen, setFixOpen] = useState(() => !!params.get("improve"));
  const [fixOptId, setFixOptId] = useState(() => params.get("improve"));
  /* "From Improvement run N" on this run's header links here with
     `?improve=` — the page is already mounted, so open it on the change too. */
  const improveParam = params.get("improve");
  useEffect(() => {
    if (improveParam) { setFixOptId(improveParam); setFixOpen(true); }
  }, [improveParam]);
  const [tab, setTab] = useState(() => {
    /* Insights leads once the run is finished — this screen only shows
       finished runs; a live one stays on the live view. */
    const t = params.get("tab") || (SHOW_INSIGHTS ? "insights" : "tasks");
    /* Insights is hidden for now — its links land on Scenarios. */
    if (t === "insights" && !SHOW_INSIGHTS) return "tasks";
    /* Stale deep-links to removed tabs — the old "Self improvement runs"
       (`optimize`/`omega`), the "Verify" tab, and the now-retired "Trials"
       tab (self-improvement trials are peer runs on the Runs list) — all
       land on Test runs. */
    return t === "optimize" || t === "omega" || t === "verify" || t === "trials" ? "tasks" : t;
  });
  const [replaying, setReplaying] = useState(null);
  const [openTask, setOpenTask] = useState(null);
  /* Which left pane the call drawer opens on — "trace" from Insights. */
  const [openPane, setOpenPane] = useState(null);
  /* A voice run's call drawer is Observe's; its trace opens on its own. */
  const [traceTask, setTraceTask] = useState(null);
  /* Eval the drawer opens on when a score cell was clicked — kept across
     prev/next so stepping through calls stays on the same eval. */
  const [evalFocus, setEvalFocus] = useState(null);
  /*
    Metric-driven drill-down filter set by clicking a bucket in the
    RunMetrics drill-down panel. Shape:
    { kind, value, label, match(task): boolean } | null
  */
  const [bucketFilter, setBucketFilter] = useState(null);
  /*
    Grouping mode for the traces table. Lifted here so the picker can
    live in the SectionCard header where the title used to sit.
  */
  const [groupBy, setGroupBy] = useState("useCase");
  /* Expand all lives in the toolbar: the table reports whether every group
     is open (so the switch is right after opening groups one by one), and
     the switch asks the table to open or close them all. */
  const [allOpen, setAllOpen] = useState(false);
  const [expandCommand, setExpandCommand] = useState(null);
  /*
    Column visibility for the traces table. A Set of column keys the
    user has chosen to show; essentials (Persona, Scenario) render
    regardless. Lifted so the picker sits in the header action slot.
  */
  const [visibleColumns, setVisibleColumns] = useState(() => defaultTraceColumns());
  /*
    Quick status filter chips sitting beside the Columns picker. A fast
    one-click narrowing by outcome (All / Failing / Flaky / Inconclusive /
    Passing), separate from the attribute FilterPanel — the two AND together.
  */
  const [statusChip, setStatusChip] = useState("all");
  /* "Failure sub-goal" and "Failure pattern" are failure-only views — passed
     and inconclusive tasks have no failure bucket, so grouping by them drops
     those tasks entirely. Selecting one of those groupings while the status
     chip asks for Passing/Inconclusive is contradictory (it empties the
     table), so the two controls are reconciled: incompatible chips are
     disabled, and switching to a failure grouping resets an incompatible
     chip back to All. */
  const failureGrouping = groupBy === "subGoal" || groupBy === "pattern";
  const incompatibleChips = failureGrouping ? ["passing", ...NOT_COUNTED_CHIPS] : [];
  const setGroupByReconciled = (g) => {
    setGroupBy(g);
    const failView = g === "subGoal" || g === "pattern";
    if (failView && (statusChip === "passing" || NOT_COUNTED_CHIPS.includes(statusChip))) {
      setStatusChip("all");
    }
  };
  /*
    Attribute filters (Goal, Sub-goal, Status, Failure pattern, Critical).
    Uses the platform's shared FilterPanel component so the popover
    (AI / Basic / Query tabs) reads the same as Evals and Datasets.
    Shape is a flat object of arrays — {goal:[…], subGoal:[…], status:[…],
    pattern:[…], critical:[…]} — the same shape the panel's Basic tab
    emits and the same shape the predicate below reads.
  */
  const [filterAnchor, setFilterAnchor] = useState(null);
  const [filters, setFilters] = useState({});
  const filterCount = Object.values(filters).reduce((a, v) => a + (v?.length || 0), 0);
  const [addingEvals, setAddingEvals] = useState(false);
  const [selected, setSelected] = useState(() => new Set());
  const { envState, patch, addAgentVersion } = useEnvState(envId);
  /* The voice call drawer hides while one opened from it (an eval, a fix) is
     open. Other runs use SideDrawer, which registers itself — registering
     here too put this entry on top and hid that drawer behind itself. */
  const callCovered = useDrawerCovered(!!openTask && effectiveModality(env, envState) === "voice");
  /* Same edit drawer the Evaluations tab uses — the variable mapping and any
     configurable fields live there. Opens when a column is edited from its
     kebab menu. Delegated to the environment's applied evals so a saved edit
     shows up wherever the eval is used. */
  const {
    appliedEvals, update: updateAppliedEval, add: addAppliedEvals, slotsLeft: evalSlotsLeft,
  } = useAppliedEvals(envState, patch);
  const { enqueueSnackbar, closeSnackbar } = useSnackbar();
  const [editingEvalId, setEditingEvalId] = useState(null);
  const editingEval = editingEvalId ? appliedEvals.find((x) => x.id === editingEvalId) : null;
  const editEvalColumn = (e) => {
    /* The header hands us the eval as it sits in the run's results — id,
       name, icon. Look up the applied eval on the environment to get the
       template / custom flags canEditEval reads. */
    const applied = appliedEvals.find((x) => x.id === e.id);
    if (!applied || !canEditEval(applied)) return;
    setEditingEvalId(e.id);
  };

  const { check: checkRunFit, dialog: runFitDialog } = useRunFitCheck(env, envState, patch);
  const { state: simState, commit: simCommit } = useSimStore();
  const integrations = useIntegrations();
  /* Fix on an issue needs trace evidence — hosted agents report every call,
     code agents need traceAI. */
  const tracing = useMemo(() => runTracingOf(envState?.agent, integrations, env?.id), [envState?.agent, integrations, env?.id]);

  /*
    Which run this is. The header said "Run complete" and then printed the raw
    id, so a screen reached from a list of five runs never told you which of
    the five you were looking at — and the id is the one label nobody in the
    list was reading.
  */
  const summaries = runSummaries(env, envState);
  const identity = summaries.find((r) => r.id === runId);

  /*
    Trial detection. A trial id is `${OPT-XXXXX}-t${n}`. When someone lands
    here from the runs list, name the parent search — so the reader always
    knows which self improvement's trial they are inside of, and can open
    the parent to see how this trial ranked against its siblings.
  */
  const trialMeta = useMemo(() => {
    const m = /^(OPT-\d+)-t(\d+)$/.exec(runId || "");
    if (!m) return null;
    const opt = (envState.optimizations || []).find((o) => o.id === m[1]);
    if (!opt) return null;
    const trialN = Number(m[2]);
    const trial = (opt.result?.trials || []).find((t) => t.n === trialN);
    if (!trial) return null;
    const ordinal = (envState.optimizations || []).indexOf(opt) + 1;
    return {
      opt,
      trial,
      ordinal,
      isWinner: opt.result?.winner?.n === trialN,
      winnerTrialN: opt.result?.winner?.n,
    };
  }, [runId, envState.optimizations]);

  /* "agent v3 × env v2", and the improvement that produced it — named the way
     its row on the runs list names it. A trial is read off the same trial
     summary that row is. */
  const named = useMemo(() => {
    const run = trialMeta
      ? trialSummaries(env, envState).find((t) => t.id === runId)
      : identity;
    return run ? runIdentityOf(run, envState, env) : null;
  }, [trialMeta, identity, env, envState, runId]);

  /* Made by an improvement to try a change. Its failures are the
     improvement's to read, so Debug failures isn't offered here. */
  const candidate = !!trialMeta || !!named?.improvement || isCandidateRun({ ...(identity || {}), id: runId }, envState);

  /*
    The run as one record — its release verdict and execution health, what it
    metered, and the digest it sent — read the same way the shared report and
    the completion digest read it. Keyed on the inputs, not the summaries
    (rebuilt every render).
  */
  const report = useMemo(
    () => runReportOf({ env, envState, runId, tasks, summaries, goalOf: deriveUseCaseLabel, integrations }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [env, envState, runId, tasks, integrations],
  );
  /* An improvement's candidate runs don't digest — a search runs dozens. */
  const record = {
    metering: report.metering,
    digest: candidate ? null : report.digest,
    destinations: identity?.digest || report.destinations,
    sentAt: identity?.finishedAt,
  };
  /* Tools the agent was told to stub under the replay flag. */
  const stubbed = stubbedToolNames(env, envState);

  /*
    A result you disagree with is usually a result that was graded on the wrong
    thing. Adding a grader here and running again is the shortest path from
    "that score looks wrong" to a run that measures what you actually meant —
    previously it meant walking back to the Evals step to find out.
  */
  /*
    Re-running with rows ticked used to start a full sweep, which is not what
    the button said and buries the four scenarios someone was actually chasing.
    The subset travels with the run and the run records that it was a subset.
  */
  /*
    A re-run repeats the test it came from — same number of trials — in one of
    two modes (PRD v7 §4.10.8):
      snapshot — "Re-run exact snapshot": the source run's scenario versions,
                 environment version, pins and seed; only the agent version
                 may change. The default for comparisons.
      now      — "Run as configured now": picks up every edit since.
  */
  const sourceRun = (envState.runs || []).find((r) => r.id === runId) || null;
  const canSnapshot = !!sourceRun?.finishedAt && !trialMeta;
  /* A snapshot re-run plays its source's pinned versions — say how many of
     those are older than today's text, so a diff against a fresh run isn't
     read as the agent's doing. Only versions whose text was kept count. */
  const snapshotNote = (() => {
    if (!sourceRun?.snapshotOf || !sourceRun.scenarioVersions) return null;
    const ids = sourceRun.scenarioIds?.length ? new Set(sourceRun.scenarioIds) : null;
    const older = (envState.scenarios || []).filter((r) => (!ids || ids.has(r.id))
      && sourceRun.scenarioVersions[r.id] && scenarioAtVersion(r, sourceRun.scenarioVersions[r.id]) !== r);
    if (!older.length) return null;
    const ordinal = (envState.runs || []).find((r) => r.id === sourceRun.snapshotOf)?.ordinal ?? "—";
    return `${older.length} scenario${older.length === 1 ? "" : "s"} played ${older.length === 1 ? "its" : "their"} text as it was in run ${ordinal}`;
  })();
  const rerun = (ids, { mode = "now", agent = null } = {}) => checkRunFit(() => {
    const url = paths.dashboard.simulate.simulationRun(envId, protoRunId(envId, Date.now().toString(36)));
    const q = new URLSearchParams();
    if (ids?.length) q.set("only", ids.join(","));
    q.set("trials", String(sourceRun?.repeats || 3));
    if (mode === "snapshot" && canSnapshot) {
      q.set("snapshot", sourceRun.id);
      q.set("agent", agent || sourceRun.agentVersion);
      if (sourceRun.envVersion) q.set("env", sourceRun.envVersion);
    }
    navigate(`${url}?${q.toString()}`);
  });
  const [runRerunAnchor, setRunRerunAnchor] = useState(null);
  /* The scenarios that didn't pass — what "Re-run N failing" replays. */
  const failingScenarioIds = useMemo(
    () => [...new Set(tasks.filter((t) => t.status === "failed" || t.status === "flaky").map((t) => t.scenarioId || t.id))],
    [tasks],
  );

  /* Re-grade (AC-12.23): offered when a sub-goal this run used got a grader
     fix, or the eval version moved, since it ran. Priced; starts on a click. */
  const regrade = useMemo(
    () => (trialMeta ? null : regradeOf({ env, envState, run: sourceRun, evals: (sourceRun?.evals || envState.evals || []).map(resolveEval).filter(Boolean) })),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [env, envState, sourceRun, trialMeta],
  );
  const startRegrade = () => {
    if (!regrade || !sourceRun) return;
    const rec = regradeRecordOf(regrade);
    patch({ runs: (envState.runs || []).map((r) => (r.id === sourceRun.id ? { ...r, regradedWith: rec } : r)) });
  };
  const regradedChanged = tasks.filter((t) => t.regraded?.changed).length;

  /* The user's own bar for pass^1 — it colours reports, nothing more. */
  const setReportBar = (pass1) => patch({ reportBar: { ...(envState.reportBar || {}), pass1 } });

  const toggle = (id) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });

  /*
    Options for each filterable field, derived from the tasks in this
    run so the popover only offers choices that will actually match.
    Sub-goal options are unioned across every task's `subTasksFor`
    breakdown so a filter like "touches Verify who is asking" works
    across scenarios.
  */
  const goalOptions = useMemo(() => {
    const s = new Set();
    tasks.forEach((t) => { const g = deriveUseCaseLabel(t); if (g) s.add(g); });
    return [...s].sort();
  }, [tasks]);
  const subGoalOptions = useMemo(() => {
    const s = new Set();
    tasks.forEach((t) => subTasksFor(t, env).forEach((sg) => s.add(sg.label)));
    return [...s].sort();
  }, [tasks, env]);
  const STATUS_LABELS = ["Passed", "Failed", "Inconclusive", "Test invalid", "Error"];
  /* Verdict · cause (§4.10.10), from the run summary. */
  const verdicts = report.summary?.verdicts;
  const causeOfTask = (t) => { const v = verdicts?.get(t.id); return v ? v.label : null; };
  const CAUSE_LABELS = [...new Set(tasks.map(causeOfTask).filter(Boolean))].sort();
  const PATTERN_LABELS = ["Critical rule broken", "Said, not done", "Evaluation failed", "Error", "Other failure"];
  const CRITICAL_LABELS = ["Critical", "Not critical"];

  const filterFields = useMemo(() => [
    { value: "goal",     label: "Goal",            type: "enum", choices: goalOptions },
    { value: "subGoal",  label: "Sub-goal",        type: "enum", choices: subGoalOptions },
    { value: "status",   label: "Status",          type: "enum", choices: STATUS_LABELS },
    { value: "cause",    label: "Verdict cause",   type: "enum", choices: CAUSE_LABELS },
    { value: "pattern",  label: "Failure pattern", type: "enum", choices: PATTERN_LABELS },
    { value: "critical", label: "Criticality",     type: "enum", choices: CRITICAL_LABELS },
    // eslint-disable-next-line react-hooks/exhaustive-deps
  ], [goalOptions, subGoalOptions, CAUSE_LABELS.join("|")]);

  /* Per-task derivations, cached so the filter predicate doesn't
     recompute them when only the selected filter changes. */
  const taskDerivations = useMemo(() => {
    const out = new Map();
    tasks.forEach((t) => {
      const statusLabel = t.status === "passed" ? "Passed"
        : t.status === "unmeasured" ? STATUS_CHIPS.find((c) => c.id === statusBucket(t))?.label || "Inconclusive"
        : t.status === "error" ? "Error"
        : "Failed";
      let patternLabel = null;
      if (t.status !== "passed" && t.status !== "unmeasured") {
        if (t.status === "error") patternLabel = "Error";
        else if (t.critical) patternLabel = "Critical rule broken";
        else if (t.callLog?.unsupportedClaim) patternLabel = "Said, not done";
        else if ((t.evalResults || []).some((r) => !r.passed)) patternLabel = "Evaluation failed";
        else patternLabel = "Other failure";
      }
      out.set(t.id, {
        goal: deriveUseCaseLabel(t),
        subGoalLabels: new Set(subTasksFor(t, env).map((s) => s.label)),
        statusLabel,
        causeLabel: causeOfTask(t),
        patternLabel,
        criticalLabel: t.critical ? "Critical" : "Not critical",
      });
    });
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tasks, env, verdicts]);

  const shown = tasks.filter((t) => {
    /* Metric drill-down predicate — clicking a bucket on the RunMetrics
       chart. Independent of the header filters; they AND together. */
    if (bucketFilter?.match && !bucketFilter.match(t)) return false;
    /* Pulled scenarios sit in their own group above the table — unless a
       claim on Insights asked for them by name. */
    if (t.pulled && !bucketFilter) return false;

    if (statusChip !== "all" && statusBucket(t) !== statusChip) return false;

    const d = taskDerivations.get(t.id);
    if (filters.goal?.length     && !filters.goal.includes(d.goal))         return false;
    if (filters.status?.length   && !filters.status.includes(d.statusLabel)) return false;
    if (filters.pattern?.length  && !filters.pattern.includes(d.patternLabel)) return false;
    if (filters.critical?.length && !filters.critical.includes(d.criticalLabel)) return false;
    if (filters.cause?.length && !filters.cause.includes(d.causeLabel)) return false;
    if (filters.subGoal?.length && !filters.subGoal.some((g) => d.subGoalLabels.has(g))) return false;
    return true;
  });

  /* Table view of the shown rows with any re-run eval columns re-scored to
     fresh values. Only the re-run column's cells change; everything else is
     the original run. */
  const tableShown = useMemo(() => {
    let out = Object.keys(evalRescore).length ? shown.map((t) => rescoreTaskEvals(t, evalRescore)) : shown;
    /* Fold adhoc results into each task's evalResults so aggregates
       (group headers, filters, chart) treat them just like any other
       eval — only the cells that lack a result show the "not part of
       this eval run" tooltip. */
    if (adhocEvals.length) {
      const byTask = new Map(); // taskId -> extra evalResults[]
      adhocEvals.forEach((ae) => {
        const kn = getEval(ae.id);
        ae.taskIds.forEach((tid) => {
          const cell = ae.results?.[tid];
          if (!cell) return;
          const arr = byTask.get(tid) || [];
          arr.push({
            id: ae.id,
            name: ae.name || kn?.name || ae.id,
            color: kn?.color,
            score: cell.score,
            passed: cell.passed,
            reason: cell.reason,
            threshold: cell.threshold,
          });
          byTask.set(tid, arr);
        });
      });
      if (byTask.size) {
        out = out.map((t) => {
          const extras = byTask.get(t.id);
          if (!extras) return t;
          /* Replace an existing adhoc result for the same eval id rather
             than double-counting when the user re-runs the same eval on
             the same row. */
          const seen = new Set(extras.map((r) => r.id));
          const base = (t.evalResults || []).filter((r) => !seen.has(r.id));
          return { ...t, evalResults: [...base, ...extras] };
        });
      }
    }
    return out;
  }, [shown, evalRescore, adhocEvals]);

  /* What will actually render as groups. Failure groupings (sub-goal / pattern)
     have no bucket for passed/unmeasured tasks, so they drop out — used to
     decide between the table and an explanatory empty state. */
  const groupableShown = failureGrouping
    ? shown.filter((t) => t.status !== "passed" && t.status !== "unmeasured")
    : shown;

  /* Counts for the status chips — from the full run so the numbers read as a
     fixed reference the chip filter narrows against, not a moving total. */
  /* Counted within a link's filter (a sub-goal, an issue), so the chips
     describe the rows on screen — not the whole run behind them. */
  const statusCounts = tasks.reduce((c, t) => {
    /* Pulled scenarios are left out of every count. */
    if (t.pulled) return c;
    if (bucketFilter?.match && !bucketFilter.match(t)) return c;
    c.all += 1;
    c[statusBucket(t)] += 1;
    return c;
  }, { all: 0, failing: 0, mixed: 0, inconclusive: 0, invalid: 0, error: 0, passing: 0 });
  const pulledTasks = tasks.filter((t) => t.pulled);

  const applyFilters = (result) => {
    if (!result) { setFilters({}); return; }
    if (Array.isArray(result)) {
      /* Query tab — collapse [{field,operator,value}] into {field:[values]}. */
      const flat = {};
      result.forEach((r) => {
        const values = Array.isArray(r.value) ? r.value : (r.value != null ? [r.value] : []);
        if (!values.length) return;
        flat[r.field] = [...(flat[r.field] || []), ...values];
      });
      setFilters(flat);
    } else {
      /* Basic tab — already {field:[values]}. */
      setFilters(result);
    }
  };

  /* When this run finished, as a relative label ("finished 2m ago"). Falls
     back across timestamp fields and yields null on a missing/bad date so the
     header shows nothing rather than "Invalid Date". */
  const finishedLabel = runFinishedLabel(identity);

  /* ── Insights → the rest of the page ── */
  /* A claim on Insights filters the Scenarios tab to the scenarios behind it. */
  const filterTo = (label, ids) => {
    const idSet = new Set(ids || []);
    setBucketFilter({ label, match: (t) => idSet.has(t.id) });
    setStatusChip("all");
    setTab("tasks");
  };
  /* Open a scenario's trace, pinned to the span where it went wrong. */
  const openTrace = (t) => {
    if (!t) return;
    if (effectiveModality(env, envState) === "voice") { setTraceTask(t); return; }
    setFocusStep(null);
    setEvalFocus(null);
    setOpenPane("trace");
    setOpenTask(t);
  };
  /* ?task=<scenario> (chat's "show me the trace") opens that trace once. */
  const taskParam = params.get("task");
  const openedFromLink = useRef(null);
  useEffect(() => {
    if (!taskParam || openedFromLink.current === taskParam) return;
    const t = tasks.find((x) => x.id === taskParam || x.scenarioId === taskParam);
    if (!t) return;
    openedFromLink.current = taskParam;
    openTrace(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [taskParam, tasks]);
  /* Fixes already started from this run's issues, by issue. */
  const existingFixes = useMemo(() => Object.fromEntries((envState?.optimizations || [])
    .filter((o) => o.trigger?.source === "run-insights" && o.fromRunId === runId)
    .map((o) => [o.trigger.issueId, o])), [envState?.optimizations, runId]);
  /* Fix: the same improvement flow, focused on the issue — opened here. */
  const fixIssue = (issue, cloudAgentId) => {
    /* Re-read the agent first, as the setup dialog does: a change made
       outside Future AGI is saved as a version and becomes the baseline. */
    if (envState?.agent) {
      const reread = rereadFor({ ...envState.agent, key: envState.agent.key || envAgentKey(envState.agent, env.id) }, simState);
      recordOutsideChange(simCommit, simState, { agent: { ...envState.agent, key: envState.agent.key || envAgentKey(envState.agent, env.id) }, reread, activateEnvId: env.id });
    }
    const record = runIssueImprovement({
      env, es: envState, state: simState, run: identity || { id: runId }, tasks, issue,
    });
    patch({ optimizations: [...(envState?.optimizations || []), record] });
    if (cloudAgentId) startImproverSession({ agentId: cloudAgentId, envId: env.id, optId: record.id, where: "future-agi" });
    setFixOptId(record.id);
    setFixOpen(true);
  };

  return (
    <Box sx={{ display: "flex", flexDirection: "column", height: "100%", minHeight: 0 }}>
      {runFitDialog}
      {/* Its environment was built for an Error Feed issue's Fix: the way back. */}
      <IssueReturnBanner envId={env?.id} />
      {/* ── header ── */}
      <Stack
        direction="row" alignItems="center" spacing={2}
        sx={{ px: 3, py: 2, borderBottom: "1px solid", borderColor: "divider", flexShrink: 0 }}
      >
        {fromIssue ? (
          <Button
            size="small"
            onClick={() => navigate(paths.dashboard.errorFeed.detail(fromIssue))}
            startIcon={<Iconify icon="solar:alt-arrow-left-linear" width={16} />}
            sx={{ typography: "s2", fontWeight: 600, color: "text.secondary", fontFamily: "ui-monospace, Menlo, monospace", flexShrink: 0, minWidth: 0 }}
          >
            {fromIssue}
          </Button>
        ) : (
        <IconButton
          size="small"
          onClick={() => {
            /* Return to whichever surface opened this run — Improvements L2,
               the env's own Runs tab, or the compare page — instead of
               hard-forcing the env workspace. Falls back on cold-load. */
            if (window.history.length > 1) navigate(-1);
            else navigate(paths.dashboard.simulate.environmentStep(envId, "runs"));
          }}
        >
          <Iconify icon="solar:alt-arrow-left-linear" width={18} sx={{ color: "text.subtitle" }} />
        </IconButton>
        )}
        <Box flex={1} minWidth={0}>
          <Stack direction="row" alignItems="center" spacing={1}>
            {trialMeta ? (
              <Box
                sx={{
                  width: 24, height: 24, borderRadius: 0.875, flexShrink: 0,
                  display: "grid", placeItems: "center",
                  typography: "s3", fontWeight: 700, color: "text.secondary",
                  bgcolor: "background.neutral",
                }}
              >
                <Iconify icon="solar:magic-stick-3-bold" width={13} />
              </Box>
            ) : identity && (
              <Box
                sx={{
                  width: 24, height: 24, borderRadius: 0.875, flexShrink: 0,
                  display: "grid", placeItems: "center",
                  typography: "s3", fontWeight: 700, color: textTone(identity.color),
                  bgcolor: (t) => alpha(identity.color, t.palette.mode === "dark" ? 0.22 : 0.14),
                }}
              >
                {identity.letter}
              </Box>
            )}
            <Typography noWrap sx={{ typography: "s1_2", fontWeight: 700 }}>
              {trialMeta
                ? `Run ${trialMeta.trial.n}${named ? ` · ${named.label}` : ""}`
                : identity
                  ? `Run ${identity.ordinal} · ${named.label}`
                  : "Run complete"}
            </Typography>
            {/*
              Failed means the run failed, not that something in it did. All
              seven scenarios failing is a failed run; three of seven failing
              is a completed run with findings, and calling that "Failed"
              buries the three that passed.
            */}
            <StatusChip status={stats.passed === 0 ? "failed" : stats.failed === 0 ? "passed" : "completed"} />
            {/* Two indicators, apart: how the agent did (a report — pass^1,
                pass^k — never a gate), and did our harness do its job. */}
            <RunSummaryChip summary={report.summary} />
            {/* The harness only earns a chip when it wasn't healthy; Insights
                carries its full read either way. */}
            {report.summary?.health && report.summary.health.label !== "Healthy" && <HealthChip health={report.summary.health} />}
            {sourceRun?.snapshotOf && (
              <Tooltip arrow title="Re-ran the exact snapshot of an earlier run: the same scenario versions, environment version, pins and seed.">
                <Typography noWrap sx={{ typography: "s3", color: "text.subtitle", cursor: "default" }}>
                  {`Snapshot of run ${(envState.runs || []).find((r) => r.id === sourceRun.snapshotOf)?.ordinal ?? "—"}`}
                </Typography>
              </Tooltip>
            )}
            {/* A candidate taken from a code snapshot — a commit or an uploaded archive. */}
            {sourceRun?.candidateSource?.label && (
              <Tooltip arrow title={sourceRun.candidateSource.detail || ""}>
                <Typography noWrap sx={{ typography: "s3", color: "text.subtitle", cursor: "default" }}>
                  {sourceRun.candidateSource.label}
                </Typography>
              </Tooltip>
            )}
            {snapshotNote && (
              <Tooltip arrow title="These scenarios were edited after the source run. The snapshot replayed the versions it pinned; Run as configured now plays today's text.">
                <Typography noWrap sx={{ typography: "s3", color: "text.subtitle", cursor: "default" }}>
                  {`· ${snapshotNote}`}
                </Typography>
              </Tooltip>
            )}
          </Stack>
          <Typography noWrap sx={{ typography: "s2", color: "text.subtitle" }}>
            {/* A run is one agent version against one environment version,
                each scenario played `repeats` times — the context every
                result on this page compares within. */}
            {env.name}
            {named?.improvement && <>{" · "}<FromImprovement improvement={named.improvement} /></>}
            {` · ${stats.total} scenario${stats.total === 1 ? "" : "s"}`}
            {identity?.repeats > 1 ? ` × ${identity.repeats} repeats` : ""}
            {finishedLabel ? ` · ${finishedLabel}` : ""}
            {/* Sealed · Live · Prompt only — a word here; its meaning on hover. */}
            {report.summary?.proofKind?.label && (
              <>
                {" · "}
                <Tooltip arrow placement="bottom-start" title={report.summary.proofKind.note || ""}>
                  <Box component="span" sx={{ cursor: "default" }}>{report.summary.proofKind.kind === "live" ? "Live · real systems" : report.summary.proofKind.label}</Box>
                </Tooltip>
              </>
            )}
            {/* What it metered, who placed the calls, which rule graded it and
                the replay header — one hover, not five clauses. */}
            <Tooltip
              arrow placement="bottom-start"
              slotProps={{ tooltip: { sx: { maxWidth: 420 } } }}
              title={(
                <Box sx={{ py: 0.25, "& > div": { py: 0.125 } }}>
                  <div>{report.metering?.line ? <MeteredText metering={report.metering} /> : "Nothing metered"}</div>
                  <div>{`Caller ${runPinsOf(identity, env, envState).simulator}`}</div>
                  {identity?.secrets && Object.keys(identity.secrets).length > 0 && (
                    <div>{`Secrets · ${Object.keys(identity.secrets).join(", ")} — the references set when it ran`}</div>
                  )}
                  {report.summary?.grading && <div>{report.summary.grading.rule >= 2 ? "Sub-goals decide pass or fail" : report.summary.grading.label}</div>}
                  <div>{stubbed.length ? `Replay header on every call · stubbed: ${stubbed.join(", ")}` : `Replay header on every call (${REPLAY_HEADER})`}</div>
                </Box>
              )}
            >
              <Box component="span" sx={{ display: "inline-flex", verticalAlign: "middle", ml: 0.75, cursor: "default" }}>
                <Iconify icon="solar:info-circle-linear" width={14} sx={{ color: "text.subtitle" }} />
              </Box>
            </Tooltip>
          </Typography>
        </Box>
        <Button
          variant="outlined" size="small"
          onClick={() => setAddingEvals(true)}
          startIcon={<Iconify icon="solar:add-circle-linear" width={15} />}
          sx={{ color: "text.primary", borderColor: "divider", typography: "s2", fontWeight: 600 }}
        >
          Add evals
        </Button>
        {/* Share replaces the old Export: a read-only link, or the report as PDF. */}
        {/* An improvement's run is shared from its improvement, not on its own. */}
        {identity && !trialMeta && (
          <ShareMenu env={env} envState={envState} identity={identity} report={report} />
        )}
        {/* Re-run the whole run, in one of the two modes (§4.10.8). Selecting
            rows offers the same modes for just those scenarios. */}
        {!candidate && !trialMeta && (failingScenarioIds.length ? (
          /* What a reader re-runs after a failed run is the failures — one
             click; the whole-run modes sit behind the caret. */
          <Stack direction="row" sx={{ flexShrink: 0 }}>
            <Button
              variant="outlined" size="small"
              onClick={() => rerun(failingScenarioIds, { mode: canSnapshot ? "snapshot" : "now" })}
              startIcon={<Iconify icon="solar:refresh-linear" width={15} />}
              sx={{ color: "text.primary", borderColor: "divider", typography: "s2", fontWeight: 600, borderTopRightRadius: 0, borderBottomRightRadius: 0 }}
            >
              {`Re-run ${failingScenarioIds.length} failing`}
            </Button>
            <Button
              variant="outlined" size="small" aria-label="More re-run options"
              onClick={(e) => setRunRerunAnchor(e.currentTarget)}
              sx={{ minWidth: 0, px: 0.75, color: "text.subtitle", borderColor: "divider", borderLeft: "none", borderTopLeftRadius: 0, borderBottomLeftRadius: 0 }}
            >
              <Iconify icon="solar:alt-arrow-down-linear" width={12} />
            </Button>
          </Stack>
        ) : (
          <Button
            variant="outlined" size="small"
            onClick={(e) => setRunRerunAnchor(e.currentTarget)}
            startIcon={<Iconify icon="solar:refresh-linear" width={15} />}
            endIcon={<Iconify icon="solar:alt-arrow-down-linear" width={12} sx={{ color: "text.subtitle" }} />}
            sx={{ color: "text.primary", borderColor: "divider", typography: "s2", fontWeight: 600 }}
          >
            Re-run
          </Button>
        ))}
        {/*
          The primary action after a failed run is not to run it again —
          the same agent against the same graders returns the same
          result. It is to summarise what went wrong so the reader can
          change the agent. Trial pages open the same drawer against the
          trial's own outcomes.
        */}
        {candidate ? (
          /* Reads as unavailable rather than vanishing, so the reason — and
             the way to the improvement that owns this run — is findable. */
          <Tooltip
            arrow
            title={(
              <>
                Candidate runs aren&apos;t analysed — open the improvement instead
                {named?.improvement?.href && (
                  <Box
                    component="span"
                    {...clickable(() => navigate(named.improvement.href), { role: "link" })}
                    sx={{ display: "block", mt: 0.5, fontWeight: 700, cursor: "pointer", textDecoration: "underline", ...focusRing }}
                  >
                    {`Open ${named.improvement.name}`}
                  </Box>
                )}
              </>
            )}
          >
            <span>
              <Button
                variant="contained" color="primary" size="small" disabled
                startIcon={<Iconify icon="solar:magnifer-linear" width={15} />}
                sx={{ typography: "s2", fontWeight: 700 }}
              >
                Debug failures
              </Button>
            </span>
          </Tooltip>
        ) : (
          <Button
            variant="contained" color="primary" size="small"
            startIcon={<Iconify icon="solar:magnifer-linear" width={15} />}
            onClick={() => { setFixOptId(null); setFixOpen(true); }}
            sx={{ typography: "s2", fontWeight: 700 }}
          >
            Debug failures
          </Button>
        )}
      </Stack>

      {/* ── the run group it was started with, if any ── */}
      <RunGroupBar envId={envId} runId={runId} />

      {/* ── re-grade: a grader got fixed since this run (AC-12.23, AC-16.11) ── */}
      {(regrade || sourceRun?.regradedWith) && (
        <Stack
          direction="row" alignItems="center" spacing={1.25}
          sx={{ px: 3, py: 0.875, borderBottom: "1px solid", borderColor: "divider", flexShrink: 0 }}
        >
          <Iconify icon="solar:restart-linear" width={15} sx={{ color: "text.subtitle", flexShrink: 0 }} />
          <Typography sx={{ typography: "s2", color: "text.secondary", flex: 1, minWidth: 0 }}>
            {regrade ? (
              <>
                {[
                  regrade.subGoals.length ? `${regrade.subGoals.length === 1 ? "A sub-goal" : `${regrade.subGoals.length} sub-goals`} this run used got a grader fix` : null,
                  regrade.evalTo ? `evals moved ${regrade.evalFrom} → ${regrade.evalTo}` : null,
                ].filter(Boolean).join(" and ")}
                {` since it ran. Re-grading reads the stored traces and end-states again — ${regrade.scenarioIds.length} scenarios, no new calls.`}
              </>
            ) : (
              `Re-graded with ${sourceRun.regradedWith.label} · ${regradedChanged} ${regradedChanged === 1 ? "verdict" : "verdicts"} changed under the newer graders.`
            )}
          </Typography>
          {regrade && (
            <Button
              size="small" variant="outlined" onClick={startRegrade}
              sx={{ color: "text.primary", borderColor: "divider", typography: "s2", fontWeight: 600, flexShrink: 0, whiteSpace: "nowrap" }}
            >
              {`${regrade.label} — ≈ ${regrade.credits.toLocaleString()} credits`}
            </Button>
          )}
        </Stack>
      )}

      {/* A run an improvement made: kept or not, and what it did vs where it started. */}
      <ImprovementRunStrip env={env} envState={envState} runId={runId} onOpenSearch={() => { setFixOptId(improvementOf({ id: runId }, envState)?.record?.id || null); setFixOpen(true); }} />

      <Box sx={{ flex: 1, minHeight: 0, overflow: "auto" }}>
        <Box sx={{ p: 2, pt: 0.5 }}>
          {/* ── tabs ── */}
          <CustomTabs
            value={tab}
            onChange={(_, v) => setTab(v)}
            sx={{ borderBottom: "1px solid", borderColor: "divider", mb: 2, minHeight: 38 }}
          >
            {SHOW_INSIGHTS && <Tab value="insights" label="Insights" sx={{ minHeight: 38 }} />}
            <Tab value="tasks" label={`Scenarios (${stats.total})`} sx={{ minHeight: 38 }} />
            <Tab value="analytics" label="Analytics" sx={{ minHeight: 38 }} />
          </CustomTabs>

          {/* Wraps both tab bodies so it survives a tab switch: errors thrown
              while one tab unmounts land here, not on the app-root boundary. */}
          <ErrorBoundary
            resetKeys={[tab]}
            onError={(error, info) => console.error("[RunResults tab]", error, info?.componentStack)}
            fallbackRender={({ error, resetErrorBoundary }) => (
              <SectionCard sx={{ p: 3 }}>
                <Stack spacing={1.5}>
                  <Typography sx={{ typography: "s1", fontWeight: 700 }}>This tab failed to render</Typography>
                  <Typography sx={{ typography: "s2", color: "text.subtitle", fontFamily: "monospace", whiteSpace: "pre-wrap" }}>
                    {String(error?.stack || error?.message || error).split("\n").slice(0, 4).join("\n")}
                  </Typography>
                  <Button size="small" variant="outlined" onClick={resetErrorBoundary} sx={{ alignSelf: "flex-start" }}>Retry</Button>
                </Stack>
              </SectionCard>
            )}
          >
          {tab === "tasks" && (
            <SectionCard
              /*
                Grow to fill the remaining vertical space so the traces
                card reaches the bottom of the viewport rather than
                sizing to its rows. Values tuned so the card starts
                just below the tab strip and ends at the viewport edge.
              */
              /* The traces card fills the viewport below the tab strip and
                 the rows scroll INSIDE it — so the toolbar (Group by, Filter,
                 Clear, Re-run N…) and the column headers stay put while the
                 reader walks the rows. `overflow: hidden` on the outer box
                 traps the row scroll inside, and the flex column lets the
                 table area grow to fill what's left after the toolbar. */
              sx={{
                height: "calc(100vh - 152px)",
                display: "flex", flexDirection: "column",
              }}
              /*
                The title used to be "Task traces" plus a one-liner
                subtitle. The header is now the two table controls: the
                Group by picker sits on the left in place of the title
                and reads as the primary axis; the Columns picker sits
                on the right in the action slot. Selection actions
                (Clear / Re-run N) override both when rows are ticked
                — that's the mode change and it should occupy the
                whole header.
              */
              title={
                selected.size ? (
                  <Typography sx={{ typography: "s1", fontWeight: 600 }}>
                    {selected.size} selected
                  </Typography>
                ) : (
                  <Stack direction="row" alignItems="center" spacing={1.25} sx={{ minWidth: 0, "& > *:not(:last-child)": { flexShrink: 0 } }}>
                    <TraceGroupByPicker value={groupBy} onChange={setGroupByReconciled} />
                    {groupBy !== "none" && (
                      <Stack
                        component="label" direction="row" alignItems="center" spacing={0.5}
                        sx={{ cursor: "pointer", pr: 0.5, userSelect: "none" }}
                      >
                        <Switch
                          size="small"
                          checked={allOpen}
                          onChange={(e) => setExpandCommand({ open: e.target.checked, at: Date.now() })}
                        />
                        <Typography sx={{ typography: "s2", fontWeight: 600, whiteSpace: "nowrap" }}>Expand all</Typography>
                      </Stack>
                    )}
                    {/*
                      Filter button — same shape as Group by / Columns
                      so the three read as a single control row. Opens
                      the shared platform FilterPanel popover so the
                      Basic / Query / AI tabs and their chrome match
                      Evals and Datasets.
                    */}
                    <Button
                      size="small" variant="outlined"
                      onClick={(e) => setFilterAnchor(e.currentTarget)}
                      startIcon={
                        <Iconify
                          icon="mage:filter"
                          width={15}
                          sx={{ color: filterCount ? "primary.main" : "text.subtitle" }}
                        />
                      }
                      endIcon={<Iconify icon="solar:alt-arrow-down-linear" width={12} sx={{ color: "text.subtitle" }} />}
                      sx={{
                        typography: "s2", fontWeight: 700, textTransform: "none",
                        height: 32,
                        color: "text.primary",
                        borderColor: "divider",
                        "&:hover": { borderColor: "text.disabled", bgcolor: "transparent" },
                      }}
                    >
                      Filter
                      {filterCount > 0 && (
                        <>
                          <Box component="span" sx={{ mx: 0.5, color: "text.subtitle", fontWeight: 400 }}>·</Box>
                          <Box component="span" sx={{ color: "primary.main" }}>{filterCount}</Box>
                        </>
                      )}
                    </Button>
                    {/* The filter a link applied (a sub-goal, an issue, a count):
                        one chip the height of the buttons, truncated — the full
                        label on hover — so a long name never reflows the bar. */}
                    {bucketFilter && (
                      <Tooltip arrow title={`Filtered · ${bucketFilter.label}`}>
                        <Stack
                          direction="row" alignItems="center" spacing={0.75}
                          sx={{
                            height: 32, pl: 1, pr: 0.5, borderRadius: 0.75, minWidth: 0, maxWidth: 360,
                            border: "1px solid", borderColor: (t) => alpha(t.palette.primary.main, 0.4),
                            bgcolor: (t) => alpha(t.palette.primary.main, t.palette.mode === "dark" ? 0.12 : 0.06),
                          }}
                        >
                          <Iconify icon="solar:filter-linear" width={13} sx={{ color: "primary.main", flexShrink: 0 }} />
                          <Typography noWrap sx={{ typography: "s2", fontWeight: 600, color: "primary.main", minWidth: 0 }}>
                            {bucketFilter.label}
                          </Typography>
                          <IconButton size="small" aria-label="Clear filter" onClick={() => setBucketFilter(null)} sx={{ p: 0.25, flexShrink: 0 }}>
                            <Iconify icon="eva:close-fill" width={15} sx={{ color: "text.subtitle" }} />
                          </IconButton>
                        </Stack>
                      </Tooltip>
                    )}
                  </Stack>
                )
              }
              action={
                selected.size ? (
                  <Stack direction="row" spacing={1}>
                    <Button
                      size="small"
                      onClick={() => setSelected(new Set())}
                      sx={{ typography: "s2", fontWeight: 600, color: "text.secondary" }}
                    >
                      Clear
                    </Button>
                    {/* Re-run opens a small popover with two choices: rerun
                        the calls (a new simulation), or score these scenarios
                        against new evals without re-running the calls. The
                        second one is the cheap version — no LLM cost — and it
                        is the one people reach for when they added a grader
                        after the fact. */}
                    <Button
                      variant="contained" color="primary" size="small"
                      onClick={(e) => setRerunAnchor(e.currentTarget)}
                      startIcon={<Iconify icon="solar:refresh-bold" width={15} />}
                      endIcon={<Iconify icon="solar:alt-arrow-down-linear" width={12} />}
                      sx={{ typography: "s2", fontWeight: 700 }}
                    >
                      Re-run {selected.size}
                    </Button>
                  </Stack>
                ) : (
                  <Stack direction="row" alignItems="center" spacing={1.5}>
                    <StatusFilterChips value={statusChip} counts={statusCounts} onChange={setStatusChip} blocked={incompatibleChips} />
                    <TraceColumnsPicker value={visibleColumns} onChange={setVisibleColumns} evals={shownEvals} />
                  </Stack>
                )
              }
            >
              {/* A failure grouping (sub-goal / pattern) drops passed and
                  unmeasured tasks — with those filtered in and nothing failing,
                  `shown` is non-empty but zero groups render. Detect that and
                  show a real empty state instead of a blank table. */}
              {/* Test invalid twice: pulled from this run, flagged for review,
                  and left out of every count — a small group of its own. */}
              {pulledTasks.length > 0 && !bucketFilter && (
                <PulledGroup tasks={pulledTasks} envId={envId} />
              )}
              {groupableShown.length === 0 ? (
                <EmptyState
                  icon="solar:filter-linear"
                  title={failureGrouping && shown.length > 0
                    ? "Nothing to group here"
                    : "No scenarios match that filter"}
                  body={failureGrouping && shown.length > 0
                    ? "This grouping only shows failing runs — none of the matching runs failed. Switch the status filter or group by Goal."
                    : undefined}
                />
              ) : (
                <TraceTable
                  tasks={verdicts ? tableShown.map((t) => ({ ...t, verdictCause: verdicts.get(t.id) || null })) : tableShown}
                  evals={shownEvals}
                  selected={selected}
                  groupBy={groupBy}
                  columns={visibleColumns}
                  env={env}
                  onToggle={toggle}
                  onToggleAll={() =>
                    setSelected((prev) =>
                      shown.every((t) => prev.has(t.id)) ? new Set() : new Set(shown.map((t) => t.id)))
                  }
                  onOpen={(t) => { setEvalFocus(null); setOpenTask(t); }}
                  onOpenTrace={openTrace}
                  onOpenEval={(t, r) => { setEvalFocus(r.name); setOpenTask(t); }}
                  onRerunEval={rerunEvalColumn}
                  onDeleteEval={deleteEvalColumn}
                  onEditEval={editEvalColumn}
                  rescoringEvalId={rescoringEval}
                  expandCommand={expandCommand}
                  onAllOpenChange={setAllOpen}
                  gradingBasis={report.summary?.grading?.basis || null}
                />
              )}
            </SectionCard>
          )}

          {SHOW_INSIGHTS && tab === "insights" && (
            <InsightsTab
              env={env}
              envState={envState}
              tasks={tasks}
              run={identity || { id: runId }}
              history={summaries}
              goalOf={deriveUseCaseLabel}
              tracing={tracing}
              candidate={candidate}
              existingFixes={existingFixes}
              insights={report.insights}
              summary={report.summary}
              onSetBar={setReportBar}
              record={record}
              onFilter={filterTo}
              onOpenTrace={openTrace}
              onFix={fixIssue}
              onOpenFix={(optId) => { setFixOptId(optId); setFixOpen(true); }}
            />
          )}

          {tab === "analytics" && (
            <RunAnalytics tasks={tasks} evals={shownEvals} env={env} stats={stats} runHistory={summaries} currentRunId={runId} />
          )}
          </ErrorBoundary>


          <ReplayDrawer task={replaying} seed={seed} onClose={() => setReplaying(null)} />
        </Box>
      </Box>

      {/*
        Shared platform filter popover. Mounted at the RunResults root
        so the popover's positioning frame is not affected by the
        scrolling body below.
      */}
      <FilterPanel
        anchorEl={filterAnchor}
        open={!!filterAnchor}
        onClose={() => setFilterAnchor(null)}
        filterFields={filterFields}
        currentFilters={filters}
        onApply={applyFilters}
        aiPlaceholder="Ask AI — e.g. 'show adversarial traces that failed identity'"
      />

      {/*
        Adding a grader applies to the environment, so it holds for every run
        from here — and the run that just finished was not graded on it, which
        is why this offers the re-run rather than silently changing the numbers
        above.
      */}
      {editingEval && (
        <EditEvalDrawer
          item={editingEval}
          env={env}
          envState={envState}
          onClose={() => setEditingEvalId(null)}
          onSave={(changes) => updateAppliedEval(editingEval.id, changes)}
        />
      )}

      <AddEvalsDrawer
        open={addingEvals}
        onClose={() => setAddingEvals(false)}
        env={env}
        envState={envState}
        existingIds={envState.evals}
        slotsLeft={evalSlotsLeft}
        onAdd={(added) => {
          /* Through the environment's own evals: the change is recorded (a
             new environment version once runs exist) and capped at 8. The
             run on screen wasn't graded on it — a re-run is offered, never
             started for the user. */
          const nextLabel = (envState.runs || []).length ? nextSnapshotLabel(env, envState) : null;
          const { added: n, dropped } = addAppliedEvals(added);
          setAddingEvals(false);
          if (!n) {
            enqueueSnackbar(dropped ? "No room — an environment holds 8 evaluations at most" : "Already applied", { variant: "info" });
            return;
          }
          enqueueSnackbar(
            [
              `Added ${n} evaluation${n === 1 ? "" : "s"}${nextLabel ? ` · environment ${nextLabel}` : ""}`,
              dropped ? `${dropped} left out — 8 at most` : null,
              "this run wasn't graded on it",
            ].filter(Boolean).join(" · "),
            {
              variant: "success",
              autoHideDuration: 10000,
              action: (key) => (
                <Button size="small" color="inherit" onClick={() => { closeSnackbar(key); rerun(); }} sx={{ fontWeight: 700 }}>
                  Re-run
                </Button>
              ),
            },
          );
        }}
      />

      {/*
        Adhoc evals — pick graders to score just the scenarios that were
        selected on the run screen. The picker filters out evals that are
        already columns (there's no point re-scoring a column that has
        every row filled), and adding them stays local to this run.
      */}
      <AddEvalsDrawer
        open={addingAdhocEvals}
        onClose={() => setAddingAdhocEvals(false)}
        env={env}
        envState={envState}
        existingIds={shownEvals.map((e) => e.id)}
        onAdd={(added) => {
          const now = new Date().toISOString();
          const additions = added.map((ev) => {
            const known = getEval(ev.id);
            const results = {};
            adhocIds.forEach((tid) => {
              /* Deterministic per (task, eval, run) so the numbers don't
                 shuffle on every re-render but do refresh across separate
                 adhoc runs of the same eval. */
              let h = 0;
              const key = `${tid}:${ev.id}:${now}`;
              for (let i = 0; i < key.length; i += 1) h = (h * 31 + key.charCodeAt(i)) >>> 0;
              const score = Math.round((0.55 + (h % 45) / 100) * 100) / 100;
              const threshold = 0.8;
              results[tid] = {
                score,
                passed: score >= threshold,
                reason: score >= threshold
                  ? "Met the configured threshold."
                  : `Scored ${Math.round(score * 100)} against a threshold of ${Math.round(threshold * 100)}.`,
                threshold,
              };
            });
            return {
              id: ev.id,
              name: ev.name || known?.name || ev.id,
              icon: ev.icon || known?.icon,
              ranAt: now,
              taskIds: adhocIds.slice(),
              results,
            };
          });
          setAdhocEvals((prev) => [...prev, ...additions]);
          setAddingAdhocEvals(false);
          setSelected(new Set());
          setAdhocIds([]);
        }}
      />

      {/* ── re-run chooser ── */}
      <Popover
        open={!!rerunAnchor}
        anchorEl={rerunAnchor}
        onClose={() => setRerunAnchor(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
        slotProps={{ paper: { sx: { mt: 0.75, minWidth: 340, borderRadius: 1.25 } } }}
      >
        <Box sx={{ px: 2, pt: 1.75, pb: 1 }}>
          <Typography sx={{ typography: "s3", fontWeight: 700, color: "text.subtitle", textTransform: "uppercase", letterSpacing: 0.4 }}>
            Re-run {selected.size} scenario{selected.size === 1 ? "" : "s"}
          </Typography>
        </Box>
        <RerunModes
          run={sourceRun}
          canSnapshot={canSnapshot}
          envLabel={sourceRun ? runIdentityOf(sourceRun, envState, env).env : null}
          agents={agentVersions(envState).map((v) => v.label)}
          onRun={(mode, agent) => {
            const ids = [...selected];
            setRerunAnchor(null);
            rerun(ids, { mode, agent });
          }}
        />
        <MenuItem
          onClick={() => {
            setAdhocIds([...selected]);
            setRerunAnchor(null);
            setAddingAdhocEvals(true);
          }}
          sx={{ alignItems: "flex-start", gap: 0.75, py: 1.25 }}
        >
          <ListItemIcon sx={{ minWidth: "0 !important", mt: "3px" }}>
            <Iconify icon="solar:checklist-minimalistic-linear" width={16} sx={{ color: "text.primary" }} />
          </ListItemIcon>
          <ListItemText
            primary="Run evals on these scenarios"
            secondary="No calls are made — pick graders and score just these rows. Adds columns to this table."
            primaryTypographyProps={{ typography: "s2", fontWeight: 700 }}
            secondaryTypographyProps={{ typography: "s3", color: "text.subtitle", sx: { whiteSpace: "normal" } }}
          />
        </MenuItem>
      </Popover>

      {/* ── re-run the whole run ── */}
      <Popover
        open={!!runRerunAnchor}
        anchorEl={runRerunAnchor}
        onClose={() => setRunRerunAnchor(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
        slotProps={{ paper: { sx: { mt: 0.75, width: 380, borderRadius: 1.25 } } }}
      >
        <Box sx={{ px: 2, pt: 1.75, pb: 1 }}>
          <Typography sx={{ typography: "s3", fontWeight: 700, color: "text.subtitle", textTransform: "uppercase", letterSpacing: 0.4 }}>
            {`Re-run ${identity ? `run ${identity.ordinal}` : "this run"} · ${stats.total} scenario${stats.total === 1 ? "" : "s"}`}
          </Typography>
        </Box>
        <RerunModes
          run={sourceRun}
          canSnapshot={canSnapshot}
          envLabel={sourceRun ? runIdentityOf(sourceRun, envState, env).env : null}
          agents={agentVersions(envState).map((v) => v.label)}
          onRun={(mode, agent) => {
            setRunRerunAnchor(null);
            rerun(sourceRun?.partial ? sourceRun.scenarioIds : null, { mode, agent });
          }}
        />
      </Popover>

      {/* ── trace drawer ── */}
      {/*
        Wide, because it is two panes: the call on the left and what was
        measured about it on the right. Prev/next walk the filtered list, so
        reading four failures in a row does not mean closing and reopening.
      */}
      {/* ── fix my agent ── */}
      <FixMyAgentDrawer
        open={fixOpen}
        onClose={() => {
          setFixOpen(false);
          setFixOptId(null);
          /* Drop ?improve so "From Improvement run N" opens it again, and Back
             doesn't stop on the same run with the drawer shut. */
          if (params.get("improve")) {
            const next = new URLSearchParams(params);
            next.delete("improve");
            setParams(next, { replace: true, state: fromIssue ? { fromIssue } : location.state });
          }
        }}
        openOptimizationId={fixOptId}
        env={env}
        envState={envState}
        patch={patch}
        addAgentVersion={addAgentVersion}
        tasks={tasks}
        stats={stats}
        runId={runId}
        onOpenTask={(t, step) => {
          setFocusStep(step || null);
          setOpenTask(t);
        }}
        onViewIssue={(label, ids) => {
          /* Filter the trace table to the exact task ids the finding
             addresses and jump to Test runs — but keep the debug drawer
             open so the user can compare the finding against the calls
             side-by-side without having to reopen it. */
          const idSet = new Set(ids);
          setBucketFilter({ label, match: (t) => idSet.has(t.id) });
          setTab("tasks");
        }}
      />

      {(() => {
        const closeTask = () => { setOpenTask(null); setFocusStep(null); setEvalFocus(null); setOpenPane(null); };
        const idx = openTask ? shown.findIndex((t) => t.id === openTask.id) : -1;
        const goPrev = idx > 0 ? () => setOpenTask(shown[idx - 1]) : undefined;
        const goNext = idx > -1 && idx < shown.length - 1 ? () => setOpenTask(shown[idx + 1]) : undefined;

        /* A voice run gets the exact drawer the Observe screen uses for a
           voice call — the real product component, fed a `data` shape by the
           adapter, not a bespoke rebuild. Every other surface keeps the
           existing call drawer. */
        if (effectiveModality(env, envState) === "voice") {
          const traceIdx = traceTask ? shown.findIndex((t) => t.id === traceTask.id) : -1;
          return (
            <>
            {/* The trace on its own — opened from Insights at the offending span. */}
            <SideDrawer open={!!traceTask} onClose={() => setTraceTask(null)} width={{ xs: "100%", md: 980 }}>
              {traceTask && (
                <TraceSheet
                  task={traceTask}
                  env={env}
                  envState={envState}
                  onClose={() => setTraceTask(null)}
                  onPrev={traceIdx > 0 ? () => setTraceTask(shown[traceIdx - 1]) : undefined}
                  onNext={traceIdx > -1 && traceIdx < shown.length - 1 ? () => setTraceTask(shown[traceIdx + 1]) : undefined}
                />
              )}
            </SideDrawer>
            <Drawer
              anchor="right"
              open={!!openTask}
              onClose={closeTask}
              ModalProps={{ BackdropProps: { style: { backgroundColor: "transparent" } } }}
              sx={{
                ...coveredDrawerSx(callCovered),
                zIndex: (t) => t.zIndex.modal,
                "&& .MuiDrawer-paper": {
                  width: "auto",
                  maxWidth: "100vw",
                  bgcolor: "background.paper",
                  backgroundImage: "none",
                  overflow: "hidden",
                  boxShadow: "-10px 0px 100px #00000035",
                },
              }}
            >
              {openTask && (
                <VoiceDetailDrawerV2
                  key={`${openTask.id}-${evalFocus || ""}`}
                  /* The verdict and its cause (§4.10.10) ride in the real
                     drawer's Status chip — "Failed · agent". */
                  data={{
                    ...taskToVoiceData(openTask, {
                      env,
                      voice: true,
                      /* The Graph tab draws the scenario's path and where the call left it. */
                      scenario: (envState.scenarios || []).find((sc) => sc.id === openTask.id) || null,
                      failedGoal: subGoalResultsOf(openTask, env, envState).items
                        .find((g) => g.status === "failed" && g.mark === "critical")?.text || null,
                    }),
                    ...(verdicts?.get(openTask.id) && { status: verdicts.get(openTask.id).label }),
                  }}
                  initialTab={evalFocus ? "evaluations" : undefined}
                  focusEvalName={evalFocus || undefined}
                  hideEvalRowFix
                  onClose={closeTask}
                  onPrev={goPrev}
                  onNext={goNext}
                  hasPrev={!!goPrev}
                  hasNext={!!goNext}
                  hideAnnotationTab
                />
              )}
            </Drawer>
            </>
          );
        }

        return (
          <SideDrawer open={!!openTask} onClose={closeTask} width={{ xs: "100%", md: 1080 }}>
            {openTask && (
              <CallDrawer
                key={`${openTask.id}-${focusStep || ""}-${evalFocus || ""}-${openPane || ""}`}
                task={openTask}
                focus={focusStep || undefined}
                initialPane={openPane || undefined}
                focusEvalName={evalFocus || undefined}
                env={env}
                envState={envState}
                patch={patch}
                addAgentVersion={addAgentVersion}
                runId={runId}
                verdict={verdicts?.get(openTask.id) || null}
                onClose={closeTask}
                onPrev={goPrev}
                onNext={goNext}
              />
            )}
          </SideDrawer>
        );
      })()}
    </Box>
  );
}

RunResults.propTypes = {
  env: PropTypes.object, runId: PropTypes.string, tasks: PropTypes.array,
  stats: PropTypes.object, evals: PropTypes.array,
  seed: PropTypes.number,
};

/*
  The two re-run modes (PRD v7 §4.10.8, closes Q29). "Re-run exact snapshot"
  is the default for comparisons: the run's own scenario versions,
  environment version, pins and seed — only the agent version may change, so
  any difference is the agent's. "Run as configured now" picks up edits.
*/
function RerunModes({ run, canSnapshot, envLabel, agents = [], onRun }) {
  const [agent, setAgent] = useState(run?.agentVersion || null);
  const itemSx = { alignItems: "flex-start", gap: 0.75, py: 1.25, whiteSpace: "normal" };
  const textProps = {
    primaryTypographyProps: { typography: "s2", fontWeight: 700 },
    secondaryTypographyProps: { typography: "s3", color: "text.subtitle", sx: { whiteSpace: "normal" } },
  };
  return (
    <>
      {canSnapshot && (
        <>
          <MenuItem onClick={() => onRun("snapshot", agent)} sx={itemSx}>
            <ListItemIcon sx={{ minWidth: "0 !important", mt: "3px" }}>
              <Iconify icon="solar:history-linear" width={16} sx={{ color: "text.primary" }} />
            </ListItemIcon>
            <ListItemText
              primary={(
                <>
                  Re-run exact snapshot
                  <Box component="span" sx={{ ml: 0.75, typography: "s3", fontWeight: 600, color: "text.subtitle" }}>default for comparisons</Box>
                </>
              )}
              secondary={`Same scenario versions, environment ${envLabel || "version"}, pins and seed${run?.repeats ? `, ${run.repeats} repeats` : ""}. Only the agent version can change.`}
              {...textProps}
            />
          </MenuItem>
          {agents.length > 1 && (
            <Stack direction="row" alignItems="center" spacing={0.75} sx={{ px: 2, pb: 1.25, pl: "44px", flexWrap: "wrap", rowGap: 0.5 }}>
              <Typography sx={{ typography: "s3", color: "text.subtitle" }}>Agent</Typography>
              {agents.map((a) => (
                <Box
                  key={a}
                  role="button" tabIndex={0}
                  onClick={() => setAgent(a)}
                  onKeyDown={(e) => { if (e.key === "Enter") setAgent(a); }}
                  sx={{
                    px: 0.75, height: 22, display: "inline-flex", alignItems: "center", borderRadius: 0.75, cursor: "pointer",
                    typography: "s3", fontWeight: 600, border: "1px solid",
                    borderColor: agent === a ? "text.primary" : "divider",
                    color: agent === a ? "text.primary" : "text.secondary",
                  }}
                >
                  {a}
                  {a === run?.agentVersion && <Box component="span" sx={{ ml: 0.5, color: "text.subtitle", fontWeight: 400 }}>this run</Box>}
                </Box>
              ))}
            </Stack>
          )}
        </>
      )}
      <MenuItem onClick={() => onRun("now", null)} sx={itemSx}>
        <ListItemIcon sx={{ minWidth: "0 !important", mt: "3px" }}>
          <Iconify icon="solar:refresh-linear" width={16} sx={{ color: "text.primary" }} />
        </ListItemIcon>
        <ListItemText
          primary="Run as configured now"
          secondary="Picks up every edit since — the current agent, scenarios, environment and evals, and a new seed. Compares only on the pairs that still match."
          {...textProps}
        />
      </MenuItem>
    </>
  );
}
RerunModes.propTypes = {
  run: PropTypes.object, canSnapshot: PropTypes.bool, envLabel: PropTypes.string, agents: PropTypes.array, onRun: PropTypes.func,
};

/* Map a task's raw status to one of the chip buckets. An errored episode is a
   measured failure (it reached a "this went wrong" verdict) — grouped with
   Failing, not Inconclusive, so the chips agree with the summary strip, the
   Analytics tab, and the failure groupings (which do bucket errored rows).
   Only genuinely unmeasured episodes are inconclusive. */
function statusBucket(t) {
  if (t.status === "passed") return "passing";
  if (t.status === "flaky") return "mixed";
  if (t.status === "unmeasured") {
    /* Inconclusive, test invalid or error — none counts for or against the agent. */
    const o = outcomeOfCause(causeOf(t));
    return o === "test-invalid" ? "invalid" : o === "error" ? "error" : "inconclusive";
  }
  return "failing";
}

/* The chips for outcomes that count neither for nor against the agent. */
const NOT_COUNTED_CHIPS = ["inconclusive", "invalid", "error"];

/* One name per status, the same on the chip and on the row. `about` says
   what each means — Flaky only exists because scenarios are repeated. */
const STATUS_CHIPS = [
  { id: "all", label: "All", color: null, about: "" },
  { id: "failing", label: "Failing", color: "#DC2626", about: "Failed every repeat, or errored." },
  { id: "mixed", label: "Flaky", color: "#CA8A04", about: "Passed some repeats and failed others — the agent isn't reliable here." },
  { id: "inconclusive", label: "Inconclusive", color: null, about: "Not enough evidence to grade. Never counted as a pass; left out of pass rates." },
  { id: "invalid", label: "Test invalid", color: null, about: "The test broke, not the agent — the simulated user left its goal, the starting state didn't load, or a judge failed. Counts neither for nor against the agent." },
  { id: "error", label: "Error", color: null, about: "Our infrastructure or the connection failed. Counts neither for nor against the agent." },
  { id: "passing", label: "Passing", color: "#16A34A", about: "Passed every repeat." },
];

/*
  Scenarios pulled from the run after testing invalid twice — listed on their
  own above the table, each with a link to the scenario in its environment
  to review it. Outside every count.
*/
function PulledGroup({ tasks, envId }) {
  const navigate = useNavigate();
  return (
    <Box sx={{ borderBottom: "1px solid", borderColor: "divider", flexShrink: 0, maxHeight: 168, overflow: "auto" }}>
      <Stack direction="row" alignItems="center" spacing={0.75} sx={{ px: 2.5, py: 0.875, borderBottom: "1px solid", borderColor: "divider" }}>
        <Iconify icon="solar:flag-linear" width={13} sx={{ color: "text.subtitle" }} />
        <Typography sx={{ fontSize: 13, fontWeight: 500, color: "text.secondary" }}>
          {`Pulled for review (${tasks.length})`}
        </Typography>
        <Typography noWrap sx={{ typography: "s3", color: "text.subtitle" }}>· test invalid twice — left out of every count</Typography>
      </Stack>
      {tasks.map((t) => (
        <Stack
          key={t.id} direction="row" alignItems="center" spacing={1.5}
          sx={{ px: 2.5, py: 0.625, borderBottom: "1px solid", borderColor: "divider", "&:last-of-type": { borderBottom: "none" } }}
        >
          <Typography noWrap sx={{ typography: "s2", fontWeight: 600, minWidth: 0, flexShrink: 1 }}>{scenarioTitle(t.title) || t.name || t.id}</Typography>
          <Typography noWrap sx={{ typography: "s3", color: "text.subtitle", flex: 1, minWidth: 0 }}>{t.pulled.reason}</Typography>
          <Button
            size="small" variant="text"
            onClick={() => navigate(`${paths.dashboard.simulate.environmentStep(envId, "scenarios")}?scenario=${encodeURIComponent(t.id)}`)}
            endIcon={<Iconify icon="solar:arrow-right-up-linear" width={13} />}
            sx={{ typography: "s3", fontWeight: 700, color: "text.primary", flexShrink: 0, minWidth: 0, px: 0.75 }}
          >
            Review
          </Button>
        </Stack>
      ))}
    </Box>
  );
}
PulledGroup.propTypes = { tasks: PropTypes.array, envId: PropTypes.string };

/* Outcome quick-filter — one chip per status bucket plus All. The active chip
   is filled in its status colour; the rest are quiet outlines. Each carries
   its count so the distribution is legible before you click. */
function StatusFilterChips({ value, counts, onChange, blocked = [] }) {
  return (
    <Stack direction="row" alignItems="center" spacing={0.75} sx={{ flexWrap: "wrap", rowGap: 0.75 }}>
      {/* An outcome nothing ended in isn't a filter worth offering — hidden,
          unless it's the one selected. Passing and failing always show. */}
      {STATUS_CHIPS.filter((chip) => ["all", "failing", "passing"].includes(chip.id) || (counts[chip.id] ?? 0) > 0 || value === chip.id).map((chip) => {
        const active = value === chip.id;
        const dot = chip.color || "text.disabled";
        const count = counts[chip.id] ?? 0;
        /* Disabled when empty, or when the current failure-only grouping has
           no bucket for this outcome (passing / inconclusive). */
        const blockedByGrouping = blocked.includes(chip.id);
        const disabled = (chip.id !== "all" && count === 0) || blockedByGrouping;
        return (
          <Tooltip
            key={chip.id}
            arrow
            title={blockedByGrouping ? "Not available while grouping by a failure view — passing runs have no failure bucket" : chip.about}
          >
          <Box
            role="button"
            tabIndex={disabled ? -1 : 0}
            onClick={() => !disabled && onChange(chip.id)}
            onKeyDown={(e) => { if (!disabled && (e.key === "Enter" || e.key === " ")) onChange(chip.id); }}
            sx={{
              display: "inline-flex", alignItems: "center", gap: 0.625,
              height: 28, px: 1.125, borderRadius: 1,
              border: "1px solid",
              borderColor: active ? (chip.color || "text.primary") : "divider",
              bgcolor: active
                ? (t) => alpha(chip.color || t.palette.text.primary, t.palette.mode === "dark" ? 0.16 : 0.1)
                : "transparent",
              color: disabled ? "text.disabled" : "text.primary",
              cursor: disabled ? "default" : "pointer",
              opacity: disabled ? 0.5 : 1,
              transition: "border-color 120ms, background-color 120ms",
              "&:hover": disabled ? {} : { borderColor: active ? (chip.color || "text.primary") : "text.disabled" },
            }}
          >
            {chip.id !== "all" && (
              <Box sx={{ width: 6, height: 6, borderRadius: "50%", bgcolor: dot, flexShrink: 0 }} />
            )}
            <Typography sx={{ typography: "s2", fontWeight: 700, whiteSpace: "nowrap" }}>
              {chip.label}
            </Typography>
            <Typography sx={{
              typography: "s3", fontWeight: 700, fontVariantNumeric: "tabular-nums",
              color: active ? "inherit" : "text.subtitle",
            }}>
              {count}
            </Typography>
          </Box>
          </Tooltip>
        );
      })}
    </Stack>
  );
}
StatusFilterChips.propTypes = {
  value: PropTypes.string, counts: PropTypes.object, onChange: PropTypes.func, blocked: PropTypes.array,
};

/* "finished 2m ago" — relative for anything recent, an absolute date once it's
   more than a week old. Tries the timestamp fields a run might carry and
   returns null on a missing/unparseable date so the caller can omit it. */
function runFinishedLabel(run) {
  const raw = run?.finishedAt || run?.completedAt || run?.createdAt;
  if (!raw) return null;
  const d = new Date(raw);
  if (Number.isNaN(d.getTime())) return null;
  const s = Math.max(0, Math.floor((Date.now() - d.getTime()) / 1000));
  if (s < 45) return "finished just now";
  if (s < 90) return "finished a minute ago";
  if (s < 3600) return `finished ${Math.round(s / 60)}m ago`;
  if (s < 86400) return `finished ${Math.round(s / 3600)}h ago`;
  if (s < 604800) return `finished ${Math.round(s / 86400)}d ago`;
  return `finished ${d.toLocaleDateString(undefined, { month: "short", day: "numeric" })}`;
}

/* ── deterministic replay ────────────────────────────────────────────────── */

/**
 * Replaying a failure is only useful if it actually reproduces, so this states
 * what is pinned rather than offering a bare "run again". Everything listed is
 * derived from the seed — that is the mechanism, and saying so is what makes
 * the button trustworthy.
 */
function ReplayDrawer({ task, seed, onClose }) {
  const PINNED = [
    { label: "World state", note: "The same seeded rows, in the same state, before the agent arrives." },
    { label: "Persona turns", note: "The same wording, the same order, the same moments of hesitation." },
    { label: "Actor entries", note: "Anyone who joins or interrupts does so at the same point." },
    { label: "Mocked responses", note: "Stubbed tools return exactly what they returned before." },
    { label: "Perturbations", note: "The same noise, the same barge-ins, the same flaky call." },
  ];

  return (
    <SideDrawer open={!!task} onClose={onClose} width={460}>
      {task && (
        <Stack sx={{ height: "100%" }}>
          <Stack direction="row" alignItems="center" spacing={2} sx={{ px: 2.5, py: 2, borderBottom: "1px solid", borderColor: "divider" }}>
            <Box flex={1} minWidth={0}>
              <Typography noWrap sx={{ typography: "s1_2", fontWeight: 700 }}>Replay this scenario</Typography>
              <Typography noWrap sx={{ typography: "s3", color: "text.subtitle" }}>{task.title}</Typography>
            </Box>
            <IconButton size="small" onClick={onClose}>
              <Iconify icon="solar:close-circle-linear" width={18} sx={{ color: "text.subtitle" }} />
            </IconButton>
          </Stack>

          <Stack spacing={2} sx={{ p: 2.5, flex: 1, overflowY: "auto" }}>
            <Stack
              direction="row" alignItems="center" spacing={1.25}
              sx={{ p: 1.75, borderRadius: 1.25, border: "1px solid", borderColor: "divider", bgcolor: "background.neutral" }}
            >
              <Iconify icon="solar:dice-linear" width={16} sx={{ color: "primary.main", flexShrink: 0 }} />
              <Typography sx={{ typography: "s2", flex: 1 }}>Deterministic seed</Typography>
              <Typography sx={{ typography: "s1", fontWeight: 700, fontFamily: "ui-monospace, Menlo, monospace" }}>
                {seed}
              </Typography>
            </Stack>

            <Typography sx={{ typography: "s2", color: "text.secondary" }}>
              Runs this one scenario again, on its own, with everything below held identical.
              If it fails the same way, the failure is the agent. If it does not, the failure
              was flaky — and that is worth knowing before anyone fixes anything.
            </Typography>

            <Stack spacing={1.25}>
              {PINNED.map((p) => (
                <Stack key={p.label} direction="row" spacing={1.25} alignItems="flex-start">
                  <Iconify icon="solar:lock-keyhole-minimalistic-linear" width={14} sx={{ color: "text.subtitle", flexShrink: 0, mt: "2px" }} />
                  <Box>
                    <Typography sx={{ typography: "s2", fontWeight: 600 }}>{p.label}</Typography>
                    <Typography sx={{ typography: "s3", color: "text.subtitle" }}>{p.note}</Typography>
                  </Box>
                </Stack>
              ))}
            </Stack>

            <Box>
              <Typography sx={{ typography: "s3", fontWeight: 700, color: "text.subtitle", textTransform: "uppercase", letterSpacing: .4, mb: 0.75 }}>
                Run it yourself
              </Typography>
              <Box
                sx={{
                  p: 1.25, borderRadius: 1, border: "1px solid", borderColor: "divider",
                  bgcolor: "background.neutral", typography: "s2",
                  fontFamily: "ui-monospace, Menlo, monospace", color: "text.secondary",
                  overflowX: "auto", whiteSpace: "nowrap",
                }}
              >
                fai replay {task.id} --seed {seed}
              </Box>
            </Box>
          </Stack>

          <Stack direction="row" spacing={1.5} sx={{ px: 2.5, py: 2, borderTop: "1px solid", borderColor: "divider" }}>
            <Box flex={1} />
            <Button onClick={onClose} sx={{ typography: "s2", fontWeight: 600, color: "text.secondary" }}>Cancel</Button>
            <Button
              variant="contained" color="primary" onClick={onClose}
              startIcon={<Iconify icon="solar:restart-linear" width={15} />}
              sx={{ typography: "s2", fontWeight: 700 }}
            >
              Replay on seed {seed}
            </Button>
          </Stack>
        </Stack>
      )}
    </SideDrawer>
  );
}
ReplayDrawer.propTypes = { task: PropTypes.object, seed: PropTypes.number, onClose: PropTypes.func };

