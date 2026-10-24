import React from "react";
import PropTypes from "prop-types";
import { Paper, Typography } from "@mui/material";

export default function JsonCodeView({ data }) {
  // Convert object to JSON string
  const jsonString = JSON.stringify(data, null, 2);

  return (
    <Paper
      variant="outlined"
      style={{ padding: "20px", backgroundColor: "var(--bg-neutral)" }}
    >
      <Typography component="pre" style={{ fontFamily: "monospace" }}>
        {jsonString}
      </Typography>
    </Paper>
  );
}

JsonCodeView.propTypes = {
  data: PropTypes.object,
};
