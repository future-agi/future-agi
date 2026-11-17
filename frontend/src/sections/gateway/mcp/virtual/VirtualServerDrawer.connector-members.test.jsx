import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";

import { renderMcp } from "../testing/renderMcp";
import VirtualServersTab from "./VirtualServersTab";

vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: 8 }),
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

const snackbars = vi.hoisted(() => []);
vi.mock("notistack", () => ({
  enqueueSnackbar: (message, options) => snackbars.push({ message, options }),
}));

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    default: { ...actual.default, get: api.get, post: api.post },
  };
});

const UPDATE_URL = "/agentcc/gateways/gw-1/update-config/";

// acme-docs is a connector with its own virtual server; camel-notes carries
// its catalog record under the other spelling only; null-first carries a
// null record first, so it is a plain server; wiki is a plain server. "Team"
// was saved with acme-docs, bare and with an account, before connectors
// were kept to their own virtual server.
const MCP = {
  enabled: true,
  access: { default: "deny" },
  servers: {
    "acme-docs": {
      url: "https://mcp.acme-docs.example.com/mcp",
      transport: "http",
      catalog_ref: "acme-docs/acme-mcp@1.2.0",
    },
    "camel-notes": {
      url: "https://mcp.camel-notes.example.com/mcp",
      transport: "http",
      catalogRef: "camel-notes/camel-mcp@1.0.0",
    },
    "null-first": {
      url: "https://mcp.null-first.example.com/mcp",
      transport: "http",
      catalog_ref: null,
      catalogRef: "null-first/null-mcp@1.0.0",
    },
    wiki: { url: "https://wiki.example.com/mcp", transport: "http" },
  },
  virtual_servers: {
    "acme-docs": {
      id: "vs_01JBCDEFGHJKMNPQRSTVWXYZ01",
      title: "Acme Docs",
      members: ["acme-docs", "acme-docs@work"],
    },
    team: {
      id: "vs_01JBCDEFGHJKMNPQRSTVWXYZ02",
      title: "Team",
      members: ["wiki", "acme-docs", "acme-docs@work"],
    },
  },
};

const STATUS_ON = {
  enabled: true,
  tools: 0,
  servers: [],
  features: { virtual_servers: true },
};

const ONLY_ITS_OWN = (server) =>
  `Connector: only its own virtual server (${server}) can include it.`;
const UNCHECK_IT = (server) =>
  `Only ${server}'s own virtual server should include it. Uncheck it to remove it.`;

beforeEach(() => {
  snackbars.length = 0;
  api.get.mockReset();
  api.post.mockReset();
  api.get.mockImplementation(async (url) => {
    if (url === "/agentcc/gateways/gw-1/config/") {
      return { data: { result: { mcp: MCP } } };
    }
    if (url === "/agentcc/gateways/gw-1/mcp-tools/") {
      return { data: { result: [] } };
    }
    if (url === "/agentcc/gateways/gw-1/mcp-status/") {
      return { data: { result: STATUS_ON } };
    }
    if (url === "/agentcc/api-keys/") return { data: { result: [] } };
    throw new Error(`unexpected GET ${url}`);
  });
  api.post.mockResolvedValue({
    data: {
      status: true,
      result: { version: 9, gateway_synced: true, warnings: [] },
    },
  });
});

async function openRow(title) {
  renderMcp(<VirtualServersTab gatewayId="gw-1" />);
  const cell = await screen.findByText(title, {
    selector: '[role="gridcell"]',
  });
  fireEvent.click(cell.closest('[role="row"]'));
  return screen.findByRole("dialog");
}

async function openNew() {
  renderMcp(<VirtualServersTab gatewayId="gw-1" />);
  fireEvent.click(
    await screen.findByRole("button", { name: "New virtual server" }),
  );
  return screen.findByRole("dialog");
}

const member = (drawer, name) => within(drawer).getByRole("checkbox", { name });

describe("a connector's server in the virtual server member picker", () => {
  it("cannot be chosen for another virtual server, under either spelling of its record", async () => {
    const drawer = await openNew();

    for (const server of ["acme-docs", "camel-notes"]) {
      expect(member(drawer, server)).toBeDisabled();
      expect(member(drawer, server)).not.toBeChecked();
      expect(member(drawer, server)).toHaveAccessibleDescription(
        ONLY_ITS_OWN(server),
      );
    }
    // A null record first is no record: a plain server stays selectable.
    for (const server of ["null-first", "wiki"]) {
      expect(member(drawer, server)).toBeEnabled();
      expect(member(drawer, server)).not.toHaveAccessibleDescription();
    }
    expect(within(drawer).queryByText(ONLY_ITS_OWN("null-first"))).toBeNull();
  });

  it("stays a choice in its own virtual server, bare and with an account", async () => {
    const drawer = await openRow("Acme Docs");

    for (const name of ["acme-docs", "acme-docs@work"]) {
      expect(member(drawer, name)).toBeEnabled();
      expect(member(drawer, name)).toBeChecked();
      expect(member(drawer, name)).not.toHaveAccessibleDescription();
    }
    expect(within(drawer).queryByText(ONLY_ITS_OWN("acme-docs"))).toBeNull();
    expect(within(drawer).queryByText(UNCHECK_IT("acme-docs"))).toBeNull();
    // Another connector's server is still not a choice here.
    expect(member(drawer, "camel-notes")).toBeDisabled();
    expect(member(drawer, "camel-notes")).toHaveAccessibleDescription(
      ONLY_ITS_OWN("camel-notes"),
    );
  });

  it("is kept where it was saved before, can only be unchecked, and is then out", async () => {
    const drawer = await openRow("Team");

    for (const name of ["acme-docs", "acme-docs@work"]) {
      expect(member(drawer, name)).toBeChecked();
      expect(member(drawer, name)).toBeEnabled();
      expect(member(drawer, name)).toHaveAccessibleDescription(
        UNCHECK_IT("acme-docs"),
      );
      fireEvent.click(member(drawer, name));
      expect(member(drawer, name)).not.toBeChecked();
      expect(member(drawer, name)).toBeDisabled();
      expect(member(drawer, name)).toHaveAccessibleDescription(
        ONLY_ITS_OWN("acme-docs"),
      );
    }
    expect(member(drawer, "wiki")).toBeChecked();

    const save = within(drawer).getByRole("button", { name: "Save" });
    await waitFor(() => expect(save).toBeEnabled());
    fireEvent.click(save);
    await waitFor(() => expect(api.post).toHaveBeenCalled());
    const [url, body] = api.post.mock.calls[0];
    expect(url).toBe(UPDATE_URL);
    expect(body.mcp.virtual_servers.team.members).toEqual(["wiki"]);
    await waitFor(() =>
      expect(snackbars).toContainEqual({
        message: "Virtual server saved",
        options: { variant: "success" },
      }),
    );
  });
});
