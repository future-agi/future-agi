import React from "react";
import PropTypes from "prop-types";
import { Typography } from "@mui/material";

export default function NumericCell({ value, sx }) {
  if (
    value === undefined ||
    value === null ||
    (typeof value === "string" && value.trim() === "")
  ) {
    return null;
  }

  const numValue = Number(value);
  const safeValue = isNaN(numValue)
    ? "-"
    : Number.isInteger(numValue)
      ? String(numValue)
      : numValue.toFixed(4);
  return (
    <Typography
      typography="s3"
      color="text.primary"
      sx={{
        padding: 1,
        ...sx,
      }}
    >
      {safeValue}
    </Typography>
  );
}

NumericCell.propTypes = {
  value: PropTypes.oneOfType([PropTypes.number, PropTypes.string]),
  sx: PropTypes.object,
};
