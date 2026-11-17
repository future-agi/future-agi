import React from "react";
import { Box } from "@mui/material";

/**
 * `Skip to content`: first in the tab order, visually hidden until focused;
 * moves focus to <main>.
 */
export default function SkipLink() {
  return (
    <Box
      component="a"
      href="#main-content"
      onClick={(e) => {
        e.preventDefault();
        document.getElementById("main-content")?.focus();
      }}
      sx={{
        position: "fixed",
        top: "calc(8px + var(--fa-proto-label-h, 0px))",
        left: 8,
        zIndex: 2000,
        px: 1.5,
        py: 0.75,
        borderRadius: 1,
        typography: "s1",
        fontWeight: "fontWeightMedium",
        bgcolor: "background.paper",
        color: "primary.main",
        border: "1px solid",
        borderColor: "primary.main",
        textDecoration: "none",
        transform: "translateY(-200%)",
        transition: "transform 120ms ease",
        "&:focus, &:focus-visible": {
          transform: "translateY(0)",
          outline: "2px solid",
          outlineColor: "primary.main",
          outlineOffset: 2,
        },
      }}
    >
      Skip to content
    </Box>
  );
}
