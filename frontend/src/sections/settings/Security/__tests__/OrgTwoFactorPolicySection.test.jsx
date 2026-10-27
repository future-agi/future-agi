import { describe, it, expect, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, fireEvent } from "src/utils/test-utils";
import OrgTwoFactorPolicySection from "../OrgTwoFactorPolicySection";

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  put: vi.fn(),
  onStatusChange: vi.fn(),
}));

vi.mock("src/utils/axios", () => ({
  default: {
    get: mocks.get,
    put: mocks.put,
  },
  endpoints: {
    orgPolicy: {
      twoFactor: "/accounts/organization/2fa-policy/",
    },
    twoFactor: {
      status: "/accounts/2fa/status/",
    },
  },
}));

vi.mock("notistack", () => ({
  enqueueSnackbar: vi.fn(),
}));

vi.mock("src/components/svg-color", () => ({
  default: () => <span data-testid="svg-color" />,
}));

function renderWithQueryClient(ui) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });

  return render(
    <QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>,
  );
}

describe("OrgTwoFactorPolicySection", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.get.mockImplementation((url) => {
      if (url === "/accounts/organization/2fa-policy/") {
        return Promise.resolve({
          data: {
            require_2fa: true,
            require_2fa_grace_period_days: 14,
            require_2fa_enforced_at: "2026-06-01T00:00:00Z",
          },
        });
      }
      if (url === "/accounts/2fa/status/") {
        return Promise.resolve({
          data: {
            two_factor_enabled: true,
            methods: {
              totp: { enabled: true, confirmed_at: "2026-06-01T00:00:00Z" },
              passkey: { enabled: false, count: 0 },
            },
            recovery_codes_remaining: 10,
          },
        });
      }
      return Promise.reject(new Error(`Unexpected GET ${url}`));
    });
    mocks.put.mockResolvedValue({
      data: {
        require_2fa: true,
        require_2fa_grace_period_days: 30,
        require_2fa_enforced_at: "2026-06-01T00:00:00Z",
      },
    });
  });

  it("clamps grace period updates to the API contract maximum", async () => {
    renderWithQueryClient(
      <OrgTwoFactorPolicySection onStatusChange={mocks.onStatusChange} />,
    );

    const input = await screen.findByRole("spinbutton");
    expect(input).toHaveAttribute("max", "30");

    fireEvent.change(input, { target: { value: "90" } });
    fireEvent.click(await screen.findByRole("button", { name: /save/i }));

    await waitFor(() => {
      expect(mocks.put).toHaveBeenCalledWith(
        "/accounts/organization/2fa-policy/",
        {
          require_2fa: true,
          require_2fa_grace_period_days: 30,
        },
      );
    });
  });
});
