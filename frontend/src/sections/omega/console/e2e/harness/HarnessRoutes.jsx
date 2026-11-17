// Test-only stand-in for src/routes/sections/index.jsx, swapped in by the
// console-frame e2e build (build-harness.cjs). Everything else on the page is
// the real app: index.html, main.jsx, app.jsx, providers and the theme.
//
// /dashboard/console/* renders the real ConsoleFramePage inside the real
// OmegaConsoleLayout (its slim bar with the product switcher while the frame
// is not shown). /dashboard/nav-mini mounts the actual compact app rail.
// Other paths are plain "Future AGI" pages carrying the real product switcher,
// as Future AGI's own nav does, so a test can see the
// Console send the user back and read the switcher's Console parts there.
import React, { useEffect } from "react";
import Box from "@mui/material/Box";
import Typography from "@mui/material/Typography";
import { Route, Routes, useLocation } from "react-router-dom";

import { useSettingsContext } from "src/components/settings";
import ConsoleFramePage from "src/sections/omega/console/ConsoleFramePage";
import OmegaConsoleLayout from "src/sections/omega/shell/OmegaConsoleLayout";
import ProductSwitcher from "src/sections/omega/shell/ProductSwitcher";
import { AuthContext } from "src/auth/context/jwt/auth-context";
import NavMini from "src/layouts/dashboard/nav-mini";

// Lets a test flip the Future AGI theme the way the settings drawer does.
function ThemeControl() {
  const settings = useSettingsContext();
  useEffect(() => {
    window.__consoleE2E = {
      setThemeMode: (mode) => settings.onUpdate("themeMode", mode),
    };
  }, [settings]);
  return null;
}

function ConsoleHarness() {
  return (
    <OmegaConsoleLayout>
      <ConsoleFramePage />
    </OmegaConsoleLayout>
  );
}

function FutureAgiPage() {
  const location = useLocation();
  return (
    <Box sx={{ display: "flex", minHeight: "100vh" }}>
      <Box
        component="nav"
        aria-label="Future AGI"
        data-testid="harness-future-agi-nav"
        sx={{ width: 240, borderRight: 1, borderColor: "divider", pt: 1 }}
      >
        <ProductSwitcher />
      </Box>
      <Box component="main" sx={{ flex: 1 }}>
        <Typography data-testid="harness-future-agi" sx={{ p: 3 }}>
          {`Future AGI page ${location.pathname}${location.search}`}
        </Typography>
      </Box>
    </Box>
  );
}

export default function HarnessRoutes() {
  return (
    <>
      <ThemeControl />
      <Routes>
        <Route path="/dashboard/console/*" element={<ConsoleHarness />} />
        {/* Real 52px rail, scroll containers and fonts; only identity is a fixture.
            Capabilities arrive over HTTP through the real app query provider. */}
        <Route
          path="/dashboard/nav-mini"
          element={
            <AuthContext.Provider
              value={{
                authenticated: true,
                user: {
                  id: "nav-mini-owner",
                  organization_role: "Owner",
                  role: "Owner",
                  ws_enabled: false,
                },
              }}
            >
              <Box data-testid="harness-nav-mini">
                <NavMini />
              </Box>
            </AuthContext.Provider>
          }
        />
        <Route path="*" element={<FutureAgiPage />} />
      </Routes>
    </>
  );
}
