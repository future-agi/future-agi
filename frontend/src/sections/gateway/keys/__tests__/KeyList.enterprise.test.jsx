import React from "react";
import { MemoryRouter } from "react-router-dom";
import { HelmetProvider } from "react-helmet-async";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ThemeProvider } from "@mui/material/styles";
import {
  afterAll,
  afterEach,
  beforeAll,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";

import KeyManagementSection from "../KeyManagementSection";
import { faTestTheme } from "../../__tests__/faTheme";
import { lastGrid } from "../../providers/__tests__/grid-mock";

const state = vi.hoisted(() => ({ keys: [] }));

vi.mock(
  "ag-grid-react",
  async () => await import("../../providers/__tests__/grid-mock"),
);
vi.mock("react-apexcharts", () => ({ default: () => null }));

vi.mock("../hooks/useApiKeys", () => ({
  useApiKeys: () => ({
    data: state.keys,
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  }),
  useApiKeyDetail: (id) => ({
    data: state.keys.find((k) => k.id === id) || null,
    isLoading: false,
  }),
  useCreateApiKey: () => ({
    mutate: vi.fn(),
    isPending: false,
    isError: false,
    reset: vi.fn(),
  }),
  useUpdateApiKey: () => ({ mutate: vi.fn(), isPending: false }),
  useRevokeApiKey: () => ({ mutate: vi.fn(), isPending: false }),
  useRotateApiKey: () => ({
    mutate: vi.fn(),
    isPending: false,
    reset: vi.fn(),
  }),
  useSyncApiKeys: () => ({ mutate: vi.fn(), isPending: false }),
}));
vi.mock("../../providers/hooks/useGatewayConfig", () => ({
  useProviderHealth: () => ({
    data: { providers: [] },
    isError: false,
    isLoading: false,
    refetch: vi.fn(),
  }),
  useGatewayConfig: () => ({ data: { providers: {} } }),
}));
vi.mock("../../providers/hooks/useOrgConfig", () => ({
  useOrgConfig: () => ({ data: { providers: {} } }),
}));
vi.mock("../../models/hooks/useManagedModels", () => ({
  useManagedModelsStatus: () => ({ data: { available: false } }),
  useManagedCatalog: () => ({ data: { models: [] } }),
}));
vi.mock("../../analytics/hooks/useAnalyticsOverview", () => ({
  useAnalyticsOverview: () => ({ data: undefined, isError: false }),
}));
vi.mock("../../analytics/hooks/useAnalyticsUsage", () => ({
  useAnalyticsUsage: () => ({ data: undefined, isError: false }),
}));
vi.mock("../../context/useGatewayContext", () => ({
  useGatewayContext: () => ({
    gatewayId: "default",
    gateway: { id: "default", baseUrl: "http://localhost:28490/v1" },
    hosted: true,
    deploymentModeConfirmed: true,
    isLoading: false,
  }),
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({
    role: "Owner",
    user: { id: "signed-in-user", organization_role: "Owner" },
  }),
}));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({
    orgLevel: 15,
    currentOrganizationId: "org-a",
    isReady: true,
  }),
}));
vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => ({ currentWorkspaceRole: null }),
}));
vi.mock("src/sections/settings/BillingV2/useCentralBilling", () => ({
  useCentralBilling: () => ({
    credits: null,
    creditsAvailable: false,
    visible: false,
    isLoading: false,
  }),
}));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));

const key = (overrides) => ({
  owner: "",
  keyPrefix: "sk-agentcc-4...",
  allowedModels: [],
  allowedProviders: [],
  metadata: {},
  user: "peer-user",
  organization: "org-a",
  created_at: "2026-10-01T12:00:00Z",
  last_used_at: null,
  expires_at: null,
  ...overrides,
});

// In the order the API sends them: a revoked key first, as on a busy org.
const KEYS = [
  key({ id: "k1", name: "old-ci", status: "revoked", gatewayKeyId: "gw-1" }),
  key({
    id: "k2",
    name: "support-bot",
    status: "active",
    gatewayKeyId: "gw-2",
    owner: "dana.lee.platform-team@example.com",
    created_at: "2026-10-20T12:00:00Z",
    last_used_at: "2026-10-20T09:00:00Z",
    expires_at: "2027-01-15T12:00:00Z",
  }),
  key({
    id: "k3",
    name: "nightly-eval",
    status: "expired",
    gatewayKeyId: "gw-3",
  }),
  key({ id: "k4", name: "checkout", status: "active", gatewayKeyId: "gw-4" }),
];

