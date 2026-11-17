import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { paths } from "src/routes/paths";
import TwoFactorVerifyView from "../two-factor-verify-view";

const mocks = vi.hoisted(() => ({
  post: vi.fn(),
  login: vi.fn(),
  navigate: vi.fn(),
  push: vi.fn(),
  enqueueSnackbar: vi.fn(),
  setSession: vi.fn(),
  setRefreshToken: vi.fn(),
}));

vi.mock("react-router-dom", () => ({
  useNavigate: () => mocks.navigate,
  useLocation: () => ({ state: null }),
}));
vi.mock("src/routes/hooks", () => ({
  useRouter: () => ({ push: mocks.push }),
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ login: mocks.login }),
}));
vi.mock("src/auth/context/jwt/utils", () => ({
  setSession: mocks.setSession,
  setRefreshToken: mocks.setRefreshToken,
}));
vi.mock("src/hooks/useDeploymentMode", () => ({
  usePostLoginPath: () => "/dashboard",
}));
vi.mock("src/utils/axios", () => ({
  default: { post: mocks.post },
  endpoints: {
    twoFactor: {
      verify: {
        totp: "/accounts/2fa/verify/totp/",
        recovery: "/accounts/2fa/verify/recovery/",
      },
    },
  },
}));
vi.mock("src/components/snackbar", () => ({
  useSnackbar: () => ({ enqueueSnackbar: mocks.enqueueSnackbar }),
}));
vi.mock("@simplewebauthn/browser", () => ({
  startAuthentication: vi.fn(),
}));
vi.mock("../RightSectionAuth", () => ({ default: () => null }));

const challenge = {
  challengeToken: "test-verification-challenge",
  methods: ["totp", "recovery"],
  email: "member@example.com",
};
const storedChallenge = JSON.stringify(challenge);
const loginData = { access: "fixture-access", refresh: "fixture-refresh" };
const factors = [
  {
    method: "totp",
    label: "Authentication code",
    invalidCode: "000000",
    correctedCode: "123456",
    backendError: "Invalid code. Please try again.",
    retryMessage: "Invalid code. Please try again.",
    endpoint: "/accounts/2fa/verify/totp/",
  },
  {
    method: "recovery",
    label: "Recovery code",
    invalidCode: "wrong-code",
    correctedCode: "correct-code",
    backendError: "Invalid or already used recovery code.",
    retryMessage: "Invalid recovery code. Please try again.",
    endpoint: "/accounts/2fa/verify/recovery/",
  },
];

async function renderFactor(method) {
  const user = userEvent.setup();
  render(<TwoFactorVerifyView />);
  if (method === "recovery") {
    await user.click(screen.getByRole("button", { name: "Use a recovery code" }));
  }
  return user;
}

// These are the backend's top-level error strings after Axios normalization.
// The challenge remains usable after a wrong factor, but not a session error.
describe("H14-auth-security-002 MFA verification retries", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    sessionStorage.clear();
    localStorage.clear();
    sessionStorage.setItem("2fa_challenge", storedChallenge);
  });

  it.each(factors)(
    "retains the challenge after an incorrect $method code and accepts a corrected retry",
    async ({
      method,
      label,
      invalidCode,
      correctedCode,
      backendError,
      retryMessage,
      endpoint,
    }) => {
      mocks.post
        .mockRejectedValueOnce({ error: backendError, statusCode: 400 })
        .mockResolvedValueOnce({ data: loginData });
      const user = await renderFactor(method);
      const input = screen.getByRole("textbox", { name: label });
      const submit = screen.getByRole("button", { name: "Verify", exact: true });

      await user.type(input, invalidCode);
      await user.click(submit);

      expect(await screen.findByText(retryMessage)).toBeInTheDocument();
      expect(sessionStorage.getItem("2fa_challenge")).toBe(storedChallenge);
      expect(submit).toBeEnabled();
      expect(mocks.navigate).not.toHaveBeenCalled();
      expect(mocks.enqueueSnackbar).not.toHaveBeenCalled();
      expect(mocks.login).not.toHaveBeenCalled();

      await user.clear(input);
      await user.type(input, correctedCode);
      await user.click(submit);

      await waitFor(() => expect(mocks.push).toHaveBeenCalledWith("/dashboard"));
      expect(mocks.post).toHaveBeenCalledTimes(2);
      expect(mocks.post).toHaveBeenNthCalledWith(1, endpoint, {
        challenge_token: challenge.challengeToken,
        code: invalidCode,
      });
      expect(mocks.post).toHaveBeenNthCalledWith(2, endpoint, {
        challenge_token: challenge.challengeToken,
        code: correctedCode,
      });
      expect(mocks.login).toHaveBeenCalledTimes(1);
      expect(mocks.login).toHaveBeenCalledWith({ status: 200, data: loginData });
      expect(mocks.setSession).toHaveBeenCalledWith(loginData.access, null);
      expect(mocks.setRefreshToken).toHaveBeenCalledWith(loginData.refresh);
      expect(sessionStorage.getItem("2fa_challenge")).toBeNull();
      expect(screen.queryByText(retryMessage)).not.toBeInTheDocument();
      expect(mocks.navigate).not.toHaveBeenCalled();
    },
  );

  it.each(
    factors.flatMap((factor) =>
      [
        "Invalid or expired verification session.",
        "Invalid verification session.",
      ].map((sessionError) => ({ ...factor, sessionError })),
    ),
  )(
    "still clears the challenge and redirects for $method: $sessionError",
    async ({ method, label, invalidCode, retryMessage, sessionError }) => {
      mocks.post.mockRejectedValueOnce({ error: sessionError, statusCode: 400 });
      const user = await renderFactor(method);
      await user.type(screen.getByRole("textbox", { name: label }), invalidCode);
      await user.click(
        screen.getByRole("button", { name: "Verify", exact: true }),
      );

      await waitFor(() =>
        expect(mocks.navigate).toHaveBeenCalledWith(paths.auth.jwt.login),
      );
      expect(mocks.navigate).toHaveBeenCalledTimes(1);
      expect(sessionStorage.getItem("2fa_challenge")).toBeNull();
      expect(mocks.enqueueSnackbar).toHaveBeenCalledWith(
        "Verification session expired. Please log in again.",
        { variant: "error" },
      );
      expect(screen.queryByText(retryMessage)).not.toBeInTheDocument();
      expect(mocks.login).not.toHaveBeenCalled();
      expect(mocks.push).not.toHaveBeenCalled();
    },
  );

  it("still redirects to login when no challenge is stored", async () => {
    sessionStorage.removeItem("2fa_challenge");
    render(<TwoFactorVerifyView />);

    await waitFor(() =>
      expect(mocks.navigate).toHaveBeenCalledWith(paths.auth.jwt.login),
    );
    expect(mocks.post).not.toHaveBeenCalled();
    expect(
      screen.queryByRole("button", { name: "Verify" }),
    ).not.toBeInTheDocument();
  });
});
