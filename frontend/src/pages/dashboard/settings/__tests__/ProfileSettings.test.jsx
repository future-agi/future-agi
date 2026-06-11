import { describe, it, expect, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { HelmetProvider } from "react-helmet-async";
import { render, screen, waitFor } from "src/utils/test-utils";

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  updateUserData: vi.fn(),
}));

vi.mock("src/utils/axios", () => ({
  default: {
    get: mocks.get,
    post: mocks.post,
  },
  endpoints: {
    twoFactor: {
      status: "/accounts/2fa/status/",
    },
    stripe: {
      getUserProfileDetails: "/accounts/get-user-profile-details/",
      updateUserFullName: "/accounts/update-user-full-name/",
    },
    auth: {
      passwordResetInitiate: "/accounts/password-reset-initiate/",
    },
  },
}));

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ updateUserData: mocks.updateUserData }),
}));

vi.mock("src/sections/settings/Security/TotpSection", () => ({
  default: ({ totp }) => (
    <div data-testid="totp-section">totp:{String(totp?.enabled)}</div>
  ),
}));

vi.mock("src/sections/settings/Security/PasskeySection", () => ({
  default: ({ passkey }) => (
    <div data-testid="passkey-section">passkey:{String(passkey?.enabled)}</div>
  ),
}));

vi.mock("src/sections/settings/Security/RecoveryCodesSection", () => ({
  default: ({ remaining, hasTotp }) => (
    <div data-testid="recovery-codes-section">
      recovery:{remaining};hasTotp:{String(hasTotp)}
    </div>
  ),
}));

vi.mock("src/utils/Mixpanel", () => ({
  trackEvent: vi.fn(),
  Events: {
    editNameClicked: "editNameClicked",
    restPassClicked: "restPassClicked",
    updateFullNameClicked: "updateFullNameClicked",
  },
  PropertyName: { formFields: "formFields" },
}));

vi.mock("notistack", () => ({
  enqueueSnackbar: vi.fn(),
}));

vi.mock("src/utils/logger", () => ({
  default: { error: vi.fn() },
}));

function renderWithProviders(ui) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <HelmetProvider>
      <QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>
    </HelmetProvider>,
  );
}

describe("ProfileSettings", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.get.mockImplementation((url) => {
      if (url === "/accounts/2fa/status/") {
        return Promise.resolve({
          data: {
            two_factor_enabled: true,
            methods: {
              totp: { enabled: true, confirmed_at: "2026-05-23T00:00:00Z" },
              passkey: { enabled: false, count: 0 },
            },
            recovery_codes_remaining: 4,
          },
        });
      }
      if (url === "/accounts/get-user-profile-details/") {
        return Promise.resolve({
          data: {
            name: "Kartik",
            email: "kartik.nvj@futureagi.com",
            org_name: "Future AGI",
          },
        });
      }
      return Promise.reject(new Error(`Unexpected GET ${url}`));
    });
  });

  it("renders recovery codes from canonical snake_case 2FA status fields", async () => {
    const { default: ProfileSettings } = await import("../ProfileSettings");

    renderWithProviders(<ProfileSettings />);

    await waitFor(() => {
      expect(screen.getByText("Kartik")).toBeInTheDocument();
    });
    expect(await screen.findByTestId("totp-section")).toHaveTextContent(
      "totp:true",
    );
    expect(screen.getByTestId("passkey-section")).toHaveTextContent(
      "passkey:false",
    );
    expect(screen.getByTestId("recovery-codes-section")).toHaveTextContent(
      "recovery:4;hasTotp:true",
    );
  });
});
