import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import JwtRegisterView from "../jwt-register-view";

// TH-8084: signup follows where the install runs, not whether it holds a
// licence. A licensed self-hosted install reports mode "ee"; the register
// view used to treat that like Cloud (no password fields, reCAPTCHA token
// required, "check your email"), so a self-hoster who activated Enterprise
// could no longer sign up.

const h = vi.hoisted(() => ({
  mode: "ee",
  register: vi.fn(),
  login: vi.fn(),
  recaptcha: vi.fn(),
  navigate: vi.fn(),
}));

vi.mock("react-router-dom", () => ({
  useNavigate: () => h.navigate,
  useLocation: () => ({ search: "" }),
}));
vi.mock("src/routes/paths", () => ({
  paths: {
    auth: { jwt: { login: "/auth/jwt/login", setup_org: "/auth/jwt/setup-org", sso: "/sso" } },
    dashboard: { getstarted: "/dashboard/get-started" },
  },
}));
vi.mock("src/routes/components", () => ({
  RouterLink: ({ children }) => children,
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({
    register: h.register,
    login: h.login,
    marketplaceRegister: vi.fn(),
  }),
}));
vi.mock("src/hooks/useDeploymentMode", () => ({
  useDeploymentMode: () => ({
    mode: h.mode,
    isOSS: h.mode === "oss",
    isEE: h.mode === "ee",
    isCloud: h.mode === "cloud",
    isLoading: false,
    isSuccess: true,
  }),
  usePostLoginPath: () => "/dashboard/get-started",
}));
vi.mock("src/utils/recaptchaService", () => ({
  getRecaptchaToken: (...args) => h.recaptcha(...args),
}));
vi.mock("src/components/snackbar", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("src/utils/Mixpanel", () => ({
  Events: {},
  PropertyName: {},
  trackEvent: vi.fn(),
}));
vi.mock("src/utils/googleAds", () => ({ trackSignupConversion: vi.fn() }));
vi.mock("src/utils/redditAds", () => ({ trackRedditSignup: vi.fn() }));
vi.mock("src/utils/twitterAds", () => ({ trackTwitterSignup: vi.fn() }));
vi.mock("src/components/RegionSelect", () => ({ default: () => null }));
vi.mock("../RightSectionAuth", () => ({ default: () => null }));
vi.mock("../password-sent-view", () => ({ default: () => null }));
vi.mock("src/components/svg-color", () => ({ default: () => null }));
vi.mock("src/components/iconify", () => ({ default: () => null }));

const fillAndSubmit = async ({ withPassword }) => {
  fireEvent.change(screen.getByLabelText(/full name/i), {
    target: { value: "Self Hoster" },
  });
  fireEvent.change(screen.getByLabelText(/email id/i), {
    target: { value: "owner@futureagi.com" },
  });
  if (withPassword) {
    const password = screen.getByLabelText(/set password/i);
    const confirm = screen.getByLabelText(/confirm password/i);
    fireEvent.change(password, { target: { value: "Futureagi@45xyz" } });
    fireEvent.change(confirm, { target: { value: "Futureagi@45xyz" } });
  }
  fireEvent.click(screen.getByRole("button", { name: "Continue" }));
};

beforeEach(() => {
  vi.clearAllMocks();
  h.register.mockResolvedValue({
    result: { access: "a", refresh: "r", new_org: true },
  });
  h.recaptcha.mockRejectedValue(new Error("no reCAPTCHA site key"));
});

describe("JwtRegisterView: licensed self-hosted (mode ee)", () => {
  it("asks for the password and signs in without reCAPTCHA", async () => {
    h.mode = "ee";
    render(<JwtRegisterView />);

    expect(screen.getByLabelText(/set password/i)).toBeInTheDocument();
    await fillAndSubmit({ withPassword: true });

    await waitFor(() => expect(h.register).toHaveBeenCalledTimes(1));
    expect(h.register.mock.calls[0][0]).toMatchObject({
      email: "owner@futureagi.com",
      password: "Futureagi@45xyz",
    });
    expect(h.recaptcha).not.toHaveBeenCalled();
    await waitFor(() => expect(h.login).toHaveBeenCalledTimes(1));
    expect(h.navigate).toHaveBeenCalledWith("/auth/jwt/setup-org");
  });
});

describe("JwtRegisterView: Cloud is unchanged", () => {
  it("has no password fields and needs a reCAPTCHA token", async () => {
    h.mode = "cloud";
    render(<JwtRegisterView />);

    expect(screen.queryByLabelText(/set password/i)).toBeNull();
    await fillAndSubmit({ withPassword: false });

    await waitFor(() => expect(h.recaptcha).toHaveBeenCalledTimes(1));
    expect(h.register).not.toHaveBeenCalled();
  });
});
