/* eslint-disable no-restricted-syntax -- ds: data colours (chart series) */
import React from "react";
import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";

export default function WidgetCodeBlock({ config }) {
  const { code, language } = config;

  return (
    <Box
      sx={{
        height: "100%",
        display: "flex",
        flexDirection: "column",
        overflow: "hidden",
      }}
    >
      {language && (
        <Box
          sx={{
            px: 1.5,
            py: 0.5,
            bgcolor: "action.hover",
            borderBottom: "1px solid",
            borderColor: "divider",
            flexShrink: 0,
          }}
        >
          <Typography
            sx={{ fontSize: 12, color: "text.secondary", fontWeight: 500 }}
          >
            {language}
          </Typography>
        </Box>
      )}
      <Box
        component="pre"
        sx={{
          flex: 1,
          overflow: "auto",
          m: 0,
          px: 1.5,
          py: 1,
          fontSize: 12,
          lineHeight: 1.6,
          fontFamily: "'IBM Plex Mono', monospace",
          color: "text.primary",
          bgcolor: "transparent",
          whiteSpace: "pre-wrap",
          wordBreak: "break-word",
        }}
      >
        {code || ""}
      </Box>
    </Box>
  );
}

WidgetCodeBlock.propTypes = { config: PropTypes.object.isRequired };
