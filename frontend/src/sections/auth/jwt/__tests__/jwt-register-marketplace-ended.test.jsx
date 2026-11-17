/* eslint-disable react/prop-types */
import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import JwtRegisterView from "src/sections/auth/jwt/jwt-register-view";

// W10-AUTH-ORG-U01: when an AWS Marketplace setup link can no longer be used,
// the buyer has no FutureAGI account or password yet. The page must send them
// back to AWS Marketplace for a new link, not only to the password sign-in.
// Head-55 repair: the 60-second redirect code is exchanged when the page
// opens (W10-AUTH-ORG-L02) and the form submits the longer-lived attempt.

const mocks = vi.hoisted(() => ({
  prepareAWSOnboarding: vi.fn(),
  marketplaceRegister: vi.fn(),
  register: vi.fn(),
  login: vi.fn(),
  navigate: vi.fn(),
  clearOnboardingRecovery: vi.fn(),
  params: new Map(),
  // Like the real hook, one reader object for the life of the view.
  reader: {
    get: (key) => mocks.params.get(key) ?? null,
    release: () => {},
    clear: () => mocks.params.clear(),
  },
}));
vi.mock("src/auth/auth-url-intake", () => ({
  clearOnboardingRecovery: mocks.clearOnboardingRecovery,
  // Like the real reader, clear() forgets the code for every later render.
  useAuthRedirectParams: () => mocks.reader,
}));
vi.mock("src/auth/aws-onboarding", () => ({
  prepareAWSOnboarding: mocks.prepareAWSOnboarding,
}));
vi.mock("src/auth/hooks", () => ({ useAuthContext: () => mocks }));
vi.mock("react-router-dom", async (importOriginal) => ({
  ...(await importOriginal()),
  useNavigate: () => mocks.navigate,
  useLocation: () => ({ search: "" }),
}));
vi.mock("src/hooks/useDeploymentMode", () => ({
  useDeploymentMode: () => ({ isOSS: false, isCloud: true, isSuccess: true }),
  usePostLoginPath: () => "/dashboard/develop",
}));
vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn() },
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
vi.mock("src/routes/components", () => ({
  RouterLink: ({ children }) => <span>{children}</span>,
}));
vi.mock("src/sections/auth/jwt/RightSectionAuth", () => ({
  default: () => null,
}));
vi.mock("src/sections/auth/jwt/password-sent-view", () => ({
  default: () => <p>Fake email confirmation</p>,
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

const AWS_EXPIRED_TITLE = "Your AWS Marketplace setup link has expired";
const AWS_EXPIRED_BODY =
  'Go back to your AWS Marketplace subscription and choose "Set up your account" to get a new link. Already set up your FutureAGI account? Sign in instead.';
const AWS_SUBSCRIPTIONS_URL =
  "https://console.aws.amazon.com/marketplace/home#/subscriptions";
const AWS_HAS_ACCOUNT =
  "This AWS Marketplace subscription already has a FutureAGI account. Sign in with the email you used to set it up.";
const EMAIL_TAKEN =
  "An account with this email already exists. Sign in instead, or use a different email.";
const EXPIRED = "Invalid or expired onboarding token";
const REDIRECT_CODE = "fake-onboarding-code";
const FORM_ATTEMPT = "fake-form-attempt";

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

async function submit(email = "fake@example.org") {
  fireEvent.change(screen.getByLabelText("Full Name"), {
    target: { value: "Fake Person" },
  });
  fireEvent.change(screen.getByLabelText("Business Email ID"), {
    target: { value: email },
  });
  const calls = mocks.marketplaceRegister.mock.calls.length;
  // AWS keeps Continue disabled until the redirect code is exchanged.
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Continue" })).toBeEnabled(),
  );
  fireEvent.click(screen.getByRole("button", { name: "Continue" }));
  await waitFor(() =>
    expect(mocks.marketplaceRegister.mock.calls.length).toBe(calls + 1),
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.params.clear();
  mocks.marketplaceRegister.mockReset();
  mocks.marketplaceRegister.mockResolvedValue({
    result: { message: "Check email" },
  });
  mocks.prepareAWSOnboarding.mockReset();
  mocks.prepareAWSOnboarding.mockResolvedValue(FORM_ATTEMPT);
});

afterEach(() => {
  vi.useRealTimers();
});

function submittedTokens() {
  return mocks.marketplaceRegister.mock.calls.map(
    ([, body]) => body.onboarding_token,
  );
}

describe("AWS Marketplace sign-up that cannot continue", () => {
  it.each([
    ["an expired code", "Invalid or expired onboarding token"],
    [
      "a wrapped expired code",
      "Signup failed: Invalid or expired onboarding token",
    ],
  ])(
    "%s sends the buyer back to AWS Marketplace for a new link",
    async (_label, reason) => {
      mocks.params.set("onboarding_token", "fake-onboarding-code");
      mocks.marketplaceRegister.mockRejectedValueOnce(
        marketplaceBadRequest(reason),
      );
      render(<JwtRegisterView />);
      await submit();

      expect(await screen.findByText(AWS_EXPIRED_TITLE)).toBeInTheDocument();
      expect(screen.getByText(AWS_EXPIRED_BODY)).toBeInTheDocument();
      const subscriptions = screen.getByRole("link", {
        name: "Open AWS Marketplace subscriptions",
      });
      expect(subscriptions).toHaveAttribute("href", AWS_SUBSCRIPTIONS_URL);
      expect(subscriptions).toHaveAttribute("target", "_blank");
      expect(subscriptions.getAttribute("rel")).toContain("noopener");
      expect(screen.queryByText("Link expired, sign in again.")).toBeNull();
      expect(
        screen.queryByRole("button", { name: "Sign in again" }),
      ).toBeNull();
      expect(screen.queryByLabelText("Full Name")).toBeNull();
      expect(screen.queryByRole("button", { name: "Continue" })).toBeNull();
      expect(mocks.clearOnboardingRecovery).toHaveBeenCalled();
      expect(mocks.navigate).not.toHaveBeenCalled();

      fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
      expect(mocks.navigate).toHaveBeenCalledWith("/auth/jwt/login", {
        replace: true,
      });
    },
  );

  it("a subscription that already has an account says which email to sign in with", async () => {
    mocks.params.set("onboarding_token", "fake-onboarding-code");
    mocks.marketplaceRegister.mockRejectedValueOnce(
      marketplaceBadRequest(
        "This AWS Marketplace customer already has an account",
      ),
    );
    render(<JwtRegisterView />);
    await submit();

    expect(await screen.findByText(AWS_HAS_ACCOUNT)).toBeInTheDocument();
    expect(screen.queryByText(AWS_EXPIRED_TITLE)).toBeNull();
    expect(screen.queryByText("Link expired, sign in again.")).toBeNull();
    expect(
      screen.queryByRole("link", {
        name: "Open AWS Marketplace subscriptions",
      }),
    ).toBeNull();
    expect(screen.queryByLabelText("Full Name")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(mocks.navigate).toHaveBeenCalledWith("/auth/jwt/login", {
      replace: true,
    });
  });

  it("an email that already has an account stays on the form and can be corrected", async () => {
    mocks.params.set("onboarding_token", "fake-onboarding-code");
    mocks.marketplaceRegister.mockRejectedValueOnce(
      marketplaceBadRequest(EMAIL_TAKEN),
    );
    render(<JwtRegisterView />);
    await submit("taken@example.org");

    expect(await screen.findByText(EMAIL_TAKEN)).toBeInTheDocument();
    expect(screen.queryByText(AWS_EXPIRED_TITLE)).toBeNull();
    expect(mocks.clearOnboardingRecovery).not.toHaveBeenCalled();

    await submit("corrected@example.org");
    expect(
      await screen.findByText("Fake email confirmation"),
    ).toBeInTheDocument();
    expect(mocks.marketplaceRegister).toHaveBeenLastCalledWith(
      "/fake/aws-signup",
      {
        onboarding_token: FORM_ATTEMPT,
        email: "corrected@example.org",
        full_name: "Fake Person",
      },
    );
    // The retry reuses the one exchange; the redirect code is never submitted.
    expect(submittedTokens()).toEqual([FORM_ATTEMPT, FORM_ATTEMPT]);
    expect(mocks.prepareAWSOnboarding).toHaveBeenCalledTimes(1);
    expect(mocks.prepareAWSOnboarding).toHaveBeenCalledWith(REDIRECT_CODE);
  });

  it("a GCP Marketplace link keeps its own expired view", async () => {
    mocks.params.set("onboarding_gcp_token", "fake-gcp-onboarding");
    mocks.marketplaceRegister.mockRejectedValueOnce(
      marketplaceBadRequest("Invalid or expired onboarding token"),
    );
    render(<JwtRegisterView />);
    await submit();

    expect(
      await screen.findByText("Link expired, sign in again."),
    ).toBeInTheDocument();
    expect(screen.queryByText(AWS_EXPIRED_TITLE)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Sign in again" }));
    expect(mocks.navigate).toHaveBeenCalledWith("/auth/jwt/login", {
      replace: true,
    });
    expect(mocks.prepareAWSOnboarding).not.toHaveBeenCalled();
  });
});

describe("AWS Marketplace sign-up takes as long as the form takes", () => {
  it("a form submitted 61 seconds after the page opens finishes sign-up", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    const openedAt = Date.now();
    mocks.params.set("onboarding_token", REDIRECT_CODE);
    // Like the server: the redirect code ends after 60 seconds, the form
    // attempt does not.
    mocks.marketplaceRegister.mockImplementation(async (_url, body) => {
      const codeEnded = Date.now() - openedAt > 60_000;
      if (body.onboarding_token !== FORM_ATTEMPT && codeEnded) {
        throw marketplaceBadRequest(EXPIRED);
      }
      return { result: { message: "Check email" } };
    });
    render(<JwtRegisterView />);
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Continue" })).toBeEnabled(),
    );
    expect(mocks.prepareAWSOnboarding).toHaveBeenCalledWith(REDIRECT_CODE);

    vi.setSystemTime(openedAt + 61_000);
    await submit();

    expect(
      await screen.findByText("Fake email confirmation"),
    ).toBeInTheDocument();
    expect(screen.queryByText(AWS_EXPIRED_TITLE)).toBeNull();
    expect(screen.queryByText("Link expired, sign in again.")).toBeNull();
    expect(submittedTokens()).toEqual([FORM_ATTEMPT]);
    expect(mocks.prepareAWSOnboarding).toHaveBeenCalledTimes(1);
    expect(mocks.clearOnboardingRecovery).not.toHaveBeenCalled();
  });

  it("a form attempt that has ended by the time the buyer submits shows the AWS ended view", async () => {
    mocks.params.set("onboarding_token", REDIRECT_CODE);
    mocks.marketplaceRegister.mockRejectedValueOnce(
      marketplaceBadRequest(EXPIRED),
    );
    render(<JwtRegisterView />);
    await submit();

    expect(await screen.findByText(AWS_EXPIRED_TITLE)).toBeInTheDocument();
    expect(screen.getByText(AWS_EXPIRED_BODY)).toBeInTheDocument();
    expect(submittedTokens()).toEqual([FORM_ATTEMPT]);
    expect(screen.queryByText("Link expired, sign in again.")).toBeNull();
    expect(screen.queryByLabelText("Full Name")).toBeNull();
    expect(mocks.clearOnboardingRecovery).toHaveBeenCalled();
  });

  it("a setup link that has ended before the page opens shows the AWS ended view without the form", async () => {
    mocks.params.set("onboarding_token", REDIRECT_CODE);
    mocks.prepareAWSOnboarding.mockRejectedValueOnce(
      new Error("Onboarding exchange failed"),
    );
    render(<JwtRegisterView />);

    expect(await screen.findByText(AWS_EXPIRED_TITLE)).toBeInTheDocument();
    expect(screen.getByText(AWS_EXPIRED_BODY)).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "Open AWS Marketplace subscriptions" }),
    ).toHaveAttribute("href", AWS_SUBSCRIPTIONS_URL);
    expect(screen.queryByText("Link expired, sign in again.")).toBeNull();
    expect(screen.queryByLabelText("Full Name")).toBeNull();
    expect(screen.queryByRole("button", { name: "Continue" })).toBeNull();
    expect(mocks.marketplaceRegister).not.toHaveBeenCalled();
    expect(mocks.clearOnboardingRecovery).toHaveBeenCalled();
  });
});
