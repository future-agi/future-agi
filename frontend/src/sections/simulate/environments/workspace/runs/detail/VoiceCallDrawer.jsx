import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import {
  Box,
  CircularProgress,
  IconButton,
  Stack,
  Typography,
} from "@mui/material";

import Iconify from "src/components/iconify";
import {
  callTranscript,
  useCallExecutionV3Detail,
  useCallTraces,
  useHasWorkers,
  withScoringStatus,
} from "src/api/simulate-environments/runDetail";
import VoiceDetailDrawerV2 from "src/components/VoiceDetailDrawerV2";
import CallTracesPanel from "./CallTracesPanel";
import CallWorkersPanel from "./CallWorkersPanel";
import { OutcomeReason } from "./chatDrawerCells";
import {
  CALL_TRACES_COPY,
  CALL_WORKERS_COPY,
  ownsArrowKeys,
} from "./callTraces.constants";
import TrialChangesPanel from "./TrialChangesPanel";
import { TRIAL_CHANGES_COPY } from "./trialChanges.constants";

// The annotate / queue / dataset / tags actions need project + annotation
// context this embedded drawer doesn't wire; hide them rather than leave dead
// menu items (Download still works from the payload alone).
const HIDDEN_VOICE_ACTIONS = ["annotate", "queue", "dataset", "tags"];

// A minimal centred pane for the pre-hydrate / error states. Carries its own
// close (the outer drawer's only other escape is a backdrop click).
function StatePane({ children, onClose }) {
  return (
    <Stack
      sx={{
        width: "60vw",
        maxWidth: "100%",
        height: "100%",
        position: "relative",
      }}
      alignItems="center"
      justifyContent="center"
      spacing={1.5}
    >
      <IconButton
        aria-label="Close"
        size="small"
        onClick={onClose}
        sx={{ position: "absolute", top: 8, right: 8, color: "text.subtitle" }}
      >
        <Iconify icon="solar:close-linear" width={18} />
      </IconButton>
      {children}
    </Stack>
  );
}
StatePane.propTypes = { children: PropTypes.node, onClose: PropTypes.func };

