import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Link, Route, Routes } from "react-router-dom";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "src/utils/test-utils";
import DerivedMetricsView from "../DerivedMetricsView";
import { initialForm } from "../metricForm";
import { readMetricDraft } from "../metric_draft";
import wire from "./fixtures/committedResults.json";

const mock = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  workspace: "ws",
}));
vi.mock("src/utils/axios", () => ({
  default: { get: mock.get, post: mock.post, put: mock.put },
  endpoints: {
    derivedMetrics: {
      list: "/metrics",
      detail: (id) => `/metrics/${id}`,
      validate: "/validate",
      runs: (id) => `/metrics/${id}/runs`,
      run: (id) => `/runs/${id}`,
      results: (id) => `/runs/${id}/results`,
      preview: (id) => `/metrics/${id}/preview`,
      contributors: (id) => `/runs/${id}/contributors`,
      evaluators: "/evaluators",
    },
    project: { updateEvalTaskConfig: (id) => `/evaluators/${id}` },
  },
}));
vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => ({ currentWorkspaceId: mock.workspace, isReady: true }),
}));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ currentOrganizationId: "org" }),
}));
vi.mock("src/sections/dashboards/hooks/useCanEditDashboard", () => ({
  default: () => ({ isReadOnly: false }),
}));
vi.mock("src/api/project/observe-project-list", () => ({
  readObserveProjectPage: async () => ({
    rows: [{ id: "project", name: "Support" }],
    totalPages: 1,
  }),
}));
const A = {
  id: wire.run.metric_id,
  name: "Average latency",
  description: "",
  project_id: "project",
  current_version: 2,
  definition: { ...initialForm(), version: 2 },
};
const B = {
  ...A,
  id: "00000000-0000-0000-0000-000000000099",
  name: "Total cost",
};
const evalA = {
  id: "44444444-4444-4444-8444-444444444444",
  name: "Answer quality",
  config_hash: "a".repeat(64),
};
const evalB = {
  id: "55555555-5555-4555-8555-555555555555",
  name: "Safety score",
  config_hash: "b".repeat(64),
};
let client;
let mounted;
let metrics;
let savedRuns;
const choose = async (label, option) => {
  fireEvent.mouseDown(
    screen.getByLabelText(label, { selector: '[role="combobox"]' }),
  );
  fireEvent.click(await screen.findByRole("option", { name: option }));
  await waitFor(() =>
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument(),
  );
};
const click = (name) => {
  if (
    !screen.queryByRole("button", { name }) &&
    ["New metric", "Total cost"].includes(name)
  )
    fireEvent.click(screen.getByRole("button", { name: "Metrics" }));
  fireEvent.click(screen.getByRole("button", { name }));
};
// Recovery is still required after a forced page close/crash, even though
// ordinary in-app navigation is now blocked. This explicitly unmounts the router
// instead of pretending the new guard should be bypassed by a sidebar click.
async function hardExit(exit = "Tasks sidebar") {
  const destination =
    exit === "Back to Evals" ? "/dashboard/evaluations" : "/dashboard/tasks";
  mounted.unmount();
  client.clear();
  window.history.pushState({ idx: 0 }, "", destination);
  mount();
}
const change = (label, value) =>
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
function mount() {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  mounted = render(
    <QueryClientProvider client={client}>
      <nav>
        <Link to="/dashboard/tasks">Tasks sidebar</Link>
        <Link to="/dashboard/evaluations">Back to Evals</Link>
      </nav>
      <Routes>
        <Route
          path="/dashboard/evaluations/metrics/*"
          element={<DerivedMetricsView />}
        />
        <Route
          path="*"
          element={
            <>
              <h1>Other route</h1>
              <Link to="/dashboard/evaluations/metrics">Return to Metrics</Link>
            </>
          }
        />
      </Routes>
    </QueryClientProvider>,
  );
  return mounted;
}
async function selectA() {
  if (screen.queryByRole("button", { name: "Metrics" })) click("Metrics");
  fireEvent.click(
    await screen.findByRole("button", { name: "Average latency" }),
  );
  await screen.findByRole("heading", { name: A.name });
}
async function editA() {
  await selectA();
  click("Edit definition");
}
async function saveEdited() {
  click("Continue");
  click("Continue");
  click("Save new version");
  await screen.findByText(/Saved version 3. Saving does not run/);
}
async function previewA() {
  await selectA();
  click("Run metric");
  click("Preview window");
  await screen.findByText(/Preview only/);
}
beforeEach(() => {
  localStorage.clear();
  mock.workspace = "ws";
  metrics = [structuredClone(A), structuredClone(B)];
  savedRuns = [];
  window.history.replaceState({}, "", "/dashboard/evaluations/metrics");
  mock.get.mockReset();
  mock.post.mockReset();
  mock.put.mockReset();
  mock.get.mockImplementation(async (url) => {
    if (url === "/metrics")
      return { data: { results: metrics, next_cursor: null } };
    if (url === "/evaluators")
      return { data: { results: [], truncated: true } };
    for (const item of [evalA, evalB])
      if (url === `/evaluators/${item.id}`)
        return { data: { id: item.id, name: item.name, project: "project" } };
    for (const metric of metrics) {
      if (url === `/metrics/${metric.id}`) return { data: metric };
      if (url === `/metrics/${metric.id}/runs`)
        return {
          data: {
            results: metric.id === A.id ? savedRuns : [],
            next_cursor: null,
          },
        };
    }
    if (url === `/runs/${wire.run.id}`) return { data: wire.run };
    if (url === `/runs/${wire.run.id}/results`) return { data: wire.old_page };
    if (url === `/runs/${wire.run.id}/contributors`)
      return {
        data: {
          result_id: wire.old_page.result_id,
          rollup: "overall",
          group_key: "",
          diagnostics: {
            valid_count: 1,
            missing_count: 0,
            invalid_probability_count: 0,
            undefined_arithmetic_count: 0,
            missing_by_input: {},
          },
          results: [
            {
              unit_id: "session-explains-result",
              session_id: "session-explains-result",
              weight: 1,
              numerator: 1,
              denominator: 2,
              influence: 0.2,
            },
          ],
          truncated: false,
        },
      };
    throw new Error(`Unexpected offline read ${url}`);
  });
  mock.post.mockImplementation(async (url) => {
    if (url.endsWith("/preview")) return { data: wire.old_page };
    if (url === "/validate") return { data: { semantics: "Valid" } };
    if (url.endsWith("/runs")) return { data: { id: wire.run.id } };
    throw new Error(`Unexpected offline write ${url}`);
  });
  mock.put.mockImplementation(async (_, body) => {
    metrics[0] = {
      ...metrics[0],
      name: body.name,
      current_version: 3,
      definition: { ...body, version: 3 },
    };
    return { data: metrics[0] };
  });
});
afterEach(() => {
  cleanup();
  client?.clear();
  localStorage.clear();
});

