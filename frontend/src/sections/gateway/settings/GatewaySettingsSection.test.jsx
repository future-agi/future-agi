import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";
import GatewaySettingsSection from "./GatewaySettingsSection";

const mockNavigate = vi.fn();
const mockReloadMutate = vi.fn();
const mockHealthCheckMutate = vi.fn();
const mockInvalidateQueries = vi.fn();
const mockEnqueueSnackbar = vi.fn();

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual("react-router-dom");
  return {
    ...actual,
    useNavigate: () => mockNavigate,
    useParams: () => ({ tab: "settings" }),
  };
});

vi.mock("notistack", () => ({
  enqueueSnackbar: (...args) => mockEnqueueSnackbar(...args),
}));

// Batch Jobs' Submit and Cancel are Owner or Admin only (useGatewayAdmin);
// these cases act as an Admin.
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: 8 }),
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
      baseUrl: "https://example.com",
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
  });

  it("shows a success toast after reload config completes", async () => {
    const user = userEvent.setup();

    render(<GatewaySettingsSection />);

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
