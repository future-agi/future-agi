import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "src/utils/test-utils";
import MetricDefinitionEditor from "../metric_definition_editor";
import SelectedRunSummary from "../SelectedRunSummary";
import useMetricEvaluatorNames from "../use_metric_evaluator_names";
import { formFromMetric, initialForm } from "../metricForm";
import { readMetricDraft } from "../metric_draft";
import wire from "./fixtures/committedResults.json";

const mock = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock("src/utils/axios", () => ({
  default: { get: mock.get },
  endpoints: {
    derivedMetrics: { evaluators: "/evaluators" },
    project: { updateEvalTaskConfig: (id) => `/evaluators/${id}` },
  },
}));
const A = {
  id: wire.run.metric_id,
  name: "Average latency",
  description: "",
  project_id: "project",
  current_version: 2,
  definition: { ...initialForm(), version: 2 },
};
const evalA = {
  id: "44444444-4444-4444-8444-444444444444",
  name: "Answer quality",
  config_hash: "a".repeat(64),
};
let client;
let metrics;
const choose = async (label, option) => {
  fireEvent.mouseDown(
    screen.getByLabelText(label, { selector: '[role="combobox"]' }),
  );
  fireEvent.click(await screen.findByRole("option", { name: option }));
  await waitFor(() =>
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument(),
  );
};
const click = (name) => fireEvent.click(screen.getByRole("button", { name }));
const change = (label, value) =>
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
function mountElement(element) {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>{element}</QueryClientProvider>,
  );
}
function mountEditor(initial = initialForm(), metric = null) {
  function Editor() {
    const [form, setForm] = useState(initial);
    return (
      <MetricDefinitionEditor
        form={form}
        metric={metric}
        projects={{
          data: { rows: [{ id: "project", name: "Support" }], totalPages: 1 },
        }}
        onChange={(key, value) =>
          setForm((current) => ({ ...current, [key]: value }))
        }
        onReplace={setForm}
        onSave={vi.fn()}
        onValidate={vi.fn()}
        onCancel={vi.fn()}
      />
    );
  }
  return mountElement(<Editor />);
}
beforeEach(() => {
  localStorage.clear();
  metrics = [structuredClone(A)];
  mock.get.mockReset().mockImplementation(async (url) => {
    if (url === "/evaluators")
      return { data: { results: [], truncated: true } };
    if (url === `/evaluators/${evalA.id}`)
      return { data: { id: evalA.id, name: evalA.name, project: "project" } };
    throw new Error(`Unexpected read ${url}`);
  });
});
afterEach(() => {
  cleanup();
  client?.clear();
  localStorage.clear();
});

it.each(["Average", "Total"])(
  "Ratio -> %s removes only its untouched auto-added denominator",
  async (calculation) => {
    mountEditor();
    change("Metric name", "New average");
    await choose("Project", "Support");
    click("Continue");
    await choose("Calculation", "Ratio");
    expect(screen.getByRole("button", { name: "Continue" })).toBeDisabled();
    await choose("Calculation", calculation);
    expect(screen.getByRole("button", { name: "Continue" })).toBeEnabled();
    click("Continue");
    click("Advanced configuration");
    expect(screen.getByText(/1 input ·/)).toBeInTheDocument();
  },
);

it("keeps a loaded blank denominator and makes its fix actionable beside Continue", async () => {
  metrics[0].definition = {
    ...A.definition,
    formula: "ratio(value, denominator)",
    inputs: [
      A.definition.inputs[0],
      {
        alias: "denominator",
        source: "span",
        field: "",
        attribute_key: "",
        reducer: "latest",
      },
    ],
  };
  mountEditor(formFromMetric(metrics[0]), metrics[0]);
  // Loaded incomplete data cannot advance; fix it first, then return to test
  // that switching a loaded input never silently deletes it.
  await choose("Span value · input 2", "cost");
  click("Continue");
  await choose("Span value · input 2", "attribute number");
  await choose("Calculation", "Average");
  expect(screen.getByRole("button", { name: "Continue" })).toBeDisabled();
  expect(screen.getByText(/retained input needs a source/)).toBeInTheDocument();
  click("Fix data inputs");
  expect(screen.getByLabelText("Attribute name · input 2")).toBeInTheDocument();
});

it("saved immutable definitions name an evaluator outside the first list page", async () => {
  const saved = {
    ...wire.run,
    definition: {
      ...wire.run.definition,
      inputs: [
        {
          alias: "quality",
          source: "eval",
          config_id: evalA.id,
          config_hash: evalA.config_hash,
          reducer: "latest",
        },
      ],
    },
  };
  function SavedSummary() {
    const evaluatorNames = useMetricEvaluatorNames(
      A.project_id,
      saved.definition.inputs,
      "org:ws",
    );
    return (
      <SelectedRunSummary
        run={saved}
        result={wire.old_page.results[0]}
        evaluatorNames={evaluatorNames}
      />
    );
  }
  mountElement(<SavedSummary />);
  const region = await screen.findByRole("region", {
    name: "Selected saved run",
  });
  fireEvent.click(within(region).getByText("Details"));
  expect(
    await within(region).findByText(new RegExp(`quality:.*${evalA.name}`)),
  ).toBeInTheDocument();
});

describe("r4 narrow recovery repairs", () => {
  it("normalizes an already-stored trailing slash without accepting other recovery destinations", () => {
    const stored = {
      scope: "org:ws",
      mode: "new",
      form: { ...initialForm(), name: "Existing local copy" },
      location: {
        pathname: "/dashboard/evaluations/metrics/",
        search: "?from=evals",
        hash: "#draft",
      },
    };
    localStorage.setItem(
      "metric-definition-draft:v1:org:ws",
      JSON.stringify(stored),
    );
    expect(readMetricDraft("org:ws")?.location).toEqual({
      pathname: "/dashboard/evaluations/metrics",
      search: "?from=evals",
      hash: "#draft",
    });
    for (const pathname of [
      "/dashboard/tasks/",
      "/dashboard/evaluations/metrics/other",
      "https://example.invalid/dashboard/evaluations/metrics/",
    ]) {
      localStorage.setItem(
        "metric-definition-draft:v1:org:ws",
        JSON.stringify({
          ...stored,
          location: { ...stored.location, pathname },
        }),
      );
      expect(readMetricDraft("org:ws")).toBeNull();
    }
  });
});
