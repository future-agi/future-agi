// The gateway re-reads a server's tools, resources and prompts every
// tools_cache_ttl_seconds, else the older tools_cache_ttl duration ("5m"),
// else 300 seconds (0 is 300 too), besides when the server says they changed.
// The dialog sets it in seconds. update-mcp-server stores the entry the dialog
// sends, so an edit that leaves the TTL alone sends it back exactly as stored,
// and leaves every other stored key as it was.
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen } from "@testing-library/react";

import { renderMcp } from "../testing/renderMcp";
import AddMCPServerDialog from "./AddMCPServerDialog";

const mockMutate = vi.fn();
const { snackbars } = vi.hoisted(() => ({ snackbars: [] }));

vi.mock("notistack", () => ({
  enqueueSnackbar: (message, options) => snackbars.push({ message, options }),
}));
vi.mock("../hooks/useMCPConfig", () => ({
  useUpdateMCPServer: () => ({ mutate: mockMutate, isPending: false }),
}));

const TTL = /Tools cache TTL/;

function open(props = {}) {
  return renderMcp(
    <AddMCPServerDialog
      open
      onClose={vi.fn()}
      gatewayId="default"
      {...props}
    />,
  );
}

function openStored(config) {
  return open({
    editServer: {
      serverId: "crm-tools",
      config: { url: "https://example.test/mcp", transport: "http", ...config },
    },
  });
}

function fillNewServer() {
  fireEvent.change(screen.getByLabelText(/Server ID/), {
    target: { value: "github" },
  });
  fireEvent.change(screen.getByLabelText(/URL/), {
    target: { value: "https://mcp.example.test/mcp" },
  });
}

const save = (name) => fireEvent.click(screen.getByRole("button", { name }));
const saved = () => mockMutate.mock.calls[0][0].config;

beforeEach(() => {
  mockMutate.mockReset();
  snackbars.length = 0;
});

describe("MCP server tools cache TTL", () => {
  it("saves the TTL in seconds as tools_cache_ttl_seconds", () => {
    open();
    fillNewServer();
    fireEvent.change(screen.getByLabelText(TTL), { target: { value: "600" } });
    save("Add server");
    expect(mockMutate).toHaveBeenCalledOnce();
    expect(saved().tools_cache_ttl_seconds).toBe(600);
  });

  it("says empty is the gateway's default, and sends none", () => {
    open();
    expect(screen.getByLabelText(TTL)).toHaveValue("");
    expect(screen.getByText(/Empty or 0 is 300 seconds/)).toBeInTheDocument();
    fillNewServer();
    save("Add server");
    expect(saved()).not.toHaveProperty("tools_cache_ttl_seconds");
  });

  it.each([
    ["seconds", { tools_cache_ttl_seconds: 120 }, "120"],
    ["an older duration", { tools_cache_ttl: "5m" }, "300"],
    ["an older duration in two units", { tools_cache_ttl: "1h30m" }, "5400"],
    ["0, the gateway's default", { tools_cache_ttl_seconds: 0 }, "0"],
  ])("opens a stored server on its TTL saved as %s", (_, ttl, shown) => {
    openStored(ttl);
    expect(screen.getByLabelText(TTL)).toHaveValue(shown);
  });

  it.each([
    ["an older duration", { tools_cache_ttl: "5m" }],
    ["an older duration in two units", { tools_cache_ttl: "1h30m" }],
    ["seconds", { tools_cache_ttl_seconds: 120 }],
    ["0", { tools_cache_ttl_seconds: 0 }],
  ])(
    "sends a TTL saved as %s back as stored when the edit leaves it alone",
    (_, ttl) => {
      openStored(ttl);
      save("Save");
      expect(saved()).toEqual({
        transport: "http",
        url: "https://example.test/mcp",
        ...ttl,
      });
    },
  );

  it("replaces an older duration with seconds when the TTL is changed", () => {
    openStored({ tools_cache_ttl: "5m" });
    fireEvent.change(screen.getByLabelText(TTL), { target: { value: "600" } });
    save("Save");
    expect(saved().tools_cache_ttl_seconds).toBe(600);
    expect(saved()).not.toHaveProperty("tools_cache_ttl");
  });

  it("sends no TTL once the field is emptied, so the gateway's default applies", () => {
    openStored({ tools_cache_ttl: "5m", tools_cache_ttl_seconds: 120 });
    fireEvent.change(screen.getByLabelText(TTL), { target: { value: "" } });
    save("Save");
    expect(saved()).not.toHaveProperty("tools_cache_ttl");
    expect(saved()).not.toHaveProperty("tools_cache_ttl_seconds");
  });

  it.each(["-5", "1.5", "five"])(
    "refuses %s, which is not a whole number of seconds",
    (value) => {
      open();
      fillNewServer();
      fireEvent.change(screen.getByLabelText(TTL), { target: { value } });
      save("Add server");
      expect(mockMutate).not.toHaveBeenCalled();
      expect(snackbars.at(-1)?.message).toMatch(/whole number of seconds/);
    },
  );
});

// Every key of a stored server entry, as update-mcp-server's contract names
// them (agentcc/contracts/gateway_admin.py MCPServerOrgConfig), some under
// their other spelling. The credential stays with the server on the backend;
// the dialog never sends it back.
const EVERY_KEY = {
  url: "https://example.test/mcp",
  transport: "sse",
  era: "2025-06-18",
  trusted_annotations: true,
  timeoutMs: 12000,
  tools_cache_ttl: "10m",
  forwardHeaders: ["x-tenant"],
  propagate_depth: true,
  enabled: false,
  oauth: { adapter: "auto" },
  labels: ["private_data"],
  toolkits: ["github"],
  catalog_ref: "gitlab/pat@1.0.0",
  urlVariables: { host: "gitlab.example.test" },
};

describe("an edit that changes nothing", () => {
  it("keeps every stored key of the server, under the spelling it was stored with", () => {
    openStored({
      ...EVERY_KEY,
      credential: { mode: "shared", credential_id: "c1", state: "active" },
    });
    save("Save");
    expect(saved()).toEqual(EVERY_KEY);
  });
});
