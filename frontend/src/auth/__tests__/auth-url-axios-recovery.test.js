import { AxiosError } from "axios";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { readAuthRedirectParams } from "src/auth/auth-url-intake";
import {
  clearTokens,
  getIsRefreshing,
  setIsRefreshing,
  setRefreshToken,
  setRememberMe,
  setSession,
} from "src/auth/context/jwt/utils";
import client, { endpoints } from "src/utils/axios";
import { SS_KEY_USER_ID } from "src/utils/sessionKeys";
import {
  onboardingStorageKey,
  resetIntake,
  runIntake,
  stashKey,
  visit,
} from "./auth-url-fixtures";

vi.mock("src/config-global", () => ({ HOST_API: "https://offline.invalid" }));
vi.mock("src/utils/Mixpanel", () => ({ resetUser: vi.fn() }));
vi.mock("src/utils/logger", () => ({
  default: { debug: vi.fn(), warn: vi.fn(), error: vi.fn() },
}));
vi.mock("src/utils/recaptchaService", () => ({
  getRecaptchaToken: vi.fn(async () => "fake-recaptcha"),
}));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));

// Keep the real Axios instance, interceptors and JWT utilities. Only transport
// and external integrations are replaced; unexpected requests fail closed.
const originalAdapter = client.defaults.adapter;
const transport = vi.fn();
const payload = {
  onboarding_token: "fake-onboarding",
  email: "fake@example.org",
  full_name: "Fake Person",
};

beforeEach(() => {
  vi.clearAllMocks();
  resetIntake();
  vi.stubGlobal("localStorage", window.localStorage);
  localStorage.clear();
  setSession(null);
  setIsRefreshing(false);
  transport.mockReset();
  transport.mockImplementation(async () => {
    throw new Error("Unexpected mocked transport request");
  });
  client.defaults.adapter = transport;
});

afterEach(() => {
  client.defaults.adapter = originalAdapter;
  setSession(null);
  clearTokens();
  setIsRefreshing(false);
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  resetIntake();
});

const rejectHttp = (status, detail) => (config) =>
  Promise.reject(
    new AxiosError(
      `Request failed with status code ${status}`,
      AxiosError.ERR_BAD_REQUEST,
      config,
      undefined,
      { status, config, headers: {}, data: { detail } },
    ),
  );

const respond = (data) => async (config) => ({
  status: 200,
  statusText: "OK",
  config,
  headers: {},
  data,
});

function enter(key) {
  visit(`/auth/jwt/register?${key}=fake-onboarding`);
  runIntake();
  expect(window.location.search).toBe("");
  expect(window.sessionStorage.getItem(onboardingStorageKey)).not.toBeNull();
  expect(window[stashKey].params.get(key)).toBe("fake-onboarding");
  return readAuthRedirectParams();
}

