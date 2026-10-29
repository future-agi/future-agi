import { afterEach, beforeEach, expect, it, vi } from "vitest";
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
import { createTheme } from "@mui/material/styles";
import { palette } from "src/theme/palette";
import DerivedMetricsView from "../DerivedMetricsView";
import LegacyMetricsRedirect from "src/routes/components/legacy_metrics_redirect";
import { initialForm } from "../metricForm";
import { readMetricDraft, writeMetricDraft } from "../metric_draft";
import { metricDashboardDescription } from "../metric_dashboard_description";
import wire from "./fixtures/committedResults.json";

const mock = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  readOnly: false,
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
      contributors: (id) => `/runs/${id}/contributors`,
      evaluators: "/evaluators",
    },
    project: { updateEvalTaskConfig: (id) => `/evaluators/${id}` },
  },
}));
vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => ({ currentWorkspaceId: "ws", isReady: true }),
}));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ currentOrganizationId: "org" }),
}));
vi.mock("src/sections/dashboards/hooks/useCanEditDashboard", () => ({
  default: () => ({ isReadOnly: mock.readOnly }),
}));
vi.mock("src/api/project/observe-project-list", () => ({
  readObserveProjectPage: async () => ({
    rows: [{ id: "project", name: "Support" }],
    totalPages: 1,
  }),
}));
const metric = {
  id: wire.run.metric_id,
  name: "Average latency for all support conversations with a deliberately long saved name",
  project_id: "project",
  current_version: 2,
  definition: { ...initialForm(), version: 2 },
};
let client;
let metrics;
let runs;
let resultPage;
function mount(mode = "light") {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <nav>
        <Link to="/dashboard/tasks">Tasks sidebar</Link>
      </nav>
      <Routes>
        <Route
          path="/dashboard/evaluations/metrics/*"
          element={<DerivedMetricsView />}
        />
        <Route
          path="/dashboard/derived-metrics"
          element={<LegacyMetricsRedirect />}
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
    { theme: createTheme({ palette: palette(mode) }) },
  );
}
const click = (name) => fireEvent.click(screen.getByRole("button", { name }));
const change = (label, value) =>
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
async function choose(label, option) {
  fireEvent.mouseDown(
    screen.getByLabelText(label, { selector: '[role="combobox"]' }),
  );
  fireEvent.click(await screen.findByRole("option", { name: option }));
}
async function select() {
  fireEvent.click(await screen.findByRole("button", { name: metric.name }));
  await screen.findByRole("heading", { name: metric.name });
}
async function edit() {
  await select();
  click("Edit definition");
}
beforeEach(() => {
  localStorage.clear();
  mock.readOnly = false;
  window.history.replaceState({ idx: 0 }, "", "/dashboard/evaluations/metrics");
  metrics = [structuredClone(metric)];
  runs = [structuredClone(wire.run)];
  resultPage = structuredClone(wire.old_page);
  mock.get.mockReset();
  mock.post.mockReset();
  mock.put.mockReset();
  mock.get.mockImplementation(async (url) => {
    if (url === "/metrics")
      return { data: { results: metrics, next_cursor: null } };
    if (url === `/metrics/${metric.id}`) return { data: metrics[0] };
    if (url === `/metrics/${metric.id}/runs`)
      return { data: { results: runs, next_cursor: null } };
    if (url === `/runs/${wire.run.id}`) return { data: runs[0] };
    if (url === `/runs/${wire.run.id}/results`) return { data: resultPage };
    if (url === "/evaluators")
      return { data: { results: [], truncated: false } };
    if (url.endsWith("/contributors"))
      return {
        data: {
          result_id: resultPage.result_id,
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
              unit_id: "proof-session",
              session_id: "proof-session",
              weight: 1,
              numerator: 1,
              denominator: 2,
            },
          ],
          truncated: false,
        },
      };
    throw new Error(`Unexpected read ${url}`);
  });
  mock.post.mockImplementation(async (url, body) => {
    if (url === "/validate") return { data: { semantics: "Valid" } };
    if (url === "/metrics") {
      metrics = [
        {
          ...metric,
          name: body.name,
          current_version: 1,
          definition: { ...body, version: 1 },
        },
      ];
      return { data: metrics[0] };
    }
    throw new Error(`Unexpected write ${url}`);
  });
  mock.put.mockImplementation(async (_, body) => {
    metrics = [
      {
        ...metric,
        name: body.name,
        current_version: 3,
        definition: { ...body, version: 3 },
      },
    ];
    return { data: metrics[0] };
  });
});
afterEach(() => {
  cleanup();
  client?.clear();
  localStorage.clear();
});

