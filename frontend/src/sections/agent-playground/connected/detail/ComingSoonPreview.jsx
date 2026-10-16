import PropTypes from "prop-types";
import React from "react";
import { Box, Stack, Typography } from "@mui/material";
import { alpha } from "@mui/material/styles";
import ComingSoonChip from "src/sections/simulate/environments/components/ComingSoonChip";

/**
 * A designed area that isn't live yet: the real layout, dimmed and out of
 * reach, with a note on top saying what it will do.
 */
export default function ComingSoonPreview({ title, description, children }) {
  return (
    <Box
      component="section"
      aria-label={title}
      sx={{
        position: "relative",
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1.5,
        bgcolor: "background.paper",
        overflow: "hidden",
      }}
    >
      <Box
        data-testid="coming-soon-preview"
        aria-hidden="true"
        inert=""
        sx={{
          maxHeight: 400,
          overflow: "hidden",
          opacity: 0.45,
          filter: "grayscale(0.5)",
          pointerEvents: "none",
          userSelect: "none",
        }}
      >
        {children}
      </Box>
      <Box
        sx={{
          position: "absolute",
          inset: 0,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          p: 2,
          background: (t) =>
            `linear-gradient(to bottom, ${alpha(t.palette.background.paper, 0.1)} 0%, ${alpha(
              t.palette.background.paper,
              0.6,
            )} 60%, ${alpha(t.palette.background.paper, 0.9)} 100%)`,
        }}
      >
        <Stack
          alignItems="center"
          spacing={0.75}
          sx={{
            px: 2.5,
            py: 2,
            maxWidth: 340,
            textAlign: "center",
            bgcolor: "background.paper",
            border: "1px solid",
            borderColor: "divider",
            borderRadius: 1.5,
            boxShadow: (t) =>
              `0 12px 32px ${alpha(t.palette.common.black, t.palette.mode === "dark" ? 0.5 : 0.12)}`,
          }}
        >
          <ComingSoonChip />
          <Typography sx={{ typography: "s1", fontWeight: 600 }}>
            {title}
          </Typography>
          <Typography sx={{ typography: "s2", color: "text.secondary" }}>
            {description}
          </Typography>
        </Stack>
      </Box>
    </Box>
  );
}

ComingSoonPreview.propTypes = {
  title: PropTypes.string.isRequired,
  description: PropTypes.string.isRequired,
  children: PropTypes.node.isRequired,
};
