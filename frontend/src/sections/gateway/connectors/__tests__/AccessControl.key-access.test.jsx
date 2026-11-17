import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, within } from "@testing-library/react";
import { renderMcp } from "../../mcp/testing/renderMcp";
import AccessControl from "../components/AccessControl";

const release = vi.hoisted(() => ({ features: null }));
vi.mock("../../mcp/hooks/useMCPReleaseFeatures", () => ({
  useMCPReleaseFeatures: () => release.features,
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));

const ON = {
  virtualServers: true,
  shown: { virtualServers: true },
  off: { virtualServers: false },
};
const OFF = {
  virtualServers: false,
  shown: { virtualServers: false },
  off: { virtualServers: true },
};

// Once a catalog connector is added, a key reaches its tools through its own
// virtual server, never through the organization's access default, so the
// drawer's Access control section says where, and whose approvals apply.
const KEY_ACCESS =
  "Keys reach this connector's tools through its own virtual server, which applies its approvals, and never through the organization default. Copy the endpoint from MCP > Virtual servers.";
// The connector's own virtual server was deleted, or edited to leave the
// connector out. The line names it by its slug, which is the connector's
// server id, as MCP > Virtual servers lists it, and says what restores it in
// each case.
const noOwnVirtualServer = (slug) =>
  `This connector's own virtual server was deleted or no longer has it as a member, so its approvals no longer apply anywhere. If MCP > Virtual servers still lists ${slug}, add the connector back to it. If not, disconnect the connector and connect it again.`;
const NO_VIRTUAL_SERVERS =
  "Virtual servers are not available in this deployment, so no key can reach this connector's tools.";
// A connector this release does not connect (Microsoft's hold, or a pilot the
// gateway does not serve): the gateway refuses every call to its tools.
const HELD =
  "No key can reach this connector's tools: connections to this app are not available in this release.";
// Any line about how keys reach a connector's tools, or that its approvals
// no longer apply.
const ANY_KEY_LINE =
  /reach (this connector's|its) tools|approvals no longer apply/;

const PROVIDER = { id: "mcp", account_modes: ["agent"], tools: [] };
const ADDED = {
  server_id: "acme-docs",
  virtual_server: { id: "vs_01M5CA9CSQ7WV0A7Z7W8T71E58", slug: "acme-docs" },
  tools: [{ name: "read_page", gateway_posture: "allow" }],
};

const accessHeading = () =>
  screen.getByRole("heading", { level: 3, name: "Access control" });

// The section shows line and no other line about keys, or none for null.
function expectOnly(line) {
  const section = within(accessHeading().closest("section"));
  if (line === null) {
    expect(section.queryByText(ANY_KEY_LINE)).not.toBeInTheDocument();
    return;
  }
  expect(section.getByText(line)).toBeInTheDocument();
  expect(section.getAllByText(ANY_KEY_LINE)).toHaveLength(1);
}

// The section shows the no-own-virtual-server line whole, with the slug in
// code face, and no other line about keys.
function expectNoOwnLine(slug) {
  const section = within(accessHeading().closest("section"));
  const code = section.getByText(slug, { selector: "code" });
  expect(code.parentElement.textContent).toBe(noOwnVirtualServer(slug));
  expect(section.getAllByText(ANY_KEY_LINE)).toHaveLength(1);
}

beforeEach(() => {
  release.features = ON;
});

describe("the Access control section of a catalog connector", () => {
  it.each([
    ["an added server with its virtual server", PROVIDER, ADDED],
    ["an added server whose provider left the catalog", null, ADDED],
  ])("says where a key reaches its tools, for %s", (_, provider, server) => {
    renderMcp(
      <AccessControl appName="Fixture" provider={provider} server={server} />,
    );
    expectOnly(KEY_ACCESS);
  });

  it("says nothing about keys for a provider that is not added yet", () => {
    renderMcp(<AccessControl appName="Fixture" provider={PROVIDER} />);
    expectOnly(null);
  });

  // Its own virtual server was deleted, or an admin edited it to leave the
  // connector out. The catalog read reports no virtual server of its own in
  // both cases; the line names the slug, which is the connector's server id.
  it.each([
    ["its own virtual server was deleted", "acme-docs"],
    ["its own virtual server no longer has it as a member", "team-wiki"],
  ])("names its virtual server by its slug when %s", (_, serverId) => {
    renderMcp(
      <AccessControl
        appName="Fixture"
        provider={PROVIDER}
        server={{ server_id: serverId, virtual_server: null, tools: [] }}
      />,
    );
    expectNoOwnLine(serverId);
  });

  it("names no slug while its own virtual server has it as a member", () => {
    renderMcp(
      <AccessControl appName="Fixture" provider={PROVIDER} server={ADDED} />,
    );
    const section = within(accessHeading().closest("section"));
    expect(
      section.queryByText(/approvals no longer apply/),
    ).not.toBeInTheDocument();
    expect(
      section.queryByText("acme-docs", { selector: "code" }),
    ).not.toBeInTheDocument();
  });

  it.each([
    ["with its virtual server", ADDED],
    ["with none of its own", { server_id: "acme-docs", tools: [] }],
  ])(
    "says no key can reach its tools while virtual servers are off, %s",
    (_, server) => {
      release.features = OFF;
      renderMcp(
        <AccessControl appName="Fixture" provider={PROVIDER} server={server} />,
      );
      expectOnly(NO_VIRTUAL_SERVERS);
    },
  );

  it("says nothing about keys for a provider not added while virtual servers are off", () => {
    release.features = OFF;
    renderMcp(<AccessControl appName="Fixture" provider={PROVIDER} />);
    expectOnly(null);
  });

  it.each([
    ["with its virtual server", ADDED],
    ["with none of its own", { server_id: "acme-docs", tools: [] }],
  ])(
    "says no key can reach the tools of a connector this release does not connect, %s",
    (_, server) => {
      renderMcp(
        <AccessControl
          appName="Fixture"
          provider={PROVIDER}
          server={server}
          held
        />,
      );
      expectOnly(HELD);
    },
  );

  it("says the same for such a connector while virtual servers are off", () => {
    release.features = OFF;
    renderMcp(
      <AccessControl
        appName="Fixture"
        provider={PROVIDER}
        server={ADDED}
        held
      />,
    );
    expectOnly(HELD);
  });

  it("says nothing about keys for such a connector that is not added", () => {
    renderMcp(<AccessControl appName="Fixture" provider={PROVIDER} held />);
    expectOnly(null);
  });
});
