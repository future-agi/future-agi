import { Box, Typography } from "@mui/material";
import React from "react";
import PropTypes from "prop-types";

const maskString = (str) => {
  const value = String(str || "");
  if (!value) return "-";
  if (value.includes("*")) return value;
  const start = value.slice(0, 4);
  const end = value.slice(-4);
  return start + "**********" + end;
};

const SecretKeyRenderer = ({ value }) => {
  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "center",
        height: "100%",
      }}
    >
      <Typography variant="body2" noWrap sx={{ fontSize: 13 }}>
        {maskString(value)}
      </Typography>
    </Box>
  );
};

SecretKeyRenderer.propTypes = {
  value: PropTypes.string,
};

export default SecretKeyRenderer;
