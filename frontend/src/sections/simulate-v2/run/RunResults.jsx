import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { alpha } from "@mui/material/styles";
import {
  Box, Stack, Typography, Button, IconButton, Tab, Checkbox, Tooltip,
} from "@mui/material";
import SideDrawer from "../components/SideDrawer";
import { FilterPanel } from "src/components/filter-panel";
import Iconify from "src/components/iconify";
import { subTasksFor } from "../_mock/contract";
import { CustomTabs } from "src/components/tabs/tabs";
import { paths } from "src/routes/paths";
import { getEval } from "../_mock/evals";
import TurnScores from "./TurnScores";
import {
  SectionCard, ScorePill, StatusChip, StatusDot, PersonaBadge, EmptyState,
} from "../components/primitives";
import Stage from "./stages";
import VerifyRun from "./VerifyRun";
import RunAnalytics from "./RunAnalytics";
import CallDrawer from "./CallDrawer";
import AddEvalsDrawer from "../workspace/evals/AddEvalsDrawer";
import { useEnvState } from "../store";
import { protoRunId } from "../_mock/executionAdapter";
import { runSummaries, trialSummaries, chipIdentity as computeChipIdentity } from "../_mock/comparison";
import { resolveEval } from "../_mock/evals";
import TraceTable, { TraceGroupByPicker, TraceColumnsPicker, defaultTraceColumns, deriveUseCaseLabel } from "./TraceTable";
import FixMyAgentDrawer from "./fixmyagent/FixMyAgentDrawer";

/**
 * Run results.
 *
 * Ordered by what a user does next: how did it go overall, which tasks failed
 * and why, then verify the builder itself, then optimise. Each of
 * those is a tab rather than a separate page so the run stays one object.
 */
