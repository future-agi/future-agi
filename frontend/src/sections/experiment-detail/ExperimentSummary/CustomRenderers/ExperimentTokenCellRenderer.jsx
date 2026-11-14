import { Box } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";

const ExperimentTokenCellRenderer = ({ value }) => {
  return (
    <Box
      sx={{
        whiteSpace: "normal",
        wordBreak: "break-word",
        width: "100%",
        lineHeight: 1.5,
        alignItems: "center",
        display: "flex",
        height: "100%",
        gap: 1.5,
      }}
    >
      <Box sx={{ display: "flex", gap: 1, alignItems: "center" }}>
        {/* <SvgColor
          src={`/assets/icons/components/ic_coin-fill.svg`}
          sx={{ width: "16px", height: "16px", color: "text.primary" }}
        /> */}
        {value}
      </Box>
    </Box>
  );
};

export default ExperimentTokenCellRenderer;

ExperimentTokenCellRenderer.propTypes = {
  value: PropTypes.string,
};
