import PropTypes from "prop-types";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Box,
  Stack,
  Typography,
  Button,
  TextField,
  MenuItem,
  Tooltip,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { paths } from "src/routes/paths";
import { protoRunId } from "../_mock/executionAdapter";
import { agentVersionsWithRuns, currentAgentVersion } from "../_mock/versions";
import { runReadiness } from "../_mock/setupGaps";
import { runScopeOf } from "../_mock/runScope";
import { EmptyState } from "../components/primitives";
import RunsSummary from "../run/RunsSummary";
import useRunFitCheck from "../run/useRunFitCheck";
import useRunGroup from "../run/useRunGroup";
import AlsoRunIn from "../run/AlsoRunIn";
import { useEnvState } from "../store";
import { runEnvVersion } from "../_mock/toolFit";
import { snapshotLabelOf } from "../_mock/envSnapshot";
import { envLabelOf } from "../_mock/envVersionKinds";
import useEnvViewAs from "./useEnvViewAs";
import { parallelOf, runSizeOf } from "../_mock/runStream";
import { runDefaultsOf } from "../_mock/rsiModel";
import { priceOf } from "../_mock/pairs";
import { creditsLabel } from "../_mock/metering";

/**
 * Run history.
 *
 * Before the first run it says there are none and offers the baseline run;
 * after it, the history list.
 */
export default function RunsPanel({ env, envState, onGo }) {
  const navigate = useNavigate();
  const { patch } = useEnvState(env.id);
  const { check: checkRunFit, dialog: runFitDialog } = useRunFitCheck(
    env,
    envState,
    patch,
  );
  const [scope, setScope] = useState("all");
  /* Optional: one environment version, one scenario-set version. */
  const [envScope, setEnvScope] = useState("all");
  /* Other environments testing this agent that the first run also starts in. */
  const [alsoRunIn, setAlsoRunIn] = useState([]);
  const runGroup = useRunGroup(env.id);
  const groupWith = alsoRunIn.filter((id) =>
    runGroup.candidates.some((c) => c.envId === id && c.ready),
  );
  /* Same readiness as every other Run button — evaluations included. */
  const readiness = runReadiness(envState);
  const ready = readiness.ready;
  /* Viewing an older environment version: only the runs taken on it. */
  const { viewAs } = useEnvViewAs(env, envState);

  /* What the first run would be: the same scenarios the header's Run takes
     (runScopeOf) × the repeats Settings sets (3 unless changed), at the
     conversations-at-once it sets. */
  const { repeats: defaultRepeats } = runDefaultsOf(envState);
  const runScope = runScopeOf(env, envState);
  const size = runSizeOf({
    scenarios: runScope.count,
    repeats: defaultRepeats,
    surface: env.surface,
    parallel: parallelOf(envState),
  });

  const startRun = () =>
    checkRunFit(() => {
      if (groupWith.length) {
        runGroup.start(groupWith, { trials: defaultRepeats });
        return;
      }
      const runId = protoRunId(env.id, Date.now().toString(36));
      /* Scenarios waiting on an open question sit out, as from the header. */
      const only =
        runScope.sitOut && runScope.count
          ? `&only=${runScope.ids.join(",")}`
          : "";
      navigate(
        `${paths.dashboard.simulate.simulationRun(env.id, runId)}?trials=${defaultRepeats}${only}`,
      );
    });

  /*
    Once there is anything to compare, this step is the summary rather than a
    launcher; new runs start from the header's Run simulation button.

    Results are scoped by agent version (PRD v7 AC-14.1, 14.3): pin the view
    to one agent version — and optionally one environment or scenario-set
    version — and only that version's runs, traces and verdicts show.
  */
  if (envState.runs.length > 0) {
    const base = viewAs
      ? envState.runs.filter((r) => runEnvVersion(env, envState, r) === viewAs)
      : envState.runs;
    if (viewAs && !base.length) {
      return (
        <Box sx={{ p: 2 }}>
          <EmptyState
            icon="solar:play-circle-linear"
            title={`No runs on environment ${envLabelOf(env, envState, viewAs)}`}
            body="Every run here was taken on another version. Go back to current to see them."
          />
        </Box>
      );
    }
    const inScope = base.filter(
      (r) =>
        (scope === "all" || r.agentVersion === scope) &&
        (viewAs ||
          envScope === "all" ||
          snapshotLabelOf(r, env, envState) === envScope),
    );
    return (
      <>
        <RunsScopeBar
          env={env}
          envState={envState}
          runs={base}
          shown={inScope.length}
          agent={scope}
          onAgent={setScope}
          envVersion={viewAs ? null : envScope}
          onEnvVersion={viewAs ? null : setEnvScope}
        />
        {inScope.length ? (
          <RunsSummary
            key={`${scope}|${envScope}`}
            env={env}
            envState={{ ...envState, runs: inScope }}
            onGo={onGo}
          />
        ) : (
          <Box sx={{ p: 2 }}>
            <EmptyState
              icon="solar:play-circle-linear"
              title={`No runs of agent ${scope} on this selection`}
              body="This environment has runs, but none with that agent and environment version together. Widen the scope, or run the suite against it."
            />
          </Box>
        )}
      </>
    );
  }

  /* No runs yet: the tab stays, and offers the first run — the baseline
     every improvement is measured against (the same action as the
     Scenarios tab's "No baseline yet" strip). */
  return (
    <Box sx={{ p: 2 }}>
      {runFitDialog}
      <EmptyState
        icon="solar:play-circle-linear"
        title="No runs yet"
        body="The first run is the baseline improvements are measured against."
        action={
          <Stack direction="row" alignItems="center" spacing={1}>
            <AlsoRunIn
              candidates={runGroup.candidates}
              value={alsoRunIn}
              onChange={setAlsoRunIn}
              agentVersion={currentAgentVersion(envState)?.label}
            />
            <Tooltip
              arrow
              title={
                ready
                  ? `${runScope.sitOut ? `Runs ${runScope.count} of the ${runScope.runnable} scenarios that can run (${runScope.sitOut} wait on Needs input)` : `Runs all ${runScope.count} scenarios`}, ${defaultRepeats} times each · ≈ ${creditsLabel(priceOf(env, envState, size.conversations, 1).usd)}`
                  : readiness.reason
              }
            >
              <span>
                <Button
                  variant="contained"
                  color="primary"
                  size="small"
                  disabled={!ready}
                  onClick={startRun}
                  startIcon={<Iconify icon="solar:play-bold" width={14} />}
                  sx={{ whiteSpace: "nowrap" }}
                >
                  {groupWith.length
                    ? `Run baseline in ${groupWith.length + 1} environments`
                    : "Run baseline"}
                </Button>
              </span>
            </Tooltip>
          </Stack>
        }
      />
    </Box>
  );
}

