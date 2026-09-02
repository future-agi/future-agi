import PropTypes from "prop-types";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography, Button, Grid, TextField, MenuItem } from "@mui/material";
import Iconify from "src/components/iconify";
import { paths } from "src/routes/paths";
import { protoRunId } from "../_mock/executionAdapter";
import { resolveEval } from "../_mock/evals";
import { getAgentType } from "../_mock/agentTypes";
import { getSurface } from "../_mock/surfaces";
import { agentVersionsWithRuns } from "../_mock/versions";
import { SectionCard, EmptyState, StatusChip } from "../components/primitives";
import RunsSummary from "../run/RunsSummary";

/**
 * Pre-flight + run history.
 *
 * Before a run costs anyone real money, this shows exactly what is about to
 * happen: how many tasks, against which agent, graded by what. After the first
 * run it becomes the history list.
 */
export default function RunsPanel({ env, envState, onGo }) {
  const navigate = useNavigate();
  const [scope, setScope] = useState("all");
  const surface = getSurface(env.surface);
  const agentType = getAgentType(envState.agent?.typeId);
  const ready = !!envState.agent && envState.scenarios.length > 0;
  const scoped = scope === "all"
    ? envState.runs
    : envState.runs.filter((r) => r.agentVersion === scope);

  const startRun = () => {
    const runId = protoRunId(env.id, Date.now().toString(36));
    navigate(paths.dashboard.simulate.simulationRun(env.id, runId));
  };

  /*
    Once there is anything to compare, this step is the summary rather than a
    launcher. Pre-flight still exists — it moved into "Add more runs", where it
    is read at the moment it matters rather than sitting above a history nobody
    came here to skip past.
  */
  if (envState.runs.length > 0) {
    return <RunsSummary env={env} envState={envState} onGo={onGo} onStart={startRun} />;
  }

  return (
    <Box sx={{ p: 2 }}>
      <Box sx={{ mb: 3 }}>
        <Typography sx={{ typography: "m2", fontWeight: 600 }}>Run simulation</Typography>
        <Typography sx={{ typography: "s1", color: "text.secondary" }}>
          Every task runs in its own clean copy of {env.name}.
        </Typography>
      </Box>

      {/* ── pre-flight ── */}
      <SectionCard
        title="Pre-flight"
        action={
          <Button
            variant="contained"
            color="primary"
            disabled={!ready}
            onClick={startRun}
            startIcon={<Iconify icon="solar:play-bold" width={16} />}
            sx={{ typography: "s2", fontWeight: 700 }}
          >
            Start simulation
          </Button>
        }
      >
        <Grid container spacing={0} sx={{ p: 2.5 }} rowSpacing={2}>
          <PreflightItem
            xs={6} md={3}
            label="Environment"
            value={env.name}
            sub={surface.label}
            icon={surface.icon}
            color={surface.color}
            ok
          />
          <PreflightItem
            xs={6} md={3}
            label="Agent"
            value={agentType?.label || "Not connected"}
            sub={envState.agent ? "Connection verified" : "Required"}
            icon={agentType?.icon || "solar:cpu-bolt-linear"}
            color={agentType?.color}
            ok={!!envState.agent}
            onFix={() => onGo("agent")}
          />
          <PreflightItem
            xs={6} md={3}
            label="Scenarios"
            value={`${envState.scenarios.length} tasks`}
            sub={envState.scenarios.length ? `${envState.scenarios.filter((s) => s.critical).length} critical` : "Required"}
            icon="solar:layers-minimalistic-linear"
            color="#7857FC"
            ok={envState.scenarios.length > 0}
            onFix={() => onGo("scenarios")}
          />
          <PreflightItem
            xs={6} md={3}
            label="Evals"
            value={`${envState.evals.length} applied`}
            sub={envState.evals.length ? envState.evals.slice(0, 2).map((e) => resolveEval(e)?.name).join(", ") : "Optional"}
            icon="solar:shield-check-linear"
            color="#16A34A"
            ok
            warn={envState.evals.length === 0}
            onFix={() => onGo("evals")}
          />
        </Grid>

        <Stack
          direction="row"
          spacing={2}
          sx={{ px: 2.5, py: 2, borderTop: "1px solid", borderColor: "divider", bgcolor: "background.neutral" }}
        >
          <Estimate icon="solar:clock-circle-linear" label="Est. duration" value={`~${Math.max(2, Math.ceil(envState.scenarios.length * 0.7))} min`} />
          <Estimate icon="solar:bolt-circle-linear" label="Concurrency" value="4 parallel" />
          <Estimate icon="solar:dollar-minimalistic-linear" label="Est. cost" value={`$${(envState.scenarios.length * 0.08).toFixed(2)}`} />
        </Stack>
      </SectionCard>

      {/* ── history ── */}
      <Box sx={{ mt: 3 }}>
        <SectionCard
          title={`Run history (${scoped.length})`}
          subtitle={scope === "all" ? undefined : `Showing only runs of agent ${scope}`}
          action={
            /*
              Scoping to a version is what makes a history readable: a mixed
              list of numbers from three different agents compares nothing.
            */
            <TextField
              select size="small" label="Agent version" value={scope}
              onChange={(e) => setScope(e.target.value)}
              sx={{ minWidth: 150, "& .MuiInputBase-input": { typography: "s2", py: 0.75 } }}
            >
              <MenuItem value="all" sx={{ typography: "s2" }}>All versions</MenuItem>
              {agentVersionsWithRuns(envState).map((v) => (
                <MenuItem key={v.id} value={v.label} sx={{ typography: "s2" }}>
                  {v.label}{v.current ? " · current" : ""}
                </MenuItem>
              ))}
            </TextField>
          }
        >
          {scoped.length === 0 ? (
            <EmptyState
              icon="solar:play-circle-linear"
              title={scope === "all" ? "No runs yet" : `No runs of agent ${scope}`}
              body={
                scope === "all"
                  ? "Start a simulation above and you'll be able to watch every task execute live."
                  : "This environment has runs, but none against that agent version. Switch the scope, or run the suite against it."
              }
            />
          ) : (
            <Stack divider={<Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />}>
              {scoped.map((r) => (
                <Stack
                  key={r.id}
                  direction="row"
                  alignItems="center"
                  spacing={2}
                  onClick={() => navigate(paths.dashboard.simulate.simulationRun(env.id, r.id))}
                  sx={{ px: 2.5, py: 1.75, cursor: "pointer", "&:hover": { bgcolor: "action.hover" } }}
                >
                  <Box flex={1} minWidth={0}>
                    <Typography noWrap sx={{ typography: "s2", fontWeight: 600 }}>{r.label}</Typography>
                    <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                      {new Date(r.finishedAt).toLocaleString()} · {r.total} tasks
                      {r.agentVersion ? ` · agent ${r.agentVersion}` : ""}
                      {r.seed != null ? ` · seed ${r.seed}` : ""}
                    </Typography>
                  </Box>
                  <Box sx={{ width: 120, display: { xs: "none", sm: "block" } }}>
                    <PassBar passed={r.passed} total={r.total} />
                  </Box>
                  <Typography sx={{ typography: "s2", fontWeight: 700, width: 54, textAlign: "right", fontVariantNumeric: "tabular-nums" }}>
                    {Math.round((r.passed / r.total) * 100)}%
                  </Typography>
                  <StatusChip status={r.passed === r.total ? "passed" : "failed"} />
                </Stack>
              ))}
            </Stack>
          )}
        </SectionCard>
      </Box>
    </Box>
  );
}

