import React from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, screen } from "@testing-library/react";

import { renderMcp } from "./testing/renderMcp";
import MCPStatusBanner, { mcpVerdict } from "./MCPStatusBanner";

const SERVERS = { servers: { "crm-tools": { url: "http://x/mcp" } } };

describe("mcpVerdict", () => {
  it("says nothing while the status loads", () => {
    expect(mcpVerdict({ status: undefined, isError: false }).kind).toBe(
      "loading",
    );
  });

  it("is on only when the API says enabled: true for an org with servers", () => {
    expect(
      mcpVerdict({
        status: { enabled: true },
        mcpConfig: { enabled: true, ...SERVERS },
      }),
    ).toMatchObject({ kind: "on", label: "MCP on", tone: "healthy" });
    for (const enabled of [false, "true", null, undefined]) {
      expect(
        mcpVerdict({
          status: { enabled },
          mcpConfig: { enabled: true, ...SERVERS },
        }).kind,
      ).toBe("not_reported");
    }
  });

  it("names the gateway's own reasons", () => {
    expect(
      mcpVerdict({
        status: { enabled: false, reason: "gateway_mcp_disabled" },
        mcpConfig: SERVERS,
      }).kind,
    ).toBe("gateway_off");
    expect(
      mcpVerdict({
        status: { enabled: false, reason: "gateway_unreachable" },
        mcpConfig: SERVERS,
      }).kind,
    ).toBe("unreachable");
  });

  // The banner's cases, on this verdict: a failed status read is
  // unknown, never "off"; a reason the page does not know is not reported
  // as on; an answer with no server list is not "on" either.
  it("says the status is unknown when the read fails, and never on for an unknown reason", () => {
    expect(
      mcpVerdict({ status: undefined, isError: true, mcpConfig: SERVERS }),
    ).toMatchObject({ kind: "status_error", label: "Status unknown" });
    expect(
      mcpVerdict({
        status: { enabled: false, reason: "something_new" },
        mcpConfig: { enabled: true, ...SERVERS },
      }),
    ).toMatchObject({ kind: "not_reported", reason: "something_new" });
    expect(
      mcpVerdict({
        status: { enabled: false },
        mcpConfig: { enabled: true, ...SERVERS },
      }).kind,
    ).toBe("not_reported");
  });

  it.each([{}, { enabled: false }, { enabled: false, servers: {} }])(
    "offers opt-in before the first server when the saved MCP switch is off: %j",
    (mcpConfig) => {
      expect(
        mcpVerdict({ status: { enabled: false }, mcpConfig }),
      ).toMatchObject({
        kind: "org_off",
        title: "MCP is off for this organization",
        action: "Turn on MCP",
      });
    },
  );

  it.each([false, true])(
    "keeps the empty state when MCP is enabled with no servers (status enabled=%s)",
    (enabled) => {
      expect(
        mcpVerdict({
          status: { enabled },
          mcpConfig: { enabled: true, servers: {} },
        }),
      ).toMatchObject({ kind: "empty", label: "No servers yet" });
    },
  );

  it.each([
    ["gateway_mcp_disabled", "gateway_off"],
    ["gateway_unreachable", "unreachable"],
  ])("preserves %s over a fresh org's saved switch", (reason, kind) => {
    expect(
      mcpVerdict({ status: { enabled: false, reason }, mcpConfig: {} }).kind,
    ).toBe(kind);
  });

  it("does not offer opt-in before a successful status and config read", () => {
    expect(mcpVerdict({ isError: true, mcpConfig: {} }).kind).toBe(
      "status_error",
    );
    expect(mcpVerdict({ mcpConfig: {} }).kind).toBe("loading");
    expect(mcpVerdict({ status: { enabled: false } }).kind).toBe(
      "not_reported",
    );
  });

  it("believes the gateway when it reports on, whatever the saved switch says", () => {
    expect(
      mcpVerdict({ status: { enabled: true }, mcpConfig: { ...SERVERS } }).kind,
    ).toBe("on");
  });

  it("tells an org whose own MCP switch is off that its servers are not connected", () => {
    const verdict = mcpVerdict({
      status: { enabled: false },
      mcpConfig: { ...SERVERS, enabled: false },
    });
    expect(verdict).toMatchObject({ kind: "org_off", action: "Turn on MCP" });
  });
});

