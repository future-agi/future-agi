import PropTypes from "prop-types";
import { useCallExecutionV3Detail } from "src/api/simulate-environments/runDetail";
import CallDrawer from "../../runs/detail/CallDrawer";

// A scenario's latest conversation, in the run's own call drawer, routed voice
// or chat by the call's `simulation_call_type`. It opens once that is known (or
// the fetch failed, and the call drawer shows its own error); the query is
// shared with the drawer it opens, so the call is fetched once. The scenario
// drawer loads this file on the first Open conversation.
export default function ConversationDrawer({ callExecutionId, open, onClose }) {
  const { data, isError } = useCallExecutionV3Detail(callExecutionId, true);
  const known = !!data || isError;
  return (
    <CallDrawer
      task={
        open && known
          ? {
              id: callExecutionId,
              simulationCallType: data?.simulation_call_type ?? null,
            }
          : null
      }
      executionId={null}
      onClose={onClose}
    />
  );
}

ConversationDrawer.propTypes = {
  callExecutionId: PropTypes.string.isRequired,
  open: PropTypes.bool,
  onClose: PropTypes.func,
};
