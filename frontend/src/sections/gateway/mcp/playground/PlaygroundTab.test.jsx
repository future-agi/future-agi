import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, screen, waitFor } from "@testing-library/react";

import { renderMcp } from "../testing/renderMcp";
import PlaygroundTab from "./PlaygroundTab";
import { ADMIN_LEVELS, expectEditable } from "../../testing/adminOnly";

const { testMutate, query, refetch, useTools } = vi.hoisted(() => ({
  testMutate: vi.fn(), query: {}, refetch: vi.fn(), useTools: vi.fn(),
}));
const TOOLS = [
  { name: "crm-tools__find_account", server_id: "crm-tools", upstream_name: "find_account" },
  { name: "support-tools__search_docs", server_id: "support-tools", upstream_name: "search_docs" },
];
vi.mock("../hooks/useMCPConfig", () => ({
  useMCPTools: (...args) => { useTools(...args); return query; },
  useTestMCPTool: () => ({ mutate: testMutate, isPending: false }),
}));

// Running a tool is Owner or Admin only (useGatewayAdmin); these cases act
// as an Admin unless they set another level.
const { org } = vi.hoisted(() => ({ org: { level: 8 } }));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: org.level }),
}));

// A block body: a function returned from beforeEach runs as its teardown.
beforeEach(() => {
  testMutate.mockReset();
  refetch.mockReset();
  useTools.mockClear();
  Object.assign(query, { data: TOOLS, isLoading: false, isError: false, isFetching: false, refetch });
  org.level = 8;
});

