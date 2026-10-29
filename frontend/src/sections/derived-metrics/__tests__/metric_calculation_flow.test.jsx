import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "src/utils/test-utils";
import MetricDefinitionEditor from "../metric_definition_editor";
import {
  applyCalculation,
  calculationFromForm,
  definitionSummary,
} from "../metric_calculation";
import { formFromMetric, initialForm, metricRequestBody } from "../metricForm";

const evaluator = {
  id: "44444444-4444-4444-8444-444444444444",
  name: "Quality score",
  config_hash: "b".repeat(64),
};
vi.mock("src/utils/axios", () => ({
  default: {
    get: vi.fn(async () => ({
      data: { results: [evaluator], truncated: false },
    })),
  },
  endpoints: { derivedMetrics: { evaluators: "/evaluators/" } },
}));
const project = { id: "33333333-3333-4333-8333-333333333333", name: "Support" };
let client;
afterEach(() => {
  cleanup();
  client?.clear();
});
function mount(form, props = {}) {
  const metricForSave = props.metric;
  const save = vi.fn();
  const validate = vi.fn();
  function Harness() {
    const [draft, setDraft] = useState(form);
    return (
      <MetricDefinitionEditor
        form={draft}
        projects={{ data: { rows: [project], totalPages: 1 } }}
        onChange={(key, value) =>
          setDraft((current) => ({ ...current, [key]: value }))
        }
        onReplace={setDraft}
        onSave={() => save(metricRequestBody(draft, metricForSave))}
        onValidate={() => validate(draft)}
        onCancel={vi.fn()}
        {...props}
      />
    );
  }
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <Harness />
    </QueryClientProvider>,
  );
  return { save, validate };
}
const choose = async (label, option) => {
  fireEvent.mouseDown(screen.getByLabelText(label));
  fireEvent.click(await screen.findByRole("option", { name: option }));
};
const next = () =>
  fireEvent.click(screen.getByRole("button", { name: "Continue" }));

describe("guided calculation uses the actual four aggregate DSL forms", () => {
  it("starts without fictional business attributes or mandatory raw formula/IDs", () => {
    const form = {
      ...initialForm(),
      project_id: project.id,
      name: "Average latency",
    };
    const { save } = mount(form);
    expect(form.inputs).toEqual([
      expect.objectContaining({
        alias: "value",
        field: "latency_ms",
        attribute_key: "",
      }),
    ]);
    expect(screen.queryByLabelText("Formula")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Input 1 alias")).not.toBeInTheDocument();
    next();
    expect(screen.getByLabelText("Calculation")).toHaveTextContent("Average");
    next();
    fireEvent.click(screen.getByRole("button", { name: "Save metric" }));
    expect(save).toHaveBeenCalledWith(
      expect.objectContaining({
        formula: "mean(value)",
        selection_unit: "session",
        inputs: [
          expect.objectContaining({ reducer: "latest", field: "latency_ms" }),
        ],
      }),
    );
  });
  it.each(["mean", "min", "max", "latest"])(
    "binds a named existing evaluator and preserves its exact hash with %s reduction",
    async (reducer) => {
      const { save } = mount({
        ...initialForm(),
        project_id: project.id,
        name: "Quality",
      });
      await choose("What to summarize", "Evaluation result");
      fireEvent.mouseDown(screen.getByLabelText("Input 1 evaluator"));
      fireEvent.click(
        await screen.findByRole("option", { name: /Quality score/ }),
      );
      await choose("If a session has several values, use", reducer);
      next();
      next();
      fireEvent.click(screen.getByRole("button", { name: "Save metric" }));
      expect(save).toHaveBeenCalledWith(
        expect.objectContaining({
          formula: "mean(value)",
          inputs: [
            {
              alias: "value",
              source: "eval",
              reducer,
              config_id: evaluator.id,
              config_hash: evaluator.config_hash,
            },
          ],
        }),
      );
    },
  );
  it("generates Total, Count matching and Ratio only on explicit changes and retains all inputs", () => {
    const form = initialForm();
    expect(applyCalculation(form, { kind: "sum" }).formula).toBe("sum(value)");
    const count = applyCalculation(form, {
      kind: "count_if",
      operator: ">=",
      threshold: "0.75",
    });
    expect(count.formula).toBe("count_if(value >= 0.75)");
    expect(calculationFromForm(count)).toEqual({
      kind: "count_if",
      operator: ">=",
      threshold: "0.75",
    });
    const ratio = applyCalculation(form, { kind: "ratio" });
    expect(ratio.formula).toBe("ratio(value, denominator)");
    expect(ratio.inputs[1].field).toBe(""); // Requires a real source choice, no invented denominator.
    expect(applyCalculation(ratio, { kind: "sum" }).inputs).toEqual(
      ratio.inputs,
    );
    expect(calculationFromForm({ ...ratio, formula: "sum(value)" }).kind).toBe(
      "custom",
    );
  });
  it("blocks incomplete conditions rather than saving an invalid generated expression", async () => {
    const { save } = mount({
      ...initialForm(),
      name: "Count",
      project_id: project.id,
    });
    next();
    await choose("Calculation", "Count matching");
    fireEvent.change(screen.getByLabelText("Matching value"), {
      target: { value: "" },
    });
    expect(screen.getByRole("button", { name: "Continue" })).toBeDisabled();
    expect(save).not.toHaveBeenCalled();
  });
});

describe("loaded custom definitions are lossless", () => {
  it("preserves the raw formula, multiple inputs, all reducers, hidden filters/cohort/weighting and optimistic version", async () => {
    const form = {
      ...initialForm(),
      formula: "ratio( quality > 0.7 and cost < 3, quality >= 0 )",
      inputs: [
        {
          alias: "quality",
          source: "eval",
          config_id: evaluator.id,
          config_hash: evaluator.config_hash,
          reducer: "min",
        },
        {
          alias: "cost",
          source: "span",
          field: "cost",
          attribute_key: "",
          reducer: "max",
        },
        {
          alias: "extra",
          source: "span",
          field: "total_tokens",
          attribute_key: "",
          reducer: "mean",
        },
        {
          alias: "last",
          source: "span",
          field: "latency_ms",
          attribute_key: "",
          reducer: "latest",
        },
      ],
      filters: [{ field: "model", value: "exact-model" }],
      cohort_key: "team",
      weighting: "inverse_probability",
    };
    const metric = {
      id: "m",
      name: "Saved custom",
      description: "",
      project_id: project.id,
      current_version: 7,
      definition: form,
    };
    const savedForm = formFromMetric(metric);
    expect(calculationFromForm(savedForm).kind).toBe("custom");
    const { save } = mount(savedForm, { metric });
    fireEvent.click(
      screen.getByRole("button", { name: "Advanced configuration" }),
    );
    expect(screen.getByText(/Cohort: team/)).toHaveTextContent(
      /model = exact-model/,
    );
    next();
    expect(screen.getByLabelText("Formula")).toHaveValue(form.formula);
    expect(screen.getByLabelText("Calculation")).toHaveTextContent(
      "Custom formula",
    );
    next();
    fireEvent.click(screen.getByRole("button", { name: "Save new version" }));
    await waitFor(() => expect(save).toHaveBeenCalledOnce());
    expect(save.mock.calls[0][0]).toEqual({
      ...savedForm,
      expected_version: 7,
    });
    expect(definitionSummary(form)).toMatch(
      /4 inputs.*min.*max.*mean.*latest.*Inverse-probability weighted.*Cohort: team.*model = exact-model/,
    );
  });
});
