import { beforeEach, describe, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { render, screen, waitFor } from "src/utils/test-utils";

import FallbacksSection from "./FallbacksSection";

const mockSaveRouting = vi.fn();
const mockRecordActivationEvent = vi.fn();

let hookReturn;
const gatewayQuickStartQuery =
  "quick_start_goal=control_model_traffic&quick_start_id=gateway&quick_start_primary_path=gateway";

vi.mock("./hooks/useFallbackConfig", () => ({
  useFallbackConfig: () => hookReturn,
}));

vi.mock("../providers/hooks/useGatewayConfig", () => ({
  useProviderHealth: () => ({
    data: {
      providers: [
        {
          name: "openai",
          models: ["gpt-4o", "gpt-4o-mini"],
        },
      ],
    },
  }),
}));

vi.mock("../context/useGatewayContext", () => ({
  useGatewayContext: () => ({
    gatewayId: "gateway-1",
    gateway: { id: "gateway-1" },
  }),
}));

vi.mock("src/sections/onboarding-home/api/onboarding-home-api", () => ({
  recordActivationEvent: (...args) => mockRecordActivationEvent(...args),
}));

describe("FallbacksSection onboarding activation", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockSaveRouting.mockResolvedValue({});
    mockRecordActivationEvent.mockResolvedValue({});
    hookReturn = {
      routing: {
        fallback_enabled: true,
        default_model: "gpt-4o",
        model_fallbacks: {},
        failover: { enabled: true },
        retry: { enabled: false },
        circuit_breaker: { enabled: false },
        model_timeouts: {},
      },
      isLoading: false,
      error: null,
      refetch: vi.fn(),
      saveRouting: mockSaveRouting,
      isSaving: false,
    };
  });

  it("records policy completion after saving onboarding fallback recovery", async () => {
    window.history.pushState(
      {},
      "Fallbacks",
      `/dashboard/gateway/fallbacks?journey_step=add_gateway_policy&request_id=req-123&${gatewayQuickStartQuery}`,
    );

    render(<FallbacksSection />);

    await userEvent.click(
      screen.getAllByRole("button", { name: /add fallback chain/i })[0],
    );
    await userEvent.click(
      screen.getAllByRole("button", { name: /save & apply/i })[0],
    );

    await waitFor(() => expect(mockSaveRouting).toHaveBeenCalledTimes(1));
    expect(mockRecordActivationEvent).toHaveBeenCalledWith(
      expect.objectContaining({
        eventName: "gateway_policy_created",
        primaryPath: "gateway",
        stage: "add_gateway_policy",
        source: "gateway_fallbacks_onboarding",
        artifactType: "gateway_policy",
        artifactId: "req-123",
        quick_start_goal: "control_model_traffic",
        quick_start_id: "gateway",
        quick_start_primary_path: "gateway",
        metadata: expect.objectContaining({
          gateway_id: "gateway-1",
          request_id: "req-123",
          policy_type: "fallback",
          fallback_chain_count: 1,
        }),
        idempotencyKey: "gateway_policy_created:fallback:req-123:gateway-1",
      }),
    );
    await waitFor(() => {
      expect(window.location.pathname).toBe("/dashboard/home");
    });
    expect(new URLSearchParams(window.location.search).get("mode")).toBe(
      "daily-quality",
    );
    expect(
      new URLSearchParams(window.location.search).get("target_event"),
    ).toBe("gateway_policy_created");
    expect(
      new URLSearchParams(window.location.search).get("quick_start_id"),
    ).toBe("gateway");
  });

  it("does not record policy completion outside onboarding route context", async () => {
    window.history.pushState({}, "Fallbacks", "/dashboard/gateway/fallbacks");

    render(<FallbacksSection />);

    await userEvent.click(
      screen.getAllByRole("button", { name: /add fallback chain/i })[0],
    );
    await userEvent.click(
      screen.getAllByRole("button", { name: /save & apply/i })[0],
    );

    await waitFor(() => expect(mockSaveRouting).toHaveBeenCalledTimes(1));
    expect(mockRecordActivationEvent).not.toHaveBeenCalled();
  });
});
