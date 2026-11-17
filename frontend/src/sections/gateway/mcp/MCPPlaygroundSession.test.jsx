import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, screen } from "@testing-library/react";
import { useLocation, useNavigate } from "react-router-dom";

import { renderMcp } from "./testing/renderMcp";
import MCPManagementSection from "./MCPManagementSection";

const state = vi.hoisted(() => ({
  organizationId: "org-1", gatewayId: "gw-1", runtimeOff: false,
  mutate: vi.fn(), useTools: vi.fn(),
}));
const TOOLS = [
  { name: "crm__find", server_id: "crm" },
  { name: "support__search", server_id: "support" },
];
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: 8, currentOrganizationId: state.organizationId }),
}));
vi.mock("../context/useGatewayContext", () => ({
  useGatewayContext: () => ({ gatewayId: state.gatewayId }),
}));
vi.mock("../providers/hooks/useGatewayConfig", async (importOriginal) => ({
  ...(await importOriginal()),
  useGatewayConfig: () => ({ data: { mcp: { enabled: true, servers: { crm: {} } } } }),
  useReloadConfig: () => ({ mutate: vi.fn(), isPending: false }),
}));
vi.mock("./hooks/useMCPConfig", () => ({
  useMCPStatus: () => ({
    data: state.runtimeOff
      ? { enabled: false, reason: "gateway_mcp_disabled" }
      : { enabled: true, features: { tool_call_explorer: true } },
    isLoading: false, isError: false,
  }),
  useMCPTools: (...args) => {
    state.useTools(...args);
    return { data: TOOLS, isLoading: false, isError: false };
  },
  useTestMCPTool: () => ({ mutate: state.mutate, isPending: false }),
}));
vi.mock("./hooks/useMCPConfigPatch", () => ({
  useMCPConfigPatch: () => ({ mutate: vi.fn(), isPending: false }),
}));
vi.mock("./overview/OverviewTab", () => ({ default: () => <div>Overview content</div> }));
vi.mock("./servers/ServersTab", () => ({ default: () => <div>Server content</div> }));
vi.mock("./calls/ToolCallsTab", () => ({ default: () => <div>Call history</div> }));
vi.mock("./calls/SiemExportDrawer", () => ({ default: () => null }));

let location;
function Probe() {
  location = useLocation();
  const navigate = useNavigate();
  return <button type="button" onClick={() => navigate("/?tab=playground&tool=support__search")}>Try linked tool</button>;
}
const page = () => <><MCPManagementSection /><Probe /></>;
const renderPage = () => renderMcp(page(), { route: "/?tab=playground&tool=crm__find" });
const selectTool = (name) => {
  fireEvent.change(screen.getByRole("combobox", { name: "Tool" }), { target: { value: name } });
  fireEvent.click(screen.getByRole("option", { name }));
};
const edit = (value) => fireEvent.change(screen.getByLabelText("Arguments (JSON)"), { target: { value } });
const complete = (callbacks, text = "Saved output") => act(() => callbacks.onSuccess({ content: [{ type: "text", text }] }));

beforeEach(() => {
  state.organizationId = "org-1";
  state.gatewayId = "gw-1";
  state.runtimeOff = false;
  state.mutate.mockReset();
  state.useTools.mockClear();
});

