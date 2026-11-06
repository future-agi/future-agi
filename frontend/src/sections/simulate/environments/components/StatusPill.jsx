import PropTypes from "prop-types";
import { Box } from "@mui/material";
import CustomTooltip from "src/components/tooltip";
import StatusBadge from "src/components/status-badge/StatusBadge";
import { STATUS_META, ENV_STATUS } from "../myEnvironments.constants";

// Statuses this area uses that the shared status map doesn't name yet.
const ROLE_FALLBACK = {
  ready: "success",
  finalizing: "info",
  cancelling: "neutral",
};

// Design system: renders through the shared StatusBadge and the one status map,
// so an environment's status looks like every other status in the product.
export default function StatusPill({ status, progress }) {
  const meta = STATUS_META[status] || STATUS_META.not_run;
  const detail =
    status === ENV_STATUS.BUILDING && progress
      ? `${progress.done}/${progress.total} steps`
      : "";
  return (
    <CustomTooltip show={!!detail} arrow size="small" title={detail}>
      <Box component="span" sx={{ display: "inline-flex" }}>
        <StatusBadge
          status={status}
          label={meta.label}
          role={ROLE_FALLBACK[status]}
        />
      </Box>
    </CustomTooltip>
  );
}

StatusPill.propTypes = {
  status: PropTypes.string,
  progress: PropTypes.shape({
    done: PropTypes.number,
    total: PropTypes.number,
  }),
};
