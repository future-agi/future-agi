import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";
import SetBudgetDialog from "./SetBudgetDialog";

// R7-BUDGET (NG-02, contract K9): the dialog's Throttle is a real throttle
// with its own requests-per-minute field, and every save carries the time it
// was made, so the push can tell a later Hard Limit from an older budget.

const mockMutate = vi.fn();

vi.mock("../providers/hooks/useGatewayConfig", () => ({
  useSetBudget: () => ({ mutate: mockMutate, isPending: false }),
}));

async function pick(user, label, option) {
  await user.click(screen.getByLabelText(label));
  await user.click(screen.getByRole("option", { name: option }));
}

describe("SetBudgetDialog (R7-BUDGET)", () => {
  beforeEach(() => mockMutate.mockClear());

  it("Throttle asks for requests per minute (default 1) and saves it", async () => {
    const user = userEvent.setup();
    render(<SetBudgetDialog open onClose={vi.fn()} gatewayId="default" />);
    await pick(user, /Budget Level/, "Organization");
    await user.type(screen.getByLabelText(/Limit \(\$\)/), "50");

    expect(
      screen.queryByLabelText(/Requests per minute/),
    ).not.toBeInTheDocument();
    await pick(user, /When Limit Exceeded/, /Throttle/);
    const rpm = screen.getByLabelText(/Requests per minute/);
    expect(rpm).toHaveValue(1);
    await user.clear(rpm);
    await user.type(rpm, "5");
    await user.click(screen.getByRole("button", { name: "Save" }));

    const { config } = mockMutate.mock.calls[0][0];
    expect(config).toMatchObject({
      limit: 50,
      on_exceed: "throttle",
      throttle_rpm: 5,
    });
  });

  it("describes Throttle as what the gateway does", async () => {
    const user = userEvent.setup();
    render(<SetBudgetDialog open onClose={vi.fn()} gatewayId="default" />);
    await user.click(screen.getByLabelText(/When Limit Exceeded/));
    expect(
      screen.getByText(/allow only the set number of requests a minute/i),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("Rate limit requests when budget is exceeded"),
    ).not.toBeInTheDocument();
  });

  it("refuses a throttle rate that is not a whole number from 1", async () => {
    const user = userEvent.setup();
    render(<SetBudgetDialog open onClose={vi.fn()} gatewayId="default" />);
    await pick(user, /Budget Level/, "Organization");
    await user.type(screen.getByLabelText(/Limit \(\$\)/), "50");
    await pick(user, /When Limit Exceeded/, /Throttle/);
    const rpm = screen.getByLabelText(/Requests per minute/);
    await user.clear(rpm);
    await user.type(rpm, "0");
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  });

  it("stamps every save with the time it was made", async () => {
    const user = userEvent.setup();
    render(<SetBudgetDialog open onClose={vi.fn()} gatewayId="default" />);
    await pick(user, /Budget Level/, "Hard Limit");
    await user.type(screen.getByLabelText(/Limit \(\$\)/), "10");
    await user.click(screen.getByRole("button", { name: "Save" }));
    const { config } = mockMutate.mock.calls[0][0];
    expect(config.saved_at).toMatch(
      /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/,
    );
    expect(config).not.toHaveProperty("throttle_rpm");
  });

  it.each(["warn", "block", "throttle"])(
    "keeps the stored %s action on edit and describes its outcome",
    async (action) => {
      const user = userEvent.setup();
      render(
        <SetBudgetDialog
          open
          onClose={vi.fn()}
          gatewayId="default"
          period="monthly"
          budget={{
            level: "hard_limit",
            limit: 20,
            on_exceed: action,
            throttle_rpm: 7,
          }}
        />,
      );
      const preview = screen.getByLabelText("Budget preview");
      if (action === "warn")
        expect(preview).toHaveTextContent(
          /allows requests past \$20\.00 per month.*logs warnings/i,
        );
      if (action === "block")
        expect(preview).toHaveTextContent(/rejects requests.*429/i);
      if (action === "throttle")
        expect(preview).toHaveTextContent(
          /7 requests a minute per gateway replica.*429.*Retry-After/i,
        );
      await user.click(screen.getByRole("button", { name: "Save" }));
      expect(mockMutate.mock.calls[0][0].config).toEqual({
        limit: 20,
        alert_threshold: 80,
        on_exceed: action,
        saved_at: expect.any(String),
        ...(action === "throttle" ? { throttle_rpm: 7 } : {}),
      });
    },
  );

  it("edit opens on the saved throttle rate", () => {
    render(
      <SetBudgetDialog
        open
        onClose={vi.fn()}
        gatewayId="default"
        budget={{
          level: "org_limit",
          limit: 20,
          on_exceed: "throttle",
          throttle_rpm: 7,
        }}
      />,
    );
    expect(screen.getByLabelText(/Requests per minute/)).toHaveValue(7);
  });
});
