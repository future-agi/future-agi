import React, { useContext } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AxiosError } from "axios";

import axios, { endpoints } from "src/utils/axios";
import { AuthProvider } from "../auth-provider";
import { AuthContext } from "../auth-context";
import { processQueue, setIsRefreshing, setRefreshToken, setRememberMe, setSession } from "../utils";
import { requestWebSocketUrl } from "src/components/websocket/use-socket";
import { getSocketSessionToken } from "src/components/websocket/socket-recovery";
import { SS_KEY_ORG_ID, SS_KEY_USER_ID, SS_KEY_WORKSPACE_ID } from "src/utils/sessionKeys";

// Keep the provider, organization loader, HTTP refresh interceptor, token
// publication helpers and socket ticket request real. Only transport and
// unrelated integrations/schema validation are replaced.
vi.mock("src/config-global", () => ({ HOST_API: "/__api" }));
vi.mock("src/utils/recaptchaService", () => ({
  getRecaptchaToken: vi.fn().mockResolvedValue("synthetic-recaptcha"),
}));
vi.mock("src/api/contracts/api-surface", () => ({
  apiPath: (path) => path,
  uncontractedApiPath: (path) => path,
}));
vi.mock("src/api/contracts/openapi-contract", () => ({
  assertContractedRequestConfig: (config) => config,
  assertContractedResponse: (response) => response,
  findOpenApiEndpoint: () => null,
}));
vi.mock("src/contexts/workspaceRefusalHold", () => ({
  claimWorkspaceRefusal: vi.fn(),
  WORKSPACE_RECOVERY_HANDLED: "synthetic-workspace-recovery",
}));
vi.mock("src/components/snackbar", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("src/auth/aws-onboarding", () => ({ clearAWSOnboardingAttempt: vi.fn() }));
vi.mock("src/utils/Mixpanel", () => ({ identifyUser: vi.fn(), resetUser: vi.fn() }));
vi.mock("src/utils/PostHog", () => ({ identifyPostHogUser: vi.fn(), resetPostHogUser: vi.fn() }));
vi.mock("@sentry/react", () => ({ setUser: vi.fn() }));
vi.mock("src/utils/logger", () => ({ default: {
  info: vi.fn(), warn: vi.fn(), error: vi.fn(), debug: vi.fn(), addBreadcrumb: vi.fn(),
} }));
vi.mock("src/sections/falcon-ai/store/useFalconStore", () => ({
  default: { getState: () => ({ resetAll: vi.fn() }) },
}));
vi.mock("src/sections/omega/code/keyStore", () => ({ forgetKey: vi.fn() }));
vi.mock("src/utils/auth/clearStores", () => ({ resetAllUserStores: vi.fn() }));
vi.mock("src/auth/hooks/use-auth-context", () => ({ useSafeAuthContext: vi.fn() }));

const OLD_TOKEN = "synthetic-bootstrap-old";
const FRESH_TOKEN = "synthetic-bootstrap-refreshed";
const REPLACEMENT_TOKEN = "synthetic-unrelated-session";
const WORKSPACE_ID = "synthetic-workspace";
const TICKET = "t".repeat(43);
const users = [
  ["organization", { id: "synthetic-user", organization: { id: "synthetic-org" }, remember_me: true }],
  ["orgless", { id: "synthetic-user", requires_org_setup: true }],
];
const originalAdapter = axios.defaults.adapter;
const originalHeaders = { ...axios.defaults.headers.common };
const queryClients = [];

function deferred() {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
}

function renderProvider() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  queryClients.push(queryClient);
  let auth;
  function Capture() {
    auth = useContext(AuthContext);
    return null;
  }
  render(
    <QueryClientProvider client={queryClient}>
      <AuthProvider><Capture /></AuthProvider>
    </QueryClientProvider>,
  );
  return () => auth;
}

function reply(config, data, status = 200) {
  return { config, data, status, statusText: status === 200 ? "OK" : "Unauthorized", headers: {} };
}