it("keeps a recovered draft locked after a save response is lost across a route exit", async () => {
  let rejectWrite;
  mock.post.mockImplementation(async (url) => {
    if (url === "/validate") return { data: { semantics: "Valid" } };
    if (url === "/metrics")
      return new Promise((_, reject) => {
        rejectWrite = reject;
      });
    throw new Error(`Unexpected write ${url}`);
  });
  mount();
  click("New metric");
  change("Metric name", "Pending new metric");
  await choose("Project", "Support");
  click("Continue");
  click("Continue");
  click("Save metric");
  await waitFor(() => expect(rejectWrite).toBeTypeOf("function"));
  await hardExit();
  await screen.findByRole("heading", { name: "Other route" });
  await act(async () => rejectWrite(new Error("Lost response")));
  fireEvent.click(screen.getByRole("link", { name: "Return to Metrics" }));
  const prompt = await screen.findByRole("dialog", {
    name: "Restore unsaved metric?",
  });
  fireEvent.click(
    within(prompt).getByRole("button", { name: "Restore draft" }),
  );
  await waitFor(() =>
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
  );
  expect(
    screen.getByText(/Could not confirm the previous save/),
  ).toBeInTheDocument();
  click("Continue");
  click("Continue");
  expect(screen.getByRole("button", { name: "Save metric" })).toBeDisabled();
  expect(
    mock.post.mock.calls.filter(([url]) => url === "/metrics"),
  ).toHaveLength(1);
});

