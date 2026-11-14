import { Box, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import SvgColor from "../../../../../components/svg-color";

const OptimizeResultHeader = ({ displayName }) => {
  return (
    <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
      <SvgColor
        src="/assets/icons/action_buttons/ic_evaluate.svg"
        sx={{ width: 20, height: 20, color: "status.success.fg" }}
      />
      <Typography typography="s1" fontWeight="fontWeightMedium">
        {displayName}
      </Typography>
    </Box>
  );
};

OptimizeResultHeader.propTypes = {
  displayName: PropTypes.string,
};

export default OptimizeResultHeader;
