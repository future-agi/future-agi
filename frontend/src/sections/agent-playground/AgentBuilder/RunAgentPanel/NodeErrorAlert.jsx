import React from "react";
import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";
import Iconify from "src/components/iconify";

export default function NodeErrorAlert({ errorMessage }) {
  if (!errorMessage) return null;

  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "flex-start",
        gap: 1.5,
        p: 1.5,
        mb: 2,
        borderRadius: 1,
        bgcolor: "error.lighter",
        border: "1px solid",
        borderColor: "error.light",
        color: "error.darker",
      }}
    >
      <Iconify
        icon="eva:alert-circle-fill"
        sx={{
          width: 20,
          height: 20,
          color: "error.main",
          flexShrink: 0,
          mt: 0.25,
        }}
      />
      <Box sx={{ flex: 1, minWidth: 0 }}>
        <Typography variant="subtitle2" sx={{ fontWeight: 600, mb: 0.25 }}>
          Execution Error
        </Typography>
        <Typography
          variant="body2"
          sx={{
            fontFamily: "monospace",
            fontSize: "0.8125rem",
            whiteSpace: "pre-wrap",
            wordBreak: "break-word",
          }}
        >
          {errorMessage}
        </Typography>
      </Box>
    </Box>
  );
}

NodeErrorAlert.propTypes = {
  errorMessage: PropTypes.string,
};
