import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { renderMcp } from "./testing/renderMcp";
import { MCP_API } from "./api";
import MCPManagementSection from "./MCPManagementSection";
import { retainMCPRuntimeRestriction } from "./hooks/mcpRuntimeState";

const { state, stub } = vi.hoisted(() => ({
  state: {
    status: {},
    get: vi.fn(),
    post: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
    request: vi.fn(),
    put: vi.fn(),
    head: vi.fn(),
    options: vi.fn(),
  },
  stub: (label) => () => ({ default: () => `${label} body` }),
}));
vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    default: {
      ...actual.default,
      get: state.get,
      post: state.post,
      patch: state.patch,
      delete: state.delete,
      request: state.request,
      put: state.put,
      head: state.head,
      options: state.options,
    },
  };
});
vi.mock("../context/useGatewayContext", () => ({
  useGatewayContext: () => ({ gatewayId: "gw-1" }),
}));
vi.mock("../hooks/useGatewayAdmin", () => ({
  useGatewayAdmin: () => ({ canEdit: true }),
}));
vi.mock("../providers/hooks/useGatewayConfig", () => ({
  useGatewayConfig: () => ({ data: { mcp: { enabled: true } } }),
  useReloadConfig: () => ({ mutate: vi.fn() }),
  SavedButNotAppliedError: Error,
}));
vi.mock("./hooks/useMCPConfig", () => ({ useMCPStatus: () => state.status }));
vi.mock("./hooks/useMCPConfigPatch", () => ({
  useMCPConfigPatch: () => ({ mutate: vi.fn() }),
}));
// Routing-only stand-ins. Actual saved controls, role checks and status hooks
// are exercised independently in MCPManagementSection.siem-off-controls.test.jsx.
vi.mock("./OffInventory", () => ({
  SavedOverview: () => "Saved overview body",
  SavedTools: () => "Saved tools body",
}));
vi.mock("./overview/OverviewTab", stub("Overview"));
vi.mock("./servers/ServersTab", stub("Servers"));
vi.mock("./virtual/VirtualServersTab", stub("Virtual servers"));
vi.mock("./connect/ConnectTab", stub("Connect"));
vi.mock("./calls/ToolCallsTab", stub("Tool calls"));
vi.mock("./credentials/CredentialsTab", stub("Credentials"));
vi.mock("./clients/MCPClientsTab", stub("MCP clients"));
vi.mock("./guardrails/GuardrailsTab", stub("Guardrails"));
vi.mock("./playground/PlaygroundTab", stub("Try a tool"));
vi.mock("./resources/ResourcesTab", stub("Resources"));
vi.mock("./prompts/PromptsTab", stub("Prompts"));
vi.mock("./registry/RegistryTab", stub("Registry"));
vi.mock("./trust/TrustTab", stub("Trust"));
vi.mock("./approvals/ApprovalsTab", stub("Approvals"));

const methods = ["get", "post", "patch", "delete", "request", "put", "head", "options"];
const siemUrls = [
  MCP_API.siemDestinations,
  MCP_API.siemDestination("destination-1"),
  `${MCP_API.siemDestination("destination-1")}rotate-secret/`,
  MCP_API.siemDeliveries,
  MCP_API.siemRetryDelivery("delivery-1"),
  MCP_API.siemDiscardDelivery("delivery-1"),
  MCP_API.siemTestConnection,
];

function status(explorer = false, download = false, siem = true, mcp = true) {
  state.status = {
    data: {
      enabled: mcp,
      features: {
        tool_call_explorer: explorer,
        tool_call_download: download,
        siem_export: siem,
      },
    },
  };
}

function expectNoSiemRequests() {
  for (const method of methods) {
    for (const url of siemUrls) {
      expect(
        state[method].mock.calls.filter(([target]) => target === url),
      ).toEqual([]);
    }
    expect(
      state[method].mock.calls.filter(([target]) =>
        String(target?.url || target).includes("/siem/"),
      ),
    ).toEqual([]);
  }
}

