import React, { StrictMode } from "react";
import { act, cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  flows,
  resetIntake,
  runIntake,
  stashKey,
  visit,
} from "./auth-url-fixtures";
import AuthGuard from "../guard/auth-guard";
import SOSLoginPage from "src/pages/SOSLoginPage";
import { bootstrapSamlSession } from "../saml-bootstrap";
import {
  readAuthRedirectParams,
  useAuthRedirectParams,
} from "../auth-url-intake";

const mocks = vi.hoisted(() => ({
  initialize: vi.fn(),
  replace: vi.fn(),
  navigate: vi.fn(),
  authenticated: false,
  axios: { defaults: { headers: { common: {} } } },
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({
    loading: false,
    authenticated: mocks.authenticated,
    method: "jwt",
    initialize: mocks.initialize,
  }),
}));
vi.mock("src/routes/hooks", () => ({
  useRouter: () => ({ replace: mocks.replace }),
}));
vi.mock("react-router-dom", () => ({
  useNavigate: () => mocks.navigate,
  useSearchParams: () => [new URLSearchParams(window.location.search)],
}));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ isReady: false }),
}));
vi.mock("src/hooks/useDeploymentMode", () => ({
  usePostLoginPath: () => "/dashboard/develop",
}));
vi.mock("src/components/loading-screen", () => ({ SplashScreen: () => null }));
vi.mock("src/utils/googleAds", () => ({ trackSignupConversion: vi.fn() }));
vi.mock("src/utils/redditAds", () => ({ trackRedditSignup: vi.fn() }));
vi.mock("src/utils/twitterAds", () => ({ trackTwitterSignup: vi.fn() }));
vi.mock("src/utils/axios", () => ({ default: mocks.axios }));

