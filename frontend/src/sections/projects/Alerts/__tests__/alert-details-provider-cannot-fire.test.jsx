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

// A static service provider error rate alert compares a share of providers,
// from 0 to 1, with its thresholds, on either rule. Above a value of 1 or
// more, or Below a value of 0 or less, a level can never fire. When no level
// can fire, the rule says so before anyone opens Edit.
const CANNOT_FIRE =
  "This alert cannot fire: a share of providers is never above 1 or below 0.";

const providerRule = (overrides = {}) => ({
  id: "monitor-p",
  metric_type: "service_provider_error_rates",
  metric_name: "Service provider error rates",
  threshold_type: "static",
  threshold_operator: "greater_than",
  threshold_rule: "earlier",
  critical_threshold_value: 95,
  warning_threshold_value: 90,
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

describe("a provider error rate alert that cannot fire says so", () => {
  it("for Above 95 and 90, beside its conditions", () => {
    renderRule(providerRule());

    expect(screen.queryByText(CANNOT_FIRE)).not.toBeNull();
    expect(
      screen.getByText("If Share of providers with no errors > 95"),
    ).toBeInTheDocument();
  });

  it("for Above 95 and 90 on the current rule too", () => {
    renderRule(providerRule({ threshold_rule: "current" }));

    expect(screen.queryByText(CANNOT_FIRE)).not.toBeNull();
  });

  it("for Above 1 with no warning", () => {
    renderRule(
      providerRule({
        threshold_rule: "current",
        critical_threshold_value: 1,
        warning_threshold_value: null,
      }),
    );

    expect(screen.queryByText(CANNOT_FIRE)).not.toBeNull();
  });

  it("for Below 0 with no warning", () => {
    renderRule(
      providerRule({
        threshold_rule: "current",
        threshold_operator: "less_than",
        critical_threshold_value: 0,
        warning_threshold_value: null,
      }),
    );

    expect(screen.queryByText(CANNOT_FIRE)).not.toBeNull();
  });
});

describe("a provider error rate alert that can fire says nothing more", () => {
  it("for Above 0.99 with no warning, just inside the bound", () => {
    renderRule(
      providerRule({
        threshold_rule: "current",
        critical_threshold_value: 0.99,
        warning_threshold_value: null,
      }),
    );

    expect(screen.queryByText(CANNOT_FIRE)).toBeNull();
  });

  it("for Above 0.6 and 0.2", () => {
    renderRule(
      providerRule({
        threshold_rule: "current",
        critical_threshold_value: 0.6,
        warning_threshold_value: 0.2,
      }),
    );

    expect(screen.queryByText(CANNOT_FIRE)).toBeNull();
  });

  it("for Above 95 with a warning at 0.5, which can fire", () => {
    renderRule(providerRule({ warning_threshold_value: 0.5 }));

    expect(screen.queryByText(CANNOT_FIRE)).toBeNull();
  });

  it("for Above -0.1, which always fires", () => {
    renderRule(
      providerRule({
        critical_threshold_value: -0.1,
        warning_threshold_value: null,
      }),
    );

    expect(screen.queryByText(CANNOT_FIRE)).toBeNull();
  });

  it("for Below 1.5 and 2, which always fires", () => {
    renderRule(
      providerRule({
        threshold_operator: "less_than",
        critical_threshold_value: 1.5,
        warning_threshold_value: 2,
      }),
    );

    expect(screen.queryByText(CANNOT_FIRE)).toBeNull();
  });

  it("for a percentage change provider alert", () => {
    renderRule(
      providerRule({
        threshold_type: "percentage_change",
        critical_threshold_value: 400,
        warning_threshold_value: 300,
        auto_threshold_time_window: 60,
      }),
    );

    expect(screen.queryByText(CANNOT_FIRE)).toBeNull();
  });

  it("for a Count of errors alert at 95", () => {
    renderRule(
      providerRule({
        metric_type: "count_of_errors",
        metric_name: "Count of errors",
        threshold_rule: "current",
      }),
    );

    expect(screen.queryByText(CANNOT_FIRE)).toBeNull();
    expect(screen.getByText("If Count of errors > 95")).toBeInTheDocument();
  });
});
