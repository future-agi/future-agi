import React, { useContext } from "react";
import { act, cleanup, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import axiosPackage from "axios";
import client, { endpoints } from "src/utils/axios";
import { AuthContext } from "src/auth/context/jwt/auth-context";
import { AuthProvider } from "src/auth/context/jwt/auth-provider";
import { OrganizationProvider, useOrganization } from "../OrganizationContext";

const h = vi.hoisted(() => ({
  notify: vi.fn(),
  clearQueries: vi.fn(),
  resetChat: vi.fn(),
}));
vi.mock("src/config-global", () => ({ HOST_API: "https://offline.invalid" }));
vi.mock("src/api/contracts/openapi-contract", () => ({
  assertContractedRequestConfig: (config) => config,
  assertContractedResponse: (response) => response,
  findOpenApiEndpoint: vi.fn(),
}));
vi.mock("src/components/snackbar", () => ({ enqueueSnackbar: h.notify }));
vi.mock("notistack", () => ({ enqueueSnackbar: h.notify }));
vi.mock("@tanstack/react-query", () => {
  const queryClient = { clear: h.clearQueries };
  return { useQueryClient: () => queryClient };
});
vi.mock("src/utils/Mixpanel", () => ({ identifyUser: vi.fn(), resetUser: vi.fn() }));
vi.mock("src/utils/PostHog", () => ({
  identifyPostHogUser: vi.fn(), resetPostHogUser: vi.fn(),
}));
vi.mock("@sentry/react", () => ({ setUser: vi.fn() }));
vi.mock("src/utils/logger", () => ({
  default: { debug: vi.fn(), info: vi.fn(), warn: vi.fn(), error: vi.fn() },
}));
vi.mock("src/utils/recaptchaService", () => ({ getRecaptchaToken: vi.fn() }));
vi.mock("src/sections/falcon-ai/store/useFalconStore", () => ({
  default: { getState: () => ({ resetAll: h.resetChat }) },
}));
vi.mock("src/sections/omega/code/keyStore", () => ({ forgetKey: vi.fn() }));

const adapter = vi.fn();
const oldClientAdapter = client.defaults.adapter;
const oldPackageAdapter = axiosPackage.defaults.adapter;
const notice = "You no longer have access to that organization. Switched to Accessible.";
const workspaceKeys = [
  "workspaceId", "workspaceName", "workspaceDisplayName", "workspaceRole",
  "wsLevel", "workspaceOrgId",
];
const organizationKeys = [
  "organizationId", "organizationName", "organizationDisplayName", "organizationRole", "orgLevel",
];
let auth;
let organization;
let organizations;
let removed;
let refuseLanding;
let listFailure;
let pendingList;
let finishList;
let pendingBootstrap;
let finishBootstrap;
const header = (config, key) => config.headers?.get?.(key) ?? config.headers?.[key];
const path = (config) => new URL(config.url, "https://offline.invalid").pathname;
const callsTo = (url) => adapter.mock.calls.map(([c]) => c).filter((c) => path(c) === url);
const ok = (config, data) => Promise.resolve({ status: 200, data, headers: {}, config });
function refuse(config, code = "organization_unavailable", status = 403) {
  // The real adapter returns JSON text, before Axios' transformResponse runs.
  return Promise.reject({
    config,
    response: { status, data: JSON.stringify({ code, detail: "Refused" }), headers: {}, config },
  });
}
function userFor(id) {
  return {
    id: "user", email: "test@example.invalid",
    organization: id ? { id, name: id === "stale" ? "Removed" : "Accessible" } : null,
    organization_role: id ? "Member" : null,
    org_level: id ? 3 : null,
    default_workspace_id: null,
    requires_org_setup: !id,
  };
}
function ObserveAuth() {
  auth = useContext(AuthContext);
  return null;
}
function ObserveOrganization() {
  organization = useOrganization();
  return null;
}
function mount() {
  render(
    <AuthProvider>
      <ObserveAuth />
      <OrganizationProvider><ObserveOrganization /></OrganizationProvider>
    </AuthProvider>,
  );
}
async function mountAuthenticated() {
  mount();
  await waitFor(() => expect(auth.authenticated).toBe(true));
  await waitFor(() => expect(organization.currentOrganizationId).toBe("stale"));
  adapter.mockClear();
}
const failedWrite = (config) => client.post("/model-hub/datasets/create/", {}, config).catch((e) => e);

beforeEach(() => {
  vi.clearAllMocks();
  sessionStorage.clear();
  localStorage.clear();
  localStorage.setItem("accessToken", "test-session");
  localStorage.setItem("refreshToken", "test-refresh");
  organizationKeys.forEach((key) => sessionStorage.setItem(key, key === "organizationId" ? "stale" : "old"));
  workspaceKeys.forEach((key) => sessionStorage.setItem(key, "old"));
  client.defaults.headers.common["X-Organization-Id"] = "stale";
  client.defaults.headers.common["X-Workspace-Id"] = "old";
  delete client.defaults.headers.common.Authorization;
  client.defaults.adapter = adapter;
  axiosPackage.defaults.adapter = adapter;
  organizations = [
    { id: "first", name: "First", role: "Member", level: 3 },
    { id: "landing", name: "Accessible", role: "Member", level: 3, is_selected: true },
  ];
  auth = undefined;
  organization = undefined;
  removed = false;
  refuseLanding = false;
  listFailure = false;
  pendingList = false;
  finishList = undefined;
  pendingBootstrap = false;
  finishBootstrap = undefined;
  adapter.mockReset().mockImplementation((config) => {
    if (path(config) === endpoints.organizations.list) {
      if (listFailure) return refuse(config, "service_unavailable", 503);
      if (pendingList) return new Promise((resolve) => {
        finishList = () => resolve({ status: 200, data: { result: { organizations } }, headers: {}, config });
      });
      return ok(config, { result: { organizations } });
    }
    if (path(config) === endpoints.auth.me) {
      const id = header(config, "X-Organization-Id");
      if ((removed && id === "stale") || (refuseLanding && id === "landing")) return refuse(config);
      if (pendingBootstrap && id === "landing") return new Promise((resolve) => {
        finishBootstrap = () => resolve({ status: 200, data: userFor(id), headers: {}, config });
      });
      return ok(config, userFor(id || organizations.find((o) => o.is_selected)?.id || organizations[0]?.id));
    }
    return refuse(config);
  });
});
afterEach(() => {
  cleanup();
  client.defaults.adapter = oldClientAdapter;
  axiosPackage.defaults.adapter = oldPackageAdapter;
  ["Authorization", "X-Organization-Id", "X-Workspace-Id"].forEach((key) => delete client.defaults.headers.common[key]);
  sessionStorage.clear();
  localStorage.clear();
});

describe("organization_unavailable recovery", () => {
  it("coalesces stale-id refusals, clears tenant scope, bootstraps the default once and never replays writes", async () => {
    await mountAuthenticated();
    removed = true;
    pendingList = true;
    let writes;
    act(() => { writes = Promise.all([failedWrite(), failedWrite()]); });
    await waitFor(() => expect(finishList).toBeTypeOf("function"));
    for (const key of [...organizationKeys, ...workspaceKeys]) expect(sessionStorage.getItem(key)).toBeNull();
    expect(client.defaults.headers.common["X-Organization-Id"]).toBeUndefined();
    expect(client.defaults.headers.common["X-Workspace-Id"]).toBeUndefined();
    const list = callsTo(endpoints.organizations.list)[0];
    expect(header(list, "X-Organization-Id")).toBeFalsy();
    expect(header(list, "X-Workspace-Id")).toBeFalsy();
    expect(header(list, "Authorization")).toBe("Bearer test-session");
    await act(async () => { finishList(); await writes; });
    await waitFor(() => expect(organization.currentOrganizationId).toBe("landing"));
    expect(sessionStorage.getItem("organizationId")).toBe("landing");
    expect(client.defaults.headers.common["X-Organization-Id"]).toBe("landing");
    expect(auth.user.organization.id).toBe("landing");
    expect(callsTo(endpoints.organizations.list)).toHaveLength(1);
    expect(callsTo(endpoints.auth.me)).toHaveLength(1);
    expect(header(callsTo(endpoints.auth.me)[0], "X-Organization-Id")).toBe("landing");
    expect(adapter.mock.calls.filter(([c]) => c.method === "post")).toHaveLength(2);
    expect((await writes).map((e) => e.code)).toEqual(["organization_unavailable", "organization_unavailable"]);
    expect(h.notify).toHaveBeenCalledTimes(1);
    expect(h.notify).toHaveBeenCalledWith(notice, expect.any(Object));
    expect(h.clearQueries).toHaveBeenCalled();
    expect(h.resetChat).toHaveBeenCalled();
    expect(localStorage.getItem("refreshToken")).toBe("test-refresh");
  });

  it("uses the first accessible organization when no default is available", async () => {
    await mountAuthenticated();
    organizations = [{ id: "first", name: "First", role: "Member", level: 3 }];
    removed = true;
    await act(async () => { await failedWrite(); });
    await waitFor(() => expect(organization.currentOrganizationId).toBe("first"));
    expect(callsTo(endpoints.auth.me)).toHaveLength(1);
    expect(h.notify.mock.calls[0][0]).toBe("You no longer have access to that organization. Switched to First.");
  });

  it("uses the existing no-organization state when no memberships remain", async () => {
    await mountAuthenticated();
    organizations = [];
    removed = true;
    await act(async () => { await failedWrite(); });
    await waitFor(() => expect(auth.user.requires_org_setup).toBe(true));
    await waitFor(() => expect(organization.currentOrganizationId).toBeNull());
    expect(organization.isReady).toBe(true);
    expect(auth.authenticated).toBe(true);
    for (const key of [...organizationKeys, ...workspaceKeys]) expect(sessionStorage.getItem(key)).toBeNull();
    expect(client.defaults.headers.common["X-Organization-Id"]).toBeUndefined();
    expect(callsTo(endpoints.organizations.list)).toHaveLength(1);
    expect(callsTo(endpoints.auth.me)).toHaveLength(1);
    expect(h.notify.mock.calls.some(([text]) => text.includes("Switched to"))).toBe(false);
  });

  it("surfaces a successor refusal during bootstrap without another recovery", async () => {
    await mountAuthenticated();
    removed = true;
    pendingBootstrap = true;
    let firstWrite;
    act(() => { firstWrite = failedWrite(); });
    await waitFor(() => expect(finishBootstrap).toBeTypeOf("function"));
    expect(sessionStorage.getItem("organizationId")).toBe("landing");
    let error;
    await act(async () => { error = await failedWrite(); });
    expect(error).toMatchObject({ code: "organization_unavailable", statusCode: 403 });
    expect(callsTo(endpoints.organizations.list)).toHaveLength(1);
    expect(callsTo(endpoints.auth.me)).toHaveLength(1);
    expect(h.notify).not.toHaveBeenCalled();
    await act(async () => { finishBootstrap(); await firstWrite; });
    await waitFor(() => expect(organization.currentOrganizationId).toBe("landing"));
    expect(callsTo(endpoints.organizations.list)).toHaveLength(1);
    expect(callsTo(endpoints.auth.me)).toHaveLength(1);
    expect(h.notify).toHaveBeenCalledTimes(1);
  });

  it("W10-AUTH-ORG-L03 recovers a later loss of the successful replacement in the same session", async () => {
    await mountAuthenticated();
    removed = true;
    await act(async () => { await failedWrite(); });
    await waitFor(() => expect(organization.currentOrganizationId).toBe("landing"));
    expect(auth.user.organization.id).toBe("landing");
    expect(callsTo(endpoints.organizations.list)).toHaveLength(1);
    expect(callsTo(endpoints.auth.me)).toHaveLength(1);

    // Independently remove the replacement membership after its successful
    // landing. Keep the same AuthProvider, coordinator and signed-in session.
    organizations = [{ id: "first", name: "First", role: "Member", level: 3 }];
    refuseLanding = true;
    let errors;
    await act(async () => { errors = await Promise.all([failedWrite(), failedWrite()]); });
    expect(callsTo(endpoints.organizations.list)).toHaveLength(2);
    expect(callsTo(endpoints.auth.me).map((config) => header(config, "X-Organization-Id"))).toEqual(["landing", "first"]);
    await waitFor(() => expect(organization.currentOrganizationId).toBe("first"));
    expect(auth.user.organization.id).toBe("first");
    expect(auth.authenticated).toBe(true);
    expect(sessionStorage.getItem("organizationId")).toBe("first");
    expect(client.defaults.headers.common["X-Organization-Id"]).toBe("first");
    expect(client.defaults.headers.common["X-Workspace-Id"]).toBeUndefined();
    for (const key of workspaceKeys) expect(sessionStorage.getItem(key)).toBeNull();
    expect(localStorage.getItem("accessToken")).toBe("test-session");
    expect(localStorage.getItem("refreshToken")).toBe("test-refresh");
    expect(errors.map((error) => error.code)).toEqual(["organization_unavailable", "organization_unavailable"]);
    expect(adapter.mock.calls.filter(([config]) => config.method === "post")).toHaveLength(3);
    expect(h.notify).toHaveBeenCalledTimes(2);
    expect(h.notify).toHaveBeenLastCalledWith(
      "You no longer have access to that organization. Switched to First.", expect.any(Object),
    );

    await act(async () => { await failedWrite({ headers: { "X-Organization-Id": "landing" } }); });
    expect(callsTo(endpoints.organizations.list)).toHaveLength(2);
    expect(organization.currentOrganizationId).toBe("first");
  });

  it("recovers a stale stored id during initial bootstrap without clearing credentials", async () => {
    removed = true;
    mount();
    await waitFor(() => expect(auth.authenticated).toBe(true));
    await waitFor(() => expect(organization.currentOrganizationId).toBe("landing"));
    expect(callsTo(endpoints.auth.me)).toHaveLength(2);
    expect(callsTo(endpoints.auth.me).map((c) => header(c, "X-Organization-Id"))).toEqual(["stale", "landing"]);
    expect(callsTo(endpoints.organizations.list)).toHaveLength(1);
    expect(localStorage.getItem("accessToken")).toBe("test-session");
    expect(h.notify).toHaveBeenCalledTimes(1);
    expect(h.notify).toHaveBeenCalledWith(notice, expect.any(Object));
  });

  it("recovers sign-in with a stale per-tab selection", async () => {
    localStorage.removeItem("accessToken");
    mount();
    await waitFor(() => expect(auth.loading).toBe(false));
    // The mounted organization provider clears selection on logout. Recreate
    // the stale per-tab choice at sign-in, with its real delivery lifecycle.
    sessionStorage.setItem("organizationId", "stale");
    client.defaults.headers.common["X-Organization-Id"] = "stale";
    removed = true;
    await act(async () => { await auth.login({ status: 200, data: { access: "new-session", refresh: "new-refresh" } }); });
    expect(auth.authenticated).toBe(true);
    expect(auth.user.organization.id).toBe("landing");
    expect(callsTo(endpoints.auth.me).map((c) => header(c, "X-Organization-Id"))).toEqual(["stale", "landing"]);
    expect(localStorage.getItem("accessToken")).toBe("new-session");
    expect(h.notify).toHaveBeenCalledTimes(1);
    expect(h.notify).toHaveBeenCalledWith(notice, expect.any(Object));
  });

  it("recovers an authenticated user-info refusal before the shared interceptor can log out", async () => {
    await mountAuthenticated();
    removed = true;
    let response;
    await act(async () => { response = await client.get(endpoints.auth.me); });
    expect(response.data.organization.id).toBe("landing");
    expect(auth.authenticated).toBe(true);
    expect(localStorage.getItem("accessToken")).toBe("test-session");
    expect(callsTo(endpoints.auth.me)).toHaveLength(2);
    expect(callsTo(endpoints.organizations.list)).toHaveLength(1);
  });

  it("ignores a late refusal from a different organization", async () => {
    await mountAuthenticated();
    await act(async () => { await failedWrite({ headers: { "X-Organization-Id": "previous" } }); });
    expect(organization.currentOrganizationId).toBe("stale");
    expect(callsTo(endpoints.organizations.list)).toHaveLength(0);
    expect(h.notify).not.toHaveBeenCalled();
  });

  it("does not interpret ordinary permission failures as organization removal", async () => {
    await mountAuthenticated();
    adapter.mockImplementation((config) => refuse(config, "permission_denied"));
    await act(async () => { await failedWrite(); });
    expect(organization.currentOrganizationId).toBe("stale");
    expect(adapter).toHaveBeenCalledTimes(1);
    expect(h.notify).not.toHaveBeenCalled();
  });

  it("does not recursively recover a refusal from the replacement bootstrap", async () => {
    removed = true;
    refuseLanding = true;
    mount();
    await waitFor(() => expect(auth.loading).toBe(false));
    expect(callsTo(endpoints.organizations.list)).toHaveLength(1);
    expect(callsTo(endpoints.auth.me)).toHaveLength(2);
    expect(h.notify.mock.calls.some(([text]) => text.includes("Switched to"))).toBe(false);
    expect(localStorage.getItem("accessToken")).toBe("test-session");
  });
});
