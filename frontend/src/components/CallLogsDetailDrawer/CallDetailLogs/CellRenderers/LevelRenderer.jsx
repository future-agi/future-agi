import { Box, Chip } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import { typography } from "src/theme/typography";

const levelColor = {
  LOG: {
    color: "primary.main",
    backgroundColor: "action.hover",
  },
  INFO: {
    color: "status.info.fg",
    backgroundColor: "status.info.bg",
  },
  WARN: {
    color: "status.warning.fg",
    backgroundColor: "status.warning.bg",
  },
  ERROR: {
    color: "status.danger.fg",
    backgroundColor: "status.danger.bg",
  },
};

const LevelRenderer = ({ value }) => {
  return (
    <Box>
      <Chip
        label={value}
        sx={{
          ...levelColor[value],

          "&:hover": {
            ...levelColor[value],
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

LevelRenderer.propTypes = {
  value: PropTypes.string,
};
export default LevelRenderer;
