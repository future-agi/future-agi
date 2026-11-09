import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import axios from "src/utils/axios";
import { renderMcp } from "../testing/renderMcp";
import ServedToolsPanel from "./ServedToolsPanel";
import AccessControl from "../../connectors/components/AccessControl";

vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn() },
  endpoints: {
    gateway: {
      mcpServedTools: (id) => `/gateways/${id}/mcp-served-tools/`,
      apiKeys: "/keys/",
      config: (id) => `/gateways/${id}/config/`,
    },
  },
}));
vi.mock("ag-grid-react", async () => {
  const { MockAgGrid } = await import("../testing/agGridMock");
  return { AgGridReact: MockAgGrid };
});
vi.mock("src/hooks/use-ag-theme", () => ({ useAgThemeWith: () => ({}) }));
vi.mock("src/components/iconify", () => ({ default: () => null }));

// Gateway projections of documented tools/list names (retrieved 2026-10-27).
// Neither source documents per-tool toolkit identity; the gateway adds Other,
// not a guessed vendor label. Detailed wire-shape fixtures live in Go tests.
const tools = [
  // https://docs.composio.dev/docs/sessions-via-mcp.md
  "GMAIL_FETCH_EMAILS",
  // https://docs.composio.dev/docs/composio-connect.md
  "COMPOSIO_SEARCH_TOOLS",
  // https://pipedream.com/docs/connect/mcp/tool-modes.md
  "google_sheets-add-single-row",
  "GOOGLE_SHEETS-ADD-SINGLE-ROW",
].map((name) => ({
  name: `partner__${name}`,
  upstream_name: name,
  server_id: "partner",
  identity: { platform: "other", toolkit_slug: "", toolkit_name: "" },
}));
let served;
beforeEach(() => {
  served = tools;
  axios.get.mockReset();
  axios.get.mockImplementation(async (url, options) => {
    if (url === "/keys/")
      return {
        data: {
          result: [
            {
              id: "database-uuid",
              gateway_key_id: "reader",
              name: "Read only",
              status: "active",
            },
          ],
        },
      };
    if (url.endsWith("/config/")) return { data: { result: { mcp: {} } } };
    if (url.endsWith("/mcp-served-tools/")) {
      if (served instanceof Error) throw served;
      if (served === null) return new Promise(() => {});
      return {
        data: { result: options?.params?.key_id ? tools.slice(0, 1) : served },
      };
    }
    throw new Error("Unexpected fixture HTTP path");
  });
});
const panel = () =>
  renderMcp(
    <ServedToolsPanel
      gatewayId="default"
      serverId="partner"
      virtualServer="vs_eng"
    />,
  );

