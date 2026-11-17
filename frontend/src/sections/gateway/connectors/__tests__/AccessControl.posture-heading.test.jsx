import React from "react";
import { describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import { renderMcp } from "../../mcp/testing/renderMcp";
import AccessControl from "../components/AccessControl";

vi.mock("../../mcp/hooks/useMCPReleaseFeatures", () => ({
  useMCPReleaseFeatures: () => ({ virtualServers: true }),
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));

// The tool-rules heading opens a part with something under it: tools, or a
// provider whose declaration the drawer explains. An added server whose
// provider the catalog no longer offers, with no tools, has neither.
const POSTURE = "Historical / declared posture — not live";

const accessHeading = () =>
  screen.getByRole("heading", { level: 3, name: "Access control" });

describe("the declared-posture heading", () => {
  it.each([
    ["recorded no tools", { server_id: "orphan", tools: [] }],
    ["sent no tool list", { server_id: "orphan" }],
  ])("is left out for a server with no provider that %s", (_, server) => {
    renderMcp(
      <AccessControl appName="Fixture" provider={null} server={server} />,
    );
    expect(accessHeading()).toBeInTheDocument();
    expect(screen.getByText("Who can connect")).toBeInTheDocument();
    expect(screen.queryByText(POSTURE)).toBeNull();
    expect(screen.queryByText("Tools")).toBeNull();
  });

  it("stays for a server with no provider that lists tools", () => {
    renderMcp(
      <AccessControl
        appName="Fixture"
        provider={null}
        server={{
          server_id: "orphan",
          tools: [{ name: "read_page", gateway_posture: "allow" }],
        }}
      />,
    );
    expect(accessHeading()).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { level: 4, name: POSTURE }),
    ).toBeInTheDocument();
    expect(screen.getByText("1 of 1 tools allowed")).toBeInTheDocument();
  });

  it("stays for a provider that declares no tools", () => {
    renderMcp(
      <AccessControl
        appName="Fixture"
        provider={{ id: "mcp", account_modes: ["agent"], tools: [] }}
      />,
    );
    expect(
      screen.getByRole("heading", { level: 4, name: POSTURE }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/This provider does not declare its tools/),
    ).toBeInTheDocument();
  });
});
