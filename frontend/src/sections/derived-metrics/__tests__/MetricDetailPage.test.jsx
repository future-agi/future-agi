import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Routes, Route } from "react-router-dom";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  userEvent,
  waitFor,
  within,
} from "src/utils/test-utils";
import MetricDetailPage from "../MetricDetailPage";
import { metric, kpi, trend, driver, field } from "./metricPageFixtures";

const mock = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  org: {},
  workspace: {},
  enabled: true,
}));
vi.mock("src/utils/axios", () => ({
  default: { get: mock.get, post: mock.post },
  endpoints: {
    derivedMetrics: Object.fromEntries(
      ["detail", "kpi", "trend", "drivers", "fields", "history", "refresh"].map(
        (name) => [name, (id) => `/metrics/${id}/${name}`],
      ),
    ),
  },
}));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => mock.org,
}));
vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => mock.workspace,
}));
vi.mock("../useMetricsEnabled", () => ({
  useMetricsEnabled: () => ({ enabled: mock.enabled }),
}));
vi.mock("react-apexcharts", () => ({
  default: () => <div data-testid="trend-chart" />,
}));
vi.mock("src/sections/projects/TracesDrawer/TracesDrawer", () => ({
  default: ({ rowData, navigationContext, onClose }) => (
    <div role="dialog" aria-label="Conversation source">
      {rowData.session_id} {JSON.stringify(navigationContext)}
      <button onClick={onClose}>Close source</button>
    </div>
  ),
}));
vi.mock("src/sections/tasks/components/TaskRunTraceDrawer", () => ({
  default: ({ traceId, projectId }) => (
    <div role="dialog" aria-label="Trace source">
      {traceId} {projectId}
    </div>
  ),
}));

let client;
let responses;
const calls = (kind) =>
  mock.get.mock.calls.filter(([url]) => url.endsWith(`/${kind}`));
const deferred = () => {
  let resolve;
  let reject;
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { resolve, reject, promise };
};
async function refetchResource(resource) {
  await act(async () => {
    void client.invalidateQueries({
      predicate: ({ queryKey }) => queryKey[4] === resource,
    });
  });
}
function mount() {
  client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0, staleTime: 300000 } },
  });
  return render(
    <QueryClientProvider client={client}>
      <Routes>
        <Route
          path="/dashboard/metrics/:metricId"
          element={<MetricDetailPage />}
        />
      </Routes>
    </QueryClientProvider>,
  );
}
beforeEach(() => {
  window.history.replaceState({}, "", "/dashboard/metrics/metric-a");
  mock.org = { currentOrganizationId: "org-a", isReady: true };
  mock.workspace = {
    currentWorkspaceId: "ws-a",
    currentWorkspaceRole: "workspace_member",
    isReady: true,
  };
  mock.enabled = true;
  responses = structuredClone({
    detail: metric,
    kpi,
    trend,
    fields: { results: [field] },
    drivers: {
      provenance: kpi.provenance,
      effective_window: kpi.effective_window,
      results: [driver],
      next_cursor: null,
    },
    history: {
      results: [
        {
          kind: "version",
          created_at: "2026-11-01T00:00:00Z",
          status: "complete",
          definition: metric.definition,
          snapshot: null,
        },
      ],
      next_cursor: null,
    },
  });
  mock.get.mockReset().mockImplementation(async (url) => {
    const kind = url.split("/").at(-1);
    if (!responses[kind]) throw new Error(`Unexpected read ${url}`);
    return { data: responses[kind] };
  });
  mock.post.mockReset().mockResolvedValue({ data: { status: "queued" } });
});
afterEach(() => {
  cleanup();
  client?.clear();
});

it("loads only active-version KPI and trend with explicit tenant and closed window", async () => {
  mount();
  expect(await screen.findByTestId("metric-hero")).toHaveTextContent("≈ 62%");
  expect(
    screen.getByRole("heading", { name: "Resolution rate" }),
  ).toBeVisible();
  expect(calls("kpi")).toHaveLength(1);
  const [, config] = calls("kpi")[0];
  expect(config.headers).toEqual({
    "X-Organization-Id": "org-a",
    "X-Workspace-Id": "ws-a",
  });
  expect(
    Date.parse(config.params.window_end) -
      Date.parse(config.params.window_start),
  ).toBe(7 * 86400000);
  expect(Object.keys(config.params).sort()).toEqual([
    "window_end",
    "window_start",
  ]);
  expect(calls("history")).toHaveLength(0);
  expect(calls("drivers")).toHaveLength(0);
  expect(
    screen.getByText("+12 percentage points vs previous period"),
  ).toBeVisible();
  expect(screen.getAllByText("95% interval: 51% – 70%").length).toBeGreaterThan(
    0,
  );
  expect(
    screen.getAllByText("from 81 evaluated of 137 conversations").length,
  ).toBeGreaterThan(0);
});

