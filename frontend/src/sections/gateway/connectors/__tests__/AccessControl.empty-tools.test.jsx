import React from "react";
import { describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import { renderMcp } from "../../mcp/testing/renderMcp";
import AccessControl from "../components/AccessControl";

vi.mock("../../mcp/hooks/useMCPReleaseFeatures", () => ({
  useMCPReleaseFeatures: () => ({ virtualServers: true }),
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));

// Mirrors the native OAuth provider card: no declared tools and no serialized
// defaultPosture. The manifest's ask posture is not a runtime approval policy.
const provider = {
  id: "mcp",
  account_modes: ["agent", "user"],
  credential: { kind: "oauth", mode: "per_user" },
  connect: { served: true, configure_oauth: true },
  tools: [],
};
const declaredTools = [
  { name: "list_records", gateway_posture: "allow", title: "List records" },
  {
    name: "delete_record",
    gateway_posture: "deny",
    reason: "destructive_tool",
  },
];
const policyGuidance =
  "An empty list does not establish read-only access or approval requirements. Review the served tools and set a tool policy on the app's virtual server.";
const inventedRestriction =
  "Read-only tools are allowed. Tools that change or delete data are blocked.";

function toolsRow() {
  return screen.getByText("Tools").parentElement;
}

describe("R3-H6-003 empty tool declarations", () => {
  it.each([
    ["empty", []],
    ["missing", undefined],
  ])("does not invent restrictions for an %s declaration", (_, tools) => {
    renderMcp(
      <AccessControl appName="Fixture" provider={{ ...provider, tools }} />,
    );

    expect(toolsRow()).toHaveTextContent(
      "This provider does not declare its tools in the catalog.",
    );
    expect(toolsRow()).toHaveTextContent(policyGuidance);
    expect(toolsRow()).not.toHaveTextContent(inventedRestriction);
    expect(screen.queryByText(/\d+ of \d+ tools allowed/)).toBeNull();
    expect(
      screen.getByText("Historical / declared posture — not live"),
    ).toBeInTheDocument();
  });

  it("distinguishes an empty historical listing from the current declaration", () => {
    renderMcp(
      <AccessControl
        appName="Fixture"
        provider={{ ...provider, tools: declaredTools }}
        server={{
          server_id: "fixture",
          virtual_server: { slug: "fixture" },
          tools: [],
        }}
      />,
    );

    expect(toolsRow()).toHaveTextContent(
      "No tools were recorded when this app was added.",
    );
    expect(toolsRow()).toHaveTextContent(policyGuidance);
    expect(toolsRow()).not.toHaveTextContent(inventedRestriction);
    expect(toolsRow()).not.toHaveTextContent("does not declare its tools");
    expect(screen.queryByText("list_records")).toBeNull();
  });

  it("preserves explicit allowed and blocked declarations", () => {
    renderMcp(
      <AccessControl
        appName="Fixture"
        provider={{ ...provider, tools: declaredTools }}
      />,
    );

    expect(toolsRow()).toHaveTextContent("1 of 2 tools allowed");
    expect(screen.getByText("list_records")).toBeInTheDocument();
    expect(screen.getByText("delete_record")).toBeInTheDocument();
    expect(screen.getByText("Allowed")).toBeInTheDocument();
    expect(screen.getByText("Blocked")).toBeInTheDocument();
    expect(
      screen.getByText("Blocked: it can delete or overwrite data."),
    ).toBeInTheDocument();
    expect(toolsRow()).not.toHaveTextContent(policyGuidance);
  });

  it("keeps a populated historical listing when the declaration is empty", () => {
    renderMcp(
      <AccessControl
        appName="Fixture"
        provider={provider}
        server={{ server_id: "fixture", tools: declaredTools }}
      />,
    );

    expect(toolsRow()).toHaveTextContent("1 of 2 tools allowed");
    expect(screen.getByText("list_records")).toBeInTheDocument();
    expect(screen.getByText("delete_record")).toBeInTheDocument();
    expect(screen.getByText(/These are the tools Fixture listed/)).toBeInTheDocument();
    expect(toolsRow()).not.toHaveTextContent(policyGuidance);
  });

  it("updates the empty-state guidance when the selected provider changes", () => {
    const { rerender } = renderMcp(
      <AccessControl appName="Fixture" provider={provider} />,
    );
    expect(toolsRow()).toHaveTextContent(policyGuidance);

    rerender(
      <AccessControl
        appName="Fixture"
        provider={{ ...provider, id: "declared", tools: declaredTools }}
      />,
    );
    expect(toolsRow()).toHaveTextContent("1 of 2 tools allowed");
    expect(toolsRow()).not.toHaveTextContent(policyGuidance);
  });
});
