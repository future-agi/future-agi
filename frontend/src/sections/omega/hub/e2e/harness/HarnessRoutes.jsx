// Test-only stand-in for src/routes/sections/index.jsx, swapped in by the
// lane build (serve-harness.cjs). Everything else is the real app: index.html,
// main.jsx, app.jsx (auth, organisation and workspace providers, theme, React
// Query) and the app's axios. It mounts HUB_ROUTES under /dashboard exactly as
// the integrator will (SPEC §7.1), inside a plain frame.
import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Typography from "@mui/material/Typography";
import { Navigate, useRoutes } from "react-router-dom";

import { useAuthContext } from "src/auth/hooks";
import { SplashScreen } from "src/components/loading-screen";
import { HUB_ROUTES } from "src/sections/omega/hub/routes";

// Like the dashboard's AuthGuard, wait for the session before any Hub page
// asks the API anything.
function Frame({ children }) {
  const { loading } = useAuthContext();
  if (loading) return <SplashScreen />;
  return (
    <Box sx={{ minHeight: "100vh", bgcolor: "background.default", display: "flex", flexDirection: "column" }}>
      <Box
        component="header"
        sx={{
          height: 48,
          px: 2,
          display: "flex",
          alignItems: "center",
          gap: 1,
          borderBottom: "1px solid",
          borderColor: "divider",
          bgcolor: "background.paper",
        }}
      >
        <Box sx={{ width: 20, height: 20, borderRadius: 0.75, bgcolor: "primary.main" }} />
        <Typography typography="s1" fontWeight={600}>
          Future AGI
        </Typography>
        <Typography typography="s2" color="text.disabled">
          / Omega Hub (lane harness)
        </Typography>
      </Box>
      <Box component="main" sx={{ flex: 1, display: "flex", minWidth: 0 }}>
        {children}
      </Box>
    </Box>
  );
}

Frame.propTypes = { children: PropTypes.node };

function SignedOut() {
  return (
    <Typography data-testid="harness-signed-out" sx={{ p: 3 }}>
      Not signed in. The e2e run signs in before opening a Hub page.
    </Typography>
  );
}

export default function HarnessRoutes() {
  return useRoutes([
    { path: "/", element: <Navigate to="/dashboard/hub" replace /> },
    { path: "/dashboard", children: HUB_ROUTES.map((route) => ({ ...route, element: <Frame>{route.element}</Frame> })) },
    { path: "/auth/*", element: <SignedOut /> },
    { path: "*", element: <SignedOut /> },
  ]);
}
