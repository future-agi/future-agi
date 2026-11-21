import { Box, Tooltip, Typography } from "@mui/material";
import { formatModelCost, modelCostSourceLabel } from "src/utils/model-cost";
import React from "react";
import PropTypes from "prop-types";
import Iconify from "src/components/iconify";
import { ShowComponent } from "src/components/show";

const OutputMetaItem = ({ icon, value }) => {
  return (
    <Box sx={{ display: "flex", alignItems: "center", gap: "2px" }}>
      <Iconify icon={icon} width="12px" height="12px" color="text.disabled" />
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
          value={`${metadata?.responseTime?.toFixed(2)}s`}
        />
      </ShowComponent>
      <Tooltip title={modelCostSourceLabel(metadata?.pricingSource)}>
        <Box component="span">
          <OutputMetaItem
            icon="solar:dollar-minimalistic-linear"
            value={formatModelCost(metadata?.cost, metadata?.pricingSource)}
          />
        </Box>
      </Tooltip>
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
