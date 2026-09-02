import PropTypes from "prop-types";
import { useEffect, useMemo, useRef } from "react";
import { useParams, useNavigate, useSearchParams } from "react-router-dom";
import {
  Box, Stack, Typography, Button, IconButton, Tooltip, ToggleButton,
  ToggleButtonGroup, LinearProgress,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { paths } from "src/routes/paths";
import RunResults from "./RunResults";
import { publishRun, installMockExecutionAdapter } from "../_mock/executionAdapter";
import { rebuildRun } from "../_mock/comparison";
import { currentAgentVersion, currentEnvVersion, versionNumber } from "../_mock/versions";
import { twinTimelineFor } from "../_mock/twins";
import { getEnvironment } from "../_mock/environments";
import { getSurface } from "../_mock/surfaces";
import { getEval, resolveEval } from "../_mock/evals";
import { BOOT_STEPS } from "../_mock/runStream";
import { SHADOW_SUMMARY } from "../_mock/sandbox";
import { useSimStore, useEnvState } from "../store";
import {
  StatusDot, StatusChip, ScorePill, EmptyState, PersonaBadge, pulse,
} from "../components/primitives";
import { ProvisioningPanel } from "../components/loading";
import useRunPlayer from "./useRunPlayer";
import Stage from "./stages";

/**
 * The live run.
 *
 * Three columns: what is queued, what is happening right now, and what the
 * graders think of it. The middle column is the point of the whole screen —
 * a user should be able to leave this open on a second monitor and understand
 * their agent's behaviour without reading a single log line.
 */
export default function LiveRunView() {
  const { envId, runId } = useParams();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const { state } = useSimStore();
  const { envState, recordRun } = useEnvState(envId);

  const env = getEnvironment(envId) || state.myEnvironments.find((e) => e.id === envId);
  const surface = getSurface(env?.surface);

  const evals = useMemo(
    () => envState.evals.map(resolveEval).filter(Boolean),
    [envState.evals],
  );

  /*
    A run is not always a run of everything. `?only=` carries the subset a
    comparison sent here — re-run these four blockers against the new version —
    and the run records what it actually covered, so nothing downstream reads a
    four-scenario check as a full sweep that lost twenty-eight rows.
  */
  const only = params.get("only");
  const scenarios = useMemo(() => {
    if (!only) return envState.scenarios;
    const wanted = new Set(only.split(","));
    const subset = envState.scenarios.filter((sc) => wanted.has(sc.id));
    return subset.length ? subset : envState.scenarios;
  }, [only, envState.scenarios]);

  /*
    Every scenario, three times. The other side of the conversation is sampled,
    so one shot per scenario reports a coin flip as a verdict — and three
    samples is the smallest n where "passed twice, failed once" is sayable.
  */
  const repeats = 3;

  const player = useRunPlayer({
    seed: runId,
    scenarios,
    stage: surface.stage,
    evals,
    tools: env?.tools || [],
    repeats,
    phrasing: versionNumber(currentAgentVersion(envState).label),
  });

  const {
    phase, start, tasks, focus, focusId, setFocusId, stats, elapsed, speed, setSpeed,
    sinceLastEvent, stalled,
  } = player;

  /*
    A run that is already in history is being read, not run. Replaying it —
    provisioning animation and all — was the old behaviour and it is wrong
    twice over: it wastes the reader's time, and it invites the suspicion that
    the numbers are generated fresh each time rather than being that run's.

    The latch matters because a run finishing puts itself into history: without
    it, the live view would flip to read-only in the same commit that finished
    it.
  */
  const stored = envState.runs.find((r) => r.id === runId);
  const wasLive = useRef(false);
  useEffect(() => {
    if (phase === "running") wasLive.current = true;
  }, [phase]);
  const readOnly = !wasLive.current && !!stored;

  const storedTasks = useMemo(
    () => (readOnly && env ? rebuildRun(env, envState, stored) : []),
    [readOnly, env, envState, stored],
  );

  const storedStats = useMemo(() => {
    const passed = storedTasks.filter((t) => t.status === "passed").length;
    const failed = storedTasks.filter((t) => t.status === "failed").length;
    const flaky = storedTasks.filter((t) => t.status === "flaky").length;
    const unmeasured = storedTasks.filter((t) => t.status === "unmeasured").length;
    const measured = storedTasks.filter((t) => t.status !== "unmeasured");
    return {
      total: storedTasks.length,
      passed,
      failed,
      flaky,
      unmeasured,
      measured: measured.length,
      repeats: stored?.repeats || 1,
      /* Same rule as the live run: the mean of the per-scenario proportions,
         over the scenarios that produced one. */
      passRate: measured.length
        ? measured.reduce((a, t) => a + (t.passShare ?? (t.status === "passed" ? 1 : 0)), 0) / measured.length
        : 0,
      tokens: storedTasks.reduce((a, t) => a + (t.tokens || 0), 0),
      cost: storedTasks.reduce((a, t) => a + (t.cost || 0), 0),
    };
  }, [storedTasks, stored]);


  /*
    Finishing hands the run to the execution-detail screen and goes there.

    That screen is real product code and fetches everything it renders, so the
    run is published to the mock adapter first — the adapter answers those
    fetches for this run id and passes every other request through untouched.
    Publishing before navigating matters: the screen's first query fires on
    mount, and an empty answer would be cached.
  */
  useEffect(() => {
    if (phase !== "done") return;
    installMockExecutionAdapter();
    publishRun(runId, { tasks, startedAt: new Date(Date.now() - elapsed).toISOString() });
  }, [phase, runId, tasks, elapsed]);

  // Record the run once it finishes so it shows up in history.
  useEffect(() => {
    if (phase !== "done") return;
    if (envState.runs.some((r) => r.id === runId)) return;
    recordRun({
      id: runId,
      label: only
        ? `${stats.total} scenario${stats.total === 1 ? "" : "s"} re-run`
        : `${env?.name} · ${stats.total} tasks`,
      finishedAt: new Date().toISOString(),
      total: stats.total,
      passed: stats.passed,
      failed: stats.failed,
      flaky: stats.flaky,
      /* Kept on the run so history can show what the run could not measure —
         a rate with no denominator behind it is not reproducible. */
      unmeasured: stats.unmeasured,
      /* Which agent version was current when this started — pinned, never
         inferred later from the run's place in the list. */
      agentVersion: currentAgentVersion(envState).label,
      /* And which env version — a run is `env × agent`, so both halves
         travel on it. Lets the runs table read "agent v2 · env v3" and
         lets the compare view distinguish "the agent changed" from "the
         world changed" instead of collapsing both into one axis. */
      envVersion: currentEnvVersion(env, envState).label,
      /* What it actually covered, and how many samples each row is made of.
         A comparison needs both before it can claim two runs are comparable. */
      scenarioIds: scenarios.map((sc) => sc.id),
      repeats,
      partial: !!only,
      /* One past the highest ordinal ever stamped here, so numbers are stable
         even after a run is deleted. */
      ordinal: Math.max(0, ...envState.runs.map((r) => r.ordinal || 0)) + 1,
      seed: 7,
      /* Twin write count summed across every task in the run. Stamped
         at record time so the runs history can show it later without
         re-deriving from task data (which is cached in the adapter and
         not always available). null for non-twin envs. */
      twinWrites: envState.twinBacking
        ? tasks.reduce((sum, task) => {
            const t = twinTimelineFor(envState, task);
            return sum + Object.values(t.writesByService).reduce((a, b) => a + b, 0);
          }, 0)
        : null,
    });
  }, [phase, runId, env, envState, stats, scenarios, only, recordRun]);

  /*
    A finished run goes to the summary, not to its own results. On its own a
    result page answers "how did that go"; the question someone has after
    changing their agent and running again is whether it moved, and only the
    list of runs can answer that. The run just finished is the top row there,
    one click from everything this screen used to show.
  */
  useEffect(() => {
    if (phase !== "done" || readOnly) return;
    navigate(paths.dashboard.simulate.environmentStep(envId, "runs"), { replace: true });
  }, [phase, readOnly, navigate, envId]);

  if (!env) {
    return (
      <Box sx={{ p: 2 }}>
        <EmptyState
          icon="solar:danger-triangle-linear"
          title="Environment not found"
          action={
            <Button variant="contained"
            color="primary" size="small" onClick={() => navigate(paths.dashboard.simulate.environments)}>
              Back to environments
            </Button>
          }
        />
      </Box>
    );
  }

  if (envState.scenarios.length === 0) {
    return (
      <Box sx={{ p: 2 }}>
        <EmptyState
          icon="solar:layers-minimalistic-linear"
          title="Nothing to run"
          body="This environment has no scenarios selected yet."
          action={
            <Button
              variant="contained"
            color="primary" size="small"
              onClick={() => navigate(paths.dashboard.simulate.environmentStep(envId, "scenarios"))}
            >
              Add scenarios
            </Button>
          }
        />
      </Box>
    );
  }

  /* ── a finished run, read back ── */
  if (readOnly) {
    return (
      <RunResults
        env={env}
        runId={runId}
        tasks={storedTasks}
        stats={storedStats}
        evals={evals}
        stage={surface.stage}
      />
    );
  }

  /* ── pre-run provisioning ── */
  if (phase === "booting") {
    return (
      <Box sx={{ p: 2, height: "100%", minHeight: 420, display: "grid", placeItems: "center" }}>
        <Box sx={{ width: "100%", maxWidth: 560, border: "1px solid", borderColor: "divider", borderRadius: 2, bgcolor: "background.paper" }}>
          <ProvisioningPanel
            icon={surface.icon}
            accent={surface.color}
            title="Preparing the simulation"
            /* What this run is actually about to do — a re-run of four
               scenarios is not "17 tasks", and the boot panel is the first
               place that claim appears. */
            subtitle={`${scenarios.length}${only ? ` of ${envState.scenarios.length}` : ""} tasks × ${repeats} samples · 4 workers · a shadow agent and a fresh copy of ${env.name} per task`}
            steps={BOOT_STEPS[surface.stage] || BOOT_STEPS.voice}
            onDone={start}
          />
        </Box>
      </Box>
    );
  }

  /* ── results ── */
  if (phase === "done") {
    return <RunResults env={env} runId={runId} tasks={tasks} stats={stats} evals={evals} stage={surface.stage} />;
  }

  /* ── live ── */
  const live = focus && ["running", "grading"].includes(focus.status);

  return (
    <Box sx={{ display: "flex", flexDirection: "column", height: "100%", minHeight: 0 }}>
      {/* ── run header ── */}
      <Box sx={{ borderBottom: "1px solid", borderColor: "divider", flexShrink: 0 }}>
        <Stack direction="row" alignItems="center" spacing={2} sx={{ px: 3, py: 1.75 }}>
          <IconButton size="small" onClick={() => navigate(paths.dashboard.simulate.environmentStep(envId, "runs"))}>
            <Iconify icon="solar:alt-arrow-left-linear" width={18} sx={{ color: "text.subtitle" }} />
          </IconButton>

          <Box minWidth={0} flex={1}>
            <Stack direction="row" alignItems="center" spacing={1}>
              <Typography noWrap sx={{ typography: "s1_2", fontWeight: 700 }}>
                Simulation running
              </Typography>
              <StatusChip status="running" />
              {/* The isolation claim, where a viewer is most likely to doubt it. */}
              <Tooltip arrow title={SHADOW_SUMMARY}>
                <Stack
                  direction="row" alignItems="center" spacing={0.5}
                  sx={{
                    px: 0.75, height: 22, borderRadius: 0.75, color: "text.subtitle",
                    border: "1px solid", borderColor: "divider",
                  }}
                >
                  <Iconify icon="solar:shield-keyhole-linear" width={12} />
                  <Typography sx={{ typography: "s3", fontWeight: 600 }}>shadow sandbox</Typography>
                </Stack>
              </Tooltip>
            </Stack>
            <Typography noWrap sx={{ typography: "s2", color: "text.subtitle" }}>
              {env.name} · {stats.done}/{stats.total} tasks complete · {formatMs(elapsed)} elapsed
            </Typography>
            {/*
              Slow and stalled look identical on a progress bar, and telling
              them apart is the only question anyone has while waiting. So the
              heartbeat is stated: how long since anything moved, and a warning
              once that gap stops being normal.
            */}
            {phase === "running" && (
              <Stack direction="row" alignItems="center" spacing={0.625} sx={{ mt: 0.25 }}>
                <Box
                  sx={{
                    width: 6, height: 6, borderRadius: "50%", flexShrink: 0,
                    bgcolor: stalled ? "#DC2626" : "#16A34A",
                    animation: stalled ? "none" : `${pulse} 1.6s ease-in-out infinite`,
                  }}
                />
                <Typography sx={{ typography: "s3", color: stalled ? "#DC2626" : "text.subtitle" }}>
                  {stalled
                    ? `No heartbeat for ${sinceLastEvent}s — the run may be stalled`
                    : `Healthy · last event ${sinceLastEvent}s ago`}
                </Typography>
              </Stack>
            )}
          </Box>

          <LiveCounter label="Passed" value={stats.passed} color="#16A34A" />
          <LiveCounter label="Failed" value={stats.failed} color="#DC2626" />
          <LiveCounter label="Running" value={stats.active} color="#2563EB" />

          <ToggleButtonGroup
            exclusive size="small" value={speed}
            onChange={(_, v) => v && setSpeed(v)}
            sx={{
              "& .MuiToggleButton-root": {
                px: 1, py: 0.375, typography: "s3", fontWeight: 700,
                border: "1px solid !important", borderColor: "divider !important",
                borderRadius: "6px !important", mx: 0.25, color: "text.secondary",
              },
            }}
          >
            {[1, 2, 4].map((s) => (
              <ToggleButton key={s} value={s}>{s}×</ToggleButton>
            ))}
          </ToggleButtonGroup>

          <Tooltip title="Stop run" arrow>
            <IconButton
              size="small"
              onClick={() => navigate(paths.dashboard.simulate.environmentStep(envId, "runs"))}
            >
              <Iconify icon="solar:stop-circle-linear" width={19} sx={{ color: "#DC2626" }} />
            </IconButton>
          </Tooltip>
        </Stack>

        <LinearProgress
          variant="determinate"
          value={stats.progress}
          sx={{
            height: 3, bgcolor: "transparent",
            "& .MuiLinearProgress-bar": { bgcolor: "primary.main", transition: "transform .4s ease" },
          }}
        />
      </Box>

      {/* ── three columns ── */}
      <Box sx={{ display: "flex", flex: 1, minHeight: 0 }}>
        {/* tasks */}
        <Box sx={{ width: 300, flexShrink: 0, borderRight: "1px solid", borderColor: "divider", overflow: "auto", display: { xs: "none", md: "block" } }}>
          <Typography
            sx={{
              px: 2, py: 1.25, typography: "s3", fontWeight: 700, color: "text.subtitle",
              textTransform: "uppercase", letterSpacing: .4,
              position: "sticky", top: 0, bgcolor: "background.default", zIndex: 1,
              borderBottom: "1px solid", borderColor: "divider",
            }}
          >
            Tasks ({stats.total})
          </Typography>
          <Stack divider={<Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />}>
            {tasks.map((t) => (
              <TaskRow
                key={t.id}
                task={t}
                active={t.id === focusId}
                onClick={() => setFocusId(t.id)}
              />
            ))}
          </Stack>
        </Box>

        {/* stage */}
        <Box sx={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column" }}>
          {focus && (
            <>
              <Stack
                direction="row" alignItems="center" spacing={1.5}
                sx={{ px: 2.5, py: 1.5, borderBottom: "1px solid", borderColor: "divider", flexShrink: 0 }}
              >
                <StatusDot status={focus.status} />
                <Box minWidth={0} flex={1}>
                  <Typography noWrap sx={{ typography: "s2", fontWeight: 700 }}>{focus.title}</Typography>
                  <Typography noWrap sx={{ typography: "s3", color: "text.subtitle" }}>{focus.task}</Typography>
                </Box>
                <Typography sx={{ typography: "s3", color: "text.subtitle", flexShrink: 0 }}>
                  step {Math.max(0, focus.stepIndex) + 1}/{focus.steps.length}
                </Typography>
              </Stack>
              <Stage
                stage={surface.stage}
                task={focus}
                stepIndex={focus.stepIndex}
                live={live}
              />
            </>
          )}
        </Box>

        {/* live evals */}
        <Box sx={{ width: 288, flexShrink: 0, borderLeft: "1px solid", borderColor: "divider", overflow: "auto", display: { xs: "none", lg: "block" } }}>
          <LiveEvalPanel task={focus} evals={evals} />
        </Box>
      </Box>
    </Box>
  );
}

/* ── pieces ──────────────────────────────────────────────────────────────── */

function LiveCounter({ label, value, color }) {
  return (
    <Stack alignItems="center" sx={{ px: 1, display: { xs: "none", sm: "flex" } }}>
      <Typography sx={{ typography: "s1", fontWeight: 700, color, lineHeight: 1.1, fontVariantNumeric: "tabular-nums" }}>
        {value}
      </Typography>
      <Typography sx={{ typography: "s3", color: "text.subtitle" }}>{label}</Typography>
    </Stack>
  );
}

function TaskRow({ task, active, onClick }) {
  const progress = task.steps.length
    ? ((task.stepIndex + 1) / task.steps.length) * 100
    : 0;
  return (
    <Box
      onClick={onClick}
      sx={{
        px: 2, py: 1.375, cursor: "pointer", position: "relative",
        bgcolor: active ? "action.hover" : "transparent",
        "&:hover": { bgcolor: "action.hover" },
      }}
    >
      {active && (
        <Box sx={{ position: "absolute", left: 0, top: 0, bottom: 0, width: 2, bgcolor: "primary.main" }} />
      )}
      <Stack direction="row" alignItems="center" spacing={1.25}>
        <StatusDot status={task.status} size={7} />
        <Box flex={1} minWidth={0}>
          <Stack direction="row" alignItems="center" spacing={0.5}>
            <Typography noWrap sx={{ typography: "s2", fontWeight: active ? 700 : 500 }}>
              {task.title}
            </Typography>
            {task.critical && (
              <Iconify icon="solar:danger-triangle-bold" width={11} sx={{ color: "text.subtitle", flexShrink: 0 }} />
            )}
          </Stack>
          <Typography noWrap sx={{ typography: "s3", color: "text.subtitle" }}>
            {task.persona?.name}
          </Typography>
        </Box>
      </Stack>

      {["running", "grading"].includes(task.status) && (
        <Box sx={{ mt: 0.875, height: 2, borderRadius: 1, bgcolor: "background.neutral", overflow: "hidden" }}>
          <Box
            sx={{
              height: "100%", width: `${progress}%`, bgcolor: "primary.main",
              transition: "width .4s ease",
            }}
          />
        </Box>
      )}
    </Box>
  );
}

/**
 * Evals scoring in real time.
 *
 * They resolve one at a time as the grader works through them, which is both
 * honest about how grading actually runs and far more legible than every score
 * appearing at once.
 */
function LiveEvalPanel({ task, evals }) {
  if (!task) return null;
  const grading = task.status === "grading";
  const settled = ["passed", "failed", "flaky", "unmeasured"].includes(task.status);

  return (
    <Box>
      <Typography
        sx={{
          px: 2, py: 1.25, typography: "s3", fontWeight: 700, color: "text.subtitle",
          textTransform: "uppercase", letterSpacing: .4,
          borderBottom: "1px solid", borderColor: "divider",
        }}
      >
        Evals
      </Typography>

      {evals.length === 0 ? (
        <EmptyState
          icon="solar:shield-cross-linear"
          title="No evals applied"
          body="You'll get traces, but nothing scoring whether the agent was right."
        />
      ) : (
        <Stack divider={<Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />}>
          {task.evalResults.map((r, i) => {
            const resolved = (grading || settled) && i <= task.evalIndex;
            return (
              <Stack key={r.id} direction="row" alignItems="center" spacing={1.25} sx={{ px: 2, py: 1.375 }}>
                <Box
                  sx={{
                    width: 26, height: 26, borderRadius: 0.75, display: "grid", placeItems: "center", flexShrink: 0,
                    bgcolor: "background.neutral", color: "text.subtitle",
                  }}
                >
                  <Iconify icon={getEval(r.id)?.icon || "solar:shield-check-linear"} width={14} />
                </Box>
                <Typography noWrap sx={{ flex: 1, typography: "s2", fontWeight: 600 }}>{r.name}</Typography>
                {resolved ? (
                  <ScorePill score={r.score} passed={r.passed} label={r.reason} />
                ) : (
                  <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                    {task.status === "queued" ? "—" : "pending"}
                  </Typography>
                )}
              </Stack>
            );
          })}
        </Stack>
      )}

      {/* Scenario context — what "right" looks like for this task. */}
      <Box sx={{ p: 2, borderTop: "1px solid", borderColor: "divider" }}>
        <Typography sx={{ typography: "s3", fontWeight: 700, color: "text.subtitle", textTransform: "uppercase", letterSpacing: .4, mb: 1.25 }}>
          Scenario
        </Typography>
        <PersonaBadge persona={task.persona} />
        <Stack direction="row" spacing={0.875} sx={{ mt: 1.5 }}>
          <Iconify icon="solar:target-linear" width={14} sx={{ color: "text.subtitle", flexShrink: 0, mt: "2px" }} />
          <Typography sx={{ typography: "s3", color: "text.secondary" }}>{task.expected}</Typography>
        </Stack>
      </Box>
    </Box>
  );
}

function formatMs(ms) {
  const s = Math.floor(ms / 1000);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

LiveCounter.propTypes = {
  label: PropTypes.string,
  value: PropTypes.node,
  color: PropTypes.string,
};

TaskRow.propTypes = {
  task: PropTypes.object,
  active: PropTypes.bool,
  onClick: PropTypes.func,
};

LiveEvalPanel.propTypes = {
  task: PropTypes.object,
  evals: PropTypes.array,
};