function coreOffStatus(clientOAuth = false) {
  status(true, true, true, false);
  state.status.data.reason = "gateway_mcp_disabled";
  state.status.data.features.client_oauth = clientOAuth;
  state.status.data.features.client_installs = true;
}

// Exact MCP_OFF_TABS contract, not generated from the implementation under test.
const offContents = [
  ["Overview", "Saved overview body"], ["Tools", "Saved tools body"],
  ["Servers", "Servers body"], ["Resources", "Resources body"],
  ["Prompts", "Prompts body"], ["Guardrails", "Guardrails body"],
  ["Playground", "Try a tool body"],
];
function expectCoreOffShell(clientOAuth = false) {
  const contents = [...offContents, ...(clientOAuth ? [["Manage MCP clients", "MCP clients body"]] : [])];
  expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual(contents.map(([label]) => label));
  for (const [label, content] of contents) {
    const tab = screen.getByRole("tab", { name: label, exact: true });
    expect(tab).toBeEnabled();
    fireEvent.click(tab);
    expect(tab).toHaveAttribute("aria-selected", "true");
    expect(screen.getByText(content, { exact: true })).toBeInTheDocument();
  }
  expect(screen.getByRole("button", { name: "Reload config" })).toBeEnabled();
  expect(screen.queryByRole("button", { name: "Turn on MCP" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "SIEM export" })).not.toBeInTheDocument();
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expectNoSiemRequests();
}

beforeEach(() => {
  status();
  for (const method of methods) state[method].mockReset();
  state.get.mockImplementation(async (url) => {
    if (url === MCP_API.siemDestinations) {
      return {
        data: {
          result: {
            export_available: true,
            can_manage: true,
            destinations: [
              {
                id: "destination-1",
                name: "Audit sink",
                kind: "webhook",
                url: "https://collector.example.test/events",
                enabled: true,
                pending_count: 0,
                dead_letter_count: 0,
                streams: ["audit_events"],
              },
            ],
          },
        },
      };
    }
    if (url === MCP_API.siemDeliveries)
      return { data: { result: { deliveries: [] } } };
    throw new Error(`Unexpected GET ${url}`);
  });
  state.patch.mockResolvedValue({ data: { result: {} } });
});
afterEach(cleanup);

const matrix = [false, true].flatMap((explorer) =>
  [false, true].flatMap((download) =>
    [false, true].flatMap((siem) =>
      [false, true].map((mcp) => [explorer, download, siem, mcp]),
    ),
  ),
);

