import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createTheme, ThemeProvider } from "@mui/material/styles";

import MetricValueCell from "../MetricValueCell";
import { metricCopy, noValueReasons } from "../copy";
import { palette } from "../../../theme/palette";
import { typography } from "../../../theme/typography";

// Contract-derived synthetic examples, not app/API integration fixtures.
const field = (overrides = {}) => ({
  id: "metric_field:synthetic",
  level: "conversation",
  kind: "custom",
  value_type: "number",
  value_format: "number",
  unit: "",
  value_labels: {},
  operators: [],
  projection: false,
  ...overrides,
});
const unit = (overrides = {}) => ({
  unit_id: "synthetic-unit",
  status: "value",
  no_value_reason: "",
  value_number: null,
  value_bool: null,
  value_text: null,
  inputs: {},
  inputs_bool: {},
  inputs_text: {},
  influence: null,
  eligible_count: 4,
  evaluated_count: 4,
  missing_count: 0,
  ...overrides,
});
const point = (overrides = {}) => ({
  value: 0.62,
  point_status: "ok",
  rate_eligible: true,
  weighted: true,
  unit: "",
  value_format: "percent",
  unit_noun: "conversations",
  value_mode: "materialized",
  eligible_count: 1200,
  evaluated_count: 120,
  missing_count: 1080,
  uncertainty: {
    confidence_level: 0.95,
    status: "ok",
    reason: "",
    method: "poisson",
    interval: "wilson",
    std_error: 0.04,
    ci_low: 0.53,
    ci_high: 0.7,
    effective_n: 75,
    degrees_of_freedom: 74,
  },
  ...overrides,
});
const mount = (props, mode = "light") =>
  render(
    <ThemeProvider theme={createTheme({ palette: palette(mode), typography })}>
      <MetricValueCell locale="en-US" {...props} />
    </ThemeProvider>,
  );
afterEach(cleanup);

