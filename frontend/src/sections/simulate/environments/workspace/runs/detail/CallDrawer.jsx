import PropTypes from "prop-types";
import { Drawer } from "@mui/material";

import ChatCallDrawer from "./ChatCallDrawer";
import VoiceCallDrawer from "./VoiceCallDrawer";
import { isVoiceCall } from "./callRouting";

/**
 * The per-call drawer. Opens on a trace row-click and routes to the matching
 * detail view over REAL call data:
 *   - voice → the product's `VoiceDetailDrawerV2` (real audio + transcript),
 *   - chat / other → the ported lean designer drawer (`ChatCallDrawer`).
 * The outer drawer is the only shell; each branch owns its own header/chrome.
 */
export default function CallDrawer({
  task,
  agentType,
  onClose,
  onPrev,
  onNext,
  hasPrev = false,
  hasNext = false,
}) {
  const open = !!task;
  const voice = isVoiceCall(task, agentType);

  return (
    <Drawer
      anchor="right"
      open={open}
      onClose={onClose}
      PaperProps={{
        sx: {
          // Voice lets `VoiceDetailDrawerV2` drive its own (draggable) width; the
          // two-pane chat drawer wants a wide fixed pane.
          width: voice ? "auto" : { xs: "100%", md: "64vw" },
          maxWidth: "100vw",
          height: "100vh",
          backgroundColor: "background.default",
        },
      }}
      // The page behind stays readable — a call drawer acts on the run you were
      // just reading, matching the env SideDrawer / product TestDetailSideDrawer.
      ModalProps={{ BackdropProps: { style: { backgroundColor: "transparent" } } }}
    >
      {task &&
        (voice ? (
          <VoiceCallDrawer
            task={task}
            onClose={onClose}
            onPrev={onPrev}
            onNext={onNext}
            hasPrev={hasPrev}
            hasNext={hasNext}
          />
        ) : (
          <ChatCallDrawer
            task={task}
            onClose={onClose}
            onPrev={onPrev}
            onNext={onNext}
            hasPrev={hasPrev}
            hasNext={hasNext}
          />
        ))}
    </Drawer>
  );
}
CallDrawer.propTypes = {
  task: PropTypes.shape({
    id: PropTypes.string,
    simulationCallType: PropTypes.string,
  }),
  agentType: PropTypes.string,
  onClose: PropTypes.func,
  onPrev: PropTypes.func,
  onNext: PropTypes.func,
  hasPrev: PropTypes.bool,
  hasNext: PropTypes.bool,
};
