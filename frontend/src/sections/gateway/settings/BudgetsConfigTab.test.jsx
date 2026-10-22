import React from "react";
/* eslint-disable react/prop-types */
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";

import BudgetsConfigTab from "./BudgetsConfigTab";

// Org Config > Budgets and the Budgets page are one state.
// The switch shows what the push sends (on when a level is set and the org
// never chose), and an org-level budget saved from Set Budget is shown as
// its limit and kept as saved.

vi.mock("../hooks/useAvailableModels", () => ({
  useAvailableModels: () => [],
}));

// The editor keeps the draft in its parent (OrgConfigEditor); so does this.
function Harness({ initial, onChange }) {
  const [budgets, setBudgets] = React.useState(initial);
  return (
    <BudgetsConfigTab
      budgets={budgets}
      costTracking={{}}
      onBudgetsChange={(next) => {
        onChange(next);
        setBudgets(next);
      }}
      onCostTrackingChange={vi.fn()}
    />
  );
}

function renderTab(budgets) {
  const onBudgetsChange = vi.fn();
  render(<Harness initial={budgets} onChange={onBudgetsChange} />);
  return onBudgetsChange;
}

const dialogBudget = {
  org_limit: {
    limit: 250,
    on_exceed: "block",
    alert_threshold: 60,
    saved_at: "2026-10-21T08:00:00.000Z",
  },
};

describe("BudgetsConfigTab", () => {
  it("the switch reads on for a level set from the Budgets page", () => {
    renderTab(dialogBudget);
    expect(
      screen.getByRole("checkbox", { name: /Enable per-org budgets/i }),
    ).toBeChecked();
    expect(screen.getByText("Organization Budget")).toBeInTheDocument();
  });

  it("an explicit off reads off", () => {
    renderTab({ ...dialogBudget, enabled: false });
    expect(
      screen.getByRole("checkbox", { name: /Enable per-org budgets/i }),
    ).not.toBeChecked();
  });

  it("toggling writes an explicit enabled", async () => {
    const user = userEvent.setup();
    const onChange = renderTab(dialogBudget);
    await user.click(
      screen.getByRole("checkbox", { name: /Enable per-org budgets/i }),
    );
    expect(onChange).toHaveBeenCalledWith({ ...dialogBudget, enabled: false });
  });

  it("a Set Budget level shows its limit, read here and edited on the Budgets page", () => {
    renderTab(dialogBudget);
    expect(
      screen.queryByDisplayValue("[object Object]"),
    ).not.toBeInTheDocument();
    expect(screen.getByText(/\$250\.00/)).toBeInTheDocument();
    expect(
      screen.getByText(/set from Budgets > Set Budget/i),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("checkbox", { name: /Hard limit \(block requests/i }),
    ).not.toBeInTheDocument();
  });

  it("a Hard Limit level saved from Set Budget is never overwritten by the switch", () => {
    renderTab({
      hard_limit: {
        limit: 9,
        on_exceed: "block",
        saved_at: "2026-10-21T08:00:00.000Z",
      },
    });
    expect(
      screen.queryByRole("checkbox", { name: /Hard limit \(block requests/i }),
    ).not.toBeInTheDocument();
    expect(screen.getByText(/\$9\.00/)).toBeInTheDocument();
  });

  it("a flat org budget stays editable here", async () => {
    const user = userEvent.setup();
    const onChange = renderTab({
      enabled: true,
      org_limit: 40,
      hard_limit: true,
    });
    const field = screen.getByLabelText(/Spend Limit \(USD\)/);
    expect(field).toHaveValue(40);
    await user.clear(field);
    await user.type(field, "7");
    expect(onChange).toHaveBeenLastCalledWith(
      expect.objectContaining({ org_limit: 7 }),
    );
    expect(
      screen.getByRole("checkbox", { name: /Hard limit \(block requests/i }),
    ).toBeChecked();
  });
});
