import { Button, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import CustomTooltip from "src/components/tooltip";

export default function ExperimentDescriptionCell({ data }) {
  const isRunning = data?.status === "Running";

  return (
    <CustomTooltip
      title="Evaluation is still running"
      show={isRunning}
      arrow
      size="small"
    >
      <span>
        <Button color="primary" variant="text" disabled={isRunning}>
          <Typography
            typography="s1"
            color={"inherit"}
            fontWeight={"fontWeightRegular"}
          >
            View Detail
          </Typography>
        </Button>
      </span>
    </CustomTooltip>
  );
}

ExperimentDescriptionCell.propTypes = {
  value: PropTypes.any,
  data: PropTypes.object,
};
