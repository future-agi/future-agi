import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import IconButton from "@mui/material/IconButton";
import Stack from "@mui/material/Stack";

import ProductSwitcher from "./ProductSwitcher";
import { OmegaIcon } from "./icons";

// Narrow screens on Omega routes only: a slim bar that opens the host's nav
// drawer, with the product switcher beside it. `end` sits at the bar's right
// edge (the layout puts the Falcon AI launcher there, so nothing floats over
// the page at phone width).
export default function OmegaMobileBar({ onOpenNav, end = null }) {
  return (
    <Stack
      direction="row"
      alignItems="center"
      gap={0.5}
      data-testid="omega-mobile-bar"
      sx={{
        flexShrink: 0,
        height: 48,
        px: 1,
        borderBottom: (theme) => `1px solid ${theme.palette.divider}`,
        backgroundColor: "background.paper",
        position: "sticky",
        top: 0,
        zIndex: (theme) => theme.zIndex.appBar,
      }}
    >
      <IconButton
        aria-label="Open navigation"
        onClick={onOpenNav}
        sx={{ color: "text.primary" }}
      >
        <OmegaIcon name="menu" size={20} />
      </IconButton>
      <ProductSwitcher sx={{ ml: 0 }} />
      {end && (
        <Box
          data-testid="omega-mobile-bar-end"
          sx={{ ml: "auto", display: "flex", alignItems: "center", pl: 1 }}
        >
          {end}
        </Box>
      )}
    </Stack>
  );
}

OmegaMobileBar.propTypes = {
  onOpenNav: PropTypes.func.isRequired,
  end: PropTypes.node,
};
