import React from "react";
import PropTypes from "prop-types";
import { Typography } from "@mui/material";

/** One "Label: value" line in the request drawer's Overview blocks. */
const DetailRow = ({ label, children }) => (
  <Typography variant="body2">
    <strong>{label}:</strong> {children}
  </Typography>
);

DetailRow.propTypes = {
  label: PropTypes.string.isRequired,
  children: PropTypes.node,
};

export default DetailRow;