describe("PlaygroundTab recovery", () => {
  it("retries failed tools instead of showing an empty state and never executes on retry", () => {
    Object.assign(query, { data: undefined, isError: true });
    const view = renderMcp(<PlaygroundTab gatewayId="gw-1" />);
    expect(screen.queryByText("No tools to run")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Retry tools" }));
    expect(refetch).toHaveBeenCalledOnce();
    expect(testMutate).not.toHaveBeenCalled();
    Object.assign(query, { data: TOOLS, isError: false });
    view.rerender(<PlaygroundTab gatewayId="gw-1" />);
    expect(screen.getByRole("combobox", { name: "Tool" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Retry tools" })).toBeNull();
  });

  it("labels a retained list stale and keeps arguments while retrying", () => {
    const view = renderMcp(<PlaygroundTab gatewayId="gw-1" />, {
      route: "/?tool=crm-tools__find_account",
    });
    fireEvent.change(screen.getByLabelText("Arguments (JSON)"), { target: { value: '{"query":"keep me"}' } });
    query.isError = true;
    view.rerender(<PlaygroundTab gatewayId="gw-1" />);
    expect(screen.getByText(/Tool inventory is stale/)).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Tool" })).toHaveValue("crm-tools__find_account");
    fireEvent.click(screen.getByRole("button", { name: "Retry tools" }));
    expect(screen.getByLabelText("Arguments (JSON)")).toHaveValue('{"query":"keep me"}');
    expect(testMutate).not.toHaveBeenCalled();
  });

  it("does not mistake a stale empty list for a successful empty response", () => {
    Object.assign(query, { data: [], isError: true });
    renderMcp(<PlaygroundTab gatewayId="gw-1" />);
    expect(screen.getByRole("button", { name: "Retry tools" })).toBeEnabled();
    expect(screen.queryByText("No tools to run")).toBeNull();
  });

  it("preserves the successful empty state", () => {
    query.data = [];
    renderMcp(<PlaygroundTab gatewayId="gw-1" />);
    expect(screen.getByText("No tools to run")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Retry tools" })).toBeNull();
  });

  it("keeps runtime-off distinct and issues no retry or tool call", () => {
    Object.assign(query, { data: undefined, isError: true });
    renderMcp(<PlaygroundTab gatewayId="gw-1" runtimeOff />);
    expect(screen.getByText(/Live MCP operations are unavailable/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Retry tools" })).toBeNull();
    expect(screen.queryByText("No tools to run")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Run" }));
    expect(testMutate).not.toHaveBeenCalled();
    expect(useTools).toHaveBeenLastCalledWith("gw-1", { enabled: false });
  });
});

describe("PlaygroundTab", () => {
  it.each(["success", "error"])("attaches a late %s to its submitted tool and arguments", (outcome) => {
    renderMcp(<PlaygroundTab gatewayId="gw-1" />, {
      route: "/?tool=crm-tools__find_account",
    });
    fireEvent.change(screen.getByLabelText("Arguments (JSON)"), {
      target: { value: '{"query":"submitted"}' },
    });
    fireEvent.click(screen.getByRole("button", { name: "Run" }));
    const callbacks = testMutate.mock.calls[0][1];
    fireEvent.change(screen.getByRole("combobox", { name: "Tool" }), {
      target: { value: "support-tools__search_docs" },
    });
    fireEvent.click(screen.getByRole("option", { name: "support-tools__search_docs" }));
    fireEvent.change(screen.getByLabelText("Arguments (JSON)"), {
      target: { value: '{"query":"next request"}' },
    });
    act(() => {
      if (outcome === "success") callbacks.onSuccess({ content: [{ type: "text", text: "Original answer" }] });
      else callbacks.onError(new Error("Original failure"));
    });
    expect(screen.getByRole("combobox", { name: "Tool" })).toHaveValue("support-tools__search_docs");
    expect(screen.getByLabelText("Arguments (JSON)")).toHaveValue('{"query":"next request"}');
    expect(screen.getByText("Submitted tool: crm-tools__find_account")).toBeInTheDocument();
    expect(screen.getByText(/"query": "submitted"/)).toBeInTheDocument();
    expect(screen.getByText(outcome === "success" ? "Original answer" : "Original failure")).toBeInTheDocument();
    expect(testMutate).toHaveBeenCalledOnce();
  });

  it("ignores a callback superseded by another explicit run", () => {
    renderMcp(<PlaygroundTab gatewayId="gw-1" />, {
      route: "/?tool=crm-tools__find_account",
    });
    fireEvent.click(screen.getByRole("button", { name: "Run" }));
    const previous = testMutate.mock.calls[0][1];
    fireEvent.click(screen.getByRole("button", { name: "Run" }));
    const current = testMutate.mock.calls[1][1];
    act(() => current.onSuccess({ content: [{ type: "text", text: "Current answer" }] }));
    act(() => previous.onError(new Error("Superseded failure")));
    expect(screen.getByText("Current answer")).toBeInTheDocument();
    expect(screen.queryByText("Superseded failure")).toBeNull();
  });

  // The API answers a reported failure with an error object
  // ({message, code}); the result shows its text, never "[object Object]".
  it.each([
    ["an error object", { message: "Gateway request failed.", code: "gateway_error" }, "Gateway request failed."],
    ["an error object with only a code", { code: "tool_denied" }, "tool_denied"],
    ["an error string", "tool not found: crm-tools__find_account", "tool not found: crm-tools__find_account"],
  ])("shows %s in a run's answer as its text", (_, error, text) => {
    testMutate.mockImplementation((_vars, { onSuccess }) => onSuccess({ error, is_error: true }));
    renderMcp(<PlaygroundTab gatewayId="gw-1" />, { route: "/?tool=crm-tools__find_account" });
    fireEvent.click(screen.getByRole("button", { name: "Run" }));
    expect(screen.getByRole("alert")).toHaveTextContent(text);
    expect(screen.getByText("Error")).toBeInTheDocument();
    expect(document.body.textContent).not.toContain("[object Object]");
  });

  it("shows a failed request's error object as its text", () => {
    testMutate.mockImplementation((_vars, { onError }) => onError({
      message: "Request failed with status code 404",
      response: { data: { result: { message: "That tool is not on this organization's servers.", code: "mcp_tool_not_found" } } },
    }));
    renderMcp(<PlaygroundTab gatewayId="gw-1" />, { route: "/?tool=crm-tools__find_account" });
    fireEvent.click(screen.getByRole("button", { name: "Run" }));
    expect(screen.getByRole("alert")).toHaveTextContent("That tool is not on this organization's servers.");
    expect(document.body.textContent).not.toContain("[object Object]");
  });

  it("does not promise arguments from a schema the tools API does not send", () => {
    renderMcp(<PlaygroundTab gatewayId="gw-1" />);
    expect(document.body.textContent).not.toMatch(/pre-filled|input schema/i);
    expect(
      screen.getByText(
        /Running a tool requires MCP to be on and an organization Owner or Admin/i,
      ),
    ).toBeInTheDocument();
  });

  it("opens on the tool a link names", () => {
    renderMcp(<PlaygroundTab gatewayId="gw-1" />, {
      route:
        "/dashboard/gateway/mcp?tab=playground&tool=crm-tools__find_account",
    });
    expect(screen.getByRole("combobox", { name: "Tool" })).toHaveValue(
      "crm-tools__find_account",
    );
  });

  it("runs the tool with the typed JSON arguments", async () => {
    renderMcp(<PlaygroundTab gatewayId="gw-1" />, {
      route:
        "/dashboard/gateway/mcp?tab=playground&tool=support-tools__search_docs",
    });
    fireEvent.change(
      screen.getByRole("textbox", { name: "Arguments (JSON)" }),
      {
        target: { value: '{"query": "refund"}' },
      },
    );
    fireEvent.click(screen.getByRole("button", { name: "Run" }));
    await waitFor(() =>
      expect(testMutate.mock.calls[0][0]).toEqual({
        gatewayId: "gw-1",
        name: "support-tools__search_docs",
        arguments: { query: "refund" },
      }),
    );
  });

  it("names the operator guard on the result and says the org's MCP guardrails are not applied", async () => {
    testMutate.mockImplementation((_vars, { onSuccess }) =>
      onSuccess({
        content: [{ type: "text", text: "2 articles" }],
        duration_ms: 42,
        guardrail_pre: "pass",
        guardrail_post: "pass",
      }),
    );
    renderMcp(<PlaygroundTab gatewayId="gw-1" />, {
      route:
        "/dashboard/gateway/mcp?tab=playground&tool=support-tools__search_docs",
    });
    fireEvent.click(screen.getByRole("button", { name: "Run" }));
    expect(
      await screen.findByText("Operator guard, pre: pass"),
    ).toBeInTheDocument();
    expect(screen.getByText("Operator guard, post: pass")).toBeInTheDocument();
    expect(
      screen.getByText(
        /organization's MCP guardrails are not applied on this gateway/i,
      ),
    ).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/Pre-check|Post-check/);
  });

  it("says running needs MCP on and an Owner or Admin, not an agent's call", () => {
    renderMcp(<PlaygroundTab gatewayId="gw-1" />);
    expect(
      screen.getByText(/A run invokes the tool on its server/i),
    ).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/as an agent's call would/i);
    expect(document.body.textContent).not.toMatch(/admin test path/i);
  });

  it("ignores a link to a tool the servers do not list", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    renderMcp(<PlaygroundTab gatewayId="gw-1" />, {
      route: "/dashboard/gateway/mcp?tab=playground&tool=gone__old_tool",
    });
    expect(screen.getByRole("combobox", { name: "Tool" })).toHaveValue("");
    expect(screen.getByRole("button", { name: "Run" })).toBeDisabled();
    const invalid = warn.mock.calls.filter((c) =>
      /is invalid/.test(String(c[0])),
    );
    warn.mockRestore();
    expect(invalid).toHaveLength(0);
  });

  it("refuses arguments that are not JSON, before calling", () => {
    renderMcp(<PlaygroundTab gatewayId="gw-1" />, {
      route:
        "/dashboard/gateway/mcp?tab=playground&tool=support-tools__search_docs",
    });
    fireEvent.change(
      screen.getByRole("textbox", { name: "Arguments (JSON)" }),
      {
        target: { value: "{query: refund" },
      },
    );
    fireEvent.click(screen.getByRole("button", { name: "Run" }));
    expect(screen.getByText(/Invalid JSON/)).toBeInTheDocument();
    expect(testMutate).not.toHaveBeenCalled();
  });

  it.each([
    ["a Member", 3],
    ["a Viewer", 1],
    ["a user whose level is not loaded", null],
  ])(
    "shows Run disabled, saying why, for %s; picking a tool stays open",
    (_, level) => {
      org.level = level;
      renderMcp(<PlaygroundTab gatewayId="gw-1" />, {
        route:
          "/dashboard/gateway/mcp?tab=playground&tool=crm-tools__find_account",
      });
      const run = screen.getByRole("button", { name: "Run" });
      expect(run).toBeDisabled();
      expect(run.closest('[aria-label="Owner or Admin only"]')).not.toBeNull();
      expect(screen.getByRole("combobox")).toBeEnabled();
      expect(testMutate).not.toHaveBeenCalled();
    },
  );

  it.each(ADMIN_LEVELS)("lets %s run a chosen tool", (_, level) => {
    org.level = level;
    renderMcp(<PlaygroundTab gatewayId="gw-1" />, {
      route:
        "/dashboard/gateway/mcp?tab=playground&tool=crm-tools__find_account",
    });
    expectEditable(screen.getByRole("button", { name: "Run" }));
  });
});
