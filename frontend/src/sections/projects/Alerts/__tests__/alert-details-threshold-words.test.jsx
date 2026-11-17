import React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { createTheme, ThemeProvider } from "@mui/material/styles";
import { afterEach, describe, expect, it, vi } from "vitest";

import { palette } from "src/theme/palette";
import { normalizeAlertDetail } from "../common";
import AlertDetails from "../components/AlertsSheetView/AlertDetails";

const mocks = vi.hoisted(() => ({ alertRuleDetails: null }));

vi.mock("src/components/svg-color", () => ({ default: () => null }));
vi.mock("../store/useAlertSheetView", () => ({
  useAlertSheetView: () => ({ alertRuleDetails: mocks.alertRuleDetails }),
}));

// A threshold saved as infinity or not-a-number has no JSON number, so the
// details answer sends it as null with its words beside it. The sheet shows
// the words where the number would be; a finite threshold reads as before.
const rule = (overrides = {}) => ({
  id: "monitor-w",
  metric_type: "count_of_errors",
  metric_name: "Count of errors",
  threshold_type: "static",
  threshold_operator: "greater_than",
  threshold_rule: "current",
  critical_threshold_value: 10,
  warning_threshold_value: 5,
  notification_emails: [],
  ...overrides,
});

const providerRule = (overrides = {}) =>
  rule({
    metric_type: "service_provider_error_rates",
    metric_name: "Service provider error rates",
    threshold_rule: "earlier",
    ...overrides,
  });

const renderRule = (detail) => {
  mocks.alertRuleDetails = normalizeAlertDetail(detail);
  render(
    <ThemeProvider theme={createTheme({ palette: palette("light") })}>
      <AlertDetails />
    </ThemeProvider>,
  );
};

afterEach(() => {
  cleanup();
  mocks.alertRuleDetails = null;
});

describe("a threshold with no number is said in words on the details sheet", () => {
  it("shows Infinity for an infinite critical level beside a numeric warning", () => {
    renderRule(
      providerRule({
        critical_threshold_value: null,
        critical_threshold_label: "Infinity",
        warning_threshold_value: 95,
      }),
    );

    expect(screen.getByText("Condition 1")).toBeInTheDocument();
    expect(screen.getByText("Condition 2")).toBeInTheDocument();
    expect(
      screen.getByText("If Share of providers with no errors > Infinity"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("If Share of providers with no errors > 95"),
    ).toBeInTheDocument();
  });

  it("shows Not a number for a not-a-number critical level", () => {
    renderRule(
      providerRule({
        critical_threshold_value: null,
        critical_threshold_label: "Not a number",
        warning_threshold_value: null,
      }),
    );

    expect(
      screen.getByText("If Share of providers with no errors > Not a number"),
    ).toBeInTheDocument();
  });

  it("shows Infinity for an infinite warning level beside a numeric critical", () => {
    renderRule(
      rule({
        threshold_operator: "less_than",
        critical_threshold_value: 5,
        warning_threshold_value: null,
        warning_threshold_label: "Infinity",
      }),
    );

    expect(screen.getByText("If Count of errors < 5")).toBeInTheDocument();
    expect(
      screen.getByText("If Count of errors < Infinity"),
    ).toBeInTheDocument();
  });

  it("shows the words with no percentage worked out on a percentage alert", () => {
    renderRule(
      rule({
        threshold_type: "percentage_change",
        threshold_rule: "earlier",
        auto_threshold_time_window: 60,
        critical_threshold_value: null,
        critical_threshold_label: "Infinity",
        warning_threshold_value: 300,
      }),
    );

    expect(
      screen.getByText("If Count of errors > Infinity"),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "Saved before the update, so this alert keeps its earlier rule. Change a threshold and save to switch to the current rule.",
      ),
    ).toBeInTheDocument();
  });

  it("keeps the numbers of an alert whose thresholds are finite", () => {
    renderRule(rule());

    expect(screen.getByText("If Count of errors > 10")).toBeInTheDocument();
    expect(screen.getByText("If Count of errors > 5")).toBeInTheDocument();
    expect(screen.queryByText(/Infinity|Not a number/)).toBeNull();
  });
});
