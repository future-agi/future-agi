import React from "react";
import PropTypes from "prop-types";
import { MemoryRouter } from "react-router-dom";
import { renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { paths } from "src/routes/paths";
import { useNavGatewayData } from "../config-navigation";
import { gatewayRailGroups } from "src/sections/gateway/shell/gateway-rail";

const state = vi.hoisted(() => ({
  status: undefined,
  isError: false,
  enabled: [],
  mcp: {},
  mcpIds: [],
}));

vi.mock("src/sections/gateway/models/hooks/useManagedModels", () => ({
  useManagedModelsStatus: ({ enabled = true } = {}) => {
    state.enabled.push(enabled);
    return {
      data: enabled ? state.status : undefined,
      isError: enabled && state.isError,
      failureCount: enabled && state.isError ? 1 : 0,
      isLoading: enabled && state.status === undefined && !state.isError,
    };
  },
}));
vi.mock("src/sections/gateway/mcp/hooks/useMCPConfig", () => ({
  useMCPStatus: (id) => {
    state.mcpIds.push(id);
    return state.mcp;
  },
}));

function wrapperAt(pathname) {
  function Wrapper({ children }) {
    return <MemoryRouter initialEntries={[pathname]}>{children}</MemoryRouter>;
  }
  Wrapper.propTypes = { children: PropTypes.node };
  return Wrapper;
}

const NOT_AVAILABLE = "Not available in this deployment";
const integrationsItem = (groups) =>
  groups
    .flatMap((group) => group.items)
    .find((item) => item.path === paths.dashboard.gateway.integrations);

function renderRail() {
  return renderHook(() => useNavGatewayData(), {
    wrapper: wrapperAt("/dashboard/gateway/logs"),
  });
}

// The rail lists Integrations whatever the
// MCP status read says. Only a catalog switch the status carries as false
// (with no account sign-in) disables it, with the deployment's line.
describe("the rail's Integrations entry stays listed", () => {
  beforeEach(() => {
    state.status = { available: true };
    state.isError = false;
    state.enabled.length = 0;
    state.mcpIds.length = 0;
  });

  it.each([
    ["loading", { isLoading: true, isPending: true }],
    ["a status read error", { isError: true }],
    [
      "an unreachable gateway",
      {
        data: {
          enabled: false,
          reason: "gateway_unreachable",
          features: { catalog: true },
        },
      },
    ],
    [
      "MCP off at runtime with the catalog switch on",
      {
        data: {
          enabled: false,
          reason: "gateway_mcp_disabled",
          features: { catalog: true, upstream_oauth: false },
        },
      },
    ],
    [
      "a retained runtime-off MCP with the catalog switch on",
      {
        data: {
          enabled: false,
          reason: "gateway_unreachable",
          lastKnownDeploymentOff: true,
          features: { catalog: true, upstream_oauth: false },
        },
      },
    ],
    [
      "a healthy status",
      { data: { enabled: true, features: { catalog: true } } },
    ],
  ])("listed and enabled during %s", (_state, mcp) => {
    state.mcp = mcp;
    const { result } = renderRail();
    const item = integrationsItem(result.current);
    expect(item).toEqual(expect.objectContaining({ title: "Integrations" }));
    expect(item.disabled).not.toBe(true);
  });

  it("catalog switch off: listed, disabled, Not available in this deployment", () => {
    state.mcp = {
      data: {
        enabled: true,
        features: { catalog: false, upstream_oauth: false },
      },
    };
    const { result } = renderRail();
    expect(integrationsItem(result.current)).toEqual(
      expect.objectContaining({
        title: "Integrations",
        disabled: true,
        caption: NOT_AVAILABLE,
      }),
    );
  });

  it("the rail data keeps a switched-off Integrations entry", () => {
    const item = integrationsItem(gatewayRailGroups({ integrationsOff: true }));
    expect(item).toEqual(
      expect.objectContaining({ disabled: true, caption: NOT_AVAILABLE }),
    );
  });
});