function captureRedirects() {
  const browser = window;
  const redirects = [];
  const locationView = {
    get href() {
      return browser.location.href;
    },
    get pathname() {
      return browser.location.pathname;
    },
    get search() {
      return browser.location.search;
    },
    set href(url) {
      // Copy the values at navigation, not mutable references that later cleanup
      // could change. The no-refresh path redirects before throwing plain Error.
      redirects.push({
        url,
        recovery: browser.sessionStorage.getItem(onboardingStorageKey),
        stash: browser[stashKey] ? [...browser[stashKey].params] : undefined,
      });
    },
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

function expectRecoveryCleared(redirects) {
  expect(redirects.length).toBeGreaterThan(0);
  for (const redirect of redirects) {
    expect(redirect).toEqual({
      url: "/auth/jwt/login",
      recovery: null,
      stash: undefined,
    });
  }
  expect(window.sessionStorage.getItem(onboardingStorageKey)).toBeNull();
  expect(window[stashKey]).toBeUndefined();
  visit("/auth/jwt/register");
  const later = readAuthRedirectParams();
  expect(later.get("onboarding_token")).toBeNull();
  expect(later.get("onboarding_gcp_token")).toBeNull();
  expect(localStorage.getItem("accessToken")).toBeNull();
  expect(localStorage.getItem("refreshToken")).toBeNull();
  expect(localStorage.getItem("rememberMe")).toBeNull();
  expect(client.defaults.headers.common.Authorization).toBeUndefined();
  expect(getIsRefreshing()).toBe(false);
}

describe.each([
  ["AWS", "onboarding_token", endpoints.auth.awsSignUp],
  ["GCP", "onboarding_gcp_token", endpoints.auth.gcpSignUp],
])(
  "%s marketplace recovery through the real Axios interceptor",
  (provider, key, endpoint) => {
    it("clears recovery before a signed-in HTTP 401 redirect without statusCode", async () => {
      const mountedReader = enter(key);
      setSession("fake-expired-access");
      // AuthProvider.login writes this only after user-info succeeds.
      sessionStorage.setItem(SS_KEY_USER_ID, "fake-signed-in-user");
      const redirects = captureRedirects();
      transport.mockImplementationOnce(
        rejectHttp(401, "Fake unauthorized signup"),
      );

      const error = await client.post(endpoint, payload).catch((err) => err);

      // This is the actual refreshTokenRequest failure, not a normalized mock.
      expect(error).toBeInstanceOf(Error);
      expect(error.message).toBe("No refresh token");
      expect(error.statusCode).toBeUndefined();
      expect(transport).toHaveBeenCalledTimes(1);
      expect(transport.mock.calls[0][0].url, provider).toBe(endpoint);
      expectRecoveryCleared(redirects);
      expect(mountedReader.get(key)).toBe("fake-onboarding");
    });

    it("clears recovery before redirect when a remembered session cannot refresh", async () => {
      enter(key);
      setSession("fake-expired-access");
      sessionStorage.setItem(SS_KEY_USER_ID, "fake-signed-in-user");
      setRememberMe(true);
      setRefreshToken("fake-expired-refresh");
      const redirects = captureRedirects();
      transport
        .mockImplementationOnce(rejectHttp(401, "Fake unauthorized signup"))
        .mockImplementationOnce(rejectHttp(400, "Fake expired refresh"));

      await expect(client.post(endpoint, payload)).rejects.toMatchObject({
        statusCode: 400,
        detail: "Fake expired refresh",
      });

      expect(transport.mock.calls.map(([config]) => config.url)).toEqual([
        endpoint,
        endpoints.auth.refreshToken,
      ]);
      expectRecoveryCleared(redirects);
    });

    describe.each([false, true])(
      "earlier session with rememberMe=%s",
      (remembered) => {
        it.each(["Sign up link", "history Back"])(
          "preserves a fresh marketplace landing through user-info 401 and %s",
          async (returnMethod) => {
            // A new tab has the earlier session's localStorage, but no user marker.
            // GuestGuard has not mounted the registration reader at this point.
            localStorage.setItem("accessToken", "fake-expired-access");
            expect(sessionStorage.getItem(SS_KEY_USER_ID)).toBeNull();
            if (remembered) {
              setRememberMe(true);
              setRefreshToken("fake-expired-refresh");
            }
            visit(`/auth/jwt/register?${key}=fake-onboarding`);
            runIntake();
            const persisted =
              window.sessionStorage.getItem(onboardingStorageKey);
            expect(persisted).not.toBeNull();
            expect(window.location.search).toBe("");
            const browser = window;
            const redirects = captureRedirects();
            transport.mockImplementationOnce(
              rejectHttp(401, "Fake expired session"),
            );
            if (remembered) {
              transport.mockImplementationOnce(
                rejectHttp(400, "Fake expired refresh"),
              );
            }

            // Match AuthProvider.initialize's request using the leftover token.
            const error = await client
              .get(endpoints.auth.me, {
                headers: {
                  Authorization: `Bearer ${localStorage.getItem("accessToken")}`,
                },
              })
              .catch((err) => err);

            if (remembered) {
              expect(error).toMatchObject({
                statusCode: 400,
                detail: "Fake expired refresh",
              });
            } else {
              expect(error).toBeInstanceOf(Error);
              expect(error.message).toBe("No refresh token");
              expect(error.statusCode).toBeUndefined();
            }
            expect(transport.mock.calls.map(([config]) => config.url)).toEqual(
              remembered
                ? [endpoints.auth.me, endpoints.auth.refreshToken]
                : [endpoints.auth.me],
            );
            expect(transport.mock.calls[0][0].headers.Authorization).toBe(
              "Bearer fake-expired-access",
            );
            expect(redirects.length).toBeGreaterThan(0);
            for (const redirect of redirects) {
              expect(redirect).toEqual({
                url: "/auth/jwt/login",
                recovery: persisted,
                stash: [[key, "fake-onboarding"]],
              });
            }
            expect(localStorage.getItem("accessToken")).toBeNull();
            expect(localStorage.getItem("refreshToken")).toBeNull();
            expect(localStorage.getItem("rememberMe")).toBeNull();
            expect(
              client.defaults.headers.common.Authorization,
            ).toBeUndefined();
            expect(getIsRefreshing()).toBe(false);

            // Simulate the existing full-page login redirect, then its Sign up
            // link or history Back. Only tab storage survives the document change.
            vi.stubGlobal("window", browser);
            window.dispatchEvent(new Event("pagehide"));
            expect(window[stashKey]).toBeUndefined();
            window.history.pushState({}, "", "/auth/jwt/login");
            runIntake();
            expect(readAuthRedirectParams().get(key)).toBeNull();
            window.dispatchEvent(new Event("pagehide"));
            if (returnMethod === "Sign up link") {
              window.history.pushState({}, "", "/auth/jwt/register");
            } else {
              await new Promise((resolve) => {
                window.addEventListener("popstate", resolve, { once: true });
                window.history.back();
              });
            }
            runIntake();
            expect(window.location.pathname).toBe("/auth/jwt/register");
            expect(window.location.search).toBe("");
            expect(readAuthRedirectParams().get(key)).toBe("fake-onboarding");
            expect(window.sessionStorage.getItem(onboardingStorageKey)).toBe(
              persisted,
            );
          },
        );
      },
    );

    it("keeps active registration reload recovery when HTTP 401 refresh succeeds", async () => {
      enter(key);
      const persisted = window.sessionStorage.getItem(onboardingStorageKey);
      setSession("fake-expired-access");
      sessionStorage.setItem(SS_KEY_USER_ID, "fake-signed-in-user");
      setRememberMe(true);
      setRefreshToken("fake-valid-refresh");
      const redirects = captureRedirects();
      const data = { result: { message: "Check email" } };
      transport
        .mockImplementationOnce(rejectHttp(401, "Fake unauthorized signup"))
        .mockImplementationOnce(respond({ access: "fake-refreshed-access" }))
        .mockImplementationOnce(respond(data));

      await expect(client.post(endpoint, payload)).resolves.toMatchObject({
        data,
      });

      expect(transport.mock.calls.map(([config]) => config.url)).toEqual([
        endpoint,
        endpoints.auth.refreshToken,
        endpoint,
      ]);
      expect(redirects).toEqual([]);
      expect(localStorage.getItem("accessToken")).toBe("fake-refreshed-access");
      expect(localStorage.getItem("refreshToken")).toBe("fake-valid-refresh");
      expect(getIsRefreshing()).toBe(false);
      expect(window.sessionStorage.getItem(onboardingStorageKey)).toBe(
        persisted,
      );
      expect(window[stashKey].params.get(key)).toBe("fake-onboarding");
      // Axios alone must not consume registration; that belongs to the form.
      window.dispatchEvent(new Event("pagehide"));
      expect(window[stashKey]).toBeUndefined();
      expect(readAuthRedirectParams().get(key)).toBe("fake-onboarding");
    });
  },
);