function renderPage() {
  return render(
    <HelmetProvider>
      <ThemeProvider theme={faTestTheme("light")}>
        <MemoryRouter initialEntries={["/dashboard/gateway/keys"]}>
          <KeyManagementSection />
        </MemoryRouter>
      </ThemeProvider>
    </HelmetProvider>,
  );
}

const rowNames = () =>
  screen.getAllByTestId("ag-row").map((row) => {
    const name = within(row).getByRole("button", { name: /^View details/ });
    return name.textContent;
  });
const rowFor = (name) =>
  screen.getAllByTestId("ag-row").find((row) => within(row).queryByText(name));

// The exact instant and its zone, as a hover title reads it.
const EXACT = /^[A-Z][a-z]{2} \d{1,2}, \d{4}, \d{2}:\d{2}:\d{2} \S+$/;

// useResponsive reads matchMedia; a desktop width (no query matches).
const originalMatchMedia = window.matchMedia;
beforeAll(() => {
  window.matchMedia = (query) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  });
});
afterAll(() => {
  window.matchMedia = originalMatchMedia;
});

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-10-20T12:00:00Z"));
  state.keys = KEYS.map((k) => ({ ...k }));
});
afterEach(() => {
  vi.useRealTimers();
});

// The API keys list read against the keys pages of Vercel, OpenRouter and
// Portkey: usable keys up front, counts, times a person can place, and the
// key's name in view when the table scrolls sideways.
describe("API keys list (enterprise pass)", () => {
  it("lists the keys that still work first, then expired, then revoked", () => {
    renderPage();
    expect(rowNames()).toEqual([
      "support-bot",
      "checkout",
      "nightly-eval",
      "old-ci",
    ]);
  });

  it("counts the keys by status and names the zone times are in", () => {
    renderPage();
    const count = screen.getByText(
      /^4 keys · 2 active · 1 expired · 1 revoked · times in \S+/,
    );
    expect(count).toHaveAttribute("role", "status");
  });

  it("names every status the keys have, so the parts add up", () => {
    state.keys = [
      ...KEYS,
      key({ id: "k5", name: "new-app", status: "pending", gatewayKeyId: "g5" }),
      key({ id: "k6", name: "broken", status: "failed", gatewayKeyId: "g6" }),
    ];
    renderPage();
    const count = screen.getByText(/^6 keys · /);
    expect(count).toHaveAttribute("role", "status");
    expect(count.textContent).toContain(
      "2 active · 1 pending · 1 failed · 1 expired · 1 revoked · times in",
    );
  });

  it("counts what a filter leaves", async () => {
    const user = userEvent.setup();
    renderPage();
    await user.click(screen.getByRole("button", { name: "Revoked" }));
    const count = screen.getByText(/^1 of 4 keys · times in \S+/);
    expect(count).toHaveAttribute("role", "status");
  });

  it("shows how long ago a key was used, the exact time on hover", () => {
    renderPage();
    const used = within(rowFor("support-bot")).getByText("3 hours ago");
    expect(used.getAttribute("title")).toMatch(EXACT);
    expect(within(rowFor("checkout")).getAllByText("Never")).toHaveLength(2);
  });

  it("puts the exact created and expiry times on hover", () => {
    renderPage();
    const row = rowFor("support-bot");
    const created = within(row).getByText("Oct 20, 2026");
    expect(created.getAttribute("title")).toMatch(EXACT);
    const expires = within(row).getByText("Jan 15, 2027");
    expect(expires.getAttribute("title")).toMatch(EXACT);
  });

  it("keeps the Name column pinned left so it stays in view", () => {
    renderPage();
    const headers = screen.getAllByRole("columnheader");
    const name = headers.find((th) => th.textContent === "Name");
    expect(name).toHaveAttribute("data-pinned", "left");
  });

  it("shows the whole owner on hover", () => {
    renderPage();
    const owner = lastGrid.props.columnDefs.find((c) => c.colId === "owner");
    const full = "dana.lee.platform-team@example.com";
    expect(owner.tooltipValueGetter?.({ value: full })).toBe(full);
  });
});
