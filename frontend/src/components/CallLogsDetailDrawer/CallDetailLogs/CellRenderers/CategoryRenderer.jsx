import { Box, Chip } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import { typography } from "src/theme/typography";
import _ from "lodash";

const categoryColor = {
  system: {
    color: "status.success.fg",
    backgroundColor: "status.success.bg",
  },
  voice: {
    color: "text.secondary",
    backgroundColor: "action.hover",
  },
  transcriber: {
    color: "status.info.fg",
    backgroundColor: "status.info.bg",
  },
  endpointing: {
    color: "status.warning.fg",
    backgroundColor: "status.warning.bg",
  },
  model: {
    color: "primary.main",
    backgroundColor: "action.hover",
  },
  latency: {
    color: "status.danger.fg",
    backgroundColor: "status.danger.bg",
  },
  transport: {
    color: "status.warning.fg",
    backgroundColor: "status.warning.bg",
  },
  webhook: {
    color: "status.success.fg",
    backgroundColor: "status.success.bg",
  },
};

const CategoryRenderer = ({ value }) => {
  return (
    <Box>
      <Chip
        label={_.capitalize(value)}
        sx={{
          ...categoryColor[value],

          "&:hover": {
            ...categoryColor[value],
          },
          "& .MuiChip-label": {
            padding: 0,
            ...typography.s3,
          },
        }}
      />
    </Box>
  );
};

export default CategoryRenderer;

CategoryRenderer.propTypes = {
  value: PropTypes.string,
};