it("does not start a definition write after exiting during validation", async () => {
  let finishValidation;
  mock.post.mockImplementation(
    () =>
      new Promise((resolve) => {
        finishValidation = resolve;
      }),
  );
  mount();
  await editA();
  change("Metric name", "Validation in flight");
  click("Continue");
  click("Continue");
  click("Save new version");
  await waitFor(() => expect(finishValidation).toBeTypeOf("function"));
  await hardExit();
  await screen.findByRole("heading", { name: "Other route" });
  await act(async () => finishValidation({ data: { semantics: "Valid" } }));
  expect(mock.put).not.toHaveBeenCalled();
  expect(
    screen.getByRole("heading", { name: "Other route" }),
  ).toBeInTheDocument();
});

it("a late successful save cannot redirect or erase a newer scoped draft", async () => {
  let finishWrite;
  mock.post.mockImplementation(async (url) => {
    if (url === "/validate") return { data: { semantics: "Valid" } };
    if (url === "/metrics")
      return new Promise((resolve) => {
        finishWrite = resolve;
      });
    throw new Error(`Unexpected write ${url}`);
  });
  mount();
  click("New metric");
  change("Metric name", "Old in-flight draft");
  await choose("Project", "Support");
  click("Continue");
  click("Continue");
  click("Save metric");
  await waitFor(() => expect(finishWrite).toBeTypeOf("function"));
  await hardExit();
  await screen.findByRole("heading", { name: "Other route" });
  fireEvent.click(screen.getByRole("link", { name: "Return to Metrics" }));
  const prompt = await screen.findByRole("dialog", {
    name: "Restore unsaved metric?",
  });
  fireEvent.click(
    within(prompt).getByRole("button", { name: "Discard draft" }),
  );
  await waitFor(() =>
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
  );
  click("New metric");
  change("Metric name", "Fresh replacement draft");
  await hardExit();
  await screen.findByRole("heading", { name: "Other route" });
  await act(async () => finishWrite({ data: { ...A, current_version: 1 } }));
  expect(
    screen.getByRole("heading", { name: "Other route" }),
  ).toBeInTheDocument();
  fireEvent.click(screen.getByRole("link", { name: "Return to Metrics" }));
  expect(
    await screen.findByRole("dialog", { name: "Restore unsaved metric?" }),
  ).toHaveTextContent("Fresh replacement draft");
});

describe("preview belongs only to its submitted metric/version/window", () => {
  it("does not show A's preview under B when both metrics have no runs", async () => {
    mount();
    await previewA();
    click("Cancel");
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    click("Total cost");
    await screen.findByRole("heading", { name: B.name });
    click("Run metric");
    expect(screen.queryByText(/Preview only/)).not.toBeInTheDocument();
    expect(
      mock.post.mock.calls.filter(([url]) => url.endsWith("/preview")),
    ).toHaveLength(1);
  });
  it("clears an old-version preview after saving a new version without changing the selected run", async () => {
    savedRuns = [wire.run];
    mount();
    await previewA();
    click("Cancel");
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    expect(window.location.search).toContain(`run=${wire.run.id}`);
    click("Edit definition");
    change("Metric name", "Updated latency");
    await saveEdited();
    expect(window.location.search).toContain(`run=${wire.run.id}`);
    click("Run metric");
    expect(screen.queryByText(/Preview only/)).not.toBeInTheDocument();
    expect(screen.getByRole("dialog")).toHaveTextContent(
      /definition version 3/,
    );
  });
  it("rejects A's in-flight preview after browser Back selects B", async () => {
    let resolve;
    mock.post.mockImplementation(
      () =>
        new Promise((done) => {
          resolve = done;
        }),
    );
    window.history.replaceState(
      {},
      "",
      `/dashboard/evaluations/metrics?metric=${B.id}&run=`,
    );
    window.history.pushState(
      {},
      "",
      `/dashboard/evaluations/metrics?metric=${A.id}&run=`,
    );
    mount();
    fireEvent.click(await screen.findByRole("button", { name: "Run metric" }));
    click("Preview window");
    await waitFor(() => expect(resolve).toBeTypeOf("function"));
    await act(async () => window.history.back());
    await screen.findByRole("heading", { name: B.name });
    await act(async () => resolve({ data: wire.old_page }));
    click("Run metric");
    expect(screen.queryByText(/Preview only/)).not.toBeInTheDocument();
  });
  it("rejects a delayed preview after the submitted configuration is superseded, even if restored", async () => {
    let resolve;
    mock.post.mockImplementation(
      () =>
        new Promise((done) => {
          resolve = done;
        }),
    );
    mount();
    await selectA();
    click("Run metric");
    const originalEnd = screen.getByLabelText("Window end").value;
    click("Preview window");
    await waitFor(() => expect(resolve).toBeTypeOf("function"));
    change("Window end", "2026-01-01T12:00");
    change("Window end", originalEnd);
    await act(async () => resolve({ data: wire.old_page }));
    expect(screen.queryByText(/Preview only/)).not.toBeInTheDocument();
  });
});

