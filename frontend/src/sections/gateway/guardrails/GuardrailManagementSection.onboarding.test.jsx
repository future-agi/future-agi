import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { render, screen, waitFor } from "src/utils/test-utils";

import GuardrailManagementSection from "./GuardrailManagementSection";

const mockCreateOrgConfigMutate = vi.fn();
const mockRecordActivationEvent = vi.fn();

let mockOrgConfigReturn;

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual("react-router-dom");
  return {
    ...actual,
    useParams: () => ({ tab: "configuration" }),
    useNavigate: () => vi.fn(),
  };
});

vi.mock("../providers/hooks/useOrgConfig", () => ({
  useOrgConfig: () => mockOrgConfigReturn,
  useCreateOrgConfig: () => ({
    mutate: mockCreateOrgConfigMutate,
    isPending: false,
  }),
}));

vi.mock("../providers/hooks/useGatewayConfig", () => ({
  useToggleGuardrail: () => ({ mutate: vi.fn(), isPending: false }),
}));

vi.mock("../context/useGatewayContext", () => ({
  useGatewayContext: () => ({ gatewayId: "gateway-1", isLoading: false }),
}));

vi.mock("src/sections/onboarding-home/api/onboarding-home-api", () => ({
  recordActivationEvent: (...args) => mockRecordActivationEvent(...args),
}));

vi.mock("./GuardrailAnalyticsTab", () => ({
  default: () => <div>Analytics tab</div>,
}));

vi.mock("./FeedbackSummaryCard", () => ({
  default: () => <div>Feedback summary</div>,
}));

vi.mock("./EditGuardrailDialog", () => ({
  default: () => null,
}));

vi.mock("../settings/GuardrailConfigTab", () => ({
  default: ({ onChange }) => (
    <button
      type="button"
      onClick={() =>
        onChange({
          checks: {
            "pii-detection": {
              enabled: true,
              action: "block",
            },
          },
        })
      }
    >
      Add guardrail rule
    </button>
  ),
}));

describe("GuardrailManagementSection onboarding activation", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockRecordActivationEvent.mockResolvedValue({});
    mockCreateOrgConfigMutate.mockImplementation((_payload, options) => {
      options?.onSuccess?.();
    });
    mockOrgConfigReturn = {
      data: {
        providers: {},
        routing: {},
        guardrails: {},
      },
      isLoading: false,
    };
  });

  it("records policy completion after saving onboarding guardrail config", async () => {
    window.history.pushState(
      {},
      "Guardrails",
      "/dashboard/gateway/guardrails/configuration?source=onboarding&request_id=req-123",
    );

    render(<GuardrailManagementSection />);

    await userEvent.click(
      screen.getByRole("button", { name: /add guardrail/i }),
    );
    await userEvent.click(
      screen.getByRole("button", { name: /save & activate/i }),
    );

    await waitFor(() =>
      expect(mockCreateOrgConfigMutate).toHaveBeenCalledTimes(1),
    );
    expect(mockRecordActivationEvent).toHaveBeenCalledWith(
      expect.objectContaining({
        eventName: "gateway_policy_created",
        primaryPath: "gateway",
        stage: "add_gateway_policy",
        source: "gateway_guardrail_onboarding",
        metadata: expect.objectContaining({
          gateway_id: "gateway-1",
          request_id: "req-123",
          policy_type: "guardrail",
          policy_id: "guardrail",
          guardrail_rule_count: 1,
        }),
      }),
    );
  });

  it("does not record policy completion for ordinary guardrail config saves", async () => {
    window.history.pushState(
      {},
      "Guardrails",
      "/dashboard/gateway/guardrails/configuration",
    );

    render(<GuardrailManagementSection />);

    await userEvent.click(
      screen.getByRole("button", { name: /add guardrail/i }),
    );
    await userEvent.click(
      screen.getByRole("button", { name: /save & activate/i }),
    );

    await waitFor(() =>
      expect(mockCreateOrgConfigMutate).toHaveBeenCalledTimes(1),
    );
    expect(mockRecordActivationEvent).not.toHaveBeenCalled();
  });
});
