// Test-only stand-in for src/routes/sections/index.jsx, swapped in by the
// e2e build (vite-harness.cjs). Everything else on the page is the real app:
// index.html, main.jsx (Sentry, PostHog, GTM), app.jsx (New Relic, Mixpanel,
// providers, theme). It renders EmbeddedSurface the way the Studio tab does,
// with requireGate: the surface is connected only once the child's own gate
// says "ready", never because a key was handed over.
import React from "react";
import Box from "@mui/material/Box";
import Typography from "@mui/material/Typography";
import { Route, Routes, useLocation } from "react-router-dom";

import {
  EmbeddedSurface,
  omegaConnectGeneration,
  peekOmegaConnect,
  takeOmegaConnectToken,
} from "src/sections/omega/embed";

// Read once per page load, before any render: the token is one-shot, and only
// this page load's link may hand it over (a later link is a newer generation).
const connect = peekOmegaConnect();
const generation = omegaConnectGeneration();
const takeToken = () => takeOmegaConnectToken(generation);

function StudioHarness() {
  if (!connect) {
    return (
      <Typography data-testid="harness-no-link" sx={{ p: 3 }}>
        No connect link on this page.
      </Typography>
    );
  }
  // The fake child takes its parent's origin from the query, the way Studio
  // takes it from its allow-list.
  const src = `${connect.url}/child.html?embed=1&parent=${encodeURIComponent(
    window.location.origin,
  )}`;
  return (
    <Box sx={{ height: "100vh", display: "flex", flexDirection: "column" }}>
      <EmbeddedSurface
        src={src}
        basePath="/dashboard/studio"
        getToken={takeToken}
        label="local Studio"
        title="Local Studio"
        probePath="/probe.json"
        requireGate
      />
    </Box>
  );
}

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
      <Route path="/dashboard/studio/*" element={<StudioHarness />} />
      <Route path="*" element={<Elsewhere />} />
    </Routes>
  );
}
