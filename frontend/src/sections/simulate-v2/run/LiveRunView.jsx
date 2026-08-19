import PropTypes from "prop-types";
import { useEffect, useMemo } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { alpha } from "@mui/material/styles";
import {
  Box, Stack, Typography, Button, IconButton, Tooltip, ToggleButton,
  ToggleButtonGroup, LinearProgress,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { paths } from "src/routes/paths";
import { getEnvironment } from "../_mock/environments";
import { getSurface } from "../_mock/surfaces";
import { getEval, resolveEval } from "../_mock/evals";
import { BOOT_STEPS } from "../_mock/runStream";
import { useSimStore, useEnvState } from "../store";
import {
  StatusDot, StatusChip, ScorePill, EmptyState, PersonaBadge,
} from "../components/primitives";
import { ProvisioningPanel } from "../components/loading";
import useRunPlayer from "./useRunPlayer";
import Stage from "./stages";
import RunResults from "./RunResults";

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
  const { state } = useSimStore();
  const { envState, recordRun } = useEnvState(envId);

  const env = getEnvironment(envId) || state.myEnvironments.find((e) => e.id === envId);
  const surface = getSurface(env?.surface);

  const evals = useMemo(
    () => envState.evals.map(resolveEval).filter(Boolean),
    [envState.evals],
  );

  const player = useRunPlayer({
    seed: runId,
    scenarios: envState.scenarios,
    stage: surface.stage,
    evals,
  });

  const { phase, start, tasks, focus, focusId, setFocusId, stats, elapsed, speed, setSpeed } = player;

  // Record the run once it finishes so it shows up in history.
  useEffect(() => {
    if (phase !== "done") return;
    if (envState.runs.some((r) => r.id === runId)) return;
    recordRun({
      id: runId,
      label: `${env?.name} · ${stats.total} tasks`,
      finishedAt: new Date().toISOString(),
      total: stats.total,
      passed: stats.passed,
      failed: stats.failed,
    });
  }, [phase, runId, env, stats, envState.runs, recordRun]);

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

  /* ── pre-run provisioning ── */
  if (phase === "booting") {
    return (
      <Box sx={{ p: 2, height: "100%", minHeight: 420, display: "grid", placeItems: "center" }}>
        <Box sx={{ width: "100%", maxWidth: 560, border: "1px solid", borderColor: "divider", borderRadius: 2, bgcolor: "background.paper" }}>
          <ProvisioningPanel
            icon={surface.icon}
            accent={surface.color}
            title="Preparing the simulation"
            subtitle={`${envState.scenarios.length} tasks · 4 workers · fresh copy of ${env.name} per task`}
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
            </Stack>
            <Typography noWrap sx={{ typography: "s2", color: "text.subtitle" }}>
              {env.name} · {stats.done}/{stats.total} tasks complete · {formatMs(elapsed)} elapsed
            </Typography>
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
            "& .MuiLinearProgress-bar": { bgcolor: surface.color, transition: "transform .4s ease" },
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
              <Iconify icon="solar:danger-triangle-bold" width={11} sx={{ color: "#DC2626", flexShrink: 0 }} />
            )}
          </Stack>
          <Typography noWrap sx={{ typography: "s3", color: "text.subtitle" }}>
            {task.persona?.name}
          </Typography>
        </Box>
        {task.status === "passed" && <Iconify icon="solar:check-circle-bold" width={15} sx={{ color: "#16A34A" }} />}
        {task.status === "failed" && <Iconify icon="solar:close-circle-bold" width={15} sx={{ color: "#DC2626" }} />}
      </Stack>

      {["running", "grading"].includes(task.status) && (
        <Box sx={{ mt: 0.875, height: 2, borderRadius: 1, bgcolor: "background.neutral", overflow: "hidden" }}>
          <Box
            sx={{
              height: "100%", width: `${progress}%`, bgcolor: "#2563EB",
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
  const settled = ["passed", "failed"].includes(task.status);

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
                    bgcolor: (t) => alpha(r.color, t.palette.mode === "dark" ? 0.16 : 0.1),
                    color: r.color,
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
          <Iconify icon="solar:target-linear" width={14} sx={{ color: "#16A34A", flexShrink: 0, mt: "2px" }} />
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
