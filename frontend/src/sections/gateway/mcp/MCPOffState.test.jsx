// The MCP page on a deployment that runs with MCP off. The real page, tabs and
// hooks run against a mocked API, under a QueryClient whose caches report
// errors through the app's own handler, so a test sees every toast the page
// would raise.
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { HelmetProvider } from "react-helmet-async";
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

const OFF_TITLE = "MCP is turned off on this gateway";
const UNREACHABLE_TITLE = "The gateway could not be reached";

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
// The tab that reads each list. Resources and prompts remain visible but
// disabled, so only enabled tabs exercise list reads. Separate cases below
// assert that the disabled tabs never request their lists.
const LIST_TAB = {
  mcpTools: "servers",
  mcpResources: "resources",
  mcpPrompts: "prompts",
};
const shownTab = (slug) =>
  MCP_TABS.some((tab) => tab.slug === slug && !tab.disabled);
const SHOWN_LISTS = LISTS.filter((name) => shownTab(LIST_TAB[name]));

// Answers GETs by endpoint name: `status` for the MCP status, `lists` (a
// value or an error) for the three MCP list reads, and the org config.
function answer({ status, lists, config = CONFIG }) {
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
    if (name === "config") return { data: { status: true, result: config } };
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
    <HelmetProvider>
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
      </QueryClientProvider>
    </HelmetProvider>,
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
    "shows the off notice and baseline tabs without live reads or error toasts for ?tab=%s",
    async (slug) => {
      answer({ status: OFF_STATUS, lists: MCP_OFF_ERROR });
      renderPage(`/dashboard/gateway/mcp?tab=${slug}`);

      expect(await screen.findByText(OFF_TITLE)).toBeInTheDocument();
      expect(screen.getByText("MCP tools")).toBeInTheDocument();
      expect(screen.getByRole("tablist")).toBeInTheDocument();
      expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual([
        "Overview",
        "Tools",
        "Servers",
        "Resources",
        "Prompts",
        "Guardrails",
        "Playground",
      ]);
      expect(
        screen.getByRole("button", { name: /Reload config/ }),
      ).toBeInTheDocument();
      expect(screen.getByRole("alert")).toHaveTextContent(OFF_TITLE);
      expect(screen.getByText("MCP off on this gateway")).toBeInTheDocument();
      expect(listCalls()).toEqual([]);
      expect(screen.queryByText(/in development/i)).toBeNull();
      // Settle any read the tab started before the status arrived.
      await waitFor(() => expect(get).toHaveBeenCalled());
      await new Promise((resolve) => setTimeout(resolve, 0));
      expect(errorToasts()).toEqual([]);
    },
  );

  it.each(SHOWN_LISTS)(
    "a %s read answered gateway_mcp_disabled raises no toast",
    async (name) => {
      // The status has not said "off" yet (still on), so the tabs mount and
      // read; the gateway turned MCP off in between.
      answer({ status: ON_STATUS, lists: MCP_OFF_ERROR });
      renderPage(`/dashboard/gateway/mcp?tab=${LIST_TAB[name]}`);

      await waitFor(() => expect(listCalls()).toContain(name));
      await new Promise((resolve) => setTimeout(resolve, 0));
      expect(errorToasts()).toEqual([]);
    },
  );
});

describe("MCP errors stay errors", () => {
  it.each(["resources", "prompts"])(
    "keeps %s visible but makes no list request even with MCP on",
    async (slug) => {
      answer({ status: ON_STATUS, lists: GATEWAY_DOWN_ERROR });
      renderPage(`/dashboard/gateway/mcp?tab=${slug}`);
      expect(await screen.findByText("MCP on")).toBeInTheDocument();
      expect(
        screen.getByRole("tab", {
          name: slug === "resources" ? "Resources" : "Prompts",
        }),
      ).toBeDisabled();
      expect(
        screen.getByText("The Resources and Prompts tabs are disabled."),
      ).toBeInTheDocument();
      expect(listCalls()).toEqual([]);
      expect(errorToasts()).toEqual([]);
      expect(post).not.toHaveBeenCalled();
    },
  );
  it("an unreachable gateway shows the warning and the tabs, not the off state", async () => {
    answer({ status: UNREACHABLE_STATUS, lists: [] });
    renderPage("/dashboard/gateway/mcp");

    expect(await screen.findByText(UNREACHABLE_TITLE)).toBeInTheDocument();
    expect(screen.getByText("gateway_unreachable")).toBeInTheDocument();
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
    // Late tab observers must not restart this failed request or hide its banner.
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(
      screen.getByText(/MCP status could not be loaded/),
    ).toBeInTheDocument();
    expect(
      get.mock.calls.filter(([url]) => url === "/gw/gw-1/mcpStatus"),
    ).toHaveLength(1);
    expect(errorToasts()).toHaveLength(1);
  });

  it.each(
    [
      ["mcpTools", "servers", "tools"],
      ["mcpResources", "resources", "resources"],
      ["mcpPrompts", "prompts", "prompts"],
    ].filter(([, slug]) => shownTab(slug)),
  )(
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
      expect(
        screen.queryByText(/^No (tools|resources|prompts)( yet)?$/),
      ).toBeNull();
      expect(screen.queryByText(OFF_TITLE)).toBeNull();
    },
  );

  it.each(
    [
      ["servers", "No tools yet"],
      ["resources", "No resources"],
      ["prompts", "No prompts"],
    ].filter(([slug]) => shownTab(slug)),
  )(
    "the %s tab still says when there is nothing to list",
    async (slug, emptyTitle) => {
      answer({ status: ON_STATUS, lists: [] });
      renderPage(`/dashboard/gateway/mcp?tab=${slug}`);

      expect(await screen.findByText(emptyTitle)).toBeInTheDocument();
      expect(
        screen.queryByText(/could not be loaded from the gateway/),
      ).toBeNull();
    },
  );

  it("an org with no MCP server, MCP on, is told so, not that MCP is off", async () => {
    answer({
      status: NO_SERVER_STATUS,
      lists: [],
      config: { version: 1, mcp: { enabled: true, servers: {} } },
    });
    renderPage("/dashboard/gateway/mcp");

    expect(await screen.findByText("No servers yet")).toBeInTheDocument();
    expect(screen.queryByText("MCP is off for this organization")).toBeNull();
    expect(screen.queryByRole("button", { name: "Turn on MCP" })).toBeNull();
    expect(screen.queryByText(/did not report MCP as on/)).toBeNull();
    expect(screen.queryByText(/turned off/)).toBeNull();
    expect(screen.getByRole("tablist")).toBeInTheDocument();
    expect(screen.queryByText(OFF_TITLE)).toBeNull();
    expect(errorToasts()).toEqual([]);
  });

  it("MCP turned on shows the tabs and the Enabled banner", async () => {
    answer({ status: ON_STATUS, lists: [] });
    renderPage("/dashboard/gateway/mcp");

    expect(await screen.findByText("MCP on")).toBeInTheDocument();
    expect(screen.getByRole("tablist")).toBeInTheDocument();
    expect(screen.queryByText(OFF_TITLE)).toBeNull();
    expect(
      screen.getByText("Manage MCP servers and saved settings."),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Reload config/ }),
    ).toBeInTheDocument();
  });
});
