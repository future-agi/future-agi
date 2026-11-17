import { Box, Typography } from "@mui/material";
import React from "react";
import PropTypes from "prop-types";
import Iconify from "src/components/iconify";
import { ShowComponent } from "src/components/show";
import { fmtCost } from "src/sections/common/format/fmt";

// New runs send response_time in milliseconds with response_time_unit "ms"
// (responseTimeUnit here). Runs saved before that marker have no unit and
// keep showing their value as seconds, as before.
const formatSeconds = (responseTime, unit) => {
  const seconds =
    unit === "ms" ? Number(responseTime) / 1000 : Number(responseTime);
  return `${seconds.toFixed(2)}s`;
};

const OutputMetaItem = ({ icon, value }) => {
  return (
    <Box sx={{ display: "flex", alignItems: "center", gap: "2px" }}>
      <Iconify icon={icon} width="12px" height="12px" color="text.tertiary" />
      <Typography
        fontWeight={"fontWeightRegular"}
        color={"text.primary"}
        typography="s3"
      >
        {value}
      </Typography>
    </Box>
  );
};

OutputMetaItem.propTypes = {
  icon: PropTypes.string,
  value: PropTypes.string,
};

const OutputMeta = ({ metadata }) => {
  if (!metadata || Object.keys(metadata).length === 0) {
    return <></>;
  }

  return (
    <Box display={"flex"} gap={0.5}>
      <ShowComponent
        condition={
          metadata?.responseTime !== null &&
          metadata?.responseTime !== undefined
        }
      >
        <OutputMetaItem
          icon="solar:clock-circle-linear"
          value={formatSeconds(
            metadata?.responseTime,
            metadata?.responseTimeUnit,
          )}
        />
      </ShowComponent>
      <ShowComponent
        condition={
          metadata?.responseTime !== null &&
          metadata?.responseTime !== undefined
        }
      >
        <OutputMetaItem
          icon="solar:dollar-minimalistic-linear"
          value={fmtCost(metadata?.cost)}
        />
      </ShowComponent>
      <ShowComponent
        condition={
          metadata?.tokens !== null && metadata?.responseTime !== undefined
        }
      >
        <OutputMetaItem
          icon="solar:dollar-minimalistic-linear"
          value={`${metadata?.tokens ?? 0}`}
        />
      </ShowComponent>
    </Box>
  );
};

OutputMeta.propTypes = {
  metadata: PropTypes.object,
};

export default OutputMeta;