describe("MCP playground session continuity", () => {
  it("keeps the later selection, per-tool draft and completed request across tabs without replay", () => {
    renderPage();
    edit('{"query":"crm draft"}');
    selectTool("support__search");
    expect(screen.getByLabelText("Arguments (JSON)")).toHaveValue("{}");
    edit('{"query":"support draft"}');
    fireEvent.click(screen.getByRole("button", { name: "Run" }));
    complete(state.mutate.mock.calls[0][1]);
    fireEvent.click(screen.getByRole("tab", { name: "Servers" }));
    expect(screen.queryByRole("textbox", { name: "Arguments (JSON)" })).toBeNull();
    expect(state.useTools).toHaveBeenLastCalledWith("gw-1", { enabled: false });
    fireEvent.click(screen.getByRole("tab", { name: "Try a tool" }));
    expect(screen.getByRole("combobox", { name: "Tool" })).toHaveValue("support__search");
    expect(screen.getByLabelText("Arguments (JSON)")).toHaveValue('{"query":"support draft"}');
    expect(screen.getByText("Saved output")).toBeVisible();
    expect(screen.getByText("Submitted tool: support__search")).toBeVisible();
    selectTool("crm__find");
    expect(screen.getByLabelText("Arguments (JSON)")).toHaveValue('{"query":"crm draft"}');
    expect(state.mutate).toHaveBeenCalledOnce();
    expect(location.search).not.toMatch(/draft|Saved|query/);
  });

  it("can complete a pending request while inspecting another tab", () => {
    renderPage();
    edit('{"query":"pending"}');
    fireEvent.click(screen.getByRole("button", { name: "Run" }));
    const callbacks = state.mutate.mock.calls[0][1];
    fireEvent.click(screen.getByRole("tab", { name: "Tool calls" }));
    fireEvent.click(screen.getByRole("tab", { name: "Try a tool" }));
    expect(screen.getByText(/Waiting for this request to finish/)).toBeVisible();
    fireEvent.click(screen.getByRole("tab", { name: "Tool calls" }));
    complete(callbacks, "Completed while away");
    fireEvent.click(screen.getByRole("tab", { name: "Try a tool" }));
    expect(screen.getByText("Completed while away")).toBeVisible();
    expect(screen.queryByText(/Waiting for this request to finish/)).toBeNull();
    expect(state.mutate).toHaveBeenCalledOnce();
  });

  it.each(["organizationId", "gatewayId"])("discards drafts and late results when %s changes", (scope) => {
    const view = renderPage();
    edit('{"query":"old scope"}');
    fireEvent.click(screen.getByRole("button", { name: "Run" }));
    const callbacks = state.mutate.mock.calls[0][1];
    state[scope] = "other-scope";
    view.rerender(page());
    complete(callbacks, "Old scope output");
    expect(screen.getByLabelText("Arguments (JSON)")).toHaveValue("{}");
    expect(screen.queryByText("Old scope output")).toBeNull();
    expect(screen.queryByText(/Submitted tool:/)).toBeNull();
    expect(state.mutate).toHaveBeenCalledOnce();
  });

  it("keeps previous output with execution disabled after runtime turns off", () => {
    const view = renderPage();
    fireEvent.click(screen.getByRole("button", { name: "Run" }));
    complete(state.mutate.mock.calls[0][1]);
    fireEvent.click(screen.getByRole("tab", { name: "Servers" }));
    state.runtimeOff = true;
    view.rerender(page());
    fireEvent.click(screen.getByRole("tab", { name: "Playground" }));
    expect(screen.getByText("Saved output")).toBeVisible();
    expect(screen.getByText("Previous result")).toBeVisible();
    expect(screen.getByRole("button", { name: "Run" })).toBeDisabled();
    expect(state.useTools).toHaveBeenLastCalledWith("gw-1", { enabled: false });
    expect(state.mutate).toHaveBeenCalledOnce();
  });

  it("honors a new explicit tool link without executing or erasing another tool's draft", () => {
    renderPage();
    edit('{"query":"crm draft"}');
    fireEvent.click(screen.getByRole("tab", { name: "Servers" }));
    fireEvent.click(screen.getByRole("button", { name: "Try linked tool" }));
    expect(screen.getByRole("combobox", { name: "Tool" })).toHaveValue("support__search");
    expect(screen.getByLabelText("Arguments (JSON)")).toHaveValue("{}");
    selectTool("crm__find");
    expect(screen.getByLabelText("Arguments (JSON)")).toHaveValue('{"query":"crm draft"}');
    expect(state.mutate).not.toHaveBeenCalled();
  });
});
