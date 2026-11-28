import { afterEach, expect, it } from "vitest";
import { cleanup, render, screen, within } from "src/utils/test-utils";
import MetricDefinition from "../MetricDefinition";
import { metric, percentileMetrics } from "./metricPageFixtures";
afterEach(cleanup);
it("renders the exact typed source, reducer and observation condition", () => {
  const definition = {
    ...metric.definition,
    inputs: [
      {
        alias: "locale",
        source: "span",
        field: "attribute_text",
        attribute_key: "locale",
        value_type: "text",
        reducer: "any",
        condition_stage: "observation",
        condition: { operator: "ne", value: "fr" },
      },
    ],
  };
  render(<MetricDefinition metric={{ ...metric, definition }} />);
  const table = screen.getByRole("table", { name: "Definition inputs" });
  expect(within(table).getByText('is not "fr"')).toBeVisible();
  // Reducer and type read as words; the stored value stays on hover.
  expect(within(table).getByText("Any observation matches")).toHaveAttribute(
    "title",
    "Stored as any",
  );
  expect(within(table).getByText("Text")).toHaveAttribute(
    "title",
    "Stored as text",
  );
  expect(within(table).getByText("locale")).toBeVisible();
  expect(within(table).getByText("Span attribute")).toBeVisible();
  // A sentence recipe's generated alias is not a user-facing name.
  expect(
    within(table).queryByRole("columnheader", { name: "Alias" }),
  ).not.toBeInTheDocument();
});
it("names definition settings in words instead of stored enums", () => {
  render(<MetricDefinition metric={metric} />);
  for (const [label, value] of [
    ["Per-unit result", "Yes or no"],
    ["Format", "Percentage"],
    ["Weighting", "None"],
  ])
    expect(
      screen.getByText(label, { selector: "dt" }).nextSibling,
    ).toHaveTextContent(value);
});
it("preserves zero, false, empty string and alias comparisons without truthiness fallbacks", () => {
  const definition = {
    ...metric.definition,
    field_spec: {
      conditions: [
        { alias: "a", operator: "eq", value: 0 },
        { alias: "b", operator: "ne", value: false },
        { alias: "c", operator: "eq", value: "" },
        { alias: "d", operator: "gt", right_alias: "other" },
      ],
    },
  };
  render(<MetricDefinition metric={{ ...metric, definition }} />);
  for (const text of [
    "a is 0",
    "b is not false",
    'c is ""',
    "d is above other",
  ])
    expect(screen.getByText(text)).toBeVisible();
});
it("keeps a legacy custom formula intact with a rebuild notice", () => {
  render(
    <MetricDefinition
      metric={{
        ...metric,
        definition: {
          ...metric.definition,
          kind: "custom",
          needs_rebuild: true,
          formula: "sum(cost) / sum(tokens)",
        },
      }}
    />,
  );
  expect(screen.getByText("Custom formula")).toBeVisible();
  expect(screen.getByText("sum(cost) / sum(tokens)")).toBeVisible();
  expect(screen.getByText(/needs a rebuild/)).toBeVisible();
});
it.each(percentileMetrics)(
  "renders the selected percentile for $id instead of its stored helper formula",
  (percentileMetric) => {
    render(<MetricDefinition metric={percentileMetric} />);
    const label = `P${percentileMetric.definition.field_spec.percentile * 100}`;
    expect(screen.getByText("Percentile", { selector: "dd" })).toBeVisible();
    expect(screen.getByText(label, { selector: "dd" })).toBeVisible();
    expect(
      screen.getByRole("heading", { name: "Percentile calculation" }),
    ).toBeVisible();
    expect(
      screen.getByText(
        `Estimates ${label} of duration from the combined distribution of per-unit values using a t-digest. This is a percentile, not an average.`,
      ),
    ).toBeVisible();
    expect(screen.queryByText("mean(duration)")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { name: "Formula" }),
    ).not.toBeInTheDocument();
  },
);
it("updates the percentile explanation when switching between otherwise identical recipes", () => {
  const view = render(<MetricDefinition metric={percentileMetrics[0]} />);
  expect(screen.getByText("P50", { selector: "dd" })).toBeVisible();
  view.rerender(<MetricDefinition metric={percentileMetrics[1]} />);
  expect(screen.getByText("P95", { selector: "dd" })).toBeVisible();
  expect(screen.queryByText(/P50/)).not.toBeInTheDocument();
});
it("preserves an exact custom formula even if field metadata contains a percentile", () => {
  const custom = {
    ...percentileMetrics[0],
    definition: {
      ...percentileMetrics[0].definition,
      kind: "custom",
      formula: "sum(cost) / sum(tokens)",
    },
  };
  render(<MetricDefinition metric={custom} />);
  expect(screen.getByText("Custom formula", { selector: "dd" })).toBeVisible();
  expect(
    screen.getByText(custom.definition.formula, { selector: "pre" })
      .textContent,
  ).toBe(custom.definition.formula);
  expect(screen.queryByText("Selected percentile")).not.toBeInTheDocument();
});
it("never substitutes an unactivated draft when the active definition is absent", () => {
  render(<MetricDefinition metric={{ ...metric, definition: null }} />);
  // An expected state, not an error: no assertive alert is announced.
  expect(screen.getByText(/No active definition yet/)).toBeVisible();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  expect(screen.queryByText(/Active definition · v3/)).not.toBeInTheDocument();
});
// Round 3 UI: the saved version is readable before its first calculation,
// read-only and labelled as waiting. Edit in the page header is the way in.
it("shows a saved version waiting for its first calculation read-only, never as active", () => {
  render(
    <MetricDefinition
      metric={{
        ...metric,
        definition: null,
        draft_definition: {
          ...metric.definition,
          id: "version-3",
          version: 3,
          backfill_status: "pending",
          created_at: "2026-11-11T05:24:07Z",
        },
      }}
    />,
  );
  expect(screen.getByRole("heading", { name: "Definition v3" })).toBeVisible();
  expect(screen.getByText("First calculation pending")).toBeVisible();
  expect(screen.getByText("11 Nov 2026, 05:24:07 UTC")).toBeVisible();
  expect(screen.getByText("passed is true")).toBeVisible();
  expect(screen.queryByText(/Active definition/)).not.toBeInTheDocument();
  expect(screen.queryByText("Activated")).not.toBeInTheDocument();
  expect(screen.queryByRole("button")).not.toBeInTheDocument();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});
it("names a saved evaluation input when its name is known, keeping the saved ID", () => {
  render(
    <MetricDefinition
      metric={metric}
      evaluatorNames={{ "eval-a": "Answer passed" }}
    />,
  );
  expect(screen.getByText("Answer passed")).toBeVisible();
  expect(screen.getByText("eval-a")).toBeVisible();
});
