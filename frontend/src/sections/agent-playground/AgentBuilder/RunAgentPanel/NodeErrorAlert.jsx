import React from "react";
import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";
import { alpha } from "@mui/material/styles";
import Iconify from "src/components/iconify";

export default function NodeErrorAlert({ errorMessage }) {
  if (!errorMessage) return null;

  return (
    <Box
      data-testid="node-error-alert"
      sx={{
        display: "flex",
        alignItems: "flex-start",
        gap: 1.5,
        p: 1.5,
        mb: 2,
        borderRadius: 1,
        bgcolor: (theme) =>
          theme.palette.mode === "dark"
            ? alpha(theme.palette.error.dark, 0.2)
            : alpha(theme.palette.error.light, 0.12),
        border: "1px solid",
        borderColor: (theme) =>
          theme.palette.mode === "dark"
            ? alpha(theme.palette.error.main, 0.3)
            : alpha(theme.palette.error.main, 0.25),
        color: "text.primary",
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
        <Typography
          typography="s2"
          sx={{
            fontWeight: 600,
            mb: 0.5,
            color: "error.main",
          }}
        >
          Node execution failed
        </Typography>
        <Typography
          typography="s3"
          sx={{
            fontFamily: "monospace",
            whiteSpace: "pre-wrap",
            wordBreak: "break-word",
            color: "text.secondary",
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
