import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Typography from "@mui/material/Typography";

import { GatewayStatusChip } from "../../components/frame";
import { GATEWAY_MONO } from "../../utils/fonts";
import { actionName } from "../catalog/actions";

const ACTION_TONES = {
  block: "error",
  warn: "warning",
  mask: "warning",
  log: "neutral",
};

/** The action the gateway runs for a rule. */
export function ActionChip({ action }) {
  return (
    <GatewayStatusChip
      tone={ACTION_TONES[action] || "neutral"}
      label={actionName(action)}
      showIcon={false}
    />
  );
}
ActionChip.propTypes = { action: PropTypes.string };

export function RuleCell({ data }) {
  return (
    <Box sx={{ minWidth: 0, lineHeight: 1.3, py: 0.75 }}>
      <Typography typography="s1" color="text.primary" noWrap>
        {data.label}
      </Typography>
      {data.label !== data.ruleName && (
        <Typography
          typography="s3"
          color="text.secondary"
          noWrap
          sx={{ fontFamily: GATEWAY_MONO }}
        >
          {data.ruleName}
        </Typography>
      )}
    </Box>
  );
}
RuleCell.propTypes = { data: PropTypes.object.isRequired };
