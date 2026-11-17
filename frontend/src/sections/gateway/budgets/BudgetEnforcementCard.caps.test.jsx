import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import BudgetEnforcementCard from "./BudgetEnforcementCard";

// Wherever a daily cap shows, caps on reads the same sentence.
vi.mock("../providers/hooks/useGatewayConfig", () => ({
  useUpdateConfig: () => ({ mutate: vi.fn(), isPending: false }),
}));
vi.mock("../hooks/useGatewayAdmin", () => ({
  useGatewayAdmin: () => ({ canEdit: true }),
}));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));

const CAPS_ON =
  "Daily caps are on. A key under a cap can make only non-streaming chat requests, and its requests over the day's cap are refused. Streaming and Gemini requests from capped keys are refused, and native message and response requests are refused for every key while daily caps are on.";

describe("daily caps on the Budgets page", () => {
  it.each([
    ["canonical", { enabled: true, admission_caps_enabled: true }],
    ["alias-only", { enabled: true, admissionCapsEnabled: true }],
  ])("%s caps on: shown with the caps-on sentence", (_name, budgets) => {
    render(
      <BudgetEnforcementCard
        gatewayId="default"
        budgets={{ ...budgets, policy_revision: 3 }}
        sectionsEnforced={["budgets"]}
      />,
    );
    expect(screen.getByTestId("budget-caps-on-notice")).toHaveTextContent(
      CAPS_ON,
    );
    expect(screen.getAllByText(CAPS_ON)).toHaveLength(1);
  });

  it("caps off: no caps sentence", () => {
    render(
      <BudgetEnforcementCard
        gatewayId="default"
        budgets={{ enabled: true, admission_caps_enabled: false }}
        sectionsEnforced={["budgets"]}
      />,
    );
    expect(screen.queryByTestId("budget-caps-on-notice")).toBeNull();
    expect(screen.queryByText(/Daily caps are on/)).toBeNull();
  });
});
