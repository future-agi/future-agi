import React, { useContext } from "react";
import { act, cleanup, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AuthContext } from "src/auth/context/jwt/auth-context";
import { AuthProvider } from "src/auth/context/jwt/auth-provider";
import { readAuthRedirectParams } from "src/auth/auth-url-intake";
import {
  bootstrapSamlSession,
  SAML_AUTH_GENERATION_KEY,
} from "src/auth/saml-bootstrap";
import { SS_KEY_USER_ID } from "src/utils/sessionKeys";
import {
  onboardingStorageKey,
  resetIntake,
  runIntake,
  stashKey,
  visit,
} from "./auth-url-fixtures";

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  queryClient: { clear: vi.fn() },
  headers: {},
}));
vi.mock("src/utils/axios", () => ({
  default: {
    get: mocks.get,
    post: mocks.post,
    defaults: { headers: { common: mocks.headers } },
  },
  endpoints: { auth: { me: "/fake/me", logout: "/fake/logout" } },
}));
vi.mock("axios", () => ({ default: { get: mocks.get } }));
vi.mock("src/config-global", () => ({ HOST_API: "" }));
vi.mock("src/auth/context/jwt/utils", () => ({
  clearTokens: vi.fn(),
  setRefreshToken: vi.fn(),
  setRememberMe: vi.fn(),
  setSession: vi.fn(),
}));
vi.mock("@tanstack/react-query", () => ({
  useQueryClient: () => mocks.queryClient,
}));
vi.mock("src/utils/Mixpanel", () => ({
  identifyUser: vi.fn(),
  resetUser: vi.fn(),
}));
vi.mock("src/utils/PostHog", () => ({
  identifyPostHogUser: vi.fn(),
  resetPostHogUser: vi.fn(),
}));
vi.mock("@sentry/react", () => ({ setUser: vi.fn() }));
vi.mock("src/utils/logger", () => ({
  default: { info: vi.fn(), warn: vi.fn(), error: vi.fn() },
}));
vi.mock("src/sections/falcon-ai/store/useFalconStore", () => ({
  default: { getState: () => ({ resetAll: vi.fn() }) },
}));
vi.mock("src/sections/omega/code/keyStore", () => ({ forgetKey: vi.fn() }));

let auth;
function Consumer() {
  auth = useContext(AuthContext);
  return null;
}

function seedOnboarding(key) {
  visit(`/auth/jwt/register?${key}=fake-onboarding`);
  runIntake();
  expect(window[stashKey].params.get(key)).toBe("fake-onboarding");
  expect(window.sessionStorage.getItem(onboardingStorageKey)).not.toBeNull();
  visit("/dashboard/develop");
}

function expectRecoveryGone() {
  expect(window.sessionStorage.getItem(onboardingStorageKey)).toBeNull();
  expect(window[stashKey]).toBeUndefined();
  visit("/auth/jwt/register");
  const later = readAuthRedirectParams();
  expect(later.get("onboarding_token")).toBeNull();
  expect(later.get("onboarding_gcp_token")).toBeNull();
}

async function renderProvider() {
  localStorage.setItem("accessToken", "fake-current-session");
  render(
    <AuthProvider>
      <Consumer />
    </AuthProvider>,
  );
  await waitFor(() => expect(auth.authenticated).toBe(true));
  expect(sessionStorage.getItem(SS_KEY_USER_ID)).toBe("fake-owner-a");
}

function captureNavigation() {
  const browser = window;
  const redirects = [];
  const capture = (url) => {
    redirects.push({
      url,
      recovery: browser.sessionStorage.getItem(onboardingStorageKey),
      stash: browser[stashKey],
    });
  };
  const locationView = {
    get pathname() {
      return browser.location.pathname;
    },
    get search() {
      return browser.location.search;
    },
    set href(url) {
      capture(url);
    },
    replace: capture,
  };
  vi.stubGlobal(
    "window",
    new Proxy(browser, {
      get(target, key) {
        return key === "location"
          ? locationView
          : Reflect.get(target, key, target);
      },
    }),
  );
  return redirects;
}

beforeEach(() => {
  resetIntake();
  vi.stubGlobal("localStorage", window.localStorage);
  vi.stubGlobal("sessionStorage", window.sessionStorage);
  vi.stubGlobal("crypto", { randomUUID: () => "fake-generation" });
  localStorage.clear();
  vi.clearAllMocks();
  mocks.get.mockReset();
  mocks.post.mockReset();
  mocks.get.mockResolvedValue({ data: { id: "fake-owner-a" } });
  mocks.post.mockResolvedValue({ data: {} });
  auth = null;
});
afterEach(async () => {
  cleanup();
  await act(async () => {});
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  resetIntake();
  window.localStorage.clear();
});

