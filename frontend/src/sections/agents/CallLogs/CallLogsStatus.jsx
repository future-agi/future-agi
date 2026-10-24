import { Box } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import StatusBadge from "src/components/status-badge/StatusBadge";
import { callStatusCellStyle } from "../constants";
import _ from "lodash";

// Call statuses the product shows, with the role each one takes. Anything
// the run-status map already knows resolves from the status itself.
const CALL_STATUS_ROLE = {
  dropped: "warning",
  "not-connected": "danger",
};

export const CallLogsStatus = ({ value }) => {
  if (!value) return "-";

  const statusKey = _.toLower(value);
  const statusConfig = callStatusCellStyle[statusKey];

  if (!statusConfig) return value; // fallback if status not mapped

  return (
    <Box
      sx={{
        height: "100%",
        display: "flex",
        alignItems: "center",
      }}
    >
      <StatusBadge
        status={statusKey}
        role={CALL_STATUS_ROLE[statusKey]}
        label={_.capitalize(value.replace(/-/g, " "))}
      />
    </Box>
  );
};

CallLogsStatus.propTypes = {
  value: PropTypes.string,
};
