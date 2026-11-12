import React, { Suspense, lazy, useMemo, useState } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Stack from "@mui/material/Stack";

import TwoFactorBanner from "src/components/two-factor-enforcement/TwoFactorBanner";
import SosModeBanner from "src/layouts/dashboard/sos-mode-banner";
import PaletteBoundary from "src/sections/common/CommandPalette/PaletteBoundary";

import ProductSwitcher from "./ProductSwitcher";
import { OmegaConsoleChromeContext } from "./console-chrome";

// The app's one command palette, mounted once for every state of the Console
// tab (frame, viewer panel, loading screen); loaded after the page paints, in
// its own chunk. The frame keeps only the bridge to it.
const ConsolePaletteHost = lazy(
  () => import("src/sections/common/CommandPalette/ConsolePaletteHost"),
);

// /dashboard/console/*: a full-width page with no Future AGI sidebar. It has
// no minimum width, so it works at 390 px. It keeps the host's account-level
// banners (2FA enforcement overlay, SOS mode); it does not fire the dashboard
// page-view event, which lane embed-host owns for framed pages.
export default function OmegaConsoleLayout({ children }) {
  const [frameShown, setFrameShown] = useState(false);
  const chrome = useMemo(() => ({ frameShown, setFrameShown }), [frameShown]);

  return (
    <OmegaConsoleChromeContext.Provider value={chrome}>
      <TwoFactorBanner />
      <SosModeBanner />
      <Box
        data-testid="omega-console-layout"
        data-frame-shown={frameShown ? "true" : "false"}
        sx={{
          height: "100vh",
          width: 1,
          minWidth: 0,
          display: "flex",
          flexDirection: "column",
          backgroundColor: "background.paper",
          overflow: "hidden",
        }}
      >
        {!frameShown && (
          <Stack
            direction="row"
            alignItems="center"
            data-testid="omega-console-bar"
            sx={{
              flexShrink: 0,
              height: 48,
              px: 1,
              borderBottom: (theme) => `1px solid ${theme.palette.divider}`,
            }}
          >
            <ProductSwitcher sx={{ ml: 0 }} />
          </Stack>
        )}
        <Box
          component="main"
          sx={{
            flex: 1,
            minHeight: 0,
            display: "flex",
            flexDirection: "column",
            overflow: "auto",
          }}
        >
          {children}
        </Box>
      </Box>
      <PaletteBoundary>
        <Suspense fallback={null}>
          <ConsolePaletteHost />
        </Suspense>
      </PaletteBoundary>
    </OmegaConsoleChromeContext.Provider>
  );
}

OmegaConsoleLayout.propTypes = {
  children: PropTypes.node,
};
