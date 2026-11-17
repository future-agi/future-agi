/* eslint-disable react/prop-types */
import React, { StrictMode } from "react";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { BrowserRouter, Link, Route, Routes } from "react-router-dom";
import { clearOnboardingRecovery, readAuthRedirectParams } from "src/auth/auth-url-intake";
import JwtRegisterView from "src/sections/auth/jwt/jwt-register-view";
import {
  onboardingStorageKey,
  resetIntake,
  runIntake,
  stashKey,
  visit,
} from "./auth-url-fixtures";

const mocks = vi.hoisted(() => ({
  exchange: vi.fn(),
  marketplaceRegister: vi.fn(),
  register: vi.fn(),
  login: vi.fn(),
  navigate: vi.fn(),
  get: vi.fn(),
  isOSS: false,
  readers: [],
}));
vi.mock("src/auth/auth-url-intake", async (importOriginal) => {
  const intake = await importOriginal();
  return {
    ...intake,
    useAuthRedirectParams: () => {
      const reader = intake.useAuthRedirectParams();
      mocks.readers.push(reader);
      return reader;
    },
  };
});
vi.mock("axios", () => ({ default: { post: mocks.exchange } }));
vi.mock("src/auth/hooks", () => ({ useAuthContext: () => mocks }));
vi.mock("react-router-dom", async (importOriginal) => ({
  ...(await importOriginal()),
  useNavigate: () => mocks.navigate,
  useLocation: () => ({ search: window.location.search }),
}));
vi.mock("src/hooks/useDeploymentMode", () => ({
  useDeploymentMode: () => ({
    isOSS: mocks.isOSS,
    isCloud: !mocks.isOSS,
    isSuccess: true,
  }),
  usePostLoginPath: () => "/dashboard/develop",
}));
vi.mock("src/utils/axios", () => ({
  default: { get: mocks.get },
  endpoints: {
    auth: {
      awsSignUp: "/fake/aws-signup",
      gcpSignUp: "/fake/gcp-signup",
      service: (provider, token) => ({ provider, token }),
    },
  },
}));
vi.mock("src/utils/Mixpanel", () => ({
  Events: {},
  PropertyName: {},
  trackEvent: vi.fn(),
}));
vi.mock("src/utils/googleAds", () => ({ trackSignupConversion: vi.fn() }));
vi.mock("src/utils/redditAds", () => ({ trackRedditSignup: vi.fn() }));
vi.mock("src/utils/twitterAds", () => ({ trackTwitterSignup: vi.fn() }));
vi.mock("src/utils/recaptchaService", () => ({
  getRecaptchaToken: async () => "fake-recaptcha",
}));
vi.mock("src/utils/workEmail", () => ({ isWorkEmail: () => true }));
vi.mock("src/utils/logger", () => ({
  default: { info: vi.fn(), error: vi.fn(), debug: vi.fn() },
}));
vi.mock("src/components/snackbar", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/svg-color", () => ({ default: () => null }));
vi.mock("src/components/RegionSelect", () => ({ default: () => null }));
vi.mock("src/routes/components", async () => {
  const { Link: RoutedLink, useInRouterContext } = await import(
    "react-router-dom"
  );
  return {
    RouterLink: ({ children, to }) => {
      const inRouter = useInRouterContext();
      return inRouter ? (
        <RoutedLink to={to}>{children}</RoutedLink>
      ) : (
        <span>{children}</span>
      );
    },
  };
});
vi.mock("src/sections/auth/jwt/RightSectionAuth", () => ({
  default: () => null,
}));
vi.mock("src/sections/auth/jwt/password-sent-view", () => ({
  default: ({ setRegisterSuccess }) => (
    <>
      <p>Fake email confirmation</p>
      <button type="button" onClick={() => setRegisterSuccess(false)}>
        Back to sign up
      </button>
    </>
  ),
}));
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

beforeEach(() => {
  vi.clearAllMocks();
  resetIntake();
  clearOnboardingRecovery();
  // Existing reader fixtures model a prepared form capability.
  mocks.exchange.mockReset();
  mocks.exchange.mockResolvedValue({ data: { onboarding_attempt: "fake-onboarding" } });
  vi.stubGlobal("localStorage", window.localStorage);
  localStorage.clear();
  mocks.isOSS = false;
  mocks.readers = [];
  mocks.marketplaceRegister.mockReset();
  mocks.marketplaceRegister.mockResolvedValue({
    result: {
      message: "Check email",
      access: "fake-signup-access",
      refresh: "fake-signup-refresh",
    },
  });
  mocks.login.mockResolvedValue(undefined);
});
afterEach(async () => {
  cleanup();
  await act(async () => {});
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  resetIntake();
});

function enter(key, source) {
  visit(
    `/auth/jwt/register?${key}=fake-onboarding&returnTo=%2Fshared%2Ffake-return#stay`,
  );
  if (source === "stash") {
    runIntake();
    expect(window.location.search).toBe("?returnTo=%2Fshared%2Ffake-return");
    expect(window[stashKey].params.get(key)).toBe("fake-onboarding");
  }
}

// AWS links that end point the buyer back to AWS Marketplace (W10-AUTH-ORG-U01).
const AWS_EXPIRED_TITLE = "Your AWS Marketplace setup link has expired";
const AWS_HAS_ACCOUNT_MESSAGE =
  "This AWS Marketplace subscription already has a FutureAGI account. Sign in with the email you used to set it up.";

// GeneralMethods.bad_request -> build_error_envelope, normalized by Axios.
function marketplaceBadRequest(message) {
  return {
    statusCode: 400,
    status: false,
    type: "validation_error",
    code: "invalid",
    detail: message,
    message,
    error: message,
    result: message,
  };
}

function expectLocalStorageHasNoOnboarding() {
  const values = Array.from({ length: localStorage.length }, (_, index) =>
    localStorage.getItem(localStorage.key(index)),
  );
  expect(values.join("\n")).not.toContain("fake-onboarding");
}

function captureSocialRedirect() {
  const browser = window;
  const redirects = [];
  const locationView = {
    get search() {
      return browser.location.search;
    },
    get pathname() {
      return browser.location.pathname;
    },
    get origin() {
      return browser.location.origin;
    },
    set href(url) {
      // Snapshot at navigation, not after the handler has had time to clean up.
      redirects.push({
        url,
        recovery: browser.sessionStorage.getItem(onboardingStorageKey),
        stash: browser[stashKey],
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

async function submit() {
  fireEvent.change(screen.getByLabelText("Full Name"), {
    target: { value: "Fake Person" },
  });
  fireEvent.change(
    screen.getByLabelText(mocks.isOSS ? "Email ID" : "Business Email ID"),
    { target: { value: "fake@example.org" } },
  );
  if (mocks.isOSS) {
    fireEvent.change(screen.getByLabelText("Set Password"), {
      target: { value: "fake-password-only" },
    });
    fireEvent.change(screen.getByLabelText("Confirm Password"), {
      target: { value: "fake-password-only" },
    });
  }
  await waitFor(() => expect(
    screen.getByRole("button", { name: "Continue", exact: true }),
  ).not.toBeDisabled());
  fireEvent.click(
    screen.getByRole("button", { name: "Continue", exact: true }),
  );
  await waitFor(() => expect(mocks.marketplaceRegister).toHaveBeenCalled());
}

describe.each(["stash", "URL fallback"])(
  "marketplace reader using %s",
  (source) => {
    it.each([
      ["AWS", "onboarding_token", "/fake/aws-signup"],
      ["GCP", "onboarding_gcp_token", "/fake/gcp-signup"],
    ])(
      "%s keeps its credential across StrictMode, form rerenders and a retry, then clears it on cloud success",
      async (provider, key, endpoint) => {
        enter(key, source);
        const persisted = window.sessionStorage.getItem(onboardingStorageKey);
        const handoff = window[stashKey];
        mocks.marketplaceRegister.mockRejectedValueOnce(
          marketplaceBadRequest("An account with this email already exists"),
        );
        const view = render(
          <StrictMode>
            <JwtRegisterView />
          </StrictMode>,
        );
        await act(async () => {}); // Flush StrictMode's cleanup microtask.
        view.rerender(
          <StrictMode>
            <JwtRegisterView />
          </StrictMode>,
        );
        await submit();
        await screen.findByText("An account with this email already exists");
        expect(mocks.marketplaceRegister, provider).toHaveBeenLastCalledWith(
          endpoint,
          {
            onboarding_token: "fake-onboarding",
            email: "fake@example.org",
            full_name: "Fake Person",
          },
        );
        // A retryable 400 keeps both shared recovery and the mounted retry copy.
        expect(window[stashKey]).toBe(handoff);
        expect(window.sessionStorage.getItem(onboardingStorageKey)).toBe(
          persisted,
        );
        fireEvent.click(
          screen.getByRole("button", { name: "Continue", exact: true }),
        );
        await screen.findByText("Fake email confirmation");
        expect(mocks.marketplaceRegister).toHaveBeenCalledTimes(2);
        expect(mocks.marketplaceRegister).toHaveBeenLastCalledWith(endpoint, {
          onboarding_token: "fake-onboarding",
          email: "fake@example.org",
          full_name: "Fake Person",
        });
        expect(mocks.register).not.toHaveBeenCalled();
        expect(mocks.login).not.toHaveBeenCalled();
        expect(mocks.navigate).not.toHaveBeenCalled();
        expect(localStorage.getItem("accessToken")).toBeNull(); // Cloud base behavior: email confirmation, not login.
        expect(localStorage.getItem("redirectUrl")).toBe("/shared/fake-return");
        expect(window[stashKey]).toBeUndefined();
      },
    );

    it.each([
      ["AWS", "onboarding_token", "/fake/aws-signup"],
      ["GCP", "onboarding_gcp_token", "/fake/gcp-signup"],
    ])(
      "%s keeps marketplace signup after cloud success and Back to sign up",
      async (provider, key, endpoint) => {
        enter(key, source);
        const cleanUrl = window.location.href;
        const view = render(
          <StrictMode>
            <JwtRegisterView />
          </StrictMode>,
        );
        await submit();
        await screen.findByText("Fake email confirmation");
        expect(window[stashKey]).toBeUndefined();
        fireEvent.click(
          screen.getByRole("button", { name: "Back to sign up" }),
        );
        view.rerender(
          <StrictMode>
            <JwtRegisterView />
          </StrictMode>,
        );
        if (provider === "GCP") {
          expect(
            screen.queryByRole("button", { name: "Continue with SSO/SAML" }),
          ).toBeNull();
        }
        fireEvent.change(screen.getByLabelText("Business Email ID"), {
          target: { value: "corrected@example.org" },
        });
        const terminalMessage = `${provider === "AWS" ? "Signup failed: " : ""}Invalid or expired onboarding token`;
        mocks.marketplaceRegister.mockRejectedValueOnce(
          marketplaceBadRequest(terminalMessage),
        );
        fireEvent.click(
          screen.getByRole("button", { name: "Continue", exact: true }),
        );
        await screen.findByText(
          provider === "AWS"
            ? AWS_EXPIRED_TITLE
            : "Link expired, sign in again.",
        );
        expect(screen.queryByRole("button", { name: "Continue", exact: true })).toBeNull();
        expect(mocks.readers.at(-1).get(key)).toBeNull();
        expect(mocks.marketplaceRegister).toHaveBeenCalledTimes(2);
        expect(mocks.marketplaceRegister).toHaveBeenLastCalledWith(endpoint, {
          onboarding_token: "fake-onboarding",
          email: "corrected@example.org",
          full_name: "Fake Person",
        });
        expect(mocks.register).not.toHaveBeenCalled();
        expect(mocks.login).not.toHaveBeenCalled();
        expect(mocks.navigate).not.toHaveBeenCalled();
        expect(window.location.href).toBe(cleanUrl);
        expect(window[stashKey]).toBeUndefined();
        expect(localStorage.getItem("accessToken")).toBeNull();
        if (provider === "GCP") {
          expect(
            screen.queryByRole("button", { name: "Continue with SSO/SAML" }),
          ).toBeNull();
        }
      },
    );

    it.each([
      ["Google", "google"],
      ["Github", "github"],
    ])(
      "GCP keeps its %s signup token after cloud success and Back to sign up",
      async (label, provider) => {
        enter("onboarding_gcp_token", source);
        mocks.get.mockResolvedValue({ data: { result: {} } });
        render(
          <StrictMode>
            <JwtRegisterView />
          </StrictMode>,
        );
        await submit();
        await screen.findByText("Fake email confirmation");
        expect(window[stashKey]).toBeUndefined();
        fireEvent.click(
          screen.getByRole("button", { name: "Back to sign up" }),
        );
        expect(
          screen.queryByRole("button", { name: "Continue with SSO/SAML" }),
        ).toBeNull();
        fireEvent.click(
          screen.getByRole("button", { name: `Continue with ${label}` }),
        );
        await waitFor(() =>
          expect(mocks.get).toHaveBeenCalledWith({
            provider,
            token: "fake-onboarding",
          }),
        );
        expect(mocks.register).not.toHaveBeenCalled();
        expect(window[stashKey]).toBeUndefined();
      },
    );

    it.each(["onboarding_token", "onboarding_gcp_token"])(
      "%s preserves OSS auto-login and returnTo behavior",
      async (key) => {
        mocks.isOSS = true;
        enter(key, source);
        render(
          <StrictMode>
            <JwtRegisterView />
          </StrictMode>,
        );
        await submit();
        await waitFor(() =>
          expect(mocks.navigate).toHaveBeenCalledWith("/shared/fake-return"),
        );
        expect(mocks.login).toHaveBeenCalledWith({
          status: 200,
          data: expect.objectContaining({
            access: "fake-signup-access",
            refresh: "fake-signup-refresh",
          }),
        });
        expect(localStorage.getItem("signupProvider")).toBe("email");
        expect(localStorage.getItem("initial-render")).toBe("done");
        expect(localStorage.getItem("redirectUrl")).toBeNull();
        expect(window[stashKey]).toBeUndefined();
      },
    );

    describe.each(["Sign up link", "history Back"])(
      "returning to register with %s",
      (returnMethod) => {
        it.each([
          ["AWS", "onboarding_token", "/fake/aws-signup"],
          ["GCP", "onboarding_gcp_token", "/fake/gcp-signup"],
        ])(
          "%s keeps onboarding across SPA unmount until successful submit",
          async (provider, key, endpoint) => {
            enter(key, source);
            const view = render(
              <StrictMode>
                <BrowserRouter>
                  <Routes>
                    <Route
                      path="/auth/jwt/register"
                      element={<JwtRegisterView />}
                    />
                    <Route
                      path="/auth/jwt/login"
                      element={
                        // The login page's Sign up link forwards its search.
                        <Link
                          to={`/auth/jwt/register${window.location.search}`}
                        >
                          Sign up
                        </Link>
                      }
                    />
                  </Routes>
                </BrowserRouter>
              </StrictMode>,
            );
            await act(async () => {});
            fireEvent.click(screen.getByRole("link", { name: "Sign In" }));
            const signup = await screen.findByRole("link", { name: "Sign up" });
            await act(async () => {}); // Run the real register unmount cleanup.
            expect(window.location.pathname).toBe("/auth/jwt/login");
            if (source === "stash") {
              expect(window[stashKey].params.get(key)).toBe("fake-onboarding");
              // Retention must not make the handoff readable on another path.
              expect(readAuthRedirectParams().get(key)).toBeNull();
            }
            if (returnMethod === "Sign up link") {
              fireEvent.click(signup);
            } else {
              act(() => window.history.back());
            }
            await screen.findByLabelText("Full Name");
            await act(async () => {}); // StrictMode replay on the new mount.
            expect(window.location.pathname).toBe("/auth/jwt/register");
            expect(readAuthRedirectParams().get(key)).toBe("fake-onboarding");
            if (provider === "GCP") {
              expect(
                screen.queryByRole("button", {
                  name: "Continue with SSO/SAML",
                }),
              ).toBeNull();
              mocks.get.mockResolvedValue({ data: { result: {} } });
              fireEvent.click(
                screen.getByRole("button", { name: "Continue with Google" }),
              );
              await waitFor(() =>
                expect(mocks.get).toHaveBeenCalledWith({
                  provider: "google",
                  token: "fake-onboarding",
                }),
              );
            }
            await submit();
            await screen.findByText("Fake email confirmation");
            expect(mocks.marketplaceRegister).toHaveBeenLastCalledWith(
              endpoint,
              {
                onboarding_token: "fake-onboarding",
                email: "fake@example.org",
                full_name: "Fake Person",
              },
            );
            expect(mocks.register).not.toHaveBeenCalled();
            expect(window[stashKey]).toBeUndefined();
            expectLocalStorageHasNoOnboarding();
            expect(
              window.sessionStorage.getItem(onboardingStorageKey),
            ).toBeNull();
            expect(window.sessionStorage.length).toBe(0);
            view.unmount();
            await act(async () => {});
            if (source === "stash") {
              // Success releases the global handoff; a later mount cannot reuse it.
              visit("/auth/jwt/register");
              render(<JwtRegisterView />);
              expect(readAuthRedirectParams().get(key)).toBeNull();
              expect(
                screen.getByRole("button", { name: "Continue with SSO/SAML" }),
              ).toBeTruthy();
            }
          },
        );
      },
    );

    it("GCP also passes the stashed credential into social signup", async () => {
      enter("onboarding_gcp_token", source);
      mocks.get.mockResolvedValue({ data: { result: {} } });
      render(
        <StrictMode>
          <JwtRegisterView />
        </StrictMode>,
      );
      expect(
        screen.queryByRole("button", { name: "Continue with SSO/SAML" }),
      ).toBeNull();
      fireEvent.click(
        screen.getByRole("button", { name: "Continue with Google" }),
      );
      await waitFor(() =>
        expect(mocks.get).toHaveBeenCalledWith({
          provider: "google",
          token: "fake-onboarding",
        }),
      );
    });
  },
);

describe.each(["stash", "URL fallback"])("single-use marketplace recovery using %s", (source) => {
  it.each(["expired", "replayed", "failed first submission"])(
    "a %s code ends in fresh sign-in, with neither a mounted copy nor a retryable signup form",
    async (outcome) => {
      enter("onboarding_token", source);
      const expiresAt = outcome === "expired" ? Date.now() - 1 : Date.now() + 60000;
      const spent = new Set(outcome === "replayed" ? ["fake-onboarding"] : []);
      mocks.marketplaceRegister.mockImplementation(async (_endpoint, payload) => {
        if (Date.now() >= expiresAt || spent.has(payload.onboarding_token)) {
          throw marketplaceBadRequest("Invalid or expired onboarding token");
        }
        // The server consumes before business validation, so that failure does
        // not make this one-time code redeemable a second time.
        spent.add(payload.onboarding_token);
        throw marketplaceBadRequest("An account with this email already exists");
      });
      const view = render(<StrictMode><JwtRegisterView /></StrictMode>);
      const mountedReader = mocks.readers.at(-1);
      const form = view.container.querySelector("form");
      expect(mountedReader.get("onboarding_token")).toBe("fake-onboarding");
      await submit();
      if (outcome === "failed first submission") {
        await screen.findByText("An account with this email already exists");
        expect(mountedReader.get("onboarding_token")).toBe("fake-onboarding");
        fireEvent.change(screen.getByLabelText("Business Email ID"), {
          target: { value: "corrected@example.org" },
        });
        fireEvent.click(screen.getByRole("button", { name: "Continue", exact: true }));
      }
      await screen.findByText(AWS_EXPIRED_TITLE);
      const submissions = outcome === "failed first submission" ? 2 : 1;
      expect(mocks.marketplaceRegister).toHaveBeenCalledTimes(submissions);
      expect(mocks.marketplaceRegister).toHaveBeenLastCalledWith("/fake/aws-signup", {
        onboarding_token: "fake-onboarding",
        email: outcome === "failed first submission" ? "corrected@example.org" : "fake@example.org",
        full_name: "Fake Person",
      });
      expect(mountedReader.get("onboarding_token")).toBeNull();
      expect(mountedReader.get("onboarding_gcp_token")).toBeNull();
      expect(window[stashKey]).toBeUndefined();
      expect(window.sessionStorage.getItem(onboardingStorageKey)).toBeNull();
      expect(screen.queryByText("Invalid or expired onboarding token")).toBeNull();
      expect(screen.queryByRole("button", { name: "Continue", exact: true })).toBeNull();
      expect(screen.queryByLabelText("Full Name")).toBeNull();
      expect(screen.queryByRole("button", { name: "Continue with Google" })).toBeNull();
      expect(mocks.navigate).not.toHaveBeenCalled();
      view.rerender(<StrictMode><JwtRegisterView /></StrictMode>);
      // Even a retained old form must not submit a spent code or ordinary signup.
      fireEvent.submit(form);
      await act(async () => {});
      expect(mocks.marketplaceRegister).toHaveBeenCalledTimes(submissions);
      expect(mocks.register).not.toHaveBeenCalled();
      expect(mocks.login).not.toHaveBeenCalled();
      expectLocalStorageHasNoOnboarding();
      fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
      expect(mocks.navigate).toHaveBeenCalledWith("/auth/jwt/login", { replace: true });
      expect(mocks.marketplaceRegister).toHaveBeenCalledTimes(submissions);
      view.unmount();
      await act(async () => {});
      visit("/auth/jwt/register");
      expect(readAuthRedirectParams().get("onboarding_token")).toBeNull();
    },
  );
});

describe.each(["onboarding_token", "onboarding_gcp_token"])(
  "marketplace failure cleanup for %s",
  (key) => {
    it.each(["invalid/expired token", "existing marketplace account"])(
      "removes recovery after the server's terminal 400 for %s so a later registration cannot reuse it",
      async (outcome) => {
        enter(key, "stash");
        expect(
          window.sessionStorage.getItem(onboardingStorageKey),
        ).not.toBeNull();
        const isAws = key === "onboarding_token";
        const reason =
          outcome === "invalid/expired token"
            ? "Invalid or expired onboarding token"
            : isAws
              ? "This AWS Marketplace customer already has an account"
              : "This Marketplace subscription already has an account";
        // AWS wraps all exceptions; GCP returns ValueError messages directly.
        const message = `${isAws ? "Signup failed: " : ""}${reason}`;
        mocks.marketplaceRegister.mockRejectedValueOnce(
          marketplaceBadRequest(message),
        );
        const view = render(<JwtRegisterView />);
        await submit();
        let endedCopy = "Link expired, sign in again.";
        if (isAws) {
          endedCopy =
            outcome === "invalid/expired token"
              ? AWS_EXPIRED_TITLE
              : AWS_HAS_ACCOUNT_MESSAGE;
        }
        await screen.findByText(endedCopy);
        expect(screen.queryByRole("button", { name: "Continue", exact: true })).toBeNull();
        expect(mocks.readers.at(-1).get(key)).toBeNull();
        expect(window.sessionStorage.getItem(onboardingStorageKey)).toBeNull();
        expect(window[stashKey]).toBeUndefined();
        view.unmount();
        await act(async () => {});
        visit("/auth/jwt/register");
        const later = readAuthRedirectParams();
        expect(later.get("onboarding_token")).toBeNull();
        expect(later.get("onboarding_gcp_token")).toBeNull();
      },
    );

    it.each([
      [400, "field validation"],
      [422, "field validation"],
      [429, "throttling"],
      [499, "other retryable error"],
      [400, "An account with this email already exists"],
      [400, "Disposable email"],
      [400, "validation with a token-like message"],
      [400, "marketplace serializer field validation"],
    ])(
      "restores marketplace signup after retryable HTTP %s (%s) and a full reload",
      async (statusCode, reason) => {
        enter(key, "stash");
        const persisted = window.sessionStorage.getItem(onboardingStorageKey);
        const isMarketplaceValidation =
          reason === "marketplace serializer field validation";
        const isValidation =
          reason.includes("validation") && !isMarketplaceValidation;
        const message = isMarketplaceValidation
          ? "email: Enter a valid email address."
          : isValidation
            ? "Fake retryable signup failure"
            : reason;
        const error = { ...marketplaceBadRequest(message), statusCode };
        if (isMarketplaceValidation) {
          // validated_api_request -> gm.bad_request(serializer.errors):
          // build_error_envelope keeps first_error_code, field attr and details,
          // while result is the flattened message (not SIGNUP_VALIDATION_FAILED).
          error.attr = "email";
          error.details = { email: ["Enter a valid email address."] };
        }
        if (isValidation) {
          error.code = "SIGNUP_VALIDATION_FAILED";
          error.result = {
            error_code: "SIGNUP_VALIDATION_FAILED",
            field_errors: {
              email: ["Fake invalid email"],
              non_field_errors: [message],
            },
          };
          if (reason === "validation with a token-like message") {
            error.error = "Invalid or expired onboarding token";
            error.message = error.error;
          }
        }
        mocks.marketplaceRegister.mockRejectedValueOnce(error);
        const view = render(<JwtRegisterView />);
        await submit();
        await screen.findByText(message);
        expect(window.sessionStorage.getItem(onboardingStorageKey)).toBe(
          persisted,
        );
        expect(window[stashKey].params.get(key)).toBe("fake-onboarding");
        view.unmount();
        await act(async () => {});
        window.dispatchEvent(new Event("pagehide"));
        expect(window[stashKey]).toBeUndefined();
        expect(window.sessionStorage.getItem(onboardingStorageKey)).toBe(
          persisted,
        );
        vi.resetModules();
        runIntake();
        const { default: ReloadedRegisterView } = await import(
          "src/sections/auth/jwt/jwt-register-view"
        );
        render(<ReloadedRegisterView />);
        await submit();
        await screen.findByText("Fake email confirmation");
        expect(mocks.marketplaceRegister).toHaveBeenCalledTimes(2);
        expect(mocks.marketplaceRegister).toHaveBeenLastCalledWith(
          key === "onboarding_token" ? "/fake/aws-signup" : "/fake/gcp-signup",
          {
            onboarding_token: "fake-onboarding",
            email: "fake@example.org",
            full_name: "Fake Person",
          },
        );
        expect(mocks.register).not.toHaveBeenCalled();
        expect(window[stashKey]).toBeUndefined();
        expect(window.sessionStorage.getItem(onboardingStorageKey)).toBeNull();
        expectLocalStorageHasNoOnboarding();
      },
    );

    it.each([undefined, 500, 503])(
      "keeps reload recovery for retryable transport/server failure %s",
      async (statusCode) => {
        enter(key, "stash");
        const persisted = window.sessionStorage.getItem(onboardingStorageKey);
        mocks.marketplaceRegister.mockRejectedValueOnce({
          statusCode,
          detail: "Fake temporary failure",
        });
        render(<JwtRegisterView />);
        await submit();
        await screen.findByText("Fake temporary failure");
        expect(window.sessionStorage.getItem(onboardingStorageKey)).toBe(
          persisted,
        );
        expect(window[stashKey].params.get(key)).toBe("fake-onboarding");
      },
    );
  },
);

it.each([
  ["Google", "google"],
  ["Github", "github"],
])(
  "clears GCP recovery before the %s social redirect",
  async (label, provider) => {
    enter("onboarding_gcp_token", "stash");
    const persisted = window.sessionStorage.getItem(onboardingStorageKey);
    let finishRequest;
    mocks.get.mockReturnValueOnce(
      new Promise((resolve) => {
        finishRequest = resolve;
      }),
    );
    const redirects = captureSocialRedirect();
    render(<JwtRegisterView />);
    fireEvent.click(
      screen.getByRole("button", { name: `Continue with ${label}` }),
    );
    expect(mocks.get).toHaveBeenCalledWith({
      provider,
      token: "fake-onboarding",
    });
    expect(window.sessionStorage.getItem(onboardingStorageKey)).toBe(persisted);
    expect(window[stashKey]).toBeDefined();
    expect(redirects).toEqual([]);
    await act(async () => {
      finishRequest({ data: { result: { url: "/fake/social-redirect" } } });
    });
    expect(redirects).toEqual([
      { url: "/fake/social-redirect", recovery: null, stash: undefined },
    ]);
    expect(window.sessionStorage.getItem(onboardingStorageKey)).toBeNull();
    expect(window[stashKey]).toBeUndefined();
    const later = readAuthRedirectParams();
    expect(later.get("onboarding_token")).toBeNull();
    expect(later.get("onboarding_gcp_token")).toBeNull();
  },
);

it.each(["missing URL", "network failure"])(
  "keeps GCP recovery if social signup has %s and does not redirect",
  async (failure) => {
    enter("onboarding_gcp_token", "stash");
    const persisted = window.sessionStorage.getItem(onboardingStorageKey);
    if (failure === "missing URL") {
      mocks.get.mockResolvedValueOnce({ data: { result: {} } });
    } else {
      mocks.get.mockRejectedValueOnce(new Error("Fake network failure"));
    }
    const redirects = captureSocialRedirect();
    render(<JwtRegisterView />);
    await act(async () => {
      fireEvent.click(
        screen.getByRole("button", { name: "Continue with Google" }),
      );
    });
    expect(mocks.get).toHaveBeenCalledWith({
      provider: "google",
      token: "fake-onboarding",
    });
    expect(redirects).toEqual([]);
    expect(window.sessionStorage.getItem(onboardingStorageKey)).toBe(persisted);
    expect(window[stashKey].params.get("onboarding_gcp_token")).toBe(
      "fake-onboarding",
    );
  },
);

it.each(["onboarding_token", "onboarding_gcp_token"])(
  "%s restores recovery after reload while registration is still in flight",
  async (key) => {
    enter(key, "stash");
    const persisted = window.sessionStorage.getItem(onboardingStorageKey);
    let finishRequest;
    mocks.marketplaceRegister.mockReturnValueOnce(
      new Promise((resolve) => {
        finishRequest = resolve;
      }),
    );
    const view = render(<JwtRegisterView />);
    await submit();
    view.unmount();
    await act(async () => {});
    window.dispatchEvent(new Event("pagehide"));
    expect(window[stashKey]).toBeUndefined();
    expect(window.sessionStorage.getItem(onboardingStorageKey)).toBe(persisted);
    vi.resetModules();
    runIntake();
    const { readAuthRedirectParams: readAfterReload } = await import(
      "src/auth/auth-url-intake"
    );
    expect(readAfterReload().get(key)).toBe("fake-onboarding");
    expect(window.sessionStorage.getItem(onboardingStorageKey)).toBe(persisted);
    // Settle the abandoned request without consuming the new document's reader.
    await act(async () => finishRequest({}));
    expect(readAfterReload().get(key)).toBe("fake-onboarding");
  },
);

describe.each([false, true])("marketplace reload with OSS=%s", (isOSS) => {
  it.each([
    ["AWS", "onboarding_token", "/fake/aws-signup"],
    ["GCP", "onboarding_gcp_token", "/fake/gcp-signup"],
  ])(
    "%s registers from tab storage after a full reload and consumes it",
    async (provider, key, endpoint) => {
      mocks.isOSS = isOSS;
      enter(key, "stash");
      const cleanUrl = window.location.href;
      const firstView = render(<JwtRegisterView />);
      firstView.unmount();
      await act(async () => {});
      window.dispatchEvent(new Event("pagehide"));
      expect(window[stashKey]).toBeUndefined();
      const persisted = window.sessionStorage.getItem(onboardingStorageKey);
      expect(persisted).not.toBeNull();
      // New page execution and module registry, with the same tab storage.
      vi.resetModules();
      runIntake();
      const { default: ReloadedRegisterView } = await import(
        "src/sections/auth/jwt/jwt-register-view"
      );
      render(
        <StrictMode>
          <ReloadedRegisterView />
        </StrictMode>,
      );
      await act(async () => {});
      expect(window.sessionStorage.getItem(onboardingStorageKey)).toBe(
        persisted,
      );
      expect(window.location.href).toBe(cleanUrl);
      if (provider === "GCP") {
        expect(
          screen.queryByRole("button", { name: "Continue with SSO/SAML" }),
        ).toBeNull();
      }
      await submit();
      expect(mocks.marketplaceRegister).toHaveBeenLastCalledWith(endpoint, {
        onboarding_token: "fake-onboarding",
        email: "fake@example.org",
        full_name: "Fake Person",
      });
      if (isOSS) {
        await waitFor(() =>
          expect(mocks.navigate).toHaveBeenCalledWith("/shared/fake-return"),
        );
        expect(mocks.login).toHaveBeenCalledWith({
          status: 200,
          data: expect.objectContaining({
            access: "fake-signup-access",
            refresh: "fake-signup-refresh",
          }),
        });
      } else {
        await screen.findByText("Fake email confirmation");
        expect(mocks.login).not.toHaveBeenCalled();
        expect(mocks.navigate).not.toHaveBeenCalled();
      }
      expect(mocks.register).not.toHaveBeenCalled();
      expect(window[stashKey]).toBeUndefined();
      expect(window.sessionStorage.getItem(onboardingStorageKey)).toBeNull();
      expectLocalStorageHasNoOnboarding();
      expect(window.location.href).toBe(cleanUrl);
    },
  );
});

it.each(["onboarding_token", "onboarding_gcp_token"])(
  "%s can still register in memory when tab storage is blocked",
  async (key) => {
    vi.spyOn(window, "sessionStorage", "get").mockImplementation(() => {
      throw new DOMException("Fake blocked storage", "SecurityError");
    });
    enter(key, "stash");
    render(
      <StrictMode>
        <JwtRegisterView />
      </StrictMode>,
    );
    await act(async () => {});
    await submit();
    await screen.findByText("Fake email confirmation");
    expect(mocks.marketplaceRegister).toHaveBeenLastCalledWith(
      key === "onboarding_token" ? "/fake/aws-signup" : "/fake/gcp-signup",
      {
        onboarding_token: "fake-onboarding",
        email: "fake@example.org",
        full_name: "Fake Person",
      },
    );
    expect(mocks.register).not.toHaveBeenCalled();
    expect(window[stashKey]).toBeUndefined();
  },
);

it("forgets a completed signup after real unmount, not StrictMode replay", async () => {
  enter("onboarding_token", "stash");
  const view = render(
    <StrictMode>
      <JwtRegisterView />
    </StrictMode>,
  );
  await act(async () => {});
  expect(window[stashKey]).toBeDefined();
  // Unmount alone must no longer discard onboarding; success consumes it.
  await submit();
  await screen.findByText("Fake email confirmation");
  view.unmount();
  await act(async () => {});
  expect(window[stashKey]).toBeUndefined();
});

it.each(["onboarding_token", "onboarding_gcp_token"])(
  "drops the memory copy of %s at pagehide but retains reload recovery until consumed",
  async (key) => {
    enter(key, "stash");
    const view = render(<JwtRegisterView />);
    view.unmount();
    visit("/auth/jwt/login");
    await act(async () => {});
    expect(window[stashKey].params.get(key)).toBe("fake-onboarding");
    window.dispatchEvent(new Event("pagehide"));
    expect(window[stashKey]).toBeUndefined();
    expect(window.sessionStorage.getItem(onboardingStorageKey)).not.toBeNull();
    visit("/auth/jwt/register");
    const recovered = readAuthRedirectParams();
    expect(recovered.get(key)).toBe("fake-onboarding");
    recovered.clear();
    expect(window.sessionStorage.getItem(onboardingStorageKey)).toBeNull();
    expect(readAuthRedirectParams().get(key)).toBeNull();
  },
);


it("W10-AUTH-ORG-L02 exchanges AWS on arrival, once under StrictMode, and retries with the form attempt", async () => {
  enter("onboarding_token", "stash");
  mocks.exchange.mockResolvedValue({ data: { onboarding_attempt: "fake-form-attempt" } });
  mocks.marketplaceRegister.mockRejectedValueOnce(
    marketplaceBadRequest("Signup failed"),
  );
  render(<StrictMode><JwtRegisterView /></StrictMode>);
  await waitFor(() => expect(mocks.exchange).toHaveBeenCalledTimes(1));
  expect(mocks.marketplaceRegister).not.toHaveBeenCalled();
  expect(mocks.exchange).toHaveBeenCalledWith(
    "/accounts/aws-marketplace/onboarding-exchange/",
    { code: "fake-onboarding" },
    expect.any(Object),
  );
  await submit();
  await waitFor(() => expect(screen.getByRole("button", { name: "Continue", exact: true })).not.toBeDisabled());
  await submit();
  await waitFor(() => expect(mocks.marketplaceRegister).toHaveBeenCalledTimes(2));
  expect(mocks.marketplaceRegister).toHaveBeenLastCalledWith("/fake/aws-signup", {
    onboarding_token: "fake-form-attempt",
    email: "fake@example.org",
    full_name: "Fake Person",
  });
  expect(mocks.exchange).toHaveBeenCalledTimes(1);
});
