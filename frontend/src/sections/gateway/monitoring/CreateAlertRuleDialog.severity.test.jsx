// The Create Alert Rule dialog sets the rule's cooldown
// (default 15m, the gateway's default) and severity, both of which the gateway
// applies.
import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";
import CreateAlertRuleDialog from "./CreateAlertRuleDialog";

const mockUpdateMutate = vi.fn();

vi.mock("../providers/hooks/useGatewayConfig", () => ({
  useUpdateConfig: () => ({
    mutate: mockUpdateMutate,
    isPending: false,
    isError: false,
    error: null,
  }),
}));

async function fill(user, name = "new-rule") {
  await user.type(screen.getByLabelText(/Rule Name/), name);
  await user.type(screen.getByLabelText(/Threshold/), "5");
}

function sentRule() {
  const [payload] = mockUpdateMutate.mock.calls[0];
  return payload.config.alerting.rules.at(-1);
}

describe("CreateAlertRuleDialog cooldown and severity", () => {
  beforeEach(() => mockUpdateMutate.mockReset());

  it("has a Cooldown field that starts at the gateway's default, 15m, and sends it", async () => {
    const user = userEvent.setup();
    render(<CreateAlertRuleDialog open onClose={vi.fn()} gatewayId="gw-1" />);
    expect(screen.getByLabelText(/Cooldown/)).toHaveValue("15m");
    await fill(user);
    await user.click(screen.getByRole("button", { name: "Create Rule" }));
    expect(sentRule()).toMatchObject({ cooldown: "15m", severity: "warning" });
  });

  it("sends the cooldown and severity the user picks", async () => {
    const user = userEvent.setup();
    render(<CreateAlertRuleDialog open onClose={vi.fn()} gatewayId="gw-1" />);
    await fill(user);
    const cooldown = screen.getByLabelText(/Cooldown/);
    await user.clear(cooldown);
    await user.type(cooldown, "1h");
    await user.click(screen.getByLabelText(/Severity/));
    await user.click(screen.getByRole("option", { name: "Critical" }));
    await user.click(screen.getByRole("button", { name: "Create Rule" }));
    expect(sentRule()).toMatchObject({ cooldown: "1h", severity: "critical" });
  });

  it("an empty cooldown is left out, so the gateway's 15m default applies", async () => {
    const user = userEvent.setup();
    render(<CreateAlertRuleDialog open onClose={vi.fn()} gatewayId="gw-1" />);
    await fill(user);
    await user.clear(screen.getByLabelText(/Cooldown/));
    await user.click(screen.getByRole("button", { name: "Create Rule" }));
    expect(sentRule()).not.toHaveProperty("cooldown");
  });

  it("refuses a cooldown the gateway cannot read", async () => {
    const user = userEvent.setup();
    render(<CreateAlertRuleDialog open onClose={vi.fn()} gatewayId="gw-1" />);
    await fill(user);
    const cooldown = screen.getByLabelText(/Cooldown/);
    await user.clear(cooldown);
    await user.type(cooldown, "15 minutes");
    expect(screen.getByRole("button", { name: "Create Rule" })).toBeDisabled();
    expect(screen.getByText(/not a duration/i)).toBeInTheDocument();
  });

  it("offers exactly the three severities the gateway sends", async () => {
    const user = userEvent.setup();
    render(<CreateAlertRuleDialog open onClose={vi.fn()} gatewayId="gw-1" />);
    await user.click(screen.getByLabelText(/Severity/));
    const options = screen.getAllByRole("option").map((o) => o.textContent);
    expect(options).toEqual(["Critical", "Warning", "Info"]);
  });
});