export default function RunResults({ env, runId, tasks, stats, evals, stage, seed = 7 }) {
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
    return byId.size ? [...byId.values()] : evals;
  }, [tasks, evals]);
  const navigate = useNavigate();
  const { envId } = useParams();
  const [params] = useSearchParams();
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
  const [fixOpen, setFixOpen] = useState(false);
  const [fixOptId, setFixOptId] = useState(null);
  const [tab, setTab] = useState(() => {
    const t = params.get("tab") || "tasks";
    /* Stale deep-links to the removed "Self improvement runs" tab
       (values `optimize` or `omega`) now land on Traces. */
    return t === "optimize" || t === "omega" ? "tasks" : t;
  });
  const [replaying, setReplaying] = useState(null);
  const [openTask, setOpenTask] = useState(null);
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
  /*
    Column visibility for the traces table. A Set of column keys the
    user has chosen to show; essentials (Persona, Scenario) render
    regardless. Lifted so the picker sits in the header action slot.
  */
  const [visibleColumns, setVisibleColumns] = useState(() => defaultTraceColumns());
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

  /* Trials launched FROM this run — reused as full run summaries via
     `trialSummaries` so the Trials tab can render the same columns the
     main runs table shows (system metrics + eval scores) and route
     Compare through the same URL. */
  const trialsFromRun = useMemo(() => {
    const optIds = new Set(
      (envState.optimizations || [])
        .filter((o) => o.fromRunId === runId)
        .map((o) => o.id),
    );
    return trialSummaries(env, envState).filter((t) => optIds.has(t.selfImprovementId));
  }, [runId, env, envState]);

  const trialChips = useMemo(() => computeChipIdentity(env, envState), [env, envState]);
  const trialEvals = useMemo(
    () => (envState?.evals || []).map(resolveEval).filter(Boolean),
    [envState?.evals],
  );
  const [trialsSelected, setTrialsSelected] = useState([]);
  const toggleTrial = (id) =>
    setTrialsSelected((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));
  const compareTrials = () => {
    if (trialsSelected.length < 2) return;
    const ordered = [...trialsSelected].sort((a, b) => {
      const ai = trialsFromRun.findIndex((t) => t.id === a);
      const bi = trialsFromRun.findIndex((t) => t.id === b);
      return ai - bi;
    });
    navigate(`${paths.dashboard.simulate.simulationCompare(env.id)}?runs=${ordered.join(",")}`);
  };
  /* What this run is read against — the released version if there is one, else
     the run before it. The comparative analyzers need something to compare
     to and say so when there is nothing. */
  const released = envState.releases?.[0]?.version;
  const baselineRun = summaries.find((r) => released && r.agentVersion === released && r.id !== runId)
    || summaries.filter((r) => r.id !== runId).slice(-1)[0]
    || null;

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
  const rerun = (ids) => {
    const url = paths.dashboard.simulate.simulationRun(envId, protoRunId(envId, Date.now().toString(36)));
    navigate(ids?.length ? `${url}?only=${ids.join(",")}` : url);
  };

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
  const STATUS_LABELS = ["Passed", "Failed", "Errored", "Not measured"];
  const PATTERN_LABELS = ["Critical rule broken", "Said, not done", "Evaluation failed", "Errored", "Other failure"];
  const CRITICAL_LABELS = ["Critical", "Not critical"];

  const filterFields = useMemo(() => [
    { value: "goal",     label: "Goal",            type: "enum", choices: goalOptions },
    { value: "subGoal",  label: "Sub-goal",        type: "enum", choices: subGoalOptions },
    { value: "status",   label: "Status",          type: "enum", choices: STATUS_LABELS },
    { value: "pattern",  label: "Failure pattern", type: "enum", choices: PATTERN_LABELS },
    { value: "critical", label: "Criticality",     type: "enum", choices: CRITICAL_LABELS },
  ], [goalOptions, subGoalOptions]);

  /* Per-task derivations, cached so the filter predicate doesn't
     recompute them when only the selected filter changes. */
  const taskDerivations = useMemo(() => {
    const out = new Map();
    tasks.forEach((t) => {
      const statusLabel = t.status === "passed" ? "Passed"
        : t.status === "unmeasured" ? "Not measured"
        : t.status === "error" ? "Errored"
        : "Failed";
      let patternLabel = null;
      if (t.status !== "passed" && t.status !== "unmeasured") {
        if (t.status === "error") patternLabel = "Errored";
        else if (t.critical) patternLabel = "Critical rule broken";
        else if (t.callLog?.unsupportedClaim) patternLabel = "Said, not done";
        else if ((t.evalResults || []).some((r) => !r.passed)) patternLabel = "Evaluation failed";
        else patternLabel = "Other failure";
      }
      out.set(t.id, {
        goal: deriveUseCaseLabel(t),
        subGoalLabels: new Set(subTasksFor(t, env).map((s) => s.label)),
        statusLabel,
        patternLabel,
        criticalLabel: t.critical ? "Critical" : "Not critical",
      });
    });
    return out;
  }, [tasks, env]);

  const shown = tasks.filter((t) => {
    /* Metric drill-down predicate — clicking a bucket on the RunMetrics
       chart. Independent of the header filters; they AND together. */
    if (bucketFilter?.match && !bucketFilter.match(t)) return false;

    const d = taskDerivations.get(t.id);
    if (filters.goal?.length     && !filters.goal.includes(d.goal))         return false;
    if (filters.status?.length   && !filters.status.includes(d.statusLabel)) return false;
    if (filters.pattern?.length  && !filters.pattern.includes(d.patternLabel)) return false;
    if (filters.critical?.length && !filters.critical.includes(d.criticalLabel)) return false;
    if (filters.subGoal?.length && !filters.subGoal.some((g) => d.subGoalLabels.has(g))) return false;
    return true;
  });

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

  const failedCritical = tasks.filter((t) => t.critical && t.status === "failed").length;

  /* The scenarios a comparison handed over, if it did. */
  const sentIds = params.get("only")?.split(",").filter(Boolean) || [];
  const sent = sentIds.length ? tasks.filter((t) => sentIds.includes(t.id)) : null;

  return (
    <Box sx={{ display: "flex", flexDirection: "column", height: "100%", minHeight: 0 }}>
      {/* ── header ── */}
      <Stack
        direction="row" alignItems="center" spacing={2}
        sx={{ px: 3, py: 2, borderBottom: "1px solid", borderColor: "divider", flexShrink: 0 }}
      >
        <IconButton size="small" onClick={() => navigate(paths.dashboard.simulate.environmentStep(envId, "runs"))}>
          <Iconify icon="solar:alt-arrow-left-linear" width={18} sx={{ color: "text.subtitle" }} />
        </IconButton>
        <Box flex={1} minWidth={0}>
          <Stack direction="row" alignItems="center" spacing={1}>
            {trialMeta ? (
              <Box
                sx={{
                  width: 24, height: 24, borderRadius: 0.875, flexShrink: 0,
                  display: "grid", placeItems: "center",
                  typography: "s3", fontWeight: 700, color: "#7857FC",
                  bgcolor: (t) => alpha("#7857FC", t.palette.mode === "dark" ? 0.22 : 0.14),
                }}
              >
                <Iconify icon="solar:magic-stick-3-bold" width={13} />
              </Box>
            ) : identity && (
              <Box
                sx={{
                  width: 24, height: 24, borderRadius: 0.875, flexShrink: 0,
                  display: "grid", placeItems: "center",
                  typography: "s3", fontWeight: 700, color: identity.color,
                  bgcolor: (t) => alpha(identity.color, t.palette.mode === "dark" ? 0.22 : 0.14),
                }}
              >
                {identity.letter}
              </Box>
            )}
            <Typography noWrap sx={{ typography: "s1_2", fontWeight: 700 }}>
              {trialMeta
                ? `Run ${trialMeta.trial.n} · ${trialMeta.opt.name || `Self improvement ${trialMeta.ordinal}`}`
                : identity
                  ? `Run ${identity.ordinal} · agent ${identity.agentVersion}`
                  : "Run complete"}
            </Typography>
            {/*
              Failed means the run failed, not that something in it did. All
              seven scenarios failing is a failed run; three of seven failing
              is a completed run with findings, and calling that "Failed"
              buries the three that passed.
            */}
            <StatusChip status={stats.passed === 0 ? "failed" : stats.failed === 0 ? "passed" : "completed"} />
          </Stack>
          <Typography noWrap sx={{ typography: "s2", color: "text.subtitle" }}>
            {env.name} · {stats.total} tasks
            {identity ? ` · ${new Date(identity.finishedAt).toLocaleString()}` : ""}
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
        <Button
          variant="outlined" size="small"
          startIcon={<Iconify icon="solar:download-minimalistic-linear" width={15} />}
          sx={{ color: "text.primary", borderColor: "divider", typography: "s2", fontWeight: 600 }}
        >
          Export
        </Button>
        {!trialMeta && (
          <Button
            variant="outlined" size="small"
            startIcon={<Iconify icon="solar:refresh-linear" width={15} />}
            onClick={rerun}
            sx={{ color: "text.primary", borderColor: "divider", typography: "s2", fontWeight: 600 }}
          >
            Run again
          </Button>
        )}
        {/*
          The primary action after a failed run is not to run it again —
          the same agent against the same graders returns the same
          result. It is to summarise what went wrong so the reader can
          change the agent. Trial pages open the same drawer against the
          trial's own outcomes.
        */}
        <Button
          variant="contained" color="primary" size="small"
          startIcon={<Iconify icon="solar:document-text-linear" width={15} />}
          onClick={() => { setFixOptId(null); setFixOpen(true); }}
          sx={{ typography: "s2", fontWeight: 700 }}
        >
          Summarize failures
        </Button>
      </Stack>

      <Box sx={{ flex: 1, minHeight: 0, overflow: "auto" }}>
        <Box sx={{ p: 2 }}>
          {/* ── critical banner ── */}
          {failedCritical > 0 && (
            <Box
              sx={{
                p: 2, mb: 2, borderRadius: 1.25,
                border: "1px solid", borderColor: alpha("#DC2626", 0.35),
                bgcolor: (t) => alpha("#DC2626", t.palette.mode === "dark" ? 0.1 : 0.05),
              }}
            >
              <Stack direction="row" alignItems="center" spacing={1.25}>
                <Iconify icon="solar:danger-triangle-linear" width={18} sx={{ color: "#DC2626" }} />
                <Typography sx={{ typography: "s2", flex: 1 }}>
                  <b>{failedCritical} critical {failedCritical === 1 ? "scenario" : "scenarios"} failed.</b>{" "}
                  These are release blockers — the agent broke a rule the environment enforces.
                </Typography>
                <Button
                  size="small"
                  onClick={() => { setGroupBy("pattern"); setTab("tasks"); }}
                  sx={{ typography: "s2", fontWeight: 700, color: "#DC2626" }}
                >
                  Review
                </Button>
              </Stack>
            </Box>
          )}

          {/* ── tabs ── */}
          <CustomTabs
            value={tab}
            onChange={(_, v) => setTab(v)}
            sx={{ borderBottom: "1px solid", borderColor: "divider", mb: 2, minHeight: 38 }}
          >
            <Tab value="tasks" label={`Traces (${stats.total})`} sx={{ minHeight: 38 }} />
            <Tab value="analytics" label="Analytics" sx={{ minHeight: 38 }} />
            <Tab value="verify" label="Verify" sx={{ minHeight: 38 }} />
            {trialsFromRun.length > 0 && (
              <Tab value="trials" label={`Trials (${trialsFromRun.length})`} sx={{ minHeight: 38 }} />
            )}
          </CustomTabs>

          {tab === "tasks" && (
            <SectionCard
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
                  <Stack direction="row" alignItems="center" spacing={1.25}>
                    <TraceGroupByPicker value={groupBy} onChange={setGroupBy} />
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
                    {bucketFilter && (
                      <Stack direction="row" alignItems="center" spacing={0.75}>
                        <Box sx={{
                          display: "inline-flex", alignItems: "center", gap: 0.625,
                          px: 0.875, py: 0.25, borderRadius: 0.75,
                          bgcolor: (t) => alpha(t.palette.primary.main, t.palette.mode === "dark" ? 0.16 : 0.1),
                          color: "primary.main", typography: "s3", fontWeight: 700,
                        }}>
                          <Iconify icon="solar:filter-linear" width={12} />
                          Filtered · {bucketFilter.label}
                        </Box>
                        <Box
                          onClick={() => setBucketFilter(null)}
                          sx={{
                            cursor: "pointer",
                            typography: "s3", color: "text.subtitle",
                            "&:hover": { color: "text.primary" },
                          }}
                        >
                          clear
                        </Box>
                      </Stack>
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
                    <Button
                      variant="contained" color="primary" size="small"
                      onClick={() => rerun([...selected])}
                      startIcon={<Iconify icon="solar:refresh-bold" width={15} />}
                      sx={{ typography: "s2", fontWeight: 700 }}
                    >
                      Re-run {selected.size}
                    </Button>
                  </Stack>
                ) : (
                  <TraceColumnsPicker value={visibleColumns} onChange={setVisibleColumns} />
                )
              }
            >
              {shown.length === 0 ? (
                <EmptyState icon="solar:filter-linear" title="No tasks match that filter" />
              ) : (
                <TraceTable
                  tasks={shown}
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
                  onOpen={setOpenTask}
                />
              )}
            </SectionCard>
          )}

          {tab === "analytics" && <RunAnalytics tasks={tasks} evals={shownEvals} env={env} stats={stats} />}

          {tab === "verify" && <VerifyRun env={env} tasks={tasks} stats={stats} runId={runId} />}

          {tab === "trials" && (
            <SectionCard
              title="Self-improvement trials from this run"
              subtitle={
                trialsSelected.length >= 2
                  ? `${trialsSelected.length} selected · ready to compare`
                  : "Every candidate the search executed. Click a row to open it; tick two or more to compare."
              }
              action={
                trialsSelected.length > 0 && (
                  <Stack direction="row" spacing={1}>
                    <Button
                      size="small" variant="outlined"
                      onClick={() => setTrialsSelected([])}
                      sx={{ typography: "s2", fontWeight: 600, color: "text.primary", borderColor: "divider" }}
                    >
                      Clear
                    </Button>
                    <Button
                      size="small" variant="contained" color="primary"
                      disabled={trialsSelected.length < 2}
                      onClick={compareTrials}
                      startIcon={<Iconify icon="solar:transfer-horizontal-linear" width={15} />}
                      sx={{ typography: "s2", fontWeight: 700 }}
                    >
                      Compare
                    </Button>
                  </Stack>
                )
              }
            >
              <TrialsTable
                trials={trialsFromRun}
                chips={trialChips}
                evals={trialEvals}
                selected={trialsSelected}
                onToggle={toggleTrial}
                onOpen={(t) => navigate(paths.dashboard.simulate.simulationRun(env.id, t.id))}
              />
            </SectionCard>
          )}

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
      <AddEvalsDrawer
        open={addingEvals}
        onClose={() => setAddingEvals(false)}
        env={env}
        envState={envState}
        existingIds={envState.evals}
        onAdd={(added) => {
          patch({ evals: [...envState.evals, ...added.map((e) => e.id)] });
          setAddingEvals(false);
          rerun();
        }}
      />

      {/* ── trace drawer ── */}
      {/*
        Wide, because it is two panes: the call on the left and what was
        measured about it on the right. Prev/next walk the filtered list, so
        reading four failures in a row does not mean closing and reopening.
      */}
      {/* ── fix my agent ── */}
      <FixMyAgentDrawer
        open={fixOpen}
        onClose={() => { setFixOpen(false); setFixOptId(null); }}
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
      />

      <SideDrawer
        open={!!openTask}
        onClose={() => { setOpenTask(null); setFocusStep(null); }}
        width={{ xs: "100%", md: 1080 }}
      >
        {openTask && (
          <CallDrawer
            key={`${openTask.id}-${focusStep || ""}`}
            task={openTask}
            focus={focusStep || undefined}
            env={env}
            envState={envState}
            onClose={() => { setOpenTask(null); setFocusStep(null); }}
            onPrev={(() => {
              const i = shown.findIndex((t) => t.id === openTask.id);
              return i > 0 ? () => setOpenTask(shown[i - 1]) : undefined;
            })()}
            onNext={(() => {
              const i = shown.findIndex((t) => t.id === openTask.id);
              return i > -1 && i < shown.length - 1 ? () => setOpenTask(shown[i + 1]) : undefined;
            })()}
          />
        )}
      </SideDrawer>
    </Box>
  );
}