describe.each(["light", "dark"])("MetricValueCell in %s theme", (mode) => {
  it("keeps zero and null distinct", () => {
    const zero = mount(
      { value: unit({ value_number: 0 }), field: field() },
      mode,
    );
    expect(screen.getByText("0")).toBeVisible();
    expect(
      zero.container.querySelector('[data-value-state="value"]'),
    ).toBeTruthy();
    zero.unmount();
    mount({ value: unit(), field: field() }, mode);
    expect(screen.getByText("No value")).toBeVisible();
    expect(screen.queryByText("0")).not.toBeInTheDocument();
  });
  it.each([
    [true, "Counts"],
    [false, "Doesn't count"],
    [null, "No value"],
  ])("shows Boolean %s as %s", (value_bool, label) => {
    mount(
      { value: unit({ value_bool }), field: field({ value_type: "boolean" }) },
      mode,
    );
    expect(screen.getByText(label)).toBeVisible();
    expect(screen.queryByText("0")).not.toBeInTheDocument();
  });
  it.each(Object.entries(noValueReasons))(
    "shows no-value reason %s",
    (no_value_reason, reason) => {
      mount(
        {
          value: unit({
            status: "no_value",
            no_value_reason,
            value_bool: false,
            value_number: 0,
          }),
          field: field(),
        },
        mode,
      );
      expect(screen.getByText(`No value: ${reason}`)).toBeVisible();
      expect(screen.queryByText("0")).not.toBeInTheDocument();
    },
  );
  it.each([
    ["in_progress", "In progress"],
    ["not_calculated", "Not calculated yet"],
  ])("shows %s without stale Boolean labels", (status, label) => {
    mount(
      {
        value: unit({ status, value_bool: false }),
        field: field({ value_type: "boolean" }),
      },
      mode,
    );
    expect(screen.getByText(label)).toBeVisible();
    expect(screen.queryByText("Doesn't count")).not.toBeInTheDocument();
  });
  it("keeps user share projections numeric with returned diagnostics", () => {
    mount(
      {
        value: unit({
          value_number: 0.25,
          value_bool: true,
          eligible_count: 6,
          missing_count: 2,
        }),
        field: field({
          level: "user",
          kind: "share",
          value_format: "percent",
          projection: true,
          value_labels: { match: "Resolved", other: "Not resolved" },
        }),
      },
      mode,
    );
    expect(screen.getByText("25%")).toBeVisible();
    expect(screen.queryByText("Resolved")).not.toBeInTheDocument();
    expect(screen.getByText("4 evaluated · 6 eligible")).toBeVisible();
    expect(screen.getByText("2 with no value")).toBeVisible();
  });
  it("keeps projected zero counts numeric", () => {
    mount(
      {
        value: unit({ value_number: 0, value_bool: false }),
        field: field({
          level: "user",
          kind: "count",
          projection: true,
          unit: "total",
        }),
      },
      mode,
    );
    expect(screen.getByText("0 total")).toBeVisible();
    expect(screen.queryByText("Doesn't count")).not.toBeInTheDocument();
  });
  it("renders estimates with asymmetric bounds, server nouns, and no fabricated margin", () => {
    mount({ source: "point", value: point() }, mode);
    expect(screen.getByText("62%")).toBeVisible();
    expect(screen.getByText("Estimate")).toBeVisible();
    expect(screen.getByText("95% interval: 53% – 70%")).toBeVisible();
    expect(
      screen.getByText("from 120 evaluated of 1,200 conversations"),
    ).toBeVisible();
    expect(screen.queryByText(/±/)).not.toBeInTheDocument();
  });
  it("does not show a sampled total as a percent", () => {
    mount(
      {
        source: "point",
        value: point({
          value: 1500,
          rate_eligible: false,
          unit: "tokens",
          value_format: "number",
          uncertainty: {
            status: "not_estimable",
            reason: "unstable_variance",
            ci_low: null,
            ci_high: null,
          },
        }),
      },
      mode,
    );
    expect(screen.getByText("1,500 tokens")).toBeVisible();
    expect(screen.getByText("Estimate")).toBeVisible();
    expect(
      screen.getByText(
        "Interval unavailable: insufficient support for a reliable interval",
      ),
    ).toBeVisible();
    expect(screen.queryByText(/1500%/)).not.toBeInTheDocument();
  });
  it("keeps exact zero visible without an estimate marker", () => {
    mount(
      { source: "point", value: point({ value: 0, weighted: false }) },
      mode,
    );
    expect(screen.getByText("0%")).toBeVisible();
    expect(screen.queryByText("Estimate")).not.toBeInTheDocument();
  });
  it("shows unavailable user estimates rather than Boolean or numeric placeholders", () => {
    mount(
      {
        source: "point",
        value: point({
          point_status: "sampled_inputs_not_supported",
          value: null,
          value_mode: "on_demand",
          unit_noun: "users",
          counted_users: 0,
          eligible_users: 4,
          no_value_users: 4,
        }),
      },
      mode,
    );
    expect(screen.getByText(metricCopy.sampledInputs)).toBeVisible();
    expect(screen.getByText("0 of 4 users")).toBeVisible();
    expect(screen.getByText("4 users with no value")).toBeVisible();
    expect(screen.queryByText("0%")).not.toBeInTheDocument();
    expect(screen.queryByText("Estimate")).not.toBeInTheDocument();
  });
  it("uses on-demand loading without showing the old cached value or counts", () => {
    mount(
      {
        source: "point",
        value: point({ value_mode: "on_demand" }),
        loading: true,
      },
      mode,
    );
    expect(screen.getByText(metricCopy.onDemand)).toBeVisible();
    expect(screen.queryByText("62%")).not.toBeInTheDocument();
    expect(screen.queryByText(/evaluated of/)).not.toBeInTheDocument();
  });
  it("shows stale values explicitly", () => {
    mount({ source: "point", value: point(), stale: true }, mode);
    expect(screen.getByText("62%")).toBeVisible();
    expect(screen.getByText(/Stale value/)).toBeVisible();
  });
  it("shows errors without stale numbers or raw exception content", () => {
    mount({ source: "point", value: point(), error: true }, mode);
    expect(screen.getByText(metricCopy.error)).toBeVisible();
    expect(screen.queryByText("62%")).not.toBeInTheDocument();
  });
  it.each([
    ["no_data", "No data"],
    ["unavailable", metricCopy.unsupported],
    ["future_status", metricCopy.unsupported],
  ])("shows point %s explicitly", (point_status, label) => {
    mount({ source: "point", value: point({ point_status }) }, mode);
    expect(screen.getByText(label)).toBeVisible();
    expect(screen.queryByText("62%")).not.toBeInTheDocument();
  });
  it("escapes server-supplied text and labels", () => {
    const text = '<img src=x onerror="alert(1)">';
    const result = mount(
      {
        value: unit({ value_text: text }),
        field: field({ value_type: "text" }),
      },
      mode,
    );
    expect(screen.getByText(text)).toBeVisible();
    expect(result.container.querySelector("img")).toBeNull();
  });
  it("supports keyboard focus, description, and Escape on the actual MUI tooltip", async () => {
    const user = userEvent.setup();
    mount({ source: "point", value: point() }, mode);
    await user.tab();
    await screen.findByRole("tooltip");
    expect(document.activeElement).toHaveAccessibleDescription(
      metricCopy.estimateExplanation,
    );
    await user.keyboard("{Escape}");
    await waitFor(() =>
      expect(screen.queryByRole("tooltip")).not.toBeInTheDocument(),
    );
  });
});
