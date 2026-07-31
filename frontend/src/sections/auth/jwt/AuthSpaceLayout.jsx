import React from "react";
import PropTypes from "prop-types";
import { Box, Stack } from "@mui/material";
import SvgColor from "src/components/svg-color";
import { SpaceBackdrop } from "src/components/space-backdrop";

// Single-column auth layout, shared by cloud and OSS. Replaces the old split
// form/promo panel.
export default function AuthSpaceLayout({ children, maxWidth = 440 }) {
  return (
    <Box
      sx={{
        position: "relative",
        width: "100%",
        height: "100vh",
        overflowY: "auto",
        bgcolor: "background.default",
        display: "flex",
      }}
    >
      <SpaceBackdrop />

      <Box
        sx={{
          position: "relative",
          zIndex: 1,
          width: "100%",
          maxWidth,
          px: 3,
          py: { xs: 6, md: 9 },
          // centers both axes, stays scrollable if content overflows
          m: "auto",
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
        }}
      >
        <Stack direction="row" gap={0.75} alignItems="center" sx={{ mb: 5 }}>
          <SvgColor
            src="/favicon/logo.svg"
            sx={{ height: 40, width: 40, color: "text.primary" }}
          />
          <SvgColor
            src="/logo/future_agi_text.svg"
            sx={{ height: 20, width: 128, color: "text.primary" }}
          />
        </Stack>

        <Box sx={{ width: "100%" }}>{children}</Box>
      </Box>
    </Box>
  );
}

AuthSpaceLayout.propTypes = {
  children: PropTypes.node,
  maxWidth: PropTypes.number,
};
