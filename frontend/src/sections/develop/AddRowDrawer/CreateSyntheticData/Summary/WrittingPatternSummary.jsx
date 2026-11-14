import { Box, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";

const WrittingPatternSummary = ({ showTitle }) => {
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
          Why Writing Pattern is Important?
        </Typography>
      )}
      <Box>
        <Typography
          fontWeight={"fontWeightRegular"}
          color="text.primary"
          typography={"s2"}
        >
          The pattern helps define the style, tone, or behavioural traits the
          generated data should follow. Whether it’s friendly, formal, concise,
          or engaging—this guides the system to produce outputs that not only
          fit your content needs, but also match the way you want the data to
          sound or behave.
        </Typography>
      </Box>
      <Box sx={{ marginTop: "8px" }}>
        <Typography
          variant="s2"
          fontWeight={"fontWeightMedium"}
          color="text.primary"
        >
          Example: &quot;Use a friendly and empathetic tone. Responses should be
          concise but conversational, as if addressing a customer in real
          time.&quot;
        </Typography>
      </Box>
    </Box>
  );
};

export default WrittingPatternSummary;

WrittingPatternSummary.propTypes = {
  showTitle: PropTypes.bool,
};
