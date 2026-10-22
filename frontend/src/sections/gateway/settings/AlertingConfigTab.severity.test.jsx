// Settings > Org Config > Alerting, the second writer of
// alert rules, sets each rule's severity, and its Window and Cooldown fields
// show what the gateway applies to an empty value (1m and 15m) instead of a
// value that is not stored.
import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen, within } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";
import AlertingConfigTab from "./AlertingConfigTab";

const BARE_RULE = {
  name: "bare",
  metric: "error_count",
  condition: ">=",
  threshold: 3,
  channels: [],
};

describe("AlertingConfigTab severity, window and cooldown", () => {
  it("Add Rule starts a rule with severity warning, window 5m and cooldown 15m", async () => {
    const onChange = vi.fn();
    const user = userEvent.setup();
    render(
      <AlertingConfigTab
        alerting={{ enabled: true, rules: [], channels: [] }}
        onChange={onChange}
      />,
    );
    await user.click(screen.getByRole("button", { name: /Add Rule/ }));
    const rules = onChange.mock.calls[0][0].rules;
    expect(rules[0]).toMatchObject({
      severity: "warning",
      window: "5m",
      cooldown: "15m",
    });
  });

  it("sets a rule's severity", async () => {
    const onChange = vi.fn();
    const user = userEvent.setup();
    render(
      <AlertingConfigTab
        alerting={{ enabled: true, rules: [BARE_RULE], channels: [] }}
        onChange={onChange}
      />,
    );
    await user.click(screen.getByLabelText(/Severity/));
    await user.click(screen.getByRole("option", { name: "Critical" }));
    expect(onChange.mock.calls.at(-1)[0].rules[0]).toMatchObject({
      name: "bare",
      severity: "critical",
    });
  });

  it("a rule saved without severity, window or cooldown shows the gateway's values", () => {
    render(
      <AlertingConfigTab
        alerting={{ enabled: true, rules: [BARE_RULE], channels: [] }}
        onChange={vi.fn()}
      />,
    );
    expect(screen.getByLabelText(/Severity/)).toHaveTextContent("Warning");
    const windowField = screen.getByLabelText(/Window/);
    const cooldownField = screen.getByLabelText(/Cooldown/);
    // Not "5m" and "15m" shown as if stored: the gateway reads an empty
    // window as 1m and an empty cooldown as 15m, and the helper says so.
    expect(windowField).toHaveValue("");
    expect(cooldownField).toHaveValue("");
    const card = windowField.closest(".MuiPaper-root");
    expect(within(card).getByText(/empty: 1m/)).toBeInTheDocument();
    expect(within(card).getByText(/empty: 15m/)).toBeInTheDocument();
  });

  it("marks a cooldown the gateway cannot read", () => {
    render(
      <AlertingConfigTab
        alerting={{
          enabled: true,
          rules: [{ ...BARE_RULE, cooldown: "15 minutes" }],
          channels: [],
        }}
        onChange={vi.fn()}
      />,
    );
    expect(screen.getByText(/not a duration/i)).toBeInTheDocument();
  });
});