// The voice branch reuses the REAL product voice drawer unchanged. It reads the
// same v3 `/simulate/v3/call-executions/{id}/` detail the chat branch does, so
// audio/transcript/evals and normalized function calls share one cache, then
// hands the payload straight to
// `VoiceDetailDrawerV2` — a content-only component that renders its own header,
// recording player and analytics. We only tag `module`/`origin` = "simulate"
// (the fields the panels branch on) and hide the annotation actions.
export default function VoiceCallDrawer({
  task,
  executionId = null,
  onClose,
  onPrev,
  onNext,
  hasPrev = false,
  hasNext = false,
  traceOpen = false,
  onTracePanelMountedChange,
}) {
  const { data, isPending, isError } = useCallExecutionV3Detail(
    task.id,
    !!task.id,
  );

  const voiceData = useMemo(
    () =>
      data
        ? {
            ...data,
            // The shared drawer reads stored `eval_metrics`; give each unscored
            // eval its status so it shows pending, failed or timed out.
            eval_metrics: withScoringStatus(
              data.eval_metrics,
              data.evaluations,
            ),
            transcript: callTranscript(data),
            module: "simulate",
            origin: "simulate",
          }
        : null,
    [data],
  );

  const callTraces = useCallTraces(executionId, task.id);
  const hasWorkers = useHasWorkers(task.id, task.completedAt);
  // Held here because the panel unmounts while the next call's detail loads.
  const [chosenTab, setChosenTab] = useState("transcript");
  const [openStep, setOpenStep] = useState(null);
  // Forgotten on another call, so stepping to it and back does not reopen the trace.
  const [traceCallId, setTraceCallId] = useState(task.id);
  if (traceCallId !== task.id) {
    setTraceCallId(task.id);
    setOpenStep(null);
  }
  // Stays on Participants while the next call's record is unread; a call known to have none has no tab.
  const showWorkers =
    hasWorkers || (hasWorkers === undefined && chosenTab === "workers");
  const leftPanelTabs = useMemo(
    () => [
      {
        label: CALL_TRACES_COPY.tab,
        value: "traces",
        icon: "solar:chart-square-linear",
        content: (
          <CallTracesPanel
            traces={callTraces.traces}
            projectId={callTraces.projectId}
            testExecutionId={executionId}
            isError={callTraces.isError}
            onMountedChange={onTracePanelMountedChange}
            openTraceId={openStep?.traceId ?? null}
            openSpanId={openStep?.spanId ?? null}
          />
        ),
      },
      // Only a call whose world had actors or faults records what changed in it.
      ...(data?.trial_state
        ? [
            {
              label: TRIAL_CHANGES_COPY.tab,
              value: "world_changes",
              icon: "solar:global-linear",
              content: <TrialChangesPanel trialState={data.trial_state} />,
            },
          ]
        : []),
      ...(showWorkers
        ? [
            {
              label: CALL_WORKERS_COPY.tab,
              value: "workers",
              icon: "solar:users-group-two-rounded-linear",
              keepMounted: true,
              content: (
                <Box {...ownsArrowKeys} sx={{ outline: "none" }}>
                  <CallWorkersPanel
                    key={task.id}
                    callExecutionId={task.id}
                    traceIds={callTraces.traces.map((trace) => trace.traceId)}
                    onOpenTrace={(traceId, spanId) => {
                      setOpenStep({ traceId, spanId });
                      setChosenTab("traces");
                    }}
                  />
                </Box>
              ),
            },
          ]
        : []),
    ],
    [
      data?.trial_state,
      task.id,
      showWorkers,
      callTraces.projectId,
      callTraces.isError,
      callTraces.traces,
      executionId,
      onTracePanelMountedChange,
      openStep,
    ],
  );

  if (isError) {
    return (
      <StatePane onClose={onClose}>
        <Iconify
          icon="solar:danger-triangle-linear"
          width={22}
          sx={{ color: "text.subtitle" }}
        />
        <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
          Couldn&rsquo;t load this call.
        </Typography>
      </StatePane>
    );
  }

  if (!voiceData) {
    return (
      <StatePane onClose={onClose}>
        <CircularProgress size={22} />
      </StatePane>
    );
  }

  return (
    <Box sx={{ height: "100%", display: "flex", flexDirection: "column" }}>
      {task.outcomeReason ? (
        <Box sx={{ pt: 1.5 }}>
          <OutcomeReason reason={task.outcomeReason} />
        </Box>
      ) : null}
      <Box sx={{ flex: 1, minHeight: 0, display: "flex" }}>
        <VoiceDetailDrawerV2
          data={voiceData}
          onClose={onClose}
          scenarioId={data?.scenario_id}
          isLoading={isPending}
          hiddenActionIds={HIDDEN_VOICE_ACTIONS}
          // Checklist and Graph have no data behind them on an environment run yet.
          hidePathTabs
          // Fix with Falcon isn't wired into the environment flow yet.
          showFixWithFalcon={false}
          // Annotation isn't either: no Annotations tab, so no "Add Label" to a
          // dead end.
          hideAnnotationTab
          leftPanelTabs={leftPanelTabs}
          leftPanelTab={chosenTab}
          onLeftPanelTabChange={(value) => {
            setOpenStep(null);
            setChosenTab(value);
          }}
          // The product header defaults both arrows to enabled, so always pass
          // real values.
          onPrev={onPrev}
          onNext={onNext}
          hasPrev={hasPrev && !traceOpen}
          hasNext={hasNext && !traceOpen}
        />
      </Box>
    </Box>
  );
}
VoiceCallDrawer.propTypes = {
  task: PropTypes.shape({
    id: PropTypes.string,
    completedAt: PropTypes.string,
    outcomeReason: PropTypes.string,
  }).isRequired,
  executionId: PropTypes.string,
  onClose: PropTypes.func,
  onPrev: PropTypes.func,
  onNext: PropTypes.func,
  hasPrev: PropTypes.bool,
  hasNext: PropTypes.bool,
  traceOpen: PropTypes.bool,
  onTracePanelMountedChange: PropTypes.func,
};
