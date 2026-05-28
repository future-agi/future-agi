import { beforeEach, describe, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { render, screen, waitFor } from "src/utils/test-utils";

import SetBudgetDialog from "./SetBudgetDialog";

const mockSetBudgetMutate = vi.fn();
const mockRecordActivationEvent = vi.fn();

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
