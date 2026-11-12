// L7c g2 (STANDIN): the `Simulated` tag every endpoint Test result (HTTP,
// A2A, MCP, the remote endpoint) carries when Future AGI's simulated host
// answered, with the one tooltip wording (endpointModel.standInAnswerTip).
import React from "react";
import PropTypes from "prop-types";
import { Typography } from "@mui/material";
import CustomTooltip from "src/components/tooltip";
import { VOCAB } from "src/sections/common/vocab";
import { standInAnswerTip } from "./endpointModel";

export default function StandInTag({ who, testId }) {
  return (
    <CustomTooltip
      show
      title={standInAnswerTip(who)}
      size="small"
      placement="top"
    >
      <Typography
        component="span"
        typography="s3"
        color="text.secondary"
        data-testid={testId}
        aria-label={`${VOCAB.SIMULATED}: ${standInAnswerTip(who)}`}
        sx={{
          px: 0.5,
          border: "1px solid",
          borderColor: "divider",
          borderRadius: 0.5,
          whiteSpace: "nowrap",
        }}
      >
        {VOCAB.SIMULATED}
      </Typography>
    </CustomTooltip>
  );
}

StandInTag.propTypes = {
  /** What the simulated answer stands in for (a host, an agent, a server). */
  who: PropTypes.string,
  testId: PropTypes.string,
};
