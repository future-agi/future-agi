import PropTypes from "prop-types";
import { Drawer } from "@mui/material";
import { coveredDrawerSx, useDrawerCovered } from "src/hooks/use-drawer-stack";
import { useHasWorkers } from "src/api/simulate-environments/runDetail";

import ChatCallDrawer from "./ChatCallDrawer";
import VoiceCallDrawer from "./VoiceCallDrawer";
import { isVoiceCall } from "./callRouting";
import useCallTraceOpen from "./useCallTraceOpen";

/**
 * The per-call drawer. Opens on a trace row-click and routes to the matching
 * detail view over REAL call data:
 *   - voice → the product's `VoiceDetailDrawerV2` (real audio + transcript),
 *   - chat / other → the ported lean designer drawer (`ChatCallDrawer`).
 * The outer drawer is the only shell; each branch owns its own header/chrome.
 */
export default function CallDrawer({
  task,
  executionId = null,
  agentType,
  onClose,
  onPrev,
  onNext,
  hasPrev = false,
  hasNext = false,
  trials = null,
  onPickTrial,
}) {
  const open = !!task;
  const voice = isVoiceCall(task, agentType);
  // A multi-agent call opens wider so its participants sequence has room.
  const wide = useHasWorkers(task?.id, task?.status) === true;
  const { traceOpen, onTracePanelMountedChange } = useCallTraceOpen();
  const covered = useDrawerCovered(open);

  return (
    <Drawer
      anchor="right"
      open={open}
      onClose={onClose}
      sx={coveredDrawerSx(covered)}
      PaperProps={{
        sx: {
          // Voice lets `VoiceDetailDrawerV2` drive its own (draggable) width; the
          // two-pane chat drawer wants a wide fixed pane.
          width: voice ? "auto" : { xs: "100%", md: wide ? "82vw" : "64vw" },
          maxWidth: "100vw",
          height: "100vh",
          backgroundColor: "background.default",
        },
      }}
      // The page behind stays readable — a call drawer acts on the run you were
      // just reading, matching the env SideDrawer / product TestDetailSideDrawer.
      // The modal takes Escape and stops it, so an open trace never hears it and
      // the call closes under it; with a trace open the key is left to the trace.
      ModalProps={{
        disableEscapeKeyDown: traceOpen,
        BackdropProps: { style: { backgroundColor: "transparent" } },
      }}
    >
      {task &&
        (voice ? (
          <VoiceCallDrawer
            task={task}
            executionId={executionId}
            onClose={onClose}
            onPrev={onPrev}
            onNext={onNext}
            hasPrev={hasPrev}
            hasNext={hasNext}
            traceOpen={traceOpen}
            onTracePanelMountedChange={onTracePanelMountedChange}
            trials={trials}
            onPickTrial={onPickTrial}
            wide={wide}
          />
        ) : (
          <ChatCallDrawer
            task={task}
            executionId={executionId}
            onClose={onClose}
            onPrev={onPrev}
            onNext={onNext}
            hasPrev={hasPrev}
            hasNext={hasNext}
            traceOpen={traceOpen}
            onTracePanelMountedChange={onTracePanelMountedChange}
            trials={trials}
            onPickTrial={onPickTrial}
            wide={wide}
          />
        ))}
    </Drawer>
  );
}
CallDrawer.propTypes = {
  task: PropTypes.shape({
    id: PropTypes.string,
    simulationCallType: PropTypes.string,
    status: PropTypes.string,
  }),
  executionId: PropTypes.string,
  agentType: PropTypes.string,
  onClose: PropTypes.func,
  onPrev: PropTypes.func,
  onNext: PropTypes.func,
  hasPrev: PropTypes.bool,
  hasNext: PropTypes.bool,
  // Trials of this call's scenario, for the picker in the drawer header.
  trials: PropTypes.arrayOf(PropTypes.object),
  onPickTrial: PropTypes.func,
};
