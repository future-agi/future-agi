// The MCP page on a deployment that runs with MCP off. The real page, tabs and
// hooks run against a mocked API, under a QueryClient whose caches report
// errors through the app's own handler, so a test sees every toast the page
// would raise.
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import {
  MutationCache,
  QueryCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import { enqueueSnackbar } from "notistack";
import { palette } from "src/theme/palette";
import { handleError } from "src/utils/queryErrorHandler";

import MCPManagementSection from "./MCPManagementSection";
import { MCP_TABS } from "./tabs";

const { get, post } = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));

vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar: vi.fn(),
}));
vi.mock("src/utils/axios", () => ({
  default: { get, post },
  // Every gateway endpoint is `/gw/<id>/<name>`, so a test answers by name.
  endpoints: {
    gateway: new Proxy(
      {},
      { get: (_, name) => (id) => `/gw/${id}/${String(name)}` },
    ),
  },
}));
vi.mock("../context/useGatewayContext", () => ({
  useGatewayContext: () => ({ gatewayId: "gw-1", isLoading: false }),
}));

const OFF_TITLE = "MCP is turned off for this deployment";
const STATUS_UNKNOWN = "MCP gateway: status unknown";

// The errors the axios interceptor rejects with for Django's error envelope.
const MCP_OFF_ERROR = {
  status: false,
  code: "gateway_mcp_disabled",
  message: "MCP is turned off for this deployment.",
  result: "MCP is turned off for this deployment.",
  statusCode: 404,
};
const GATEWAY_DOWN_ERROR = {
  status: false,
  code: "gateway_unreachable",
  message: "The gateway could not be reached for MCP data.",
  result: "The gateway could not be reached for MCP data.",
  statusCode: 502,
};

const OFF_STATUS = {
  enabled: false,
  reason: "gateway_mcp_disabled",
  sessions: 0,
  tools: 0,
  resources: 0,
  prompts: 0,
  servers: [],
};
const UNREACHABLE_STATUS = { ...OFF_STATUS, reason: "gateway_unreachable" };
// MCP is on, and this org has no MCP server of its own.
const NO_SERVER_STATUS = {
  enabled: false,
  sessions: 0,
  tools: 0,
  resources: 0,
  prompts: 0,
  servers: [],
};
const ON_STATUS = {
  enabled: true,
  sessions: 0,
  tools: 1,
  resources: 0,
  prompts: 0,
  servers: [{ server_id: "alpha", healthy: true, tool_count: 1 }],
};
const CONFIG = {
  version: 1,
  mcp: { servers: { alpha: { url: "https://a" } } },
};

const LISTS = ["mcpTools", "mcpResources", "mcpPrompts"];

// Answers GETs by endpoint name: `status` for the MCP status, `lists` (a
// value or an error) for the three MCP list reads, and the org config.
function answer({ status, lists }) {
  get.mockImplementation(async (url) => {
    const name = url.split("/").pop();
    if (name === "mcpStatus") {
      if (status instanceof Error || status?.statusCode) throw status;
      return { data: { status: true, result: status } };
    }
    if (LISTS.includes(name)) {
      if (lists?.statusCode) throw lists;
      return { data: { status: true, result: lists ?? [] } };
    }
    if (name === "config") return { data: { status: true, result: CONFIG } };
    throw new Error(`unexpected GET ${url}`);
  });
}

function listCalls() {
  return get.mock.calls
    .map(([url]) => url.split("/").pop())
    .filter((name) => LISTS.includes(name));
}

function errorToasts() {
  return enqueueSnackbar.mock.calls.filter(
    ([, options]) => options?.variant === "error",
  );
}

const testTheme = createTheme({ palette: palette("light") });

