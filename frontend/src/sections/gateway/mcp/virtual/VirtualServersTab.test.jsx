import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";

import { renderMcp, DEMO_MCP } from "../testing/renderMcp";
import VirtualServersTab from "./VirtualServersTab";

const org = vi.hoisted(() => ({ level: 8 }));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: org.level }),
}));

vi.mock("ag-grid-react", async () => {
  const { MockAgGrid } = await import("../testing/agGridMock");
  return { AgGridReact: MockAgGrid };
});

vi.mock("../../context/useGatewayContext", () => ({
  useGatewayContext: () => ({
    gatewayId: "gw-1",
    gateway: { id: "gw-1", baseUrl: "http://localhost:28490/v1" },
  }),
}));

// Every snackbar the drawer raises. A write test ends only once its success
// snackbar has landed: the mutation's tail must not run after the test
// environment is torn down.
const snackbars = vi.hoisted(() => []);
vi.mock("notistack", () => ({
  enqueueSnackbar: (message, options) => snackbars.push({ message, options }),
}));

const api = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  mcp: null,
  status: null,
  tools: null,
}));
vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    default: { ...actual.default, get: api.get, post: api.post },
  };
});

const UPDATE_URL = "/agentcc/gateways/gw-1/update-config/";

const TOOLS = [
  {
    name: "crm-tools__find_account",
    server_id: "crm-tools",
    upstream_name: "find_account",
  },
  {
    name: "support-tools__create_ticket",
    server_id: "support-tools",
    upstream_name: "create_ticket",
  },
];

// The gateway's answer for the demo org (agentcc/gateways/<id>/mcp-status/).
const STATUS_ON = {
  enabled: true,
  tools: 2,
  servers: [
    { server_id: "crm-tools", healthy: true, tool_count: 1 },
    { server_id: "support-tools", healthy: true, tool_count: 1 },
  ],
};

// The demo org's one virtual server's Status cell.
function statusCell() {
  return screen.getByRole("row").querySelector('[data-col="status"]');
}

function succeeded(message) {
  return waitFor(() =>
    expect(snackbars).toContainEqual({
      message,
      options: { variant: "success" },
    }),
  );
}

beforeEach(() => {
  org.level = 8;
  snackbars.length = 0;
  api.mcp = DEMO_MCP;
  api.status = STATUS_ON;
  api.tools = TOOLS;
  api.get.mockReset();
  api.post.mockReset();
  api.get.mockImplementation(async (url) => {
    if (url === "/agentcc/gateways/gw-1/config/") {
      return { data: { result: { mcp: api.mcp } } };
    }
    if (url === "/agentcc/gateways/gw-1/mcp-tools/") {
      return { data: { result: api.tools } };
    }
    if (url === "/agentcc/gateways/gw-1/mcp-status/") {
      if (api.status instanceof Error) throw api.status;
      return { data: { result: api.status } };
    }
    if (url === "/agentcc/api-keys/") {
      return {
        data: {
          result: [{ gateway_key_id: "key_5bed", name: "support-bot" }],
        },
      };
    }
    throw new Error(`unexpected GET ${url}`);
  });
  api.post.mockResolvedValue({
    data: {
      status: true,
      result: { version: 28, gateway_synced: true, warnings: [] },
    },
  });
});

