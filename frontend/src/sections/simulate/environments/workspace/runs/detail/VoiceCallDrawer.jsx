import PropTypes from "prop-types";
import { useMemo } from "react";
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
  mapCallDetail,
  useCallExecutionV3Detail,
  useCallTraces,
  withScoringStatus,
} from "src/api/simulate-environments/runDetail";
import VoiceDetailDrawerV2 from "src/components/VoiceDetailDrawerV2";
import CallTracesPanel from "./CallTracesPanel";
import { CALL_TRACES_COPY } from "./callTraces.constants";
import CallAttributes from "./call/CallAttributes";
import CallChecklist from "./call/CallChecklist";
import CallGraphPane from "./call/CallGraphPane";
import CallToolCalls from "./call/CallToolCalls";
import UnmeasuredBanner from "./call/UnmeasuredBanner";
import { isUnmeasured, toolCallsOf } from "./call/callDrawer.utils";

// The annotate / queue / dataset / tags actions need project + annotation
// context this embedded drawer doesn't wire; hide them rather than leave dead
// menu items (Download still works from the payload alone).
const HIDDEN_VOICE_ACTIONS = ["annotate", "queue", "dataset", "tags"];
const NONE = [];

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
        <Iconify icon="mingcute:close-line" width={18} />
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
  focusEvalId = null,
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
  // The same view-model the chat drawer reads, for Vel's Checklist and Tool
  // calls tabs.
  const callDetail = useMemo(() => mapCallDetail(data), [data]);
  const subGoalResults = callDetail?.subGoalResults?.length
    ? callDetail.subGoalResults
    : task.subGoalResults ?? NONE;
  const subGoalChecks = callDetail?.subGoalChecks ?? NONE;
  const leftPanelTabs = useMemo(
    () => [
      // Not "checklist"/"graph": those values are the product's own path tabs,
      // which read a scenario graph environment runs don't have.
      {
        label: "Checklist",
        value: "subGoals",
        icon: "mdi:format-list-checks",
        content: (
          <CallChecklist
            subGoalResults={subGoalResults}
            checks={subGoalChecks}
          />
        ),
      },
      {
        label: "Graph",
        value: "callGraph",
        icon: "mdi:graph-outline",
        content: <CallGraphPane />,
      },
      {
        label: CALL_TRACES_COPY.tab,
        value: "traces",
        icon: "mdi:chart-gantt",
        content: (
          <CallTracesPanel
            traces={callTraces.traces}
            projectId={callTraces.projectId}
            testExecutionId={executionId}
            isError={callTraces.isError}
            onMountedChange={onTracePanelMountedChange}
          />
        ),
      },
    ],
    [
      callTraces.projectId,
      callTraces.isError,
      callTraces.traces,
      executionId,
      onTracePanelMountedChange,
      subGoalResults,
      subGoalChecks,
    ],
  );

  const toolCalls = useMemo(
    () => toolCallsOf(callDetail?.turns ?? []),
    [callDetail],
  );
  const rightPanelTabs = useMemo(
    () => [
      {
        label: `Tool calls (${toolCalls.length})`,
        value: "toolCalls",
        icon: "mdi:wrench-outline",
        content: <CallToolCalls calls={toolCalls} />,
      },
      // The product's Attributes tab holds the provider's raw attributes, so
      // Vel's scenario attributes get their own name.
      {
        label: "Call attributes",
        value: "callAttributes",
        icon: "mdi:account-details-outline",
        content: (
          <CallAttributes
            scenario={task.scenario ?? data?.scenario}
            status={task.status}
            persona={task.personaDetails ?? data?.persona_details}
            personaName={task.persona ?? data?.persona}
            expected={task.idealOutcome ?? data?.ideal_outcome}
          />
        ),
      },
    ],
    [toolCalls, task, data],
  );
  const notice = isUnmeasured(task.status) ? (
    <UnmeasuredBanner
      status={task.status}
      harnessOutcomeStatus={task.harnessOutcomeStatus}
    />
  ) : null;
  const focusEvalName = focusEvalId
    ? data?.eval_metrics?.[focusEvalId]?.name ??
      data?.evaluations?.find((e) => e?.id === focusEvalId)?.name ??
      task.evalResults?.find((r) => r.id === focusEvalId)?.name ??
      null
    : null;

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
    <Box sx={{ height: "100%", display: "flex" }}>
      <VoiceDetailDrawerV2
        data={voiceData}
        onClose={onClose}
        scenarioId={data?.scenario_id}
        isLoading={isPending}
        hiddenActionIds={HIDDEN_VOICE_ACTIONS}
        // The product's own Checklist and Graph read a scenario graph that
        // environment runs don't have; Vel's come in through leftPanelTabs.
        hidePathTabs
        // Fix with Falcon isn't wired into the environment flow yet.
        showFixWithFalcon={false}
        // Annotation isn't either: no Annotations tab, so no "Add Label" to a
        // dead end.
        hideAnnotationTab
        leftPanelTabs={leftPanelTabs}
        rightPanelTabs={rightPanelTabs}
        rightPanelNotice={notice}
        focusEvalName={focusEvalName ?? undefined}
        // The product header defaults both arrows to enabled, so always pass
        // real values.
        onPrev={onPrev}
        onNext={onNext}
        hasPrev={hasPrev && !traceOpen}
        hasNext={hasNext && !traceOpen}
      />
    </Box>
  );
}
VoiceCallDrawer.propTypes = {
  task: PropTypes.shape({
    id: PropTypes.string,
    scenario: PropTypes.string,
    status: PropTypes.string,
    persona: PropTypes.string,
    personaDetails: PropTypes.object,
    idealOutcome: PropTypes.string,
    subGoalResults: PropTypes.array,
    harnessOutcomeStatus: PropTypes.string,
    evalResults: PropTypes.array,
  }).isRequired,
  executionId: PropTypes.string,
  focusEvalId: PropTypes.string,
  onClose: PropTypes.func,
  onPrev: PropTypes.func,
  onNext: PropTypes.func,
  hasPrev: PropTypes.bool,
  hasNext: PropTypes.bool,
  traceOpen: PropTypes.bool,
  onTracePanelMountedChange: PropTypes.func,
};
