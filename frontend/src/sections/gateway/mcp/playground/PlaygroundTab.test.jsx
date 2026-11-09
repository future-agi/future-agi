import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";

import { renderMcp } from "../testing/renderMcp";
import PlaygroundTab from "./PlaygroundTab";
import { ADMIN_LEVELS, expectEditable } from "../../testing/adminOnly";

const { testMutate } = vi.hoisted(() => ({ testMutate: vi.fn() }));
vi.mock("../hooks/useMCPConfig", () => ({
  useMCPTools: () => ({
    data: [
      {
        name: "crm-tools__find_account",
        server_id: "crm-tools",
        upstream_name: "find_account",
      },
      {
        name: "support-tools__search_docs",
        server_id: "support-tools",
        upstream_name: "search_docs",
      },
    ],
    isLoading: false,
  }),
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
  org.level = 8;
});

describe("PlaygroundTab", () => {
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
