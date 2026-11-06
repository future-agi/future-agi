import PropTypes from "prop-types";
import { Box } from "@mui/material";
import CustomTooltip from "src/components/tooltip";
import StatusBadge from "src/components/status-badge/StatusBadge";
import { BUILD_STATUS, ENV_STATUS, STATUS_META } from "../myEnvironments.constants";
import { WORKSPACE_COPY } from "./workspace.constants";

// The status chip beside an environment's name, drawn by the shared
// StatusBadge. Keyed on the env's build status: "Ready" once the environment is
// built and runnable, a live "Building" while it is still being derived (you
// cannot correct a world that isn't done being built), and a static "Failed"
// once a build stage is terminal-failed — a failed build must not read as still
// building.
//
// Roles come from STATUS_META, the same source the table's StatusPill uses, so
// the chip inside the workspace matches the one outside it.
export default function LivePill({ env, building }) {
  const isCancelling = env?.status === ENV_STATUS.CANCELLING;
  const isCancelled = env?.status === ENV_STATUS.CANCELLED;
  const isFinalizing = env?.status === ENV_STATUS.FINALIZING;
  const isBuilding = building ?? env?.buildStatus === BUILD_STATUS.BUILDING;
  const isFailed = !isBuilding && env?.buildStatus === BUILD_STATUS.FAILED;
  const isRunning = !isBuilding && !isFailed && env?.status === ENV_STATUS.RUNNING;
  const key = isCancelled
    ? ENV_STATUS.CANCELLED
    : isCancelling
      ? ENV_STATUS.CANCELLING
      : isFailed
        ? ENV_STATUS.FAILED
        : isFinalizing
          ? ENV_STATUS.FINALIZING
          : isBuilding
            ? ENV_STATUS.BUILDING
            : isRunning
              ? ENV_STATUS.RUNNING
              : ENV_STATUS.READY;
  const label = isCancelled
    ? WORKSPACE_COPY.cancelledLabel
    : isCancelling
      ? WORKSPACE_COPY.cancellingLabel
      : isFailed
        ? WORKSPACE_COPY.failedLabel
        : isFinalizing
          ? WORKSPACE_COPY.finalizingLabel
          : isBuilding
            ? WORKSPACE_COPY.buildingLabel
            : isRunning
              ? STATUS_META.running.label
              : WORKSPACE_COPY.live;
  const tooltip = isCancelled
    ? WORKSPACE_COPY.cancelledTooltip
    : isCancelling
      ? WORKSPACE_COPY.cancellingTooltip
      : isFailed
        ? WORKSPACE_COPY.failedTooltip
        : isFinalizing
          ? WORKSPACE_COPY.finalizingTooltip
          : isBuilding
            ? WORKSPACE_COPY.buildingTooltip
            : isRunning
              ? WORKSPACE_COPY.runningTooltip
              : WORKSPACE_COPY.liveTooltip;
  // The in-flight states pulse; the rest sit still.
  const pulsing = isBuilding || isFinalizing || isCancelling;

  return (
    <CustomTooltip show title={tooltip} size="small" arrow>
      <Box component="span" sx={{ display: "inline-flex", cursor: "default" }}>
        <StatusBadge
          status={pulsing ? "building" : key}
          role={STATUS_META[key].role}
          label={label}
          variant="badge"
        />
      </Box>
    </CustomTooltip>
  );
}

LivePill.propTypes = {
  env: PropTypes.shape({ buildStatus: PropTypes.string, status: PropTypes.string }),
  building: PropTypes.bool,
};
