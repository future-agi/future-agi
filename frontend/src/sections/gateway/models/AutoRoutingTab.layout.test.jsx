import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider, createTheme } from "@mui/material/styles";

import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";
import { toggleButton } from "src/theme/overrides/components/toggle-button";

import AutoRoutingTab from "./AutoRoutingTab";
import { CATALOG_MODELS } from "./test-fixtures/catalog";

const { get, post } = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));

vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ currentOrganizationId: "org-one" }),
}));

vi.mock("src/utils/axios", () => ({
  default: { get, post },
  endpoints: {
    gateway: {
      config: (id) => `/gateway/${id}/config`,
      updateConfig: (id) => `/gateway/${id}/config/update`,
      providers: (id) => `/gateway/${id}/providers`,
    },
  },
}));

vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));

// The app's segmented-control override (theme/overrides/components/
// toggle-button.js) makes every ToggleButton a 24 px option that never wraps.
// The Preference tiles are cards with a title and a sentence, so each tile
// sets its own height and wrapping; render with that override, as the app does.
const base = createTheme({ palette: palette("light"), typography });
const appTheme = createTheme({
  palette: palette("light"),
  typography,
  components: toggleButton(base),
});

const QUERY_OPTIONS = { defaultOptions: { queries: { retry: false } } };

const renderTab = () =>
  render(
    <ThemeProvider theme={appTheme}>
      <MemoryRouter>
        <QueryClientProvider client={new QueryClient(QUERY_OPTIONS)}>
          <AutoRoutingTab
            gatewayId="gw-1"
            canWrite
            catalogModels={CATALOG_MODELS}
            managedStatus={{ available: true, enabled: true }}
          />
        </QueryClientProvider>
      </MemoryRouter>
    </ThemeProvider>,
  );

beforeEach(() => {
  get.mockReset();
  get.mockImplementation(async (url) =>
    url.endsWith("/providers")
      ? { data: { status: true, result: { providers: [] } } }
      : { data: { status: true, result: { routing: { strategy: "cost" } } } },
  );
});

// The tiles were 24 px tall with nowrap text, so each
// description ran under its tile, into the next one and off the card.
describe("Auto routing preference tiles (layout)", () => {
  it.each(["Cost", "Balanced", "Quality"])(
    "lets the %s tile grow to fit its wrapped title and description",
    async (name) => {
      renderTab();
      const tile = await screen.findByRole("button", {
        name: new RegExp(`^${name}`),
      });
      await waitFor(() => expect(tile).toBeEnabled());
      const style = window.getComputedStyle(tile);
      expect(style.whiteSpace).toBe("normal");
      expect(style.height).toBe("auto");
      expect(style.lineHeight).not.toBe("24px");
      expect(style.alignSelf).toBe("stretch");
    },
  );
});
