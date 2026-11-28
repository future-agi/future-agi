import React, { StrictMode, useContext } from "react";
import { act, cleanup, render, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterAll, afterEach, beforeEach, expect, it, vi } from "vitest";
import { AuthProvider } from "../auth-provider";
import { AuthContext } from "../auth-context";
import { initPostHog } from "src/utils/PostHog";

// Exercise the real identify helpers. The SDKs themselves are synthetic spies;
// no analytics requests, and their restored IDs/groups can vary independently.
const { mp, ph, get, loginGet, sentry } = vi.hoisted(() => {
  vi.stubEnv("VITE_MIXPANEL_TOKEN", "synthetic");
  vi.stubEnv("VITE_POSTHOG_KEY", "synthetic");
  return {
    mp: {
      init: vi.fn(),
      identify: vi.fn(),
      people: { set_once: vi.fn() },
      set_group: vi.fn(),
      get_group: vi.fn(),
      reset: vi.fn(),
      track: vi.fn(),
      has_opted_out_tracking: vi.fn(),
      get_distinct_id: vi.fn(),
      get_property: vi.fn(),
    },
    ph: {
      init: vi.fn(),
      identify: vi.fn(),
      group: vi.fn(),
      reset: vi.fn(),
      has_opted_out_capturing: vi.fn(),
      get_distinct_id: vi.fn(),
      getGroups: vi.fn(),
    },
    get: vi.fn(),
    loginGet: vi.fn(),
    sentry: vi.fn(),
  };
});
vi.mock("mixpanel-browser", () => ({ default: mp }));
vi.mock("posthog-js", () => ({ default: ph }));
vi.mock("axios", () => ({ default: { get: loginGet } }));
vi.mock("@sentry/react", () => ({ setUser: sentry }));
vi.mock("src/utils/auth/clearStores", () => ({ resetAllUserStores: vi.fn() }));
vi.mock("src/utils/logger", () => ({
  default: { debug: vi.fn(), info: vi.fn(), error: vi.fn(), warn: vi.fn() },
}));
vi.mock("src/utils/axios", () => ({
  default: { get, post: vi.fn(), defaults: { headers: { common: {} } } },
  endpoints: {
    auth: { me: "/accounts/user-info/", logout: "/accounts/logout/" },
  },
}));

const KEY = "consentIdentityHandoff.v1";
const member = {
  id: "member-a",
  email: "member-a@example.test",
  name: "Example Member",
  organization: { id: "org-a", name: "Example Org" },
  default_workspace_id: "workspace-a",
};

async function openDocument(consent = false, user = member) {
  Object.defineProperty(window, "__analyticsConsentHandoffAtLoad", {
    configurable: true,
    value: consent,
  });
  get.mockResolvedValue({ data: user });
  let auth;
  function Probe() {
    auth = useContext(AuthContext);
    return null;
  }
  const view = render(
    <StrictMode>
      <QueryClientProvider client={new QueryClient()}>
        <AuthProvider>
          <Probe />
        </AuthProvider>
      </QueryClientProvider>
    </StrictMode>,
  );
  await waitFor(() => expect(auth.loading).toBe(false));
  return { unmount: view.unmount, auth: () => auth };
}

async function leaveSource() {
  const source = await openDocument();
  expect(typeof window.__prepareConsentIdentityHandoff()).toBe("function");
  source.unmount();
  expect(window.__prepareConsentIdentityHandoff).toBeUndefined();
}

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  sessionStorage.clear();
  localStorage.setItem("accessToken", "synthetic-session");
  initPostHog();
  mp.get_group.mockReturnValue({ set_once: vi.fn() });
  mp.has_opted_out_tracking.mockReturnValue(false);
  ph.has_opted_out_capturing.mockReturnValue(false);
  mp.get_distinct_id.mockReturnValue(member.id);
  ph.get_distinct_id.mockReturnValue(member.id);
  mp.get_property.mockImplementation((key) =>
    key === "org_id" ? [member.organization.id] : undefined,
  );
  ph.getGroups.mockReturnValue({
    organization: member.organization.id,
    workspace: member.default_workspace_id,
  });
});
afterEach(() => {
  cleanup();
  delete window.__analyticsConsentHandoffAtLoad;
});
afterAll(() => vi.unstubAllEnvs());

it("skips only the matching handoff hydration, not /me, Sentry, refresh, or explicit login", async () => {
  await leaveSource();
  const receipt = sessionStorage.getItem(KEY);
  for (const field of [
    member.id,
    member.name,
    member.email,
    member.organization.id,
    member.default_workspace_id,
    "synthetic-session",
  ]) {
    expect(receipt).not.toContain(field);
  }
  const dest = await openDocument(true);
  expect(dest.auth().user.id).toBe(member.id);
  expect(sessionStorage.getItem(KEY)).toBeNull();
  expect(get).toHaveBeenCalledTimes(2);
  expect(sentry).toHaveBeenCalledTimes(2);
  expect(mp.identify).toHaveBeenCalledTimes(1);
  expect(ph.identify).toHaveBeenCalledTimes(1);
  expect(mp.set_group).toHaveBeenCalledTimes(1);
  expect(ph.group).toHaveBeenCalledTimes(2);

  await act(() => dest.auth().initialize());
  loginGet.mockResolvedValue({ data: member });
  await act(() =>
    dest.auth().login({ status: 200, data: { access: "synthetic-login" } }),
  );
  expect(mp.identify).toHaveBeenCalledTimes(3);
  expect(ph.identify).toHaveBeenCalledTimes(3);
  dest.unmount();
  await openDocument(true);
  expect(mp.identify).toHaveBeenCalledTimes(4);
  expect(ph.identify).toHaveBeenCalledTimes(4);
});