RunResults.propTypes = {
  env: PropTypes.object, runId: PropTypes.string, tasks: PropTypes.array,
  stats: PropTypes.object, evals: PropTypes.array, stage: PropTypes.string,
  seed: PropTypes.number,
};

/**
 * Trials table.
 *
 * Mirrors the column set the main runs table shows — chip, name,
 * PASS %, avg duration, tokens, cost, plus one column per eval — so a
 * trial reads the same as any other run. Checkbox multi-select routes
 * to the standard compare page via `/simulationCompare?runs=...`.
 */
function TrialsTable({ trials, chips, evals, selected, onToggle, onOpen }) {
  const scoreOf = (t, evalId) => t.scores?.[evalId];
  return (
    <Box sx={{ overflowX: "auto" }}>
      <Box sx={{ minWidth: 780 + evals.length * 84 }}>
        {/* header */}
        <Box
          sx={{
            display: "grid",
            gridTemplateColumns: `36px minmax(220px, 1fr) 80px 96px 96px 88px ${evals.map(() => "84px").join(" ")}`,
            columnGap: 1.5,
            px: 2.5, py: 1.25, borderBottom: "1px solid", borderColor: "divider",
          }}
        >
          <Box />
          <ColHead>Run</ColHead>
          <ColHead right>Pass</ColHead>
          <ColHead right>Avg duration</ColHead>
          <ColHead right>Tokens</ColHead>
          <ColHead right>Cost</ColHead>
          {evals.map((e) => (
            <ColHead key={e.id} right>{e.name}</ColHead>
          ))}
        </Box>

        {/* rows */}
        <Stack divider={<Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />}>
          {trials.map((t) => {
            const picked = selected.includes(t.id);
            const chip = chips.get(t.id) || { letter: t.letter, color: t.color };
            return (
              <Box
                key={t.id}
                onClick={() => onOpen(t)}
                sx={{
                  display: "grid",
                  gridTemplateColumns: `36px minmax(220px, 1fr) 80px 96px 96px 88px ${evals.map(() => "84px").join(" ")}`,
                  columnGap: 1.5, alignItems: "center",
                  px: 2.5, py: 1.25, minHeight: 44, cursor: "pointer",
                  borderLeft: "2px solid",
                  borderColor: t.isWinner ? "#EA580C" : "transparent",
                  bgcolor: picked ? (th) => alpha(th.palette.primary.main, 0.05) : "transparent",
                  "&:hover": { bgcolor: "action.hover" },
                }}
              >
                <Box
                  onClick={(e) => { e.stopPropagation(); onToggle(t.id); }}
                  sx={{ display: "flex", alignItems: "center" }}
                >
                  <Checkbox
                    size="small" checked={picked} readOnly tabIndex={-1}
                    sx={{ p: 0.5, pointerEvents: "none" }}
                  />
                </Box>

                <Stack direction="row" alignItems="center" spacing={1.25} sx={{ minWidth: 0 }}>
                  <Box
                    sx={{
                      minWidth: 26, height: 22, px: 0.75, borderRadius: 0.75, flexShrink: 0,
                      display: "grid", placeItems: "center",
                      typography: "s3", fontWeight: 700,
                      fontVariantNumeric: "tabular-nums",
                      color: chip.color,
                      bgcolor: (th) => alpha(chip.color, th.palette.mode === "dark" ? 0.22 : 0.14),
                    }}
                  >
                    {chip.letter}
                  </Box>
                  {t.isWinner && (
                    <Tooltip arrow title="Best-scoring trial of its self improvement">
                      <Box sx={{ display: "flex", alignItems: "center", flexShrink: 0 }}>
                        <Iconify icon="solar:cup-star-bold" width={14} sx={{ color: "#EA580C" }} />
                      </Box>
                    </Tooltip>
                  )}
                  <Box minWidth={0}>
                    <Typography noWrap sx={{ typography: "s2", fontWeight: 600 }}>
                      Run {t.trialN}
                      <Box component="span" sx={{ color: "text.subtitle", fontWeight: 500 }}>
                        {" "}· {t.selfImprovementName}
                      </Box>
                    </Typography>
                    <Typography noWrap sx={{ typography: "s3", color: "text.subtitle" }}>
                      {t.trialTried}
                    </Typography>
                  </Box>
                </Stack>

                <Typography sx={cellNum}>{t.passRate}%</Typography>
                <Typography sx={cellNum}>{(t.avgDurationMs / 1000).toFixed(1)}s</Typography>
                <Typography sx={cellNum}>{t.tokens.toLocaleString()}</Typography>
                <Typography sx={cellNum}>${t.cost.toFixed(2)}</Typography>
                {evals.map((e) => (
                  <Typography key={e.id} sx={cellNum}>
                    {scoreOf(t, e.id) == null ? "—" : `${scoreOf(t, e.id)}%`}
                  </Typography>
                ))}
              </Box>
            );
          })}
        </Stack>
      </Box>
    </Box>
  );
}
TrialsTable.propTypes = {
  trials: PropTypes.array, chips: PropTypes.instanceOf(Map),
  evals: PropTypes.array, selected: PropTypes.array,
  onToggle: PropTypes.func, onOpen: PropTypes.func,
};

