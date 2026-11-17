import React, { StrictMode } from "react";
import { act, cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
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

const REDEEM_URL = "https://api.handoff.test/accounts/handoff/redeem/";
const mocks = vi.hoisted(() => ({
  initialize: vi.fn(),
  replace: vi.fn(),
  navigate: vi.fn(),
  documentReplace: vi.fn(),
  redeem: vi.fn(),
  authenticated: false,
  axios: { defaults: { headers: { common: {} } } },
}));
vi.mock("axios", () => ({ default: { post: mocks.redeem } }));
vi.mock("src/config-global", () => ({ HOST_API: "https://api.handoff.test" }));
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
    get hash() {
      return browser.location.hash;
    },
    get href() {
      return browser.location.href;
    },
    set href(value) {
      destinations.push(value);
    },
    replace(value) {
      mocks.documentReplace(value);
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

function enterHandoff(pathname, query, source) {
  visit(`${pathname}?${query}&keep=1#stay`);
  if (source === "first-script intake") runIntake();
  // Current producers send codes, not the legacy session keys that the first
  // script stashes. Exercise that real path, without inventing a code stash.
  expect(window.location.search).toBe(`?${query}&keep=1`);
  expect(window[stashKey]).toBeUndefined();
}

function pendingRedemption(access, refresh = null) {
  let resolve;
  mocks.redeem.mockReturnValue(new Promise((done) => {
    resolve = done;
  }));
  return async () => {
    await act(async () => {
      resolve({ data: { access, refresh } });
    });
  };
}

function expectRedemption(code, kind) {
  expect(mocks.redeem).toHaveBeenCalledTimes(1);
  expect(mocks.redeem).toHaveBeenCalledWith(
    REDEEM_URL,
    { code, kind },
    { headers: { "Content-Type": "application/json" } },
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.redeem.mockReset();
  mocks.redeem.mockRejectedValue(new Error("Unexpected handoff redemption"));
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

// CR-07 changed the transport, not the OAuth returnTo/once-only contract.
// Keep the legacy fixtures for the lower-level stash tests below, and give
// the real guard the code/body response pair that current callbacks produce.
describe.each(["first-script intake", "URL fallback"])(
  "code redirect readers using %s",
  (source) => {
    it.each([
      ["Google", "fake-google", true],
      ["GitHub", "fake-github", false],
      ["Microsoft", "fake-microsoft", true],
    ])(
      "%s sets base tokens and preserves a stored returnTo through StrictMode and guard rechecks",
      async (name, access, isNewUser) => {
        const code = `fake-${name.toLowerCase()}-code`;
        enterHandoff(
          "/dashboard/develop",
          `code=${code}&kind=sso&is_new_user=${isNewUser}`,
          source,
        );
        localStorage.setItem("redirectUrl", "/shared/fake-return");
        const complete = pendingRedemption(access);
        const destinations = captureDocumentNavigation();
        const view = render(
          <StrictMode>
            <AuthGuard>
              <span>Protected</span>
            </AuthGuard>
          </StrictMode>,
        );
        expectRedemption(code, "sso");
        expect(localStorage.getItem("accessToken")).toBeNull();
        expect(localStorage.getItem("redirectUrl")).toBe("/shared/fake-return");
        expect(destinations).toEqual([]);
        expect(window.location.search).toBe(`?is_new_user=${isNewUser}&keep=1`);
        await complete();
        expect(localStorage.getItem("accessToken"), name).toBe(access);
        expect(localStorage.getItem("refreshToken")).toBeNull();
        expect(localStorage.getItem("isNewOAuthSignup")).toBe(isNewUser ? "1" : null);
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
        await act(async () => {});
        expectRedemption(code, "sso");
        expect(destinations).toEqual(["/shared/fake-return"]);
        expect(mocks.replace).not.toHaveBeenCalled();
        expect(mocks.initialize).not.toHaveBeenCalled();
      },
    );

    it("AWS launch sets both tokens and preserves the marketplace landing through StrictMode and guard rechecks", async () => {
      enterHandoff(
        "/dashboard/develop",
        "code=fake-aws-code&kind=aws_marketplace&new_org=True",
        source,
      );
      // AWS is an account replacement, not an OAuth return to the old account.
      localStorage.setItem("accessToken", "fake-old-access");
      localStorage.setItem("refreshToken", "fake-old-refresh");
      localStorage.setItem("redirectUrl", "/shared/fake-return");
      sessionStorage.setItem("organizationId", "fake-old-org");
      const complete = pendingRedemption("fake-aws", "fake-aws-refresh");
      const destinations = captureDocumentNavigation();
      const view = render(
        <StrictMode>
          <AuthGuard>
            <span>Protected</span>
          </AuthGuard>
        </StrictMode>,
      );
      expectRedemption("fake-aws-code", "aws_marketplace");
      expect(localStorage.getItem("accessToken")).toBe("fake-old-access");
      expect(destinations).toEqual([]);
      await complete();
      expect(localStorage.getItem("accessToken")).toBe("fake-aws");
      expect(localStorage.getItem("refreshToken")).toBe("fake-aws-refresh");
      expect(localStorage.getItem("isNewOAuthSignup")).toBeNull();
      expect(localStorage.getItem("redirectUrl")).toBeNull();
      expect(sessionStorage.getItem("organizationId")).toBeNull();
      expect(destinations).toEqual(["/dashboard/develop?new_org=True&keep=1#stay"]);
      view.rerender(
        <StrictMode>
          <AuthGuard>
            <span>Protected again</span>
          </AuthGuard>
        </StrictMode>,
      );
      await act(async () => {});
      expectRedemption("fake-aws-code", "aws_marketplace");
      expect(destinations).toEqual(["/dashboard/develop?new_org=True&keep=1#stay"]);
      expect(mocks.replace).not.toHaveBeenCalled();
      expect(mocks.initialize).not.toHaveBeenCalled();
    });

    it.each([null, "//invalid.example"])(
      "uses the original pathname when returnTo is %s",
      async (returnTo) => {
        enterHandoff(
          "/dashboard/develop",
          "code=fake-oauth-code&kind=sso&is_new_user=false&next=%2Fignored",
          source,
        );
        if (returnTo) localStorage.setItem("redirectUrl", returnTo);
        const complete = pendingRedemption("fake-oauth");
        const destinations = captureDocumentNavigation();
        render(
          <StrictMode>
            <AuthGuard>
              <span>Protected</span>
            </AuthGuard>
          </StrictMode>,
        );
        expectRedemption("fake-oauth-code", "sso");
        expect(destinations).toEqual([]);
        await complete();
        expect(destinations).toEqual(["/dashboard/develop"]);
        expect(localStorage.getItem("accessToken")).toBe("fake-oauth");
        expect(localStorage.getItem("redirectUrl")).toBeNull();
        expect(mocks.replace).not.toHaveBeenCalled();
        expect(mocks.initialize).not.toHaveBeenCalled();
      },
    );

    it("SOS stores both tokens at document exit and navigates once after StrictMode effect replay", async () => {
      enterHandoff("/sos", "code=fake-sos-code", source);
      const complete = pendingRedemption("fake-sos", "fake-sos-refresh");
      const destinations = captureDocumentNavigation();
      const view = render(
        <StrictMode>
          <SOSLoginPage />
        </StrictMode>,
      );
      expectRedemption("fake-sos-code", "sos");
      expect(localStorage.getItem("sosMode")).toBeNull();
      expect(localStorage.getItem("accessToken")).toBeNull();
      expect(localStorage.getItem("refreshToken")).toBeNull();
      expect(destinations).toEqual([]);
      view.rerender(
        <StrictMode>
          <SOSLoginPage />
        </StrictMode>,
      );
      await complete();
      expectRedemption("fake-sos-code", "sos");
      expect(mocks.documentReplace).toHaveBeenCalledTimes(1);
      expect(mocks.documentReplace).toHaveBeenCalledWith("/dashboard/develop");
      expect(destinations).toEqual(["/dashboard/develop"]);
      expect(localStorage.getItem("accessToken")).toBeNull();
      expect(localStorage.getItem("refreshToken")).toBeNull();
      // jsdom cannot leave the document. Dispatch the browser event, rather
      // than teaching the navigation double to write the expected tokens.
      act(() => window.dispatchEvent(new Event("pagehide")));
      expect(localStorage.getItem("sosMode")).toBe("true");
      expect(localStorage.getItem("accessToken")).toBe("fake-sos");
      expect(localStorage.getItem("refreshToken")).toBe("fake-sos-refresh");
      view.rerender(
        <StrictMode>
          <SOSLoginPage />
        </StrictMode>,
      );
      await act(async () => {});
      expectRedemption("fake-sos-code", "sos");
      expect(destinations).toEqual(["/dashboard/develop"]);
      expect(mocks.documentReplace).toHaveBeenCalledTimes(1);
      expect(mocks.navigate).not.toHaveBeenCalled();
      expect(window[stashKey]).toBeUndefined();
    });
  },
);

describe.each(["stash", "URL fallback"])(
  "auth redirect readers using %s",
  (source) => {
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

it("the stash wins over a later conflicting URL value", async () => {
  // The first-script stash still wins for keys it owns. A later *code* would
  // instead mean a superseding sign-in, which the guard correctly refuses.
  enter("/dashboard/develop", "sso_token=fake-original&is_new_user=true", "stash");
  visit("/dashboard/develop?code=fake-original-code&kind=sso&sso_token=fake-later&is_new_user=false");
  const params = readAuthRedirectParams();
  expect(params.get("sso_token")).toBe("fake-original");
  expect(params.get("is_new_user")).toBe("true");
  const complete = pendingRedemption("fake-original");
  const destinations = captureDocumentNavigation();
  render(
    <AuthGuard>
      <span>Protected</span>
    </AuthGuard>,
  );
  expectRedemption("fake-original-code", "sso");
  expect(localStorage.getItem("accessToken")).toBeNull();
  await complete();
  expect(localStorage.getItem("accessToken")).toBe("fake-original");
  expect(localStorage.getItem("isNewOAuthSignup")).toBe("1");
  expect(destinations).toEqual(["/dashboard/develop"]);
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