describe("VirtualServersTab", () => {
  it("lists the org's virtual servers with their endpoint", async () => {
    renderMcp(<VirtualServersTab gatewayId="gw-1" />);
    const row = await screen.findByRole("row");
    expect(within(row).getByText("Support desk")).toBeInTheDocument();
    expect(
      within(row).getByText("support-tools, crm-tools"),
    ).toBeInTheDocument();
    expect(within(row).getByText("2")).toBeInTheDocument();
    expect(
      within(row).getByText("http://localhost:28490/mcp/v/support-desk"),
    ).toBeInTheDocument();
    await waitFor(() => expect(statusCell()).toHaveTextContent("MCP on"));
  });

  it("does not say serving while the gateway has MCP turned off", async () => {
    api.status = { enabled: false, reason: "gateway_mcp_disabled" };
    renderMcp(<VirtualServersTab gatewayId="gw-1" />);
    await waitFor(() => expect(statusCell()).toHaveTextContent("MCP off"));
    expect(screen.queryByText("Serving")).toBeNull();
  });

  it("does not say serving while MCP is off for the organization", async () => {
    api.mcp = { ...DEMO_MCP, enabled: false };
    api.status = { enabled: false };
    renderMcp(<VirtualServersTab gatewayId="gw-1" />);
    await waitFor(() => expect(statusCell()).toHaveTextContent("MCP off"));
    expect(screen.queryByText("Serving")).toBeNull();
  });

  it("says a virtual server with no served tool has no tools", async () => {
    api.tools = [];
    renderMcp(<VirtualServersTab gatewayId="gw-1" />);
    await waitFor(() =>
      expect(statusCell()).toHaveTextContent("No candidates"),
    );
    expect(screen.queryByText("Serving")).toBeNull();
  });

  it("says the status is unknown when the gateway's status does not load", async () => {
    api.status = new Error("status down");
    renderMcp(<VirtualServersTab gatewayId="gw-1" />);
    await waitFor(() =>
      expect(statusCell()).toHaveTextContent("Status unknown"),
    );
    expect(screen.queryByText("Serving")).toBeNull();
  });

  it("does not mislabel the accepted approval kernel and starts limits off", async () => {
    renderMcp(<VirtualServersTab gatewayId="gw-1" />);
    fireEvent.click(await screen.findByRole("row"));
    const drawer = await screen.findByRole("dialog");
    expect(
      within(drawer).queryByText("Not enforced on this gateway"),
    ).toBeNull();
    expect(within(drawer).getByText("Limits off")).toBeInTheDocument();
    expect(
      within(drawer).queryByRole("checkbox", { name: /approval/i }),
    ).toBeNull();
    // The slug of a stored virtual server cannot change.
    expect(
      within(drawer).getByRole("textbox", { name: "Slug" }),
    ).toBeDisabled();
  });

  it("creates a virtual server as a config patch", async () => {
    renderMcp(<VirtualServersTab gatewayId="gw-1" />);
    fireEvent.click(
      await screen.findByRole("button", { name: "New virtual server" }),
    );
    const drawer = await screen.findByRole("dialog");

    fireEvent.change(within(drawer).getByRole("textbox", { name: "Title" }), {
      target: { value: "Billing desk" },
    });
    expect(within(drawer).getByRole("textbox", { name: "Slug" })).toHaveValue(
      "billing-desk",
    );
    fireEvent.click(
      within(drawer).getByRole("checkbox", { name: "crm-tools" }),
    );
    // The preview identifies upstream candidates, not composed exposed names.
    expect(
      within(drawer).getByText("crm-tools/find_account"),
    ).toBeInTheDocument();

    fireEvent.click(within(drawer).getByRole("button", { name: "Create" }));
    await waitFor(() => expect(api.post).toHaveBeenCalled());
    const [url, body] = api.post.mock.calls[0];
    expect(url).toBe(UPDATE_URL);
    expect(body).toEqual({
      mcp: {
        virtual_servers: {
          "billing-desk": {
            title: "Billing desk",
            description: null,
            members: ["crm-tools"],
            naming: "prefixed",
            tools: null,
            access: null,
          },
        },
      },
    });
    await succeeded("Virtual server created");
  });

  it("refuses a slug the API would refuse, before sending anything", async () => {
    renderMcp(<VirtualServersTab gatewayId="gw-1" />);
    fireEvent.click(
      await screen.findByRole("button", { name: "New virtual server" }),
    );
    const drawer = await screen.findByRole("dialog");
    fireEvent.change(within(drawer).getByRole("textbox", { name: "Slug" }), {
      target: { value: "Support Desk" },
    });
    expect(within(drawer).getByText(/lowercase/)).toBeInTheDocument();
    expect(
      within(drawer).getByRole("button", { name: "Create" }),
    ).toBeDisabled();
  });

  it("edits a stored virtual server without sending its id or approval", async () => {
    renderMcp(<VirtualServersTab gatewayId="gw-1" />);
    fireEvent.click(await screen.findByRole("row"));
    const drawer = await screen.findByRole("dialog");
    fireEvent.change(within(drawer).getByRole("textbox", { name: "Title" }), {
      target: { value: "Support desk (EU)" },
    });
    fireEvent.click(within(drawer).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.post).toHaveBeenCalled());
    const entry = api.post.mock.calls[0][1].mcp.virtual_servers["support-desk"];
    expect(entry.title).toBe("Support desk (EU)");
    expect(entry).not.toHaveProperty("id");
    expect(entry).not.toHaveProperty("approval");
    await succeeded("Virtual server saved");
  });

  it("saves explicit count and rate limits through the org config API", async () => {
    renderMcp(<VirtualServersTab gatewayId="gw-1" />);
    fireEvent.click(await screen.findByRole("row"));
    const drawer = await screen.findByRole("dialog");
    fireEvent.change(
      within(drawer).getByLabelText("Calls per minute per key"),
      { target: { value: "7" } },
    );
    fireEvent.change(within(drawer).getByLabelText("Calls per run"), {
      target: { value: "0" },
    });
    fireEvent.click(within(drawer).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.post).toHaveBeenCalled());
    expect(
      api.post.mock.calls[0][1].mcp.virtual_servers["support-desk"].budgets,
    ).toEqual({ rpm_per_key: 7, max_tool_calls_per_run: 0, per_tool: {} });
  });

  // A change Django stored but the gateway did not apply warns in the words
  // every other gateway config write uses, never as a success.
  it("warns that a saved change was not applied by the gateway", async () => {
    api.post.mockResolvedValue({
      data: {
        status: true,
        result: {
          version: 29,
          gateway_synced: false,
          gateway_warning: "config push timed out",
        },
      },
    });
    renderMcp(<VirtualServersTab gatewayId="gw-1" />);
    fireEvent.click(await screen.findByRole("row"));
    const drawer = await screen.findByRole("dialog");
    fireEvent.click(within(drawer).getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(snackbars).toContainEqual({
        message:
          "Saved, but the gateway did not apply it: config push timed out",
        options: { variant: "warning" },
      }),
    );
    expect(snackbars.map((s) => s.options?.variant)).not.toContain("success");
  });

  it("deletes a virtual server after confirming", async () => {
    renderMcp(<VirtualServersTab gatewayId="gw-1" />);
    fireEvent.click(await screen.findByRole("row"));
    const drawer = await screen.findByRole("dialog");
    fireEvent.click(within(drawer).getByRole("button", { name: "Delete" }));
    fireEvent.click(
      await screen.findByRole("button", { name: "Delete virtual server" }),
    );
    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith(UPDATE_URL, {
        mcp: { virtual_servers: { "support-desk": null } },
      }),
    );
    await succeeded("Virtual server deleted");
  });

  it("shows the API's reason when a save is refused", async () => {
    api.post.mockRejectedValueOnce({
      response: {
        status: 400,
        data: {
          status: false,
          result: "mcp.virtual_servers.x.members: unknown server",
        },
      },
    });
    renderMcp(<VirtualServersTab gatewayId="gw-1" />);
    fireEvent.click(await screen.findByRole("row"));
    const drawer = await screen.findByRole("dialog");
    fireEvent.click(within(drawer).getByRole("button", { name: "Save" }));
    expect(
      await within(drawer).findByText(
        "mcp.virtual_servers.x.members: unknown server",
      ),
    ).toBeInTheDocument();
  });

  it("offers to create the first virtual server when there is none", async () => {
    api.mcp = { ...DEMO_MCP, virtual_servers: {} };
    renderMcp(<VirtualServersTab gatewayId="gw-1" />);
    expect(
      await screen.findByText("No virtual servers yet"),
    ).toBeInTheDocument();
    expect(
      screen.getAllByRole("button", { name: "New virtual server" }).length,
    ).toBeGreaterThan(0);
  });
});

