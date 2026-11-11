import PropTypes from "prop-types";

import StatusChip from "../runs/StatusChip";

import { STATE_LABEL } from "./fixRunView";

// One colour per ending that earns one, as the design's status pill does: in
// progress, ready to review, or broken. Endings that just stopped stay neutral.
const STATE_TONE = {
  queued: "queued",
  running: "running",
  stopping: "cancelling",
  ready: "passed",
  failed: "failed",
};

export default function FixStatusChip({ state }) {
  return (
    <StatusChip
      status={STATE_TONE[state] || "cancelled"}
      label={STATE_LABEL[state] || state}
    />
  );
}

FixStatusChip.propTypes = { state: PropTypes.string.isRequired };