it("uses the native table then canonical detail, preserving run links and immutable results", async () => {
  mount();
  for (const name of [
    "Name",
    "Latest value",
    "Last run / status",
    "Version",
    "Project",
  ])
    expect(
      await screen.findByRole("columnheader", { name }),
    ).toBeInTheDocument();
  expect(await screen.findByText("0.5")).toBeInTheDocument();
  expect(await screen.findByText("Support")).toBeInTheDocument();
  await select();
  await screen.findByRole("heading", { name: "0.50" });
  expect(window.location.pathname).toBe(
    `/dashboard/evaluations/metrics/${metric.id}`,
  );
  expect(window.location.search).toContain(`run=${wire.run.id}`);
  expect(screen.queryByRole("grid")).not.toBeInTheDocument();
  for (const name of ["Result", "Past runs", "Contributors", "Definition"])
    expect(screen.getByRole("tab", { name })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("tab", { name: "Definition" }));
  expect(
    screen.getByText("Current definition · version 2"),
  ).toBeInTheDocument();
  fireEvent.click(screen.getByRole("tab", { name: "Result" }));
  expect(
    screen.getByRole("region", { name: "Selected saved run" }),
  ).toHaveTextContent("version 1");
});

it.each([0, null])(
  "never coerces latest %s to a different value",
  async (value) => {
    resultPage.results[0].value = value;
    mount();
    expect(
      await screen.findByText(value === null ? "No value" : "0"),
    ).toBeInTheDocument();
  },
);

it("does not label the data window as the execution time when run time is unavailable", async () => {
  delete runs[0].created_at;
  delete runs[0].as_of;
  mount();
  expect(
    await screen.findByText("Time unavailable · complete"),
  ).toBeInTheDocument();
});

it("does not show an unverified latest result, and offers retry", async () => {
  resultPage.result_id = "00000000-0000-0000-0000-000000000099";
  mount();
  expect(await screen.findByText("Unavailable")).toBeInTheDocument();
  expect(screen.queryByText("0.5")).not.toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: "Retry latest run" }),
  ).toBeInTheDocument();
});

it("preserves canonical reload, legacy query redirect, exact run and unrelated parameters", async () => {
  window.history.replaceState(
    { idx: 0 },
    "",
    `/dashboard/derived-metrics?metric=${metric.id}&run=${wire.run.id}&project=kept`,
  );
  const view = mount();
  await screen.findByRole("heading", { name: "0.50" });
  expect(window.location.search).toContain("project=kept");
  view.unmount();
  client.clear();
  window.history.replaceState(
    { idx: 0 },
    "",
    `/dashboard/evaluations/metrics/${metric.id}?run=${wire.run.id}&project=kept`,
  );
  mount();
  expect(
    await screen.findByRole("heading", { name: "0.50" }),
  ).toBeInTheDocument();
  expect(window.location.search).toBe(`?run=${wire.run.id}&project=kept`);
});