describe("MCPStatusBanner", () => {
  it("never says 'in development'", () => {
    for (const status of [
      {},
      { enabled: false },
      { enabled: false, reason: "x" },
    ]) {
      const { unmount } = renderMcp(
        <MCPStatusBanner status={status} isError={false} mcpConfig={SERVERS} />,
      );
      expect(document.body.textContent).not.toMatch(/in development/i);
      unmount();
    }
  });

  it("shows nothing when MCP is on (the page header carries the chip)", () => {
    const { container } = renderMcp(
      <MCPStatusBanner
        status={{ enabled: true }}
        isError={false}
        mcpConfig={{ enabled: true, ...SERVERS }}
      />,
    );
    expect(container.textContent).toBe("");
  });

  it.each([{}, { enabled: false, servers: {} }, SERVERS])(
    "offers to turn MCP on regardless of server count: %j",
    (mcpConfig) => {
      const onTurnOn = vi.fn();
      renderMcp(
        <MCPStatusBanner
          status={{ enabled: false }}
          isError={false}
          mcpConfig={mcpConfig}
          onTurnOn={onTurnOn}
        />,
      );
      expect(
        screen.getByText("MCP is off for this organization"),
      ).toBeInTheDocument();
      expect(screen.queryByText(/Servers are saved/)).toBeNull();
      expect(screen.queryByText("Owner or Admin only")).toBeNull();
      expect(onTurnOn).not.toHaveBeenCalled();
      fireEvent.click(screen.getByRole("button", { name: "Turn on MCP" }));
      expect(onTurnOn).toHaveBeenCalledTimes(1);
    },
  );

  it.each([{}, { enabled: false, servers: {} }, SERVERS])(
    "explains why a read-only user cannot turn MCP on: %j",
    (mcpConfig) => {
      renderMcp(
        <MCPStatusBanner status={{ enabled: false }} mcpConfig={mcpConfig} />,
      );
      expect(
        screen.getByText("MCP is off for this organization"),
      ).toBeInTheDocument();
      expect(screen.getByText("Owner or Admin only")).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Turn on MCP" })).toBeNull();
    },
  );

  it("disables the fresh-org action while the write is pending", () => {
    const onTurnOn = vi.fn();
    renderMcp(
      <MCPStatusBanner
        status={{ enabled: false }}
        mcpConfig={{}}
        onTurnOn={onTurnOn}
        turningOn
      />,
    );
    const button = screen.getByRole("button", { name: "Turn on MCP" });
    expect(button).toBeDisabled();
    fireEvent.click(button);
    expect(onTurnOn).not.toHaveBeenCalled();
  });

  it.each([false, true])(
    "shows no banner for an enabled empty org (status enabled=%s)",
    (enabled) => {
      const { container } = renderMcp(
        <MCPStatusBanner
          status={{ enabled }}
          mcpConfig={{ enabled: true, servers: {} }}
          onTurnOn={vi.fn()}
        />,
      );
      expect(container.textContent).toBe("");
    },
  );

  it("shows the reason code the API gave", () => {
    renderMcp(
      <MCPStatusBanner
        status={{ enabled: false, reason: "gateway_mcp_disabled" }}
        isError={false}
        mcpConfig={SERVERS}
      />,
    );
    expect(
      screen.getByText("MCP is turned off on this gateway"),
    ).toBeInTheDocument();
    expect(screen.getByText("gateway_mcp_disabled")).toBeInTheDocument();
  });
});
