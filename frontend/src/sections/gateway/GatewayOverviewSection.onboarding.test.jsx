import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { render, screen, waitFor } from "src/utils/test-utils";

import GatewayOverviewSection from "./GatewayOverviewSection";

const mockNavigate = vi.hoisted(() => vi.fn());
const mockAxiosPost = vi.hoisted(() => vi.fn());
const mockRecordActivationEvent = vi.hoisted(() => vi.fn());
const mockRefreshGateways = vi.hoisted(() => vi.fn());
const mockEnqueueSnackbar = vi.hoisted(() => vi.fn());
const mockGateway = vi.hoisted(() => ({
  id: "gateway-1",
  name: "Default gateway",
  status: "healthy",
  baseUrl: "https://gateway.futureagi.dev/v1",
  providerCount: 1,
  modelCount: 1,
}));
const mockProviderHealth = vi.hoisted(() => ({
  providers: [{ name: "openai", status: "healthy" }],
}));
const gatewayQuickStartQuery =
  "quick_start_goal=control_model_traffic&quick_start_id=gateway&quick_start_primary_path=gateway";

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual("react-router-dom");
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  };
});

vi.mock("notistack", () => ({
  enqueueSnackbar: (...args) => mockEnqueueSnackbar(...args),
}));

vi.mock("src/utils/axios", () => ({
  default: {
    post: (...args) => mockAxiosPost(...args),
  },
  endpoints: {
    gateway: {
      healthCheck: (id) => `/agentcc/gateways/${id}/health_check/`,
      testPlayground: (id) => `/agentcc/gateways/${id}/test-playground/`,
    },
  },
}));

vi.mock("src/sections/onboarding-home/api/onboarding-home-api", () => ({
  recordActivationEvent: (...args) => mockRecordActivationEvent(...args),
}));

vi.mock("./context/useGatewayContext", () => ({
  useGatewayContext: () => ({
    gateway: mockGateway,
    gatewayId: "gateway-1",
    isLoading: false,
    error: null,
    refreshGateways: mockRefreshGateways,
  }),
}));

vi.mock("./providers/hooks/useGatewayConfig", () => ({
  useProviderHealth: () => ({
    data: mockProviderHealth,
  }),
}));

vi.mock("./analytics/hooks/useAnalyticsOverview", () => ({
  useAnalyticsOverview: () => ({
    data: {
      total_requests: { value: 0 },
      total_cost: { value: 0 },
      avg_latency_ms: null,
      error_rate: { value: 0 },
    },
  }),
}));

vi.mock("./keys/hooks/useApiKeys", () => ({
  useApiKeys: () => ({
    data: [{ id: "key-1", gatewayKeyId: "gw-key-1" }],
  }),
}));

const renderWithQueryClient = (ui) => {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });

  return render(
    <QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>,
  );
};

describe("GatewayOverviewSection onboarding request", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    Object.assign(mockGateway, {
      id: "gateway-1",
      name: "Default gateway",
      status: "healthy",
      baseUrl: "https://gateway.futureagi.dev/v1",
      providerCount: 1,
      modelCount: 1,
    });
    Object.assign(mockProviderHealth, {
      providers: [{ name: "openai", status: "healthy" }],
    });
    window.history.pushState(
      {},
      "Gateway",
      `/dashboard/gateway?onboarding=test-request&${gatewayQuickStartQuery}`,
    );
    mockAxiosPost.mockResolvedValue({
      data: {
        result: {
          status_code: 200,
          body: { id: "chatcmpl-1" },
          guardrail_headers: {},
          model: "gpt-4o-mini",
          request_id: "req-123",
          blocked: false,
          warned: false,
        },
      },
    });
    mockRecordActivationEvent.mockResolvedValue({
      recommendedAction: {
        href: "/dashboard/gateway/logs?onboarding=review-request&request_id=req-123",
      },
    });
  });

  it("sends a gateway request and records the onboarding signal", async () => {
    renderWithQueryClient(<GatewayOverviewSection />);

    expect(screen.getByTestId("gateway-onboarding-focus")).toBeVisible();
    await userEvent.click(
      screen.getByRole("button", { name: /send test request/i }),
    );

    await waitFor(() =>
      expect(mockAxiosPost).toHaveBeenCalledWith(
        "/agentcc/gateways/gateway-1/test-playground/",
        {
          prompt:
            "Send a short gateway onboarding request and reply with one sentence.",
        },
      ),
    );
    expect(mockRecordActivationEvent).toHaveBeenCalledWith(
      expect.objectContaining({
        eventName: "gateway_request_seen",
        primaryPath: "gateway",
        stage: "run_gateway_request",
        source: "gateway_request_onboarding",
        artifactType: "gateway_request",
        artifactId: "req-123",
        quick_start_goal: "control_model_traffic",
        quick_start_id: "gateway",
        quick_start_primary_path: "gateway",
        metadata: expect.objectContaining({
          gateway_id: "gateway-1",
          request_id: "req-123",
          status_code: 200,
          model: "gpt-4o-mini",
        }),
      }),
    );
    expect(mockRefreshGateways).toHaveBeenCalledTimes(1);
    expect(mockNavigate).toHaveBeenCalledWith(
      `/dashboard/gateway/logs?onboarding=review-request&request_id=req-123&${gatewayQuickStartQuery}`,
    );
  });

  it("shows request focus from Home journey-step params", () => {
    window.history.pushState(
      {},
      "Gateway",
      "/dashboard/gateway?tour_anchor=gateway_request_button&journey_step=run_gateway_request",
    );

    renderWithQueryClient(<GatewayOverviewSection />);

    expect(screen.getByTestId("gateway-onboarding-focus")).toBeVisible();
    expect(screen.getByText("Send the first gateway request")).toBeVisible();
    expect(
      screen.getByRole("button", { name: /send test request/i }),
    ).toHaveAttribute("data-tour-anchor", "gateway_request_button");
  });

  it("uses provider health as provider setup evidence when gateway counts lag", () => {
    mockGateway.providerCount = 0;
    mockProviderHealth.providers = [{ name: "openai", status: "healthy" }];

    renderWithQueryClient(<GatewayOverviewSection />);

    expect(
      screen.getByRole("button", { name: /send test request/i }),
    ).toBeVisible();
    expect(
      screen.queryByRole("button", { name: /^add provider$/i }),
    ).not.toBeInTheDocument();
  });

  it("continues to request review when onboarding state recording fails", async () => {
    mockRecordActivationEvent.mockRejectedValueOnce(new Error("unavailable"));

    renderWithQueryClient(<GatewayOverviewSection />);

    await userEvent.click(
      screen.getByRole("button", { name: /send test request/i }),
    );

    await waitFor(() =>
      expect(mockNavigate).toHaveBeenCalledWith(
        `/dashboard/gateway/logs?onboarding=review-request&request_id=req-123&${gatewayQuickStartQuery}`,
      ),
    );
    expect(mockRefreshGateways).toHaveBeenCalledTimes(1);
    expect(mockEnqueueSnackbar).toHaveBeenCalledWith("Gateway request sent", {
      variant: "success",
    });
  });
});
