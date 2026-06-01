import { beforeEach, describe, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { render, screen, waitFor } from "src/utils/test-utils";

import SetBudgetDialog from "./SetBudgetDialog";

const mockSetBudgetMutate = vi.fn();
const mockRecordActivationEvent = vi.fn();
const gatewayQuickStartQuery =
  "quick_start_goal=control_model_traffic&quick_start_id=gateway&quick_start_primary_path=gateway";

vi.mock("../providers/hooks/useGatewayConfig", () => ({
  useSetBudget: () => ({
    mutate: mockSetBudgetMutate,
    isPending: false,
    isError: false,
    error: null,
  }),
}));

vi.mock("src/sections/onboarding-home/api/onboarding-home-api", () => ({
  recordActivationEvent: (...args) => mockRecordActivationEvent(...args),
}));

vi.mock("notistack", () => ({
  enqueueSnackbar: vi.fn(),
}));

describe("SetBudgetDialog onboarding activation", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockRecordActivationEvent.mockResolvedValue({});
    mockSetBudgetMutate.mockImplementation((_payload, options) => {
      options?.onSuccess?.();
    });
  });

  it("records policy completion after saving an onboarding budget", async () => {
    window.history.pushState(
      {},
      "Budgets",
      `/dashboard/gateway/budgets?source=onboarding&request_id=req-123&${gatewayQuickStartQuery}`,
    );

    render(
      <SetBudgetDialog
        open
        onClose={vi.fn()}
        gatewayId="gateway-1"
        onboardingRequestId="req-123"
        shouldRecordOnboardingCompletion
      />,
    );

    await userEvent.click(screen.getByLabelText(/budget level/i));
    await userEvent.click(screen.getByRole("option", { name: /per model/i }));
    await userEvent.type(screen.getByLabelText(/monthly limit/i), "1000");
    await userEvent.clear(screen.getByLabelText(/alert threshold/i));
    await userEvent.type(screen.getByLabelText(/alert threshold/i), "75");
    await userEvent.click(screen.getByRole("button", { name: /^save$/i }));

    await waitFor(() => expect(mockSetBudgetMutate).toHaveBeenCalledTimes(1));
    expect(mockRecordActivationEvent).toHaveBeenCalledWith(
      expect.objectContaining({
        eventName: "gateway_policy_created",
        primaryPath: "gateway",
        stage: "add_gateway_policy",
        source: "gateway_budget_onboarding",
        quick_start_goal: "control_model_traffic",
        quick_start_id: "gateway",
        quick_start_primary_path: "gateway",
        metadata: expect.objectContaining({
          gateway_id: "gateway-1",
          request_id: "req-123",
          policy_type: "budget",
          policy_id: "budget:per_model",
          budget_level: "per_model",
          limit: 1000,
          alert_threshold: 75,
          on_exceed: "warn",
        }),
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

  it("records failed request repair before completing budget policy onboarding", async () => {
    window.history.pushState(
      {},
      "Budgets",
      `/dashboard/gateway/budgets?source=onboarding&request_id=req-123&repair_request=1&${gatewayQuickStartQuery}`,
    );

    render(
      <SetBudgetDialog
        open
        onClose={vi.fn()}
        gatewayId="gateway-1"
        onboardingRequestId="req-123"
        shouldRecordOnboardingCompletion
        shouldRecordOnboardingRepair
      />,
    );

    await userEvent.click(screen.getByLabelText(/budget level/i));
    await userEvent.click(screen.getByRole("option", { name: /per model/i }));
    await userEvent.type(screen.getByLabelText(/monthly limit/i), "1000");
    await userEvent.click(screen.getByRole("button", { name: /^save$/i }));

    await waitFor(() => expect(mockSetBudgetMutate).toHaveBeenCalledTimes(1));
    await waitFor(() =>
      expect(mockRecordActivationEvent).toHaveBeenCalledTimes(2),
    );
    expect(mockRecordActivationEvent.mock.calls[0][0]).toMatchObject({
      eventName: "gateway_failure_resolved",
      primaryPath: "gateway",
      stage: "fix_gateway_failure",
      source: "gateway_budget_onboarding",
      metadata: expect.objectContaining({
        request_id: "req-123",
        repair_type: "budget",
        budget_level: "per_model",
      }),
    });
    expect(mockRecordActivationEvent.mock.calls[1][0]).toMatchObject({
      eventName: "gateway_policy_created",
      stage: "add_gateway_policy",
    });
  });

  it("does not record policy completion for ordinary budget saves", async () => {
    render(<SetBudgetDialog open onClose={vi.fn()} gatewayId="gateway-1" />);

    await userEvent.click(screen.getByLabelText(/budget level/i));
    await userEvent.click(screen.getByRole("option", { name: /per model/i }));
    await userEvent.type(screen.getByLabelText(/monthly limit/i), "1000");
    await userEvent.click(screen.getByRole("button", { name: /^save$/i }));

    await waitFor(() => expect(mockSetBudgetMutate).toHaveBeenCalledTimes(1));
    expect(mockRecordActivationEvent).not.toHaveBeenCalled();
  });
});
