import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import OssSetupView from "../OssSetupView";

const h = vi.hoisted(() => ({
  navigate: vi.fn(),
  authenticated: false,
  mode: "oss",
}));

vi.mock("react-router-dom", () => ({
  // eslint-disable-next-line react/prop-types
  Navigate: ({ to }) => <p>{`redirect:${to}`}</p>,
  useNavigate: () => h.navigate,
}));
vi.mock("src/routes/paths", () => ({
  paths: {
    auth: { jwt: { login: "/auth/jwt/login", register: "/auth/jwt/register" } },
  },
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ authenticated: h.authenticated }),
}));
vi.mock("src/hooks/useDeploymentMode", () => ({
  useDeploymentMode: () => ({
    mode: h.mode,
    isOSS: h.mode === "oss",
    isEE: h.mode === "ee",
    isCloud: h.mode === "cloud",
    isSelfHosted: h.mode !== "cloud",
    isLoading: false,
    isSuccess: true,
  }),
  usePostLoginPath: () => "/dashboard",
}));
vi.mock("src/components/loading-screen", () => ({ SplashScreen: () => null }));
vi.mock("../OssSetupShell", () => ({ default: ({ children }) => children }));
vi.mock("../HorizontalSpaceship", () => ({ default: () => null }));
vi.mock("../LaunchModeStep", () => ({
  default: ({ onContinue }) => (
    <button type="button" onClick={onContinue}>
      Pick mode
    </button>
  ),
}));
// Continue as the validation step sends it, with what the server said.
vi.mock("../ValidationStep", () => ({
  default: ({ onContinue }) => (
    <>
      <button type="button" onClick={() => onContinue({ accountExists: true })}>
        Continue with an account
      </button>
      <button
        type="button"
        onClick={() => onContinue({ accountExists: false })}
      >
        Continue without one
      </button>
    </>
  ),
}));
vi.mock("../ossFlowState", () => ({ markValidationDone: vi.fn() }));

const continueWith = (label) => {
  render(<OssSetupView />);
  fireEvent.click(screen.getByRole("button", { name: "Pick mode" }));
  fireEvent.click(screen.getByRole("button", { name: label }));
};

beforeEach(() => {
  vi.clearAllMocks();
  h.authenticated = false;
  h.mode = "oss";
});

describe("OssSetupView", () => {
  it("sends people to sign in when the install made an account", () => {
    continueWith("Continue with an account");
    expect(h.navigate).toHaveBeenCalledWith("/auth/jwt/login");
  });

  it("sends people to sign up while there is no account", () => {
    continueWith("Continue without one");
    expect(h.navigate).toHaveBeenCalledWith("/auth/jwt/register");
  });

  it("sends a signed-in operator to the app", () => {
    h.authenticated = true;
    continueWith("Continue with an account");
    expect(h.navigate).toHaveBeenCalledWith("/dashboard");
  });
});

// TH-8084 option 1: a licence does not take the first-run screen away.
describe("OssSetupView by deployment", () => {
  it("ee: a licensed self-hosted install runs the checks", () => {
    h.mode = "ee";
    continueWith("Continue with an account");
    expect(h.navigate).toHaveBeenCalledWith("/auth/jwt/login");
  });

  it("cloud: a typed /setup goes to the app", () => {
    h.mode = "cloud";
    render(<OssSetupView />);
    expect(screen.getByText("redirect:/dashboard")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Pick mode" })).toBeNull();
  });
});