// Pause once in refresh transport and again before the successful user-info
// response. No sleeps: the real interceptor publishes/retries between gates.
async function pendingBootstrap(user, { refresh = true, omitResponseConfig = false } = {}) {
  const refreshGate = deferred();
  const userInfoGate = deferred();
  const requests = [];
  const responses = [];
  let userInfoAttempts = 0;
  const orgId = user.organization?.id || null;
  setSession(OLD_TOKEN, orgId);
  setRefreshToken("synthetic-refresh-token");
  setRememberMe(true);
  if (orgId) sessionStorage.setItem(SS_KEY_ORG_ID, orgId);
  sessionStorage.setItem(SS_KEY_WORKSPACE_ID, WORKSPACE_ID);
  axios.defaults.headers.common["X-Workspace-Id"] = WORKSPACE_ID;
  axios.defaults.adapter = async (config) => {
    const request = {
      url: config.url,
      baseURL: config.baseURL,
      method: config.method,
      authorization: config.headers.get("Authorization"),
      organization: config.headers.get("X-Organization-Id"),
      workspace: config.headers.get("X-Workspace-Id"),
    };
    requests.push(request);
    if (config.url === endpoints.auth.refreshToken) {
      await refreshGate.promise;
      return reply(config, { access: FRESH_TOKEN });
    }
    if (config.url === endpoints.auth.me) {
      userInfoAttempts += 1;
      if (refresh && userInfoAttempts === 1) {
        throw new AxiosError("Synthetic expired session", "ERR_BAD_REQUEST", config, null,
          reply(config, { detail: "Synthetic expired session" }, 401));
      }
      await userInfoGate.promise;
      const response = reply(config, user);
      responses.push({ status: response.status, authorization: request.authorization });
      return omitResponseConfig ? { ...response, config: undefined } : response;
    }
    if (config.url === endpoints.overview.dashboardSummary) return reply(config, {});
    throw new Error("Unexpected request in bootstrap regression");
  };

  const getAuth = renderProvider();
  await waitFor(() => expect(requests.filter((request) => request.url === endpoints.auth.me)).toHaveLength(1));
  expect(getAuth().loading).toBe(true);
  expect(localStorage.getItem("accessToken")).toBe(OLD_TOKEN);
  expect(axios.defaults.headers.common.Authorization).toBe(`Bearer ${OLD_TOKEN}`);
  if (refresh) {
    await waitFor(() => expect(requests.filter((request) => request.url === endpoints.auth.refreshToken)).toHaveLength(1));
    await act(async () => { refreshGate.resolve(); });
    await waitFor(() => expect(requests.filter((request) => request.url === endpoints.auth.me)).toHaveLength(2));
    expect(localStorage.getItem("accessToken")).toBe(FRESH_TOKEN);
    expect(axios.defaults.headers.common.Authorization).toBe(`Bearer ${FRESH_TOKEN}`);
  }
  expect(getAuth().user).toBeNull();
  expect(responses).toHaveLength(0);
  expect(requests.filter((request) => request.url === endpoints.auth.me).map((request) => request.authorization))
    .toEqual(refresh ? [`Bearer ${OLD_TOKEN}`, `Bearer ${FRESH_TOKEN}`] : [`Bearer ${OLD_TOKEN}`]);
  return {
    getAuth, requests, responses,
    finish: async () => {
      await act(async () => { userInfoGate.resolve(); });
      await waitFor(() => expect(getAuth().loading).toBe(false));
    },
  };
}

async function expectSessionConsumers(run, user, token) {
  expect(run.getAuth().authenticated).toBe(true);
  // Exercise both consumers before asserting: on the base, BOTH headers
  // regress even though the retried user-info response was already a 200.
  const socketUrl = await requestWebSocketUrl(
    getSocketSessionToken(run.getAuth().user.accessToken), "/ws/connect/",
  );
  await axios.get(endpoints.overview.dashboardSummary);
  expect(fetch).toHaveBeenCalledTimes(1);
  const [ticketUrl, options] = fetch.mock.calls[0];
  expect(ticketUrl).toBe("/__api/accounts/handoff/ws-ticket/");
  expect(options).toMatchObject({ method: "POST", credentials: "omit", cache: "no-store", redirect: "error" });
  expect.soft(options.headers.Authorization).toBe(`Bearer ${token}`);
  expect(JSON.parse(options.body)).toEqual({ path: "/ws/connect/", query: {} });
  expect(options.body).not.toContain(token);
  expect(socketUrl).toBe(`/__api/ws/connect/?ticket=${TICKET}`);
  expect(socketUrl).not.toContain(token);
  expect.soft(run.requests.at(-1)).toMatchObject({
    url: endpoints.overview.dashboardSummary, baseURL: "/__api", method: "get",
    authorization: `Bearer ${token}`, workspace: WORKSPACE_ID,
  });
  expect.soft(localStorage.getItem("accessToken")).toBe(token);
  expect.soft(axios.defaults.headers.common.Authorization).toBe(`Bearer ${token}`);
  expect.soft(run.getAuth().user).toEqual({ ...user, accessToken: token });
  expect(sessionStorage.getItem(SS_KEY_USER_ID)).toBe(user.id);
  expect(sessionStorage.getItem(SS_KEY_ORG_ID)).toBe(user.organization?.id || null);
  expect(axios.defaults.headers.common["X-Organization-Id"]).toBe(user.organization?.id);
  expect(axios.defaults.headers.common["X-Workspace-Id"]).toBe(WORKSPACE_ID);
}

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  sessionStorage.clear();
  delete window.__faiAuthRedirect;
  window.history.replaceState(null, "", "/dashboard/develop");
  axios.defaults.headers.common = { ...originalHeaders };
  setIsRefreshing(false);
  processQueue(null);
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
    ok: true, status: 200, json: async () => ({ ticket: TICKET }),
  }));
});

