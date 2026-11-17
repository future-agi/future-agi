import React, { useEffect } from "react";
import { afterEach, expect, it, vi } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import {
  OrganizationProvider,
  useOrganization,
} from "src/contexts/OrganizationContext";
import { SS_KEY_ORG_ID } from "src/utils/sessionKeys";
import { useMCPPrompts, useMCPResources } from "./useMCPConfig";

vi.mock("src/auth/hooks", () => ({
  // Keep the session-backed organization; no login/logout effect in this test.
  useAuthContext: () => ({ user: null, authenticated: false, loading: true }),
}));
vi.mock("src/components/snackbar", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("src/utils/Mixpanel", () => ({ resetUser: vi.fn() }));
vi.mock("src/utils/logger", () => ({
  default: { debug: vi.fn(), warn: vi.fn(), error: vi.fn() },
}));
vi.mock("src/config-global", async (importOriginal) => ({
  ...(await importOriginal()),
  HOST_API: "https://offline.invalid",
}));
vi.mock("src/auth/context/jwt/utils", () => ({
  addToQueue: vi.fn(),
  clearTokens: vi.fn(),
  getIsRefreshing: vi.fn(),
  getRefreshToken: vi.fn(),
  getRememberMe: vi.fn(),
  processQueue: vi.fn(),
  refreshTokenRequest: vi.fn(),
  setIsRefreshing: vi.fn(),
  setSession: vi.fn(),
}));

const GATEWAY_ID = "00000000-0000-0000-0000-0000000000a1";
const ORG_A = "00000000-0000-0000-0000-0000000000a2";
const ORG_B = "00000000-0000-0000-0000-0000000000b2";
const rows = {
  [ORG_A]: {
    resources: [{ name: "A resource", uri: "test://a", server_id: "a" }],
    prompts: [{ name: "A prompt", server_id: "a" }],
  },
  [ORG_B]: {
    resources: [{ name: "B resource", uri: "test://b", server_id: "b" }],
    prompts: [{ name: "B prompt", server_id: "b" }],
  },
};
const originalAdapter = axios.defaults.adapter;
const originalLocation = Object.getOwnPropertyDescriptor(window, "location");
const clients = [];

afterEach(() => {
  clients.splice(0).forEach((client) => client.clear());
  axios.defaults.adapter = originalAdapter;
  delete axios.defaults.headers.common["X-Organization-Id"];
  delete axios.defaults.headers.common["X-Workspace-Id"];
  Object.defineProperty(window, "location", originalLocation);
  sessionStorage.clear();
});

it("817: a real organization switch never reuses another org's resources or prompts for the same gateway", async () => {
  const warn = vi.spyOn(console, "warn");
  sessionStorage.clear();
  sessionStorage.setItem(SS_KEY_ORG_ID, ORG_B);
  const assign = vi.fn();
  Object.defineProperty(window, "location", {
    configurable: true,
    writable: true,
    value: { assign, href: "http://localhost/dashboard/gateway/mcp" },
  });

  const requests = [];
  // Only the transport is replaced: real hooks, axios interceptors, and the
  // real organization switch set the header used to choose these fixtures.
  axios.defaults.adapter = async (config) => {
    const org = config.headers.get("X-Organization-Id");
    requests.push({ org, url: config.url, method: config.method });
    let result;
    if (
      config.method === "post" &&
      config.url === endpoints.organizations.switch
    ) {
      const { organization_id: id } = JSON.parse(config.data);
      expect([ORG_A, ORG_B]).toContain(id);
      result = {
        organization: { id, name: id, display_name: id },
        org_role: null,
        org_level: null,
        workspace_role: null,
      };
    } else {
      expect(config.method).toBe("get");
      expect([ORG_A, ORG_B]).toContain(org);
      if (config.url === endpoints.gateway.mcpResources(GATEWAY_ID)) {
        result = rows[org].resources;
      } else if (config.url === endpoints.gateway.mcpPrompts(GATEWAY_ID)) {
        result = rows[org].prompts;
      } else {
        throw new Error(`Unexpected offline request: ${config.url}`);
      }
    }
    return {
      config,
      data: JSON.stringify({ status: true, result }),
      status: 200,
      statusText: "OK",
      headers: { "content-type": "application/json" },
      request: {},
    };
  };

  const observed = [];
  function mountDocument() {
    // app.jsx owns a module-level QueryClient, recreated by a hard navigation.
    // Never clear/rekey a live cache to simulate an in-place organization switch.
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    clients.push(client);
    const wrapper = ({ children }) => (
      <QueryClientProvider client={client}>
        <OrganizationProvider>{children}</OrganizationProvider>
      </QueryClientProvider>
    );
    const hook = renderHook(
      () => {
        const organization = useOrganization();
        const resources = useMCPResources(GATEWAY_ID);
        const prompts = useMCPPrompts(GATEWAY_ID);
        useEffect(() => {
          observed.push({
            org: organization.currentOrganizationId,
            resources: resources.data,
            prompts: prompts.data,
          });
        }, [organization.currentOrganizationId, resources.data, prompts.data]);
        return { organization, resources, prompts };
      },
      { wrapper },
    );
    return { ...hook, client };
  }

  async function expectOwnRows(document, org) {
    await waitFor(() => {
      expect(document.result.current.resources.data).toEqual(
        rows[org].resources,
      );
      expect(document.result.current.prompts.data).toEqual(rows[org].prompts);
    });
    expect(document.result.current.organization.currentOrganizationId).toBe(
      org,
    );
  }

  let document = mountDocument();
  await expectOwnRows(document, ORG_B);
  for (const [previous, next] of [
    [ORG_B, ORG_A],
    [ORG_A, ORG_B],
  ]) {
    assign.mockClear();
    await act(async () => {
      await document.result.current.organization.switchOrganization(next);
    });
    expect(sessionStorage.getItem(SS_KEY_ORG_ID)).toBe(next);
    expect(axios.defaults.headers.common["X-Organization-Id"]).toBe(next);
    expect(assign).toHaveBeenCalledExactlyOnceWith("/dashboard/develop");

    // jsdom cannot navigate. Only after the real switch requests a hard reload,
    // simulate that document boundary, then revisit MCP with the SAME gateway.
    // The previous cache really contains rows; no different gateway key masks it.
    for (const kind of ["resources", "prompts"]) {
      expect(
        document.client.getQueryData([`agentcc-mcp-${kind}`, GATEWAY_ID]),
      ).toEqual(rows[previous][kind]);
    }
    document.unmount();
    document = mountDocument();
    expect(document.result.current.resources.data).toBeUndefined();
    expect(document.result.current.prompts.data).toBeUndefined();
    await expectOwnRows(document, next);
  }

  for (const snapshot of observed) {
    for (const kind of ["resources", "prompts"]) {
      if (snapshot[kind] !== undefined) {
        expect(snapshot[kind]).toEqual(rows[snapshot.org][kind]);
      }
    }
  }
  const listRequests = requests.filter(({ method }) => method === "get");
  expect(listRequests).toEqual(
    [ORG_B, ORG_A, ORG_B].flatMap((org) => [
      { org, method: "get", url: endpoints.gateway.mcpResources(GATEWAY_ID) },
      { org, method: "get", url: endpoints.gateway.mcpPrompts(GATEWAY_ID) },
    ]),
  );
  expect(requests.filter(({ method }) => method === "post")).toEqual([
    { org: ORG_B, method: "post", url: endpoints.organizations.switch },
    { org: ORG_A, method: "post", url: endpoints.organizations.switch },
  ]);
  document.unmount();
  expect(warn).not.toHaveBeenCalled();
  warn.mockRestore();
});