describe.each(["onboarding_token", "onboarding_gcp_token"])(
  "onboarding isolation for %s",
  (key) => {
    it.each([false, true])(
      "logout clears recovery before awaiting the server (request fails=%s)",
      async (fails) => {
        seedOnboarding(key);
        await renderProvider();
        let finishRequest;
        mocks.post.mockReturnValueOnce(
          new Promise((resolve, reject) => {
            finishRequest = () =>
              fails ? reject(new Error("Fake logout failure")) : resolve({});
          }),
        );
        let pending;
        act(() => {
          pending = auth.logout();
        });
        expect(mocks.post).toHaveBeenCalledWith(
          "/fake/logout",
          {},
          { headers: { Authorization: "Bearer fake-current-session" } },
        );
        expectRecoveryGone();
        expect(auth.unauthenticated).toBe(true);
        await act(async () => {
          const settled = fails
            ? expect(pending).rejects.toThrow("Fake logout failure")
            : expect(pending).resolves.toBeUndefined();
          finishRequest();
          await settled;
        });
        expectRecoveryGone();
      },
    );

    it.each(["token removed", "different user", "SAML generation"])(
      "clears recovery before cross-tab navigation on %s",
      async (eventType) => {
        seedOnboarding(key);
        await renderProvider();
        const redirects = captureNavigation();
        const generation = eventType === "SAML generation";
        const newValue = generation
          ? "fake-other-generation"
          : eventType === "different user"
            ? `fake.${window.btoa(JSON.stringify({ user_id: "fake-owner-b" }))}.fake`
            : null;
        act(() => {
          window.dispatchEvent(
            new StorageEvent("storage", {
              key: generation ? SAML_AUTH_GENERATION_KEY : "accessToken",
              newValue,
            }),
          );
        });
        expect(redirects).toEqual([
          {
            url: generation ? "/dashboard/develop" : "/auth/jwt/login",
            recovery: null,
            stash: undefined,
          },
        ]);
        expect(auth.unauthenticated).toBe(true);
        expect(mocks.queryClient.clear).toHaveBeenCalled();
        expectRecoveryGone();
      },
    );

    it.each(["same-user refresh", "different-user sign-in"])(
      "enforces org-less cross-tab isolation for %s",
      async (change) => {
        seedOnboarding(key);
        const persisted = sessionStorage.getItem(onboardingStorageKey);
        const user = { id: "fake-owner-a", requires_org_setup: true };
        mocks.get.mockResolvedValueOnce({ data: user });
        localStorage.setItem("accessToken", "fake-current-session");
        render(
          <AuthProvider>
            <Consumer />
          </AuthProvider>,
        );
        await waitFor(() => expect(auth.authenticated).toBe(true));
        expect(auth.user).toMatchObject(user);
        const markerBeforeEvent = sessionStorage.getItem(SS_KEY_USER_ID);
        const redirects = captureNavigation();
        const sameUser = change === "same-user refresh";
        const newValue = `fake.${window.btoa(
          JSON.stringify({
            user_id: sameUser ? user.id : "fake-owner-b",
            jti: "fake-reissued-token",
          }),
        )}.fake`;
        expect(newValue).not.toBe("fake-current-session");
        // A storage event is delivered to the old tab after the other tab
        // has persisted its new token. Token bytes change in BOTH cases.
        localStorage.setItem("accessToken", newValue);
        act(() => {
          window.dispatchEvent(
            new StorageEvent("storage", {
              key: "accessToken",
              oldValue: "fake-current-session",
              newValue,
            }),
          );
        });
        if (sameUser) {
          expect(redirects).toEqual([]);
          expect(auth.authenticated).toBe(true);
          expect(auth.user).toMatchObject(user);
          expect(mocks.queryClient.clear).not.toHaveBeenCalled();
          expect(sessionStorage.getItem("auth_error")).toBeNull();
          expect(sessionStorage.getItem(onboardingStorageKey)).toBe(persisted);
          expect(window[stashKey].params.get(key)).toBe("fake-onboarding");
          expect(sessionStorage.getItem(SS_KEY_USER_ID)).toBe(user.id);
          expect(localStorage.getItem("accessToken")).toBe(newValue);
          visit("/auth/jwt/register");
          expect(readAuthRedirectParams().get(key)).toBe("fake-onboarding");
        } else {
          expect(redirects).toEqual([
            { url: "/auth/jwt/login", recovery: null, stash: undefined },
          ]);
          expect(auth.unauthenticated).toBe(true);
          expect(mocks.queryClient.clear).toHaveBeenCalledTimes(1);
          expect(sessionStorage.getItem(SS_KEY_USER_ID)).toBeNull();
          expect(sessionStorage.getItem("auth_error")).toBe(
            "Another user logged in from a different tab. Please log in again.",
          );
          expectRecoveryGone();
        }
        // This proves the preservation case exercised the ID comparison,
        // rather than passing only because the tab lacked a marker.
        expect(markerBeforeEvent).toBe(user.id);
      },
    );

    it("SAML bootstrap invalidates abandoned registration when publishing a new generation", () => {
      seedOnboarding(key);
      visit(
        "/dashboard/develop?sso_token=fake-saml&auth=saml&next=%2Fdashboard%2Fruns",
      );
      expect(bootstrapSamlSession()).toBe(true);
      expect(localStorage.getItem("accessToken")).toBe("fake-saml");
      expect(sessionStorage.getItem(SAML_AUTH_GENERATION_KEY)).toBe(
        "fake-generation",
      );
      expect(sessionStorage.getItem("fai_saml_next")).toBe("/dashboard/runs");
      expectRecoveryGone();
    });

    it("does not clear recovery for a non-SAML bootstrap or same-user cross-tab update", async () => {
      seedOnboarding(key);
      const persisted = window.sessionStorage.getItem(onboardingStorageKey);
      expect(bootstrapSamlSession()).toBe(false);
      await renderProvider();
      const redirects = captureNavigation();
      act(() => {
        window.dispatchEvent(
          new StorageEvent("storage", {
            key: "accessToken",
            newValue: `fake.${window.btoa(JSON.stringify({ user_id: "fake-owner-a" }))}.fake`,
          }),
        );
      });
      expect(redirects).toEqual([]);
      expect(auth.authenticated).toBe(true);
      expect(window.sessionStorage.getItem(onboardingStorageKey)).toBe(
        persisted,
      );
      visit("/auth/jwt/register");
      expect(readAuthRedirectParams().get(key)).toBe("fake-onboarding");
    });
  },
);