const cellNum = {
  textAlign: "right", typography: "s2", fontWeight: 600,
  fontVariantNumeric: "tabular-nums", color: "text.primary",
};

function ColHead({ children, right }) {
  return (
    <Typography sx={{
      typography: "s3", fontWeight: 700, color: "text.subtitle",
      textTransform: "uppercase", letterSpacing: 0.4,
      textAlign: right ? "right" : "left",
    }}>
      {children}
    </Typography>
  );
}
ColHead.propTypes = { children: PropTypes.node, right: PropTypes.bool };


/* ── trace detail ────────────────────────────────────────────────────────── */

function TraceDetail({ task, stage, onClose }) {
  const [tab, setTab] = useState("replay");
  const failedEvals = task.evalResults.filter((r) => !r.passed);

  return (
    <Box sx={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <Stack
        direction="row" alignItems="center" spacing={1.5}
        sx={{ px: 2.5, py: 2, borderBottom: "1px solid", borderColor: "divider", flexShrink: 0 }}
      >
        <StatusDot status={task.status} />
        <Box flex={1} minWidth={0}>
          <Typography noWrap sx={{ typography: "s1", fontWeight: 700 }}>{task.title}</Typography>
          <Typography noWrap sx={{ typography: "s2", color: "text.subtitle" }}>{task.task}</Typography>
        </Box>
        <IconButton size="small" onClick={onClose}>
          <Iconify icon="solar:close-circle-linear" width={19} sx={{ color: "text.subtitle" }} />
        </IconButton>
      </Stack>

      {/* Failure explanation first — it is why the drawer was opened. */}
      {failedEvals.length > 0 && (
        <Box
          sx={{
            m: 2.5, mb: 0, p: 1.75, borderRadius: 1.25,
            border: "1px solid", borderColor: alpha("#DC2626", 0.35),
            bgcolor: (t) => alpha("#DC2626", t.palette.mode === "dark" ? 0.1 : 0.05),
          }}
        >
          {failedEvals.map((r) => (
            <Stack key={r.id} direction="row" spacing={1.25} alignItems="flex-start" sx={{ mb: 0.75, "&:last-child": { mb: 0 } }}>
              <Iconify icon="solar:close-circle-bold" width={15} sx={{ color: "#DC2626", flexShrink: 0, mt: "2px" }} />
              <Box>
                <Typography sx={{ typography: "s2", fontWeight: 700 }}>
                  {r.name} failed ({(r.score * 100).toFixed(0)})
                </Typography>
                <Typography sx={{ typography: "s2", color: "text.secondary" }}>{r.reason}</Typography>
              </Box>
            </Stack>
          ))}
        </Box>
      )}

      <CustomTabs
        value={tab}
        onChange={(_, v) => setTab(v)}
        sx={{ px: 2.5, borderBottom: "1px solid", borderColor: "divider", mt: 2, minHeight: 36, flexShrink: 0 }}
      >
        <Tab value="replay" label="Replay" sx={{ minHeight: 36 }} />
        <Tab value="turns" label="Per turn" sx={{ minHeight: 36 }} />
        <Tab value="steps" label={`Trajectory (${task.steps.length})`} sx={{ minHeight: 36 }} />
        <Tab value="evals" label={`Evals (${task.evalResults.length})`} sx={{ minHeight: 36 }} />
      </CustomTabs>

      <Box sx={{ flex: 1, minHeight: 0, overflow: "auto", display: "flex", flexDirection: "column" }}>
        {tab === "replay" && (
          <Stage stage={stage} task={task} stepIndex={task.steps.length - 1} live={false} />
        )}

        {tab === "turns" && <TurnScores task={task} />}

        {tab === "steps" && (
          <Stack sx={{ p: 2.5 }} spacing={0}>
            {task.steps.map((s, i) => {
              const isFailPoint = task.failStep === i;
              return (
                <Stack key={s.id} direction="row" spacing={1.75}>
                  <Stack alignItems="center" sx={{ flexShrink: 0 }}>
                    <Box
                      sx={{
                        width: 24, height: 24, borderRadius: "50%", display: "grid", placeItems: "center",
                        bgcolor: (t) => isFailPoint
                          ? alpha("#DC2626", t.palette.mode === "dark" ? 0.2 : 0.12)
                          : "background.neutral",
                        color: isFailPoint ? "#DC2626" : "text.subtitle",
                        border: isFailPoint ? `1px solid ${alpha("#DC2626", 0.4)}` : "none",
                        typography: "s3", fontWeight: 700,
                      }}
                    >
                      {i + 1}
                    </Box>
                    {i < task.steps.length - 1 && (
                      <Box sx={{ flex: 1, width: "2px", bgcolor: "divider", my: 0.5, minHeight: 16 }} />
                    )}
                  </Stack>
                  <Box sx={{ pb: 2, flex: 1, minWidth: 0 }}>
                    <Typography sx={{ typography: "s2", fontWeight: 600 }}>
                      {s.role ? (s.role === "agent" ? "Agent" : "Customer") : s.action || s.tool || s.cmd || s.kind}
                    </Typography>
                    <Typography sx={{ typography: "s2", color: "text.secondary" }}>
                      {s.text || s.thought || s.result || s.out || s.note}
                    </Typography>
                    {isFailPoint && (
                      <Typography sx={{ typography: "s3", color: "#DC2626", fontWeight: 700, mt: 0.5 }}>
                        ← the run diverged here
                      </Typography>
                    )}
                  </Box>
                </Stack>
              );
            })}
          </Stack>
        )}

        {tab === "evals" && (
          <Stack divider={<Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />}>
            {task.evalResults.map((r) => (
              <Box key={r.id} sx={{ px: 2.5, py: 1.75 }}>
                <Stack direction="row" alignItems="center" spacing={1.25} sx={{ mb: 0.5 }}>
                  <Iconify icon={getEval(r.id)?.icon || "solar:shield-check-linear"} width={15} sx={{ color: r.color }} />
                  <Typography sx={{ flex: 1, typography: "s2", fontWeight: 700 }}>{r.name}</Typography>
                  <ScorePill score={r.score} passed={r.passed} />
                </Stack>
                <Typography sx={{ typography: "s2", color: "text.subtitle", pl: 3.5 }}>{r.reason}</Typography>
              </Box>
            ))}
          </Stack>
        )}
      </Box>

      <Box sx={{ p: 2, borderTop: "1px solid", borderColor: "divider", flexShrink: 0 }}>
        <PersonaBadge persona={task.persona} />
      </Box>
    </Box>
  );
}
TraceDetail.propTypes = { task: PropTypes.object, stage: PropTypes.string, onClose: PropTypes.func };


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

