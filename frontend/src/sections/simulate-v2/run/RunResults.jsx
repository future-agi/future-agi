import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { alpha } from "@mui/material/styles";
import {
  Box, Stack, Typography, Button, IconButton, Tab, Chip,
} from "@mui/material";
import { FilterPanel } from "src/components/filter-panel";
import SideDrawer from "../components/SideDrawer";
import Iconify from "src/components/iconify";
import { CustomTabs } from "src/components/tabs/tabs";
import { paths } from "src/routes/paths";
import { getEval } from "../_mock/evals";
import TurnScores from "./TurnScores";
import {
  SectionCard, ScorePill, StatusChip, StatusDot, PersonaBadge, EmptyState,
} from "../components/primitives";
import Stage from "./stages";
import VerifyRun from "./VerifyRun";
import RunMetrics from "./RunMetrics";
import RunAnalytics from "./RunAnalytics";
import CallDrawer from "./CallDrawer";
import AddEvalsDrawer from "../workspace/evals/AddEvalsDrawer";
import { useEnvState } from "../store";
import { protoRunId } from "../_mock/executionAdapter";
import { runSummaries } from "../_mock/comparison";
import TraceTable from "./TraceTable";
import { describeUseCase, subTasksFor } from "../_mock/contract";
import FixMyAgentDrawer from "./fixmyagent/FixMyAgentDrawer";
import OptimizationRunsList from "./fixmyagent/OptimizationRunsList";
import { optimizationsFromRun } from "../_mock/optimizationRuns";

/**
 * Run results.
 *
 * Ordered by what a user does next: how did it go overall, which tasks failed
 * and why, then verify the builder itself, then optimise. Each of
 * those is a tab rather than a separate page so the run stays one object.
 */
