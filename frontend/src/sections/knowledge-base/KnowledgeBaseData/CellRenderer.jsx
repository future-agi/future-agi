import React from "react";
import { Box } from "@mui/material";
import PropTypes from "prop-types";
import { format } from "date-fns";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip/CustomTooltip";
import StatusBadge from "src/components/status-badge/StatusBadge";

// Design system: a file's processing state renders through the shared
// StatusBadge. "Completed" here means the files are processed and ready to
// use, so it takes the success role (green) — unlike a run's "Completed",
// which means finished with mixed results and stays neutral.
const STATUS_KEY = {
  Processing: "running",
  Failed: "failed",
  Completed: "completed",
};
const STATUS_ROLE = { Completed: "success" };

export const ProcessingStatusCell = ({ value, data }) => {
  const status = value === "PartialCompleted" ? "Processing" : value;

  return (
    <Box
      sx={{
        height: "100%",
        width: "fit-content",
        display: "flex",
        justifyContent: "flex-start",
        alignItems: "center",
        gap: 0.5,
      }}
    >
      <StatusBadge
        status={STATUS_KEY[status] || "queued"}
        role={STATUS_ROLE[status]}
        label={status}
      />
      {status === "Failed" && data?.error && (
        <CustomTooltip show={true} title={data.error} arrow>
          <Iconify
            icon="solar:info-circle-linear"
            color="text.secondary"
            width={14}
            height={14}
          />
        </CustomTooltip>
      )}
    </Box>
  );
};

ProcessingStatusCell.propTypes = {
  value: PropTypes.string,
  data: PropTypes.object,
};

export const DateFormat = ({ value }) => {
  const newData = value
    ? format(new Date(value), "dd/MM/yyyy, HH:mm aaa")
    : value;
  return (
    <Box
      sx={{
        height: "100%",
        width: "100%",
        display: "flex",
        justifyContent: "flex-start",
        alignItems: "center",
      }}
    >
      {newData}
    </Box>
  );
};

DateFormat.propTypes = {
  value: PropTypes.string,
};
