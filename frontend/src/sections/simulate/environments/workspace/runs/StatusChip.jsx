import PropTypes from "prop-types";
import { Box, Tooltip } from "@mui/material";
import StatusBadge from "src/components/status-badge/StatusBadge";
import {
  STATUS_META,
  LIVE_STATUSES,
  PULSING_DOT_CLASS,
} from "./runs.constants";
import { STOP_CHIP_STATUSES } from "./runStop";

// Statuses this area uses that the shared status map doesn't name yet.
const ROLE_FALLBACK = {
  inconclusive: "neutral",
  finished: "neutral",
  cancelling: "neutral",
};

// Design system: every run status renders through the shared StatusBadge and
// the one status map (src/utils/runStatus.js), so it matches every other screen.
export default function StatusChip({ status, label, tip }) {
  const meta = STATUS_META[status] || STATUS_META.queued;
  const key = status === "finished" ? "completed" : status;
  const badge = (
    <StatusBadge
      status={key}
      label={label ?? meta.label}
      role={
        ROLE_FALLBACK[status] ??
        (STOP_CHIP_STATUSES.has(status) ? meta.role : undefined)
      }
      className={LIVE_STATUSES.includes(status) ? PULSING_DOT_CLASS : undefined}
    />
  );
  return tip ? (
    <Tooltip title={tip} arrow>
      <Box component="span" sx={{ display: "inline-flex" }}>
        {badge}
      </Box>
    </Tooltip>
  ) : (
    badge
  );
}

StatusChip.propTypes = {
  status: PropTypes.string,
  // Overrides the status's own label, for a caller whose vocabulary differs.
  label: PropTypes.string,
  tip: PropTypes.string,
};