export default function RunResults({ env, runId, tasks, stats, evals, stage, seed = 7 }) {
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
    return t === "optimize" ? "omega" : t;
  });
  const [replaying, setReplaying] = useState(null);
  const [openTask, setOpenTask] = useState(null);
  const [filter, setFilter] = useState("all");
  /*
    Two extra filter dimensions the product team asked for. Use cases group
    scenarios by what they are testing (a tool, rule enforcement, data
    traps…); sub-goals let you pull every trace whose scenario touches a
    specific settlement step (e.g. "Verify who is asking"), regardless of
    which scenario it is. Multi-select, ANDed together with the status
    chip: "failed AND touches sub-goal X" is a natural way to hunt a bug.
  */
  const [selectedUseCases, setSelectedUseCases] = useState([]);
  const [selectedSubGoals, setSelectedSubGoals] = useState([]);
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
    Options for the two dropdowns, computed from the tasks in this run so
    the filters can only offer choices that would actually match something.
    Use cases are keyed by the stable id from describeUseCase; sub-goals collect
    unique labels across every task's derived breakdown.
  */
  const useCaseOptions = useMemo(() => {
    const map = new Map();
    tasks.forEach((t) => {
      const uc = describeUseCase(t);
      if (!map.has(uc.id)) map.set(uc.id, uc);
    });
    return [...map.values()].sort((a, b) => a.label.localeCompare(b.label));
  }, [tasks]);
  const subGoalOptions = useMemo(() => {
    const set = new Set();
    tasks.forEach((t) => subTasksFor(t, env).forEach((s) => set.add(s.label)));
    return [...set].sort();
  }, [tasks, env]);

  /* Per-task derivations, cached so the filter step doesn't recompute them
     when only the selected filter changes. */
  const taskDerivations = useMemo(() => {
    const out = new Map();
    tasks.forEach((t) => {
      out.set(t.id, {
        useCaseId: describeUseCase(t).id,
        subGoalLabels: new Set(subTasksFor(t, env).map((s) => s.label)),
      });
    });
    return out;
  }, [tasks, env]);

  const shown = tasks.filter((t) => {
    if (filter === "failed" && t.status !== "failed") return false;
    if (filter === "passed" && t.status !== "passed") return false;
    if (filter === "flaky" && t.status !== "flaky") return false;
    if (filter === "unmeasured" && t.status !== "unmeasured") return false;
    if (filter === "critical" && !t.critical) return false;

    const d = taskDerivations.get(t.id);
    if (selectedUseCases.length && !selectedUseCases.includes(d.useCaseId)) return false;
    if (selectedSubGoals.length && !selectedSubGoals.some((g) => d.subGoalLabels.has(g))) return false;
    return true;
  });

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
            {identity && (
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
              {identity ? `Run ${identity.ordinal} · agent ${identity.agentVersion}` : "Run complete"}
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
        <Button
          variant="outlined" size="small"
          startIcon={<Iconify icon="solar:refresh-linear" width={15} />}
          onClick={rerun}
          sx={{ color: "text.primary", borderColor: "divider", typography: "s2", fontWeight: 600 }}
        >
          Run again
        </Button>
        {/*
          The primary action after a failed run is not to run it again — the
          same agent against the same graders returns the same result. It is to
          change the agent, so that is the filled button and re-running is a
          neighbour of Export.
        */}
        <Button
          variant="contained" color="primary" size="small"
          startIcon={<Iconify icon="solar:magic-stick-3-linear" width={15} />}
          onClick={() => { setFixOptId(null); setFixOpen(true); }}
          sx={{ typography: "s2", fontWeight: 700 }}
        >
          Fix my agent
        </Button>
      </Stack>

      <Box sx={{ flex: 1, minHeight: 0, overflow: "auto" }}>
        <Box sx={{ p: 2 }}>
          <RunMetrics env={env} tasks={tasks} stats={stats} evals={evals} />

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
                  onClick={() => { setFilter("failed"); setTab("tasks"); }}
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
            <Tab value="omega" label="Optimization runs" sx={{ minHeight: 38 }} />
          </CustomTabs>

          {tab === "tasks" && (
            <SectionCard
              title={selected.size ? `${selected.size} selected` : "Task traces"}
              subtitle={
                selected.size
                  ? "Re-runs only these scenarios, against the same agent and graders"
                  : "One row per scenario — click through for the full trajectory"
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
                <Stack direction="row" spacing={0.75}>
                  {[
                    { id: "all", label: `All ${stats.total}` },
                    { id: "failed", label: `Failed ${stats.failed}` },
                    { id: "passed", label: `Passed ${stats.passed}` },
                    /* Only when there are any — a permanent "Flaky 0" chip
                       teaches people to stop reading it. */
                    ...(stats.flaky ? [{ id: "flaky", label: `Flaky ${stats.flaky}` }] : []),
                    /* Ours, not the agent's — filterable because the first
                       question after a bad rate is "how much of that was us". */
                    ...(stats.unmeasured ? [{ id: "unmeasured", label: `Not measured ${stats.unmeasured}` }] : []),
                    { id: "critical", label: "Critical" },
                  ].map((f) => (
                    <Chip
                      key={f.id}
                      size="small"
                      label={f.label}
                      onClick={() => setFilter(f.id)}
                      sx={{
                        height: 24, borderRadius: 0.75,
                        border: "1px solid",
                        borderColor: filter === f.id ? "primary.main" : "divider",
                        color: filter === f.id ? "primary.main" : "text.secondary",
                        bgcolor: (t) => filter === f.id ? alpha(t.palette.primary.main, 0.08) : "transparent",
                        "& .MuiChip-label": { px: 1, typography: "s3", fontWeight: 600 },
                      }}
                    />
                  ))}
                </Stack>
                )
              }
            >
              {/*
                Filter toolbar. The status chips above the card decide "how
                did it go"; these two decide "which slice of the run".
                Multi-select so a person hunting a regression can say
                "adversarial pressure OR data traps" in one filter, and ask
                for scenarios whose settlement involves the sub-goal that
                usually breaks the agent.
              */}
              <TracesFilterBar
                useCaseOptions={useCaseOptions}
                subGoalOptions={subGoalOptions}
                selectedUseCases={selectedUseCases}
                onUseCasesChange={setSelectedUseCases}
                selectedSubGoals={selectedSubGoals}
                onSubGoalsChange={setSelectedSubGoals}
                totalTasks={tasks.length}
                shownTasks={shown.length}
              />

              {shown.length === 0 ? (
                <EmptyState icon="solar:filter-linear" title="No tasks match that filter" />
              ) : (
                <TraceTable
                  tasks={shown}
                  evals={evals}
                  selected={selected}
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

          {tab === "analytics" && <RunAnalytics tasks={tasks} evals={evals} />}

          {tab === "verify" && <VerifyRun env={env} tasks={tasks} stats={stats} runId={runId} />}

          <ReplayDrawer task={replaying} seed={seed} onClose={() => setReplaying(null)} />
          {tab === "omega" && (
            <SectionCard
              title="Optimization runs"
              subtitle={`Searches started from this run · the environment's full history is under Optimizations`}
              action={
                <Button
                  size="small" variant="outlined" onClick={() => { setFixOptId(null); setFixOpen(true); }}
                  startIcon={<Iconify icon="solar:magic-stick-3-linear" width={15} />}
                  sx={{ typography: "s2", fontWeight: 700, color: "text.primary", borderColor: "divider" }}
                >
                  Fix my agent
                </Button>
              }
            >
              <OptimizationRunsList
                records={optimizationsFromRun(envState, runId)}
                onNew={() => { setFixOptId(null); setFixOpen(true); }}
                onOpen={(r) => { setFixOptId(r.id); setFixOpen(true); }}
                emptyBody="Nothing has been optimized from this run yet. Open Fix my agent to read what went wrong and start a search."
              />
            </SectionCard>
          )}
        </Box>
      </Box>

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

/**
 * Two multi-select dropdowns above the traces table: filter the run by use
 * case and by the sub-goals a scenario has to settle. Both derive their
 * options from the tasks in the run so a dropdown never offers a choice
 * that would show nothing.
 *
 * Compact by design — the primary filter (status) still lives up in the
 * SectionCard action, so this is the *second* line of narrowing and reads
 * as such: two inputs, and a "Clear" affordance that only appears when
 * either is set.
 */
/*
  Uses the platform's shared FilterPanel — the same Basic/Query popover
  the Evals and Datasets lists use, so the filter chrome (AI box, tabs,
  Property/operator/value row, Add filter, Clear all) reads the same
  across the app. Our two axes — Use case and Sub-goal — are declared as
  enum fields; the panel handles the interaction.
*/
function TracesFilterBar({
  useCaseOptions, subGoalOptions,
  selectedUseCases, onUseCasesChange,
  selectedSubGoals, onSubGoalsChange,
  totalTasks, shownTasks,
}) {
  const [anchor, setAnchor] = useState(null);
  const activeCount = selectedUseCases.length + selectedSubGoals.length;
  const anySet = activeCount > 0;

  /* FilterPanel's enum choices are plain strings. We store use cases by
     their id but the picker needs a human label, so map both ways here. */
  const useCaseLabelById = useMemo(
    () => Object.fromEntries(useCaseOptions.map((o) => [o.id, o.label])),
    [useCaseOptions],
  );
  const useCaseIdByLabel = useMemo(
    () => Object.fromEntries(useCaseOptions.map((o) => [o.label, o.id])),
    [useCaseOptions],
  );

  const filterFields = useMemo(() => [
    { value: "useCase", label: "Use case", type: "enum", choices: useCaseOptions.map((o) => o.label), operators: ["is"] },
    { value: "subGoal", label: "Sub-goal", type: "enum", choices: subGoalOptions, operators: ["is"] },
  ], [useCaseOptions, subGoalOptions]);

  /* Shape FilterPanel expects: { fieldName: [values] } */
  const currentFilters = useMemo(() => {
    const out = {};
    if (selectedUseCases.length) {
      out.useCase = selectedUseCases.map((id) => useCaseLabelById[id]).filter(Boolean);
    }
    if (selectedSubGoals.length) out.subGoal = [...selectedSubGoals];
    return out;
  }, [selectedUseCases, selectedSubGoals, useCaseLabelById]);

  const applyFilters = (result) => {
    if (!result) {
      onUseCasesChange([]);
      onSubGoalsChange([]);
      return;
    }
    /* FilterPanel's Query tab returns an array of {field, op, value}; the
       Basic tab returns {field: [values]}. Normalise into label lists. */
    let ucLabels = [];
    let sgLabels = [];
    if (Array.isArray(result)) {
      result.forEach((r) => {
        if (r.field === "useCase") ucLabels.push(...(Array.isArray(r.value) ? r.value : [r.value]));
        if (r.field === "subGoal") sgLabels.push(...(Array.isArray(r.value) ? r.value : [r.value]));
      });
    } else {
      ucLabels = Array.isArray(result.useCase) ? result.useCase : [];
      sgLabels = Array.isArray(result.subGoal) ? result.subGoal : [];
    }
    onUseCasesChange(ucLabels.map((l) => useCaseIdByLabel[l]).filter(Boolean));
    onSubGoalsChange(sgLabels);
  };

  return (
    <>
      <Stack
        direction="row" alignItems="center" spacing={1.25} flexWrap="wrap" rowGap={1}
        sx={{ px: 2.5, py: 1.25, borderBottom: "1px solid", borderColor: "divider" }}
      >
        {/*
          Same button shape the Evals list uses — outlined, mage filter
          icon, caret, count in parens when active.
        */}
        <Button
          size="small" variant="outlined"
          onClick={(e) => setAnchor(e.currentTarget)}
          startIcon={<Iconify icon="mage:filter" width={15} />}
          endIcon={<Iconify icon="solar:alt-arrow-down-linear" width={12} />}
          sx={{
            typography: "s2", fontWeight: 700, textTransform: "none",
            height: 32,
            color: anySet ? "primary.main" : "text.primary",
            borderColor: anySet ? "primary.main" : "divider",
            "&:hover": { borderColor: anySet ? "primary.main" : "text.disabled" },
          }}
        >
          Filter{anySet ? ` (${activeCount})` : ""}
        </Button>

        {/* Applied filters, one chip each — deletable inline so a single
            narrowing can be dropped without reopening the popover. */}
        {selectedUseCases.map((id) => (
          <Chip
            key={id} size="small" label={useCaseLabelById[id]}
            onDelete={() => onUseCasesChange(selectedUseCases.filter((v) => v !== id))}
            sx={{
              height: 24, borderRadius: 0.75,
              border: "1px solid",
              borderColor: (t) => alpha(t.palette.primary.main, 0.4),
              color: "primary.main",
              bgcolor: (t) => alpha(t.palette.primary.main, 0.08),
              "& .MuiChip-label": { px: 0.875, typography: "s3", fontWeight: 600 },
            }}
          />
        ))}
        {selectedSubGoals.map((label) => (
          <Chip
            key={label} size="small"
            label={label.length > 42 ? `${label.slice(0, 42)}…` : label}
            onDelete={() => onSubGoalsChange(selectedSubGoals.filter((v) => v !== label))}
            sx={{
              height: 24, borderRadius: 0.75, maxWidth: 320,
              border: "1px solid",
              borderColor: (t) => alpha(t.palette.primary.main, 0.4),
              color: "primary.main",
              bgcolor: (t) => alpha(t.palette.primary.main, 0.08),
              "& .MuiChip-label": { px: 0.875, typography: "s3", fontWeight: 600 },
            }}
          />
        ))}

        <Box flex={1} />

        {anySet && (
          <Typography sx={{ typography: "s3", color: "text.subtitle", whiteSpace: "nowrap" }}>
            {shownTasks} of {totalTasks} shown
          </Typography>
        )}
      </Stack>

      <FilterPanel
        anchorEl={anchor}
        open={!!anchor}
        onClose={() => setAnchor(null)}
        filterFields={filterFields}
        currentFilters={currentFilters}
        onApply={applyFilters}
        aiPlaceholder="Ask AI — e.g. 'show adversarial scenarios that verify identity'"
      />
    </>
  );
}

TracesFilterBar.propTypes = {
  useCaseOptions: PropTypes.array.isRequired,
  subGoalOptions: PropTypes.array.isRequired,
  selectedUseCases: PropTypes.array.isRequired,
  onUseCasesChange: PropTypes.func.isRequired,
  selectedSubGoals: PropTypes.array.isRequired,
  onSubGoalsChange: PropTypes.func.isRequired,
  totalTasks: PropTypes.number.isRequired,
  shownTasks: PropTypes.number.isRequired,
};
