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
});