afterEach(() => {
  cleanup();
  queryClients.splice(0).forEach((client) => client.clear());
  axios.defaults.adapter = originalAdapter;
  axios.defaults.headers.common = { ...originalHeaders };
  setIsRefreshing(false);
  processQueue(null);
  vi.unstubAllGlobals();
  localStorage.clear();
  sessionStorage.clear();
  delete window.__faiAuthRedirect;
  window.history.replaceState(null, "", "/");
});

describe("AuthProvider bootstrap socket refresh", () => {
  it.each(users)("keeps the refreshed bearer for socket tickets and later API requests (%s)", async (_label, user) => {
    const run = await pendingBootstrap(user);
    await run.finish();
    expect(run.responses).toEqual([{ status: 200, authorization: `Bearer ${FRESH_TOKEN}` }]);
    await expectSessionConsumers(run, user, FRESH_TOKEN);
    expect(run.requests.filter((request) => request.url === endpoints.auth.refreshToken)).toHaveLength(1);
    expect(run.requests.filter((request) => request.url === endpoints.auth.me)).toHaveLength(2);
  });

  it.each(users)("preserves bootstrap without refresh (%s)", async (_label, user) => {
    const run = await pendingBootstrap(user, { refresh: false });
    await run.finish();
    expect(run.responses).toEqual([{ status: 200, authorization: `Bearer ${OLD_TOKEN}` }]);
    await expectSessionConsumers(run, user, OLD_TOKEN);
    expect(run.requests.filter((request) => request.url === endpoints.auth.refreshToken)).toHaveLength(0);
    expect(run.requests.filter((request) => request.url === endpoints.auth.me)).toHaveLength(1);
  });

  it.each(users)("does not revive a session cleared while refreshed user-info is pending (%s)", async (_label, user) => {
    const run = await pendingBootstrap(user);
    setSession(null);
    await run.finish();
    expect(run.responses).toEqual([{ status: 200, authorization: `Bearer ${FRESH_TOKEN}` }]);
    expect.soft(localStorage.getItem("accessToken")).toBeNull();
    expect.soft(axios.defaults.headers.common.Authorization).toBeUndefined();
    expect.soft(run.getAuth().user).toBeNull();
    expect.soft(run.getAuth().unauthenticated).toBe(true);
    expect.soft(sessionStorage.getItem(SS_KEY_USER_ID)).toBeNull();
    expect.soft(getSocketSessionToken(run.getAuth().user?.accessToken)).toBeNull();
    expect(fetch).not.toHaveBeenCalled();
  });

  it.each(users)("does not pair refreshed user-info with an unrelated replacement session (%s)", async (_label, user) => {
    const run = await pendingBootstrap(user);
    setSession(REPLACEMENT_TOKEN, "synthetic-replacement-org");
    await run.finish();
    expect(run.responses).toEqual([{ status: 200, authorization: `Bearer ${FRESH_TOKEN}` }]);
    expect.soft(localStorage.getItem("accessToken")).toBe(REPLACEMENT_TOKEN);
    expect.soft(axios.defaults.headers.common.Authorization).toBe(`Bearer ${REPLACEMENT_TOKEN}`);
    expect.soft(axios.defaults.headers.common["X-Organization-Id"]).toBe("synthetic-replacement-org");
    expect.soft(run.getAuth().user).toBeNull();
    expect.soft(run.getAuth().unauthenticated).toBe(true);
    expect.soft(sessionStorage.getItem(SS_KEY_USER_ID)).toBeNull();
    expect(fetch).not.toHaveBeenCalled();
  });

  it("preserves unchanged bootstrap when response config is absent", async () => {
    const user = users[0][1];
    const run = await pendingBootstrap(user, { refresh: false, omitResponseConfig: true });
    await run.finish();
    await expectSessionConsumers(run, user, OLD_TOKEN);
  });

  it("does not adopt changed storage when response config is absent", async () => {
    const run = await pendingBootstrap(users[0][1], { refresh: false, omitResponseConfig: true });
    setSession(REPLACEMENT_TOKEN, "synthetic-replacement-org");
    await run.finish();
    expect.soft(localStorage.getItem("accessToken")).toBe(REPLACEMENT_TOKEN);
    expect.soft(axios.defaults.headers.common.Authorization).toBe(`Bearer ${REPLACEMENT_TOKEN}`);
    expect.soft(run.getAuth().user).toBeNull();
    expect.soft(run.getAuth().unauthenticated).toBe(true);
    expect.soft(sessionStorage.getItem(SS_KEY_USER_ID)).toBeNull();
  });
});