function renderPage(url) {
  const client = new QueryClient({
    queryCache: new QueryCache({ onError: handleError }),
    mutationCache: new MutationCache({ onError: handleError }),
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <ThemeProvider theme={testTheme}>
        <MemoryRouter initialEntries={[url]}>
          <Routes>
            <Route
              path="/dashboard/gateway/mcp"
              element={<MCPManagementSection />}
            />
            <Route
              path="/dashboard/gateway/mcp/:tab"
              element={<MCPManagementSection />}
            />
          </Routes>
        </MemoryRouter>
      </ThemeProvider>
    </QueryClientProvider>,
  );
}

// The tabs that read MCP data, as the deep links a user may hold.
const MCP_READING_TABS = [
  "overview",
  "servers",
  "guardrails",
  "playground",
  "resources",
  "prompts",
].filter((slug) => MCP_TABS.some((tab) => tab.slug === slug));

beforeEach(() => {
  get.mockReset();
  post.mockReset();
  enqueueSnackbar.mockClear();
});
afterEach(() => vi.clearAllMocks());

describe("MCP page with MCP turned off", () => {
  it.each(MCP_READING_TABS)(
    "shows the off state, no tabs and no error toast for ?tab=%s",
    async (slug) => {
      answer({ status: OFF_STATUS, lists: MCP_OFF_ERROR });
      renderPage(`/dashboard/gateway/mcp?tab=${slug}`);

      expect(await screen.findByText(OFF_TITLE)).toBeInTheDocument();
      expect(screen.getByText("MCP Tools")).toBeInTheDocument();
      expect(screen.queryByRole("tablist")).toBeNull();
      // Nothing to manage or reload: no management subtitle, no Reload.
      expect(screen.queryByText(/^Manage Model Context Protocol/)).toBeNull();
      expect(screen.queryByRole("button", { name: /Reload/ })).toBeNull();
      // The off state replaces the status banner: no other verdict shows.
      expect(screen.queryByText(/^MCP gateway:/)).toBeNull();
      expect(screen.queryByText(/in development/i)).toBeNull();
      // Settle any read the tab started before the status arrived.
      await waitFor(() => expect(get).toHaveBeenCalled());
      await new Promise((resolve) => setTimeout(resolve, 0));
      expect(errorToasts()).toEqual([]);
    },
  );

  it.each(LISTS)(
    "a %s read answered gateway_mcp_disabled raises no toast",
    async (name) => {
      // The status has not said "off" yet (still on), so the tabs mount and
      // read; the gateway turned MCP off in between.
      answer({ status: ON_STATUS, lists: MCP_OFF_ERROR });
      const slug = {
        mcpTools: "servers",
        mcpResources: "resources",
        mcpPrompts: "prompts",
      }[name];
      renderPage(`/dashboard/gateway/mcp?tab=${slug}`);

      await waitFor(() => expect(listCalls()).toContain(name));
      await new Promise((resolve) => setTimeout(resolve, 0));
      expect(errorToasts()).toEqual([]);
    },
  );
});

describe("MCP errors stay errors", () => {
  it("an unreachable gateway shows the warning and the tabs, not the off state", async () => {
    answer({ status: UNREACHABLE_STATUS, lists: [] });
    renderPage("/dashboard/gateway/mcp");

    expect(await screen.findByText(STATUS_UNKNOWN)).toBeInTheDocument();
    expect(
      screen.getByText(/gateway could not be reached/),
    ).toBeInTheDocument();
    expect(screen.getByRole("tablist")).toBeInTheDocument();
    expect(screen.queryByText(OFF_TITLE)).toBeNull();
  });

  it("a failed status request shows the error, not the off state", async () => {
    answer({ status: GATEWAY_DOWN_ERROR, lists: [] });
    renderPage("/dashboard/gateway/mcp");

    expect(
      await screen.findByText(/MCP status could not be loaded/),
    ).toBeInTheDocument();
    expect(screen.getByRole("tablist")).toBeInTheDocument();
    expect(screen.queryByText(OFF_TITLE)).toBeNull();
  });

  it.each([
    ["mcpTools", "servers", "tools"],
    ["mcpResources", "resources", "resources"],
    ["mcpPrompts", "prompts", "prompts"],
  ])(
    "a %s read the gateway failed still raises an error toast",
    async (name, slug, noun) => {
      answer({ status: ON_STATUS, lists: GATEWAY_DOWN_ERROR });
      renderPage(`/dashboard/gateway/mcp?tab=${slug}`);

      await waitFor(() => expect(errorToasts()).not.toEqual([]));
      expect(listCalls()).toContain(name);
      // The tab says the read failed, not that there is nothing to list.
      expect(
        await screen.findByText(
          `MCP ${noun} could not be loaded from the gateway.`,
        ),
      ).toBeInTheDocument();
      expect(screen.queryByText(/^No MCP \w+ registered/)).toBeNull();
      expect(screen.queryByText(OFF_TITLE)).toBeNull();
    },
  );

  it.each([
    ["servers", "tools"],
    ["resources", "resources"],
    ["prompts", "prompts"],
  ])(
    "the %s tab still says when there is nothing to list",
    async (slug, noun) => {
      answer({ status: ON_STATUS, lists: [] });
      renderPage(`/dashboard/gateway/mcp?tab=${slug}`);

      expect(
        await screen.findByText(new RegExp(`^No MCP ${noun} registered`)),
      ).toBeInTheDocument();
      expect(
        screen.queryByText(/could not be loaded from the gateway/),
      ).toBeNull();
    },
  );

  it("an org with no MCP server, MCP on, is told so, not that MCP is off", async () => {
    answer({ status: NO_SERVER_STATUS, lists: [] });
    renderPage("/dashboard/gateway/mcp");

    expect(
      await screen.findByText("MCP gateway: no server configured"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("No MCP server is configured for this organization."),
    ).toBeInTheDocument();
    expect(screen.queryByText(/did not report MCP as enabled/)).toBeNull();
    expect(screen.getByRole("tablist")).toBeInTheDocument();
    expect(screen.queryByText(OFF_TITLE)).toBeNull();
    expect(errorToasts()).toEqual([]);
  });

  it("MCP turned on shows the tabs and the Enabled banner", async () => {
    answer({ status: ON_STATUS, lists: [] });
    renderPage("/dashboard/gateway/mcp");

    expect(await screen.findByText("MCP gateway: Enabled")).toBeInTheDocument();
    expect(screen.getByRole("tablist")).toBeInTheDocument();
    expect(screen.queryByText(OFF_TITLE)).toBeNull();
    expect(
      screen.getByText(/^Manage Model Context Protocol/),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Reload Config/ }),
    ).toBeInTheDocument();
  });
});
