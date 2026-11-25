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
import { formatMetricTime } from "../metricTime";
import { metric, kpi, trend, driver, field } from "./metricPageFixtures";

const mock = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  delete: vi.fn(),
  org: {},
  workspace: {},
  enabled: true,
  projectsFail: false,
}));
vi.mock("src/utils/axios", () => ({
  default: { get: mock.get, post: mock.post, delete: mock.delete },
  endpoints: {
    derivedMetrics: {
      ...Object.fromEntries(
        [
          "detail",
          "kpi",
          "trend",
          "drivers",
          "fields",
          "history",
          "refresh",
          "snapshots",
        ].map((name) => [name, (id) => `/metrics/${id}/${name}`]),
      ),
      snapshot: (id, snapshotId) => `/metrics/${id}/snapshots/${snapshotId}`,
    },
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
  default: ({ open, rowData, navigationContext, onClose }) =>
    open ? (
      <div role="dialog" aria-label="Conversation source">
        {rowData.session_id} {JSON.stringify(navigationContext)}
        <button onClick={onClose}>Close source</button>
      </div>
    ) : null,
}));
// The header names the project from the same Observe project read as the list.
vi.mock("src/api/project/observe-project-list", () => ({
  readObserveProjectPage: async () => {
    if (mock.projectsFail) throw new Error("projects unavailable");
    return {
      rows: [{ id: "project-a", name: "Customer care" }],
      totalRows: 1,
      totalPages: 1,
    };
  },
}));
vi.mock("src/sections/tasks/components/TaskRunTraceDrawer", () => ({
  default: ({ open, traceId, projectId }) =>
    open ? (
      <div role="dialog" aria-label="Trace source">
        {traceId} {projectId}
      </div>
    ) : null,
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
  mock.projectsFail = false;
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
  mock.delete
    .mockReset()
    .mockResolvedValue({ data: { status: "cleanup_pending" } });
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

it("saves the selected window once and shows queued snapshot history", async () => {
  mount();
  await screen.findByTestId("metric-hero");
  fireEvent.click(screen.getByRole("button", { name: "Save snapshot" }));
  const dialog = screen.getByRole("dialog", { name: "Save snapshot" });
  expect(
    within(dialog).getByText(/Later recalculations will not change it/),
  ).toBeVisible();
  // Round 3 UI: the value the Overview read for this window, with its time.
  expect(
    within(dialog).getByText("Current value for this window"),
  ).toBeVisible();
  expect(within(dialog).getByText("≈ 62%")).toBeVisible();
  expect(within(dialog).getByText(/^As read at \d\d:\d\d UTC\./)).toBeVisible();
  responses.history.results.unshift({
    kind: "snapshot",
    created_at: "2026-11-09T00:00:00Z",
    status: "queued",
    snapshot: {
      id: "snapshot-a",
      metric_version: 2,
      window_start: "2026-11-01T00:00:00Z",
      window_end: "2026-11-08T00:00:00Z",
      captured_at: "2026-11-09T00:00:00Z",
      status: "queued",
    },
  });
  fireEvent.click(
    within(dialog).getByRole("button", { name: "Save snapshot" }),
  );
  await waitFor(() => expect(mock.post).toHaveBeenCalledTimes(1));
  const [url, body, config] = mock.post.mock.calls[0];
  expect(url).toBe("/metrics/metric-a/snapshots");
  expect(body.window_start).toBe(calls("kpi")[0][1].params.window_start);
  expect(body.window_end).toBe(calls("kpi")[0][1].params.window_end);
  expect(body.idempotency_key).toEqual(expect.any(String));
  expect(config.headers).toEqual({
    "X-Organization-Id": "org-a",
    "X-Workspace-Id": "ws-a",
  });
  expect(await screen.findByText("Snapshot · v2")).toBeVisible();
  expect(screen.getByText("Queued")).toBeVisible();
});

it.each(["unique_count", "percentile"])(
  "enables Save snapshot for supported conversation %s metrics",
  async (kind) => {
    responses.detail.definition.kind = kind;
    responses.detail.snapshot_supported = true;
    responses.detail.snapshot_unsupported_reason = "";
    mount();
    await screen.findByTestId("metric-hero");
    const save = screen.getByRole("button", { name: "Save snapshot" });
    expect(save).toBeEnabled();
    fireEvent.click(save);
    const dialog = screen.getByRole("dialog", { name: "Save snapshot" });
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Save snapshot" }),
    );
    await waitFor(() => expect(mock.post).toHaveBeenCalledTimes(1));
    expect(mock.post.mock.calls[0][0]).toBe("/metrics/metric-a/snapshots");
    expect(mock.post.mock.calls[0][1]).toMatchObject(calls("kpi")[0][1].params);
  },
);

it("confirms permanent deletion and shows cleanup pending, not deleted", async () => {
  const snapshot = {
    id: "snapshot-a",
    metric_version: 2,
    window_start: "2026-11-01T00:00:00Z",
    window_end: "2026-11-08T00:00:00Z",
    captured_at: "2026-11-09T00:00:00Z",
    status: "complete",
  };
  responses.history.results = [
    {
      kind: "snapshot",
      created_at: snapshot.captured_at,
      status: "complete",
      snapshot,
    },
  ];
  mount();
  await screen.findByTestId("metric-hero");
  fireEvent.click(screen.getByRole("tab", { name: "History" }));
  fireEvent.click(
    await screen.findByRole("button", { name: "Delete snapshot" }),
  );
  expect(mock.delete).not.toHaveBeenCalled();
  const dialog = screen.getByRole("dialog", { name: "Delete snapshot?" });
  snapshot.deleted_at = "2026-11-09T01:00:00Z";
  snapshot.cleanup_status = "pending";
  fireEvent.click(
    within(dialog).getByRole("button", { name: "Delete snapshot" }),
  );
  await waitFor(() => expect(mock.delete).toHaveBeenCalledTimes(1));
  expect(mock.delete.mock.calls[0][0]).toBe(
    "/metrics/metric-a/snapshots/snapshot-a",
  );
  expect(await screen.findByText("Cleanup pending")).toBeVisible();
  expect(screen.queryByText("Deleted")).not.toBeInTheDocument();
});

it.each(["span", "user", "trace"])(
  "does not enable unsupported %s snapshot writes",
  async (level) => {
    responses.detail.definition.level = level;
    responses.detail.snapshot_supported = false;
    responses.detail.snapshot_unsupported_reason = `Snapshots are not available for ${level} metrics.`;
    mount();
    await screen.findByRole("heading", { name: "Resolution rate" });
    expect(
      screen.getByRole("button", { name: "Save snapshot" }),
    ).toBeDisabled();
    expect(mock.post).not.toHaveBeenCalled();
  },
);

it("names the metric's project in the header, or says it is unavailable", async () => {
  mount();
  await screen.findByRole("heading", { name: "Resolution rate" });
  expect(await screen.findByText("Customer care")).toBeVisible();
  expect(screen.getByText("Project:", { exact: false })).toBeInTheDocument();
  cleanup();
  client.clear();
  mock.projectsFail = true;
  mount();
  await screen.findByRole("heading", { name: "Resolution rate" });
  expect(await screen.findByText("Project name unavailable")).toBeVisible();
  expect(screen.queryByText("Customer care")).not.toBeInTheDocument();
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

it("re-anchors a preset to a fresh window ending now from the active range and Refresh, and keeps a custom window", async () => {
  vi.useFakeTimers({ toFake: ["Date"] });
  try {
    vi.setSystemTime(new Date("2026-11-08T12:00:00Z"));
    mount();
    await screen.findByTestId("metric-hero");
    const lastKpi = () => calls("kpi").at(-1)[1].params;
    expect(lastKpi().window_end).toBe("2026-11-08T12:00:00.000Z");

    // Clicking the range that is already selected reads it again, ending now.
    vi.setSystemTime(new Date("2026-11-08T13:00:00Z"));
    fireEvent.click(screen.getByRole("button", { name: "7 days" }));
    await waitFor(() =>
      expect(lastKpi().window_end).toBe("2026-11-08T13:00:00.000Z"),
    );
    expect(
      Date.parse(lastKpi().window_end) - Date.parse(lastKpi().window_start),
    ).toBe(7 * 86400000);
    expect(screen.getByRole("button", { name: "7 days" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );

    // Refresh does the same, like the list page's Refresh.
    vi.setSystemTime(new Date("2026-11-08T14:00:00Z"));
    fireEvent.click(screen.getByRole("button", { name: "Refresh values" }));
    await waitFor(() =>
      expect(lastKpi().window_end).toBe("2026-11-08T14:00:00.000Z"),
    );
    await screen.findByTestId("metric-hero");

    // A custom window is explicit: Refresh reads the same window again.
    fireEvent.click(screen.getByRole("button", { name: "Custom" }));
    const dialog = screen.getByRole("dialog", { name: "Custom window" });
    fireEvent.change(within(dialog).getByLabelText("Window start"), {
      target: { value: "2026-11-01T00:00" },
    });
    fireEvent.change(within(dialog).getByLabelText("Window end"), {
      target: { value: "2026-11-02T00:00" },
    });
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Apply window" }),
    );
    const customEnd = new Date("2026-11-02T00:00").toISOString();
    await waitFor(() => expect(lastKpi().window_end).toBe(customEnd));
    await screen.findByTestId("metric-hero");
    const readsBefore = calls("kpi").length;
    vi.setSystemTime(new Date("2026-11-08T15:00:00Z"));
    fireEvent.click(screen.getByRole("button", { name: "Refresh values" }));
    await waitFor(() =>
      expect(calls("kpi").length).toBeGreaterThan(readsBefore),
    );
    expect(lastKpi().window_end).toBe(customEnd);
  } finally {
    vi.useRealTimers();
  }
});

it("reads only the fresh window on a preset Refresh, never the window it leaves", async () => {
  vi.useFakeTimers({ toFake: ["Date"] });
  try {
    vi.setSystemTime(new Date("2026-11-08T12:00:00Z"));
    mount();
    await screen.findByTestId("metric-hero");
    const oldEnd = "2026-11-08T12:00:00.000Z";
    const readsOf = (kind, end) =>
      calls(kind).filter(([, config]) => config.params.window_end === end)
        .length;
    const oldReads = {
      kpi: readsOf("kpi", oldEnd),
      trend: readsOf("trend", oldEnd),
    };
    expect(oldReads.kpi).toBeGreaterThan(0);
    const details = calls("detail").length;
    vi.setSystemTime(new Date("2026-11-08T14:00:00Z"));
    fireEvent.click(screen.getByRole("button", { name: "Refresh values" }));
    await waitFor(() =>
      expect(calls("kpi").at(-1)[1].params.window_end).toBe(
        "2026-11-08T14:00:00.000Z",
      ),
    );
    await screen.findByTestId("metric-hero");
    // No read of the window being left, and the metric itself is read again.
    expect(readsOf("kpi", oldEnd)).toBe(oldReads.kpi);
    expect(readsOf("trend", oldEnd)).toBe(oldReads.trend);
    expect(calls("detail").length).toBeGreaterThan(details);
  } finally {
    vi.useRealTimers();
  }
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

it("scopes an empty drivers page to the drivers, and names an empty later page", async () => {
  responses.drivers.results = [];
  mount();
  await screen.findByTestId("metric-hero");
  fireEvent.click(screen.getByRole("tab", { name: "Drivers" }));
  expect(await screen.findByText("No drivers in this window")).toBeVisible();
  // Only the returned drivers are known, never every unit in the window.
  expect(
    screen.getByText(
      "No unit pulling the value down has a settled value in this window. Try a longer window.",
    ),
  ).toBeVisible();
  expect(
    screen.queryByText(/No unit has a settled value/),
  ).not.toBeInTheDocument();
  cleanup();
  client.clear();

  responses.drivers.results = [driver];
  responses.drivers.next_cursor = "signed-driver-page";
  mock.get.mockImplementation(async (url, config) => {
    const kind = url.split("/").at(-1);
    if (kind === "drivers" && config.params.after)
      return {
        data: { ...responses.drivers, results: [], next_cursor: null },
      };
    return { data: responses[kind] };
  });
  mount();
  await screen.findByTestId("metric-hero");
  fireEvent.click(screen.getByRole("tab", { name: "Drivers" }));
  await screen.findByRole("table", { name: "Metric drivers" });
  fireEvent.click(screen.getByRole("button", { name: "Next drivers" }));
  expect(await screen.findByText("No more drivers")).toBeVisible();
  expect(
    screen.getByText(
      "You have reached the end of the list. Use Previous to go back.",
    ),
  ).toBeVisible();
  expect(
    screen.queryByText("No drivers in this window"),
  ).not.toBeInTheDocument();
  expect(screen.getByText("Page 2")).toBeVisible();
  cleanup();
  client.clear();

  // Every returned unit is still open, and more pages follow: the copy says
  // so for this page only, never that the window has no drivers.
  responses.drivers.results = [
    {
      ...driver,
      unit_id: "open-unit",
      status: "in_progress",
      no_value_reason: "not_evaluated",
    },
  ];
  responses.drivers.next_cursor = "signed-driver-page";
  mock.get.mockImplementation(async (url) => ({
    data: responses[url.split("/").at(-1)],
  }));
  mount();
  await screen.findByTestId("metric-hero");
  fireEvent.click(screen.getByRole("tab", { name: "Drivers" }));
  expect(await screen.findByText("No drivers on this page")).toBeVisible();
  expect(
    screen.getByText(
      "No unit on this page has a settled value. Use Next to see more.",
    ),
  ).toBeVisible();
  expect(
    screen.queryByText("No drivers in this window"),
  ).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Next drivers" })).toBeEnabled();
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

it("renders history and cursor paging without implicitly saving a snapshot", async () => {
  responses.history.next_cursor = "history-anchor";
  mount();
  await screen.findByTestId("metric-hero");
  fireEvent.click(screen.getByRole("tab", { name: "History" }));
  expect(
    await screen.findByRole("table", { name: "Metric history" }),
  ).toBeVisible();
  expect(screen.getByText("Definition v2")).toBeVisible();
  expect(screen.getByText("7 of 7 days")).toBeVisible();
  expect(screen.getByRole("button", { name: "Save snapshot" })).toBeEnabled();
  expect(screen.getByText(/Snapshots keep the captured result/)).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Older events" }));
  await waitFor(() =>
    expect(calls("history").at(-1)[1].params).toEqual({
      limit: 30,
      before: "history-anchor",
    }),
  );
  expect(mock.post).not.toHaveBeenCalled();
});

it("returns to newest history after saving from an older page", async () => {
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
  fireEvent.click(screen.getByRole("button", { name: "Save snapshot" }));
  const dialog = screen.getByRole("dialog", { name: "Save snapshot" });
  fireEvent.click(
    within(dialog).getByRole("button", { name: "Save snapshot" }),
  );
  await waitFor(() => expect(mock.post).toHaveBeenCalledTimes(1));
  await waitFor(() => expect(screen.getByText("Page 1")).toBeVisible());
  await waitFor(() =>
    expect(calls("history").at(-1)[1].params).toEqual({ limit: 30 }),
  );
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

// Merge of B18 snapshots with the polished History: every snapshot state, its
// captions, the read-only reason and the pending and error states.
const snapshotWindowText =
  "01 Nov 2026, 00:00:00 UTC – 08 Nov 2026, 00:00:00 UTC";
const erasure = { changed_at: "2026-11-09T02:00:00Z", removed_unit_count: 3 };
const erasureNote =
  "Changed 09 Nov 2026, 02:00:00 UTC: 3 conversations removed after a data erasure request";
const expiredNote =
  "Snapshot not saved: the data changed before it could be copied. Try again.";
const deletionNote =
  "Deletion requested 09 Nov 2026, 01:00:00 UTC. Its stored results are being removed.";
const deletion = (cleanup_status) => ({
  deleted_at: "2026-11-09T01:00:00Z",
  cleanup_status,
});
const readOnlyReason =
  "A workspace member role is required to delete snapshots.";

async function openSnapshotHistory(fields = {}) {
  const snapshot = {
    id: "snapshot-a",
    metric_version: 2,
    window_start: "2026-11-01T00:00:00Z",
    window_end: "2026-11-08T00:00:00Z",
    captured_at: "2026-11-09T00:00:00Z",
    status: "complete",
    ...fields,
  };
  responses.history.results = [
    {
      kind: "snapshot",
      created_at: snapshot.captured_at,
      status: snapshot.status,
      snapshot,
    },
  ];
  mount();
  await screen.findByTestId("metric-hero");
  fireEvent.click(screen.getByRole("tab", { name: "History" }));
  const table = await screen.findByRole("table", { name: "Metric history" });
  return within(table).getByText("Snapshot · v2").closest("tr");
}

it.each([
  {
    name: "queued",
    fields: { status: "queued" },
    label: "Queued",
    notes: [],
  },
  {
    name: "capturing",
    fields: { status: "running" },
    label: "Capturing",
    notes: [],
  },
  {
    name: "saved",
    fields: {},
    label: "Saved",
    notes: [],
  },
  {
    name: "failed: window not calculated",
    fields: { status: "failed", error: "window_not_calculated" },
    label: "Failed",
    notes: [
      "Not saved. This window has not been calculated. Choose a later start date.",
    ],
  },
  {
    name: "failed: capture did not finish",
    fields: { status: "failed", error: "capture_failed" },
    label: "Failed",
    notes: ["Not saved. The capture did not finish. Try again."],
  },
  {
    name: "failed: unknown code",
    fields: { status: "failed", error: "a_new_server_code" },
    label: "Failed",
    notes: [],
  },
  {
    name: "expired",
    fields: { status: "failed", error: "expired" },
    label: "Expired",
    notes: [expiredNote],
  },
  {
    name: "changed, count not returned",
    fields: { changed_at: "2026-11-09T02:00:00Z", removed_unit_count: null },
    label: "Changed by data erasure",
    notes: ["Snapshot changed after a data erasure request"],
  },
  {
    name: "changed, none removed",
    fields: { changed_at: "2026-11-09T02:00:00Z", removed_unit_count: 0 },
    label: "Changed by data erasure",
    notes: [
      "Changed 09 Nov 2026, 02:00:00 UTC: 0 conversations removed after a data erasure request",
    ],
  },
  {
    name: "expired and changed",
    fields: { ...erasure, status: "failed", error: "expired" },
    label: "Expired",
    notes: [expiredNote, erasureNote],
  },
  {
    name: "cleanup pending",
    fields: deletion("pending"),
    label: "Cleanup pending",
    notes: [deletionNote],
  },
  {
    name: "cleanup pending and changed",
    fields: { ...deletion("pending"), ...erasure },
    label: "Cleanup pending",
    notes: [deletionNote, erasureNote],
  },
  {
    name: "deleted",
    fields: deletion("done"),
    label: "Deleted",
    notes: [],
  },
  {
    name: "deleted and changed",
    fields: { ...deletion("done"), ...erasure },
    label: "Deleted",
    notes: [erasureNote],
  },
])(
  "labels a $name snapshot and shows only the captions that apply",
  async ({ fields, label, notes }) => {
    const row = await openSnapshotHistory(fields);
    const status = within(row).getAllByRole("cell")[2];
    // The label and its captions are the whole cell: no other text or number.
    expect(status.textContent).toBe([label, ...notes].join(""));
    expect(within(status).getByText(label)).toBeVisible();
    for (const note of notes) {
      expect(within(status).getByText(note)).toBeVisible();
    }
    if (fields.deleted_at) {
      expect(within(row).getByText("No longer available")).toBeVisible();
      expect(within(row).queryByRole("button")).not.toBeInTheDocument();
    } else {
      expect(
        within(row).getByRole("button", { name: "Delete snapshot" }),
      ).toBeEnabled();
    }
  },
);

it("lets a Viewer reach why Delete snapshot is disabled, by keyboard", async () => {
  mock.workspace.currentWorkspaceRole = "workspace_viewer";
  const row = await openSnapshotHistory();
  const button = within(row).getByRole("button", { name: "Delete snapshot" });
  expect(button).toBeDisabled();
  // Each row's button says which captured window it deletes.
  expect(button).toHaveAccessibleDescription(snapshotWindowText);
  const reason = button.parentElement;
  expect(reason).toHaveAttribute("tabindex", "0");
  const user = userEvent.setup();
  for (let step = 0; step < 30; step += 1) {
    if (document.activeElement === reason) break;
    await user.tab();
  }
  expect(reason).toHaveFocus();
  await waitFor(() =>
    expect(screen.getByRole("tooltip")).toHaveTextContent(readOnlyReason),
  );
  expect(reason).toHaveAccessibleDescription(readOnlyReason);
  expect(mock.delete).not.toHaveBeenCalled();
});

it("shows Deleting… while pending, then keeps the confirmation and its error", async () => {
  const pending = deferred();
  mock.delete.mockReturnValueOnce(pending.promise);
  const row = await openSnapshotHistory();
  expect(
    within(row).getByRole("button", { name: "Delete snapshot" }),
  ).toHaveAccessibleDescription(snapshotWindowText);
  fireEvent.click(within(row).getByRole("button", { name: "Delete snapshot" }));
  const dialog = screen.getByRole("dialog", { name: "Delete snapshot?" });
  expect(within(dialog).getByText(snapshotWindowText)).toBeVisible();
  fireEvent.click(
    within(dialog).getByRole("button", { name: "Delete snapshot" }),
  );
  expect(
    await within(dialog).findByRole("button", { name: "Deleting…" }),
  ).toBeDisabled();
  expect(within(dialog).getByRole("button", { name: "Cancel" })).toBeDisabled();
  expect(
    within(dialog).queryByRole("button", { name: "Close" }),
  ).not.toBeInTheDocument();
  await act(async () => pending.reject({ statusCode: 503 }));
  // Round 3 UI: an action failure says what did not happen.
  expect(await within(dialog).findByRole("alert")).toHaveTextContent(
    "The snapshot was not deleted. Try again.",
  );
  expect(
    screen.getByRole("dialog", { name: "Delete snapshot?" }),
  ).toBeVisible();
  expect(
    within(dialog).getByRole("button", { name: "Delete snapshot" }),
  ).toBeEnabled();
  expect(within(dialog).getByRole("button", { name: "Close" })).toBeEnabled();
  expect(mock.delete).toHaveBeenCalledTimes(1);
});

it("shows Queuing… while a snapshot request is pending, with no way to close", async () => {
  const pending = deferred();
  mock.post.mockReturnValueOnce(pending.promise);
  mount();
  await screen.findByTestId("metric-hero");
  fireEvent.click(screen.getByRole("button", { name: "Save snapshot" }));
  const dialog = screen.getByRole("dialog", { name: "Save snapshot" });
  fireEvent.click(
    within(dialog).getByRole("button", { name: "Save snapshot" }),
  );
  expect(
    await within(dialog).findByRole("button", { name: "Queuing…" }),
  ).toBeDisabled();
  expect(within(dialog).getByRole("button", { name: "Cancel" })).toBeDisabled();
  expect(
    within(dialog).queryByRole("button", { name: "Close" }),
  ).not.toBeInTheDocument();
  await act(async () => pending.resolve({ data: { status: "queued" } }));
  await waitFor(() =>
    expect(
      screen.queryByRole("dialog", { name: "Save snapshot" }),
    ).not.toBeInTheDocument(),
  );
  expect(mock.post).toHaveBeenCalledTimes(1);
});

it("shows the bucket-aligned window a snapshot saves, and sends the selection", async () => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-11-09T14:09:54.321Z"));
  try {
    mount();
    await screen.findByTestId("metric-hero");
    fireEvent.click(screen.getByRole("button", { name: "Save snapshot" }));
    const dialog = screen.getByRole("dialog", { name: "Save snapshot" });
    expect(
      within(dialog).getByText(
        "02 Nov 2026, 14:00:00 UTC – 09 Nov 2026, 14:00:00 UTC",
      ),
    ).toBeVisible();
    expect(within(dialog).queryByText(/14:09:54/)).not.toBeInTheDocument();
    expect(
      within(dialog).getByText(
        "Your selected range, with both ends rounded down to 15-minute UTC buckets.",
      ),
    ).toBeVisible();
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Save snapshot" }),
    );
    await waitFor(() => expect(mock.post).toHaveBeenCalledTimes(1));
    // The server rounds the request itself; the client sends what was chosen.
    expect(mock.post.mock.calls[0][1]).toMatchObject({
      window_start: "2026-11-02T14:09:54.321Z",
      window_end: "2026-11-09T14:09:54.321Z",
    });
  } finally {
    vi.useRealTimers();
  }
});

// Round 3 UI: the reads Run now triggers keep the header's identity and
// scope, so its result is no longer reset before anyone sees it.
it("keeps the header and the Run now result through the detail read it triggers", async () => {
  mount();
  await screen.findByTestId("metric-hero");
  const pending = deferred();
  mock.get.mockImplementation((url) =>
    url.endsWith("/detail")
      ? pending.promise
      : Promise.resolve({ data: responses[url.split("/").at(-1)] }),
  );
  fireEvent.click(screen.getByRole("button", { name: "Run now" }));
  await waitFor(() => expect(mock.post).toHaveBeenCalledTimes(1));
  expect(await screen.findByText("Loading metric…")).toBeVisible();
  // The value waits for the fresh read; the title and scope do not.
  expect(screen.queryByTestId("metric-hero")).not.toBeInTheDocument();
  expect(
    screen.getByRole("heading", { name: "Resolution rate" }),
  ).toBeVisible();
  expect(screen.getByText("Customer care")).toBeVisible();
  await act(async () => pending.resolve({ data: responses.detail }));
  expect(
    await screen.findByText(/Recalculation queued at \d\d:\d\d UTC\./),
  ).toBeVisible();
  expect(
    screen.getByText(/Current values stay until it finishes/),
  ).toBeVisible();
  expect(await screen.findByTestId("metric-hero")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Close" }));
  await waitFor(() =>
    expect(screen.queryByText(/Recalculation queued/)).not.toBeInTheDocument(),
  );
});

it("says a recalculation was not queued, not that something failed to load", async () => {
  mount();
  await screen.findByTestId("metric-hero");
  mock.post.mockRejectedValueOnce({ statusCode: 503 });
  fireEvent.click(screen.getByRole("button", { name: "Run now" }));
  expect(
    await screen.findByText("The recalculation was not queued. Try again."),
  ).toBeVisible();
  expect(screen.queryByText(/Could not load/)).not.toBeInTheDocument();
});

const pendingMetric = () => {
  responses.detail.definition = null;
  responses.detail.active_version = null;
  responses.detail.draft_definition = {
    ...metric.definition,
    id: "version-3",
    version: 3,
    backfill_status: "pending",
    backfill_days_done: null,
    backfill_days_total: null,
    created_at: "2026-11-11T05:24:07Z",
  };
  const attempt = (created_at, status) => ({
    kind: "recalculation",
    created_at,
    status,
    definition: null,
    snapshot: null,
  });
  responses.history.results = [
    attempt("2026-11-11T05:22:07Z", "prepared"),
    attempt("2026-11-11T04:58:09Z", "aborted"),
    attempt("2026-11-11T04:34:07Z", "aborted"),
    {
      kind: "version",
      created_at: "2026-11-11T04:20:00Z",
      status: "pending",
      definition: responses.detail.draft_definition,
      snapshot: null,
    },
  ];
};

it("states only reported facts while the first calculation is pending", async () => {
  pendingMetric();
  mount();
  expect(await screen.findByText("Not calculated yet")).toBeVisible();
  // One word per state: the header's "pending" is the panel's "Pending".
  expect(screen.getByText(/The first calculation is pending\./)).toBeVisible();
  expect(screen.getByText("v3 · First calculation pending")).toBeVisible();
  expect(screen.getByText("11 Nov 2026, 05:24:07 UTC")).toBeVisible();
  expect(screen.getByText("Pending")).toBeVisible();
  expect(screen.queryByText("Queued")).not.toBeInTheDocument();
  expect(await screen.findByText("Preparing")).toBeVisible();
  expect(screen.getByText("started 11 Nov 2026, 05:22:07 UTC")).toBeVisible();
  expect(
    screen.getByText(/2 of the last 3 attempts stopped before finishing\./),
  ).toBeVisible();
  // Most attempts stopped: a next step, with no guessed cause.
  expect(
    screen.getByText(
      /If attempts keep stopping, edit the definition or contact support\./,
    ),
  ).toBeVisible();
  expect(screen.getByText(/^Checked /)).toBeVisible();
  expect(
    screen.getByText(/Checks again every 30 seconds while Overview is open\./),
  ).toBeVisible();
  expect(calls("kpi")).toHaveLength(0);
  expect(calls("trend")).toHaveLength(0);
  const reads = calls("history").length;
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Check now" })).toBeEnabled(),
  );
  fireEvent.click(screen.getByRole("button", { name: "Check now" }));
  await waitFor(() => expect(calls("history").length).toBeGreaterThan(reads));
  // The panel keeps its facts while it checks again.
  expect(screen.getByText("Preparing")).toBeVisible();
});

it("collapses repeated stopped attempts in History and labels attempt states", async () => {
  pendingMetric();
  mount();
  await screen.findByText("Not calculated yet");
  fireEvent.click(await screen.findByRole("button", { name: "View history" }));
  const table = await screen.findByRole("table", { name: "Metric history" });
  expect(within(table).getByText("Time (UTC)")).toBeVisible();
  expect(within(table).getByText("2 recalculations")).toBeVisible();
  expect(within(table).getAllByText("Stopped")).toHaveLength(1);
  expect(within(table).getByText("Preparing")).toBeVisible();
  expect(within(table).queryByText("aborted")).not.toBeInTheDocument();
  expect(within(table).queryByText("Not reported")).not.toBeInTheDocument();
  expect(within(table).getAllByText("Progress not reported").length).toBe(3);
  fireEvent.click(
    within(table).getByRole("button", { name: "Show 2 recalculations" }),
  );
  expect(within(table).getAllByText("Stopped")).toHaveLength(3);
  expect(
    within(table).getByRole("button", { name: "Hide 2 recalculations" }),
  ).toHaveAttribute("aria-expanded", "true");
});

it("shows the saved version read-only on Definition while it waits", async () => {
  pendingMetric();
  mount();
  await screen.findByText("Not calculated yet");
  fireEvent.click(screen.getByRole("tab", { name: "Definition" }));
  expect(screen.getByRole("heading", { name: "Definition v3" })).toBeVisible();
  expect(screen.getByText(/No active definition yet/)).toBeVisible();
  expect(screen.queryByText(/Active definition ·/)).not.toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "Open draft" }),
  ).not.toBeInTheDocument();
});

it("says a metric saved a moment ago was saved just now, with no attempts yet", async () => {
  pendingMetric();
  const savedAt = new Date().toISOString();
  responses.detail.draft_definition.created_at = savedAt;
  responses.history.results = [
    {
      kind: "version",
      created_at: savedAt,
      status: "pending",
      definition: responses.detail.draft_definition,
      snapshot: null,
    },
  ];
  mount();
  expect(await screen.findByText("Not calculated yet")).toBeVisible();
  expect(await screen.findByText("None recorded yet")).toBeVisible();
  expect(screen.getByText(/· just now/)).toBeVisible();
  expect(
    screen.getByText(/Nothing is shown as zero in the meantime\./),
  ).toBeVisible();
  expect(screen.queryByText(/contact support/)).not.toBeInTheDocument();
});

// The poll's contract: while the first calculation can still finish, the
// panel reads the metric every 30 seconds; once a read returns an active
// definition, the page's own detail read runs once and its values replace
// the panel. Page read and status read share one URL, so counts are exact.
it("replaces the pending panel with values when a poll finds the first calculation finished", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  try {
    pendingMetric();
    mount();
    expect(await screen.findByText("Not calculated yet")).toBeVisible();
    // The page's detail read, then the panel's status read.
    await waitFor(() => expect(calls("detail")).toHaveLength(2));
    expect(calls("kpi")).toHaveLength(0);
    responses.detail = structuredClone(metric);
    await act(() => vi.advanceTimersByTimeAsync(30000));
    expect(await screen.findByTestId("metric-hero")).toBeVisible();
    expect(screen.queryByText("Not calculated yet")).not.toBeInTheDocument();
    expect(screen.getByText("Active · v2")).toBeVisible();
    // One poll, then one invalidated read of the page's own detail.
    expect(calls("detail")).toHaveLength(4);
    await act(() => vi.advanceTimersByTimeAsync(90000));
    expect(calls("detail")).toHaveLength(4);
  } finally {
    vi.useRealTimers();
  }
});

it("does not poll a first calculation that failed", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  try {
    pendingMetric();
    responses.detail.draft_definition.backfill_status = "failed";
    mount();
    expect(await screen.findByText("Not calculated yet")).toBeVisible();
    expect(screen.getByText(/The first calculation failed/)).toBeVisible();
    expect(screen.getByText("Failed")).toBeVisible();
    await waitFor(() => expect(calls("detail")).toHaveLength(2));
    await waitFor(() => expect(calls("history").length).toBeGreaterThan(0));
    const history = calls("history").length;
    await act(() => vi.advanceTimersByTimeAsync(90000));
    expect(calls("detail")).toHaveLength(2);
    expect(calls("history")).toHaveLength(history);
    expect(screen.queryByText(/Checks again every/)).not.toBeInTheDocument();
  } finally {
    vi.useRealTimers();
  }
});

it("quotes the Overview's rounded value in the snapshot dialog, unrounded on hover", async () => {
  responses.kpi.current.value = 0.801887;
  responses.trend.results = responses.trend.results.map((bucket) => ({
    ...bucket,
    value: 0.801887,
  }));
  mount();
  expect(await screen.findByTestId("metric-hero")).toHaveTextContent("≈ 80.2%");
  fireEvent.click(screen.getByRole("button", { name: "Save snapshot" }));
  const dialog = screen.getByRole("dialog", { name: "Save snapshot" });
  const value = within(dialog).getByText("≈ 80.2%");
  expect(value).toBeVisible();
  expect(value).toHaveAttribute("title", "80.1887%");
  expect(within(dialog).queryByText(/80\.1887/)).not.toBeInTheDocument();
});

it("moves the header with the panel when a poll finds the calculation running, without another page read", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  try {
    pendingMetric();
    mount();
    expect(
      await screen.findByText("v3 · First calculation pending"),
    ).toBeVisible();
    await waitFor(() => expect(calls("detail")).toHaveLength(2));
    responses.detail = structuredClone(responses.detail);
    Object.assign(responses.detail.draft_definition, {
      backfill_status: "running",
      backfill_days_done: 2,
      backfill_days_total: 7,
    });
    await act(() => vi.advanceTimersByTimeAsync(30000));
    expect(
      await screen.findByText("v3 · First calculation running"),
    ).toBeVisible();
    expect(screen.getByText("Running")).toBeVisible();
    expect(
      screen.getByText(/The first calculation is running\./),
    ).toBeVisible();
    expect(
      screen.getByText("Recalculating history: 2 of 7 days"),
    ).toBeVisible();
    expect(calls("detail")).toHaveLength(3);
  } finally {
    vi.useRealTimers();
  }
});

it("starts a forward-only metric's KPI, trend and chart at its coverage start and says why", async () => {
  // Two days ago, inside the default 7-day window.
  const coverage = new Date(Date.now() - 2 * 86400000).toISOString();
  const forward = { history_mode: "forward", coverage_start: coverage };
  const first = new Date(
    Math.ceil(Date.parse(coverage) / (15 * 60 * 1000)) * 15 * 60 * 1000,
  ).toISOString();
  responses.detail = structuredClone({
    ...metric,
    definition: { ...metric.definition, ...forward },
  });
  const effective = {
    ...kpi.effective_window,
    window_start: first,
    window_end: new Date().toISOString(),
  };
  responses.kpi = {
    ...structuredClone(kpi),
    previous: null,
    delta: null,
    provenance: { ...kpi.provenance, ...forward },
    effective_window: effective,
  };
  responses.trend = {
    ...structuredClone(trend),
    provenance: { ...trend.provenance, ...forward },
    effective_window: effective,
  };
  mount();
  expect(await screen.findByTestId("metric-hero")).toHaveTextContent("≈ 62%");
  // The reads begin where values exist, not 7 days back.
  for (const kind of ["kpi", "trend"]) {
    const [, config] = calls(kind).at(-1);
    expect(config.params.window_start).toBe(first);
    expect(Date.parse(config.params.window_end)).toBeGreaterThan(
      Date.parse(first),
    );
  }
  expect(screen.getByTestId("metric-coverage")).toHaveTextContent(
    `Values from ${formatMetricTime(first)}. History isn't computed for projects this large yet.`,
  );
  expect(screen.getByText("Previous period unavailable")).toBeVisible();
});

it("does not read or allow a snapshot while coverage starts inside the current bucket", async () => {
  const coverage = new Date(Date.now() - 5000).toISOString();
  responses.detail = structuredClone({
    ...metric,
    definition: {
      ...metric.definition,
      history_mode: "forward",
      coverage_start: coverage,
    },
  });
  const defaultGet = mock.get.getMockImplementation();
  mock.get.mockImplementation(async (url, config) => {
    if (url.endsWith("/kpi") || url.endsWith("/trend")) {
      const floor = (value) => Math.floor(Date.parse(value) / 900000);
      if (floor(config.params.window_start) === floor(config.params.window_end))
        throw { statusCode: 400 };
    }
    return defaultGet(url, config);
  });
  mount();
  expect(await screen.findByTestId("metric-coverage-pending")).toBeVisible();
  expect(screen.getByText("Not calculated yet")).toBeVisible();
  expect(screen.getByTestId("metric-coverage")).toHaveTextContent(
    "Values from ",
  );
  expect(screen.queryByText(/Could not load/)).not.toBeInTheDocument();
  expect(calls("kpi")).toHaveLength(0);
  expect(calls("trend")).toHaveLength(0);
  expect(screen.getByRole("button", { name: "Save snapshot" })).toBeDisabled();
});

it("handles forward activation after mount without a bad read, then reanchors after the first full bucket", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  try {
    vi.setSystemTime(new Date("2026-11-13T10:03:00.000Z"));
    mount();
    expect(await screen.findByTestId("metric-hero")).toBeVisible();
    expect(calls("kpi")).toHaveLength(1);

    // The definition activates five seconds after the page's window was fixed.
    responses.detail = structuredClone({
      ...metric,
      definition: {
        ...metric.definition,
        history_mode: "forward",
        coverage_start: "2026-11-13T10:03:05.000Z",
      },
    });
    await refetchResource("detail");
    expect(await screen.findByTestId("metric-coverage-pending")).toBeVisible();
    expect(screen.getByTestId("metric-coverage")).toHaveTextContent(
      "Values from 13 Nov 2026, 10:15:00 UTC.",
    );
    // The fixed mount-time window ends before activation; it is skipped rather
    // than sent as a sub-bucket request or left in a retryable 400 state.
    expect(calls("kpi")).toHaveLength(1);
    expect(calls("trend")).toHaveLength(1);
    expect(screen.queryByText(/Could not load/)).not.toBeInTheDocument();

    // The preset window re-anchors after the first whole bucket ends, with a
    // short jitter allowance, so it can be read without a manual reload.
    await act(() => vi.advanceTimersByTimeAsync(27 * 60 * 1000 + 2500));
    expect(await screen.findByTestId("metric-hero")).toBeVisible();
    expect(calls("kpi")).toHaveLength(2);
    const [, request] = calls("kpi").at(-1);
    expect(request.params.window_start).toBe("2026-11-13T10:15:00.000Z");
    expect(Date.parse(request.params.window_end)).toBeGreaterThan(
      Date.parse("2026-11-13T10:30:00.000Z"),
    );
  } finally {
    vi.useRealTimers();
  }
});

it("explains a recalculating forward definition truthfully and keeps coverage visible without KPI data", async () => {
  const coverage = new Date(Date.now() - 2 * 86400000).toISOString();
  responses.detail = structuredClone({
    ...metric,
    definition: {
      ...metric.definition,
      history_mode: "forward",
      coverage_start: coverage,
    },
  });
  mock.get.mockImplementation(async (url) => {
    const kind = url.split("/").at(-1);
    if (kind === "kpi" || kind === "trend")
      throw {
        statusCode: 503,
        code: "metric_recalculating",
        retryable: true,
      };
    if (!responses[kind]) throw new Error(`Unexpected read ${url}`);
    return { data: responses[kind] };
  });
  mount();
  expect(
    await screen.findAllByText(/Not calculated yet\. Values from/),
  ).toHaveLength(2);
  expect(screen.getByTestId("metric-coverage")).toHaveTextContent(
    "Values from ",
  );
  expect(
    screen.queryByText(/completed active definition is not available/i),
  ).not.toBeInTheDocument();
  expect(screen.queryByTestId("metric-hero")).not.toBeInTheDocument();
});