describe("draft recovery after actual route unmount", () => {
  it.each(["Tasks sidebar", "Back to Evals", "browser Back"])(
    "restores name and formula after forced page close from %s",
    async (exit) => {
      window.history.replaceState({}, "", "/dashboard/evaluations");
      window.history.pushState(
        {},
        "",
        `/dashboard/evaluations/metrics?metric=${A.id}&run=`,
      );
      mount();
      fireEvent.click(
        await screen.findByRole("button", { name: "Edit definition" }),
      );
      change("Metric name", "Unsaved question");
      click("Continue");
      await choose("Calculation", "Custom formula");
      change("Formula", "sum(value) + 7");
      await hardExit(exit);
      await screen.findByRole("heading", { name: "Other route" });
      expect(screen.queryByLabelText("Metric name")).not.toBeInTheDocument();
      fireEvent.click(screen.getByRole("link", { name: "Return to Metrics" }));
      const prompt = await screen.findByRole("dialog", {
        name: "Restore unsaved metric?",
      });
      expect(prompt).toHaveTextContent("Unsaved question");
      fireEvent.click(
        within(prompt).getByRole("button", { name: "Restore draft" }),
      );
      await waitFor(() =>
        expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
      );
      expect(await screen.findByLabelText("Metric name")).toHaveValue(
        "Unsaved question",
      );
      click("Continue");
      expect(screen.getByLabelText("Formula")).toHaveValue("sum(value) + 7");
      expect(mock.put).not.toHaveBeenCalled();
      expect(mock.post).not.toHaveBeenCalled();
    },
  );
  it("lets the user discard recovery explicitly and never recovers a different workspace's draft", async () => {
    const view = mount();
    await editA();
    change("Metric name", "Scoped draft");
    await hardExit();
    await screen.findByRole("heading", { name: "Other route" });
    mock.workspace = "other-workspace";
    fireEvent.click(screen.getByRole("link", { name: "Return to Metrics" }));
    await screen.findByRole("button", { name: "Average latency" });
    expect(
      screen.queryByRole("dialog", { name: "Restore unsaved metric?" }),
    ).not.toBeInTheDocument();
    view.unmount();
    client.clear();
    mock.workspace = "ws";
    mount();
    const prompt = await screen.findByRole("dialog", {
      name: "Restore unsaved metric?",
    });
    fireEvent.click(
      within(prompt).getByRole("button", { name: "Discard draft" }),
    );
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    await hardExit();
    await screen.findByRole("heading", { name: "Other route" });
    fireEvent.click(screen.getByRole("link", { name: "Return to Metrics" }));
    expect(
      screen.queryByRole("dialog", { name: "Restore unsaved metric?" }),
    ).not.toBeInTheDocument();
  });
});

