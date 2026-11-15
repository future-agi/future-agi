import React from "react";
import PropTypes from "prop-types";
import { Typography } from "@mui/material";
import { formatLatency } from "../formatters";

const LatencyCell = ({ value }) => {
  return (
    <Typography
      variant="body2"
      sx={{
        fontSize: 12,
        fontVariantNumeric: "tabular-nums",
        textAlign: "right",
        width: "100%",
        px: 1.5,
        height: "100%",
        display: "flex",
        alignItems: "center",
        justifyContent: "flex-end",
      }}
    >
      {formatLatency(value)}
    </Typography>
  );
};

LatencyCell.propTypes = {
  value: PropTypes.any,
};

export default React.memo(LatencyCell);
