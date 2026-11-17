import React from "react";
import { describe, expect, it, vi } from "vitest";
import { screen, within } from "@testing-library/react";
import { renderMcp } from "../../mcp/testing/renderMcp";
import AccessControl from "../components/AccessControl";

vi.mock("../../mcp/hooks/useMCPReleaseFeatures", () => ({
  useMCPReleaseFeatures: () => ({ virtualServers: true }),
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));

const READD = "Re-add this app to let its write tools run after an approval.";
const STORED_NOTE =
  "These are the tools the app's virtual server names, with the access it stores for each.";

// Acme Docs as the catalog declares it today: create_page runs once an
// Owner or Admin approves the call.
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

// The server card: each tool the app's own virtual server names, with the
// access it stores.
function server(tools) {
  return {
    server_id: "acme-docs",
    credential_mode: "shared",
    virtual_server: { slug: "acme-docs", id: "vs_01M5F7JBJH5XDKQ57KQFMC2NY1" },
    ...(tools && { tools }),
  };
}

const row = (name) => within(screen.getByText(name).closest("li"));
const toolsRow = () => screen.getByText("Tools").parentElement;

describe("each tool's stored access in the connector drawer", () => {
  it("shows a write tool an older virtual server denies as blocked, with the re-add line", () => {
    renderMcp(
      <AccessControl
        appName="Acme Docs"
        provider={provider}
        server={server([
          { name: "archive_page", access: "denied" },
          { name: "create_page", access: "denied" },
          { name: "search_pages", access: "allowed" },
        ])}
      />,
    );

    // The catalog would ask for an approval today; the stored virtual server
    // still denies it, and that is what the gateway enforces.
    expect(row("create_page").getByText("Blocked")).toBeInTheDocument();
    expect(row("create_page").queryByText("Needs approval")).toBeNull();
    expect(row("archive_page").getByText("Blocked")).toBeInTheDocument();
    expect(
      row("archive_page").getByText("Blocked: the catalog denies this tool."),
    ).toBeInTheDocument();
    expect(row("search_pages").getByText("Allowed")).toBeInTheDocument();
    expect(row("search_pages").getByText("Search pages")).toBeInTheDocument();
    expect(toolsRow()).toHaveTextContent("1 of 3 tools allowed");
    expect(screen.getByText(READD)).toBeInTheDocument();
  });

  it("labels a write tool stored under an approval Needs approval, with no re-add line", () => {
    renderMcp(
      <AccessControl
        appName="Acme Docs"
        provider={provider}
        server={server([
          { name: "archive_page", access: "denied" },
          { name: "create_page", access: "approval_required" },
          { name: "search_pages", access: "allowed" },
        ])}
      />,
    );

    expect(row("create_page").getByText("Needs approval")).toBeInTheDocument();
    expect(row("create_page").queryByText("Blocked")).toBeNull();
    expect(row("create_page").getByText("Create page")).toBeInTheDocument();
    expect(row("archive_page").getByText("Blocked")).toBeInTheDocument();
    expect(toolsRow()).toHaveTextContent("2 of 3 tools allowed");
    expect(screen.queryByText(READD)).toBeNull();
    // The access shown is the stored virtual server's, so the drawer does
    // not say a later edit to it is hidden. Nor does it say the app listed
    // these tools when it was added: a per-person sign-in app's virtual
    // server names the tools the catalog declares, with no listing.
    expect(screen.getByText(STORED_NOTE)).toBeInTheDocument();
    expect(
      screen.queryByText(/A later edit to that virtual server is not shown/),
    ).toBeNull();
    expect(screen.queryByText(/listed when it was added/)).toBeNull();
  });

  it("lists the tools listed at add when the provider declares none", () => {
    renderMcp(
      <AccessControl
        appName="Acme Docs"
        provider={{ ...provider, tools: [] }}
        server={server([
          { name: "delete_file", access: "approval_required" },
          { name: "echo", access: "allowed" },
          { name: "get_weather", access: "allowed" },
        ])}
      />,
    );

    expect(row("delete_file").getByText("Needs approval")).toBeInTheDocument();
    expect(row("echo").getByText("Allowed")).toBeInTheDocument();
    expect(row("get_weather").getByText("Allowed")).toBeInTheDocument();
    expect(toolsRow()).toHaveTextContent("3 of 3 tools allowed");
    expect(toolsRow()).not.toHaveTextContent("does not declare its tools");
    expect(screen.queryByText(READD)).toBeNull();
    expect(screen.getByText(STORED_NOTE)).toBeInTheDocument();
    expect(screen.queryByText(/listed when it was added/)).toBeNull();
  });

  it("shows a tool the virtual server excludes as blocked, with no re-add line", () => {
    renderMcp(
      <AccessControl
        appName="Acme Docs"
        provider={provider}
        server={server([
          { name: "archive_page", access: "denied" },
          { name: "create_page", access: "excluded" },
          { name: "search_pages", access: "allowed" },
        ])}
      />,
    );

    // An admin excluded it on the virtual server; re-adding the app would
    // undo that edit, so the drawer does not offer it.
    expect(row("create_page").getByText("Blocked")).toBeInTheDocument();
    expect(row("create_page").queryByText("Needs approval")).toBeNull();
    expect(row("search_pages").getByText("Allowed")).toBeInTheDocument();
    expect(toolsRow()).toHaveTextContent("1 of 3 tools allowed");
    expect(screen.queryByText(READD)).toBeNull();
    expect(screen.getByText(STORED_NOTE)).toBeInTheDocument();
  });

  it("shows the catalog's tools with the later-edit note when the card names none", () => {
    renderMcp(
      <AccessControl
        appName="Acme Docs"
        provider={provider}
        server={server()}
      />,
    );

    // The card could not read the app's virtual server exactly, so the
    // drawer shows the catalog's rules and says a later edit is not shown.
    expect(row("create_page").getByText("Needs approval")).toBeInTheDocument();
    expect(row("search_pages").getByText("Allowed")).toBeInTheDocument();
    expect(row("archive_page").getByText("Blocked")).toBeInTheDocument();
    expect(
      screen.getByText(/A later edit to that virtual server is not shown/),
    ).toBeInTheDocument();
    expect(screen.queryByText(STORED_NOTE)).toBeNull();
    expect(screen.queryByText(READD)).toBeNull();
  });

  it("says the provider does not declare its tools only when the card has none at all", () => {
    renderMcp(
      <AccessControl
        appName="Acme Docs"
        provider={{ ...provider, tools: [] }}
        server={server()}
      />,
    );

    expect(toolsRow()).toHaveTextContent(
      "This provider does not declare its tools in the catalog.",
    );
    expect(screen.queryAllByRole("listitem")).toHaveLength(0);
    expect(screen.queryByText(READD)).toBeNull();
  });
});
