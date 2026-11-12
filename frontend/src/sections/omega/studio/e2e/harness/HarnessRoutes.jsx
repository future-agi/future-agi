// Test-only stand-in for src/routes/sections/index.jsx, swapped in by the
// Studio e2e build (serve-harness.cjs). Everything else on the page is the
// real app: index.html with its connect intake, main.jsx (Sentry, PostHog,
// GTM), app.jsx (New Relic, Mixpanel, providers, theme).
//
// It registers the Studio tab exactly as the shell lane does
// (routes/sections/omega.jsx): two routes, "studio" and "studio/*", both
// rendering the real StudioPage, so the browser proof covers moving between
// them without remounting the frame.
import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Typography from "@mui/material/Typography";
import { Route, Routes, useLocation } from "react-router-dom";

import StudioPage from "src/sections/omega/studio/StudioPage";

function Page({ children }) {
  return (
    <Box
      sx={{
        height: "100vh",
        display: "flex",
        flexDirection: "column",
        bgcolor: "background.default",
        overflow: "auto",
      }}
    >
      {children}
    </Box>
  );
}

Page.propTypes = { children: PropTypes.node };

function Elsewhere() {
  const location = useLocation();
  return (
    <Typography data-testid="harness-elsewhere" sx={{ p: 3 }}>
      {`Future AGI page ${location.pathname}${location.search}`}
    </Typography>
  );
}

export default function HarnessRoutes() {
  return (
    <Routes>
      <Route
        path="/dashboard/studio"
        element={
          <Page>
            <StudioPage />
          </Page>
        }
      />
      <Route
        path="/dashboard/studio/*"
        element={
          <Page>
            <StudioPage />
          </Page>
        }
      />
      <Route path="*" element={<Elsewhere />} />
    </Routes>
  );
}
