import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
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
import MetricsListPage from "../MetricsListPage";
import { metricWindow } from "../metricTime";

const mock = vi.hoisted(() => ({
  get: vi.fn(),
  projects: vi.fn(),
  org: {},
  workspace: {},
  enabled: true,
}));
vi.mock("src/utils/axios", () => ({
  default: { get: mock.get },
  endpoints: {
    derivedMetrics: { list: "/metrics", kpi: (id) => `/metrics/${id}/kpi` },
  },
}));
vi.mock("src/api/project/observe-project-list", () => ({
  readObserveProjectPage: mock.projects,
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
const conversation = {
  id: "00000000-0000-0000-0000-000000000001",
  project_id: "project-a",
  name: "Resolution rate",
  current_version: 3,
  active_version: "active-v2",
  definition: { version: 2, level: "conversation" },
  draft_definition: { version: 3, level: "user" },
  value_mode: "materialized",
  updated_at: "2025-01-01T00:00:00Z",
};
const userMetric = {
  ...conversation,
  id: "00000000-0000-0000-0000-000000000002",
  name: "Returning users",
  definition: { version: 2, level: "user" },
  value_mode: "on_demand",
};
const kpi = (value = 0, overrides = {}) => ({
  provenance: {
    manifest_id: "manifest",
    generation: 1,
    version_id: "active-v2",
    version: 2,
    definition_hash: "hash",
    updated_at: "2026-11-08T00:00:00Z",
    backfill_days_done: 7,
    backfill_days_total: 7,
  },
  effective_window: metricWindow(7, "2026-11-08T00:00:00Z"),
  current: {
    value,
    point_status: "ok",
    weighted: false,
    rate_eligible: false,
    unit: "",
    value_format: "number",
    unit_noun: "conversations",
    value_mode: "materialized",
    eligible_count: 100,
    evaluated_count: 100,
    missing_count: 0,
    uncertainty: { status: "unavailable", reason: "unweighted_total" },
  },
  previous: null,
  delta: null,
  delta_uncertainty: null,
  ...overrides,
});
const deferred = () => {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
};
let client;
let list;
let values;
function mount() {
  client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MetricsListPage />
    </QueryClientProvider>,
  );
}
const getRow = (name) =>
  screen.getByRole("button", { name }).closest('[role="row"]');
const readCalls = (url) => mock.get.mock.calls.filter(([path]) => path === url);
beforeEach(() => {
  window.history.replaceState({}, "", "/dashboard/metrics");
  mock.enabled = true;
  mock.org = { currentOrganizationId: "org-a", isReady: true };
  mock.workspace = {
    currentWorkspaceId: "ws-a",
    currentWorkspaceRole: "workspace_member",
    isReady: true,
  };
  list = { results: [conversation], next_cursor: null };
  values = kpi();
  mock.get.mockReset().mockImplementation(async (url) => {
    if (url === "/metrics") return { data: list };
    if (url.endsWith("/kpi")) return { data: values };
    throw new Error(`Unexpected read: ${url}`);
  });
  mock.projects.mockReset().mockResolvedValue({
    rows: [
      { id: "project-a", name: "Support" },
      { id: "project-b", name: "Sales" },
    ],
    totalPages: 1,
  });
});
afterEach(() => {
  cleanup();
  client?.clear();
});

it("distinguishes initial loading from first use and exposes creation", async () => {
  const pending = deferred();
  mock.get.mockReturnValue(pending.promise);
  mount();
  expect(screen.getByRole("status")).toHaveTextContent("Loading metrics…");
  expect(screen.queryByText("No metrics yet")).not.toBeInTheDocument();
  await act(async () =>
    pending.resolve({ data: { results: [], next_cursor: null } }),
  );
  expect(await screen.findByText("No metrics yet")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Create metric" }));
  expect(window.location.pathname).toBe("/dashboard/metrics/new");
});

it("does not report first use on a failed list read and retries the real list", async () => {
  mock.get.mockRejectedValueOnce({ statusCode: 503 });
  mount();
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Metrics could not be loaded",
  );
  expect(screen.queryByText("No metrics yet")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Retry list" }));
  expect(
    await screen.findByRole("button", { name: conversation.name }),
  ).toBeVisible();
});

it("says the Metrics schema is not installed instead of a generic list failure", async () => {
  mock.get.mockRejectedValueOnce({
    statusCode: 503,
    code: "metric_schema_not_installed",
    retryable: true,
  });
  mount();
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Metrics schema not installed. An administrator needs to install it",
  );
  expect(screen.queryByText(/could not be loaded/)).not.toBeInTheDocument();
  expect(screen.queryByText("No metrics yet")).not.toBeInTheDocument();
});

it("searches only the current page without inventing a server search argument", async () => {
  mount();
  await screen.findByRole("button", { name: conversation.name });
  fireEvent.change(
    screen.getByRole("textbox", { name: "Search metrics on this page" }),
    { target: { value: "absent" } },
  );
  expect(await screen.findByText("No metrics match your search")).toBeVisible();
  expect(screen.queryByText("No metrics yet")).not.toBeInTheDocument();
  expect(readCalls("/metrics")).toHaveLength(1);
  expect(readCalls("/metrics")[0][1].params).toEqual({ limit: 25 });
});

it("passes a closed Last 7 days window and explicit tenant scope, never reading legacy runs", async () => {
  mount();
  await screen.findByText("0");
  const request = readCalls(`/metrics/${conversation.id}/kpi`);
  expect(request).toHaveLength(1);
  expect(request[0][1].headers).toEqual({
    "X-Organization-Id": "org-a",
    "X-Workspace-Id": "ws-a",
  });
  const params = request[0][1].params;
  expect(new Date(params.window_end) - new Date(params.window_start)).toBe(
    7 * 86400000,
  );
  expect(Object.keys(params).sort()).toEqual(["window_end", "window_start"]);
  expect(
    mock.get.mock.calls.every(
      ([url]) => url === "/metrics" || url.endsWith("/kpi"),
    ),
  ).toBe(true);
  expect(within(getRow(conversation.name)).getByText("v2")).toBeVisible();
});

it.each([0, null])(
  "preserves a returned %s through the shared value cell",
  async (value) => {
    values = kpi(value);
    mount();
    const label = await screen.findByText(value === null ? "No value" : "0");
    expect(label.closest("[data-value-state]")).toHaveAttribute(
      "data-value-state",
      value === null ? "no_value" : "value",
    );
    if (value === null) expect(screen.queryByText("0")).not.toBeInTheDocument();
  },
);

it("renders materialized rows before an independent on-demand value resolves", async () => {
  list.results = [conversation, userMetric];
  const pending = deferred();
  mock.get.mockImplementation(async (url) =>
    url === "/metrics"
      ? { data: list }
      : url.includes(userMetric.id)
        ? pending.promise
        : { data: values },
  );
  mount();
  await screen.findByRole("button", { name: userMetric.name });
  expect(await within(getRow(conversation.name)).findByText("0")).toBeVisible();
  expect(
    within(getRow(userMetric.name)).getByText("Calculating for this window…"),
  ).toBeVisible();
  const userValue = kpi(0.25);
  userValue.current = {
    ...userValue.current,
    value_mode: "on_demand",
    unit_noun: "users",
    value_format: "percent",
    rate_eligible: true,
  };
  await act(async () => pending.resolve({ data: userValue }));
  expect(await within(getRow(userMetric.name)).findByText("25%")).toBeVisible();
  expect(within(getRow(conversation.name)).getByText("0")).toBeVisible();
});

it("an on-demand failure stays local and retries without blocking other rows", async () => {
  list.results = [conversation, userMetric];
  const pending = deferred();
  mock.get.mockImplementation(async (url) =>
    url === "/metrics"
      ? { data: list }
      : url.includes(userMetric.id)
        ? pending.promise
        : { data: values },
  );
  mount();
  await screen.findByRole("button", { name: userMetric.name });
  await act(async () => pending.reject({ statusCode: 503 }));
  expect(
    await within(getRow(userMetric.name)).findByText(
      "Could not load metric value",
    ),
  ).toBeVisible();
  expect(
    within(getRow(userMetric.name)).getByText("Values unavailable"),
  ).toBeVisible();
  expect(within(getRow(conversation.name)).getByText("0")).toBeVisible();
  mock.get.mockImplementation(async (url) => ({
    data: url === "/metrics" ? list : kpi(9),
  }));
  fireEvent.click(
    within(getRow(userMetric.name)).getByRole("button", {
      name: `Retry value for ${userMetric.name}`,
    }),
  );
  expect(await within(getRow(userMetric.name)).findByText("9")).toBeVisible();
  expect(readCalls(`/metrics/${conversation.id}/kpi`)).toHaveLength(1);
  expect(window.location.pathname).toBe("/dashboard/metrics");
});

it("shows committed freshness and recalculation progress, not definition edit time", async () => {
  values.provenance.backfill_days_done = 0;
  mount();
  expect(await screen.findByText("Recalculating · 0 of 7 days")).toBeVisible();
  const updated = screen.getByText(/^Updated /);
  fireEvent.mouseOver(updated);
  expect(await screen.findByRole("tooltip")).toHaveTextContent("08 Nov 2026");
  expect(screen.queryByText(/01 Jan 2025/)).not.toBeInTheDocument();
});

it("missing provenance never becomes a timestamp or zero progress", async () => {
  values.provenance = {
    ...values.provenance,
    updated_at: null,
    backfill_days_done: null,
  };
  mount();
  expect(await screen.findByText("Freshness unavailable")).toBeVisible();
  expect(screen.queryByText(/Recalculating/)).not.toBeInTheDocument();
});

it("unactivated drafts have no KPI request and no invented zero", async () => {
  list.results = [{ ...conversation, definition: null, active_version: null }];
  mount();
  expect(await screen.findByText("Not calculated yet")).toBeVisible();
  // Freshness is a quiet dash; its reason is the cell's accessible text.
  expect(screen.getByText("No active definition")).toBeInTheDocument();
  // Round 3 UI: saved and waiting for its first calculation, not "Draft v3".
  expect(screen.getByText("v3 · Pending")).toBeVisible();
  expect(screen.queryByText("0")).not.toBeInTheDocument();
  expect(mock.get.mock.calls.every(([url]) => url === "/metrics")).toBe(true);
});

it("supports keyboard activation of a metric and preserves its exact detail route", async () => {
  mount();
  const name = await screen.findByRole("button", { name: conversation.name });
  name.focus();
  await userEvent.keyboard("{Enter}");
  expect(window.location.pathname).toBe(
    `/dashboard/metrics/${conversation.id}`,
  );
});

it("uses cursor navigation and resets the cursor on page-size changes", async () => {
  list.next_cursor = conversation.id;
  mount();
  await screen.findByRole("button", { name: conversation.name });
  expect(
    screen.getByText("1 metric on this page · More available"),
  ).toBeVisible();
  expect(screen.queryByText(/of more than 25/)).not.toBeInTheDocument();
  list = { results: [userMetric], next_cursor: null };
  fireEvent.click(screen.getByRole("button", { name: "Next page" }));
  await screen.findByRole("button", { name: userMetric.name });
  expect(readCalls("/metrics").at(-1)[1].params).toEqual({
    limit: 25,
    before: conversation.id,
  });
  fireEvent.change(screen.getByLabelText("Rows per page"), {
    target: { value: "10" },
  });
  await waitFor(() =>
    expect(readCalls("/metrics").at(-1)[1].params).toEqual({ limit: 10 }),
  );
});

it("project selection filters definitions, clears pagination, and distinguishes project emptiness", async () => {
  list.next_cursor = conversation.id;
  mount();
  await screen.findByRole("button", { name: conversation.name });
  fireEvent.click(screen.getByRole("button", { name: "Next page" }));
  await waitFor(() => expect(readCalls("/metrics")).toHaveLength(2));
  list = { results: [], next_cursor: null };
  fireEvent.mouseDown(screen.getByRole("combobox", { name: "Project filter" }));
  fireEvent.click(await screen.findByRole("option", { name: "Sales" }));
  expect(await screen.findByText("No metrics in this project")).toBeVisible();
  expect(readCalls("/metrics").at(-1)[1].params).toEqual({
    limit: 25,
    project_id: "project-b",
  });
  expect(screen.queryByText("No metrics yet")).not.toBeInTheDocument();
});

it("scope changes abort old KPI requests, discard search/cursors and do not paint late data", async () => {
  const pending = deferred();
  mock.get.mockImplementation(async (url) =>
    url === "/metrics" ? { data: list } : pending.promise,
  );
  const view = mount();
  await screen.findByRole("button", { name: conversation.name });
  const oldRequest = readCalls(`/metrics/${conversation.id}/kpi`)[0][1];
  fireEvent.change(
    screen.getByRole("textbox", { name: "Search metrics on this page" }),
    { target: { value: "Resolution" } },
  );
  mock.workspace = { ...mock.workspace, currentWorkspaceId: "ws-b" };
  list = { results: [], next_cursor: null };
  view.rerender(
    <QueryClientProvider client={client}>
      <MetricsListPage />
    </QueryClientProvider>,
  );
  expect(await screen.findByText("No metrics yet")).toBeVisible();
  expect(
    screen.getByRole("textbox", { name: "Search metrics on this page" }),
  ).toHaveValue("");
  expect(oldRequest.signal.aborted).toBe(true);
  await act(async () => pending.resolve({ data: kpi(999) }));
  expect(screen.queryByText("999")).not.toBeInTheDocument();
  expect(readCalls("/metrics").at(-1)[1].headers["X-Workspace-Id"]).toBe(
    "ws-b",
  );
});

it("read-only members can inspect values but cannot create", async () => {
  mock.workspace.currentWorkspaceRole = "workspace_viewer";
  mount();
  expect(await screen.findByText("0")).toBeVisible();
  expect(screen.getByRole("button", { name: "Create metric" })).toBeDisabled();
});

it("refresh reads a new closed window without submitting a metric run", async () => {
  mount();
  await screen.findByText("0");
  fireEvent.click(screen.getByRole("button", { name: "Refresh values" }));
  await waitFor(() =>
    expect(readCalls(`/metrics/${conversation.id}/kpi`).length).toBeGreaterThan(
      1,
    ),
  );
  expect(
    mock.get.mock.calls.every(
      ([url]) => url === "/metrics" || url.endsWith("/kpi"),
    ),
  ).toBe(true);
});

it.each(["Owner", "Admin"])(
  "organization %s can create even without an explicit workspace role",
  async (role) => {
    mock.org.currentOrganizationRole = role;
    mock.workspace.currentWorkspaceRole = null;
    mount();
    await screen.findByText("0");
    expect(screen.getByRole("button", { name: "Create metric" })).toBeEnabled();
  },
);

it("workspace admins can create and unknown roles cannot", async () => {
  mock.workspace.currentWorkspaceRole = "workspace_admin";
  const view = mount();
  await screen.findByText("0");
  expect(screen.getByRole("button", { name: "Create metric" })).toBeEnabled();
  mock.workspace.currentWorkspaceRole = null;
  view.rerender(
    <QueryClientProvider client={client}>
      <MetricsListPage />
    </QueryClientProvider>,
  );
  expect(screen.getByRole("button", { name: "Create metric" })).toBeDisabled();
});

it("retains the Metrics heading while waiting for workspace scope without making requests", () => {
  mock.workspace.isReady = false;
  mount();
  expect(screen.getByRole("heading", { name: "Metrics" })).toBeVisible();
  expect(screen.getByRole("status")).toHaveTextContent("Loading workspace…");
  expect(mock.get).not.toHaveBeenCalled();
  expect(mock.projects).not.toHaveBeenCalled();
});

it("allows grid arrow navigation from the name button", async () => {
  list.results = [conversation, userMetric];
  mount();
  const name = await screen.findByRole("button", { name: conversation.name });
  name.focus();
  await userEvent.keyboard("{ArrowDown}");
  expect(document.activeElement.closest('[role="row"]')).toBe(
    getRow(userMetric.name),
  );
});

it("a project-catalog failure leaves loaded metrics usable and provides retry", async () => {
  mock.projects.mockRejectedValue(new Error("Offline"));
  mount();
  expect(await screen.findByText("0")).toBeVisible();
  expect(
    await screen.findByRole("button", { name: "Retry projects" }),
  ).toBeVisible();
  expect(screen.getByText("Project name unavailable")).toBeVisible();
});

it("shows a window past calculated values as not calculated yet, never a load error", async () => {
  const through = "2026-11-07T22:14:28.000Z";
  mock.get.mockImplementation(async (url) => {
    if (url === "/metrics") return { data: list };
    if (url.endsWith("/kpi"))
      throw {
        statusCode: 503,
        code: "window_not_calculated_yet",
        retryable: true,
        calculated_through: through,
      };
    throw new Error(`Unexpected read: ${url}`);
  });
  mount();
  const row = await screen.findByRole("button", { name: conversation.name });
  const cells = within(row.closest('[role="row"]'));
  expect(
    (await cells.findAllByText("Not calculated yet")).length,
  ).toBeGreaterThan(0);
  expect(cells.queryByText("Values unavailable")).not.toBeInTheDocument();
  expect(
    cells.queryByText("Could not load metric value"),
  ).not.toBeInTheDocument();
});

it.each([
  [
    "too large",
    {
      reason: "too_large",
      since: "2026-11-13T09:00:00Z",
      rows: 12500000,
      limit: 10000000,
    },
    "Calculation paused: this project stores 12,500,000 metric rows, above the limit of 10,000,000.",
  ],
  [
    "too busy",
    {
      reason: "too_busy",
      since: "2026-11-17T00:00:00Z",
      rows: null,
      limit: 10000000,
      work: { owners: 3200, captures: 100, head_generation: 4 },
    },
    "Calculation paused: one 15-minute period in this project has too much activity to calculate in a single pass.",
  ],
])(
  "badges a project paused as %s and says why on hover, keeping calculated values",
  async (_name, calculationPaused, why) => {
    list = {
      results: [{ ...conversation, calculation_paused: calculationPaused }],
      next_cursor: null,
    };
    mount();
    const row = await screen.findByRole("button", { name: conversation.name });
    const cells = within(row.closest('[role="row"]'));
    const badge = await cells.findByText("Paused");
    expect(badge).toBeVisible();
    // Values calculated before the pause still show in the Value column.
    expect(await cells.findByText("0")).toBeVisible();
    fireEvent.mouseOver(badge);
    const tooltip = await screen.findByRole("tooltip");
    expect(tooltip).toHaveTextContent(why);
    expect(tooltip).toHaveTextContent(
      "Values calculated so far stay available",
    );
    expect(cells.queryByText("Values unavailable")).not.toBeInTheDocument();
  },
);
