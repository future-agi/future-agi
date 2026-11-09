import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import userEvent from "@testing-library/user-event";
import { render, screen } from "src/utils/test-utils";
import GuardrailManagementSection from "./GuardrailManagementSection";

const mockMutate = vi.fn();

const ACTIVE_CONFIG = {
  id: "config-1",
  version: 7,
  guardrails: { checks: {} },
  routing: { strategy: "weighted" },
  cache: { enabled: true, backend: "memory" },
  rate_limiting: { enabled: true, rpm: 120 },
  budgets: { enabled: true, org_limit: { limit: 100, period: "monthly" } },
  cost_tracking: { enabled: true },
  ip_acl: { enabled: true, allow: ["10.0.0.0/8"] },
  alerting: { enabled: true, rules: [{ name: "spend" }] },
  privacy: { redact: true },
  tool_policy: { default: "deny" },
  mcp: { servers: [{ name: "docs" }] },
  a2a: { agents: [{ name: "planner" }] },
  audit: { enabled: true },
  model_database: { "custom-model": { input: 1 } },
  model_map: { fast: "gpt-4o-mini" },
};

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual("react-router-dom");
  return {
    ...actual,
    useParams: () => ({ tab: "configuration" }),
    useNavigate: () => vi.fn(),
  };
});

vi.mock("../providers/hooks/useOrgConfig", () => ({
  useOrgConfig: () => ({ data: ACTIVE_CONFIG, isLoading: false }),
  useCreateOrgConfig: () => ({ mutate: mockMutate, isPending: false }),
}));

vi.mock("../providers/hooks/useGatewayConfig", () => ({
  useToggleGuardrail: () => ({ mutate: vi.fn(), isPending: false }),
}));

vi.mock("../context/useGatewayContext", () => ({
  useGatewayContext: () => ({ gatewayId: "gateway-1", isLoading: false }),
}));

vi.mock("./GuardrailAnalyticsTab", () => ({ default: () => null }));
vi.mock("./FeedbackSummaryCard", () => ({ default: () => null }));
vi.mock("./EditGuardrailDialog", () => ({ default: () => null }));

vi.mock("../settings/GuardrailConfigTab", () => ({
  default: ({ onChange }) => (
    <button
      type="button"
      onClick={() =>
        onChange({
          checks: {
            "pii-detection": { enabled: true, action: "block", threshold: 0.5 },
          },
        })
      }
    >
      Edit guardrails
    </button>
  ),
}));

describe("GuardrailManagementSection Save & Activate", () => {
  beforeEach(() => {
    mockMutate.mockClear();
  });

  it("keeps every other org-config section when saving guardrails", async () => {
    const user = userEvent.setup();
    render(<GuardrailManagementSection />);

    await user.click(screen.getByRole("button", { name: "Edit guardrails" }));
    await user.click(screen.getByRole("button", { name: "Save & Activate" }));

    expect(mockMutate).toHaveBeenCalledTimes(1);
    const [payload] = mockMutate.mock.calls[0];
    const expected = { ...ACTIVE_CONFIG, guardrails: payload.guardrails };
    delete expected.id;
    delete expected.version;
    expect(payload).toEqual(expected);
    expect(payload.guardrails.rules).toEqual([
      expect.objectContaining({ name: "pii-detector", action: "block" }),
    ]);
  });
});
