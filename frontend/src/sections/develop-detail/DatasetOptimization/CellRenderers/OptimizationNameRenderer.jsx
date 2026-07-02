import React from "react";
import { Box, Typography } from "@mui/material";
import PropTypes from "prop-types";
import { format, isValid } from "date-fns";
import CustomTooltip from "src/components/tooltip";

const OptimizationNameRenderer = ({ value, data }) => {
  // Use data directly if value is not properly passed from valueGetter
  const title = value?.title || data?.optimizationName;
  const startedAt = value?.startedAt || data?.startedAt;

  return (
    <CustomTooltip title={title} arrow size="small" type="black">
      <Box display="flex" flexDirection="column" gap={0.5} sx={{ paddingY: 1 }}>
        <Typography
          typography="s1"
          fontWeight="fontWeightMedium"
          color={"text.primary"}
          sx={{
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
            width: "100%",
          }}
        >
          {title || "-"}
        </Typography>
        {startedAt && isValid(new Date(startedAt)) ? (
          <Typography color={"text.secondary"} typography="s3">
            {`Started at ${format(new Date(startedAt), "dd-MM-yyyy, HH:mm")}`}
          </Typography>
        ) : (
          <Typography typography="s3" color="text.secondary">
            -
          </Typography>
        )}
      </Box>
    </CustomTooltip>
  );
};

OptimizationNameRenderer.propTypes = {
  value: PropTypes.object,
  data: PropTypes.object,
};

export default OptimizationNameRenderer;
