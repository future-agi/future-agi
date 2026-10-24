import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { palette } from "src/theme/palette";

import ModelsSection from "./ModelsSection";
import AutoRoutingTab from "./AutoRoutingTab";
import ModelCatalog from "./ModelCatalog";
import { useManagedCatalog } from "./hooks/useManagedModels";

const { statusQuery } = vi.hoisted(() => ({
  statusQuery: { current: {} },
}));

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Owner" }),
}));
// The page's write controls follow the gateway admin role check
// (useGatewayAdmin): Owner (15) and Admin (8) write, Member (3) and Viewer
// (1) read.
let mockOrgLevel = 15;
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: mockOrgLevel }),
}));
vi.mock("../context/useGatewayContext", () => ({
  useGatewayContext: () => ({
    gateway: { baseUrl: "https://gw.example.com/v1" },
    gatewayId: "gw-1",
  }),
}));
vi.mock("./hooks/useManagedModels", () => ({
  useManagedModelsStatus: () => statusQuery.current,
  useManagedCatalog: vi.fn(({ enabled }) => ({
    data: enabled ? { models: [{ id: "x-ai/grok-5" }] } : undefined,
    isLoading: false,
    error: null,
    refetch: vi.fn(),
  })),
  useSetManagedModelsEnabled: () => ({ mutate: vi.fn(), isPending: false }),
  saveErrorMessage: (_, fallback) => fallback,
}));
// The tabs have their own tests; this file tests the page around them.
vi.mock("./AutoRoutingTab", () => ({
  default: vi.fn(() => <div>auto routing tab</div>),
}));
vi.mock("./ModelCatalog", () => ({
  default: vi.fn(() => <div>catalog tab</div>),
}));

// What the console's managed-models status returns on a deployment whose
// gateway has no managed provider
// (agentcc/views/managed_models.py).
const NOT_OFFERED = {
  available: false,
  enabled: false,
  balance_usd: null,
  markup_percent: null,
};
const OFFERED = {
  available: true,
  enabled: true,
  balance_usd: 20,
  markup_percent: 5,
};
const BROWSE_SUBTITLE =
  'Browse the models the gateway can call, and tune how model "auto" chooses between them';
const AUTO_ONLY_SUBTITLE =
  'Tune how model "auto" chooses between the models your providers serve';

const testTheme = createTheme({ palette: palette("light") });

let currentLocation;
const LocationProbe = () => {
  currentLocation = useLocation();
  return null;
};

function renderPage(url = "/dashboard/gateway/models") {
  return render(
    <ThemeProvider theme={testTheme}>
      <MemoryRouter initialEntries={[url]}>
        <Routes>
          <Route path="/dashboard/gateway/models" element={<ModelsSection />} />
          <Route
            path="/dashboard/gateway/models/:tab"
            element={<ModelsSection />}
          />
        </Routes>
        <LocationProbe />
      </MemoryRouter>
    </ThemeProvider>,
  );
}

const tabLabels = () => screen.queryAllByRole("tab").map((t) => t.textContent);
const catalogFetchEnabled = () =>
  useManagedCatalog.mock.calls.some(([options]) => options?.enabled);

beforeEach(() => {
  vi.clearAllMocks();
});

describe("ModelsSection without managed models", () => {
  beforeEach(() => {
    statusQuery.current = { data: NOT_OFFERED, isLoading: false };
  });

  it("opens on Auto routing and offers no catalog", () => {
    renderPage();

    expect(tabLabels()).toEqual(["Auto routing"]);
    expect(screen.getByRole("tab", { selected: true })).toHaveTextContent(
      "Auto routing",
    );
    expect(screen.getByText("auto routing tab")).toBeInTheDocument();
    expect(screen.queryByText("catalog tab")).toBeNull();
    expect(ModelCatalog).not.toHaveBeenCalled();
    expect(catalogFetchEnabled()).toBe(false);
  });

  it("shows no managed models card", () => {
    renderPage();

    expect(screen.queryByText("Managed models")).toBeNull();
    expect(screen.queryByText(/Not available on this deployment/)).toBeNull();
    expect(screen.queryByLabelText("Use managed models")).toBeNull();
  });

  it("says what the page does here, not that it lists models", () => {
    renderPage();

    expect(screen.getByText(AUTO_ONLY_SUBTITLE)).toBeInTheDocument();
    expect(screen.queryByText(BROWSE_SUBTITLE)).toBeNull();
  });

  it("opens Auto routing for a link to the catalog tab", () => {
    renderPage("/dashboard/gateway/models/catalog");

    expect(screen.getByText("auto routing tab")).toBeInTheDocument();
    expect(screen.queryByText("catalog tab")).toBeNull();
  });

  it("gives Auto routing no managed catalog models", () => {
    renderPage();

    expect(AutoRoutingTab).toHaveBeenCalledWith(
      expect.objectContaining({
        gatewayId: "gw-1",
        canWrite: true,
        catalogModels: undefined,
        managedStatus: NOT_OFFERED,
        baseUrl: "https://gw.example.com/v1",
      }),
      expect.anything(),
    );
  });
});

