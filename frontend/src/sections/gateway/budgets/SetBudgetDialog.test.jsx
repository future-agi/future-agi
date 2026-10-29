import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "src/utils/test-utils";
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
    await user.type(screen.getByLabelText(/Monthly Limit/), "250");
    await user.click(screen.getByRole("button", { name: "Save" }));

    expect(mockMutate.mock.calls[0][0]).toEqual({
      gatewayId: "default",
      level: "org_limit",
      config: { limit: 250, alert_threshold: 80, on_exceed: "warn" },
    });
  });
});
