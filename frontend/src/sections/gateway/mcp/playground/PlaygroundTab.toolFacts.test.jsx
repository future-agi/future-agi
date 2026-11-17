import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, screen } from "@testing-library/react";

import { renderMcp } from "../testing/renderMcp";
import PlaygroundTab from "./PlaygroundTab";

// Restore (v1.58.0 mcp/MCPPlaygroundTab.jsx): a chosen tool shows its
// description, version and deprecation and its input schema, its arguments
// start from the schema's property types, and a result names the server that
// ran the tool. The tool facts are the gateway's per-organization tool
// inventory's (description, input_schema, version, deprecated,
// deprecation_message, replaced_by).

const { testMutate, query } = vi.hoisted(() => ({
  testMutate: vi.fn(),
  query: {},
}));
const SCHEMA = {
  type: "object",
  properties: {
    query: { type: "string" },
    limit: { type: "integer" },
    score: { type: "number" },
    exact: { type: "boolean" },
    tags: { type: "array" },
    filters: { type: "object" },
    anything: {},
  },
  required: ["query"],
};
const TOOLS = [
  {
    name: "crm-tools__find_account",
    server_id: "crm-tools",
    upstream_name: "find_account",
    description: "Find an account by its name or web domain.",
    input_schema: SCHEMA,
    version: "2.1.0",
    deprecated: true,
    deprecation_message: "Use lookup_account, which also returns contacts.",
    replaced_by: "crm-tools__lookup_account",
  },
  {
    name: "docs__fetch_page",
    server_id: "docs",
    upstream_name: "fetch_page",
    input_schema: '{"type":"object","properties":{"url":{"type":"string"}}}',
  },
  {
    name: "support-tools__search_docs",
    server_id: "support-tools",
    upstream_name: "search_docs",
  },
];
vi.mock("../hooks/useMCPConfig", () => ({
  useMCPTools: () => query,
  useTestMCPTool: () => ({ mutate: testMutate, isPending: false }),
}));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: 8 }),
}));

beforeEach(() => {
  testMutate.mockReset();
  Object.assign(query, {
    data: TOOLS,
    isLoading: false,
    isError: false,
    isFetching: false,
    refetch: vi.fn(),
  });
});

const open = (tool) =>
  renderMcp(<PlaygroundTab gatewayId="gw-1" />, { route: `/?tool=${tool}` });
const args = () => screen.getByLabelText("Arguments (JSON)");
const choose = (tool) => {
  fireEvent.change(screen.getByRole("combobox", { name: "Tool" }), {
    target: { value: tool },
  });
  fireEvent.click(screen.getByRole("option", { name: tool }));
};

describe("PlaygroundTab tool facts (restored from v1.58.0)", () => {
  it("shows a chosen tool's description, version and deprecation", () => {
    open("crm-tools__find_account");
    expect(
      screen.getByText("Find an account by its name or web domain."),
    ).toBeInTheDocument();
    expect(screen.getByText("v2.1.0")).toBeInTheDocument();
    expect(screen.getByText("Deprecated")).toBeInTheDocument();
    expect(
      screen.getByText(
        "Use lookup_account, which also returns contacts. Replaced by crm-tools__lookup_account.",
      ),
    ).toBeInTheDocument();
  });

  it("shows the chosen tool's input schema", () => {
    open("crm-tools__find_account");
    const schema = screen.getByLabelText("Input schema");
    expect(JSON.parse(schema.textContent)).toEqual(SCHEMA);
  });

  it("starts the arguments from the schema's property types", () => {
    open("crm-tools__find_account");
    expect(JSON.parse(args().value)).toEqual({
      query: "",
      limit: 0,
      score: 0,
      exact: false,
      tags: [],
      filters: {},
      anything: null,
    });
  });

  it("reads a schema sent as JSON text", () => {
    open("docs__fetch_page");
    expect(
      JSON.parse(screen.getByLabelText("Input schema").textContent),
    ).toEqual({
      type: "object",
      properties: { url: { type: "string" } },
    });
    expect(JSON.parse(args().value)).toEqual({ url: "" });
  });

  it("keeps a typed draft over the schema's starting arguments", () => {
    open("crm-tools__find_account");
    fireEvent.change(args(), { target: { value: '{"query":"acme.com"}' } });
    choose("support-tools__search_docs");
    choose("crm-tools__find_account");
    expect(args()).toHaveValue('{"query":"acme.com"}');
  });

  it("shows no schema, version or deprecation for a tool its server sends none for", () => {
    open("support-tools__search_docs");
    expect(screen.queryByLabelText("Input schema")).toBeNull();
    expect(screen.queryByText("Deprecated")).toBeNull();
    expect(args()).toHaveValue("{}");
  });

  it("names the server that ran the tool on the result", () => {
    open("crm-tools__find_account");
    fireEvent.click(screen.getByRole("button", { name: "Run" }));
    act(() =>
      testMutate.mock.calls[0][1].onSuccess({
        content: [{ type: "text", text: "Acme Inc." }],
        server: "crm-tools",
        server_id: "crm-tools",
      }),
    );
    expect(screen.getByText("Ran on server crm-tools")).toBeInTheDocument();
  });

  it("runs the tool with its schema's starting arguments when none are typed", () => {
    open("docs__fetch_page");
    fireEvent.click(screen.getByRole("button", { name: "Run" }));
    expect(testMutate.mock.calls[0][0]).toEqual({
      gatewayId: "gw-1",
      name: "docs__fetch_page",
      arguments: { url: "" },
    });
  });
});