it("keeps loading separate from empty data", async () => {
  const pending = deferred();
  mock.get.mockReturnValue(pending.promise);
  mount();
  expect(screen.getByRole("status")).toHaveTextContent("Loading metric…");
  expect(screen.queryByText("No data")).not.toBeInTheDocument();
  expect(screen.queryByTestId("metric-hero")).not.toBeInTheDocument();
});

it.each([403, 404, 503])(
  "shows a %s detail failure without values, with retry",
  async (statusCode) => {
    mock.get.mockRejectedValueOnce({ statusCode });
    mount();
    expect(await screen.findByRole("alert")).toBeVisible();
    expect(screen.queryByTestId("metric-hero")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry metric" }));
    expect(await screen.findByTestId("metric-hero")).toBeVisible();
  },
);

it("allows keyboard tabs and date presets without querying hidden tabs", async () => {
  mount();
  await screen.findByTestId("metric-hero");
  const overview = screen.getByRole("tab", { name: "Overview" });
  await act(async () => {
    overview.focus();
    await userEvent.keyboard("{ArrowRight}{Enter}");
  });
  expect(
    await screen.findByRole("table", { name: "Metric drivers" }),
  ).toBeVisible();
  expect(screen.getByRole("tab", { name: "Drivers" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  const day = screen.getByRole("button", { name: "24h" });
  await act(async () => {
    day.focus();
    await userEvent.keyboard("{Enter}");
  });
  await waitFor(() => expect(calls("drivers")).toHaveLength(2));
  const params = calls("drivers").at(-1)[1].params;
  expect(Date.parse(params.window_end) - Date.parse(params.window_start)).toBe(
    86400000,
  );
  expect(calls("history")).toHaveLength(0);
});

it("validates custom windows before starting reads", async () => {
  mount();
  await screen.findByTestId("metric-hero");
  fireEvent.click(screen.getByRole("button", { name: "Custom" }));
  const dialog = screen.getByRole("dialog", { name: "Custom window" });
  fireEvent.change(within(dialog).getByLabelText("Window start"), {
    target: { value: "2026-11-01T00:00" },
  });
  fireEvent.change(within(dialog).getByLabelText("Window end"), {
    target: { value: "2026-10-01T00:00" },
  });
  fireEvent.click(within(dialog).getByRole("button", { name: "Apply window" }));
  expect(within(dialog).getByRole("alert")).toHaveTextContent(
    "Choose an end after the start",
  );
  expect(calls("kpi")).toHaveLength(1);
  fireEvent.change(within(dialog).getByLabelText("Window end"), {
    target: { value: "2026-11-03T00:00" },
  });
  fireEvent.click(within(dialog).getByRole("button", { name: "Apply window" }));
  await waitFor(() => expect(calls("kpi")).toHaveLength(2));
  expect(calls("kpi").at(-1)[1].params.window_start).toBe(
    new Date("2026-11-01T00:00").toISOString(),
  );
});

it.each([null, 0])("does not coerce %s to another value", async (value) => {
  responses.kpi.current.value = value;
  responses.kpi.current.weighted = false;
  mount();
  expect(await screen.findByTestId("metric-hero")).toHaveTextContent(
    value === null ? "No value" : "0%",
  );
  if (value === null)
    expect(screen.getByText("Comparison unavailable")).toBeVisible();
});

it.each(["no_data", "missing_inputs", "retention_expired"])(
  "labels %s rather than a fake zero",
  async (status) => {
    responses.kpi.current.value = null;
    responses.kpi.current.point_status = status;
    mount();
    const hero = await screen.findByTestId("metric-hero");
    expect(hero).not.toHaveTextContent("0");
    expect(hero).toHaveTextContent(
      status === "no_data"
        ? "No data"
        : status === "retention_expired"
          ? "outside data retention"
          : "missing inputs",
    );
  },
);

it("keeps user metrics unavailable and never starts numerical reads", async () => {
  responses.detail.definition.level = "user";
  mount();
  await screen.findByRole("heading", { name: "Resolution rate" });
  expect(screen.getByRole("button", { name: "Run now" })).toBeDisabled();
  expect(
    screen.getAllByText(/User-level metrics are not available yet/).length,
  ).toBeGreaterThan(0);
  fireEvent.click(screen.getByRole("tab", { name: "Drivers" }));
  expect(
    screen.getByText("User-level metrics and drivers are not available yet."),
  ).toBeVisible();
  expect(calls("kpi")).toHaveLength(0);
  expect(calls("trend")).toHaveLength(0);
  expect(calls("drivers")).toHaveLength(0);
});

it("shows unactivated backfill progress without querying or fabricating values", async () => {
  responses.detail.definition = null;
  mount();
  expect(await screen.findByText("Not calculated yet")).toBeVisible();
  expect(screen.getByText("Recalculating history: 2 of 7 days")).toBeVisible();
  expect(calls("kpi")).toHaveLength(0);
  fireEvent.click(screen.getByRole("tab", { name: "Definition" }));
  expect(screen.getByText(/No active definition yet/)).toBeVisible();
});

it("queues refresh with a real mutation and never claims completion", async () => {
  mount();
  await screen.findByTestId("metric-hero");
  fireEvent.click(screen.getByRole("button", { name: "Run now" }));
  expect(await screen.findByText(/Recalculation queued/)).toBeVisible();
  expect(mock.post).toHaveBeenCalledWith(
    "/metrics/metric-a/refresh",
    { confirm: true },
    expect.objectContaining({
      headers: { "X-Organization-Id": "org-a", "X-Workspace-Id": "ws-a" },
    }),
  );
  expect(screen.queryByText("Recalculation completed")).not.toBeInTheDocument();
  await screen.findByTestId("metric-hero");
  const count = calls("kpi").length;
  fireEvent.click(screen.getByRole("button", { name: "Check for updates" }));
  await waitFor(() => expect(calls("kpi").length).toBeGreaterThan(count));
});

it("honors Viewer no-write and leaves unavailable header actions disabled", async () => {
  mock.workspace.currentWorkspaceRole = "workspace_viewer";
  mount();
  await screen.findByTestId("metric-hero");
  expect(screen.getByRole("button", { name: "Run now" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Edit" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "More" }));
  expect(
    screen.getByRole("menuitem", { name: "Create alert" }),
  ).toHaveAttribute("aria-disabled", "true");
  expect(mock.post).not.toHaveBeenCalled();
});

it("refreshes even a fresh cache, and hides a cached value after refetch fails", async () => {
  mount();
  await screen.findByTestId("metric-hero");
  mock.get.mockImplementation(async (url) => {
    if (url.endsWith("/kpi")) throw { statusCode: 503 };
    return { data: responses[url.split("/").at(-1)] };
  });
  fireEvent.click(screen.getByRole("button", { name: "Refresh values" }));
  expect(await screen.findByText(/Could not load metric value/)).toBeVisible();
  expect(screen.queryByTestId("metric-hero")).not.toBeInTheDocument();
});

it("does not combine KPI and trend from different committed generations", async () => {
  responses.trend.provenance = { ...responses.trend.provenance, generation: 2 };
  expect(responses.kpi.provenance.generation).toBe(1);
  mount();
  expect(await screen.findByText(/metric updated between reads/)).toBeVisible();
  expect(screen.queryByTestId("trend-chart")).not.toBeInTheDocument();
});

it("aborts old workspace reads and never presents their late result", async () => {
  const pending = deferred();
  mock.get.mockImplementation((url, config) =>
    config.headers["X-Workspace-Id"] === "ws-a"
      ? pending.promise
      : Promise.resolve({ data: responses[url.split("/").at(-1)] }),
  );
  const view = mount();
  const signal = calls("detail")[0][1].signal;
  mock.workspace = { ...mock.workspace, currentWorkspaceId: "ws-b" };
  view.rerender(
    <QueryClientProvider client={client}>
      <Routes>
        <Route
          path="/dashboard/metrics/:metricId"
          element={<MetricDetailPage />}
        />
      </Routes>
    </QueryClientProvider>,
  );
  expect(signal.aborted).toBe(true);
  expect(await screen.findByTestId("metric-hero")).toBeVisible();
  await act(async () =>
    pending.resolve({ data: { ...metric, name: "Old workspace secret" } }),
  );
  expect(screen.queryByText("Old workspace secret")).not.toBeInTheDocument();
});

it("renders typed driver evidence and per-row effects, opening the scoped real drawer", async () => {
  responses.drivers.results.push({
    ...driver,
    unit_id: "session-b",
    influence: -0.027,
  });
  mount();
  await screen.findByTestId("metric-hero");
  fireEvent.click(screen.getByRole("tab", { name: "Drivers" }));
  expect(await screen.findByText("-1.3 percentage points")).toBeVisible();
  expect(screen.getByText("-2.7 percentage points")).toBeVisible();
  expect(screen.getAllByText("Doesn't count")).toHaveLength(2);
  expect(screen.getAllByText("passed: false")).toHaveLength(2);
  fireEvent.click(screen.getByRole("button", { name: "session-a" }));
  const source = await screen.findByRole("dialog", {
    name: "Conversation source",
  });
  expect(source).toHaveTextContent('"workspace_id":"ws-a"');
  expect(source).toHaveTextContent('"project_id":"project-a"');
  fireEvent.click(within(source).getByRole("button", { name: "Close source" }));
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});

it("excludes open, sampled-out and expired drivers, without equal-effect claims", async () => {
  responses.drivers.results.push(
    ...["in_progress", "no_value", "not_calculated"].map((status) => ({
      ...driver,
      unit_id: status,
      status,
      no_value_reason: "not_evaluated",
    })),
  );
  responses.drivers.results[0].influence = null;
  mount();
  await screen.findByTestId("metric-hero");
  fireEvent.click(screen.getByRole("tab", { name: "Drivers" }));
  const table = await screen.findByRole("table", { name: "Metric drivers" });
  expect(within(table).getAllByRole("row")).toHaveLength(2);
  expect(screen.getByText("Not available")).toBeVisible();
  expect(screen.getByText(/3 open or no-value units excluded/)).toBeVisible();
  expect(screen.queryByText(/0.08/)).not.toBeInTheDocument();
});

it("pages drivers with the returned cursor and resets it on a new range", async () => {
  responses.drivers.next_cursor = "signed-driver-page";
  mount();
  await screen.findByTestId("metric-hero");
  fireEvent.click(screen.getByRole("tab", { name: "Drivers" }));
  await screen.findByRole("table", { name: "Metric drivers" });
  fireEvent.click(screen.getByRole("button", { name: "Next drivers" }));
  await waitFor(() =>
    expect(calls("drivers").at(-1)[1].params.after).toBe("signed-driver-page"),
  );
  fireEvent.click(screen.getByRole("button", { name: "30 days" }));
  await waitFor(() =>
    expect(calls("drivers").at(-1)[1].params.after).toBeUndefined(),
  );
});

it("does not apply field labels from a concurrently activated definition", async () => {
  responses.fields.results[0].value_labels = {
    match: "New meaning",
    other: "Changed meaning",
  };
  mount();
  await screen.findByTestId("metric-hero");
  fireEvent.click(screen.getByRole("tab", { name: "Drivers" }));
  expect(
    await screen.findByText(
      "Field metadata changed. Reload the metric before inspecting driver values.",
    ),
  ).toBeVisible();
  expect(
    screen.queryByRole("table", { name: "Metric drivers" }),
  ).not.toBeInTheDocument();
  expect(screen.queryByText("Changed meaning")).not.toBeInTheDocument();
});

it("shows pending draft progress without replacing the committed version", async () => {
  mount();
  expect(await screen.findByTestId("metric-hero")).toHaveTextContent("≈ 62%");
  expect(
    screen.getByText("Draft v3 is not active. Values below still use v2."),
  ).toBeVisible();
  expect(screen.getByText("Recalculating history: 2 of 7 days")).toBeVisible();
  expect(screen.queryByText("Not calculated yet")).not.toBeInTheDocument();
});

it("keeps span drivers unavailable while leaving KPI and trend readable", async () => {
  responses.detail.definition.level = "span";
  mount();
  await screen.findByTestId("metric-hero");
  fireEvent.click(screen.getByRole("tab", { name: "Drivers" }));
  expect(
    screen.getByText(/Span drivers and unit evidence are not available yet/),
  ).toBeVisible();
  expect(calls("drivers")).toHaveLength(0);
});

it("renders history and cursor paging without calling snapshot placeholders", async () => {
  responses.history.next_cursor = "history-anchor";
  mount();
  await screen.findByTestId("metric-hero");
  fireEvent.click(screen.getByRole("tab", { name: "History" }));
  expect(
    await screen.findByRole("table", { name: "Metric history" }),
  ).toBeVisible();
  expect(screen.getByText("Definition v2")).toBeVisible();
  expect(screen.getByText("7 of 7 days")).toBeVisible();
  expect(screen.getByRole("button", { name: "Save snapshot" })).toBeDisabled();
  expect(
    screen.getByText(
      "Snapshots are not available yet. No snapshot will be saved.",
    ),
  ).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Older events" }));
  await waitFor(() =>
    expect(calls("history").at(-1)[1].params).toEqual({
      limit: 30,
      before: "history-anchor",
    }),
  );
  expect(mock.post).not.toHaveBeenCalled();
});

it.each(["success", "error"])(
  "preserves the later history page through a background detail refetch and %s",
  async (outcome) => {
    responses.history.next_cursor = "history-anchor";
    mount();
    await screen.findByTestId("metric-hero");
    fireEvent.click(screen.getByRole("tab", { name: "History" }));
    await screen.findByRole("table", { name: "Metric history" });
    fireEvent.click(screen.getByRole("button", { name: "Older events" }));
    await waitFor(() =>
      expect(calls("history").at(-1)[1].params.before).toBe("history-anchor"),
    );
    await screen.findByRole("table", { name: "Metric history" });
    expect(screen.getByText("Page 2")).toBeVisible();

    const pending = deferred();
    mock.get.mockImplementation((url) =>
      url.endsWith("/detail")
        ? pending.promise
        : Promise.resolve({ data: responses[url.split("/").at(-1)] }),
    );
    await refetchResource("detail");
    expect(await screen.findByText("Loading metric…")).toBeVisible();
    expect(
      screen.queryByRole("table", { name: "Metric history", hidden: true }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("Definition v2")).not.toBeInTheDocument();

    if (outcome === "error") {
      await act(async () => pending.reject({ statusCode: 503 }));
      expect(await screen.findByText(/Could not load metric\./)).toBeVisible();
      expect(screen.queryByText("Definition v2")).not.toBeInTheDocument();
      mock.get.mockImplementation(async (url) => ({
        data: responses[url.split("/").at(-1)],
      }));
      fireEvent.click(screen.getByRole("button", { name: "Retry metric" }));
    } else {
      await act(async () => pending.resolve({ data: responses.detail }));
    }

    expect(
      await screen.findByRole("table", { name: "Metric history" }),
    ).toBeVisible();
    expect(screen.getByText("Page 2")).toBeVisible();
    expect(screen.queryByText("Page 1")).not.toBeInTheDocument();
    expect(calls("history").at(-1)[1].params.before).toBe("history-anchor");
    expect(screen.getByRole("button", { name: "Newer events" })).toBeEnabled();
  },
);

it("restores the selected driver source and page after a background detail refetch", async () => {
  responses.drivers.next_cursor = "signed-driver-page";
  mount();
  await screen.findByTestId("metric-hero");
  fireEvent.click(screen.getByRole("tab", { name: "Drivers" }));
  await screen.findByRole("table", { name: "Metric drivers" });
  fireEvent.click(screen.getByRole("button", { name: "Next drivers" }));
  await waitFor(() =>
    expect(calls("drivers").at(-1)[1].params.after).toBe("signed-driver-page"),
  );
  fireEvent.click(await screen.findByRole("button", { name: "session-a" }));
  await screen.findByRole("dialog", { name: "Conversation source" });

  const pending = deferred();
  mock.get.mockImplementation((url) =>
    url.endsWith("/detail")
      ? pending.promise
      : Promise.resolve({ data: responses[url.split("/").at(-1)] }),
  );
  await refetchResource("detail");
  expect(await screen.findByText("Loading metric…")).toBeVisible();
  expect(
    screen.queryByRole("dialog", { hidden: true }),
  ).not.toBeInTheDocument();
  expect(screen.queryByText("passed: false")).not.toBeInTheDocument();
  await act(async () => pending.resolve({ data: responses.detail }));

  const source = await screen.findByRole("dialog", {
    name: "Conversation source",
  });
  expect(source).toHaveTextContent("session-a");
  expect(source).toHaveTextContent('"workspace_id":"ws-a"');
  expect(screen.getByText("Page 2")).toBeVisible();
  expect(calls("drivers").at(-1)[1].params.after).toBe("signed-driver-page");
});

it.each(["detail", "trend"])(
  "preserves the trend table through a background %s refetch, but resets on explicit refresh",
  async (resource) => {
    mount();
    await screen.findByTestId("metric-hero");
    fireEvent.click(
      await screen.findByRole("button", { name: "View data table" }),
    );
    expect(
      screen.getByRole("table", { name: "Metric trend data" }),
    ).toBeVisible();
    const pending = deferred();
    mock.get.mockImplementation((url) =>
      url.endsWith(`/${resource}`)
        ? pending.promise
        : Promise.resolve({ data: responses[url.split("/").at(-1)] }),
    );
    await refetchResource(resource);
    expect(
      await screen.findByText(
        resource === "detail" ? "Loading metric…" : "Loading trend…",
      ),
    ).toBeVisible();
    expect(
      screen.queryByRole("table", { name: "Metric trend data", hidden: true }),
    ).not.toBeInTheDocument();
    if (resource === "detail")
      expect(screen.queryByTestId("metric-hero")).not.toBeInTheDocument();
    await act(async () => pending.resolve({ data: responses[resource] }));

    expect(
      await screen.findByRole("table", { name: "Metric trend data" }),
    ).toBeVisible();
    expect(screen.queryByTestId("trend-chart")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Refresh values" }));
    expect(await screen.findByTestId("trend-chart")).toBeVisible();
    expect(
      screen.queryByRole("table", { name: "Metric trend data" }),
    ).not.toBeInTheDocument();
  },
);

it("resets history pagination when the active definition actually changes", async () => {
  responses.history.next_cursor = "history-anchor";
  mount();
  await screen.findByTestId("metric-hero");
  fireEvent.click(screen.getByRole("tab", { name: "History" }));
  await screen.findByRole("table", { name: "Metric history" });
  fireEvent.click(screen.getByRole("button", { name: "Older events" }));
  await waitFor(() =>
    expect(calls("history").at(-1)[1].params.before).toBe("history-anchor"),
  );
  await screen.findByRole("table", { name: "Metric history" });
  expect(screen.getByText("Page 2")).toBeVisible();
  responses.detail = {
    ...responses.detail,
    active_version: "version-3",
    definition: { ...responses.detail.definition, id: "version-3", version: 3 },
  };
  await refetchResource("detail");
  expect(await screen.findByText("Page 1")).toBeVisible();
  await screen.findByRole("table", { name: "Metric history" });
  expect(calls("history").at(-1)[1].params.before).toBeUndefined();
  expect(screen.getByRole("button", { name: "Newer events" })).toBeDisabled();
});

it("clears tab interaction state when the workspace changes", async () => {
  const view = mount();
  await screen.findByTestId("metric-hero");
  fireEvent.click(
    await screen.findByRole("button", { name: "View data table" }),
  );
  expect(
    screen.getByRole("table", { name: "Metric trend data" }),
  ).toBeVisible();
  mock.workspace = { ...mock.workspace, currentWorkspaceId: "ws-b" };
  view.rerender(
    <QueryClientProvider client={client}>
      <Routes>
        <Route
          path="/dashboard/metrics/:metricId"
          element={<MetricDetailPage />}
        />
      </Routes>
    </QueryClientProvider>,
  );
  expect(await screen.findByTestId("trend-chart")).toBeVisible();
  expect(
    screen.queryByRole("table", { name: "Metric trend data" }),
  ).not.toBeInTheDocument();
  expect(calls("detail").at(-1)[1].headers["X-Workspace-Id"]).toBe("ws-b");
});

it("reads the active definition instead of silently substituting the newer draft", async () => {
  responses.detail.definition.formula = "sum(cost) / sum(tokens)";
  responses.detail.draft_definition.formula = "mean(different)";
  mount();
  await screen.findByTestId("metric-hero");
  fireEvent.click(screen.getByRole("tab", { name: "Definition" }));
  expect(screen.getByText("sum(cost) / sum(tokens)")).toBeVisible();
  expect(screen.queryByText("mean(different)")).not.toBeInTheDocument();
  expect(screen.getByText("passed is true")).toBeVisible();
  expect(
    screen.getByText(
      "Draft v3 is not active. Its changes are not reflected in these values.",
    ),
  ).toBeVisible();
});
