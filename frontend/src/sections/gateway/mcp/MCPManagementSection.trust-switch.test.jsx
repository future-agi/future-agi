import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { HelmetProvider } from "react-helmet-async";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";
import axios from "src/utils/axios";

const { release, reviewMount, stubTab } = vi.hoisted(() => ({
  release: { enabled: false },
  reviewMount: vi.fn(),
  stubTab: (name) => () => ({ default: () => <div>{`${name} tab`}</div> }),
}));
vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn(), post: vi.fn() },
  endpoints: { gateway: {} },
}));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ currentOrganizationId: "org-a" }),
}));
vi.mock("../hooks/useGatewayAdmin", () => ({
  useGatewayAdmin: () => ({ canEdit: true }),
}));
vi.mock("../context/useGatewayContext", () => ({
  useGatewayContext: () => ({ gatewayId: "gw-1", isLoading: false }),
}));
vi.mock("../providers/hooks/useGatewayConfig", () => ({
  useGatewayConfig: () => ({ data: { mcp: { enabled: true } } }),
  useReloadConfig: () => ({ mutate: vi.fn(), isPending: false }),
  SavedButNotAppliedError: Error,
}));
vi.mock("./hooks/useMCPConfig", () => ({
  useMCPStatus: () => ({ data: { enabled: true, servers: [] } }),
}));
vi.mock("./hooks/useMCPConfigPatch", () => ({
  useMCPConfigPatch: () => ({ mutate: vi.fn(), isPending: false }),
}));
// OFF delegates to the real release registry; it is never forced hidden.
// Only the positive control overrides trust, at the existing visibility seam.
vi.mock("../shell/page-status", async (importOriginal) => {
  const actual = /** @type {typeof import("../shell/page-status")} */ (
    await importOriginal()
  );
  return {
    ...actual,
    isMcpTabHidden: (slug) =>
      slug === "trust" && release.enabled ? false : actual.isMcpTabHidden(slug),
  };
});
// Observe every render while retaining the real component and revision queries.
vi.mock("./trust/RevisionReview", async (importOriginal) => {
  const { default: RevisionReview } =
    /** @type {typeof import("./trust/RevisionReview")} */ (
      await importOriginal()
    );
  return {
    default: function ObservedRevisionReview(props) {
      reviewMount(props);
      return <RevisionReview {...props} />;
    },
  };
});
// Unrelated tab bodies cannot obscure whether the revision boundary is reached.
vi.mock("./overview/OverviewTab", stubTab("overview"));
vi.mock("./servers/ServersTab", stubTab("servers"));
vi.mock("./virtual/VirtualServersTab", stubTab("virtual"));
vi.mock("./calls/ToolCallsTab", stubTab("calls"));
vi.mock("./credentials/CredentialsTab", stubTab("credentials"));
vi.mock("./clients/MCPClientsTab", stubTab("clients"));
vi.mock("./guardrails/GuardrailsTab", stubTab("guardrails"));
vi.mock("./playground/PlaygroundTab", stubTab("playground"));
vi.mock("./resources/ResourcesTab", stubTab("resources"));
vi.mock("./prompts/PromptsTab", stubTab("prompts"));
vi.mock("./registry/RegistryTab", stubTab("registry"));
vi.mock("./connect/ConnectTab", stubTab("connect"));
vi.mock("./approvals/ApprovalsTab", stubTab("approvals"));

const MCP_PATH = "/dashboard/gateway/mcp";
const REVISIONS = "/agentcc/mcp/revisions/";
// W09 revision_1029 pins these exact literals, independently of product copy.
const OFF_TITLE = "Not available on this gateway";
const OFF_DESCRIPTION =
  "This gateway does not review changes to a server's tools.";