RunsPanel.propTypes = {
  env: PropTypes.object.isRequired,
  envState: PropTypes.object.isRequired,
  onGo: PropTypes.func,
};

export function PassBar({ passed, total }) {
  const pct = total ? (passed / total) * 100 : 0;
  return (
    <Box
      sx={{
        display: "flex",
        height: 6,
        borderRadius: 3,
        overflow: "hidden",
        bgcolor: "background.neutral",
      }}
    >
      <Box sx={{ width: `${pct}%`, bgcolor: "status.success.solid" }} />
      <Box sx={{ width: `${100 - pct}%`, bgcolor: "status.danger.solid" }} />
    </Box>
  );
}
PassBar.propTypes = { passed: PropTypes.number, total: PropTypes.number };

/*
  The scope the Runs tab reads at (AC-14.1): an agent version, and
  optionally an environment version and a scenario-set version. Compact,
  one line — the selectors only appear when there is more than one value to
  pick between.
*/
function RunsScopeBar({
  env,
  envState,
  runs,
  shown,
  agent,
  onAgent,
  envVersion,
  onEnvVersion,
}) {
  const agents = agentVersionsWithRuns(envState).filter((v) => v.runs > 0);
  /* Environment versions (snapshots), newest first. */
  const envs = [
    ...new Set(
      runs.map((r) => snapshotLabelOf(r, env, envState)).filter(Boolean),
    ),
  ].sort((a, b) => Number(b.slice(1)) - Number(a.slice(1)));
  const selectSx = {
    minWidth: 132,
    "& .MuiInputBase-input": { typography: "s2", py: 0.5 },
  };
  /* Newest version first, like the runs table. */
  const chips = [
    { label: "all", text: "All", count: runs.length },
    ...[...agents].reverse().map((v) => ({
      label: v.label,
      text: v.label,
      count: v.runs,
      note: v.current ? "current" : "",
    })),
  ];
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={1}
      flexWrap="wrap"
      rowGap={1}
      sx={{ px: 2, pt: 2, pb: 0.5 }}
    >
      <Typography sx={{ typography: "s2", color: "text.tertiary", mr: 0.25 }}>
        Agent version
      </Typography>
      {/* One click per version — every number below reads that version's runs alone. */}
      {chips.map((c) => {
        const on = agent === c.label;
        return (
          <Tooltip
            key={c.label}
            arrow
            title={c.note || ""}
            disableHoverListener={!c.note}
          >
            <Box
              role="button"
              tabIndex={0}
              aria-pressed={on}
              onClick={() => onAgent(c.label)}
              onKeyDown={(e) => {
                if (e.key === "Enter") onAgent(c.label);
              }}
              sx={{
                display: "inline-flex",
                alignItems: "center",
                gap: 0.625,
                height: 28,
                px: 1.25,
                borderRadius: "4px",
                cursor: "pointer",
                border: "1px solid",
                borderColor: on ? "text.primary" : "divider",
                bgcolor: on ? "action.selected" : "transparent",
                "&:hover": {
                  borderColor: on ? "text.primary" : "text.disabled",
                },
              }}
            >
              <Typography sx={{ typography: "s2", fontWeight: on ? 600 : 500 }}>
                {c.text}
              </Typography>
              <Typography
                sx={{
                  typography: "s3",
                  color: "text.tertiary",
                  fontVariantNumeric: "tabular-nums",
                }}
              >
                {c.count}
              </Typography>
            </Box>
          </Tooltip>
        );
      })}
      {onEnvVersion && envs.length > 1 && (
        <TextField
          select
          size="small"
          value={envVersion}
          onChange={(e) => onEnvVersion(e.target.value)}
          sx={{ ...selectSx, ml: "16px !important" }}
        >
          <MenuItem value="all" sx={{ typography: "s2" }}>
            All environment versions
          </MenuItem>
          {envs.map((v) => (
            <MenuItem
              key={v}
              value={v}
              sx={{ typography: "s2" }}
            >{`Environment ${v}`}</MenuItem>
          ))}
        </TextField>
      )}
      {(agent !== "all" || (envVersion && envVersion !== "all")) && (
        <Typography
          sx={{ typography: "s3", color: "text.tertiary" }}
        >{`${shown} of ${runs.length} runs`}</Typography>
      )}
    </Stack>
  );
}

RunsScopeBar.propTypes = {
  env: PropTypes.object,
  envState: PropTypes.object,
  runs: PropTypes.array,
  shown: PropTypes.number,
  agent: PropTypes.string,
  onAgent: PropTypes.func,
  envVersion: PropTypes.string,
  onEnvVersion: PropTypes.func,
};
