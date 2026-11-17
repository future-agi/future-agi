import React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { createTheme, ThemeProvider } from "@mui/material/styles";
import { afterEach, describe, expect, it, vi } from "vitest";

import { palette } from "src/theme/palette";
import { alertDetails, normalizeAlertDetail } from "../common";
import AlertDetails from "../components/AlertsSheetView/AlertDetails";

const mocks = vi.hoisted(() => ({ alertRuleDetails: null }));

vi.mock("src/components/svg-color", () => ({ default: () => null }));
vi.mock("../store/useAlertSheetView", () => ({
  useAlertSheetView: () => ({ alertRuleDetails: mocks.alertRuleDetails }),
}));

// The walk's monitor: count of errors, warning at 10%, critical at 20%.
const percentageRule = (overrides = {}) => ({
  id: "monitor-a",
  metric_type: "count_of_errors",
  metric_name: "Count of errors",
  threshold_type: "percentage_change",
  threshold_operator: "greater_than",
  critical_threshold_value: 20,
  warning_threshold_value: 10,
  notification_emails: [],
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

const SAVED_BEFORE = /^Saved before the update/;

describe("Alert rule details for a percentage change monitor", () => {
  it("says the percentage and the comparison for critical and warning", () => {
    renderRule(percentageRule());

    expect(
      screen.getByText(
        "If Count of errors is more than 20% higher compared to previous period",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "If Count of errors is more than 10% higher compared to previous period",
      ),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("If Count of errors > 20"),
    ).not.toBeInTheDocument();
    // A server without threshold_rule judges every alert by the current rule.
    expect(screen.queryByText(SAVED_BEFORE)).not.toBeInTheDocument();
  });

  it("keeps today's sentence for an alert on the current rule", () => {
    renderRule(percentageRule({ threshold_rule: "current" }));

    expect(
      screen.getByText(
        "If Count of errors is more than 20% higher compared to previous period",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(SAVED_BEFORE)).not.toBeInTheDocument();
  });

  it("says lower for a Below rule", () => {
    renderRule(
      percentageRule({
        threshold_operator: "less_than",
        warning_threshold_value: null,
      }),
    );

    expect(
      screen.getByText(
        "If Count of errors is more than 20% lower compared to previous period",
      ),
    ).toBeInTheDocument();
  });

  it("leaves a static value rule as it was", () => {
    renderRule(
      percentageRule({ threshold_type: "static", warning_threshold_value: 10 }),
    );

    expect(screen.getByText("If Count of errors > 20")).toBeInTheDocument();
    expect(screen.getByText("If Count of errors > 10")).toBeInTheDocument();
  });
});

// Saved before the update: the alert is still judged by a band of standard
// deviations around its average, stddev x (1 + p/100), until a threshold
// changes.
describe("Alert rule details for a percentage alert on its earlier rule", () => {
  it("states the standard deviation band for critical and warning", () => {
    renderRule(
      percentageRule({
        threshold_rule: "earlier",
        critical_threshold_value: 400,
        warning_threshold_value: 300,
        auto_threshold_time_window: 10080,
      }),
    );

    expect(
      screen.getByText(
        "If Count of errors is more than 5 standard deviations above its average over the last 7 days",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "If Count of errors is more than 4 standard deviations above its average over the last 7 days",
      ),
    ).toBeInTheDocument();
    expect(
      screen.queryByText(
        "If Count of errors is more than 400% higher compared to previous period",
      ),
    ).not.toBeInTheDocument();
  });

  it("says why it keeps the rule and how to switch to the current one", () => {
    renderRule(
      percentageRule({
        threshold_rule: "earlier",
        critical_threshold_value: 400,
        warning_threshold_value: 300,
        auto_threshold_time_window: 10080,
      }),
    );

    expect(
      screen.getByText(
        "Saved before the update, so this alert keeps its earlier rule. Change a threshold and save to switch to the current rule: more than 400% higher than the previous period.",
      ),
    ).toBeInTheDocument();
  });

  it("says below and lower for a Below rule", () => {
    renderRule(
      percentageRule({
        threshold_rule: "earlier",
        threshold_operator: "less_than",
        critical_threshold_value: 50,
        warning_threshold_value: null,
        auto_threshold_time_window: 60,
      }),
    );

    expect(
      screen.getByText(
        "If Count of errors is more than 1.5 standard deviations below its average over the last hour",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "Saved before the update, so this alert keeps its earlier rule. Change a threshold and save to switch to the current rule: more than 50% lower than the previous period.",
      ),
    ).toBeInTheDocument();
  });

  it("leaves a static value rule saved before the update as it was", () => {
    renderRule(
      percentageRule({ threshold_rule: "earlier", threshold_type: "static" }),
    );

    expect(screen.getByText("If Count of errors > 20")).toBeInTheDocument();
    expect(screen.getByText("If Count of errors > 10")).toBeInTheDocument();
    expect(screen.queryByText(SAVED_BEFORE)).not.toBeInTheDocument();
  });
});

describe("Service provider error rate description", () => {
  it("describes the share of providers that had an error", () => {
    const { markdown } = alertDetails.service_provider_error_rates;

    expect(markdown).toContain(
      "the share of your providers that had at least one error",
    );
    expect(markdown).not.toContain("per 10 minutes");
    expect(markdown).not.toMatch(/error-free|no errors/);
  });
});
