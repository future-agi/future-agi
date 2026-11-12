import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  useAddConnector,
  useConnectorCatalog,
  useStoreConnectorKey,
} from "../../connectors/hooks/useConnectorCatalog";
import { useMCPCredentials, useCreateMCPCredential } from "./useMCPCredentials";
import { releaseFeatures } from "./useMCPReleaseFeatures";

const { state, api } = vi.hoisted(() => ({
  state: { current: {} },
  api: { get: vi.fn(), post: vi.fn(), delete: vi.fn() },
}));
vi.mock("./useMCPConfig", () => ({ useMCPStatus: () => state.current }));
vi.mock("../../providers/hooks/useGatewayConfig", () => ({
  refreshGatewayConfig: vi.fn(),
}));
vi.mock("src/utils/axios", () => ({
  default: api,
  endpoints: {
    gateway: {
      connectorCatalog: "/catalog/",
      connectorAdd: (id) => `/apps/${id}/`,
      mcpCredentials: "/credentials/",
    },
  },
}));

let client;
function wrapper({ children }) {
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
function setFlags(catalog, credential_vault, virtual_servers) {
  state.current = {
    data: { features: { catalog, credential_vault, virtual_servers } },
  };
}
beforeEach(() => {
  client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  vi.clearAllMocks();
  api.get.mockResolvedValue({
    data: { results: [{ id: "fixture" }], count: 1 },
  });
  api.post.mockResolvedValue({ data: { result: { version: 2 } } });
  setFlags(false, false, false);
});
afterEach(() => {
  cleanup();
  client.clear();
});

const matrix = Array.from({ length: 8 }, (_, bits) => [
  !!(bits & 4),
  !!(bits & 2),
  !!(bits & 1),
]);
describe("W07 capabilities and IO matrix", () => {
  it.each(matrix)(
    "catalog=%s vault=%s virtual=%s writes only with all dependencies",
    async (catalog, vault, virtual) => {
      setFlags(catalog, vault, virtual);
      const { result } = renderHook(() => useAddConnector("gw"), { wrapper });
      await act(async () => {
        if (catalog && vault && virtual) {
          await expect(
            result.current.add("fixture", { mode: "agent" }),
          ).resolves.toEqual({ version: 2 });
        } else {
          await expect(
            result.current.add("fixture", { mode: "agent" }),
          ).rejects.toMatchObject({
            code: "mcp_feature_disabled",
            message: "This MCP feature is off or its status is unavailable.",
          });
        }
      });
      expect(api.post).toHaveBeenCalledTimes(
        catalog && vault && virtual ? 1 : 0,
      );
      expect(api.delete).not.toHaveBeenCalled();
    },
  );
  it.each([false, true])(
    "catalog read on=%s is independent of write dependencies",
    async (on) => {
      setFlags(on, false, false);
      const { result } = renderHook(
        () => useConnectorCatalog({ gatewayId: "gw" }),
        { wrapper },
      );
      if (on)
        await waitFor(() => expect(result.current.data?.apps).toHaveLength(1));
      else {
        await act(async () => {
          await result.current.refetch();
        });
        expect(result.current.data).toBeUndefined();
      }
      expect(api.get).toHaveBeenCalledTimes(on ? 1 : 0);
    },
  );
  it.each([false, true])(
    "vault read and management on=%s ignores catalogue and virtual flags",
    async (on) => {
      setFlags(false, on, false);
      const { result } = renderHook(
        () => ({ read: useMCPCredentials(), write: useCreateMCPCredential() }),
        { wrapper },
      );
      if (on)
        await waitFor(() =>
          expect(result.current.read.data?.rows).toHaveLength(1),
        );
      await act(async () => {
        if (on)
          await result.current.write.mutateAsync({ server_id: "fixture" });
        else
          await expect(
            result.current.write.mutateAsync({ server_id: "fixture" }),
          ).rejects.toMatchObject({
            code: "mcp_feature_disabled",
            message: "This MCP feature is off or its status is unavailable.",
          });
      });
      expect(api.post).toHaveBeenCalledTimes(on ? 1 : 0);
      expect(api.get).toHaveBeenCalledTimes(on ? 2 : 0); // read + post-write invalidation
    },
  );
  it("cached ON catalogue and retained mutation callbacks fail closed after OFF", async () => {
    setFlags(true, true, true);
    const { result, rerender } = renderHook(
      () => ({ read: useConnectorCatalog(), write: useStoreConnectorKey() }),
      { wrapper },
    );
    await waitFor(() => expect(result.current.read.data?.apps).toHaveLength(1));
    const retained = result.current.write.store;
    setFlags(false, false, false);
    rerender();
    expect(result.current.read.data).toBeUndefined();
    await act(async () => {
      await expect(
        retained("fixture", {}, "fixture-value"),
      ).rejects.toMatchObject({
        code: "mcp_feature_disabled",
        message: "This MCP feature is off or its status is unavailable.",
      });
    });
    expect(api.post).not.toHaveBeenCalled();
  });
  it.each([undefined, null, "true", 1])(
    "invalid capability %s stays OFF",
    (value) => {
      setFlags(value, value, value);
      expect(releaseFeatures(state.current)).toEqual({
        catalog: false,
        credentialVault: false,
        virtualServers: false,
        catalogConnect: false,
      });
    },
  );
  it.each(["isError", "isLoading", "isPending"])(
    "%s drops cached ON capabilities",
    (key) => {
      setFlags(true, true, true);
      expect(
        releaseFeatures({ ...state.current, [key]: true }).catalogConnect,
      ).toBe(false);
    },
  );
});

describe("c26 runtime restrictions survive W07 composition", () => {
  it.each([
    { enabled: false, reason: "gateway_mcp_disabled" },
    {
      enabled: false,
      reason: "gateway_unreachable",
      lastKnownDeploymentOff: true,
    },
  ])("%j suppresses stale advertised capabilities", (runtime) => {
    setFlags(true, true, true);
    state.current.data = { ...state.current.data, ...runtime };
    expect(releaseFeatures(state.current)).toEqual({
      catalog: false,
      credentialVault: false,
      virtualServers: false,
      catalogConnect: false,
    });
  });
  // An unreachable gateway with no retained
  // OFF still carries the deployment's switches, and they are read.
  it("an unreachable gateway's advertised switches are read", () => {
    setFlags(true, true, true);
    state.current.data = {
      ...state.current.data,
      enabled: false,
      reason: "gateway_unreachable",
    };
    expect(releaseFeatures(state.current)).toEqual({
      catalog: true,
      credentialVault: true,
      virtualServers: true,
      catalogConnect: true,
    });
  });
  it("retained OFF suppresses reads and a captured mutation after failed refresh", async () => {
    setFlags(true, true, true);
    const { result, rerender } = renderHook(
      () => ({ read: useConnectorCatalog(), write: useStoreConnectorKey() }),
      { wrapper },
    );
    await waitFor(() => expect(result.current.read.data?.apps).toHaveLength(1));
    const retained = result.current.write.store;
    api.get.mockClear();
    state.current.data = {
      ...state.current.data,
      enabled: false,
      reason: "gateway_unreachable",
      lastKnownDeploymentOff: true,
    };
    rerender();
    expect(result.current.read.data).toBeUndefined();
    await act(async () => {
      await result.current.read.refetch();
      await expect(
        retained("fixture", {}, "synthetic-fixture-key"),
      ).rejects.toMatchObject({
        code: "mcp_feature_disabled",
      });
    });
    expect(api.get).not.toHaveBeenCalled();
    expect(api.post).not.toHaveBeenCalled();
  });
});
