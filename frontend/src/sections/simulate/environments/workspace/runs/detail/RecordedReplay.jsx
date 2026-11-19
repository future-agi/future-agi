import PropTypes from "prop-types";
import { useState } from "react";
import {
  Button,
  Chip,
  Link,
  MenuItem,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import { useMutation } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { enqueueSnackbar } from "notistack";
import ModalWrapper from "src/components/ModalWrapper/ModalWrapper";
import { paths } from "src/routes/paths";
import {
  replayTrialWithRecordedTools,
  runHarnessEnvironment,
} from "src/api/simulate-environments/harnessEnvironments";
import { useEnvironmentVersions } from "src/api/simulate-environments/versions";
import { runOutcome } from "./trace/traceTable.constants";
import { RECORDED_REPLAY_COPY as COPY } from "./recordedReplay.constants";

const SAME_AGENT = "";

const originalShape = PropTypes.shape({
  call_execution_id: PropTypes.string,
  test_execution_id: PropTypes.string,
  outcome: PropTypes.string,
});

// The label a recorded replay carries wherever it shows: a run row, a run
// header, a trial.
export function RecordedReplayChip() {
  return (
    <Tooltip arrow title={COPY.about}>
      <Chip
        size="small"
        variant="outlined"
        label={COPY.label}
        data-testid="recorded-replay-chip"
      />
    </Tooltip>
  );
}

// On a replayed trial: the label, how the original trial ended, and the way to it.
export function RecordedReplayOrigin({ envId, testId, original }) {
  const navigate = useNavigate();
  const outcome = original.outcome ? runOutcome(original.outcome).label : null;
  const open = () =>
    navigate(
      `${paths.dashboard.simulate.environments.execution(
        envId,
        testId,
        original.test_execution_id,
      )}?rowId=${original.call_execution_id}`,
    );
  return (
    <Stack direction="row" alignItems="center" gap={1} flexWrap="wrap">
      <RecordedReplayChip />
      {outcome && (
        <Typography sx={{ typography: "s1", color: "text.secondary" }}>
          {COPY.original(outcome)}
        </Typography>
      )}
      {envId && testId && original.test_execution_id && (
        <Link
          component="button"
          type="button"
          onClick={open}
          sx={{ typography: "s1" }}
        >
          {COPY.openOriginal}
        </Link>
      )}
    </Stack>
  );
}
RecordedReplayOrigin.propTypes = {
  envId: PropTypes.string,
  testId: PropTypes.string,
  original: originalShape.isRequired,
};

// On a replay that stopped: where the agent took another path, and a live run
// of the same agent version to see what it does with the world as it is now.
export function ReplayStopped({ envId, testId, recordedReplay }) {
  const navigate = useNavigate();
  // One key for this trial view: a double click starts one live run.
  const [requestKey] = useState(() => crypto.randomUUID());
  const stopped = recordedReplay?.stopped;
  const liveRun = recordedReplay?.live_run;
  const run = useMutation({
    mutationFn: () =>
      runHarnessEnvironment(
        envId,
        [liveRun.scenario_id],
        1,
        requestKey,
        liveRun.agent_version_id ? { agent: liveRun.agent_version_id } : {},
      ),
    onSuccess: (created) => {
      enqueueSnackbar(COPY.runLiveStarted, { variant: "success" });
      navigate(
        paths.dashboard.simulate.environments.execution(
          envId,
          created.run_test_id ?? testId,
          created.test_execution_id,
        ),
      );
    },
    onError: (error) =>
      enqueueSnackbar(error?.message || error?.detail || COPY.runLiveFailed, {
        variant: "error",
      }),
  });
  if (!stopped) return null;
  return (
    <Stack direction="row" alignItems="center" gap={1} flexWrap="wrap">
      {stopped.diverged && (
        <Typography sx={{ typography: "s1" }}>
          {COPY.diverged(
            stopped.call,
            stopped.tool,
            recordedReplay.agent_changed,
          )}
        </Typography>
      )}
      {envId && liveRun && (
        <Tooltip arrow title={COPY.runLiveAbout}>
          <span>
            <Button
              size="small"
              variant="outlined"
              color="inherit"
              disabled={run.isPending}
              onClick={() => run.mutate()}
            >
              {COPY.runLive}
            </Button>
          </span>
        </Tooltip>
      )}
    </Stack>
  );
}
ReplayStopped.propTypes = {
  envId: PropTypes.string,
  testId: PropTypes.string,
  recordedReplay: PropTypes.shape({
    stopped: PropTypes.shape({
      call: PropTypes.number,
      tool: PropTypes.string,
      diverged: PropTypes.bool,
    }),
    live_run: PropTypes.shape({
      scenario_id: PropTypes.string,
      agent_version_id: PropTypes.string,
    }),
    agent_changed: PropTypes.bool,
  }),
};

// Replay a finished trial with the tool answers it recorded, with this agent
// or another version of it. Off, with the reason, when the trial can't be.
export function ReplayWithRecordedTools({
  envId,
  testId,
  callId,
  recordedReplay,
}) {
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [agentVersionId, setAgentVersionId] = useState(SAME_AGENT);
  // One key per opened dialog: a double click replays once.
  const [requestKey, setRequestKey] = useState(null);
  const versions = useEnvironmentVersions(envId, { enabled: open });
  const agents = (versions.data?.agent ?? []).filter(
    (one) => one.isDefault || one.pickable,
  );
  const replay = useMutation({
    mutationFn: () =>
      replayTrialWithRecordedTools(
        envId,
        callId,
        requestKey,
        agentVersionId || undefined,
      ),
    onSuccess: (run) => {
      setOpen(false);
      enqueueSnackbar(COPY.started, { variant: "success" });
      navigate(
        paths.dashboard.simulate.environments.execution(
          envId,
          run.run_test_id ?? testId,
          run.test_execution_id,
        ),
      );
    },
    onError: (error) =>
      enqueueSnackbar(error?.message || error?.detail || COPY.failed, {
        variant: "error",
      }),
  });
  const available = Boolean(recordedReplay?.available);
  const button = (
    <Button
      size="small"
      variant="outlined"
      color="inherit"
      disabled={!available}
      onClick={() => {
        setAgentVersionId(SAME_AGENT);
        setRequestKey(crypto.randomUUID());
        setOpen(true);
      }}
    >
      {COPY.action}
    </Button>
  );
  return (
    <>
      {available ? (
        button
      ) : (
        <Tooltip arrow title={recordedReplay?.unavailable_reason || ""}>
          <span>{button}</span>
        </Tooltip>
      )}
      <ModalWrapper
        open={open}
        onClose={() => setOpen(false)}
        title={COPY.title}
        actionBtnTitle={COPY.submit}
        onSubmit={() => replay.mutate()}
        isValid={!replay.isPending}
        isLoading={replay.isPending}
      >
        <Stack gap={2}>
          <Typography sx={{ typography: "s1", color: "text.secondary" }}>
            {COPY.explain}
          </Typography>
          <TextField
            select
            size="small"
            label={COPY.agentLabel}
            value={agentVersionId}
            onChange={(event) => setAgentVersionId(event.target.value)}
          >
            <MenuItem value={SAME_AGENT}>{COPY.sameAgent}</MenuItem>
            {agents.map((one) => (
              <MenuItem key={one.id} value={one.id}>
                {one.label}
              </MenuItem>
            ))}
          </TextField>
        </Stack>
      </ModalWrapper>
    </>
  );
}
ReplayWithRecordedTools.propTypes = {
  envId: PropTypes.string.isRequired,
  testId: PropTypes.string,
  callId: PropTypes.string.isRequired,
  recordedReplay: PropTypes.shape({
    available: PropTypes.bool,
    unavailable_reason: PropTypes.string,
    original: originalShape,
  }),
};
