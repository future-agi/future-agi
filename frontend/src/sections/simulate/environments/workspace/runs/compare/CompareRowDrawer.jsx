import PropTypes from "prop-types";
import { useState } from "react";
import {
  Box,
  Button,
  CircularProgress,
  Stack,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";

import StatusBadge from "src/components/status-badge/StatusBadge";
import { useCallDetail } from "src/api/simulate-environments/runDetail";
import SideDrawer from "../../../components/SideDrawer";
import ChatTranscriptPane from "../detail/ChatTranscriptPane";
import { runOutcome } from "../detail/trace/traceTable.constants";
import { COMPARE_COPY } from "../../workspace.constants";
import { RunDot } from "./compareCells";

function CallTranscript({ callId }) {
  const { callDetail, isLoading } = useCallDetail(callId);
  if (isLoading)
    return (
      <Stack alignItems="center" sx={{ py: 4 }}>
        <CircularProgress size={18} />
      </Stack>
    );
  return <ChatTranscriptPane turns={callDetail?.turns ?? []} />;
}
CallTranscript.propTypes = { callId: PropTypes.string.isRequired };

function CallColumn({ run, task, onOpenCall }) {
  const outcome = task ? runOutcome(task.status) : null;
  return (
    <Stack
      sx={{
        flex: "1 0 360px",
        minWidth: 360,
        minHeight: 0,
        borderRight: "1px solid",
        borderColor: "divider",
      }}
    >
      <Stack
        direction="row"
        alignItems="center"
        spacing={1}
        sx={{
          px: 2,
          py: 1.25,
          borderBottom: "1px solid",
          borderColor: "divider",
        }}
      >
        <RunDot ordinal={run.ordinal} colorIndex={run.colorIndex} />
        <Typography
          noWrap
          sx={{ typography: "s2", fontWeight: "fontWeightMedium", minWidth: 0 }}
        >
          {run.ordinal != null && `${COMPARE_COPY.run(run.ordinal)} · `}
          {COMPARE_COPY.runLabel(run.agentLabel, run.environmentLabel)}
          {task?.trialIndex ? ` · ${COMPARE_COPY.trial(task.trialIndex)}` : ""}
        </Typography>
        {outcome && (
          <StatusBadge
            status={task.status}
            label={outcome.label}
            role={outcome.role}
            variant="dot"
          />
        )}
        {task && (
          <Button
            size="small"
            onClick={() => onOpenCall(task, run)}
            sx={{ ml: "auto", flexShrink: 0 }}
          >
            {COMPARE_COPY.openCall}
          </Button>
        )}
      </Stack>
      <Box
        sx={{
          flex: 1,
          minHeight: 0,
          overflow: "auto",
          display: "flex",
          flexDirection: "column",
        }}
      >
        {task ? (
          <CallTranscript callId={task.id} />
        ) : (
          <Typography sx={{ typography: "s2", color: "text.disabled", p: 2 }}>
            {COMPARE_COPY.notRun}
          </Typography>
        )}
      </Box>
    </Stack>
  );
}
CallColumn.propTypes = {
  run: PropTypes.object.isRequired,
  task: PropTypes.object,
  onOpenCall: PropTypes.func.isRequired,
};

// A scenario row's calls across the compared runs, side by side: one column
// per run, for one trial at a time, each with its transcript. Open call hands
// one call to the run detail's call drawer.
export default function CompareRowDrawer({ row, runs, onClose, onOpenCall }) {
  // The trial picked, kept with its row so another row opens on its first.
  const [picked, setPicked] = useState({ rowId: null, trial: null });
  // Every numbered trial a run played, the baseline's first. A call with no
  // trial number shows when its run has no call for the picked trial.
  const trials = row
    ? [
        ...new Set(
          runs.flatMap((run) =>
            (row.calls[run.id] ?? [])
              .map((task) => task.trialIndex)
              .filter((one) => one != null),
          ),
        ),
      ]
    : [];
  const trial =
    picked.rowId === row?.rowId && trials.includes(picked.trial)
      ? picked.trial
      : trials[0];
  // A run without that trial shows its first call, as Show diff pairs them.
  const columns = runs.map((run) => {
    const calls = row?.calls[run.id] ?? [];
    return {
      run,
      task: calls.find((task) => task.trialIndex === trial) ?? calls[0] ?? null,
    };
  });
  return (
    <SideDrawer open={!!row} onClose={onClose} width="92vw">
      {row && (
        <Stack sx={{ height: "100%", minHeight: 0 }}>
          <Box
            sx={{
              px: 2.5,
              py: 1.5,
              pr: 6,
              borderBottom: "1px solid",
              borderColor: "divider",
            }}
          >
            <Typography sx={{ typography: "m3" }}>
              {columns.find(({ task }) => task)?.task.sourceScenario ||
                row.scenario ||
                row.scenarioKey}
            </Typography>
            <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
              {[COMPARE_COPY.drawerTitle, row.useCase]
                .filter(Boolean)
                .join(" · ")}
            </Typography>
            {trials.length > 1 && (
              <ToggleButtonGroup
                exclusive
                size="small"
                value={trial}
                onChange={(_, value) =>
                  value != null && setPicked({ rowId: row.rowId, trial: value })
                }
                aria-label={COMPARE_COPY.trialPicker}
                sx={{ mt: 1 }}
              >
                {trials.map((one) => (
                  <ToggleButton key={one ?? "none"} value={one}>
                    {COMPARE_COPY.trial(one)}
                  </ToggleButton>
                ))}
              </ToggleButtonGroup>
            )}
          </Box>
          <Stack
            direction="row"
            sx={{ flex: 1, minHeight: 0, overflowX: "auto" }}
          >
            {columns.map(({ run, task }) => (
              <CallColumn
                key={run.id}
                run={run}
                task={task}
                onOpenCall={onOpenCall}
              />
            ))}
          </Stack>
        </Stack>
      )}
    </SideDrawer>
  );
}
CompareRowDrawer.propTypes = {
  row: PropTypes.object,
  runs: PropTypes.array.isRequired,
  onClose: PropTypes.func.isRequired,
  onOpenCall: PropTypes.func.isRequired,
};
