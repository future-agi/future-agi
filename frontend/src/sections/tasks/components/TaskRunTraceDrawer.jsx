import React from "react";
import PropTypes from "prop-types";
import { Drawer } from "@mui/material";
import { useGetProjectDetails } from "src/api/project/project-detail";
import { useVoiceCallDetail } from "src/sections/agents/helper";
import { PROJECT_SOURCE } from "src/utils/constants";
import TraceDetailDrawerV2 from "src/components/traceDetail/TraceDetailDrawerV2";
import VoiceDetailDrawerV2 from "src/components/VoiceDetailDrawerV2/VoiceDetailDrawerV2";

// Opens a task run's source trace over the Usage tab in the drawer Observe
// uses for that project: the voice call drawer for voice projects, the trace
// drawer otherwise. Mirrors Error Feed's Traces tab.
const TaskRunTraceDrawer = ({
  open,
  onClose,
  projectId,
  traceId,
  spanId,
  onPrev,
  onNext,
  hasPrev,
  hasNext,
}) => {
  const { data: projectDetail, isLoading: projectLoading } =
    useGetProjectDetails(projectId, !!projectId);
  const isVoiceProject = projectDetail?.source === PROJECT_SOURCE.SIMULATOR;
  const { data: voiceCallData, isFetching: voiceLoading } = useVoiceCallDetail(
    traceId,
    { enabled: open && isVoiceProject, projectId },
  );

  // Wait for the project's kind so a voice call never flashes the trace
  // drawer first.
  if (projectLoading) return null;

  if (isVoiceProject) {
    return (
      // Same overlay shape as TraceDetailDrawerV2: persistent (no backdrop,
      // page stays interactive), fixed and right-anchored.
      <Drawer
        anchor="right"
        variant="persistent"
        open={open}
        onClose={onClose}
        PaperProps={{
          sx: {
            width: "auto",
            height: "100vh",
            position: "fixed",
            right: 0,
            borderRadius: 0,
            bgcolor: "background.paper",
            display: "flex",
            flexDirection: "column",
            borderLeft: "1px solid",
            borderColor: "divider",
            transition: "none",
          },
        }}
        ModalProps={{
          BackdropProps: { style: { backgroundColor: "transparent" } },
        }}
      >
        {open && traceId && (
          <VoiceDetailDrawerV2
            data={
              voiceCallData
                ? { ...voiceCallData, project_id: projectId, module: "project" }
                : { trace_id: traceId, project_id: projectId }
            }
            onClose={onClose}
            onPrev={onPrev}
            onNext={onNext}
            hasPrev={hasPrev}
            hasNext={hasNext}
            isLoading={voiceLoading}
          />
        )}
      </Drawer>
    );
  }

  return (
    <TraceDetailDrawerV2
      traceId={traceId}
      open={open}
      onClose={onClose}
      projectId={projectId}
      initialSpanId={spanId}
      initialEvalFocus
      onPrev={onPrev}
      onNext={onNext}
      hasPrev={hasPrev}
      hasNext={hasNext}
    />
  );
};

TaskRunTraceDrawer.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  projectId: PropTypes.string,
  traceId: PropTypes.string,
  spanId: PropTypes.string,
  onPrev: PropTypes.func,
  onNext: PropTypes.func,
  hasPrev: PropTypes.bool,
  hasNext: PropTypes.bool,
};

export default TaskRunTraceDrawer;