function captureDocumentNavigation() {
  const browser = window;
  const destinations = [];
  const locationView = {
    get search() {
      return browser.location.search;
    },
    get pathname() {
      return browser.location.pathname;
    },
    get href() {
      return browser.location.href;
    },
    set href(value) {
      destinations.push(value);
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
  return destinations;
}

function enter(pathname, query, source) {
  visit(`${pathname}?${query}&keep=1#stay`);
  if (source === "stash") {
    runIntake();
    expect(window.location.search).toBe("?keep=1");
    expect(window[stashKey]).toBeDefined();
  }
}

beforeEach(() => {
  vi.clearAllMocks();
  resetIntake();
  // Explicit jsdom storage avoids Node's experimental global storage.
  vi.stubGlobal("localStorage", window.localStorage);
  vi.stubGlobal("sessionStorage", window.sessionStorage);
  localStorage.clear();
  sessionStorage.clear();
  mocks.authenticated = false;
  mocks.axios.defaults.headers.common = {};
  vi.stubGlobal("crypto", { randomUUID: () => "fake-generation" });
});
afterEach(async () => {
  cleanup();
  await act(async () => {});
  vi.useRealTimers();
  vi.unstubAllGlobals();
  resetIntake();
});

describe.each(["stash", "URL fallback"])(
  "auth redirect readers using %s",
  (source) => {
    it.each(
      flows.filter(([name]) =>
        ["Google", "GitHub", "Microsoft", "AWS launch"].includes(name),
      ),
    )(
      "%s sets base tokens and preserves a stored returnTo through StrictMode and guard rechecks",
      (name, pathname, query) => {
        enter(pathname, query, source);
        localStorage.setItem("redirectUrl", "/shared/fake-return");
        const destinations = captureDocumentNavigation();
        const view = render(
          <StrictMode>
            <AuthGuard>
              <span>Protected</span>
            </AuthGuard>
          </StrictMode>,
        );
        const params = new URLSearchParams(query);
        expect(localStorage.getItem("accessToken"), name).toBe(
          params.get("sso_token") || params.get("access_token"),
        );
        expect(localStorage.getItem("refreshToken")).toBe(
          params.get("refresh_token"),
        );
        expect(localStorage.getItem("isNewOAuthSignup")).toBe(
          params.get("is_new_user") === "true" ? "1" : null,
        );
        expect(localStorage.getItem("redirectUrl")).toBeNull();
        expect(localStorage.getItem("initial-render")).toBe("done");
        expect(destinations).toEqual(["/shared/fake-return"]);
        view.rerender(
          <StrictMode>
            <AuthGuard>
              <span>Protected again</span>
            </AuthGuard>
          </StrictMode>,
        );
        expect(destinations).toEqual(["/shared/fake-return"]);
        expect(mocks.replace).not.toHaveBeenCalled();
        expect(mocks.initialize).not.toHaveBeenCalled();
        if (source === "stash") expect(window[stashKey]).toBeDefined();
      },
    );

    it.each([null, "//invalid.example"])(
      "uses the original pathname when returnTo is %s",
      (returnTo) => {
        enter(
          "/dashboard/develop",
          "sso_token=fake-oauth&is_new_user=false&next=%2Fignored",
          source,
        );
        if (returnTo) localStorage.setItem("redirectUrl", returnTo);
        const destinations = captureDocumentNavigation();
        render(
          <StrictMode>
            <AuthGuard>
              <span>Protected</span>
            </AuthGuard>
          </StrictMode>,
        );
        expect(destinations).toEqual(["/dashboard/develop"]);
        expect(localStorage.getItem("accessToken")).toBe("fake-oauth");
      },
    );

    it("SAML bootstrap replaces the old session, retains safe next and completes only once", () => {
      enter(
        "/dashboard/develop",
        "sso_token=fake-saml&auth=saml&next=%2Fdashboard%2Fruns",
        source,
      );
      localStorage.setItem("refreshToken", "fake-old-refresh");
      localStorage.setItem("sosMode", "true");
      localStorage.setItem("redirectUrl", "/old");
      sessionStorage.setItem("organizationId", "fake-old-org");
      mocks.axios.defaults.headers.common["X-Organization-Id"] = "fake-old-org";
      expect(bootstrapSamlSession()).toBe(true);
      expect(localStorage.getItem("accessToken")).toBe("fake-saml");
      expect(localStorage.getItem("refreshToken")).toBeNull();
      expect(localStorage.getItem("sosMode")).toBeNull();
      expect(localStorage.getItem("redirectUrl")).toBeNull();
      expect(sessionStorage.getItem("organizationId")).toBeNull();
      expect(sessionStorage.getItem("fai_saml_next")).toBe("/dashboard/runs");
      expect(mocks.axios.defaults.headers.common).toEqual({
        Authorization: "Bearer fake-saml",
      });
      expect(window.location.pathname).toBe("/dashboard/develop");
      expect(window.location.search).toBe("");
      expect(window[stashKey]).toBeUndefined();
      expect(bootstrapSamlSession()).toBe(false);
      mocks.authenticated = true;
      render(
        <StrictMode>
          <AuthGuard>
            <span>Protected</span>
          </AuthGuard>
        </StrictMode>,
      );
      expect(mocks.replace).toHaveBeenCalledWith("/dashboard/runs");
      expect(sessionStorage.getItem("fai_saml_next")).toBeNull();
    });

    it("SOS stores both tokens and navigates once after StrictMode effect replay", () => {
      vi.useFakeTimers();
      enter("/sos", "access=fake-sos&refresh=fake-sos-refresh", source);
      const view = render(
        <StrictMode>
          <SOSLoginPage />
        </StrictMode>,
      );
      expect(localStorage.getItem("sosMode")).toBe("true");
      expect(localStorage.getItem("accessToken")).toBe("fake-sos");
      expect(localStorage.getItem("refreshToken")).toBe("fake-sos-refresh");
      view.rerender(
        <StrictMode>
          <SOSLoginPage />
        </StrictMode>,
      );
      expect(mocks.navigate).not.toHaveBeenCalled();
      act(() => vi.advanceTimersByTime(1000));
      expect(mocks.navigate).toHaveBeenCalledTimes(1);
      expect(mocks.navigate).toHaveBeenCalledWith("/dashboard/develop");
      expect(window[stashKey]).toBeUndefined();
    });
  },
);

describe.each(["stash", "URL fallback"])(
  "reader snapshot using %s",
  (source) => {
    it("releases the global handoff without clearing the mounted reader", () => {
      enter(
        "/auth/jwt/register",
        "onboarding_gcp_token=fake-onboarding",
        source,
      );
      const params = readAuthRedirectParams();
      params.release();
      expect(window[stashKey]).toBeUndefined();
      expect(params.get("onboarding_gcp_token")).toBe("fake-onboarding");
      params.release();
      expect(params.get("onboarding_gcp_token")).toBe("fake-onboarding");
      params.clear();
      expect(params.get("onboarding_gcp_token")).toBeNull();
    });

    it("still clears the guard's own credential on completion", () => {
      enter("/dashboard/develop", "sso_token=fake-oauth", source);
      const params = readAuthRedirectParams();
      expect(params.get("sso_token")).toBe("fake-oauth");
      params.clear();
      expect(window[stashKey]).toBeUndefined();
      expect(params.get("sso_token")).toBeNull();
    });
  },
);

it("an old reader release or clear cannot erase a newer handoff", () => {
  enter("/auth/jwt/register", "onboarding_token=fake-old", "stash");
  const oldReader = readAuthRedirectParams();
  delete window[stashKey];
  enter("/auth/jwt/register", "onboarding_gcp_token=fake-new", "stash");
  const currentHandoff = window[stashKey];
  oldReader.release();
  expect(window[stashKey]).toBe(currentHandoff);
  expect(oldReader.get("onboarding_token")).toBe("fake-old");
  oldReader.clear();
  expect(window[stashKey]).toBe(currentHandoff);
  expect(oldReader.get("onboarding_token")).toBeNull();
});

function RedirectReader() {
  useAuthRedirectParams();
  return null;
}

it.each([
  ["/dashboard", "sso_token"],
  ["/dashboard", "access_token"],
  ["/dashboard", "refresh_token"],
  ["/sos", "access"],
  ["/sos", "refresh"],
])(
  "releases abandoned session credential %s %s on unmount",
  async (path, key) => {
    enter(path, `${key}=fake-session`, "stash");
    const view = render(
      <StrictMode>
        <RedirectReader />
      </StrictMode>,
    );
    await act(async () => {});
    expect(window[stashKey].params.get(key)).toBe("fake-session");
    view.unmount();
    await act(async () => {});
    expect(window[stashKey]).toBeUndefined();
  },
);

it("retains only onboarding on unmount and an unrelated reader cannot consume it", async () => {
  enter(
    "/auth/jwt/register",
    "onboarding_token=fake-onboarding&sso_token=fake-session&auth=saml&next=%2Fx",
    "stash",
  );
  const view = render(<RedirectReader />);
  view.unmount();
  await act(async () => {});
  expect([...window[stashKey].params]).toEqual([
    ["onboarding_token", "fake-onboarding"],
  ]);
  visit("/dashboard");
  const unrelatedReader = readAuthRedirectParams();
  expect(unrelatedReader.get("onboarding_token")).toBeNull();
  unrelatedReader.clear();
  visit("/auth/jwt/register");
  const signupReader = readAuthRedirectParams();
  expect(signupReader.get("onboarding_token")).toBe("fake-onboarding");
  signupReader.release();
  expect(window[stashKey]).toBeUndefined();
});

it("SAML does not consume a pending OAuth handoff", () => {
  enter("/dashboard/develop", "sso_token=fake-oauth&is_new_user=true", "stash");
  expect(bootstrapSamlSession()).toBe(false);
  expect(window[stashKey].params.get("sso_token")).toBe("fake-oauth");
});

it("the stash wins over a later conflicting URL value", () => {
  enter("/dashboard/develop", "sso_token=fake-original", "stash");
  visit("/dashboard/develop?sso_token=fake-later");
  captureDocumentNavigation();
  render(
    <AuthGuard>
      <span>Protected</span>
    </AuthGuard>,
  );
  expect(localStorage.getItem("accessToken")).toBe("fake-original");
});

it("does not use a stale handoff after changing paths", async () => {
  enter("/dashboard/develop", "sso_token=fake-original", "stash");
  visit("/different");
  render(
    <AuthGuard>
      <span>Protected</span>
    </AuthGuard>,
  );
  expect(localStorage.getItem("accessToken")).toBeNull();
  expect(mocks.replace).toHaveBeenCalledWith(
    expect.stringContaining("returnTo=%2Fdifferent"),
  );
  cleanup();
  await act(async () => {});
  expect(window[stashKey]).toBeUndefined();
});
