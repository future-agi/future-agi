import React from "react";
import { Box, keyframes } from "@mui/material";
import PropTypes from "prop-types";
import SvgColor from "src/components/svg-color";
import StatusBadge from "src/components/status-badge/StatusBadge";
import { EXECUTION_STATUS_ROLE } from "src/sections/common/simulation/constants/statusStyles";

const spin = keyframes`
  from {
    transform: rotate(0deg);
  }
  to {
    transform: rotate(360deg);
  }
`;

const customLabels = {
  NotStarted: "Not Started",
};

const formatChipLabel = (label) => customLabels[label] || label;

const statusIcons = {
  NotStarted: "/assets/icons/ic_call_pending.svg",
  Queued: "/assets/icons/ic_queued_header.svg",
  Running: "/assets/icons/ic_queued_header.svg",
  Completed: "/assets/icons/ic_completed.svg",
  Cancelled: "/assets/icons/ic_close.svg",
  Editing: "/assets/icons/ic_edit_pencil.svg",
  Inactive: "/assets/icons/ic_clock.svg",
  Failed: "/assets/icons/ic_failed.svg",
  default: "/assets/icons/ic_clock.svg",
};

const ConsistentCellRender = ({ value }) => {
  const icon =
    statusIcons[value] ??
    statusIcons[value?.toLowerCase()] ??
    statusIcons.default;

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        width: "100%",
        height: "100%",
      }}
    >
      <StatusBadge
        status={value}
        label={formatChipLabel(value)}
        role={EXECUTION_STATUS_ROLE[value]}
        icon={
          <SvgColor
            src={icon}
            sx={{
              height: 14,
              width: 14,
              ...(value === "Running" && {
                animation: `${spin} 1s linear infinite`,
                willChange: "transform",
              }),
            }}
          />
        }
      />
    </Box>
  );
};

ConsistentCellRender.propTypes = {
  value: PropTypes.string,
};

export default ConsistentCellRender;
