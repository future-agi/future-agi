import React from "react";
import CardWrapper from "./CardWrapper";
import MetricTile from "./MetricTile";
import PropTypes from "prop-types";
import { Grid } from "@mui/material";
import {
  getIcon,
  getIconColor,
  getLabel,
  getSubtext,
  getSuffix,
  getTooltipMessage,
} from "./common";

export default function SystemMetrics({ data, expanded }) {
  const keysLength = Object.keys(data)?.length;
  return (
    <CardWrapper expanded={expanded} title={`System Metrics (${keysLength})`}>
      <Grid container spacing={2}>
        {Object.entries(data)?.map(([key, value]) => (
          <Grid key={key} item xs={6}>
            <MetricTile
              label={getLabel(key)}
              value={value}
              suffix={getSuffix(key)}
              subtext={getSubtext(key)}
              icon={getIcon(key)}
              iconColor={getIconColor(key)}
              tooltip={getTooltipMessage(key)}
            />
          </Grid>
        ))}
      </Grid>
    </CardWrapper>
  );
}

SystemMetrics.propTypes = {
  data: PropTypes.object.isRequired,
  expanded: PropTypes.bool,
};
