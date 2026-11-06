import PropTypes from "prop-types";
import React from "react";
import { Box } from "@mui/material";

const CustomTooltipImage = ({ show, title, x, y }) => {
  if (!show) return null;

  return (
    <Box
      sx={{
        position: "absolute",
        top: y + 10,
        left: x + 10,
        backgroundColor: (theme) => theme.palette.background.default,
        color: "text.primary",
        padding: "9px 16px",
        borderRadius: "4px",
        fontSize: "13px",
        maxWidth: "400px",
        wordWrap: "break-word",
        zIndex: 1000,
      }}
    >
      {title}
    </Box>
  );
};

CustomTooltipImage.propTypes = {
  show: PropTypes.bool.isRequired,
  title: PropTypes.node.isRequired,
  x: PropTypes.number.isRequired,
  y: PropTypes.number.isRequired,
};

export default CustomTooltipImage;