it.each(["light", "dark"])(
  "guided create round-trips supported data/calculation without typing formulas or IDs (%s)",
  async (mode) => {
    runs = [];
    mount(mode);
    click("New metric");
    change("Metric name", "Mean support latency");
    await choose("Project", "Support");
    expect(screen.queryByLabelText("Formula")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Input 1 alias")).not.toBeInTheDocument();
    click("Continue");
    expect(screen.getByLabelText("Calculation")).toHaveTextContent("Average");
    click("Continue");
    click("Save metric");
    await screen.findByText(/Saving does not run the metric/);
    expect(mock.post).toHaveBeenCalledWith(
      "/metrics",
      expect.objectContaining({
        name: "Mean support latency",
        project_id: "project",
        formula: "mean(value)",
        inputs: [
          expect.objectContaining({ field: "latency_ms", reducer: "latest" }),
        ],
      }),
    );
    expect(window.location.pathname).toBe(
      `/dashboard/evaluations/metrics/${metric.id}`,
    );
    expect(
      mock.post.mock.calls.filter(([url]) => url.endsWith("/runs")),
    ).toHaveLength(0);
  },
);

it("guards sidebar navigation locally; Keep editing preserves, explicit discard leaves and clears", async () => {
  mount();
  await edit();
  change("Metric name", "Unsaved question");
  fireEvent.click(screen.getByRole("link", { name: "Tasks sidebar" }));
  const prompt = await screen.findByRole("dialog", {
    name: "Discard unsaved changes?",
  });
  expect(window.location.pathname).toContain(metric.id);
  fireEvent.click(within(prompt).getByRole("button", { name: "Keep editing" }));
  await waitFor(() =>
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
  );
  expect(screen.getByLabelText("Metric name")).toHaveValue("Unsaved question");
  fireEvent.click(screen.getByRole("link", { name: "Tasks sidebar" }));
  click("Discard changes");
  await screen.findByRole("heading", { name: "Other route" });
  expect(readMetricDraft("org:ws")).toBeNull();
  expect(mock.put).not.toHaveBeenCalled();
});

it("guards real indexed browser Back, then replays only after discard", async () => {
  mount();
  await edit();
  change("Metric name", "Keep this on Back");
  await act(async () => window.history.back());
  await screen.findByRole("dialog", { name: "Discard unsaved changes?" });
  expect(screen.getByLabelText("Metric name")).toHaveValue("Keep this on Back");
  expect(window.location.pathname).toContain(metric.id);
  click("Discard changes");
  await screen.findByRole("button", { name: "New metric" });
  expect(window.location.pathname).toBe("/dashboard/evaluations/metrics");
});

it("deferred recovery cannot be overwritten by starting a new draft", async () => {
  writeMetricDraft("org:ws", {
    mode: "new",
    token: "old",
    form: { ...initialForm(), name: "Deferred question" },
    location: {
      pathname: "/dashboard/evaluations/metrics",
      search: "",
      hash: "",
    },
  });
  mount();
  let prompt = await screen.findByRole("dialog", {
    name: "Restore unsaved metric?",
  });
  fireEvent.click(within(prompt).getByRole("button", { name: "Not now" }));
  await waitFor(() =>
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
  );
  click("New metric");
  prompt = await screen.findByRole("dialog", {
    name: "Restore unsaved metric?",
  });
  expect(prompt).toHaveTextContent("Deferred question");
  expect(readMetricDraft("org:ws").form.name).toBe("Deferred question");
  fireEvent.click(
    within(prompt).getByRole("button", { name: "Discard draft" }),
  );
  await waitFor(() =>
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
  );
  expect(screen.getByLabelText("Metric name")).toHaveValue("");
});

it("saved result and frozen definition are not changed by draft edits or saving a new version", async () => {
  mount();
  await edit();
  change("Metric name", "Version three");
  click("Continue");
  await choose("Calculation", "Total");
  click("Continue");
  click("Save new version");
  await screen.findByText(/Saved version 3/);
  const result = await screen.findByRole("region", {
    name: "Selected saved run",
  });
  expect(result).toHaveTextContent("version 1");
  expect(result).toHaveTextContent(wire.run.definition.formula);
  expect(result).not.toHaveTextContent("sum(value)");
  expect(screen.getByRole("heading", { name: "0.50" })).toBeInTheDocument();
  expect(mock.put).toHaveBeenCalledWith(
    `/metrics/${metric.id}`,
    expect.objectContaining({ expected_version: 2, formula: "sum(value)" }),
  );
});

it("opens verified contributors directly in their host tab", async () => {
  mount();
  await select();
  await screen.findByRole("heading", { name: "0.50" });
  fireEvent.click(screen.getByRole("tab", { name: "Contributors" }));
  expect(screen.getByRole("tab", { name: "Contributors" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  expect(
    await screen.findByRole("table", { name: "Metric contributors" }),
  ).toHaveTextContent("proof-session");
});

it("keeps long names accessible in compact rows and wraps the detail heading, with bounded dark Review code", async () => {
  mount("dark");
  const name = await screen.findByRole("button", { name: metric.name });
  expect(getComputedStyle(name).whiteSpace).toBe("nowrap");
  expect(name).toHaveAttribute("title", metric.name);
  expect(name.firstElementChild).toHaveStyle({ textOverflow: "ellipsis" });
  await select();
  expect(screen.getByRole("heading", { name: metric.name })).toHaveStyle({
    whiteSpace: "normal",
    overflowWrap: "anywhere",
  });
  click("Edit definition");
  click("Continue");
  click("Continue");
  const editor = screen.getByRole("region", {
    name: "Metric definition editor",
  });
  expect(getComputedStyle(editor).minWidth).toBe("0");
  expect(
    screen.getByRole("button", { name: "Save new version" }),
  ).toBeEnabled();
});

it("normalizes only older generated UUID descriptions without mutating ordinary descriptions", () => {
  expect(
    metricDashboardDescription(
      `Frozen metric run ${wire.run.id}. A point represents the complete run window.`,
    ),
  ).toBe("Saved metric result. A point represents the complete run window.");
  expect(metricDashboardDescription(`Incident ${wire.run.id}`)).toBe(
    `Incident ${wire.run.id}`,
  );
});
