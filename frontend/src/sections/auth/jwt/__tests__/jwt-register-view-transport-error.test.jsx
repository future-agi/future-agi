/* eslint-disable react/prop-types */
import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import JwtRegisterView from "src/sections/auth/jwt/jwt-register-view";

const mocks = vi.hoisted(() => ({
  register: vi.fn(),
  marketplaceRegister: vi.fn(),
  login: vi.fn(),
  navigate: vi.fn(),
  get: vi.fn(),
  enqueueSnackbar: vi.fn(),
  isOSS: false,
  reader: {
    get: () => null,
    clear: () => {},
    release: () => {},
  },
}));

// Plain sign-up: no marketplace onboarding token in the URL.
vi.mock("src/auth/auth-url-intake", async (importOriginal) => ({
  ...(await importOriginal()),
  clearOnboardingRecovery: vi.fn(),
  useAuthRedirectParams: () => mocks.reader,
}));
vi.mock("src/auth/hooks", () => ({ useAuthContext: () => mocks }));
vi.mock("react-router-dom", async (importOriginal) => ({
  ...(await importOriginal()),
  useNavigate: () => mocks.navigate,
  useLocation: () => ({ search: "" }),
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
vi.mock("src/components/snackbar", () => ({
  enqueueSnackbar: mocks.enqueueSnackbar,
}));
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

const CONNECTION_MESSAGE =
  "We couldn't reach FutureAGI. Check your connection and try again.";
const SIGNUP_FAILED_MESSAGE =
  "We couldn't create your account right now. Please try again in a minute.";

// Same shape as the response interceptor in src/utils/axios.js rejects with:
// response.data spread into an object (a non-JSON body is a string, so it
// becomes character keys), or only Axios' message when nothing came back.
function axiosRejection({ data, message, status, code }) {
  const errData = data || { message };
  return {
    ...errData,
    statusCode: status,
    transportCode: code,
    responseHeaders: { "x-request-id": undefined },
  };
}

async function fillAndSubmit() {
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
  fireEvent.click(
    screen.getByRole("button", { name: "Continue", exact: true }),
  );
  await waitFor(() => expect(mocks.register).toHaveBeenCalled());
}

async function expectRetryableFailure(expected) {
  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent(expected);
  // The user keeps what they typed and can press Continue again.
  expect(screen.getByLabelText("Full Name")).toHaveValue("Fake Person");
  expect(
    screen.getByLabelText(mocks.isOSS ? "Email ID" : "Business Email ID"),
  ).toHaveValue("fake@example.org");
  expect(
    screen.getByRole("button", { name: "Continue", exact: true }),
  ).toBeEnabled();
}

describe("W10-AUTH-ORG-U05 sign-up failure without an API error body", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.register.mockReset();
    mocks.isOSS = false;
  });

  it.each([
    [
      "the network drops",
      { message: "Network Error", code: "ERR_NETWORK" },
      CONNECTION_MESSAGE,
    ],
    [
      "the request times out",
      { message: "timeout of 30000ms exceeded", code: "ECONNABORTED" },
      CONNECTION_MESSAGE,
    ],
    [
      "a gateway returns a non-JSON 502 page",
      {
        data: "<html><body><h1>502 Bad Gateway</h1></body></html>",
        status: 502,
        code: "ERR_BAD_RESPONSE",
      },
      SIGNUP_FAILED_MESSAGE,
    ],
    [
      "a gateway returns an empty 503",
      {
        data: "",
        message: "Request failed with status code 503",
        status: 503,
        code: "ERR_BAD_RESPONSE",
      },
      SIGNUP_FAILED_MESSAGE,
    ],
  ])(
    "hosted: tells the user what happened when %s",
    async (_case, failure, expected) => {
      mocks.register.mockRejectedValueOnce(axiosRejection(failure));
      render(<JwtRegisterView />);

      await fillAndSubmit();

      await expectRetryableFailure(expected);
    },
  );

  it("self-hosted: tells the user to check the connection when the network drops", async () => {
    mocks.isOSS = true;
    mocks.register.mockRejectedValueOnce(
      axiosRejection({ message: "Network Error", code: "ERR_NETWORK" }),
    );
    render(<JwtRegisterView />);

    await fillAndSubmit();

    await expectRetryableFailure(CONNECTION_MESSAGE);
  });

  it("still shows the server's own message when the API sends one", async () => {
    mocks.register.mockRejectedValueOnce({
      error: "An account with this email already exists",
      statusCode: 400,
      transportCode: "ERR_BAD_REQUEST",
      responseHeaders: { "x-request-id": undefined },
    });
    render(<JwtRegisterView />);

    await fillAndSubmit();

    await expectRetryableFailure("An account with this email already exists");
    expect(screen.queryByText(SIGNUP_FAILED_MESSAGE)).toBeNull();
    expect(screen.queryByText(CONNECTION_MESSAGE)).toBeNull();
  });
});