describe("SIEM requires its own capability and MCP ON", () => {
  it.each([false, true])(
    "core-OFF keeps the seven-tab shell and blocks SIEM through retained-OFF refreshes (clients=%s)",
    async (clientOAuth) => {
      coreOffStatus(clientOAuth);
      const view = renderMcp(<MCPManagementSection />, {
        route: "/dashboard/gateway/mcp?tab=calls",
      });
      expectCoreOffShell(clientOAuth);

      // A rejected refresh retains the last answer, not an invented ON status.
      state.status = { ...state.status, isError: true };
      view.rerender(<MCPManagementSection />);
      expectCoreOffShell(false); // capability is not authoritative during error

      const features = state.status.data.features;
      for (const reply of [
        { enabled: true, reason: "gateway_unreachable", features },
        { enabled: true, reason: "unknown", features },
        { features },
      ]) {
        state.status = {
          data: retainMCPRuntimeRestriction(state.status.data, reply),
        };
        view.rerender(<MCPManagementSection />);
        expectCoreOffShell(clientOAuth);
        await waitFor(expectNoSiemRequests);
      }
    },
  );

  it("core-OFF closes SIEM, retained OFF keeps it closed, and authoritative recovery requires an explicit reopen", async () => {
    const view = renderMcp(<MCPManagementSection />);
    fireEvent.click(screen.getByRole("button", { name: "SIEM export" }));
    expect(await screen.findByText("Audit sink")).toBeInTheDocument();
    await waitFor(() =>
      expect(state.get).toHaveBeenCalledWith(MCP_API.siemDeliveries, {
        params: { status: "dead_letter" },
      }),
    );
    for (const method of methods) state[method].mockClear();
    coreOffStatus();
    view.rerender(<MCPManagementSection />);
    expectCoreOffShell();
    state.status.data = retainMCPRuntimeRestriction(state.status.data, {
      enabled: true,
      reason: "unknown",
      features: state.status.data.features,
    });
    view.rerender(<MCPManagementSection />);
    expectCoreOffShell();

    state.status.data = retainMCPRuntimeRestriction(state.status.data, {
      enabled: true,
      features: state.status.data.features,
    });
    view.rerender(<MCPManagementSection />);
    expect(screen.getByRole("button", { name: "SIEM export" })).toBeEnabled();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expectNoSiemRequests();
    fireEvent.click(screen.getByRole("button", { name: "SIEM export" }));
    expect(await screen.findByText("Audit sink")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Turn off Audit sink" }));
    await waitFor(() =>
      expect(state.patch).toHaveBeenCalledWith(
        MCP_API.siemDestination("destination-1"),
        { enabled: false },
      ),
    );
  });

  it.each(matrix)(
    "explorer=%s download=%s SIEM=%s MCP=%s",
    async (explorer, download, siem, mcp) => {
      status(explorer, download, siem, mcp);
      renderMcp(<MCPManagementSection />, {
        route: "/dashboard/gateway/mcp?tab=calls",
      });
      expectNoSiemRequests();
      if (!siem || !mcp) {
        expect(
          screen.queryByRole("button", { name: "SIEM export" }),
        ).not.toBeInTheDocument();
        expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
        expect(
          screen.queryByText(/SIEM export is off/),
        ).not.toBeInTheDocument();
        await waitFor(expectNoSiemRequests);
        return;
      }
      expect(
        screen.getAllByRole("button", { name: "SIEM export" }),
      ).toHaveLength(1);
      fireEvent.click(screen.getByRole("button", { name: "SIEM export" }));
      expect(await screen.findByText("Audit sink")).toBeInTheDocument();
      fireEvent.click(
        screen.getByRole("button", { name: "Turn off Audit sink" }),
      );
      await waitFor(() =>
        expect(state.patch).toHaveBeenCalledWith(
          MCP_API.siemDestination("destination-1"),
          { enabled: false },
        ),
      );
    },
  );

  it.each(["unknown", "gateway_unreachable"])(
    "cold and previously-ON %s with positive flags deny SIEM without making the base shell OFF",
    async (reason) => {
      state.status.data.reason = reason;
      const view = renderMcp(<MCPManagementSection />);
      const expectUnknownShell = () => {
        expect(screen.queryByRole("button", { name: "SIEM export" })).not.toBeInTheDocument();
        expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
        expect(screen.getByRole("button", { name: "Reload config" })).toBeEnabled();
        expect(screen.queryByRole("tab", { name: "Tools", exact: true })).not.toBeInTheDocument();
        for (const name of ["Overview", "Servers", "Guardrails"]) {
          const tab = screen.getByRole("tab", { name, exact: true });
          expect(tab).toBeEnabled();
          fireEvent.click(tab);
          expect(tab).toHaveAttribute("aria-selected", "true");
          expect(screen.getByText(`${name} body`)).toBeInTheDocument();
        }
        expectNoSiemRequests();
      };
      expectUnknownShell();
      for (const recoveredReason of [undefined, null, "gateway_not_org_scoped"]) {
        status();
        state.status.data.reason = recoveredReason;
        view.rerender(<MCPManagementSection />);
        expect(screen.getByRole("button", { name: "SIEM export" })).toBeEnabled();
        expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
        expectNoSiemRequests();
        fireEvent.click(screen.getByRole("button", { name: "SIEM export" }));
        expect(await screen.findByText("Audit sink")).toBeInTheDocument();
        await waitFor(() => expect(state.get).toHaveBeenCalledWith(MCP_API.siemDeliveries, {
          params: { status: "dead_letter" },
        }));
        await waitFor(() => expect(view.client.isFetching({ queryKey: ["agentcc-siem"] })).toBe(0));
        for (const method of methods) state[method].mockClear();
        // No deployment-OFF predecessor: retention cannot mask the admission bug.
        state.status.data = retainMCPRuntimeRestriction(state.status.data, {
          enabled: true, reason, features: state.status.data.features,
        });
        expect(state.status.data.lastKnownDeploymentOff).not.toBe(true);
        view.rerender(<MCPManagementSection />);
        expectUnknownShell();
        await waitFor(expectNoSiemRequests);
      }
    },
  );

  it.each([undefined, null, "true", 1, false])(
    "does not accept MCP enabled value %s",
    (value) => {
      state.status.data.enabled = value;
      renderMcp(<MCPManagementSection />);
      expect(screen.queryByRole("button", { name: "SIEM export" })).not.toBeInTheDocument();
      expectNoSiemRequests();
    },
  );

  it.each([undefined, null, "true", 1, false])(
    "does not accept capability %s",
    (value) => {
      status();
      state.status.data.features.siem_export = value;
      renderMcp(<MCPManagementSection />);
      expect(
        screen.queryByRole("button", { name: "SIEM export" }),
      ).not.toBeInTheDocument();
      expectNoSiemRequests();
    },
  );

  it.each(["isError", "isLoading"])(
    "does not use cached ON status during %s",
    (field) => {
      state.status[field] = true;
      renderMcp(<MCPManagementSection />);
      expect(
        screen.queryByRole("button", { name: "SIEM export" }),
      ).not.toBeInTheDocument();
      expectNoSiemRequests();
    },
  );

  it.each(["siem", "mcp", "malformed", "isError", "isLoading"])(
    "unmounts an open drawer on %s and does not reopen it",
    async (off) => {
      const view = renderMcp(<MCPManagementSection />);
      fireEvent.click(screen.getByRole("button", { name: "SIEM export" }));
      expect(await screen.findByText("Audit sink")).toBeInTheDocument();
      await waitFor(() =>
        expect(state.get).toHaveBeenCalledWith(MCP_API.siemDeliveries, {
          params: { status: "dead_letter" },
        }),
      );
      for (const method of methods) state[method].mockClear();
      status(false, false, off !== "siem", off !== "mcp");
      if (off.startsWith("is")) state.status[off] = true;
      if (off === "malformed") delete state.status.data.enabled;
      view.rerender(<MCPManagementSection />);
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
      expect(
        screen.queryByRole("button", { name: "SIEM export" }),
      ).not.toBeInTheDocument();
      expectNoSiemRequests();
      status();
      view.rerender(<MCPManagementSection />);
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
      expectNoSiemRequests();
    },
  );

  it("lets the drawer finish its ordinary close transition", async () => {
    renderMcp(<MCPManagementSection />);
    fireEvent.click(screen.getByRole("button", { name: "SIEM export" }));
    expect(await screen.findByText("Audit sink")).toBeInTheDocument();
    const drawer = screen.getByRole("dialog");
    fireEvent.click(within(drawer).getByRole("button", { name: "Close" }));
    expect(drawer).toBeInTheDocument();
    await waitFor(() => expect(drawer).not.toBeInTheDocument());
  });

  it("keeps SIEM usable when only the independent explorer turns OFF", async () => {
    status(true, true);
    const view = renderMcp(<MCPManagementSection />);
    fireEvent.click(screen.getByRole("button", { name: "SIEM export" }));
    expect(await screen.findByText("Audit sink")).toBeInTheDocument();
    status(false);
    view.rerender(<MCPManagementSection />);
    expect(screen.getByText("Audit sink")).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: "Turn off Audit sink" }),
    );
    await waitFor(() =>
      expect(state.patch).toHaveBeenCalledWith(
        MCP_API.siemDestination("destination-1"),
        { enabled: false },
      ),
    );
  });
});
