import PropTypes from "prop-types";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography } from "@mui/material";
import CustomTooltip from "src/components/tooltip";
import { BUILD_STATUS, ENV_STATUS, STATUS_META } from "../myEnvironments.constants";
import { WORKSPACE_COPY } from "./workspace.constants";

// The status pill beside an environment's name. Three states keyed on the env's
// build status: green "Live" once the environment is adopted and answering, a
// pulsing "Building" while it is still being derived (you cannot correct a world
// that isn't done being built), and a static "Failed" once a build stage is
// terminal-failed — a failed build must not read as still building.
//
// Colours come from STATUS_META, the same source the table's StatusPill uses, so
// the pill inside the workspace matches the chip outside it (Building was amber
// here vs purple in the table before this).
export default function LivePill({ env, building }) {
  const isCancelling = env?.status === ENV_STATUS.CANCELLING;
  const isCancelled = env?.status === ENV_STATUS.CANCELLED;
  const isFinalizing = env?.status === ENV_STATUS.FINALIZING;
  const isBuilding = building ?? env?.buildStatus === BUILD_STATUS.BUILDING;
  const isFailed = !isBuilding && env?.buildStatus === BUILD_STATUS.FAILED;
  const isRunning = !isBuilding && !isFailed && env?.status === ENV_STATUS.RUNNING;
  const tone = isCancelled
    ? STATUS_META.cancelled.color
    : isCancelling
      ? STATUS_META.cancelling.color
      : isFailed
        ? STATUS_META.failed.color
        : isFinalizing
          ? STATUS_META.finalizing.color
          : isBuilding
            ? STATUS_META.building.color
            : isRunning
              ? STATUS_META.running.color
              : STATUS_META.passed.color;
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

  return (
    <CustomTooltip show title={tooltip} size="small" arrow>
      <Stack
        direction="row"
        alignItems="center"
        spacing={0.5}
        sx={{
          px: 0.75,
          height: 22,
          borderRadius: 0.75,
          cursor: "default",
          color: tone,
          bgcolor: (t) => alpha(tone, t.palette.mode === "dark" ? 0.16 : 0.1),
          border: () => `1px solid ${alpha(tone, 0.24)}`,
        }}
      >
        <Box
          sx={{
            width: 6,
            height: 6,
            borderRadius: "50%",
            bgcolor: tone,
            animation: (isBuilding || isFinalizing || isCancelling) && !isCancelled ? "env-pulse 1.4s ease-in-out infinite" : undefined,
            "@keyframes env-pulse": {
              "0%,100%": { opacity: 0.4 },
              "50%": { opacity: 1 },
            },
          }}
        />
        <Typography sx={{ typography: "s3", fontWeight: "fontWeightSemiBold" }}>
          {label}
        </Typography>
      </Stack>
    </CustomTooltip>
  );
}

LivePill.propTypes = {
  env: PropTypes.shape({ buildStatus: PropTypes.string, status: PropTypes.string }),
  building: PropTypes.bool,
};