it.each(["Average", "Total"])(
  "Ratio -> %s removes only its untouched auto-added denominator",
  async (calculation) => {
    mount();
    click("New metric");
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

it("preserves a filled denominator when switching to Average", async () => {
  mount();
  await editA();
  click("Continue");
  await choose("Calculation", "Ratio");
  await choose("Span value · input 2", "cost");
  await choose("Calculation", "Average");
  expect(screen.getByRole("button", { name: "Continue" })).toBeEnabled();
  click("Continue");
  click("Save new version");
  await waitFor(() => expect(mock.put).toHaveBeenCalledOnce());
  expect(mock.put.mock.calls[0][1]).toMatchObject({
    formula: "mean(value)",
    inputs: [
      expect.objectContaining({ field: "latency_ms" }),
      expect.objectContaining({ field: "cost" }),
    ],
  });
});

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
  mount();
  await editA();
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

it("new drafts survive forced unmount and explicit breadcrumb discard clears recovery", async () => {
  mount();
  click("New metric");
  change("Metric name", "New draft question");
  await hardExit();
  await screen.findByRole("heading", { name: "Other route" });
  fireEvent.click(screen.getByRole("link", { name: "Return to Metrics" }));
  const prompt = await screen.findByRole("dialog", {
    name: "Restore unsaved metric?",
  });
  fireEvent.click(
    within(prompt).getByRole("button", { name: "Restore draft" }),
  );
  await waitFor(() =>
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
  );
  expect(screen.getByLabelText("Metric name")).toHaveValue(
    "New draft question",
  );
  click("Metrics");
  click("Keep editing");
  await waitFor(() =>
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
  );
  expect(screen.getByLabelText("Metric name")).toHaveValue(
    "New draft question",
  );
  click("Metrics");
  click("Discard changes");
  await screen.findByRole("button", { name: "New metric" });
  await screen.findByRole("button", { name: "New metric" });
  expect(
    screen.queryByRole("dialog", { name: "Restore unsaved metric?" }),
  ).not.toBeInTheDocument();
  expect(mock.post).not.toHaveBeenCalled();
});

it("recovers an unfinished filter edit instead of silently restoring the last valid array", async () => {
  mount();
  await editA();
  click("Advanced configuration");
  change("Definition filters (JSON)", '[{"field":');
  await hardExit();
  await screen.findByRole("heading", { name: "Other route" });
  fireEvent.click(screen.getByRole("link", { name: "Return to Metrics" }));
  const prompt = await screen.findByRole("dialog", {
    name: "Restore unsaved metric?",
  });
  fireEvent.click(
    within(prompt).getByRole("button", { name: "Restore draft" }),
  );
  await waitFor(() =>
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
  );
  expect(
    screen.getByText(/Definition filters need a valid JSON array/),
  ).toBeInTheDocument();
  click("Advanced configuration");
  expect(screen.getByLabelText("Definition filters (JSON)")).toHaveValue(
    '[{"field":',
  );
  click("Continue");
  click("Continue");
  expect(
    screen.getByRole("button", { name: "Save new version" }),
  ).toBeDisabled();
  expect(mock.post).not.toHaveBeenCalled();
});

it("restored editing preserves the original optimistic version when the saved definition advanced", async () => {
  mount();
  await editA();
  change("Metric name", "Older draft");
  await hardExit();
  await screen.findByRole("heading", { name: "Other route" });
  metrics[0] = {
    ...metrics[0],
    current_version: 3,
    definition: { ...metrics[0].definition, version: 3 },
  };
  client.removeQueries({ queryKey: ["derived-metrics"] });
  fireEvent.click(screen.getByRole("link", { name: "Return to Metrics" }));
  const prompt = await screen.findByRole("dialog", {
    name: "Restore unsaved metric?",
  });
  fireEvent.click(
    within(prompt).getByRole("button", { name: "Restore draft" }),
  );
  await waitFor(() =>
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
  );
  expect(
    screen.getByText(/Saved version 2.*Unsaved changes/),
  ).toBeInTheDocument();
  click("Continue");
  click("Continue");
  click("Save new version");
  await waitFor(() => expect(mock.put).toHaveBeenCalledOnce());
  expect(mock.put.mock.calls[0][1]).toMatchObject({
    expected_version: 2,
    name: "Older draft",
  });
});

it("Review names each off-first-page evaluator without changing exact bindings", async () => {
  metrics[0].definition = {
    ...A.definition,
    formula: "ratio(quality, safety)",
    inputs: [evalA, evalB].map((item, i) => ({
      alias: i ? "safety" : "quality",
      source: "eval",
      config_id: item.id,
      config_hash: item.config_hash,
      reducer: "latest",
    })),
  };
  mount();
  await editA();
  expect(await screen.findByDisplayValue(evalA.name)).toBeInTheDocument();
  expect(await screen.findByDisplayValue(evalB.name)).toBeInTheDocument();
  for (const evaluator of [evalA, evalB])
    expect(mock.get).toHaveBeenCalledWith(`/evaluators/${evaluator.id}`, {
      signal: expect.any(AbortSignal),
    }); // Existing detail contract: no undocumented query parameters.
  click("Continue");
  click("Continue");
  click("Advanced configuration");
  expect(screen.getByText(/2 inputs ·/)).toHaveTextContent(
    /Answer quality.*Safety score/,
  );
  click("Save new version");
  await waitFor(() => expect(mock.put).toHaveBeenCalledOnce());
  expect(mock.put.mock.calls[0][1].inputs).toEqual(
    metrics[0].definition.inputs,
  );
});

it("does not display a name from a mismatched evaluator detail response", async () => {
  metrics[0].definition.inputs = [
    {
      alias: "quality",
      source: "eval",
      config_id: evalA.id,
      config_hash: evalA.config_hash,
      reducer: "latest",
    },
  ];
  metrics[0].definition.formula = "mean(quality)";
  const original = mock.get.getMockImplementation();
  mock.get.mockImplementation((url) =>
    url === `/evaluators/${evalA.id}`
      ? Promise.resolve({
          data: {
            id: evalB.id,
            project: "another-project",
            name: "Wrong scope name",
          },
        })
      : original(url),
  );
  mount();
  await editA();
  await waitFor(() =>
    expect(screen.getByLabelText("Input 1 evaluator")).toHaveValue(
      "Saved evaluator (name unavailable; binding preserved)",
    ),
  );
  expect(screen.queryByText("Wrong scope name")).not.toBeInTheDocument();
  click("Continue");
  click("Continue");
  click("Save new version");
  await waitFor(() => expect(mock.put).toHaveBeenCalledOnce());
  expect(mock.put.mock.calls[0][1].inputs[0]).toMatchObject({
    config_id: evalA.id,
    config_hash: evalA.config_hash,
  });
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
  savedRuns = [saved];
  const original = mock.get.getMockImplementation();
  mock.get.mockImplementation((url) =>
    url === `/runs/${wire.run.id}`
      ? Promise.resolve({ data: saved })
      : original(url),
  );
  mount();
  await selectA();
  const region = await screen.findByRole("region", {
    name: "Selected saved run",
  });
  fireEvent.click(within(region).getByText("Details"));
  expect(
    await within(region).findByText(new RegExp(`quality:.*${evalA.name}`)),
  ).toBeInTheDocument();
});

it("Run names the metric and current definition, explicitly distinguishing an inspected older run", async () => {
  savedRuns = [wire.run];
  mount();
  await selectA();
  await screen.findByRole("region", { name: "Selected saved run" });
  click("Run metric");
  const dialog = screen.getByRole("dialog", { name: "Run metric" });
  expect(dialog).toHaveTextContent(/Average latency.*definition version 2/);
  expect(dialog).toHaveTextContent(/inspecting.*version 1.*not.*replay/i);
  click("Run and persist");
  await waitFor(() =>
    expect(mock.post).toHaveBeenCalledWith(
      `/metrics/${A.id}/runs`,
      expect.objectContaining({ version: 2 }),
    ),
  );
});

it("opens the explaining rows in one direct contributors action", async () => {
  savedRuns = [wire.run];
  mount();
  await selectA();
  await screen.findByRole("heading", { name: "0.50" });
  expect(
    mock.get.mock.calls.some(([url]) => url.endsWith("/contributors")),
  ).toBe(false);
  // The host tab is the one direct action; there is no duplicate Inspect link.
  fireEvent.click(screen.getByRole("tab", { name: "Contributors" }));
  expect(
    await screen.findByRole("table", { name: "Metric contributors" }),
  ).toHaveTextContent("session-explains-result");
  expect(
    screen.queryByRole("button", { name: "Inspect contributors" }),
  ).not.toBeInTheDocument();
});

it.each([
  [
    {
      type: "validation_error",
      attr: "formula",
      details: "Unknown input: missing_score",
      statusCode: 400,
    },
    /Formula:.*Unknown input: missing_score.*Calculation/,
  ],
  [
    {
      type: "validation_error",
      details: [
        { attr: "filters.0.field", detail: "Unsupported filter field" },
      ],
      statusCode: 422,
    },
    /Definition filters:.*Unsupported filter field.*Advanced configuration/,
  ],
])(
  "gives field-aware recovery for normalized validation errors %j",
  async (failure, expected) => {
    mock.post.mockRejectedValue(failure);
    mount();
    await editA();
    click("Continue");
    click("Continue");
    click("Validate formula");
    expect(await screen.findByText(expected)).toBeInTheDocument();
    expect(mock.put).not.toHaveBeenCalled();
  },
);

describe("r4 narrow recovery repairs", () => {
  it("keeps restored-and-edited recovery after the original pending POST succeeds", async () => {
    let finishWrite;
    mock.post.mockImplementation(async (url) => {
      if (url === "/validate") return { data: { semantics: "Valid" } };
      if (url === "/metrics")
        return new Promise((resolve) => {
          finishWrite = resolve;
        });
      throw new Error(`Unexpected write ${url}`);
    });
    mount();
    click("New metric");
    change("Metric name", "Submitted before leaving");
    await choose("Project", "Support");
    click("Continue");
    click("Continue");
    click("Save metric");
    await waitFor(() => expect(finishWrite).toBeTypeOf("function"));
    await hardExit();
    await screen.findByRole("heading", { name: "Other route" });
    fireEvent.click(screen.getByRole("link", { name: "Return to Metrics" }));
    const prompt = await screen.findByRole("dialog", {
      name: "Restore unsaved metric?",
    });
    fireEvent.click(
      within(prompt).getByRole("button", { name: "Restore draft" }),
    );
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    change("Metric name", "Newer restored question");
    click("Continue");
    await choose("Calculation", "Custom formula");
    change("Formula", "sum(value) + 11");
    click("Continue");
    expect(screen.getByRole("button", { name: "Save metric" })).toBeDisabled();
    await waitFor(() =>
      expect(readMetricDraft("org:ws")?.form.formula).toBe("sum(value) + 11"),
    );
    await act(async () =>
      finishWrite({
        data: { ...A, name: "Submitted before leaving", current_version: 1 },
      }),
    );
    expect(screen.getByRole("button", { name: "Save metric" })).toBeDisabled();
    // No edit after completion: the late response must not erase the newer copy.
    await hardExit();
    await screen.findByRole("heading", { name: "Other route" });
    fireEvent.click(screen.getByRole("link", { name: "Return to Metrics" }));
    const recovered = await screen.findByRole("dialog", {
      name: "Restore unsaved metric?",
    });
    expect(recovered).toHaveTextContent("Newer restored question");
    fireEvent.click(
      within(recovered).getByRole("button", { name: "Restore draft" }),
    );
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    expect(screen.getByLabelText("Metric name")).toHaveValue(
      "Newer restored question",
    );
    expect(
      screen.getByText(/Could not confirm the previous save/),
    ).toBeInTheDocument();
    click("Continue");
    expect(screen.getByLabelText("Formula")).toHaveValue("sum(value) + 11");
    click("Continue");
    expect(screen.getByRole("button", { name: "Save metric" })).toBeDisabled();
    expect(
      mock.post.mock.calls.filter(([url]) => url === "/metrics"),
    ).toHaveLength(1);
    expect(mock.put).not.toHaveBeenCalled();
  });

  it("recovers a new draft started on the trailing-slash Metrics route", async () => {
    window.history.replaceState(
      {},
      "",
      "/dashboard/evaluations/metrics/?from=evals#draft",
    );
    mount();
    click("New metric");
    change("Metric name", "Trailing slash question");
    await choose("Project", "Support");
    click("Continue");
    await choose("Calculation", "Custom formula");
    change("Formula", "sum(value) + 13");
    await hardExit();
    await screen.findByRole("heading", { name: "Other route" });
    fireEvent.click(screen.getByRole("link", { name: "Return to Metrics" }));
    const prompt = await screen.findByRole("dialog", {
      name: "Restore unsaved metric?",
    });
    expect(prompt).toHaveTextContent("Trailing slash question");
    fireEvent.click(
      within(prompt).getByRole("button", { name: "Restore draft" }),
    );
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    expect(screen.getByLabelText("Metric name")).toHaveValue(
      "Trailing slash question",
    );
    click("Continue");
    expect(screen.getByLabelText("Formula")).toHaveValue("sum(value) + 13");
    expect(window.location.pathname).toBe("/dashboard/evaluations/metrics");
    expect(window.location.search).toBe("?from=evals");
    expect(window.location.hash).toBe("#draft");
    expect(readMetricDraft("org:ws")?.location.pathname).toBe(
      "/dashboard/evaluations/metrics",
    );
    expect(mock.post).not.toHaveBeenCalled();
    expect(mock.put).not.toHaveBeenCalled();
  });

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

  it.each(["normalized", "Axios"])(
    "gives each root-keyed validation field its own recovery advice (%s)",
    async (envelope) => {
      const body = {
        type: "validation_error",
        attr: "formula",
        details: {
          formula: ["Ensure this field has no more than 2048 characters."],
          filters: [{ field: ["Unsupported filter field."] }],
        },
      };
      mock.post.mockRejectedValue(
        envelope === "Axios"
          ? { response: { status: 400, data: body } }
          : { ...body, statusCode: 400 },
      );
      mount();
      await editA();
      click("Continue");
      click("Continue");
      click("Validate formula");
      expect(
        await screen.findByText(/Ensure this field has no more than 2048/),
      ).toHaveTextContent(
        "Formula: Ensure this field has no more than 2048 characters. Open Calculation and correct the custom formula. Definition filters: Unsupported filter field. Open Advanced configuration and correct the definition filters.",
      );
      expect(mock.put).not.toHaveBeenCalled();
    },
  );

  it.each(["Escape", "Not now"])(
    "dismisses recovery with %s, returns focus and keeps the draft recoverable",
    async (dismiss) => {
      mount();
      await editA();
      change("Metric name", "Return to this draft later");
      await hardExit();
      await screen.findByRole("heading", { name: "Other route" });
      fireEvent.click(screen.getByRole("link", { name: "Return to Metrics" }));
      const prompt = await screen.findByRole("dialog", {
        name: "Restore unsaved metric?",
      });
      const stored = localStorage.getItem("metric-definition-draft:v1:org:ws");
      expect(stored).toContain("Return to this draft later");
      if (dismiss === "Escape")
        fireEvent.keyDown(prompt, { key: "Escape", code: "Escape" });
      else
        fireEvent.click(
          within(prompt).getByRole("button", { name: "Not now" }),
        );
      await waitFor(() =>
        expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
      );
      await waitFor(() =>
        expect(
          screen.getByRole("button", { name: "New metric" }),
        ).toHaveFocus(),
      );
      expect(localStorage.getItem("metric-definition-draft:v1:org:ws")).toBe(
        stored,
      );
      await selectA();
      expect(screen.getByRole("heading", { name: A.name })).toBeInTheDocument();
      expect(screen.queryByLabelText("Metric name")).not.toBeInTheDocument();
      await hardExit();
      await screen.findByRole("heading", { name: "Other route" });
      fireEvent.click(screen.getByRole("link", { name: "Return to Metrics" }));
      expect(
        await screen.findByRole("dialog", { name: "Restore unsaved metric?" }),
      ).toHaveTextContent("Return to this draft later");
      expect(mock.post).not.toHaveBeenCalled();
      expect(mock.put).not.toHaveBeenCalled();
    },
  );
});
