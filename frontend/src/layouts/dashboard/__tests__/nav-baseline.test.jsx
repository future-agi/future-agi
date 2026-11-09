import React from "react";
import { MemoryRouter } from "react-router-dom";
import { render } from "@testing-library/react";
import { HelmetProvider } from "react-helmet-async";
import { QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";

import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";

import NavVertical from "../nav-vertical";
import NavMini from "../nav-mini";
import {
  rolloutQueryClient,
  capabilitiesAnswer,
} from "src/sections/omega/shell/__tests__/rollout-fixtures";
import { CAPABILITIES_QUERY_KEY } from "src/hooks/useCapabilities";

// With VITE_OMEGA_CONSOLE unset, the Future AGI sidebar must render exactly as
// it does on the line the Omega shell sits on. The snapshot in __snapshots__
// was first recorded against an export of omega/integration 28b805bc8; at the
// round 7 merge it was re-recorded against an export of the gateway UI v2 head
// 213abb82d0 (the AI Gateway switcher and rail) with dev's d469318d0 applied
// (no Personas entry), the two changes that line brings, with this file's
// rollout client replaced by an empty QueryClient (the flag is unset). Any
// change the Omega shell leaks into the unflagged sidebar still fails here.
// This file deliberately imports only modules that exist at that export.

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({
    user: {
      id: "u1",
      role: "Owner",
      organization_role: "Owner",
      getStartedCompleted: true,
      ws_enabled: false,
    },
  }),
}));
vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => ({ currentWorkspaceRole: null }),
}));
vi.mock("src/hooks/useDeploymentMode", () => ({
  useDeploymentMode: () => ({
    isOSS: false,
    isCloud: true,
    isEE: false,
    isLoading: false,
  }),
}));
vi.mock("src/api/workspaces/list", () => ({
  useWorkspacesList: () => ({ data: [] }),
  useWorkspaceFromList: () => ({ workspace: null }),
}));
vi.mock("src/utils/Mixpanel", () => {
  const names = new Proxy({}, { get: (_, key) => String(key) });
  return { trackEvent: () => {}, Events: names, PropertyName: names };
});
vi.mock("src/layouts/dashboard/WorkspaceSwitcher/WorkspaceSwitcher", () => ({
  default: () => <div data-testid="workspace-switcher" />,
}));
vi.mock("src/components/settings", () => ({
  useSettingsContext: () => ({ themeLayout: "vertical", onUpdate: () => {} }),
}));

const theme = createTheme({ palette: palette("light"), typography });

function installDesktopViewport() {
  window.matchMedia = (query) => {
    const min = /min-width:\s*([\d.]+)px/.exec(query);
    const max = /max-width:\s*([\d.]+)px/.exec(query);
    const width = 1280;
    let matches = Boolean(min || max);
    if (min && width < Number(min[1])) matches = false;
    if (max && width > Number(max[1])) matches = false;
    return {
      matches,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    };
  };
}

function renderNav(Nav, path) {
  // The nav reads the Omega rollout answer from the capabilities query; with
  // the flag unset it must not change the sidebar (the baseline below).
  const client = rolloutQueryClient();
  const capabilities = capabilitiesAnswer();
  // The pinned host snapshot includes Falcon. Model that deployment explicitly;
  // denied/loading/error capabilities are covered by the navigation gate tests.
  client.setQueryData(CAPABILITIES_QUERY_KEY, {
    data: {
      ...capabilities,
      features: { ...capabilities.features, falcon_ai: { allowed: true } },
    },
  });
  return render(
    <HelmetProvider>
      <QueryClientProvider client={client}>
        <ThemeProvider theme={theme}>
          <MemoryRouter initialEntries={[path]}>
            <Nav openNav={false} onCloseNav={() => {}} />
          </MemoryRouter>
        </ThemeProvider>
      </QueryClientProvider>
    </HelmetProvider>,
  );
}

describe("Future AGI sidebar with VITE_OMEGA_CONSOLE unset", () => {
  const originalMatchMedia = window.matchMedia;
  beforeAll(() => {
    vi.stubEnv("VITE_OMEGA_CONSOLE", "");
    installDesktopViewport();
  });
  afterAll(() => {
    vi.unstubAllEnvs();
    window.matchMedia = originalMatchMedia;
  });

  it.each(["/dashboard/develop", "/dashboard/observe"])(
    "vertical nav at %s matches the line baseline",
    (path) => {
      const { container } = renderNav(NavVertical, path);
      expect(container.innerHTML).toMatchSnapshot();
    },
  );

  it.each(["/dashboard/develop", "/dashboard/observe"])(
    "mini nav at %s matches the line baseline",
    (path) => {
      const { container } = renderNav(NavMini, path);
      expect(container.innerHTML).toMatchSnapshot();
    },
  );
});
