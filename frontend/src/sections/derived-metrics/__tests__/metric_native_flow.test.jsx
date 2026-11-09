import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "src/utils/test-utils";
import MetricList from "../MetricList";
import { metricWindow } from "../metricTime";
import { metricDashboardDescription } from "../metric_dashboard_description";
import wire from "./fixtures/committedResults.json";

const mock = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock("src/utils/axios", () => ({
  default: { get: mock.get },
  endpoints: { derivedMetrics: { kpi: (id) => `/metrics/${id}/kpi` } },
}));
vi.mock("../useMetricsEnabled", () => ({
  useMetricsEnabled: () => ({ enabled: true }),
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
  current_version: 3,
  definition: { version: 2 },
  value_mode: "materialized",
};
let client;
let response;
function mount() {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MetricList
        definitions={{ data: { results: [metric], next_cursor: null } }}
        scope={{ organizationId: "org", workspaceId: "ws" }}
        window={metricWindow(7)}
        onSelect={vi.fn()}
      />
    </QueryClientProvider>,
  );
}
beforeEach(() => {
  response = {
    provenance: {
      version: 2,
      updated_at: null,
      backfill_days_done: 7,
      backfill_days_total: 7,
    },
    current: {
      value: 0.5,
      point_status: "ok",
      value_format: "number",
      unit: "",
      weighted: false,
      rate_eligible: false,
      value_mode: "materialized",
      uncertainty: { status: "unavailable", reason: "not_computed" },
    },
    effective_window: metricWindow(7),
    previous: null,
    delta: null,
    delta_uncertainty: null,
  };
  mock.get.mockReset().mockImplementation(async (url) => {
    if (url === `/metrics/${metric.id}/kpi`) return { data: response };
    throw new Error(`Unexpected read ${url}`);
  });
});
afterEach(() => {
  cleanup();
  client?.clear();
});

it.each([0, null])(
  "never coerces current %s to a different value",
  async (value) => {
    response.current.value = value;
    mount();
    expect(
      await screen.findByText(value === null ? "No value" : "0"),
    ).toBeInTheDocument();
  },
);

it("does not label the data window as committed freshness when refresh time is unavailable", async () => {
  mount();
  expect(await screen.findByText("Freshness unavailable")).toBeInTheDocument();
  expect(screen.queryByText(/^Updated /)).not.toBeInTheDocument();
});

it("does not show a KPI from a different active version, and offers retry", async () => {
  response.provenance.version = 3;
  mount();
  expect(
    await screen.findByText("Could not load metric value"),
  ).toBeInTheDocument();
  expect(screen.queryByText("0.5")).not.toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: `Retry value for ${metric.name}` }),
  ).toBeInTheDocument();
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
