import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import BudgetEnforcementCard from "./BudgetEnforcementCard";

vi.mock("../providers/hooks/useGatewayConfig", () => ({
  useUpdateConfig: () => ({ mutate: vi.fn(), isPending: false }),
}));
vi.mock("../hooks/useGatewayAdmin", () => ({
  useGatewayAdmin: () => ({ canEdit: true }),
}));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));

const activated = "2026-10-27T12:00:00Z";
const budgets = {
  enabled: true,
  admission_caps_enabled: true,
  agents: { worker: { limit: 5, activated_at: activated } },
  key_ids: { stable: { limit: 2, activated_at: activated } },
};

describe("Cap activation accounting", () => {
  it("shows the durable activation beside each cap, including after a value edit", () => {
    const { rerender } = render(
      <BudgetEnforcementCard
        budgets={budgets}
        sectionsEnforced={["budgets"]}
      />,
    );
    expect(
      screen.getAllByText(
        new RegExp(`counts spend admitted since ${activated}`),
      ),
    ).toHaveLength(2);
    screen.getByText(/UTC and include BYOK/);
    rerender(
      <BudgetEnforcementCard
        budgets={{
          ...budgets,
          agents: { worker: { limit: 1, activated_at: activated } },
        }}
        sectionsEnforced={["budgets"]}
      />,
    );
    screen.getByText(
      new RegExp(
        `Agent worker: \\$1 daily — counts spend admitted since ${activated}`,
      ),
    );
  });
  it("never invents an activation when absent", () => {
    render(
      <BudgetEnforcementCard
        budgets={{ ...budgets, agents: { worker: { limit: 5 } }, key_ids: {} }}
        sectionsEnforced={["budgets"]}
      />,
    );
    screen.getByText(/Activation unavailable; capped requests are refused/);
    expect(screen.queryByText(/counts spend admitted since/)).toBeNull();
  });
  it("keeps default-off caps hidden", () => {
    render(
      <BudgetEnforcementCard
        budgets={{ enabled: false }}
        sectionsEnforced={["budgets"]}
      />,
    );
    expect(screen.queryByLabelText("Admission caps")).toBeNull();
  });
});