it.each([
  ["member", { ...member, id: "member-b" }],
  ["organization", { ...member, organization: { id: "org-b" } }],
  ["workspace", { ...member, default_workspace_id: "workspace-b" }],
  ["role", { ...member, organization_role: "changed-role" }],
])("identifies when the resolved %s changes", async (_field, next) => {
  await leaveSource();
  const dest = await openDocument(true, next);
  expect(dest.auth().user.id).toBe(next.id);
  expect(mp.identify).toHaveBeenCalledTimes(2);
  expect(ph.identify).toHaveBeenCalledTimes(2);
  expect(sessionStorage.getItem(KEY)).toBeNull();
});

it.each(["currentUserId", "organizationId", "workspaceId"])(
  "validates existing %s before hydration writes",
  async (key) => {
    await leaveSource();
    sessionStorage.setItem(key, "changed-tab-context");
    await openDocument(true);
    expect(mp.identify).toHaveBeenCalledTimes(2);
    expect(ph.identify).toHaveBeenCalledTimes(2);
  },
);

it.each([
  "ordinary",
  "cancelled",
  "expired",
  "future",
  "malformed",
  "blocked-storage",
])("discards a %s receipt without changing auth", async (kind) => {
  const source = await openDocument();
  const cancel = window.__prepareConsentIdentityHandoff();
  if (kind === "cancelled") cancel();
  if (kind === "malformed") sessionStorage.setItem(KEY, "not-json");
  if (kind === "expired" || kind === "future") {
    const data = JSON.parse(sessionStorage.getItem(KEY));
    sessionStorage.setItem(
      KEY,
      JSON.stringify({
        ...data,
        at: Date.now() + (kind === "future" ? 1e6 : -1e6),
      }),
    );
  }
  source.unmount();
  const remove = Storage.prototype.removeItem;
  const spy = vi
    .spyOn(Storage.prototype, "removeItem")
    .mockImplementation(function (key) {
      if (kind === "blocked-storage" && key === KEY)
        throw new Error("storage unavailable");
      return remove.call(this, key);
    });
  try {
    const dest = await openDocument(kind !== "ordinary");
    expect(dest.auth().authenticated).toBe(true);
    expect(mp.identify).toHaveBeenCalledTimes(2);
    expect(ph.identify).toHaveBeenCalledTimes(2);
    if (kind !== "blocked-storage")
      expect(sessionStorage.getItem(KEY)).toBeNull();
  } finally {
    spy.mockRestore();
  }
});

it("does not authenticate a signed-out arrival from a matching receipt", async () => {
  await leaveSource();
  localStorage.removeItem("accessToken");
  const destination = await openDocument(true);
  expect(destination.auth().unauthenticated).toBe(true);
  expect(sessionStorage.getItem(KEY)).toBeNull();
  expect(get).toHaveBeenCalledTimes(1);
  expect(mp.identify).toHaveBeenCalledTimes(1);
  expect(ph.identify).toHaveBeenCalledTimes(1);
});

it("does not reuse a receipt when both SDKs confirmed a different source identity", async () => {
  mp.get_distinct_id.mockReturnValueOnce("other-member");
  ph.getGroups.mockReturnValueOnce({ organization: "other-org" });
  const source = await openDocument();
  expect(window.__prepareConsentIdentityHandoff()).toBeUndefined();
  expect(sessionStorage.getItem(KEY)).toBeNull();
  source.unmount();
  await openDocument(true);
  expect(mp.identify).toHaveBeenCalledTimes(2);
  expect(ph.identify).toHaveBeenCalledTimes(2);
});

it("retries a partial Mixpanel group failure even if SDK identity readback matches", async () => {
  mp.get_group.mockImplementationOnce(() => {
    throw new Error("synthetic group failure");
  });
  await leaveSource();
  await openDocument(true);
  expect(mp.identify).toHaveBeenCalledTimes(2);
  expect(ph.identify).toHaveBeenCalledTimes(1);
});

it("does not treat an opted-out PostHog call as successful", async () => {
  ph.has_opted_out_capturing.mockReturnValueOnce(true);
  await leaveSource();
  await openDocument(true);
  expect(mp.identify).toHaveBeenCalledTimes(1);
  expect(ph.identify).toHaveBeenCalledTimes(2);
});

it("re-identifies each SDK whose restored identity or groups changed", async () => {
  await leaveSource();
  mp.get_distinct_id.mockReturnValue("member-b");
  ph.getGroups.mockReturnValue({
    organization: member.organization.id,
    workspace: "workspace-b",
  });
  await openDocument(true);
  expect(mp.identify).toHaveBeenCalledTimes(2);
  expect(ph.identify).toHaveBeenCalledTimes(2);
});

it.each(["logout", "cross-tab logout", "cross-tab login"])(
  "invalidates a prepared handoff on %s",
  async (kind) => {
    const source = await openDocument();
    window.__prepareConsentIdentityHandoff();
    expect(sessionStorage.getItem(KEY)).not.toBeNull();
    if (kind === "logout") {
      await act(() => source.auth().logout());
      expect(mp.reset).toHaveBeenCalledOnce();
      expect(ph.reset).toHaveBeenCalledOnce();
    } else {
      const payload = btoa(JSON.stringify({ user_id: "member-b" }));
      const newValue = kind === "cross-tab login" ? `x.${payload}.x` : null;
      act(() =>
        window.dispatchEvent(
          new StorageEvent("storage", { key: "accessToken", newValue }),
        ),
      );
    }
    expect(source.auth().authenticated).toBe(false);
    expect(window.__prepareConsentIdentityHandoff()).toBeUndefined();
    expect(sessionStorage.getItem(KEY)).toBeNull();
  },
);
