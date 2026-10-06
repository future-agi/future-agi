import React from "react";
import PropTypes from "prop-types";
import { Typography } from "@mui/material";

const CompareSectionLabel = ({ children }) => (
  <Typography
    sx={{
      fontSize: 10,
      fontWeight: 600,
      color: "text.secondary",
      textTransform: "uppercase",
      letterSpacing: "0.06em",
    }}
  >
    {children}
  </Typography>
);

CompareSectionLabel.propTypes = { children: PropTypes.node };

export default CompareSectionLabel;
