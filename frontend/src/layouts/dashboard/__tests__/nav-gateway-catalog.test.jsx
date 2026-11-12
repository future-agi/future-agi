import React from "react";
import PropTypes from "prop-types";
import { MemoryRouter } from "react-router-dom";
import { renderHook } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { paths } from "src/routes/paths";
import { useNavData, useNavGatewayData } from "../config-navigation";

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

const items = (groups) => groups.flatMap((group) => group.items);
const coreLinks = [
  ["Overview", paths.dashboard.gateway.overview],
  ["Providers", paths.dashboard.gateway.providers],
  ["API keys", paths.dashboard.gateway.keys],
  ["Logs", paths.dashboard.gateway.logs],
  ["Gateway settings", paths.dashboard.gateway.settings],
];
const expectCoreLinks = (groups) => {
  for (const [title, path] of coreLinks) {
    expect(items(groups)).toContainEqual(
      expect.objectContaining({ title, path }),
    );
  }
};
const expectNoManagedOffer = (groups) => {
  // BP-04 may supply a stable Models destination for own-provider models.
  // That is not a managed offer: only managed-only entries must be withheld.
  expect(items(groups).filter((item) => item.managedOnly)).toEqual([]);
  expect(items(groups).some((item) => item.title === "Catalog")).toBe(false);
};
const expectIntegrations = (groups, visible) => {
  expect(
    items(groups).some(
      (item) => item.path === paths.dashboard.gateway.integrations,
    ),
  ).toBe(visible);
};

function renderRail() {
  return renderHook(() => useNavGatewayData(), {
    wrapper: wrapperAt("/dashboard/gateway/logs"),
  });
}

describe("the AI Gateway rail's optional capabilities", () => {
  beforeEach(() => {
    state.status = undefined;
    state.isError = false;
    state.enabled.length = 0;
    state.mcp = { isLoading: true, isPending: true };
    state.mcpIds.length = 0;
  });

  // Integrations stays listed while the MCP
  // status is unresolved.
  it("keeps core links available while both optional reads are unresolved", () => {
    const { result } = renderRail();
    expectCoreLinks(result.current);
    expectNoManagedOffer(result.current);
    expectIntegrations(result.current, true);
  });

  it("omits managed offers on a deployment that offers no managed models", () => {
    state.status = { available: false };
    const { result } = renderRail();
    expectCoreLinks(result.current);
    expectNoManagedOffer(result.current);
    expect(items(result.current)).toContainEqual(
      expect.objectContaining({ title: "Auto routing" }),
    );
  });

  it("lists the models destination when the deployment offers managed models", () => {
    state.status = { available: true, enabled: false };
    const { result } = renderRail();
    expectCoreLinks(result.current);
    expect(items(result.current)).toContainEqual(
      expect.objectContaining({ path: paths.dashboard.gateway.models }),
    );
  });

  it("omits managed offers but keeps core links when the status cannot be read", () => {
    state.isError = true;
    const { result } = renderRail();
    expectCoreLinks(result.current);
    expectNoManagedOffer(result.current);
  });

  it.each(["available", "unavailable", "error"])(
    "keeps core links after a late %s managed-model response",
    (outcome) => {
      const { result, rerender } = renderRail();
      expectCoreLinks(result.current);
      state.status =
        outcome === "error"
          ? undefined
          : { available: outcome === "available" };
      state.isError = outcome === "error";
      rerender();
      expectCoreLinks(result.current);
      if (outcome !== "available") expectNoManagedOffer(result.current);
      expectIntegrations(result.current, true);
    },
  );

  it.each(["catalog", "upstream_oauth"])(
    "keeps Integrations listed before and after %s is confirmed, independently of managed status",
    (feature) => {
      const { result, rerender } = renderRail();
      expectCoreLinks(result.current);
      expectIntegrations(result.current, true);
      state.mcp = {
        data: { features: { [feature]: true } },
        isLoading: false,
        isPending: false,
      };
      rerender();
      expectCoreLinks(result.current);
      expectNoManagedOffer(result.current);
      expectIntegrations(result.current, true);
      state.mcp = { ...state.mcp, isError: true };
      rerender();
      expectCoreLinks(result.current);
      expectIntegrations(result.current, true);
    },
  );

  it("disables Integrations, still listed, for false capabilities", () => {
    state.mcp = {
      data: { features: { catalog: false, upstream_oauth: false } },
    };
    const { result } = renderRail();
    expectCoreLinks(result.current);
    expect(
      items(result.current).find(
        (item) => item.path === paths.dashboard.gateway.integrations,
      ),
    ).toEqual(
      expect.objectContaining({
        disabled: true,
        caption: "Not available in this deployment",
      }),
    );
  });

  // The Future AGI sidebar's Models entry and the rail's Models item are one
  // destination under one name, whatever the managed-model answer.
  it.each([
    ["unresolved", undefined, false],
    ["available", { available: true }, false],
    ["unavailable", { available: false }, false],
    ["error", undefined, true],
  ])(
    "names the catalog Models in both navs when managed status is %s",
    (_outcome, status, isError) => {
      state.status = status;
      state.isError = isError;
      const client = new QueryClient({
        defaultOptions: { queries: { retry: false } },
      });
      function Wrapper({ children }) {
        return (
          <QueryClientProvider client={client}>
            <MemoryRouter initialEntries={["/dashboard/develop"]}>
              {children}
            </MemoryRouter>
          </QueryClientProvider>
        );
      }
      Wrapper.propTypes = { children: PropTypes.node };
      const { result } = renderHook(
        () => ({ host: useNavData(), rail: useNavGatewayData() }),
        { wrapper: Wrapper },
      );
      const atCatalog = (groups) =>
        items(groups).filter(
          (item) => item.path === paths.dashboard.gateway.models,
        );
      expect(atCatalog(result.current.host)).toEqual([
        expect.objectContaining({ title: "Models" }),
      ]);
      expect(atCatalog(result.current.rail)).toEqual([
        expect.objectContaining({ title: "Models" }),
      ]);
    },
  );

  it("reads optional statuses only on AI Gateway pages", () => {
    renderHook(() => useNavGatewayData(), {
      wrapper: wrapperAt("/dashboard/observe"),
    });
    expect(state.enabled).not.toContain(true);
    expect(state.mcpIds).not.toContain("default");
  });
});