RunsPanel.propTypes = {
  env: PropTypes.object.isRequired,
  envState: PropTypes.object.isRequired,
  onGo: PropTypes.func,
};

function PreflightItem({ xs, md, label, value, sub, icon, color, ok, warn, onFix }) {
  const state = ok ? (warn ? "#CA8A04" : "#16A34A") : "#DC2626";
  return (
    <Grid item xs={xs} md={md}>
      <Stack direction="row" spacing={1.25} alignItems="flex-start" sx={{ pr: 2 }}>
        <Box
          sx={{
            width: 32, height: 32, borderRadius: 1, display: "grid", placeItems: "center", flexShrink: 0,
            bgcolor: (t) => alpha(color || "#7857FC", t.palette.mode === "dark" ? 0.16 : 0.1),
            color: color || "#7857FC",
          }}
        >
          <Iconify icon={icon} width={16} />
        </Box>
        <Box minWidth={0}>
          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>{label}</Typography>
          <Typography noWrap sx={{ typography: "s2", fontWeight: 700 }}>{value}</Typography>
          <Stack direction="row" alignItems="center" spacing={0.5}>
            <Iconify
              icon={ok ? (warn ? "solar:info-circle-bold" : "solar:check-circle-bold") : "solar:close-circle-bold"}
              width={11}
              sx={{ color: state, flexShrink: 0 }}
            />
            <Typography noWrap sx={{ typography: "s3", color: state }}>{sub}</Typography>
            {!ok && onFix && (
              <Button size="small" onClick={onFix} sx={{ minWidth: 0, px: 0.5, typography: "s3", fontWeight: 700 }}>
                Fix
              </Button>
            )}
          </Stack>
        </Box>
      </Stack>
    </Grid>
  );
}
PreflightItem.propTypes = {
  xs: PropTypes.number, md: PropTypes.number, label: PropTypes.string, value: PropTypes.node,
  sub: PropTypes.node, icon: PropTypes.string, color: PropTypes.string,
  ok: PropTypes.bool, warn: PropTypes.bool, onFix: PropTypes.func,
};

function Estimate({ icon, label, value }) {
  return (
    <Stack direction="row" alignItems="center" spacing={0.75}>
      <Iconify icon={icon} width={15} sx={{ color: "text.subtitle" }} />
      <Typography sx={{ typography: "s2", color: "text.subtitle" }}>{label}</Typography>
      <Typography sx={{ typography: "s2", fontWeight: 700 }}>{value}</Typography>
    </Stack>
  );
}
Estimate.propTypes = { icon: PropTypes.string, label: PropTypes.string, value: PropTypes.node };

export function PassBar({ passed, total }) {
  const pct = total ? (passed / total) * 100 : 0;
  return (
    <Box sx={{ display: "flex", height: 6, borderRadius: 3, overflow: "hidden", bgcolor: "background.neutral" }}>
      <Box sx={{ width: `${pct}%`, bgcolor: "#16A34A" }} />
      <Box sx={{ width: `${100 - pct}%`, bgcolor: "#DC2626" }} />
    </Box>
  );
}
PassBar.propTypes = { passed: PropTypes.number, total: PropTypes.number };
