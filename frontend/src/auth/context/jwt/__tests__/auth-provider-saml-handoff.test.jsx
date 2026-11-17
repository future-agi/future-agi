import React, { StrictMode, useContext } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { AuthProvider } from "../auth-provider";
import { AuthContext } from "../auth-context";
import { readAuthRedirectParams } from "src/auth/auth-url-intake";
import { SS_KEY_ORG_ID, SS_KEY_USER_ID, SS_KEY_WORKSPACE_ID } from "src/utils/sessionKeys";

const { get, rawGet, setSession } = vi.hoisted(() => ({
  get: vi.fn(), rawGet: vi.fn(), setSession: vi.fn(),
}));
vi.mock("src/utils/axios", () => ({
  default: { get, post: vi.fn(), defaults: { headers: { common: {} } } },
  endpoints: { auth: { me: "/accounts/user-info/" } },
}));
vi.mock("axios", () => ({ default: { get: rawGet } }));
vi.mock("src/config-global", () => ({ HOST_API: "http://localhost" }));
vi.mock("src/components/snackbar", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("src/auth/aws-onboarding", () => ({ clearAWSOnboardingAttempt: vi.fn() }));
vi.mock("../utils", () => ({
  setSession, clearTokens: vi.fn(), setRefreshToken: vi.fn(), setRememberMe: vi.fn(),
}));
vi.mock("src/utils/Mixpanel", () => ({ identifyUser: vi.fn(), resetUser: vi.fn() }));
vi.mock("src/utils/PostHog", () => ({ identifyPostHogUser: vi.fn(), resetPostHogUser: vi.fn() }));
vi.mock("@sentry/react", () => ({ setUser: vi.fn() }));
vi.mock("src/utils/logger", () => ({ default: { info: vi.fn(), warn: vi.fn(), error: vi.fn() } }));
vi.mock("src/sections/falcon-ai/store/useFalconStore", () => ({
  default: { getState: () => ({ resetAll: vi.fn() }) },
}));
vi.mock("src/sections/omega/code/keyStore", () => ({ forgetKey: vi.fn() }));
vi.mock("src/utils/auth/clearStores", () => ({ resetAllUserStores: vi.fn() }));

const PASSWORD_TOKEN = "synthetic-password-session";
const user = { id: "synthetic-user", organization: { id: "synthetic-org" } };

function captureRedirect(params, pathname = window.location.pathname) {
  const handoff = { pathname, params: new URLSearchParams(params) };
  Object.defineProperty(window, "__faiAuthRedirect", {
    configurable: true, enumerable: false, value: handoff,
  });
  return handoff;
}

function renderProvider(strict = false) {
  const queryClient = new QueryClient();
  let auth;
  function Capture() {
    auth = useContext(AuthContext);
    return null;
  }
  const tree = (
    <QueryClientProvider client={queryClient}>
      <AuthProvider><Capture /></AuthProvider>
    </QueryClientProvider>
  );
  const view = render(strict ? <StrictMode>{tree}</StrictMode> : tree);
  return { ...view, getAuth: () => auth };
}

describe("AuthProvider SAML handoff initialization", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    sessionStorage.clear();
    delete window.__faiAuthRedirect;
    window.history.replaceState(null, "", "/dashboard/develop");
    get.mockResolvedValue({ data: user });
    rawGet.mockResolvedValue({ data: user });
  });

  afterEach(() => {
    cleanup();
    delete window.__faiAuthRedirect;
    localStorage.clear();
    sessionStorage.clear();
    window.history.replaceState(null, "", "/");
  });

  it.each([false, true])("defers old-session hydration for captured SAML (StrictMode=%s)", async (strict) => {
    localStorage.setItem("accessToken", PASSWORD_TOKEN);
    sessionStorage.setItem(SS_KEY_ORG_ID, "synthetic-old-org");
    sessionStorage.setItem(SS_KEY_WORKSPACE_ID, "synthetic-old-workspace");
    const handoff = captureRedirect("code=synthetic-handoff&kind=saml");
    expect(window.location.search).toBe("");
    const { getAuth } = renderProvider(strict);

    await waitFor(() => expect(getAuth().loading).toBe(false));
    expect(get).not.toHaveBeenCalled();
    expect(rawGet).not.toHaveBeenCalled();
    expect(setSession).not.toHaveBeenCalled();
    expect(getAuth().user).toBeNull();
    expect(getAuth().unauthenticated).toBe(true);
    expect(sessionStorage.getItem(SS_KEY_USER_ID)).toBeNull();
    expect(localStorage.getItem("accessToken")).toBe(PASSWORD_TOKEN);
    // AuthGuard can mount and redeem; provider must not consume its intake.
    expect(window.__faiAuthRedirect).toBe(handoff);
    expect(readAuthRedirectParams().get("code")).toBe("synthetic-handoff");
    await act(() => getAuth().initialize());
    expect(get).not.toHaveBeenCalled();
    expect(getAuth().loading).toBe(false);
    expect(handoff.params.get("kind")).toBe("saml");
  });

  it.each([
    ["ordinary session", ""],
    ["non-SAML handoff", "code=synthetic-handoff&kind=sso"],
    ["SAML kind without code", "kind=saml"],
    ["code without kind", "code=synthetic-handoff"],
  ])("preserves initialization for %s", async (_label, params) => {
    localStorage.setItem("accessToken", PASSWORD_TOKEN);
    if (params) captureRedirect(params);
    const { getAuth } = renderProvider();

    await waitFor(() => expect(getAuth().authenticated).toBe(true));
    expect(get).toHaveBeenCalledTimes(1);
    expect(get).toHaveBeenCalledWith("/accounts/user-info/", expect.objectContaining({
      headers: expect.objectContaining({ Authorization: `Bearer ${PASSWORD_TOKEN}` }),
    }));
    expect(setSession).toHaveBeenCalledWith(PASSWORD_TOKEN, user.organization.id);
    expect(getAuth().user).toEqual({ ...user, accessToken: PASSWORD_TOKEN });
    expect(getAuth().loading).toBe(false);
  });

  it("ignores captured SAML for a different pathname", async () => {
    localStorage.setItem("accessToken", PASSWORD_TOKEN);
    captureRedirect("code=synthetic-handoff&kind=saml", "/another-path");
    const { getAuth } = renderProvider();
    await waitFor(() => expect(getAuth().authenticated).toBe(true));
    expect(get).toHaveBeenCalledTimes(1);
  });

  it("initializes the replacement session after the handoff ends", async () => {
    localStorage.setItem("accessToken", PASSWORD_TOKEN);
    captureRedirect("code=synthetic-handoff&kind=saml");
    const first = renderProvider();
    await waitFor(() => expect(first.getAuth().loading).toBe(false));
    expect(get).not.toHaveBeenCalled();
    first.unmount();
    readAuthRedirectParams().clear();
    localStorage.setItem("accessToken", "synthetic-saml-session");
    const second = renderProvider();
    await waitFor(() => expect(second.getAuth().authenticated).toBe(true));
    expect(get).toHaveBeenCalledTimes(1);
    expect(setSession).toHaveBeenCalledWith("synthetic-saml-session", user.organization.id);
  });

  it("preserves ordinary signed-out initialization and password login", async () => {
    const { getAuth } = renderProvider();
    await waitFor(() => expect(getAuth().loading).toBe(false));
    expect(getAuth().unauthenticated).toBe(true);
    expect(get).not.toHaveBeenCalled();
    await act(() => getAuth().login({ status: 200, data: { access: PASSWORD_TOKEN } }));
    expect(rawGet).toHaveBeenCalledWith("http://localhost/accounts/user-info/", {
      headers: { Authorization: `Bearer ${PASSWORD_TOKEN}`, "X-Organization-Id": null, "X-Workspace-Id": null },
    });
    expect(getAuth().authenticated).toBe(true);
    expect(setSession).toHaveBeenCalledWith(PASSWORD_TOKEN, user.organization.id);
  });
});
