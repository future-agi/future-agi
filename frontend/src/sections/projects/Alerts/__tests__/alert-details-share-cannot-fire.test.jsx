import React from "react";
import { cleanup, render } from "@testing-library/react";
import { createTheme, ThemeProvider } from "@mui/material/styles";
import { afterEach, describe, expect, it, vi } from "vitest";

import { palette } from "src/theme/palette";
import { normalizeAlertDetail } from "../common";
import AlertDetails from "../components/AlertsSheetView/AlertDetails";

const mocks = vi.hoisted(() => ({ alertRuleDetails: null }));

vi.mock("src/components/svg-color", async (importOriginal) => ({
  ...(await importOriginal()),
  default: () => null,
}));
vi.mock("../store/useAlertSheetView", async (importOriginal) => ({
  ...(await importOriginal()),
  useAlertSheetView: () => ({ alertRuleDetails: mocks.alertRuleDetails }),
}));

// LLM API failure rates, error rates for function calling and the error-free
// session rate are shares from 0 to 1, and a static alert compares the share
// with its levels strictly. A level outside 0 to 1 in the alert's direction,
// such as Above 400 or Above 10 typed for 10%, can never fire. When none of an
// alert's levels can fire, its sheet says so beside the conditions, where the
// service provider error rate's note goes, and says how to fix it.
const SHARES = [
  {
    metricType: "llm_api_failure_rates",
    metricName: "LLM API failure rates",
    cannotFire:
      "This alert cannot fire: its levels are outside 0 to 1, and a share of LLM calls never is. Edit the alert and enter a share, for example 0.1 for 10%.",
  },
  {
    metricType: "error_rates_for_function_calling",
    metricName: "Error rates for function calling",
    cannotFire:
      "This alert cannot fire: its levels are outside 0 to 1, and a share of function calls never is. Edit the alert and enter a share, for example 0.1 for 10%.",
  },
  {
    metricType: "error_free_session_rates",
    metricName: "Error free session rates",
    cannotFire:
      "This alert cannot fire: its levels are outside 0 to 1, and a share of sessions never is. Edit the alert and enter a share, for example 0.9 for 90%.",
  },
];

const ERROR_FREE = SHARES.find(
  ({ metricType }) => metricType === "error_free_session_rates",
);

const LLM_ONE_LEVEL_CANNOT_FIRE =
  "This alert cannot fire: its level is outside 0 to 1, and a share of LLM calls never is. Edit the alert and enter a share, for example 0.1 for 10%.";

// A static alert as the details response sends it, saved at Above 400 and 300.
const shareRule = ({ metricType, metricName }, overrides = {}) => ({
  id: "monitor-s",
  metric_type: metricType,
  metric_name: metricName,
  threshold_type: "static",
  threshold_operator: "greater_than",
  threshold_rule: "current",
  critical_threshold_value: 400,
  warning_threshold_value: 300,
  notification_emails: [],
  ...overrides,
});

// The note the sheet shows for a saved alert, or "" when it shows none.
const noteFor = (detail) => {
  mocks.alertRuleDetails = normalizeAlertDetail(detail);
  render(
    <ThemeProvider theme={createTheme({ palette: palette("light") })}>
      <AlertDetails />
    </ThemeProvider>,
  );
  const note = document.querySelector("[data-alert-cannot-fire]");
  const text = note ? note.textContent : "";
  cleanup();
  return text;
};

afterEach(() => {
  cleanup();
  mocks.alertRuleDetails = null;
});

describe("a saved share alert that cannot fire says so on its sheet", () => {
  it.each(SHARES)(
    "for a $metricType alert at Above 400 and 300, with how to fix it, and says nothing at Above 0.2 and 0.1",
    (share) => {
      expect(noteFor(shareRule(share))).toBe(share.cannotFire);
      expect(
        noteFor(
          shareRule(share, {
            critical_threshold_value: 0.2,
            warning_threshold_value: 0.1,
          }),
        ),
      ).toBe("");
    },
  );

  it("for an LLM API failure rate alert at Above 10, typed for 10%, and says nothing at Above 0.2", () => {
    const llm = SHARES[0];

    expect(
      noteFor(
        shareRule(llm, {
          critical_threshold_value: 10,
          warning_threshold_value: null,
        }),
      ),
    ).toBe(LLM_ONE_LEVEL_CANNOT_FIRE);
    expect(
      noteFor(
        shareRule(llm, {
          critical_threshold_value: 0.2,
          warning_threshold_value: null,
        }),
      ),
    ).toBe("");
  });

  it("only when no level can fire: not for a level that always fires, a warning that can fire, or a percentage change", () => {
    expect(noteFor(shareRule(ERROR_FREE))).toBe(ERROR_FREE.cannotFire);
    // Every share is below 300 and 400, so this alert fires on every check.
    expect(
      noteFor(
        shareRule(ERROR_FREE, {
          threshold_operator: "less_than",
          critical_threshold_value: 300,
          warning_threshold_value: 400,
        }),
      ),
    ).toBe("");
    // A share above 0.95 still raises the warning.
    expect(
      noteFor(shareRule(ERROR_FREE, { warning_threshold_value: 0.95 })),
    ).toBe("");
    // A percentage change alert compares a percent, not the share.
    expect(
      noteFor(
        shareRule(ERROR_FREE, {
          threshold_type: "percentage_change",
          auto_threshold_time_window: 60,
        }),
      ),
    ).toBe("");
  });
});
