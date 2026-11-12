import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";
import { HelmetProvider } from "react-helmet-async";
import GatewaySettingsSection from "./GatewaySettingsSection";

const mockNavigate = vi.fn();
const mockReloadMutate = vi.fn();
const mockHealthCheckMutate = vi.fn();
const mockInvalidateQueries = vi.fn();
const mockEnqueueSnackbar = vi.fn();
const mockGateway = vi.hoisted(() => ({ baseUrl: "https://example.com" }));

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual("react-router-dom");
  return {
    ...actual,
    useNavigate: () => mockNavigate,
    useParams: () => ({ tab: "settings" }),
  };
});

vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar: (...args) => mockEnqueueSnackbar(...args),
}));

// Batch Jobs' Submit and Cancel are Owner or Admin only (useGatewayAdmin);
// these cases act as an Admin.
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: 8 }),
}));

// General links central Billing for billing roles (BillingCreditsPanel); this
// test is about the page's actions, so the viewer is a member.
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ user: { organization_role: "Member" } }),
}));
vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => ({ currentWorkspaceRole: null }),
}));
vi.mock("src/sections/settings/BillingV2/useCentralBilling", () => ({
  useCentralBilling: () => ({ visible: false, credits: null }),
}));

vi.mock("@tanstack/react-query", async () => {
  const actual = await vi.importActual("@tanstack/react-query");
  return {
    ...actual,
    useQueryClient: () => ({ invalidateQueries: mockInvalidateQueries }),
    useMutation: (options) => ({
      isPending: false,
      mutate: (...args) => {
        mockHealthCheckMutate(...args);
        const [, callbacks] = args;
        if (callbacks?.onSuccess) callbacks.onSuccess();
        else if (options?.onSuccess) options.onSuccess();
      },
    }),
  };
});

vi.mock("../providers/hooks/useGatewayConfig", () => ({
  useGatewayConfig: () => ({
    data: {
      server: { host: "localhost", port: 8080 },
      logging: {},
      cost_tracking: {},
    },
    isLoading: false,
  }),
  useReloadConfig: () => ({
    isPending: false,
    mutate: (...args) => {
      mockReloadMutate(...args);
      const [, callbacks] = args;
      if (callbacks?.onSuccess) callbacks.onSuccess();
    },
  }),
}));

vi.mock("../context/useGatewayContext", () => ({
  useGatewayContext: () => ({
    gatewayId: "gateway-1",
    isLoading: false,
    gateway: {
      id: "gateway-1",
      name: "Gateway One",
      baseUrl: mockGateway.baseUrl,
      status: "healthy",
      providerCount: 3,
      modelCount: 5,
    },
  }),
}));

vi.mock("./OrgConfigSection", () => ({
  default: () => <div>Org config section</div>,
}));

vi.mock("./EmailAlertsCard", () => ({
  default: () => <div>Email alerts card</div>,
}));

vi.mock("./hooks/useBatchJobs", () => ({
  useSubmitBatch: () => ({ mutate: vi.fn(), isPending: false }),
  useBatchStatus: () => ({ data: [], isLoading: false }),
  useCancelBatch: () => ({ mutate: vi.fn(), isPending: false }),
}));

describe("GatewaySettingsSection", () => {
  beforeEach(() => {
    mockNavigate.mockReset();
    mockReloadMutate.mockReset();
    mockHealthCheckMutate.mockReset();
    mockInvalidateQueries.mockReset();
    mockEnqueueSnackbar.mockReset();
    mockGateway.baseUrl = "https://example.com";
  });

  it("shows an explicit message when the gateway URL is not configured", () => {
    mockGateway.baseUrl = "";
    render(
      <HelmetProvider>
        <GatewaySettingsSection />
      </HelmetProvider>,
    );
    expect(screen.getByText("Gateway URL not configured")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Copy endpoint" })).toBeNull();
  });

  it("shows a success toast after reload config completes", async () => {
    const user = userEvent.setup();

    render(
      <HelmetProvider>
        <GatewaySettingsSection />
      </HelmetProvider>,
    );

    await user.click(screen.getByRole("button", { name: /reload config/i }));

    expect(mockReloadMutate).toHaveBeenCalledWith(
      "gateway-1",
      expect.objectContaining({
        onSuccess: expect.any(Function),
        onError: expect.any(Function),
      }),
    );

    expect(mockEnqueueSnackbar).toHaveBeenCalledWith("Configuration reloaded", {
      variant: "success",
    });
  });
});
