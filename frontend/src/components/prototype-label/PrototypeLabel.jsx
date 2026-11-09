import React from "react";
import Box from "@mui/material/Box";
import GlobalStyles from "@mui/material/GlobalStyles";
import { alpha, useTheme } from "@mui/material/styles";

import {
  PROTOTYPE_LABEL_NAME,
  PROTOTYPE_LABEL_TEXT,
  PROTOTYPE_LABEL_VAR,
  prototypeLabelGlobalStyles,
} from "./prototypeLabelModel";

/**
 * the capability contract: the prototype label. A fixed strip across the top of every
 * page whose space the global styles reserve, so it never covers content or
 * controls. It has no close button, takes no focus and ignores clicks; its
 * text is real DOM text a screen reader reads. app.jsx loads it only in a
 * prototype build (static env check), so other builds carry none of it.
 */
export default function PrototypeLabel() {
  const theme = useTheme();
  const tint = alpha(theme.palette.warning.main, 0.18);
  return (
    <>
      <GlobalStyles styles={prototypeLabelGlobalStyles()} />
      <Box
        component="aside"
        role="note"
        aria-label={PROTOTYPE_LABEL_NAME}
        data-testid="prototype-label"
        sx={{
          position: "fixed",
          top: 0,
          left: 0,
          right: 0,
          height: `var(${PROTOTYPE_LABEL_VAR})`,
          zIndex: theme.zIndex.tooltip + 1,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          gap: 0.75,
          px: 2,
          boxSizing: "border-box",
          backgroundColor: "background.paper",
          backgroundImage: `linear-gradient(${tint}, ${tint})`,
          borderBottom: `1px solid ${theme.palette.divider}`,
          color: "text.primary",
          fontSize: 12,
          fontWeight: 500,
          lineHeight: 1,
          whiteSpace: "nowrap",
          overflow: "hidden",
          textOverflow: "ellipsis",
        }}
      >
        <Box
          component="span"
          aria-hidden="true"
          sx={{
            width: 6,
            height: 6,
            flexShrink: 0,
            borderRadius: "50%",
            backgroundColor: "warning.dark",
          }}
        />
        {PROTOTYPE_LABEL_TEXT}
      </Box>
    </>
  );
}
