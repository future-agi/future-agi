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

const existingRules = [
  {
    name: "high-errors",
    metric: "error_count",
    condition: ">=",
    threshold: 10,
    window: "5m",
    cooldown: "15m",
    channels: ["ops"],
  },
];

async function fillAndSubmit(user, name = "new-rule") {
  await user.type(screen.getByLabelText(/Rule Name/), name);
  await user.type(screen.getByLabelText(/Threshold/), "5");
  await user.click(screen.getByRole("button", { name: "Create Rule" }));
}

describe("CreateAlertRuleDialog", () => {
  beforeEach(() => {
    mockUpdateMutate.mockReset();
  });

  it("only offers metrics the gateway's alerting plugin records", async () => {
    const user = userEvent.setup();
    render(<CreateAlertRuleDialog open onClose={vi.fn()} gatewayId="gw-1" />);

    await user.click(screen.getByLabelText(/Metric/));

    expect(
      screen.getByRole("option", { name: "Error Count" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("option", { name: /error_rate/ }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("option", { name: /latency_p99/ }),
    ).not.toBeInTheDocument();
  });

  it("appends to the existing rules array instead of replacing it", async () => {
    const user = userEvent.setup();
    render(
      <CreateAlertRuleDialog
        open
        onClose={vi.fn()}
        gatewayId="gw-1"
        existingRules={existingRules}
      />,
    );

    await fillAndSubmit(user);

    const [payload] = mockUpdateMutate.mock.calls[0];
    expect(payload.config.alerting.rules).toEqual([
      existingRules[0],
      expect.objectContaining({ name: "new-rule", metric: "error_count" }),
    ]);
  });

  it("turns per-org alerting on so the new rule is actually evaluated", async () => {
    const user = userEvent.setup();
    render(<CreateAlertRuleDialog open onClose={vi.fn()} gatewayId="gw-1" />);

    await fillAndSubmit(user);

    const [payload] = mockUpdateMutate.mock.calls[0];
    expect(payload.config.alerting.enabled).toBe(true);
  });

  it("replaces a rule of the same name rather than duplicating it", async () => {
    const user = userEvent.setup();
    render(
      <CreateAlertRuleDialog
        open
        onClose={vi.fn()}
        gatewayId="gw-1"
        existingRules={existingRules}
      />,
    );

    await fillAndSubmit(user, "high-errors");

    const [payload] = mockUpdateMutate.mock.calls[0];
    expect(payload.config.alerting.rules).toHaveLength(1);
    expect(payload.config.alerting.rules[0].threshold).toBe(5);
  });
});
