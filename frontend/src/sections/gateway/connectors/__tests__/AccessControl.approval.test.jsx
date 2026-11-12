import React from "react";
import { describe, expect, it, vi } from "vitest";
import { screen, within } from "@testing-library/react";
import { renderMcp } from "../../mcp/testing/renderMcp";
import AccessControl from "../components/AccessControl";

vi.mock("../../mcp/hooks/useMCPReleaseFeatures", () => ({
  useMCPReleaseFeatures: () => ({ virtualServers: true }),
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));

// A write tool of an added app runs after an Owner or Admin approves the call
// and the API sends it as allowed, not denied, with reason
// approval_required.
const provider = {
  id: "acme-mcp",
  account_modes: ["agent"],
  credential: { kind: "api_key", mode: "shared" },
  tools: [
    {
      name: "search_pages",
      title: "Search pages",
      gateway_posture: "allow",
      reason: null,
    },
    {
      name: "create_page",
      title: "Create page",
      gateway_posture: "allow",
      reason: "approval_required",
    },
    {
      name: "archive_page",
      title: "Archive page",
      gateway_posture: "deny",
      reason: "posture_deny",
    },
  ],
};

const row = (name) => within(screen.getByText(name).closest("li"));

describe("the posture label of a tool that needs an approval", () => {
  it("says it needs an approval, not that it is blocked or allowed", () => {
    renderMcp(<AccessControl appName="Acme Docs" provider={provider} />);

    expect(row("create_page").getByText("Needs approval")).toBeInTheDocument();
    expect(row("create_page").queryByText("Blocked")).toBeNull();
    expect(row("create_page").queryByText("Allowed")).toBeNull();
    expect(row("search_pages").getByText("Allowed")).toBeInTheDocument();
    expect(row("archive_page").getByText("Blocked")).toBeInTheDocument();
  });
});
