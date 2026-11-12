/* eslint-disable react/prop-types */
import React from "react";
import axiosPackage, { AxiosError } from "axios";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { clearOnboardingRecovery, readAuthRedirectParams } from "src/auth/auth-url-intake";
import { AuthProvider } from "src/auth/context/jwt/auth-provider";
import {
  clearTokens,
  getIsRefreshing,
  setIsRefreshing,
  setRefreshToken,
  setRememberMe,
  setSession,
} from "src/auth/context/jwt/utils";
import { useAuthContext } from "src/auth/hooks";
import JwtRegisterView from "src/sections/auth/jwt/jwt-register-view";
import JwtLoginView from "src/sections/auth/jwt/jwt-login-view";
import client, { endpoints } from "src/utils/axios";
import { getRecaptchaToken } from "src/utils/recaptchaService";
import { SS_KEY_USER_ID } from "src/utils/sessionKeys";
import {
  onboardingStorageKey,
  resetIntake,
  runIntake,
  stashKey,
  visit,
} from "./auth-url-fixtures";

const mocks = vi.hoisted(() => ({ queryClient: { clear: vi.fn() } }));
vi.mock("src/config-global", () => ({ HOST_API: "https://offline.invalid" }));
vi.mock("@tanstack/react-query", () => ({
  useQueryClient: () => mocks.queryClient,
  useMutation: () => ({ mutate: vi.fn() }),
}));
vi.mock("src/utils/Mixpanel", () => ({
  Events: {},
  PropertyName: {},
  trackEvent: vi.fn(),
  identifyUser: vi.fn(),
  resetUser: vi.fn(),
}));
vi.mock("src/utils/PostHog", () => ({
  identifyPostHogUser: vi.fn(),
  resetPostHogUser: vi.fn(),
}));
vi.mock("@sentry/react", () => ({ setUser: vi.fn() }));
vi.mock("src/utils/googleAds", () => ({ trackSignupConversion: vi.fn() }));
vi.mock("src/utils/redditAds", () => ({ trackRedditSignup: vi.fn() }));
vi.mock("src/utils/twitterAds", () => ({ trackTwitterSignup: vi.fn() }));
vi.mock("src/utils/recaptchaService", () => ({
  getRecaptchaToken: vi.fn(async () => "fake-recaptcha"),
}));
vi.mock("src/utils/workEmail", () => ({ isWorkEmail: () => true }));
vi.mock("src/utils/logger", () => ({
  default: { info: vi.fn(), error: vi.fn(), debug: vi.fn(), warn: vi.fn() },
}));
vi.mock("src/sections/falcon-ai/store/useFalconStore", () => ({
  default: { getState: () => ({ resetAll: vi.fn() }) },
}));
vi.mock("src/sections/omega/code/keyStore", () => ({ forgetKey: vi.fn() }));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("src/components/snackbar", () => ({
  enqueueSnackbar: vi.fn(),
  useSnackbar: () => ({ enqueueSnackbar: vi.fn() }),
}));
vi.mock("@simplewebauthn/browser", () => ({
  browserSupportsWebAuthn: () => false,
  startAuthentication: vi.fn(),
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/svg-color", () => ({ default: () => null }));
vi.mock("src/components/RegionSelect", () => ({ default: () => null }));
vi.mock("src/sections/auth/jwt/RightSectionAuth", () => ({
  default: () => null,
}));
vi.mock("src/sections/auth/jwt/password-sent-view", () => ({
  default: () => <p>Fake email confirmation</p>,
}));
vi.mock("src/hooks/useDeploymentMode", () => ({
  useDeploymentMode: () => ({ isOSS: false, isCloud: true, isSuccess: true }),
  usePostLoginPath: () => "/dashboard/develop",
}));
vi.mock("src/routes/components", async () => {
  const { Link } = await import("react-router-dom");
  return { RouterLink: Link };
});
vi.mock("src/components/hook-form", async () => {
  const { FormProvider, useFormContext } = await import("react-hook-form");
  return {
    default: ({ methods, onSubmit, children }) => (
      <FormProvider {...methods}>
        <form onSubmit={onSubmit}>{children}</form>
      </FormProvider>
    ),
    RHFTextField: ({ name, label }) => {
      const { register } = useFormContext();
      return (
        <label>
          {label}
          <input {...register(name)} />
        </label>
      );
    },
  };
});

// Real provider, auth context, registration form, interceptor and JWT utilities.
// Only transport/UI integrations are replaced; neither Axios instance can egress.
const originalAdapter = client.defaults.adapter;
const originalPackageAdapter = axiosPackage.defaults.adapter;
const transport = vi.fn();

beforeEach(() => {
  vi.clearAllMocks();
  resetIntake();
  clearOnboardingRecovery();
  vi.stubGlobal("localStorage", window.localStorage);
  vi.stubGlobal("sessionStorage", window.sessionStorage);
  localStorage.clear();
  setSession(null);
  setIsRefreshing(false);
  transport.mockReset();
  transport.mockImplementation(async () => {
    throw new Error("Unexpected mocked transport request");
  });
  client.defaults.adapter = transport;
  axiosPackage.defaults.adapter = async (config) => {
    if (config.url === "/accounts/aws-marketplace/onboarding-exchange/") {
      // These fixtures model the prepared capability with the same fake value.
      // The dedicated onboarding regression pins distinct redirect/form values.
      return {
        status: 200,
        statusText: "OK",
        headers: {},
        config,
        data: { onboarding_attempt: "fake-onboarding" },
      };
    }
    return transport(config);
  };
});
afterEach(async () => {
  cleanup();
  await act(async () => {});
  client.defaults.adapter = originalAdapter;
  axiosPackage.defaults.adapter = originalPackageAdapter;
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

function RegistrationAfterInitialize() {
  const { loading } = useAuthContext();
  return loading ? <p>Loading session</p> : <JwtRegisterView />;
}
function mountRegistration() {
  return render(
    <MemoryRouter initialEntries={[window.location.pathname]}>
      <AuthProvider>
        <RegistrationAfterInitialize />
      </AuthProvider>
    </MemoryRouter>,
  );
}
function mountLogin() {
  return render(
    <MemoryRouter initialEntries={["/auth/jwt/login"]}>
      <AuthProvider>
        <Routes>
          <Route path="/auth/jwt/login" element={<JwtLoginView />} />
          <Route path="/auth/jwt/org-removed" element={<p>Fake org setup</p>} />
        </Routes>
      </AuthProvider>
    </MemoryRouter>,
  );
}

function enter(key, remembered) {
  visit(`/auth/jwt/register?${key}=fake-onboarding`);
  runIntake();
  expect(window.location.search).toBe("");
  expect(sessionStorage.getItem(SS_KEY_USER_ID)).toBeNull();
  const persisted = sessionStorage.getItem(onboardingStorageKey);
  expect(persisted).not.toBeNull();
  if (remembered) {
    setRememberMe(true);
    setRefreshToken("fake-expired-refresh");
  }
  return persisted;
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
    get origin() {
      return browser.location.origin;
    },
    set href(url) {
      // Snapshot BEFORE navigation can unload the form and interrupt its catch.
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
  return { browser, redirects };
}
async function returnToRegistration(view, browser) {
  view.unmount();
  await act(async () => {});
  vi.stubGlobal("window", browser);
  window.dispatchEvent(new Event("pagehide"));
  visit("/auth/jwt/login");
  runIntake();
  expect(readAuthRedirectParams().get("onboarding_token")).toBeNull();
  expect(readAuthRedirectParams().get("onboarding_gcp_token")).toBeNull();
  window.dispatchEvent(new Event("pagehide"));
  visit("/auth/jwt/register");
  runIntake();
  return readAuthRedirectParams();
}

function expectSessionEnded() {
  expect(sessionStorage.getItem(SS_KEY_USER_ID)).toBeNull();
  expect(localStorage.getItem("accessToken")).toBeNull();
  expect(localStorage.getItem("refreshToken")).toBeNull();
  expect(localStorage.getItem("rememberMe")).toBeNull();
  expect(client.defaults.headers.common.Authorization).toBeUndefined();
  expect(getIsRefreshing()).toBe(false);
}

describe.each([
  ["AWS", "onboarding_token", endpoints.auth.awsSignUp],
  ["GCP", "onboarding_gcp_token", endpoints.auth.gcpSignUp],
])("%s guest marketplace HTTP 401 integration", (provider, key, endpoint) => {
  describe.each([false, true])("remembered refresh=%s", (remembered) => {
    it("preserves signup recovery when a stale bearer gets 401 before onboarding is checked", async () => {
      const persisted = enter(key, remembered);
      const { browser, redirects } = captureRedirects();
      transport.mockImplementationOnce(
        rejectHttp(401, "Invalid Token parsed: Token is invalid or expired"),
      );
      if (remembered)
        transport.mockImplementationOnce(
          rejectHttp(400, "Fake expired refresh"),
        );
      const view = mountRegistration();
      fireEvent.change(await screen.findByLabelText("Full Name"), {
        target: { value: "Fake Person" },
      });
      fireEvent.change(screen.getByLabelText("Business Email ID"), {
        target: { value: "fake@example.org" },
      });
      // The form mounted anonymously; a leftover bearer is not a session that
      // AuthProvider authenticated in this tab, and is rejected before the view.
      setSession("fake-earlier-session-access");
      expect(sessionStorage.getItem(SS_KEY_USER_ID)).toBeNull();
      await waitFor(() => expect(
        screen.getByRole("button", { name: "Continue", exact: true }),
      ).toBeEnabled());
      await act(async () => {
        fireEvent.click(
          screen.getByRole("button", { name: "Continue", exact: true }),
        );
      });
      await waitFor(() => expect(redirects.length).toBeGreaterThan(0));
      await waitFor(() =>
        expect(
          screen.getByRole("button", { name: "Continue", exact: true }),
        ).toBeEnabled(),
      );

      expect(
        transport.mock.calls.map(([config]) => config.url),
        provider,
      ).toEqual(
        remembered ? [endpoint, endpoints.auth.refreshToken] : [endpoint],
      );
      expect(transport.mock.calls[0][0].headers.Authorization).toBe(
        "Bearer fake-earlier-session-access",
      );
      expect(JSON.parse(transport.mock.calls[0][0].data)).toEqual({
        onboarding_token: "fake-onboarding",
        email: "fake@example.org",
        full_name: "Fake Person",
      });
      // Without a refresh token the real helper throws Error("No refresh token")
      // without statusCode. Do not replace this path with a normalized error mock.
      expect(getRecaptchaToken).toHaveBeenCalledWith("refresh");
      for (const redirect of redirects) {
        expect(redirect).toEqual({
          url: "/auth/jwt/login",
          recovery: persisted,
          stash: [[key, "fake-onboarding"]],
        });
      }
      expect(sessionStorage.getItem(onboardingStorageKey)).toBe(persisted);
      expect(window[stashKey].params.get(key)).toBe("fake-onboarding");
      expectSessionEnded();
      const later = await returnToRegistration(view, browser);
      expect(later.get(key)).toBe("fake-onboarding");
      expect(
        later.get(
          key === "onboarding_token"
            ? "onboarding_gcp_token"
            : "onboarding_token",
        ),
      ).toBeNull();
      expect(sessionStorage.getItem(onboardingStorageKey)).toBe(persisted);
    });

    it("clears recovery on a non-signup 401 after the real provider authenticated this tab", async () => {
      enter(key, remembered);
      localStorage.setItem("accessToken", "fake-current-session");
      transport.mockImplementationOnce(async (config) => ({
        status: 200,
        statusText: "OK",
        config,
        headers: {},
        data: { id: "fake-signed-in-user" },
      }));
      const view = mountRegistration();
      await screen.findByLabelText("Full Name");
      // The real initialize, not a manual seed, establishes this tab's marker.
      expect(sessionStorage.getItem(SS_KEY_USER_ID)).toBe(
        "fake-signed-in-user",
      );
      expect(sessionStorage.getItem(onboardingStorageKey)).not.toBeNull();
      visit("/dashboard/develop");
      const { browser, redirects } = captureRedirects();
      transport.mockImplementationOnce(rejectHttp(401, "Fake expired session"));
      if (remembered)
        transport.mockImplementationOnce(
          rejectHttp(400, "Fake expired refresh"),
        );

      const error = await client.get(endpoints.auth.me).catch((err) => err);

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
          ? [endpoints.auth.me, endpoints.auth.me, endpoints.auth.refreshToken]
          : [endpoints.auth.me, endpoints.auth.me],
      );
      expect(transport.mock.calls[1][0].method).toBe("get");
      expect(transport.mock.calls[1][0].headers.Authorization).toBe(
        "Bearer fake-current-session",
      );
      expect(redirects).toHaveLength(remembered ? 1 : 2);
      for (const redirect of redirects) {
        expect(redirect).toEqual({
          url: "/auth/jwt/login",
          recovery: null,
          stash: undefined,
        });
      }
      expect(sessionStorage.getItem(onboardingStorageKey)).toBeNull();
      expect(window[stashKey]).toBeUndefined();
      expect(localStorage.getItem("accessToken")).toBeNull();
      expect(localStorage.getItem("refreshToken")).toBeNull();
      expect(localStorage.getItem("rememberMe")).toBeNull();
      expect(client.defaults.headers.common.Authorization).toBeUndefined();
      expect(getIsRefreshing()).toBe(false);
      const later = await returnToRegistration(view, browser);
      expect(later.get("onboarding_token")).toBeNull();
      expect(later.get("onboarding_gcp_token")).toBeNull();
    });

    it.each(["initialize", "org_removed login"])(
      "clears recovery on a non-signup 401 for an org-less user after %s",
      async (entryPoint) => {
        enter(key, remembered);
        const user = { id: "fake-orgless-user", requires_org_setup: true };
        const success = (config, data) => ({
          status: 200,
          statusText: "OK",
          config,
          headers: {},
          data,
        });
        transport.mockImplementation(async (config) => {
          if (config.url === endpoints.auth.me) return success(config, user);
          if (config.url === endpoints.auth.login)
            return success(config, {
              access: "fake-orgless-session",
              ...(remembered ? { refresh: "fake-orgless-refresh" } : {}),
              requires_org_setup: true,
            });
          throw new Error("Unexpected org-less authentication request");
        });
        let view;
        if (entryPoint === "initialize") {
          localStorage.setItem("accessToken", "fake-orgless-session");
          view = mountRegistration();
          await screen.findByLabelText("Full Name");
        } else {
          visit("/auth/jwt/login");
          view = mountLogin();
          fireEvent.change(await screen.findByLabelText("Email address"), {
            target: { value: "fake@example.org" },
          });
          fireEvent.change(screen.getByLabelText("Password"), {
            target: { value: "fake-password" },
          });
          await act(async () => {
            fireEvent.click(
              screen.getByRole("button", { name: "Continue", exact: true }),
            );
          });
          await screen.findByText("Fake org setup");
        }
        expect(sessionStorage.getItem(onboardingStorageKey)).not.toBeNull();
        expect(localStorage.getItem("accessToken")).toBe(
          "fake-orgless-session",
        );
        const markerBefore401 = sessionStorage.getItem(SS_KEY_USER_ID);
        const authRequests = transport.mock.calls.map(([config]) => config.url);
        visit("/dashboard/develop");
        const { browser, redirects } = captureRedirects();
        transport.mockReset();
        transport.mockImplementation(rejectHttp(500, "Unexpected request"));
        transport.mockImplementationOnce(
          rejectHttp(401, "Fake expired session"),
        );
        if (remembered)
          transport.mockImplementationOnce(
            rejectHttp(400, "Fake expired refresh"),
          );

        const error = await client.get(endpoints.auth.me).catch((err) => err);

        // RED at the accepted product: recovery survives because the real
        // org-less entry point has not established a marker. Never seed it here.
        expect(sessionStorage.getItem(onboardingStorageKey)).toBeNull();
        expect(window[stashKey]).toBeUndefined();
        expect(markerBefore401).toBe(user.id);
        expect(authRequests).toEqual(
          entryPoint === "initialize"
            ? [endpoints.auth.me]
            : [endpoints.auth.login, endpoints.auth.me],
        );
        expect(transport.mock.calls.map(([config]) => config.url)).toEqual(
          remembered
            ? [endpoints.auth.me, endpoints.auth.refreshToken]
            : [endpoints.auth.me],
        );
        expect(transport.mock.calls[0][0].method).toBe("get");
        expect(transport.mock.calls[0][0].headers.Authorization).toBe(
          "Bearer fake-orgless-session",
        );
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
        expect(redirects).toHaveLength(remembered ? 1 : 2);
        for (const redirect of redirects) {
          expect(redirect).toEqual({
            url: "/auth/jwt/login",
            recovery: null,
            stash: undefined,
          });
        }
        expect(localStorage.getItem("accessToken")).toBeNull();
        expect(localStorage.getItem("refreshToken")).toBeNull();
        expect(localStorage.getItem("rememberMe")).toBeNull();
        expect(client.defaults.headers.common.Authorization).toBeUndefined();
        expect(getIsRefreshing()).toBe(false);
        const later = await returnToRegistration(view, browser);
        expect(later.get("onboarding_token")).toBeNull();
        expect(later.get("onboarding_gcp_token")).toBeNull();
      },
    );

    it("preserves fresh landing recovery when real provider initialization gets user-info 401", async () => {
      const persisted = enter(key, remembered);
      localStorage.setItem("accessToken", "fake-earlier-session-access");
      const { browser, redirects } = captureRedirects();
      transport.mockImplementationOnce(
        rejectHttp(401, "Fake expired earlier session"),
      );
      if (remembered)
        transport.mockImplementationOnce(
          rejectHttp(400, "Fake expired refresh"),
        );
      const view = mountRegistration();
      await screen.findByLabelText("Full Name");

      expect(transport.mock.calls.map(([config]) => config.url)).toEqual(
        remembered
          ? [endpoints.auth.me, endpoints.auth.refreshToken]
          : [endpoints.auth.me],
      );
      expect(transport.mock.calls[0][0].headers.Authorization).toBe(
        "Bearer fake-earlier-session-access",
      );
      expect(redirects.length).toBeGreaterThan(0);
      for (const redirect of redirects) {
        expect(redirect).toEqual({
          url: "/auth/jwt/login",
          recovery: persisted,
          stash: [[key, "fake-onboarding"]],
        });
      }
      expectSessionEnded();
      const later = await returnToRegistration(view, browser);
      expect(later.get(key)).toBe("fake-onboarding");
      expect(sessionStorage.getItem(onboardingStorageKey)).toBe(persisted);
    });
  });
});