describe("ModelsSection for a user who is not an Owner or Admin", () => {
  beforeEach(() => {
    statusQuery.current = { data: OFFERED, isLoading: false };
  });

  it.each([
    ["a Member", 3],
    ["a Viewer", 1],
    ["a user whose level is not loaded", null],
  ])(
    "%s gets a read-only page: no auto routing or managed-models writes",
    (_who, level) => {
      mockOrgLevel = level;
      try {
        renderPage("/dashboard/gateway/models/auto");
        expect(AutoRoutingTab).toHaveBeenLastCalledWith(
          expect.objectContaining({ canWrite: false }),
          expect.anything(),
        );
        const toggle = screen.getByRole("checkbox", {
          name: "Use managed models",
        });
        expect(toggle).toBeDisabled();
        expect(
          toggle.closest('[aria-label="Owner or Admin only"]'),
        ).not.toBeNull();
      } finally {
        mockOrgLevel = 15;
      }
    },
  );
});

describe("ModelsSection with managed models offered", () => {
  beforeEach(() => {
    statusQuery.current = { data: OFFERED, isLoading: false };
  });

  it("shows the card and opens the catalog, with Auto routing beside it", () => {
    renderPage();

    expect(screen.getByText("Managed models")).toBeInTheDocument();
    expect(screen.getByText(BROWSE_SUBTITLE)).toBeInTheDocument();
    expect(tabLabels()).toEqual(["Catalog", "Auto routing"]);
    expect(screen.getByText("catalog tab")).toBeInTheDocument();
    expect(catalogFetchEnabled()).toBe(true);
  });

  it("switches to Auto routing by its URL", () => {
    renderPage();

    fireEvent.click(screen.getByRole("tab", { name: "Auto routing" }));

    expect(currentLocation.pathname).toBe("/dashboard/gateway/models/auto");
    expect(screen.getByText("auto routing tab")).toBeInTheDocument();
    expect(AutoRoutingTab).toHaveBeenLastCalledWith(
      expect.objectContaining({ catalogModels: [{ id: "x-ai/grok-5" }] }),
      expect.anything(),
    );

    fireEvent.click(screen.getByRole("tab", { name: "Catalog" }));

    expect(currentLocation.pathname).toBe("/dashboard/gateway/models");
    expect(screen.getByText("catalog tab")).toBeInTheDocument();
  });
});

describe("ModelsSection before the status is known", () => {
  it("shows neither tab while the status loads", () => {
    statusQuery.current = { data: undefined, isLoading: true };
    renderPage();

    expect(tabLabels()).toEqual([]);
    expect(screen.queryByText("auto routing tab")).toBeNull();
    expect(screen.queryByText("catalog tab")).toBeNull();
    expect(screen.queryByText(BROWSE_SUBTITLE)).toBeNull();
    expect(screen.queryByText(AUTO_ONLY_SUBTITLE)).toBeNull();
    expect(catalogFetchEnabled()).toBe(false);
  });

  it("keeps the retry and offers only Auto routing when the status fails", () => {
    const refetch = vi.fn();
    statusQuery.current = {
      data: undefined,
      isLoading: false,
      error: new Error("boom"),
      refetch,
    };
    renderPage();

    expect(
      screen.getByText("Couldn't load managed model status."),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(refetch).toHaveBeenCalled();
    expect(tabLabels()).toEqual(["Auto routing"]);
    expect(screen.getByText("auto routing tab")).toBeInTheDocument();
    expect(screen.queryByText(BROWSE_SUBTITLE)).toBeNull();
    expect(screen.queryByText(AUTO_ONLY_SUBTITLE)).toBeNull();
    expect(catalogFetchEnabled()).toBe(false);
  });
});
