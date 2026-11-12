import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "src/utils/test-utils";
import BudgetEnforcementCard from "./BudgetEnforcementCard";

const { mutate } = vi.hoisted(() => ({ mutate: vi.fn() }));
vi.mock("../providers/hooks/useGatewayConfig", () => ({
  useUpdateConfig: () => ({ mutate, isPending: false }),
}));
vi.mock("../hooks/useGatewayAdmin", () => ({
  useGatewayAdmin: () => ({ canEdit: true }),
}));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
beforeEach(() => mutate.mockReset());

describe("J13.W03-04 budget enforcement dependency", () => {
  it("does not offer an impossible enforcement-off mutation while daily caps are active", () => {
    render(
      <BudgetEnforcementCard
        gatewayId="default"
        budgets={{
          enabled: true,
          admission_caps_enabled: true,
          policy_revision: 3,
        }}
        sectionsEnforced={["budgets"]}
      />,
    );
    const toggle = screen.getByRole("checkbox", { name: "Enforce budgets" });
    expect(toggle).toBeDisabled();
    fireEvent.click(toggle);
    expect(mutate).not.toHaveBeenCalled();
    expect(
      screen.getByText(/deactivate daily admission caps/i),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: /admission policy/i }),
    ).toHaveAttribute(
      "href",
      expect.stringContaining("/dashboard/gateway/settings"),
    );
  });

  it("MONEY-02 locks alias-only active policies and shows their normalized caps", () => {
    render(
      <BudgetEnforcementCard
        gatewayId="default"
        budgets={{
          enabled: true,
          admissionCapsEnabled: true,
          policyRevision: 3,
          agents: {
            worker: { limit: 4, activatedAt: "2026-11-09T00:00:00Z" },
          },
          keyIds: {
            "synthetic-key-id": {
              limit: 2,
              activatedAt: "2026-11-09T01:00:00Z",
            },
          },
        }}
        sectionsEnforced={["budgets"]}
      />,
    );
    const toggle = screen.getByRole("checkbox", { name: "Enforce budgets" });
    expect(toggle).toBeDisabled();
    fireEvent.click(toggle);
    expect(mutate).not.toHaveBeenCalled();
    expect(
      screen.getByText(/deactivate daily admission caps/i),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: /manage admission policy/i }),
    ).toHaveAttribute(
      "href",
      expect.stringContaining("tab=org-config&section=budgets"),
    );
    expect(
      screen.getByText(/Agent worker:.*2026-11-09T00:00:00Z/),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Key synthetic-key-id:.*2026-11-09T01:00:00Z/),
    ).toBeInTheDocument();
  });

  it.each([false, null])(
    "MONEY-02 preserves canonical-first precedence for admission_caps_enabled=%s",
    (canonical) => {
      render(
        <BudgetEnforcementCard
          gatewayId="default"
          budgets={{
            enabled: true,
            admission_caps_enabled: canonical,
            admissionCapsEnabled: true,
            policy_revision: 4,
          }}
          sectionsEnforced={["budgets"]}
        />,
      );
      const toggle = screen.getByRole("checkbox", { name: "Enforce budgets" });
      expect(toggle).toBeEnabled();
      expect(
        screen.queryByText(/deactivate daily admission caps/i),
      ).not.toBeInTheDocument();
      fireEvent.click(toggle);
      expect(mutate).toHaveBeenCalledWith(
        { gatewayId: "default", config: { budgets: { enabled: false } } },
        expect.anything(),
      );
    },
  );

  it("MONEY-02 does not let inactive aliases shadow canonical active policy and caps", () => {
    render(
      <BudgetEnforcementCard
        gatewayId="default"
        budgets={{
          enabled: true,
          admission_caps_enabled: true,
          admissionCapsEnabled: false,
          key_ids: {
            canonical: {
              limit: 3,
              activated_at: "2026-11-09T02:00:00Z",
              activatedAt: "1999-01-01T00:00:00Z",
            },
          },
          keyIds: {
            shadow: { limit: 99, activatedAt: "1999-01-01T00:00:00Z" },
          },
        }}
        sectionsEnforced={["budgets"]}
      />,
    );
    const toggle = screen.getByRole("checkbox", { name: "Enforce budgets" });
    expect(toggle).toBeDisabled();
    fireEvent.click(toggle);
    expect(mutate).not.toHaveBeenCalled();
    expect(
      screen.getByText(/Key canonical:.*2026-11-09T02:00:00Z/),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Key shadow:/)).not.toBeInTheDocument();
    expect(screen.queryByText(/1999-01-01/)).not.toBeInTheDocument();
  });

  it("keeps ordinary enforcement editable after admission caps are deactivated", () => {
    render(
      <BudgetEnforcementCard
        gatewayId="default"
        budgets={{
          enabled: true,
          admission_caps_enabled: false,
          policy_revision: 4,
        }}
        sectionsEnforced={["budgets"]}
      />,
    );
    const toggle = screen.getByRole("checkbox", { name: "Enforce budgets" });
    expect(toggle).toBeEnabled();
    fireEvent.click(toggle);
    expect(mutate).toHaveBeenCalledWith(
      { gatewayId: "default", config: { budgets: { enabled: false } } },
      expect.anything(),
    );
  });
});
