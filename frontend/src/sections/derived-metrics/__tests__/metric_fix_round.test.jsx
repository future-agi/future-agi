import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Route, Routes } from "react-router-dom";
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
import MetricList from "../MetricList";
import MetricDefinitionEditor from "../metric_definition_editor";
import SelectedRunSummary from "../SelectedRunSummary";
import { initialForm } from "../metricForm";
import wire from "./fixtures/committedResults.json";

const mock = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  projects: vi.fn(),
}));
vi.mock("src/utils/axios", () => ({
  default: { get: mock.get, post: mock.post },
  endpoints: {
    derivedMetrics: {
      list: "/metrics",
      detail: (id) => `/metrics/${id}`,
      runs: (id) => `/metrics/${id}/runs`,
      run: (id) => `/runs/${id}`,
      results: (id) => `/runs/${id}/results`,
      contributors: (id) => `/runs/${id}/contributors`,
      evaluators: "/evaluators",
    },
  },
}));
vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => ({ currentWorkspaceId: "ws", isReady: true }),
}));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ currentOrganizationId: "org" }),
}));
vi.mock("src/sections/dashboards/hooks/useCanEditDashboard", () => ({
  default: () => ({ isReadOnly: false }),
}));
vi.mock("src/api/project/observe-project-list", () => ({
  readObserveProjectPage: mock.projects,
}));
const metric = {
  id: wire.run.metric_id,
  name: "Resolution ratio",
  project_id: "project",
  current_version: 1,
  definition: wire.run.definition,
};
let client;
let runs;
let readRuns;
const deferred = () => {
  let resolve;
  const promise = new Promise((done) => {
    resolve = done;
  });
  return { promise, resolve };
};
function mount(
  element = (
    <Routes>
      <Route path="*" element={<DerivedMetricsView />} />
    </Routes>
  ),
) {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>{element}</QueryClientProvider>,
  );
}
const click = (name) => fireEvent.click(screen.getByRole("button", { name }));
const tab = (name) => fireEvent.click(screen.getByRole("tab", { name }));
async function choose(label, option) {
  fireEvent.mouseDown(
    screen.getByLabelText(label, { selector: '[role="combobox"]' }),
  );
  fireEvent.click(await screen.findByRole("option", { name: option }));
}
function coldDetail() {
  window.history.replaceState(
    { idx: 0 },
    "",
    `/dashboard/evaluations/metrics/${metric.id}`,
  );
  mount();
}
function listElement(projects) {
  return (
    <MetricList
      definitions={{
        data: {
          results: [
            metric,
            {
              ...metric,
              id: "00000000-0000-0000-0000-000000000002",
              name: "Other metric",
              project_id: "other-project",
            },
          ],
        },
      }}
      scope="org:ws"
      projects={projects}
      onSelect={vi.fn()}
      onBefore={vi.fn()}
    />
  );
}
beforeEach(() => {
  localStorage.clear();
  window.history.replaceState({ idx: 0 }, "", "/dashboard/evaluations/metrics");
  runs = [structuredClone(wire.run)];
  readRuns = null;
  mock.projects.mockReset().mockImplementation(async ({ params }) => ({
    rows: params.page_number === 0 ? [{ id: "project", name: "Support" }] : [],
    totalPages: 1,
  }));
  mock.get.mockReset().mockImplementation(async (url, options) => {
    if (url === "/metrics")
      return { data: { results: [metric], next_cursor: null } };
    if (url === `/metrics/${metric.id}/runs`) {
      if (readRuns) return readRuns();
      return { data: { results: runs, next_cursor: null } };
    }
    if (url.endsWith("/runs") && url.startsWith("/metrics/"))
      return { data: { results: [], next_cursor: null } };
    if (url === `/runs/${wire.run.id}`) return { data: runs[0] };
    if (url === `/runs/${wire.run.id}/results`) {
      const { rollup, after } = options.params;
      return {
        data:
          rollup === "overall"
            ? wire.old_page
            : after
              ? wire.session_next
              : wire.session_page,
      };
    }
    if (url.endsWith("/contributors")) {
      return {
        data: {
          ...wire.contributors,
          rollup: options.params.rollup,
          group_key: options.params.group_key,
        },
      };
    }
    if (url === "/evaluators")
      return { data: { results: [], truncated: false } };
    throw new Error(`Unexpected read ${url}`);
  });
  mock.post.mockReset().mockImplementation(async (url) => {
    if (url === `/metrics/${metric.id}/runs`) {
      runs = [
        { ...wire.run, status: "queued", result_id: null, snapshot_id: null },
      ];
      return { data: runs[0] };
    }
    throw new Error(`Unexpected write ${url}`);
  });
});
afterEach(() => {
  cleanup();
  client?.clear();
  localStorage.clear();
});

