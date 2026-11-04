import PropTypes from "prop-types";
import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Button, MenuItem, Select, Stack, Typography } from "@mui/material";
import { paths } from "src/routes/paths";
import { ConfirmDialog } from "src/components/custom-dialog";
import { EmptyState } from "../components/primitives";
import { envOf } from "../_mock/runGroup";
import { agentReportFor, recheckCostOf } from "../_mock/compatibility";
import {
  checkSavedPatch,
  checksConnection,
  checksFor,
  connectionSpecOf,
  runConnectionCheck,
} from "../_mock/connectionCheck";
import { foundValuesOf } from "../agents/foundFacts";
import CapabilitiesPanel from "./CapabilitiesPanel";

const STEP_MS = 450;

/**
 * Agent → Capabilities (TH-8877): open the agent in one of its environments
 * — picked here, the main one first — and the Capabilities panel lists each
 * capability with its status. Re-check runs the connection check here, step
 * by step; Fix opens the repair for that capability; a re-check that needs
 * the environment rebuilt asks first.
 */
export default function AgentCompatibilityTab({
  agent,
  state,
  envId,
  mainEnvId,
  onPickEnv,
  onRepair,
  onSaveValues,
}) {
  const navigate = useNavigate();
  const [testing, setTesting] = useState(false);
  const [step, setStep] = useState(-1);
  const [confirmRebuild, setConfirmRebuild] = useState(null);
  const timers = useRef([]);
  useEffect(() => () => timers.current.forEach(clearTimeout), []);
  const envs = useMemo(
    () =>
      (agent?.usedIn || [])
        .map((u) => envOf(state, u.envId))
        .filter(Boolean)
        .sort((a, b) => (b.id === mainEnvId) - (a.id === mainEnvId)),
    [agent, state, mainEnvId],
  );
  const env = envs.find((e) => e.id === envId) || envs[0] || null;
  const envState = env ? state?.byEnv?.[env.id] : null;
  const report = useMemo(
    () => agentReportFor(agent, env, envState),
    [agent, env, envState],
  );
  if (!env || !report)
    return (
      <EmptyState
        icon="solar:shield-check-linear"
        title="No environment to check against"
        body="Capabilities are per environment — build or add this agent to one, and what it can do there appears here."
      />
    );
  const specNow = () => connectionSpecOf(agent, foundValuesOf(agent));
  const runCheck = () => {
    if (testing || !onSaveValues) return;
    const found = foundValuesOf(agent);
    const spec = connectionSpecOf(agent, found);
    const steps = checksFor(spec);
    timers.current.forEach(clearTimeout);
    timers.current = [];
    setTesting(true);
    setStep(0);
    steps.forEach((_, i) =>
      timers.current.push(setTimeout(() => setStep(i + 1), STEP_MS * (i + 1))),
    );
    timers.current.push(
      setTimeout(
        () => {
          const out = runConnectionCheck(agent, spec, found.tools);
          const failed = out.results.filter((r) => r.state === "failed").length;
          setTesting(false);
          setStep(-1);
          onSaveValues(
            checkSavedPatch(agent.values, out),
            out.passed
              ? "Connection ready — every check passed."
              : `${failed} check${failed === 1 ? "" : "s"} failed — each has a Fix in the list.`,
            out.passed ? "success" : "warning",
          );
        },
        STEP_MS * (steps.length + 1),
      ),
    );
  };
  const checkable = checksConnection(agent) && !!onSaveValues;
  /* A fix to tool routing changes how the environment binds the tool:
     checking it again rebuilds those bindings — asked first. */
  const check = () => {
    const world = report.rows.find(
      (r) => r.fixApplied && r.fix?.recheck === "world",
    );
    if (world) setConfirmRebuild(world);
    else runCheck();
  };
  const checkSteps = testing
    ? checksFor(specNow()).map((c, i) => ({
        label: c.label,
        state: i < step ? "done" : i === step ? "running" : "waiting",
      }))
    : null;
  /* Reset is proved by the environment's release checks; the rest is fixed
     on the agent. */
  const repair = (row) => {
    /* Reset is proved, and a changed tool rebuilt, on the environment. */
    if (row?.repair === "release" || row?.repair === "rebuild")
      navigate(paths.dashboard.simulate.environmentStep(env.id, "overview"));
    else onRepair(row);
  };
  const cost = recheckCostOf(env);
  /* Each affected case by its name and caller, as the Scenarios tab reads it. */
  const titles = Object.fromEntries(
    (envState.scenarios || []).map((s) => [
      s.id,
      [s.title || s.name || s.task, s.persona?.name]
        .filter(Boolean)
        .join(" · "),
    ]),
  );
  return (
    <Stack spacing={2}>
      <Stack direction="row" alignItems="center" spacing={1.5}>
        <Typography sx={{ typography: "s2", color: "text.secondary" }}>
          Environment
        </Typography>
        <Select
          size="small"
          value={env.id}
          onChange={(e) => onPickEnv(e.target.value)}
          inputProps={{ "aria-label": "Environment" }}
          sx={{
            typography: "s2",
            minWidth: 240,
            "& .MuiSelect-select": { py: 0.5 },
          }}
        >
          {envs.map((e) => (
            <MenuItem key={e.id} value={e.id} sx={{ typography: "s2" }}>
              {`${e.name}${e.id === mainEnvId ? " · main" : ""}`}
            </MenuItem>
          ))}
        </Select>
      </Stack>
      <CapabilitiesPanel
        report={report}
        agentName={agent?.name || "this agent"}
        worldName={env.name}
        titles={titles}
        checkable={checkable}
        testing={testing}
        checkSteps={checkSteps}
        onCheck={onSaveValues ? check : undefined}
        onRepair={repair}
      />
      {confirmRebuild && (
        <ConfirmDialog
          open
          onClose={() => setConfirmRebuild(null)}
          PaperProps={{ sx: { maxWidth: 480 } }}
          title={`Rebuild ${env.name}'s tool bindings to re-check?`}
          content={
            <Typography sx={{ typography: "s2", color: "text.secondary" }}>
              {`${confirmRebuild.label} is proved by the environment's build: re-checking it rebuilds how ${env.name} binds the agent's tools — about ${cost.minutes} minutes. No scenario runs.`}
            </Typography>
          }
          action={
            <Button
              size="small"
              variant="contained"
              color="primary"
              onClick={() => {
                setConfirmRebuild(null);
                runCheck();
              }}
            >
              Rebuild and re-check
            </Button>
          }
        />
      )}
    </Stack>
  );
}
AgentCompatibilityTab.propTypes = {
  agent: PropTypes.object,
  state: PropTypes.object,
  envId: PropTypes.string,
  mainEnvId: PropTypes.string,
  onPickEnv: PropTypes.func.isRequired,
  onRepair: PropTypes.func.isRequired,
  /* Saves the agent's values — the check result lands here and every row re-reads it. */
  onSaveValues: PropTypes.func,
};
