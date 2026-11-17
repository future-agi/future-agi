import React from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, screen } from "@testing-library/react";

import { renderMcp } from "./testing/renderMcp";
import MCPOffState from "./MCPOffState";
import MCPStatusBanner from "./MCPStatusBanner";

const OPERATOR_REMEDY =
  "For a self-hosted gateway, ask your operator to configure AGENTCC_MCP_STATE_KEYS, set mcp.enabled: true and restart the gateway.";
const SETUP_GUIDE = "Self-hosted MCP setup guide";
const SETUP_HREF =
  "https://github.com/future-agi/future-agi/blob/55cb111a056ddf520e89ffb5a576ce1a7ff449ce/docs/gateway/mcp.md#run-it-on-your-own-gateway";
const OFF_STATUS = { enabled: false, reason: "gateway_mcp_disabled" };

function expectSetupGuide() {
  expect(screen.getByText(OPERATOR_REMEDY)).toBeInTheDocument();
  const link = screen.getByRole("link", { name: SETUP_GUIDE });
  expect(link).toHaveAttribute("href", SETUP_HREF);
  expect(link).toHaveAttribute("target", "_blank");
  expect(link.rel.split(/\s+/)).toEqual(
    expect.arrayContaining(["noopener", "noreferrer"]),
  );
  link.focus();
  expect(link).toHaveFocus();
  return link;
}

function expectNoDeploymentRemedy() {
  expect(screen.queryByText(OPERATOR_REMEDY)).toBeNull();
  expect(screen.queryByRole("link", { name: SETUP_GUIDE })).toBeNull();
}

describe("deployment-off operator remedy", () => {
  it.each([
    [
      undefined,
      "MCP servers, tools, resources and prompts are not available here.",
    ],
    [
      "Apps connect through this gateway's MCP.",
      "Apps connect through this gateway's MCP.",
    ],
  ])(
    "keeps the off-state description %s and offers only setup guidance",
    (description, expected) => {
      renderMcp(<MCPOffState description={description} />);

      expect(
        screen.getByText("MCP is turned off for this deployment"),
      ).toBeInTheDocument();
      expect(screen.getByText(expected)).toBeInTheDocument();
      expectSetupGuide();
      expect(screen.queryByRole("button")).toBeNull();
    },
  );

  it.each([false, true])(
    "guides the gateway operator without activation when org enabled=%s",
    (enabled) => {
      const onTurnOn = vi.fn();
      const onRetry = vi.fn();
      renderMcp(
        <MCPStatusBanner
          status={OFF_STATUS}
          mcpConfig={{ enabled, servers: {} }}
          onTurnOn={onTurnOn}
          onRetry={onRetry}
        />,
      );

      expect(screen.getByRole("alert")).toHaveTextContent(
        "MCP is turned off on this gateway",
      );
      expect(screen.getByRole("alert")).toHaveTextContent(
        "The hosted profile requires MCP to remain off; self-hosted runtime configuration is managed separately.",
      );
      expect(screen.getByText("gateway_mcp_disabled")).toBeInTheDocument();
      const link = expectSetupGuide();
      expect(screen.queryByRole("button")).toBeNull();
      link.addEventListener("click", (event) => event.preventDefault(), {
        once: true,
      });
      fireEvent.click(link);
      expect(onTurnOn).not.toHaveBeenCalled();
      expect(onRetry).not.toHaveBeenCalled();
    },
  );

  it("keeps organization Turn on MCP working without deployment instructions", () => {
    const onTurnOn = vi.fn();
    renderMcp(
      <MCPStatusBanner
        status={{ enabled: false }}
        mcpConfig={{ enabled: false, servers: {} }}
        onTurnOn={onTurnOn}
      />,
    );

    expect(
      screen.getByText("MCP is off for this organization"),
    ).toBeInTheDocument();
    expectNoDeploymentRemedy();
    expect(onTurnOn).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Turn on MCP" }));
    expect(onTurnOn).toHaveBeenCalledTimes(1);
  });

  it("keeps read-only organization controls without deployment instructions", () => {
    renderMcp(
      <MCPStatusBanner
        status={{ enabled: false }}
        mcpConfig={{ enabled: false }}
      />,
    );

    expect(screen.getByText("Owner or Admin only")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Turn on MCP" })).toBeNull();
    expectNoDeploymentRemedy();
  });

  it.each([
    [
      "unreachable",
      { enabled: false, reason: "gateway_unreachable" },
      false,
      "The gateway could not be reached",
    ],
    ["status_error", undefined, true, "MCP status could not be loaded"],
    [
      "failed refresh of a deployment-off status",
      OFF_STATUS,
      true,
      "MCP status could not be loaded",
    ],
  ])("does not mislabel %s as deployment off", (_, status, isError, title) => {
    const onTurnOn = vi.fn();
    const onRetry = vi.fn();
    renderMcp(
      <MCPStatusBanner
        status={status}
        isError={isError}
        mcpConfig={{ enabled: false }}
        onTurnOn={onTurnOn}
        onRetry={onRetry}
      />,
    );

    expect(screen.getByRole("alert")).toHaveTextContent(title);
    expectNoDeploymentRemedy();
    expect(screen.queryByText("MCP is turned off on this gateway")).toBeNull();
    expect(screen.queryByRole("button", { name: "Turn on MCP" })).toBeNull();
    if (isError) {
      fireEvent.click(screen.getByRole("button", { name: "Retry" }));
      expect(onRetry).toHaveBeenCalledTimes(1);
    } else {
      expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
      expect(onRetry).not.toHaveBeenCalled();
    }
    expect(onTurnOn).not.toHaveBeenCalled();
  });

  it("does not show deployment instructions while status loads", () => {
    const { container } = renderMcp(
      <MCPStatusBanner mcpConfig={{ enabled: false }} onTurnOn={vi.fn()} />,
    );

    expect(container).toBeEmptyDOMElement();
    expectNoDeploymentRemedy();
  });
});
