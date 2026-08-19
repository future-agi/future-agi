import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { alpha } from "@mui/material/styles";
import {
  Box, Stack, Typography, Button, Drawer, IconButton, Tab, Chip,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { CustomTabs } from "src/components/tabs/tabs";
import { paths } from "src/routes/paths";
import { getEval } from "../_mock/evals";
import {
  SectionCard, MetricTile, ScorePill, StatusChip, StatusDot, PersonaBadge, EmptyState, cardGrid,
} from "../components/primitives";
import Stage from "./stages";
import OmegaVerify from "./OmegaVerify";
import OptimizePanel from "./OptimizePanel";

/**
 * Run results.
 *
 * Ordered by what a user does next: how did it go overall, which tasks failed
 * and why, then verify the harness itself with Omega, then optimise. Each of
 * those is a tab rather than a separate page so the run stays one object.
 */
export default function RunResults({ env, runId, tasks, stats, evals, stage }) {
  const navigate = useNavigate();
  const { envId } = useParams();
  const [tab, setTab] = useState("tasks");
  const [openTask, setOpenTask] = useState(null);
  const [filter, setFilter] = useState("all");

  const evalSummary = useMemo(
    () =>
      evals.map((e) => {
        const results = tasks.map((t) => t.evalResults.find((r) => r.id === e.id)).filter(Boolean);
        const passed = results.filter((r) => r.passed).length;
        const avg = results.length ? results.reduce((a, r) => a + r.score, 0) / results.length : 0;
        return { ...e, passed, total: results.length, avg };
      }),
    [evals, tasks],
  );

  const shown = tasks.filter((t) => {
    if (filter === "failed") return t.status === "failed";
    if (filter === "passed") return t.status === "passed";
    if (filter === "critical") return t.critical;
    return true;
  });

  const failedCritical = tasks.filter((t) => t.critical && t.status === "failed").length;

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
            <Typography noWrap sx={{ typography: "s1_2", fontWeight: 700 }}>Run complete</Typography>
            <StatusChip status={stats.failed === 0 ? "passed" : "failed"} />
          </Stack>
          <Typography noWrap sx={{ typography: "s2", color: "text.subtitle" }}>
            {env.name} · {stats.total} tasks · run {runId}
          </Typography>
        </Box>
        <Button
          variant="outlined" size="small"
          startIcon={<Iconify icon="solar:download-minimalistic-linear" width={15} />}
          sx={{ color: "text.primary", borderColor: "divider", typography: "s2", fontWeight: 600 }}
        >
          Export
        </Button>
        <Button
          variant="contained"
            color="primary" size="small"
          startIcon={<Iconify icon="solar:refresh-bold" width={15} />}
          onClick={() => navigate(paths.dashboard.simulate.simulationRun(envId, `run-${Date.now().toString(36)}`))}
          sx={{ typography: "s2", fontWeight: 700 }}
        >
          Run again
        </Button>
      </Stack>

      <Box sx={{ flex: 1, minHeight: 0, overflow: "auto" }}>
        <Box sx={{ p: 2 }}>
          {/* ── headline metrics ── */}
          <Stack direction={{ xs: "column", sm: "row" }} spacing={2} sx={{ mb: 2 }}>
            <MetricTile
              label="Pass rate"
              value={`${Math.round(stats.passRate * 100)}%`}
              sub={`${stats.passed} of ${stats.total} tasks`}
              color={stats.passRate >= 0.8 ? "#16A34A" : stats.passRate >= 0.5 ? "#CA8A04" : "#DC2626"}
              icon="solar:target-linear"
              progress={stats.passRate * 100}
            />
            <MetricTile
              label="Failed"
              value={stats.failed}
              sub={failedCritical ? `${failedCritical} critical` : "none critical"}
              color={stats.failed ? "#DC2626" : "#16A34A"}
              icon="solar:close-circle-linear"
            />
            <MetricTile
              label="Total cost"
              value={`$${stats.cost.toFixed(2)}`}
              sub={`${(stats.tokens / 1000).toFixed(1)}k tokens`}
              icon="solar:dollar-minimalistic-linear"
            />
            <MetricTile
              label="Avg. duration"
              value={`${(tasks.reduce((a, t) => a + t.durationMs, 0) / tasks.length / 1000).toFixed(1)}s`}
              sub="per task"
              icon="solar:stopwatch-linear"
            />
          </Stack>

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

          {/* ── eval breakdown ── */}
          {evalSummary.length > 0 && (
            <Box sx={{ ...cardGrid(180), mb: 3 }}>
              {evalSummary.map((e) => (
                  <Box key={e.id} sx={{ p: 1.75, border: "1px solid", borderColor: "divider", borderRadius: 1.25, bgcolor: "background.paper" }}>
                    <Stack direction="row" alignItems="center" spacing={0.75} sx={{ mb: 1 }}>
                      <Iconify icon={e.icon} width={14} sx={{ color: e.color, flexShrink: 0 }} />
                      <Typography noWrap sx={{ typography: "s3", fontWeight: 600 }}>{e.name}</Typography>
                    </Stack>
                    <Typography sx={{ typography: "m3", fontWeight: 700, lineHeight: 1.1, fontVariantNumeric: "tabular-nums" }}>
                      {Math.round((e.passed / Math.max(e.total, 1)) * 100)}%
                    </Typography>
                    <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                      avg {e.avg.toFixed(2)}
                    </Typography>
                    <Box sx={{ mt: 1, height: 3, borderRadius: 2, bgcolor: "background.neutral", overflow: "hidden" }}>
                      <Box sx={{ height: "100%", width: `${(e.passed / Math.max(e.total, 1)) * 100}%`, bgcolor: e.color }} />
                    </Box>
                  </Box>
              ))}
            </Box>
          )}

          {/* ── tabs ── */}
          <CustomTabs
            value={tab}
            onChange={(_, v) => setTab(v)}
            sx={{ borderBottom: "1px solid", borderColor: "divider", mb: 2, minHeight: 38 }}
          >
            <Tab value="tasks" label={`Traces (${stats.total})`} sx={{ minHeight: 38 }} />
            <Tab value="omega" label="Verify with Omega" sx={{ minHeight: 38 }} />
            <Tab value="optimize" label="Optimize" sx={{ minHeight: 38 }} />
          </CustomTabs>

          {tab === "tasks" && (
            <SectionCard
              title="Task traces"
              subtitle="One row per scenario — click through for the full trajectory"
              action={
                <Stack direction="row" spacing={0.75}>
                  {[
                    { id: "all", label: `All ${stats.total}` },
                    { id: "failed", label: `Failed ${stats.failed}` },
                    { id: "passed", label: `Passed ${stats.passed}` },
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
              }
            >
              {shown.length === 0 ? (
                <EmptyState icon="solar:filter-linear" title="No tasks match that filter" />
              ) : (
                <Stack divider={<Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />}>
                  {shown.map((t) => (
                    <TraceRow key={t.id} task={t} evals={evals} onClick={() => setOpenTask(t)} />
                  ))}
                </Stack>
              )}
            </SectionCard>
          )}

          {tab === "omega" && <OmegaVerify env={env} tasks={tasks} stats={stats} runId={runId} />}
          {tab === "optimize" && <OptimizePanel env={env} tasks={tasks} stats={stats} />}
        </Box>
      </Box>

      {/* ── trace drawer ── */}
      <Drawer
        anchor="right"
        open={!!openTask}
        onClose={() => setOpenTask(null)}
        PaperProps={{ sx: { width: { xs: "100%", md: 720 } } }}
      >
        {openTask && (
          <TraceDetail task={openTask} stage={stage} onClose={() => setOpenTask(null)} />
        )}
      </Drawer>
    </Box>
  );
}

RunResults.propTypes = {
  env: PropTypes.object, runId: PropTypes.string, tasks: PropTypes.array,
  stats: PropTypes.object, evals: PropTypes.array, stage: PropTypes.string,
};

/* ── row ─────────────────────────────────────────────────────────────────── */

function TraceRow({ task, evals, onClick }) {
  return (
    <Stack
      direction="row" alignItems="center" spacing={2}
      onClick={onClick}
      sx={{ px: 2.5, py: 1.5, cursor: "pointer", "&:hover": { bgcolor: "action.hover" } }}
    >
      <StatusDot status={task.status} size={7} />
      <Box sx={{ flex: 1.5, minWidth: 0 }}>
        <Stack direction="row" alignItems="center" spacing={0.625}>
          <Typography noWrap sx={{ typography: "s2", fontWeight: 600 }}>{task.title}</Typography>
          {task.critical && (
            <Iconify icon="solar:danger-triangle-bold" width={12} sx={{ color: "#DC2626", flexShrink: 0 }} />
          )}
        </Stack>
        <Typography noWrap sx={{ typography: "s3", color: "text.subtitle" }}>
          {task.persona?.name} · {task.steps.length} steps · {(task.durationMs / 1000).toFixed(1)}s
        </Typography>
      </Box>

      <Stack direction="row" spacing={0.625} sx={{ flexShrink: 0, display: { xs: "none", md: "flex" } }}>
        {task.evalResults.slice(0, evals.length).map((r) => (
          <ScorePill key={r.id} score={r.score} passed={r.passed} label={`${r.name}: ${r.reason}`} />
        ))}
      </Stack>

      <Iconify icon="solar:alt-arrow-right-linear" width={15} sx={{ color: "text.subtitle", flexShrink: 0 }} />
    </Stack>
  );
}
TraceRow.propTypes = { task: PropTypes.object, evals: PropTypes.array, onClick: PropTypes.func };

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
        <Tab value="steps" label={`Trajectory (${task.steps.length})`} sx={{ minHeight: 36 }} />
        <Tab value="evals" label={`Evals (${task.evalResults.length})`} sx={{ minHeight: 36 }} />
      </CustomTabs>

      <Box sx={{ flex: 1, minHeight: 0, overflow: "auto", display: "flex", flexDirection: "column" }}>
        {tab === "replay" && (
          <Stage stage={stage} task={task} stepIndex={task.steps.length - 1} live={false} />
        )}

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
