import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import MetricPreviewPanel from "../MetricPreviewPanel";
afterEach(cleanup);
const data = {
  point_status: "ok",
  unit_noun: "conversations",
  value_format: "percent",
  rate_eligible: true,
  weighted: false,
  summary: "Preview of settled units; missing inputs are not zero.",
  results: [
    { group_key: "", status: "ok", value: 0, numerator: 0, denominator: 3 },
  ],
  sample_units: [
    {
      unit_id: "missing",
      status: "no_value",
      no_value_reason: "missing_inputs",
      value_bool: null,
      inputs: { price: null },
      inputs_bool: {},
      inputs_text: {},
    },
    {
      unit_id: "false-unit",
      status: "value",
      value_bool: false,
      inputs: { price: 0 },
      inputs_bool: { resolved: false },
      inputs_text: { locale: "fr" },
    },
    {
      unit_id: "true-unit",
      status: "value",
      value_bool: true,
      inputs: {},
      inputs_bool: {},
      inputs_text: {},
    },
  ],
};
describe("honest previews", () => {
  // Round 3 UI: the basis comes from the returned counts, never the rate.
  it("states what the preview read and left out, rounding only the headline", () => {
    render(
      <MetricPreviewPanel
        form={{ kind: "share" }}
        preview={{
          state: "ready",
          data: {
            ...data,
            results: [
              {
                group_key: "",
                status: "ok",
                value: 0.801887,
                numerator: 1255,
                denominator: 1565,
                unit_count: 1577,
                valid_count: 1565,
                missing_count: 12,
              },
            ],
          },
        }}
      />,
    );
    expect(screen.getByText("80.2%").getAttribute("title")).toBe("80.1887%");
    expect(screen.getByText("1,255 of 1,565 conversations match")).toBeTruthy();
    // The basis is the units with a value (valid_count), out of every unit
    // read; never the larger count alone.
    expect(
      screen.getByText("Based on 1,565 of 1,577 finished conversations."),
    ).toBeTruthy();
    expect(
      screen.queryByText("Based on 1,577 finished conversations."),
    ).toBeNull();
    expect(
      screen.getByText("12 had no value and are left out, not counted as 0."),
    ).toBeTruthy();
    expect(screen.queryByText(data.summary)).toBeNull();
    expect(
      screen.getByText("Last 7 days · finished conversations"),
    ).toBeTruthy();
    expect(
      screen.getByText(/Saving starts the metric’s first calculation/),
    ).toBeTruthy();
  });
  it("says an invalid sampling probability withholds the value, and claims no basis", () => {
    render(
      <MetricPreviewPanel
        form={{ kind: "share" }}
        preview={{
          state: "ready",
          data: {
            ...data,
            point_status: "invalid_probability",
            sample_units: [],
            results: [
              {
                group_key: "",
                status: "invalid_probability",
                value: null,
                numerator: null,
                denominator: null,
                unit_count: 40,
                valid_count: 40,
                missing_count: 0,
                invalid_probability_count: 3,
              },
            ],
          },
        }}
      />,
    );
    expect(screen.getByText("No value")).toBeTruthy();
    expect(screen.getByText("Read 40 finished conversations.")).toBeTruthy();
    expect(
      screen.getByText(
        "3 had an invalid sampling probability, so no value is shown.",
      ),
    ).toBeTruthy();
    expect(screen.queryByText(/^Based on/)).toBeNull();
  });
  it("says every unit read has a value when none is missing", () => {
    render(
      <MetricPreviewPanel
        form={{ kind: "share" }}
        preview={{
          state: "ready",
          data: {
            ...data,
            sample_units: [],
            results: [
              {
                group_key: "",
                status: "ok",
                value: 0.5,
                numerator: 20,
                denominator: 40,
                unit_count: 40,
                valid_count: 40,
                missing_count: 0,
                invalid_probability_count: 0,
              },
            ],
          },
        }}
      />,
    );
    expect(
      screen.getByText("Based on all 40 finished conversations."),
    ).toBeTruthy();
    expect(screen.queryByText(/invalid sampling probability/)).toBeNull();
  });
  it("keeps the active-version note for an edit and groups input digits", () => {
    render(
      <MetricPreviewPanel
        form={{ kind: "share" }}
        mode="edit"
        preview={{
          state: "ready",
          data: {
            ...data,
            sample_units: [
              {
                unit_id: "big",
                status: "value",
                value_bool: true,
                inputs: { price: 12345.5 },
                inputs_bool: {},
                inputs_text: {},
              },
            ],
          },
        }}
      />,
    );
    expect(screen.getByText("price: 12,345.5")).toBeTruthy();
    expect(screen.getByText(/the active version stays visible/)).toBeTruthy();
    expect(
      screen.getByText(
        "Conversations still in progress, or without a value, are left out, not counted as 0.",
      ),
    ).toBeTruthy();
  });
  it("distinguishes zero, false, null and text with default labels", () => {
    render(
      <MetricPreviewPanel
        form={{ kind: "share" }}
        preview={{ state: "ready", data }}
      />,
    );
    expect(screen.getByText("0%")).toBeTruthy();
    expect(screen.getByText("0 of 3 conversations match")).toBeTruthy();
    expect(screen.getByText("Doesn't count")).toBeTruthy();
    expect(screen.getByText("price: 0")).toBeTruthy();
    expect(screen.getByText("resolved: false")).toBeTruthy();
    expect(screen.getByText("price: No value")).toBeTruthy();
    expect(screen.getByText("locale: fr")).toBeTruthy();
  });
  it("uses custom labels only for boolean sample values", () => {
    const { rerender } = render(
      <MetricPreviewPanel
        form={{
          kind: "share",
          value_labels: { match: "Resolved", other: "Not resolved" },
        }}
        preview={{ state: "ready", data }}
      />,
    );
    expect(screen.getByText("Resolved")).toBeTruthy();
    expect(screen.getByText("Not resolved")).toBeTruthy();
    rerender(
      <MetricPreviewPanel
        form={{
          kind: "total",
          value_labels: { match: "Resolved", other: "Not resolved" },
        }}
        preview={{
          state: "ready",
          data: {
            ...data,
            sample_units: [
              { unit_id: "numeric", status: "value", value_number: 0 },
            ],
          },
        }}
      />,
    );
    expect(screen.queryByText("Resolved")).toBeNull();
    expect(screen.getByText("0")).toBeTruthy();
  });
  it.each(["unavailable", "busy", "superseded"])(
    "provides retry for %s and hides stale results",
    (state) => {
      const retry = vi.fn();
      render(
        <MetricPreviewPanel
          form={{ kind: "share" }}
          preview={{ state, data, retry }}
        />,
      );
      expect(screen.queryByText("0%")).toBeNull();
      fireEvent.click(screen.getByRole("button", { name: "Retry" }));
      expect(retry).toHaveBeenCalledOnce();
    },
  );
  it("does not invent a zero for an empty result or a missing value", () => {
    const { rerender } = render(
      <MetricPreviewPanel
        form={{ kind: "share" }}
        preview={{
          state: "ready",
          data: {
            ...data,
            point_status: "no_data",
            results: [],
            sample_units: [],
          },
        }}
      />,
    );
    expect(screen.getByText("No data")).toBeTruthy();
    expect(screen.queryByText("0%")).toBeNull();
    rerender(
      <MetricPreviewPanel
        form={{ kind: "share" }}
        preview={{
          state: "ready",
          data: {
            ...data,
            results: [{ group_key: "", status: "ok", value: null }],
            sample_units: [],
          },
        }}
      />,
    );
    expect(screen.getByText("No value")).toBeTruthy();
    expect(screen.queryByText("0%")).toBeNull();
  });
  it("never presents a grouped row as a total or a weighted numerator as a match count", () => {
    render(
      <MetricPreviewPanel
        form={{ kind: "share" }}
        preview={{
          state: "ready",
          data: {
            ...data,
            weighted: true,
            results: [{ ...data.results[0], group_key: "model-a" }],
          },
        }}
      />,
    );
    expect(screen.queryByText("0%")).toBeNull();
    expect(screen.queryByText(/of 3 conversations match/)).toBeNull();
  });
  it("names sample inputs by their source and never shows the compiled condition alias", () => {
    render(
      <MetricPreviewPanel
        form={{
          kind: "share",
          level: "conversation",
          inputs: [
            {
              alias: "value_1",
              source: "span",
              field: "attribute_number",
              attribute_key: "resolved",
              value_type: "number",
              reducer: "latest",
            },
          ],
          field_spec: {
            conditions: [{ alias: "value_1", operator: "eq", value: 1 }],
          },
        }}
        preview={{
          state: "ready",
          data: {
            ...data,
            sample_units: [
              {
                unit_id: "f04ffcfa-e377-5a81-80ec-19aac081098f",
                status: "value",
                value_bool: true,
                inputs: { value_1: 1, field_condition: 1 },
                inputs_bool: {},
                inputs_text: {},
              },
            ],
          },
        }}
      />,
    );
    expect(
      screen.getByText("Share of conversations where resolved (latest) is 1"),
    ).toBeTruthy();
    expect(screen.getByText("resolved: 1")).toBeTruthy();
    expect(screen.queryByText(/value_1/)).toBeNull();
    expect(screen.queryByText(/field_condition/)).toBeNull();
    // The full unit ID stays readable for copy and assistive tech.
    expect(
      screen.getByText("f04ffcfa-e377-5a81-80ec-19aac081098f"),
    ).toBeTruthy();
    expect(screen.getByText("Counts")).toBeTruthy();
  });
  it("shows at most twenty sample rows", () => {
    render(
      <MetricPreviewPanel
        form={{ kind: "share" }}
        preview={{
          state: "ready",
          data: {
            ...data,
            sample_units: Array.from({ length: 30 }, (_, i) => ({
              ...data.sample_units[0],
              unit_id: `unit-${i}`,
            })),
          },
        }}
      />,
    );
    expect(screen.getAllByRole("row")).toHaveLength(21);
  });
  it("formats the hero and unit values with the same unit and grouping", () => {
    render(
      <MetricPreviewPanel
        form={{ kind: "average", unit: "ms", value_format: "number" }}
        preview={{
          state: "ready",
          data: {
            ...data,
            value_format: "number",
            unit: "",
            rate_eligible: false,
            results: [{ group_key: "", status: "ok", value: 2445.32 }],
            sample_units: [
              {
                unit_id: "slow",
                status: "value",
                value_number: 1297,
                inputs: { duration: 1297 },
              },
              {
                unit_id: "missing",
                status: "no_value",
                no_value_reason: "missing_inputs",
                value_number: null,
                inputs: { duration: null },
              },
            ],
          },
        }}
      />,
    );
    expect(screen.getByText("2,445.32 ms")).toBeTruthy();
    expect(screen.getByText("1,297 ms")).toBeTruthy();
    // A missing unit value is a labelled state with its reason, never zero.
    expect(screen.getByText("No value")).toBeTruthy();
    expect(screen.queryByText("0 ms")).toBeNull();
  });
  it("names only the missing piece while idle, never a project already chosen", () => {
    const { rerender } = render(
      <MetricPreviewPanel
        form={{ kind: "share", project_id: "", inputs: [] }}
        preview={{ state: "idle" }}
      />,
    );
    expect(screen.getByText("Choose a project to see a preview.")).toBeTruthy();
    rerender(
      <MetricPreviewPanel
        form={{ kind: "share", project_id: "project-a", inputs: [] }}
        preview={{ state: "idle" }}
      />,
    );
    expect(screen.getByText("Choose a source to see a preview.")).toBeTruthy();
    expect(screen.queryByText(/project/i)).toBeNull();
    rerender(
      <MetricPreviewPanel
        form={{ kind: "share", project_id: "project-a", inputs: [] }}
        preview={{ state: "idle" }}
        idleMessage="Rebuild this metric to see a preview."
      />,
    );
    expect(
      screen.getByText("Rebuild this metric to see a preview."),
    ).toBeTruthy();
    expect(screen.queryByText("Choose a source to see a preview.")).toBeNull();
  });
  it("names the field still missing a value, and marks it in the sentence for screen readers", () => {
    render(
      <MetricPreviewPanel
        form={{
          kind: "share",
          level: "conversation",
          project_id: "project-a",
          inputs: [
            {
              alias: "value_1",
              source: "span",
              field: "attribute_text",
              attribute_key: "locale",
              value_type: "text",
              reducer: "latest",
            },
          ],
          field_spec: {
            conditions: [{ alias: "value_1", operator: "eq", value: null }],
          },
        }}
        preview={{ state: "idle" }}
      />,
    );
    expect(
      screen.getByText("Choose a value for locale to see a preview."),
    ).toBeTruthy();
    const sentence = screen.getByText(/^Share of conversations where/);
    expect(sentence).toHaveTextContent(
      "Share of conversations where locale (latest) is …no value yet",
    );
    expect(screen.getByText("…")).toHaveAttribute("aria-hidden", "true");
    expect(screen.queryByText(/to compare with/)).toBeNull();
  });
});
