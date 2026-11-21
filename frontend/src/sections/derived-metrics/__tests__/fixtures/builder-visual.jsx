// Desktop-only component test entry. The companion Vite config swaps transport
// and tenant hooks for explicit fixtures; this is never an app route or build entry.
import React from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { SettingsProvider } from "src/components/settings";
import ThemeProvider from "src/theme";
import MetricBuilderPage from "../../MetricBuilderPage";

createRoot(document.getElementById("root")).render(
  <SettingsProvider
    defaultSettings={{
      themeMode: "light",
      themeDirection: "ltr",
      themeContrast: "default",
      themeColorPresets: "default",
      themeLayout: "vertical",
      themeStretch: false,
    }}
  >
    <ThemeProvider>
      <QueryClientProvider client={new QueryClient()}>
        <BrowserRouter>
          <MetricBuilderPage />
        </BrowserRouter>
      </QueryClientProvider>
    </ThemeProvider>
  </SettingsProvider>,
);
