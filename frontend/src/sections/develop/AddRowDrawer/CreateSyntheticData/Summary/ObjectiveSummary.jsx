import { Box, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";

const ObjectiveSummary = ({ showTitle }) => {
  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        gap: "12px",
        backgroundColor: "status.warning.bg",
        border: "1px solid",
        borderColor: "status.warning.border",
        padding: "12px",
        borderRadius: "8px",
      }}
    >
      {showTitle && (
        <Typography
          variant="s1"
          fontWeight={"fontWeightSemiBold"}
          color="text.primary"
        >
          Why objective is important?
        </Typography>
      )}
      <Box>
        <Typography
          fontWeight={"fontWeightRegular"}
          color="text.primary"
          typography={"s2"}
        >
          The objective tells us how you plan to use the dataset—like for
          fine-tuning, RAG, or classification. This helps us adapt the
          generation strategy to match your end goal, ensuring the data is not
          just realistic, but truly usable for your task.
        </Typography>
      </Box>
      <Box sx={{ marginTop: "8px" }}>
        <Typography
          variant="s2"
          fontWeight={"fontWeightMedium"}
          color="text.primary"
        >
          Example: &quot;fine-tune a model for classifying customer complaints
          by issue type.&quot;
        </Typography>
      </Box>
    </Box>
  );
};

export default ObjectiveSummary;

ObjectiveSummary.propTypes = {
  showTitle: PropTypes.bool,
};
