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
});