describe("virtual server audit regressions", () => {
  it("warns when deletion persisted but the gateway did not apply it", async () => {
    api.post.mockImplementation(async () => {
      api.mcp = { ...DEMO_MCP, virtual_servers: {} };
      return {
        data: {
          result: {
            gateway_synced: false,
            gateway_warning: "config push timed out",
          },
        },
      };
    });
    renderMcp(<VirtualServersTab gatewayId="gw-1" />);
    fireEvent.click(await screen.findByRole("row"));
    fireEvent.click(await screen.findByRole("button", { name: "Delete" }));
    fireEvent.click(
      await screen.findByRole("button", { name: "Delete virtual server" }),
    );
    await waitFor(() => expect(snackbars).toHaveLength(1));
    expect(snackbars[0].options.variant).toBe("warning");
    expect(snackbars[0].message).toContain("config push timed out");
    expect(api.post).toHaveBeenCalledWith(UPDATE_URL, {
      mcp: { virtual_servers: { "support-desk": null } },
    });
    expect(
      await screen.findByText("No virtual servers yet"),
    ).toBeInTheDocument();
    expect(
      api.get.mock.calls.filter(([url]) => url.endsWith("/config/")).length,
    ).toBeGreaterThan(1);
  });

  it.each([1, 3, null])(
    "keeps role %s read-only with an explanation",
    async (level) => {
      org.level = level;
      renderMcp(<VirtualServersTab gatewayId="gw-1" />);
      await screen.findByRole("row");
      expect(
        screen.getByRole("button", { name: "New virtual server" }),
      ).toBeDisabled();
      fireEvent.click(screen.getByRole("row"));
      const drawer = await screen.findByRole("dialog");
      expect(
        within(drawer).getByRole("button", { name: "Save" }),
      ).toBeDisabled();
      expect(
        within(drawer).getByRole("button", { name: "Delete" }),
      ).toBeDisabled();
      expect(
        within(drawer).getByText("Owner or Admin only"),
      ).toBeInTheDocument();
      expect(api.post).not.toHaveBeenCalled();
    },
  );

  it("keeps first-server creation visible but disabled for a Viewer", async () => {
    org.level = 1;
    api.mcp = { ...DEMO_MCP, virtual_servers: {} };
    renderMcp(<VirtualServersTab gatewayId="gw-1" />);
    await screen.findByText("No virtual servers yet");
    const button = screen.getByRole("button", { name: "New virtual server" });
    expect(button).toBeDisabled();
    fireEvent.click(button);
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(api.post).not.toHaveBeenCalled();
    expect(screen.getByText("Owner or Admin only")).toBeInTheDocument();
  });

  it("shows colliding flat tools as upstream candidates, not two exposed tools", async () => {
    api.mcp = structuredClone(DEMO_MCP);
    api.mcp.virtual_servers["support-desk"].naming = "flat";
    api.tools = ["crm-tools", "support-tools"].map((server) => ({
      server_id: server,
      upstream_name: "search",
      name: `${server}__search`,
    }));
    renderMcp(<VirtualServersTab gatewayId="gw-1" />);
    fireEvent.click(await screen.findByRole("row"));
    const drawer = await screen.findByRole("dialog");
    expect(within(drawer).getByText("2 tool candidates")).toBeInTheDocument();
    expect(within(drawer).getByText("crm-tools/search")).toBeInTheDocument();
    expect(
      within(drawer).getByText("support-tools/search"),
    ).toBeInTheDocument();
    expect(
      within(drawer).getByText(/not the served tool set/i),
    ).toBeInTheDocument();
    expect(within(drawer).queryByText(/Exposes/)).toBeNull();
  });
});
