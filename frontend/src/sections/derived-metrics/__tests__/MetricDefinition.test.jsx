import { afterEach, expect, it } from "vitest";
import { cleanup, render, screen, within } from "src/utils/test-utils";
import MetricDefinition from "../MetricDefinition";
import { metric } from "./metricPageFixtures";
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
  expect(within(table).getByText("any")).toBeVisible();
  expect(within(table).getByText("text")).toBeVisible();
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
it("never substitutes an unactivated draft when the active definition is absent", () => {
  render(<MetricDefinition metric={{ ...metric, definition: null }} />);
  expect(screen.getByRole("alert")).toHaveTextContent(
    "No active definition yet",
  );
  expect(screen.queryByText(/Active definition · v3/)).not.toBeInTheDocument();
});
