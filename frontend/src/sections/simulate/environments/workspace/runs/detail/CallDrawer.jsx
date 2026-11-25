import PropTypes from "prop-types";
import { useEffect, useState } from "react";
import { Drawer } from "@mui/material";
import { coveredDrawerSx, useDrawerCovered } from "src/hooks/use-drawer-stack";
import {
  useHasWorkers,
  useTrialRoutes,
} from "src/api/simulate-environments/runDetail";

import ChatCallDrawer from "./ChatCallDrawer";
import VoiceCallDrawer from "./VoiceCallDrawer";
import { isVoiceCall } from "./callRouting";
import useCallTraceOpen from "./useCallTraceOpen";
import useTrialRoutesView, { resetTrialRoutesView } from "./participants/store";

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
  onOpenTrial,
}) {
  const open = !!task;
  const voice = isVoiceCall(task, agentType);
  // A multi-agent call opens wider so its participants sequence has room; the
  // width holds while the next call's record loads, so the drawer doesn't jump.
  const hasWorkers = useHasWorkers(task?.id, task?.status);
  const [wide, setWide] = useState(false);
  if (hasWorkers !== undefined && hasWorkers !== wide) setWide(hasWorkers);
  // A multi-agent call's trials come from its trial routes, the same list the trials table shows, so a trial off
  // the table's loaded page is listed and opened too; other calls list the trials the table has loaded.
  const showTools = useTrialRoutesView((view) => view.showTools);
  const { data: routes } = useTrialRoutes(
    wide ? executionId : null,
    task?.sourceScenarioKey ?? null,
    showTools,
  );
  const pickerTrials = routes
    ? routes.trials.map((one) => ({
        id: one.call_execution_id,
        trialIndex: one.number,
        status: one.result,
      }))
    : trials;
  const pickTrial = routes ? (id) => onOpenTrial?.(id, null) : onPickTrial;
  const { traceOpen, onTracePanelMountedChange } = useCallTraceOpen();
  const covered = useDrawerCovered(open);
  // The trials table's open/tool-call view lasts while moving between trials, not past closing the drawer.
  useEffect(() => {
    if (!open) resetTrialRoutesView();
  }, [open]);

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
            trials={pickerTrials}
            onPickTrial={pickTrial}
            onOpenTrial={onOpenTrial}
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
            trials={pickerTrials}
            onPickTrial={pickTrial}
            onOpenTrial={onOpenTrial}
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
    sourceScenarioKey: PropTypes.string,
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
  // (callExecutionId, stepKey | null) → another trial of the scenario, from the trials table.
  onOpenTrial: PropTypes.func,
};
