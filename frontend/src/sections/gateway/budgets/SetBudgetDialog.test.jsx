import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";
import SetBudgetDialog from "./SetBudgetDialog";

const mockMutate = vi.fn();

vi.mock("../providers/hooks/useGatewayConfig", () => ({
  useSetBudget: () => ({ mutate: mockMutate, isPending: false }),
}));

describe("SetBudgetDialog", () => {
  beforeEach(() => {
    mockMutate.mockClear();
  });

  it("offers only the levels the per-org gateway config has", async () => {
    const user = userEvent.setup();
    render(<SetBudgetDialog open onClose={vi.fn()} gatewayId="default" />);

    await user.click(screen.getByLabelText(/Budget Level/));

    expect(
      screen.getAllByRole("option").map((option) => option.textContent),
    ).toEqual(["Organization", "Hard Limit"]);
  });

  it("saves the picked level with its limit", async () => {
    const user = userEvent.setup();
    render(<SetBudgetDialog open onClose={vi.fn()} gatewayId="default" />);

    await user.click(screen.getByLabelText(/Budget Level/));
    await user.click(screen.getByRole("option", { name: "Organization" }));
    await user.type(screen.getByLabelText(/Limit \(\$\)/), "250");
    await user.click(screen.getByRole("button", { name: "Save" }));

    expect(mockMutate.mock.calls[0][0]).toEqual({
      gatewayId: "default",
      level: "org_limit",
      config: {
        limit: 250,
        alert_threshold: 80,
        on_exceed: "warn",
        saved_at: expect.any(String),
      },
    });
  });
});

describe("SetBudgetDialog budget setup preview", () => {
  beforeEach(() => mockMutate.mockClear());

  it.each(["", "-1", "-0.01", "1e309", "Infinity", "NaN"])(
    "does not submit an invalid USD limit: %s",
    async (value) => {
      const user = userEvent.setup();
      render(
        <SetBudgetDialog
          open
          onClose={vi.fn()}
          gatewayId="gw"
          budget={{ level: "org_limit", limit: 100, on_exceed: "warn" }}
        />,
      );
      fireEvent.change(screen.getByLabelText(/Limit \(\$\)/), {
        target: { value },
      });
      expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
      await user.click(screen.getByRole("button", { name: "Save" }));
      expect(mockMutate).not.toHaveBeenCalled();
    },
  );

  it("preserves a saved zero, explains no limit, and submits zero unchanged", async () => {
    const user = userEvent.setup();
    render(
      <SetBudgetDialog
        open
        onClose={vi.fn()}
        gatewayId="gw"
        budget={{ level: "org_limit", limit: 0, on_exceed: "block" }}
      />,
    );
    expect(screen.getByLabelText(/Limit \(\$\)/)).toHaveValue(0);
    expect(
      screen.getByText(/0 means no limit for this entry/i),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(mockMutate.mock.calls[0][0].config).toMatchObject({
      limit: 0,
      on_exceed: "block",
    });
  });

  it("previews replacement of a blocking organization budget by a non-blocking Hard Limit", async () => {
    const user = userEvent.setup();
    render(
      <SetBudgetDialog
        open
        onClose={vi.fn()}
        gatewayId="gw"
        period="daily"
        storedBudgets={{ org_limit: { limit: 100, on_exceed: "block" } }}
      />,
    );
    await user.click(screen.getByLabelText(/Budget Level/));
    await user.click(screen.getByRole("option", { name: "Hard Limit" }));
    await user.type(screen.getByLabelText(/Limit \(\$\)/), "25");
    expect(
      screen.queryByText(/Absolute cap|requests are blocked when exceeded/i),
    ).not.toBeInTheDocument();
    expect(
      screen.getByText(/replaces the Organization budget.*Block/i),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Budget preview")).toHaveTextContent(
      /allows requests past \$25\.00 per day.*logs warnings/i,
    );
    expect(mockMutate).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(mockMutate.mock.calls[0][0]).toEqual({
      gatewayId: "gw",
      level: "hard_limit",
      config: {
        limit: 25,
        alert_threshold: 80,
        on_exceed: "warn",
        saved_at: expect.any(String),
      },
    });
  });

  it("offers a direct period link without changing the draft or submitting", () => {
    const href =
      "/dashboard/gateway/settings?tab=org-config&section=budgets&org=o1&gateway=g1";
    render(
      <SetBudgetDialog
        open
        onClose={vi.fn()}
        gatewayId="g1"
        budgetEditorHref={href}
      />,
    );
    expect(
      screen.getByRole("link", { name: "Change period" }),
    ).toHaveAttribute("href", href);
    expect(mockMutate).not.toHaveBeenCalled();
  });

  it("cannot submit after editing permission is removed", async () => {
    const user = userEvent.setup();
    render(
      <SetBudgetDialog
        open
        onClose={vi.fn()}
        gatewayId="gw"
        canEdit={false}
        budget={{ level: "org_limit", limit: 100, on_exceed: "warn" }}
      />,
    );
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(mockMutate).not.toHaveBeenCalled();
  });
});

describe("SetBudgetDialog and the budgets switch", () => {
  it("says a saved budget is enforced only while budgets are on", () => {
    render(<SetBudgetDialog open onClose={vi.fn()} gatewayId="gw" />);
    expect(
      screen.getByText(
        /enforced only while budgets are on for your organization/i,
      ),
    ).toBeInTheDocument();
  });
});
