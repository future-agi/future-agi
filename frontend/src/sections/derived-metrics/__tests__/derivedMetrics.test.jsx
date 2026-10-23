import React, { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, within } from "src/utils/test-utils";
import MetricInputs from "../MetricInputs";
import MetricResults from "../MetricResults";
import { formFromMetric, initialForm, metricRequestBody } from "../metricForm";

const row = (overrides) => ({
  group_key: "",
  value: 0,
  numerator: 0,
  denominator: 2,
  status: "ok",
  unit_count: 2,
  valid_count: 2,
  missing_count: 0,
  invalid_probability_count: 0,
  ...overrides,
});

describe("derived metric result semantics", () => {
  it("renders valid zero and missing data differently with persisted diagnostics", () => {
    render(
      <MetricResults
        results={[
          row({ group_key: "zero" }),
          row({
            group_key: "missing",
            value: null,
            numerator: null,
            denominator: null,
            status: "missing_inputs",
            valid_count: 0,
            missing_count: 2,
          }),
        ]}
      />,
    );
    const rows = screen.getAllByRole("row");
    expect(within(rows[1]).getAllByRole("cell")[1]).toHaveTextContent(/^0$/);
    expect(within(rows[2]).getAllByRole("cell")[1]).toHaveTextContent(/^—$/);
    expect(within(rows[2]).getByText("missing inputs")).toBeInTheDocument();
  });

  it("does not turn an invalid probability into a numeric result", () => {
    render(
      <MetricResults
        results={[
          row({
            value: null,
            numerator: null,
            denominator: null,
            status: "invalid_probability",
            invalid_probability_count: 1,
          }),
        ]}
      />,
    );
    expect(screen.getByText("invalid probability")).toBeInTheDocument();
    expect(screen.getAllByText("—")).toHaveLength(3);
  });

  const uncertainty = (overrides) => ({
    confidence_level: 0.95,
    status: "ok",
    reason: "",
    method: "stratified_wor_fpc",
    interval: "wilson",
    std_error: 0.05,
    ci_low: 0.4,
    ci_high: 0.6,
    effective_n: 120,
    degrees_of_freedom: 118,
    ...overrides,
  });

  it("shows the 95% interval in its own table and keeps the value table unchanged", () => {
    render(
      <MetricResults
        results={[
          row({ value: 0.5, numerator: 1, uncertainty: uncertainty() }),
        ]}
      />,
    );
    const values = screen.getByRole("table", { name: "Metric results" });
    expect(
      within(values)
        .getAllByRole("cell")
        .map((cell) => cell.textContent),
    ).toEqual(["All units", "0.5", "1", "2", "ok", "2", "2", "0", "0"]);
    const interval = screen.getByRole("table", { name: "Metric uncertainty" });
    const cells = within(interval)
      .getAllByRole("cell")
      .map((cell) => cell.textContent);
    expect(cells).toEqual([
      "All units",
      "0.4 – 0.6",
      "0.05",
      "120",
      "Stratified sample (finite population) · Wilson",
    ]);
  });

  it("states why an interval is not estimable instead of showing one", () => {
    render(
      <MetricResults
        results={[
          row({
            value: 3,
            uncertainty: uncertainty({
              status: "not_estimable",
              reason: "single_unit_stratum",
              interval: "",
              std_error: null,
              ci_low: null,
              ci_high: null,
            }),
          }),
        ]}
      />,
    );
    const interval = screen.getByRole("table", { name: "Metric uncertainty" });
    expect(
      within(interval).getByText("Not estimable: single unit stratum"),
    ).toBeInTheDocument();
    expect(within(interval).queryByText(/–/)).not.toBeInTheDocument();
  });
});

describe("derived metric form contract", () => {
  it("round trips only writable recipe fields and pins optimistic version", () => {
    const recipe = initialForm();
    const saved = {
      id: "metric-id",
      project_id: "project-id",
      name: "Resolution",
      description: "Saved",
      current_version: 4,
      definition: {
        ...recipe,
        version: 4,
        definition_hash: "a".repeat(64),
        created_at: "2026-10-21T00:00:00Z",
      },
    };
    const request = metricRequestBody(formFromMetric(saved), saved);
    expect(request.expected_version).toBe(4);
    expect(request.project_id).toBe("project-id");
    expect(request.formula).toBe(recipe.formula);
    expect(request).not.toHaveProperty("definition_hash");
    expect(request).not.toHaveProperty("created_at");
    expect(request).not.toHaveProperty("version");
    expect(request.inputs[0]).not.toHaveProperty("config_hash");
  });

  it("does not send hidden span fields for an evaluator input", () => {
    const form = initialForm();
    form.inputs[0] = {
      ...form.inputs[0],
      source: "eval",
      config_id: "config-id",
      config_hash: "b".repeat(64),
    };
    const request = metricRequestBody(form, null);
    expect(request.inputs[0]).toEqual({
      alias: "resolved",
      source: "eval",
      config_id: "config-id",
      config_hash: "b".repeat(64),
      reducer: "latest",
    });
    expect(request).not.toHaveProperty("expected_version");
  });

  it("switches a saved evaluator back to a numeric span input without missing field state", () => {
    function Form() {
      const [inputs, setInputs] = useState([
        {
          alias: "resolved",
          source: "eval",
          config_id: "config-id",
          config_hash: "",
          reducer: "latest",
        },
      ]);
      return (
        <MetricInputs inputs={inputs} onChange={setInputs} disabled={false} />
      );
    }
    // The evaluator picker reads through react-query, which the app always
    // provides. No project is set, so the picker never issues a request.
    render(
      <QueryClientProvider client={new QueryClient()}>
        <Form />
      </QueryClientProvider>,
    );
    fireEvent.mouseDown(screen.getByLabelText("Input 1 source"));
    fireEvent.click(screen.getByRole("option", { name: "Span field" }));
    expect(screen.getByLabelText("Input 1 attribute key")).toBeInTheDocument();
    expect(screen.getByLabelText("Input 1 field")).toHaveTextContent(
      "attribute number",
    );
  });

  it("prevents a read-only viewer from editing any input", () => {
    const change = vi.fn();
    render(
      <MetricInputs inputs={initialForm().inputs} onChange={change} disabled />,
    );
    expect(screen.getByLabelText("Input 1 alias")).toBeDisabled();
    expect(screen.getByRole("button", { name: "Add input" })).toBeDisabled();
    expect(change).not.toHaveBeenCalled();
  });
});