describe("live served tools", () => {
  const configured = (name, platform, slug) => ({
    name: `partner__${name}`,
    upstream_name: name,
    server_id: "partner",
    identity: {
      platform,
      toolkit_slug: slug,
      toolkit_name: slug,
      source: "from server configuration",
    },
  });

  it("labels all tools of a single-app Pipedream connection from server configuration", async () => {
    served = [
      configured("google_sheets-add-single-row", "pipedream", "google_sheets"),
      configured("resend-retrieve-email", "pipedream", "google_sheets"),
    ];
    panel();
    expect(
      await screen.findByRole("heading", { name: "google_sheets (2)" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText("pipedream — from server configuration"),
    ).toBeInTheDocument();
    const user = userEvent.setup();
    await user.click(screen.getByRole("combobox", { name: "Group" }));
    await user.click(
      within(screen.getByRole("listbox")).getByText(
        "google_sheets — from server configuration",
      ),
    );
    expect(
      screen.getByText("partner__resend-retrieve-email"),
    ).toBeInTheDocument();
  });

  it("displays configured Composio hints without merging ambiguous prefixes or meta tools", async () => {
    served = [
      configured("SHARE_POINT_SEARCH_QUERY", "composio", "share_point"),
      configured(
        "COMPOSIO_SEARCH_DUCK_DUCK_GO_SEARCH",
        "composio",
        "composio_search",
      ),
      tools[1],
    ];
    panel();
    expect(
      await screen.findByRole("heading", { name: "share_point (1)" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "composio_search (1)" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Other (1)" }),
    ).toBeInTheDocument();
    expect(
      screen.getAllByText("composio — from server configuration"),
    ).toHaveLength(2);
  });

  it.each(["multi-app", "missing-slug", "no-config"])(
    "keeps the server's %s Other projection without guessing from names",
    async () => {
      panel();
      expect(
        await screen.findByRole("heading", { name: "Other (4)" }),
      ).toBeInTheDocument();
      expect(
        screen.queryByText(/— from server configuration/),
      ).not.toBeInTheDocument();
    },
  );

  it("does not trust a vendor identity without configuration provenance", async () => {
    const unlabelled = configured("GMAIL_FETCH_EMAILS", "composio", "gmail");
    delete unlabelled.identity.source;
    served = [unlabelled];
    panel();
    expect(
      await screen.findByRole("heading", { name: "Other (1)" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { name: "gmail (1)" }),
    ).not.toBeInTheDocument();
  });
  it("keeps documented vendor tools in Other without inferring identity from names", async () => {
    panel();
    expect(await screen.findByText("Served tools (4)")).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Other (4)" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByText(/GitHub \(|Google Sheets \(/),
    ).not.toBeInTheDocument();
    expect(
      screen.getByText(
        "No unambiguous group from server configuration; tool names alone do not establish identity.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText("partner__COMPOSIO_SEARCH_TOOLS"),
    ).toBeInTheDocument();
    expect(axios.get).toHaveBeenCalledWith(
      "/gateways/default/mcp-served-tools/",
      expect.objectContaining({
        params: { vs: "vs_eng", key_id: "", server_id: "partner" },
      }),
    );
  });
  it("searches original tool names and filters the honest Other group", async () => {
    const user = userEvent.setup();
    panel();
    await screen.findByText("Served tools (4)");
    await user.type(
      screen.getByPlaceholderText("Search served tools"),
      "google_sheets",
    );
    expect(
      screen.getByText("partner__google_sheets-add-single-row"),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("partner__GMAIL_FETCH_EMAILS"),
    ).not.toBeInTheDocument();
    await user.clear(screen.getByPlaceholderText("Search served tools"));
    await user.click(screen.getByRole("combobox", { name: "Group" }));
    await user.click(within(screen.getByRole("listbox")).getByText("Other"));
    expect(
      screen.getByText("partner__COMPOSIO_SEARCH_TOOLS"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("partner__google_sheets-add-single-row"),
    ).toBeInTheDocument();
  });
  it("reads again as the selected key without showing the previous broader list", async () => {
    const user = userEvent.setup();
    panel();
    await screen.findByText("Served tools (4)");
    await user.click(screen.getByRole("combobox", { name: "As seen by key" }));
    await user.click(
      within(screen.getByRole("listbox")).getByText("Read only"),
    );
    await screen.findByText("Served tools (1)");
    expect(
      screen.queryByText("partner__google_sheets-add-single-row"),
    ).not.toBeInTheDocument();
    expect(axios.get).toHaveBeenCalledWith(
      "/gateways/default/mcp-served-tools/",
      expect.objectContaining({
        params: { vs: "vs_eng", key_id: "reader", server_id: "partner" },
      }),
    );
  });
  it.each([
    [null, "Loading served tools"],
    [new Error("offline"), "Served tools could not be loaded"],
    [[], "No tools served"],
  ])("shows an honest pending, error or empty state", async (value, label) => {
    served = value;
    panel();
    expect(await screen.findByText(label)).toBeInTheDocument();
    expect(screen.queryByText("Served tools (4)")).not.toBeInTheDocument();
  });
  it("keeps historical catalogue posture explicitly separate from live tools", async () => {
    renderMcp(
      <AccessControl
        appName="Fixture"
        gatewayId="default"
        canManage
        server={{
          server_id: "partner",
          virtual_server: { id: "vs_eng", slug: "eng" },
          tools: [{ name: "old_deleted", gateway_posture: "allow" }],
        }}
        provider={{}}
      />,
    );
    expect(await screen.findByText("Served tools (4)")).toBeInTheDocument();
    expect(
      screen.getByText("Historical / declared posture — not live"),
    ).toBeInTheDocument();
    expect(screen.getByText("old_deleted")).toBeInTheDocument();
    await waitFor(() => expect(axios.get).toHaveBeenCalled());
  });
});