const theme = createTheme({ palette: palette("light"), typography });
let clients;
let location;
function LocationProbe() {
  location = useLocation();
  return null;
}
async function mount(url, { directTrust = false } = {}) {
  // tabs.js filters at module evaluation: re-import after setting the control.
  const { default: MCPManagementSection } = await import(
    "./MCPManagementSection"
  );
  const { default: TrustTab } = await import("./trust/TrustTab");
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  clients.push(client);
  const view = render(
    <QueryClientProvider client={client}>
      <HelmetProvider>
        <ThemeProvider theme={theme}>
          <MemoryRouter initialEntries={[url]}>
            <Routes>
              <Route
                path={`${MCP_PATH}/:tab?`}
                element={
                  directTrust ? (
                    <TrustTab gatewayId="gw-1" />
                  ) : (
                    <MCPManagementSection />
                  )
                }
              />
            </Routes>
            <LocationProbe />
          </MemoryRouter>
        </ThemeProvider>
      </HelmetProvider>
    </QueryClientProvider>,
  );
  await act(async () => {});
  return view;
}
function expectHiddenNavigation() {
  expect(
    screen.queryByRole("tab", { name: /^(Trust|Revisions|Tool revisions)$/ }),
  ).toBeNull();
}
function expectNeverMounted() {
  expect(reviewMount).not.toHaveBeenCalled();
  expect(screen.queryByRole("heading", { name: "Tool revisions" })).toBeNull();
}
function expectNoRequests() {
  // Stronger than filtering the revision prefix: no list, decision or config IO.
  expect(axios.get).not.toHaveBeenCalled();
  expect(axios.post).not.toHaveBeenCalled();
}
function expectOffFallback(container) {
  const view = within(container);
  expect(view.getByText(OFF_TITLE, { exact: true })).toBeVisible();
  expect(view.getByText(OFF_DESCRIPTION, { exact: true })).toBeVisible();
  expect(view.queryByRole("button")).toBeNull();
}
beforeEach(() => {
  vi.resetModules();
  vi.clearAllMocks();
  release.enabled = false;
  clients = [];
  location = undefined;
  vi.mocked(axios.get).mockImplementation(async (url) => {
    if (url === REVISIONS)
      return {
        data: { status: true, result: { revisions: [], gateway_synced: true } },
      };
    if (url === "/agentcc/gateways/gw-1/config/")
      return {
        data: { status: true, result: { mcp: { pinning: { pins: {} } } } },
      };
    throw new Error(`Unexpected GET ${url}`);
  });
  vi.mocked(axios.post).mockImplementation(async (url) => {
    throw new Error(`Unexpected POST ${url}`);
  });
});
afterEach(() => {
  cleanup();
  clients.forEach((client) => client.clear());
  release.enabled = false;
});

describe("tool revision release switch", () => {
  it.each([MCP_PATH, `${MCP_PATH}?tab=trust&x=1`, `${MCP_PATH}/trust?x=1`])(
    "default OFF hides navigation and never mounts or requests revisions at %s",
    async (url) => {
      const { HIDDEN_MCP_TABS, isMcpTabHidden } = await import(
        "../shell/page-status"
      );
      expect(HIDDEN_MCP_TABS).toHaveProperty("trust");
      expect(isMcpTabHidden("trust")).toBe(true);
      await mount(url);
      expect(screen.getByRole("tab", { name: "Overview" })).toHaveAttribute(
        "aria-selected",
        "true",
      );
      expect(screen.getByText("overview tab")).toBeVisible();
      expect(location.pathname).toBe(MCP_PATH);
      expect(location.search).toBe(url === MCP_PATH ? "" : "?x=1");
      expectHiddenNavigation();
      expectNeverMounted();
      expectNoRequests();
    },
  );

  it("default OFF preserves the exact W09 fallback at the direct Trust component boundary", async () => {
    // The real shell redirects hidden links (above). Mount its route content
    // directly to verify the defense-in-depth fallback W09 checks in source.
    const { container } = await mount(`${MCP_PATH}/trust`, {
      directTrust: true,
    });
    expectOffFallback(container);
    expectNeverMounted();
    expectNoRequests();
  });

  it("ON control reaches the real review and rejects every OFF invariant", async () => {
    release.enabled = true;
    const { container } = await mount(`${MCP_PATH}?tab=trust`);
    expect(await screen.findByText("No pending tool revisions.")).toBeVisible();
    await waitFor(() =>
      expect(axios.get).toHaveBeenCalledWith(
        "/agentcc/gateways/gw-1/config/",
        expect.anything(),
      ),
    );
    expect(screen.getByRole("tab", { name: "Tool revisions" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(location.search).toBe("?tab=trust");
    expect(reviewMount).toHaveBeenCalledWith({
      gatewayId: "gw-1",
      organizationId: "org-a",
      canEdit: true,
    });
    for (const status of ["pending", "approved"])
      expect(axios.get).toHaveBeenCalledWith(
        REVISIONS,
        expect.objectContaining({
          params: { status },
          headers: { "X-Organization-Id": "org-a" },
        }),
      );
    expect(screen.queryByText(OFF_TITLE)).toBeNull();
    expect(screen.queryByText(OFF_DESCRIPTION)).toBeNull();
    // The SAME assertions as the OFF tests must fail, not merely a second
    // set of positive assertions that could leave the absence test vacuous.
    expect(expectHiddenNavigation).toThrow();
    expect(expectNeverMounted).toThrow();
    expect(expectNoRequests).toThrow();
    expect(() => expectOffFallback(container)).toThrow();
  });
});