it("M1 restores the verified overall value and first page after grouped contributors", async () => {
  coldDetail();
  await screen.findByRole("heading", { name: "0.50" });
  tab("Contributors");
  await choose("Group results by", "session");
  await screen.findByRole("table", { name: "Metric contributors" });
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Next results" })).toBeEnabled(),
  );
  click("Next results");
  await waitFor(() =>
    expect(mock.get).toHaveBeenCalledWith(
      `/runs/${wire.run.id}/results`,
      expect.objectContaining({
        params: expect.objectContaining({
          rollup: "session",
          after: wire.session_page.next_cursor,
        }),
      }),
    ),
  );
  tab("Result");
  expect(screen.getByRole("tab", { name: "Result" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  expect(
    await screen.findByRole("heading", { name: "0.50" }),
  ).toBeInTheDocument();
  expect(
    screen.queryByText(/Select the overall rollup/),
  ).not.toBeInTheDocument();
  tab("Contributors");
  expect(screen.getByLabelText("Group results by")).toHaveTextContent(
    "overall",
  );
  expect(screen.getByRole("button", { name: "First results" })).toBeDisabled();
});

it("M2 shows cold latest-run discovery loading on the default Result tab", async () => {
  const pending = deferred();
  readRuns = () => pending.promise;
  coldDetail();
  await screen.findByRole("heading", { name: metric.name });
  expect(screen.getByRole("tab", { name: "Result" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  expect(
    screen.getByRole("progressbar", { name: "Loading past runs" }),
  ).toBeInTheDocument();
  expect(screen.queryByText("No saved results")).not.toBeInTheDocument();
  await act(async () =>
    pending.resolve({ data: { results: [], next_cursor: null } }),
  );
  expect(await screen.findByText("No saved results")).toBeInTheDocument();
  expect(
    screen.queryByRole("progressbar", { name: "Loading past runs" }),
  ).not.toBeInTheDocument();
});

it("M2 shows run-list failure and inline retry without leaving Result", async () => {
  readRuns = () =>
    Promise.reject(
      Object.assign(new Error("Unavailable"), { statusCode: 503 }),
    );
  coldDetail();
  const retry = await screen.findByRole("button", { name: "Retry past runs" });
  expect(screen.getByRole("tab", { name: "Result" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  expect(screen.getByRole("alert")).toHaveTextContent(
    "Past runs could not be loaded",
  );
  expect(screen.queryByText("No saved results")).not.toBeInTheDocument();
  readRuns = null;
  fireEvent.click(retry);
  expect(
    await screen.findByRole("heading", { name: "0.50" }),
  ).toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "Retry past runs" }),
  ).not.toBeInTheDocument();
});

it("M3 refreshes a cached Not run row immediately after first successful enqueue", async () => {
  runs = [];
  mount();
  await waitFor(() => expect(screen.getAllByText("Not run")).toHaveLength(2));
  fireEvent.click(screen.getByRole("button", { name: metric.name }));
  await screen.findByText("No saved results");
  click("Run metric");
  const dialog = await screen.findByRole("dialog", { name: "Run metric" });
  fireEvent.click(
    within(dialog).getByRole("button", { name: "Run and persist" }),
  );
  await waitFor(() =>
    expect(window.location.search).toBe(`?run=${wire.run.id}`),
  );
  await waitFor(() =>
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
  );
  click("Metrics");
  expect(await screen.findByText(/· queued$/)).toBeInTheDocument();
  expect(screen.queryByText("Not run")).not.toBeInTheDocument();
  expect(mock.post).toHaveBeenCalledTimes(1);
  expect(
    mock.get.mock.calls.filter(
      ([url, options]) => url.endsWith("/runs") && options?.params?.limit === 1,
    ),
  ).toHaveLength(2);
});

it("M4 resolves all visible projects independently of the editor search and page", async () => {
  mock.projects.mockImplementation(async ({ params }) => ({
    rows:
      params.page_number === 0
        ? [{ id: "project", name: "Support" }]
        : [{ id: "other-project", name: "International" }],
    totalPages: 2,
  }));
  const view = mount(
    listElement({
      data: { rows: [{ id: "unrelated", name: "Editor search" }] },
    }),
  );
  expect(await screen.findByText("Support")).toBeInTheDocument();
  expect(await screen.findByText("International")).toBeInTheDocument();
  expect(mock.projects).toHaveBeenCalledWith(
    expect.objectContaining({
      params: expect.objectContaining({ page_number: 1, page_size: 50 }),
    }),
  );
  view.rerender(
    <QueryClientProvider client={client}>
      {listElement({
        data: { rows: [{ id: "project", name: "Wrong editor-only name" }] },
      })}
    </QueryClientProvider>,
  );
  expect(screen.getByText("Support")).toBeInTheDocument();
  expect(screen.queryByText("Wrong editor-only name")).not.toBeInTheDocument();
});

it("M4 shows honest loading and neutral fallback rather than borrowing editor names", async () => {
  const pending = deferred();
  mock.projects.mockReturnValue(pending.promise);
  mount(
    listElement({
      data: { rows: [{ id: "project", name: "Editor-only name" }] },
    }),
  );
  expect(await screen.findAllByText("Loading project…")).toHaveLength(2);
  expect(screen.queryByText("Editor-only name")).not.toBeInTheDocument();
  await act(async () => pending.resolve({ rows: [], totalPages: 1 }));
  expect(await screen.findAllByText("Project name unavailable")).toHaveLength(
    2,
  );
});

it.each([
  ["2026-10-27T10:00:00.100000Z", "2026-10-27T10:00:00.900000Z"],
  ["2026-10-27T10:00:00.123456Z", "2026-10-27T10:00:01.654321Z"],
])("M5 discloses exact saved boundaries %s to %s", (start, end) => {
  mount(
    <SelectedRunSummary
      run={{ ...wire.run, window_start: start, window_end: end }}
      result={wire.old_page.results[0]}
      detailsOpen
    />,
  );
  const summary = screen.getByRole("region", { name: "Selected saved run" });
  expect(summary).toHaveTextContent(
    `Exact saved boundaries: ${start} to ${end}`,
  );
  expect(summary).toHaveTextContent("Window (UTC):");
});

it.each([false, true])(
  "M6 keeps final-step Validate and Save compact (editing=%s)",
  (editing) => {
    mount(
      <MetricDefinitionEditor
        form={{ ...initialForm(), name: "Latency", project_id: "project" }}
        metric={editing ? metric : null}
        projects={{ data: { rows: [{ id: "project", name: "Support" }] } }}
        onChange={vi.fn()}
        onReplace={vi.fn()}
        onValidate={vi.fn()}
        onSave={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    click("Continue");
    click("Continue");
    expect(
      screen.getByRole("button", { name: "Validate formula" }),
    ).toHaveClass("MuiButton-sizeSmall");
    expect(
      screen.getByRole("button", {
        name: editing ? "Save new version" : "Save metric",
      }),
    ).toHaveClass("MuiButton-sizeSmall");
    expect(screen.getByRole("button", { name: "Back" })).toHaveClass(
      "MuiButton-sizeSmall",
    );
  },
);
